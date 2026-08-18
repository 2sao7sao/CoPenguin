from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from .artifacts import ArtifactCAS
from .coordinator import ActiveRun, ThreadCoordinator
from .errors import ExecutionError, PermanentExecutionError, StaleLease
from .models import JobState, StepKind, VerificationResult, VerificationVerdict
from .repository import SQLiteRuntimeRepository
from .snapshots import SnapshotStore


class WorkerRunStatus(StrEnum):
    COMPLETED = "completed"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    LOST_LEASE = "lost_lease"


@dataclass(frozen=True)
class ExecutionRequest:
    project_id: str
    thread_id: str
    run_id: str
    executor_key: str
    attempt: int
    task_snapshot: Mapping[str, Any]
    agent_snapshot: Mapping[str, Any]
    context_manifest: Mapping[str, Any]
    checkpoint_state: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ExecutionResult:
    output_artifact_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ExecutionControl:
    """Narrow callback surface exposed to an Executor.

    Executors cannot write Runtime state directly. They may only ask the owning
    WorkerHost to persist a checkpoint and observe whether its fenced lease was lost.
    """

    def __init__(
        self,
        *,
        checkpoint_callback: Callable[[Mapping[str, Any]], None],
        cancellation_error: Callable[[], BaseException | None],
    ) -> None:
        self._checkpoint_callback = checkpoint_callback
        self._cancellation_error = cancellation_error

    def checkpoint(self, state: Mapping[str, Any]) -> None:
        self.raise_if_cancelled()
        self._checkpoint_callback(dict(state))
        self.raise_if_cancelled()

    def raise_if_cancelled(self) -> None:
        error = self._cancellation_error()
        if error is None:
            return
        if isinstance(error, StaleLease):
            raise error
        raise StaleLease(f"worker heartbeat failed: {error}") from error


class Executor(Protocol):
    key: str
    version: str

    def execute(
        self,
        request: ExecutionRequest,
        control: ExecutionControl,
    ) -> ExecutionResult: ...


class Verifier(Protocol):
    key: str
    version: str
    executor_key: str

    def verify(
        self,
        request: ExecutionRequest,
        draft_artifact_id: str,
        control: ExecutionControl,
    ) -> VerificationResult: ...


@dataclass(frozen=True)
class WorkerHostConfig:
    worker_id: str
    concurrency: int = 1
    lease_seconds: int = 30
    heartbeat_interval_seconds: float = 5.0
    retry_delay_seconds: int = 1

    def __post_init__(self) -> None:
        worker_id = self.worker_id.strip()
        if not worker_id:
            raise ValueError("worker_id is required")
        if not 1 <= self.concurrency <= 32:
            raise ValueError("concurrency must be between 1 and 32")
        if self.lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if self.heartbeat_interval_seconds >= self.lease_seconds:
            raise ValueError("heartbeat interval must be shorter than the worker lease")
        if self.retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        object.__setattr__(self, "worker_id", worker_id)


