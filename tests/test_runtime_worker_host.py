from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from super_agent_runtime import (
    AgentSnapshot,
    ArtifactCAS,
    AttentionState,
    ContextItem,
    ContextManifest,
    ExecutionControl,
    ExecutionRequest,
    ExecutionResult,
    InboxCoordinator,
    InboxMessage,
    JobState,
    PermanentExecutionError,
    RoutingContext,
    RunState,
    SnapshotStore,
    SourceSnapshotBinding,
    SourceToArtifactExecutor,
    SourceToArtifactTaskCompiler,
    SQLiteRuntimeRepository,
    TaskSnapshot,
    ThreadCoordinator,
    ThreadState,
    WorkerHost,
    WorkerHostConfig,
    WorkerRunStatus,
)

WORKFLOW = "source_to_project_decision_record_v1"


def _enqueue_source_task(
    tmp_path,
    *,
    repository: SQLiteRuntimeRepository | None = None,
    artifacts: ArtifactCAS | None = None,
    thread_id: str = "thread-source",
    run_id: str = "run-source",
    executor_key: str = WORKFLOW,
):
    repository = repository or SQLiteRuntimeRepository(tmp_path / "runtime.db")
    artifacts = artifacts or ArtifactCAS(tmp_path / "artifacts")
    snapshots = SnapshotStore(artifacts)
    source = artifacts.put_json(
        {
            "title": "Project Penguin launch review",
            "background": ["The team is choosing the Alpha launch scope."],
            "facts": ["Alpha is local-first."],
            "decisions": [
                {
                    "decision": "Use Source-to-Artifact as the Alpha path.",
                    "rationale": "It creates an inspectable trust loop.",
                    "alternatives": ["Start with broad autonomous actions."],
                }
            ],
            "action_items": [
                {
                    "action": "Implement the bounded Worker Host.",
                    "owner": "CoPenguin team",
                    "due": "2026-08-20",
                    "status": "open",
                }
            ],
            "open_questions": ["Which pilot cohort should receive the first build?"],
            "risks": ["A generated record may still contain unsupported claims."],
        },
        kind="source_snapshot_content",
    )
    task = snapshots.put_task(
        TaskSnapshot(
            task_id=thread_id,
            thread_id=thread_id,
            project_id="work",
            objective="把已选择的项目材料沉淀成项目决策记录",
            domain="work",
            acceptance_criteria=("Produce an inspectable Project Decision Record",),
            input_artifact_ids=(source.artifact_id,),
            workflow_id=executor_key,
            created_at="2026-08-13T08:00:00.000000Z",
        )
    )
    agent = snapshots.put_agent(
        AgentSnapshot(
            agent_id="copenguin-fixture",
            model_profile={"provider": "deterministic-fixture"},
            tool_registry={},
            capability_manifest={"workflows": [executor_key]},
            created_at="2026-08-13T08:00:00.000000Z",
        )
    )
    context = snapshots.put_context(
        ContextManifest(
            task_snapshot_id=task.artifact_id,
            agent_snapshot_id=agent.artifact_id,
            items=(
                ContextItem(
                    ordinal=1,
                    source_type="source_snapshot",
                    source_ref="feishu:docx:doc-1",
                    content_artifact_id=source.artifact_id,
                    allowed_use="artifact_only",
                    metadata={
                        "source_snapshot_id": "source-snapshot-1",
                        "source_ref_id": "feishu:docx:doc-1",
                        "revision_id": "revision-7",
                        "access_envelope_id": "access-envelope-1",
                    },
                ),
            ),
            compiler_version="source-task-fixture-v1",
            compiled_at="2026-08-13T08:00:00.000000Z",
        )
    )
    handle = ThreadCoordinator(repository, artifacts).submit_task(
        project_id="work",
        title="Create a Project Decision Record",
        thread_id=thread_id,
        run_id=run_id,
        correlation_id=f"source-task:{thread_id}",
        task_snapshot_id=task.artifact_id,
        agent_snapshot_id=agent.artifact_id,
        context_manifest_id=context.artifact_id,
        executor_key=executor_key,
    )
    return repository, artifacts, snapshots, handle


def _host(
    repository: SQLiteRuntimeRepository,
    artifacts: ArtifactCAS,
    *executors,
    concurrency: int = 1,
    lease_seconds: int = 5,
    heartbeat_interval_seconds: float = 0.05,
) -> WorkerHost:
    return WorkerHost(
        repository=repository,
        artifacts=artifacts,
        executors=executors or (SourceToArtifactExecutor(artifacts),),
        config=WorkerHostConfig(
            worker_id="worker-test",
            concurrency=concurrency,
            lease_seconds=lease_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            retry_delay_seconds=0,
        ),
    )


