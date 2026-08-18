from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .artifacts import ArtifactCAS, ArtifactRef
from .models import ThreadProjection, WorkerClaim
from .repository import SQLiteRuntimeRepository


@dataclass(frozen=True)
class TaskHandle:
    project_id: str
    thread_id: str
    run_id: str
    correlation_id: str


@dataclass(frozen=True)
class ActiveRun:
    claim: WorkerClaim
    projection: ThreadProjection
    checkpoint_id: str | None


class ThreadCoordinator:
    """Coordinates durable submission, scheduler claims, and recovery checkpoints."""

    def __init__(
        self,
        repository: SQLiteRuntimeRepository,
        artifacts: ArtifactCAS,
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts

    def submit_task(
        self,
        *,
        project_id: str,
        title: str,
        thread_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor: str = "inbox-router",
        priority: int = 0,
        max_attempts: int = 3,
        executor_key: str = "unassigned",
        task_snapshot_id: str | None = None,
        agent_snapshot_id: str | None = None,
        context_manifest_id: str | None = None,
    ) -> TaskHandle:
        thread_id = thread_id or uuid4().hex
        run_id = run_id or uuid4().hex
        correlation_id = correlation_id or uuid4().hex
        self.repository.submit_task(
            project_id=project_id,
            title=title,
            thread_id=thread_id,
            run_id=run_id,
            correlation_id=correlation_id,
            metadata=metadata,
            actor=actor,
            priority=priority,
            max_attempts=max_attempts,
            executor_key=executor_key,
            task_snapshot_id=task_snapshot_id,
            agent_snapshot_id=agent_snapshot_id,
            context_manifest_id=context_manifest_id,
        )
        return TaskHandle(
            project_id=project_id,
            thread_id=thread_id,
            run_id=run_id,
            correlation_id=correlation_id,
        )

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        executor_keys: Iterable[str] | None = None,
    ) -> ActiveRun | None:
        claim = self.repository.claim_next_run(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            executor_keys=executor_keys,
        )
        if claim is None:
            return None
        projection = self.repository.start_claimed_run(claim)
        run = projection.run(claim.run_id)
        assert run is not None
        return ActiveRun(
            claim=claim,
            projection=projection,
            checkpoint_id=run.latest_checkpoint_id,
        )

    def save_checkpoint(
        self,
        active: ActiveRun,
        state: dict[str, Any],
    ) -> tuple[ActiveRun, ArtifactRef]:
        checkpoint = self.artifacts.put_json(
            {
                "checkpoint_type": "run",
                "thread_id": active.claim.thread_id,
                "run_id": active.claim.run_id,
                "fencing_token": active.claim.fencing_token,
                "state": state,
            },
            kind="run_checkpoint",
        )
        projection = self.repository.record_claimed_run_checkpoint(
            active.claim,
            checkpoint_id=checkpoint.artifact_id,
        )
        return (
            ActiveRun(
                claim=active.claim,
                projection=projection,
                checkpoint_id=checkpoint.artifact_id,
            ),
            checkpoint,
        )

    def load_checkpoint(self, active: ActiveRun) -> dict[str, Any] | None:
        if active.checkpoint_id is None:
            return None
        value = self.artifacts.get_json(active.checkpoint_id)
        if not isinstance(value, dict) or value.get("checkpoint_type") != "run":
            raise ValueError("artifact is not a CoPenguin run checkpoint")
        if value.get("thread_id") != active.claim.thread_id:
            raise ValueError("checkpoint belongs to a different thread")
        if value.get("run_id") != active.claim.run_id:
            raise ValueError("checkpoint belongs to a different run")
        return value