@dataclass(frozen=True)
class WorkerRunResult:
    thread_id: str
    run_id: str
    executor_key: str
    executor_version: str
    status: WorkerRunStatus
    attempt: int
    output_artifact_id: str | None = None
    verifier_result_artifact_id: str | None = None
    delivery_id: str | None = None
    outbox_id: str | None = None
    checkpoint_id: str | None = None
    error_code: str | None = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkerHost:
    """A bounded, fenced host for immutable-input Executors."""

    def __init__(
        self,
        *,
        repository: SQLiteRuntimeRepository,
        artifacts: ArtifactCAS,
        executors: Sequence[Executor],
        config: WorkerHostConfig,
        verifiers: Sequence[Verifier] = (),
    ) -> None:
        registry: dict[str, Executor] = {}
        for executor in executors:
            key = executor.key.strip()
            version = executor.version.strip()
            if not key or not version:
                raise ValueError("Executor key and version are required")
            if key in registry:
                raise ValueError(f"duplicate Executor key: {key}")
            registry[key] = executor
        if not registry:
            raise ValueError("at least one Executor is required")
        verifier_registry: dict[str, Verifier] = {}
        for verifier in verifiers:
            executor_key = verifier.executor_key.strip()
            if not executor_key or not verifier.key.strip() or not verifier.version.strip():
                raise ValueError("Verifier executor key, key, and version are required")
            if executor_key not in registry:
                raise ValueError(f"Verifier targets an unregistered Executor: {executor_key}")
            if executor_key in verifier_registry:
                raise ValueError(f"duplicate Verifier for Executor: {executor_key}")
            verifier_registry[executor_key] = verifier
        self.repository = repository
        self.artifacts = artifacts
        self.snapshots = SnapshotStore(artifacts)
        self.coordinator = ThreadCoordinator(repository, artifacts)
        self.executors = registry
        self.verifiers = verifier_registry
        self.config = config

    @property
    def executor_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.executors))

    def run_once(self) -> WorkerRunResult | None:
        active = self._claim_one()
        if active is None:
            return None
        return self._execute_active(active)

    def run_batch(self, *, max_jobs: int | None = None) -> list[WorkerRunResult]:
        if max_jobs is not None and max_jobs < 1:
            raise ValueError("max_jobs must be at least 1")
        active_runs: list[ActiveRun] = []
        claim_limit = min(self.config.concurrency, max_jobs or self.config.concurrency)
        for _ in range(claim_limit):
            active = self._claim_one()
            if active is None:
                break
            active_runs.append(active)
        if not active_runs:
            return []
        if len(active_runs) == 1:
            return [self._execute_active(active_runs[0])]
        with ThreadPoolExecutor(
            max_workers=self.config.concurrency,
            thread_name_prefix="copenguin-worker",
        ) as pool:
            futures = [pool.submit(self._execute_active, active) for active in active_runs]
            return [future.result() for future in futures]

    def run_until_idle(self, *, max_jobs: int = 100) -> list[WorkerRunResult]:
        if max_jobs < 1:
            raise ValueError("max_jobs must be at least 1")
        completed: list[WorkerRunResult] = []
        while len(completed) < max_jobs:
            batch = self.run_batch(max_jobs=max_jobs - len(completed))
            if not batch:
                break
            completed.extend(batch)
        return completed

    def serve(
        self,
        stop_event: threading.Event,
        *,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        while not stop_event.is_set():
            if not self.run_batch():
                stop_event.wait(poll_interval_seconds)

    def _claim_one(self) -> ActiveRun | None:
        return self.coordinator.claim_next(
            worker_id=self.config.worker_id,
            lease_seconds=self.config.lease_seconds,
            executor_keys=self.executor_keys,
        )

    def _execute_active(self, active: ActiveRun) -> WorkerRunResult:
        run = active.projection.run(active.claim.run_id)
        assert run is not None
        executor = self.executors.get(run.executor_key)
        if executor is None:
            return self._settle_failure(
                active,
                executor_key=run.executor_key,
                executor_version="unknown",
                code="executor_not_registered",
                message=f"No Executor is registered for {run.executor_key}",
                retryable=False,
            )

        stop_heartbeat = threading.Event()
        heartbeat_error: list[BaseException] = []

        def heartbeat_loop() -> None:
            while not stop_heartbeat.wait(self.config.heartbeat_interval_seconds):
                try:
                    self.repository.heartbeat_run(
                        active.claim,
                        lease_seconds=self.config.lease_seconds,
                    )
                except BaseException as exc:  # noqa: BLE001 - transferred to the owner thread
                    heartbeat_error.append(exc)
                    stop_heartbeat.set()
                    return

        heartbeat = threading.Thread(
            target=heartbeat_loop,
            name=f"copenguin-heartbeat-{active.claim.run_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        checkpoint_id = active.checkpoint_id
        active_box = [active]

        def cancellation_error() -> BaseException | None:
            return heartbeat_error[0] if heartbeat_error else None

        def save_checkpoint(state: Mapping[str, Any]) -> None:
            nonlocal checkpoint_id
            normalized = dict(state)
            reserved = {
                "executor_key": executor.key,
                "executor_version": executor.version,
            }
            for key, value in reserved.items():
                existing = normalized.get(key)
                if existing is not None and existing != value:
                    raise PermanentExecutionError(
                        "checkpoint_executor_mismatch",
                        f"Checkpoint {key} does not match the claimed Executor",
                    )
                normalized[key] = value
            updated, checkpoint = self.coordinator.save_checkpoint(active_box[0], normalized)
            active_box[0] = updated
            checkpoint_id = checkpoint.artifact_id

        control = ExecutionControl(
            checkpoint_callback=save_checkpoint,
            cancellation_error=cancellation_error,
        )
        current_step_id: str | None = None
        verification: VerificationResult | None = None
        try:
            request = self._load_request(active_box[0], executor)
            transform_step_id = self._step_id(
                active.claim.run_id,
                active.claim.attempt,
                ordinal=1,
                kind=StepKind.TRANSFORM,
            )
            self.repository.begin_claimed_step(
                active.claim,
                step_id=transform_step_id,
                ordinal=1,
                kind=StepKind.TRANSFORM,
                provider_key=executor.key,
                provider_version=executor.version,
                input_artifact_ids=tuple(request.task_snapshot.get("input_artifact_ids", ())),
                budget={"bounded": True},
            )
            current_step_id = transform_step_id
            result = executor.execute(request, control)
            control.raise_if_cancelled()
            if not result.output_artifact_id.startswith("artifact:sha256:"):
                raise PermanentExecutionError(
                    "invalid_executor_output",
                    "Executor output must be an Artifact CAS reference",
                )
            if not self.artifacts.exists(result.output_artifact_id, verify=True):
                raise PermanentExecutionError(
                    "missing_executor_output",
                    "Executor output is missing from the configured Artifact CAS",
                )
            self.repository.finish_claimed_step(
                active.claim,
                step_id=transform_step_id,
                succeeded=True,
                output_artifact_ids=(result.output_artifact_id,),
            )
            current_step_id = None

            verifier = self.verifiers.get(executor.key)
            if verifier is not None:
                verifier_step_id = self._step_id(
                    active.claim.run_id,
                    active.claim.attempt,
                    ordinal=2,
                    kind=StepKind.VERIFIER,
                )
                self.repository.begin_claimed_step(
                    active.claim,
                    step_id=verifier_step_id,
                    ordinal=2,
                    kind=StepKind.VERIFIER,
                    provider_key=verifier.key,
                    provider_version=verifier.version,
                    input_artifact_ids=(result.output_artifact_id,),
                    budget={"bounded": True, "remote_reads": 0, "model_calls": 0},
                )
                current_step_id = verifier_step_id
                verification = verifier.verify(request, result.output_artifact_id, control)
                verifier_outputs = [verification.report_artifact_id]
                if verification.verified_artifact_id is not None:
                    verifier_outputs.append(verification.verified_artifact_id)
                if verification.verdict != VerificationVerdict.PASSED:
                    self.repository.finish_claimed_step(
                        active.claim,
                        step_id=verifier_step_id,
                        succeeded=False,
                        output_artifact_ids=verifier_outputs,
                        verifier_result_artifact_id=verification.report_artifact_id,
                        error_code="verification_failed",
                        error="Decision Record did not pass every required verifier check",
                    )
                    current_step_id = None
                    raise PermanentExecutionError(
                        "verification_failed",
                        "Decision Record did not pass every required verifier check",
                    )
                assert verification.verified_artifact_id is not None
                self.repository.finish_claimed_step(
                    active.claim,
                    step_id=verifier_step_id,
                    succeeded=True,
                    output_artifact_ids=verifier_outputs,
                    verifier_result_artifact_id=verification.report_artifact_id,
                )
                current_step_id = None
                result = ExecutionResult(
                    output_artifact_id=verification.verified_artifact_id,
                    metadata={
                        **dict(result.metadata),
                        "verification": verification.verdict.value,
                    },
                )
        except StaleLease as exc:
            return WorkerRunResult(
                thread_id=active.claim.thread_id,
                run_id=active.claim.run_id,
                executor_key=executor.key,
                executor_version=executor.version,
                status=WorkerRunStatus.LOST_LEASE,
                attempt=active.claim.attempt,
                checkpoint_id=checkpoint_id,
                error_code="worker_lease_lost",
                error=str(exc),
            )
        except ExecutionError as exc:
            self._fail_step_safely(
                active_box[0],
                step_id=current_step_id,
                code=exc.code,
                message=str(exc),
            )
            return self._settle_failure(
                active_box[0],
                executor_key=executor.key,
                executor_version=executor.version,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
                checkpoint_id=checkpoint_id,
            )
        except Exception as exc:  # noqa: BLE001 - unhandled pure Executor failures are bounded retries
            self._fail_step_safely(
                active_box[0],
                step_id=current_step_id,
                code="executor_unhandled_error",
                message=str(exc) or type(exc).__name__,
            )
            return self._settle_failure(
                active_box[0],
                executor_key=executor.key,
                executor_version=executor.version,
                code="executor_unhandled_error",
                message=str(exc) or type(exc).__name__,
                retryable=True,
                checkpoint_id=checkpoint_id,
            )
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=max(1.0, self.config.heartbeat_interval_seconds * 2))

        delivery_id: str | None = None
        outbox_id: str | None = None
        try:
            if verification is None:
                job, _ = self.repository.finish_claimed_execution(
                    active.claim,
                    executor_key=executor.key,
                    executor_version=executor.version,
                    succeeded=True,
                    output_artifact_id=result.output_artifact_id,
                )
            else:
                verified_record = self.artifacts.get_json(result.output_artifact_id)
                citations = (
                    verified_record.get("citations", ())
                    if isinstance(verified_record, dict)
                    else ()
                )
                source_refs = tuple(
                    str(item["source_ref_id"])
                    for item in citations
                    if isinstance(item, dict) and item.get("source_ref_id")
                )
                draft_artifact_id = str(
                    self.artifacts.get_json(verification.report_artifact_id)["draft_artifact_id"]
                )
                job, _, delivery, outbox = self.repository.finalize_claimed_delivery(
                    active.claim,
                    executor_key=executor.key,
                    executor_version=executor.version,
                    primary_artifact_id=result.output_artifact_id,
                    verifier_result_artifact_id=verification.report_artifact_id,
                    supporting_artifact_ids=(draft_artifact_id,),
                    source_refs=source_refs,
                    sensitivity=str(request.task_snapshot.get("sensitivity") or "normal"),
                )
                delivery_id = delivery.delivery_id
                outbox_id = outbox.outbox_id
        except StaleLease as exc:
            return WorkerRunResult(
                thread_id=active.claim.thread_id,
                run_id=active.claim.run_id,
                executor_key=executor.key,
                executor_version=executor.version,
                status=WorkerRunStatus.LOST_LEASE,
                attempt=active.claim.attempt,
                checkpoint_id=checkpoint_id,
                error_code="worker_lease_lost",
                error=str(exc),
            )
        assert job.state == JobState.COMPLETED
        return WorkerRunResult(
            thread_id=active.claim.thread_id,
            run_id=active.claim.run_id,
            executor_key=executor.key,
            executor_version=executor.version,
            status=WorkerRunStatus.COMPLETED,
            attempt=active.claim.attempt,
            output_artifact_id=result.output_artifact_id,
            verifier_result_artifact_id=(
                verification.report_artifact_id if verification is not None else None
            ),
            delivery_id=delivery_id,
            outbox_id=outbox_id,
            checkpoint_id=checkpoint_id,
            metadata=dict(result.metadata),
        )

    def _step_id(
        self,
        run_id: str,
        attempt: int,
        *,
        ordinal: int,
        kind: StepKind,
    ) -> str:
        return uuid5(
            NAMESPACE_URL,
            f"copenguin:{run_id}:attempt:{attempt}:step:{ordinal}:{kind.value}",
        ).hex

    def _fail_step_safely(
        self,
        active: ActiveRun,
        *,
        step_id: str | None,
        code: str,
        message: str,
    ) -> None:
        if step_id is None:
            return
        try:
            self.repository.finish_claimed_step(
                active.claim,
                step_id=step_id,
                succeeded=False,
                error_code=code,
                error=message,
            )
        except Exception:  # noqa: BLE001 - preserve the original execution failure
            return

    def _load_request(self, active: ActiveRun, executor: Executor) -> ExecutionRequest:
        run = active.projection.run(active.claim.run_id)
        assert run is not None
        task_snapshot_id = run.task_snapshot_id
        agent_snapshot_id = run.agent_snapshot_id
        context_manifest_id = run.context_manifest_id
        if task_snapshot_id is None or agent_snapshot_id is None or context_manifest_id is None:
            raise PermanentExecutionError(
                "execution_manifest_incomplete",
                "Run is missing one or more frozen execution snapshots",
            )
        task = self.snapshots.load(task_snapshot_id)
        agent = self.snapshots.load(agent_snapshot_id)
        context = self.snapshots.load(context_manifest_id)
        if task.get("snapshot_type") != "task":
            raise PermanentExecutionError("invalid_task_snapshot", "Task snapshot type is invalid")
        if agent.get("snapshot_type") != "agent":
            raise PermanentExecutionError(
                "invalid_agent_snapshot", "Agent snapshot type is invalid"
            )
        if context.get("snapshot_type") != "context_manifest":
            raise PermanentExecutionError(
                "invalid_context_manifest", "Context manifest snapshot type is invalid"
            )
        if task.get("thread_id") != active.claim.thread_id:
            raise PermanentExecutionError(
                "task_thread_mismatch", "Task snapshot belongs to a different Thread"
            )
        if task.get("project_id") != active.projection.project_id:
            raise PermanentExecutionError(
                "task_project_mismatch", "Task snapshot belongs to a different Project"
            )
        if task.get("workflow_id", "unassigned") != executor.key:
            raise PermanentExecutionError(
                "workflow_executor_mismatch",
                "Task workflow does not match the scheduler Executor key",
            )
        if context.get("task_snapshot_id") != run.task_snapshot_id:
            raise PermanentExecutionError(
                "context_task_mismatch", "Context manifest references another Task snapshot"
            )
        if context.get("agent_snapshot_id") != run.agent_snapshot_id:
            raise PermanentExecutionError(
                "context_agent_mismatch", "Context manifest references another Agent snapshot"
            )
        workflows = agent.get("capability_manifest", {}).get("workflows", ())
        if executor.key not in workflows:
            raise PermanentExecutionError(
                "executor_not_in_agent_snapshot",
                "The frozen Agent snapshot does not authorize this Executor",
            )
        for item in context.get("items", ()):
            artifact_id = item.get("content_artifact_id")
            if not isinstance(artifact_id, str) or not self.artifacts.exists(
                artifact_id, verify=True
            ):
                raise PermanentExecutionError(
                    "context_artifact_missing",
                    "A bound Context item is missing or corrupt",
                )

        checkpoint_state: Mapping[str, Any] | None = None
        checkpoint = self.coordinator.load_checkpoint(active)
        if checkpoint is not None:
            state = checkpoint.get("state")
            if not isinstance(state, dict):
                raise PermanentExecutionError(
                    "invalid_checkpoint", "Run checkpoint state must be an object"
                )
            if (
                state.get("executor_key") != executor.key
                or state.get("executor_version") != executor.version
            ):
                raise PermanentExecutionError(
                    "checkpoint_executor_mismatch",
                    "Run checkpoint belongs to another Executor version",
                )
            checkpoint_state = state
        return ExecutionRequest(
            project_id=active.projection.project_id,
            thread_id=active.claim.thread_id,
            run_id=active.claim.run_id,
            executor_key=executor.key,
            attempt=active.claim.attempt,
            task_snapshot=task,
            agent_snapshot=agent,
            context_manifest=context,
            checkpoint_state=checkpoint_state,
        )

    def _settle_failure(
        self,
        active: ActiveRun,
        *,
        executor_key: str,
        executor_version: str,
        code: str,
        message: str,
        retryable: bool,
        checkpoint_id: str | None = None,
    ) -> WorkerRunResult:
        try:
            job, _ = self.repository.finish_claimed_execution(
                active.claim,
                executor_key=executor_key,
                executor_version=executor_version,
                succeeded=False,
                error_code=code,
                error=message,
                retryable=retryable,
                retry_delay_seconds=self.config.retry_delay_seconds,
            )
        except StaleLease as exc:
            return WorkerRunResult(
                thread_id=active.claim.thread_id,
                run_id=active.claim.run_id,
                executor_key=executor_key,
                executor_version=executor_version,
                status=WorkerRunStatus.LOST_LEASE,
                attempt=active.claim.attempt,
                checkpoint_id=checkpoint_id,
                error_code="worker_lease_lost",
                error=str(exc),
            )
        status = (
            WorkerRunStatus.RETRY_SCHEDULED
            if job.state == JobState.QUEUED
            else WorkerRunStatus.FAILED
        )
        return WorkerRunResult(
            thread_id=active.claim.thread_id,
            run_id=active.claim.run_id,
            executor_key=executor_key,
            executor_version=executor_version,
            status=status,
            attempt=active.claim.attempt,
            checkpoint_id=checkpoint_id,
            error_code=code,
            error=message,
        )