def test_source_task_runs_from_queue_to_deterministic_inspectable_artifact(tmp_path) -> None:
    repository, artifacts, _, handle = _enqueue_source_task(tmp_path)
    host = _host(repository, artifacts)

    results = host.run_until_idle(max_jobs=1)

    assert len(results) == 1
    result = results[0]
    assert result.status == WorkerRunStatus.COMPLETED
    assert result.output_artifact_id is not None
    output = artifacts.get_json(result.output_artifact_id)
    assert output["artifact_type"] == "project_decision_record"
    assert output["project_id"] == "work"
    assert output["source_snapshot_ids"] == ["source-snapshot-1"]
    assert output["sections"]["decisions"][0]["decision"].startswith("Use Source-to-Artifact")
    assert output["verification"]["status"] == "pending_v2_005"
    assert output["publishable"] is False

    thread = repository.get_thread(handle.thread_id)
    run = thread.run(handle.run_id)
    assert run is not None and run.state == RunState.COMPLETED
    assert run.output_artifact_id == result.output_artifact_id
    assert run.latest_checkpoint_id is not None
    assert thread.actual_state == ThreadState.DORMANT
    assert repository.get_job(handle.run_id).state == JobState.COMPLETED
    assert repository.verify_thread_replay(handle.thread_id)


def test_source_task_compiler_binds_only_explicit_captured_sources(tmp_path) -> None:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    artifacts = ArtifactCAS(tmp_path / "artifacts")
    snapshots = SnapshotStore(artifacts)
    agent = snapshots.put_agent(
        AgentSnapshot(
            agent_id="source-compiler-test",
            model_profile={"provider": "deterministic-fixture"},
            tool_registry={},
            capability_manifest={"workflows": [WORKFLOW]},
        )
    )
    source = artifacts.put_json(
        {"title": "Selected source", "decisions": [], "facts": []},
        kind="source_snapshot_content",
    )
    compiler = SourceToArtifactTaskCompiler(
        repository=repository,
        artifacts=artifacts,
        agent_snapshot_id=agent.artifact_id,
    )

    submitted = compiler.submit(
        project_id="work",
        objective="Create a decision record",
        sources=(
            SourceSnapshotBinding(
                source_snapshot_id="snapshot-explicit",
                source_ref_id="feishu:wiki:node-1",
                revision_id="revision-1",
                access_envelope_id="access-1",
                content_artifact_id=source.artifact_id,
            ),
        ),
        thread_id="compiled-thread",
        run_id="compiled-run",
    )

    thread = repository.get_thread(submitted.task.thread_id)
    run = thread.run(submitted.task.run_id)
    assert run is not None and run.executor_key == WORKFLOW
    task = snapshots.load(submitted.task_snapshot_id)
    context = snapshots.load(submitted.context_manifest_id)
    assert task["workflow_id"] == WORKFLOW
    assert task["input_artifact_ids"] == [source.artifact_id]
    assert context["items"][0]["metadata"]["source_snapshot_id"] == "snapshot-explicit"
    assert repository.get_job(submitted.task.run_id).executor_key == WORKFLOW


def test_worker_only_claims_jobs_supported_by_its_executor_registry(tmp_path) -> None:
    repository, artifacts, _, generic = _enqueue_source_task(
        tmp_path,
        thread_id="thread-generic",
        run_id="run-generic",
        executor_key="unassigned",
    )
    _, _, _, supported = _enqueue_source_task(
        tmp_path,
        repository=repository,
        artifacts=artifacts,
        thread_id="thread-supported",
        run_id="run-supported",
    )
    host = _host(repository, artifacts)

    result = host.run_once()

    assert result is not None and result.run_id == supported.run_id
    assert repository.get_job(supported.run_id).state == JobState.COMPLETED
    assert repository.get_job(generic.run_id).state == JobState.QUEUED


def test_thread_supplement_preserves_source_workflow_executor_routing(tmp_path) -> None:
    repository, artifacts, snapshots, handle = _enqueue_source_task(tmp_path)
    original = repository.get_thread(handle.thread_id).run(handle.run_id)
    assert original is not None and original.agent_snapshot_id is not None
    inbox = InboxCoordinator(
        repository=repository,
        artifacts=artifacts,
        snapshots=snapshots,
        agent_snapshot_id=original.agent_snapshot_id,
    )

    inbox.receive(
        InboxMessage(
            platform="local",
            message_id="source-supplement",
            chat_id="control-room",
            actor_id="owner",
            text=f"/thread {handle.thread_id} 补充：突出行动项",
            created_at="2026-08-13T08:00:30.000000Z",
        ),
        RoutingContext(project_id="work"),
    )

    updated = repository.get_thread(handle.thread_id)
    replacement = updated.run(updated.updates[-1].new_run_id)
    assert replacement is not None and replacement.executor_key == WORKFLOW
    assert repository.get_job(replacement.run_id).executor_key == WORKFLOW
    result = _host(repository, artifacts).run_once()
    assert result is not None and result.run_id == replacement.run_id
    assert result.status == WorkerRunStatus.COMPLETED


@dataclass
class _CrashOnceExecutor:
    artifacts: ArtifactCAS
    key: str = "crash_once_fixture"
    version: str = "1"

    def execute(
        self,
        request: ExecutionRequest,
        control: ExecutionControl,
    ) -> ExecutionResult:
        checkpoint = request.checkpoint_state or {}
        draft_id = checkpoint.get("draft_artifact_id")
        if draft_id:
            assert self.artifacts.exists(str(draft_id), verify=True)
            return ExecutionResult(output_artifact_id=str(draft_id), metadata={"resumed": True})
        draft = self.artifacts.put_json(
            {"artifact_type": "fixture", "run_id": request.run_id},
            kind="fixture_output",
        )
        control.checkpoint(
            {
                "phase": "artifact_written",
                "draft_artifact_id": draft.artifact_id,
            }
        )
        raise RuntimeError("simulated process crash after durable checkpoint")


def test_restarted_host_resumes_latest_checkpoint_without_recreating_output(tmp_path) -> None:
    repository, artifacts, _, handle = _enqueue_source_task(
        tmp_path,
        executor_key="crash_once_fixture",
    )
    first = _host(repository, artifacts, _CrashOnceExecutor(artifacts))

    failed_attempt = first.run_once()

    assert failed_attempt is not None
    assert failed_attempt.status == WorkerRunStatus.RETRY_SCHEDULED
    checkpoint_id = repository.get_thread(handle.thread_id).run(handle.run_id).latest_checkpoint_id
    assert checkpoint_id is not None

    restarted_repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    restarted = _host(
        restarted_repository,
        ArtifactCAS(tmp_path / "artifacts"),
        _CrashOnceExecutor(ArtifactCAS(tmp_path / "artifacts")),
    )
    recovered = restarted.run_once()

    assert recovered is not None and recovered.status == WorkerRunStatus.COMPLETED
    assert recovered.checkpoint_id == checkpoint_id
    assert recovered.metadata["resumed"] is True
    assert restarted_repository.get_job(handle.run_id).attempts == 2
    events = restarted_repository.list_events(run_id=handle.run_id, limit=100)
    assert [event.event_type for event in events].count("run.checkpoint_recorded") == 1


class _ConcurrencyProbeExecutor:
    key = "concurrency_probe"
    version = "1"

    def __init__(self, artifacts: ArtifactCAS) -> None:
        self.artifacts = artifacts
        self._lock = threading.Lock()
        self.current = 0
        self.maximum = 0
        self.barrier = threading.Barrier(2)

    def execute(
        self,
        request: ExecutionRequest,
        control: ExecutionControl,
    ) -> ExecutionResult:
        with self._lock:
            self.current += 1
            self.maximum = max(self.maximum, self.current)
        try:
            self.barrier.wait(timeout=2)
            time.sleep(0.03)
            output = self.artifacts.put_json(
                {"run_id": request.run_id},
                kind="concurrency_probe",
            )
            return ExecutionResult(output_artifact_id=output.artifact_id)
        finally:
            with self._lock:
                self.current -= 1


def test_worker_batch_respects_configured_concurrency_bound(tmp_path) -> None:
    repository, artifacts, _, _ = _enqueue_source_task(
        tmp_path,
        thread_id="thread-a",
        run_id="run-a",
        executor_key="concurrency_probe",
    )
    _enqueue_source_task(
        tmp_path,
        repository=repository,
        artifacts=artifacts,
        thread_id="thread-b",
        run_id="run-b",
        executor_key="concurrency_probe",
    )
    executor = _ConcurrencyProbeExecutor(artifacts)
    host = _host(repository, artifacts, executor, concurrency=2)

    results = host.run_batch()

    assert len(results) == 2
    assert all(result.status == WorkerRunStatus.COMPLETED for result in results)
    assert executor.maximum == 2


class _SlowExecutor:
    key = "slow_fixture"
    version = "1"

    def __init__(self, artifacts: ArtifactCAS) -> None:
        self.artifacts = artifacts

    def execute(
        self,
        request: ExecutionRequest,
        control: ExecutionControl,
    ) -> ExecutionResult:
        time.sleep(0.18)
        control.raise_if_cancelled()
        output = self.artifacts.put_json({"ok": True}, kind="slow_fixture")
        return ExecutionResult(output_artifact_id=output.artifact_id)


def test_worker_heartbeats_while_executor_is_running(tmp_path) -> None:
    repository, artifacts, _, handle = _enqueue_source_task(
        tmp_path,
        executor_key="slow_fixture",
    )
    host = _host(
        repository,
        artifacts,
        _SlowExecutor(artifacts),
        lease_seconds=1,
        heartbeat_interval_seconds=0.03,
    )

    result = host.run_once()

    assert result is not None and result.status == WorkerRunStatus.COMPLETED
    events = repository.list_events(run_id=handle.run_id, limit=100)
    assert any(event.event_type == "scheduler.run_heartbeat" for event in events)


class _PermanentFailureExecutor:
    key = "permanent_failure"
    version = "1"

    def execute(
        self,
        request: ExecutionRequest,
        control: ExecutionControl,
    ) -> ExecutionResult:
        raise PermanentExecutionError("source_permission_denied", "Source cannot be read")


def test_permanent_executor_failure_terminates_run_and_surfaces_attention(tmp_path) -> None:
    repository, artifacts, _, handle = _enqueue_source_task(
        tmp_path,
        executor_key="permanent_failure",
    )
    host = _host(repository, artifacts, _PermanentFailureExecutor())

    result = host.run_once()

    assert result is not None and result.status == WorkerRunStatus.FAILED
    assert result.error_code == "source_permission_denied"
    job = repository.get_job(handle.run_id)
    thread = repository.get_thread(handle.thread_id)
    run = thread.run(handle.run_id)
    assert job.state == JobState.FAILED
    assert job.attempts == 1
    assert run is not None and run.state == RunState.FAILED
    assert thread.actual_state == ThreadState.FAILED
    assert thread.attention_state == AttentionState.FAILED
    assert host.run_once() is None


class _CancellationProbeExecutor:
    key = "cancellation_probe"
    version = "1"

    def __init__(self, artifacts: ArtifactCAS) -> None:
        self.artifacts = artifacts
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(
        self,
        request: ExecutionRequest,
        control: ExecutionControl,
    ) -> ExecutionResult:
        self.started.set()
        assert self.release.wait(timeout=2)
        control.raise_if_cancelled()
        output = self.artifacts.put_json({"too_late": True}, kind="cancel_probe")
        return ExecutionResult(output_artifact_id=output.artifact_id)


def test_thread_cancellation_fences_inflight_worker_before_output_can_commit(tmp_path) -> None:
    repository, artifacts, snapshots, handle = _enqueue_source_task(
        tmp_path,
        executor_key="cancellation_probe",
    )
    executor = _CancellationProbeExecutor(artifacts)
    host = _host(
        repository,
        artifacts,
        executor,
        heartbeat_interval_seconds=0.02,
    )
    thread = repository.get_thread(handle.thread_id)
    run = thread.run(handle.run_id)
    assert run is not None and run.agent_snapshot_id is not None
    inbox = InboxCoordinator(
        repository=repository,
        artifacts=artifacts,
        snapshots=snapshots,
        agent_snapshot_id=run.agent_snapshot_id,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(host.run_once)
        assert executor.started.wait(timeout=2)
        inbox.receive(
            InboxMessage(
                platform="local",
                message_id="cancel-worker",
                chat_id="control-room",
                actor_id="owner",
                text=f"/thread {handle.thread_id} 取消任务",
                created_at="2026-08-13T08:01:00.000000Z",
            ),
            RoutingContext(project_id="work"),
        )
        executor.release.set()
        result = future.result(timeout=2)

    assert result is not None and result.status == WorkerRunStatus.LOST_LEASE
    cancelled = repository.get_thread(handle.thread_id)
    assert cancelled.actual_state == ThreadState.CANCELLED
    assert cancelled.run(handle.run_id).state == RunState.CANCELLED
    assert cancelled.run(handle.run_id).output_artifact_id is None
    assert repository.get_job(handle.run_id).state == JobState.CANCELLED
