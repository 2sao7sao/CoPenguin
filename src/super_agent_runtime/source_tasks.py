from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from .artifacts import ArtifactCAS
from .coordinator import TaskHandle, ThreadCoordinator
from .models import to_timestamp, utc_now
from .repository import SQLiteRuntimeRepository
from .snapshots import ContextItem, ContextManifest, SnapshotStore, TaskSnapshot
from .source_artifact import SourceToArtifactExecutor


@dataclass(frozen=True)
class SourceSnapshotBinding:
    source_snapshot_id: str
    source_ref_id: str
    revision_id: str
    access_envelope_id: str
    content_artifact_id: str
    allowed_use: str = "artifact_only"
    sensitivity: str = "normal"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required = {
            "source_snapshot_id": self.source_snapshot_id,
            "source_ref_id": self.source_ref_id,
            "revision_id": self.revision_id,
            "access_envelope_id": self.access_envelope_id,
        }
        for name, value in required.items():
            normalized = value.strip()
            if not normalized:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, normalized)
        if not self.content_artifact_id.startswith("artifact:sha256:"):
            raise ValueError("content_artifact_id must be an Artifact CAS reference")
        if self.allowed_use not in {"direct", "artifact_only"}:
            raise ValueError("SourceSnapshot allowed_use must be direct or artifact_only")


@dataclass(frozen=True)
class SourceTaskHandle:
    task: TaskHandle
    task_snapshot_id: str
    agent_snapshot_id: str
    context_manifest_id: str
    source_snapshot_ids: tuple[str, ...]


class SourceToArtifactTaskCompiler:
    """Compiles explicitly selected, already-captured sources into a frozen Run."""

    def __init__(
        self,
        *,
        repository: SQLiteRuntimeRepository,
        artifacts: ArtifactCAS,
        agent_snapshot_id: str,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not agent_snapshot_id.startswith("artifact:sha256:"):
            raise ValueError("agent_snapshot_id must be an Artifact CAS reference")
        self.repository = repository
        self.artifacts = artifacts
        self.snapshots = SnapshotStore(artifacts)
        self.coordinator = ThreadCoordinator(repository, artifacts)
        self.agent_snapshot_id = agent_snapshot_id
        self.clock = clock

    def submit(
        self,
        *,
        project_id: str,
        objective: str,
        sources: Sequence[SourceSnapshotBinding],
        title: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
        actor: str = "source-task-compiler",
        acceptance_criteria: Sequence[str] = (),
        constraints: Sequence[str] = (),
        sensitivity: str = "normal",
        priority: int = 0,
        max_attempts: int = 3,
    ) -> SourceTaskHandle:
        project_id = project_id.strip()
        objective = objective.strip()
        if not project_id or not objective:
            raise ValueError("project_id and objective are required")
        if not sources:
            raise ValueError("at least one explicitly selected SourceSnapshot is required")
        source_ids = [source.source_snapshot_id for source in sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_snapshot_ids must be unique")
        for source in sources:
            if not self.artifacts.exists(source.content_artifact_id, verify=True):
                raise ValueError(
                    f"SourceSnapshot content is missing or corrupt: {source.source_snapshot_id}"
                )

        agent = self.snapshots.load(self.agent_snapshot_id)
        workflows = agent.get("capability_manifest", {}).get("workflows", ())
        if SourceToArtifactExecutor.key not in workflows:
            raise ValueError("Agent snapshot does not authorize the Source-to-Artifact workflow")

        thread_id = thread_id or uuid4().hex
        run_id = run_id or uuid4().hex
        correlation_id = correlation_id or uuid4().hex
        occurred_at = to_timestamp(self.clock())
        task_snapshot = self.snapshots.put_task(
            TaskSnapshot(
                task_id=thread_id,
                thread_id=thread_id,
                project_id=project_id,
                objective=objective,
                domain="work",
                acceptance_criteria=tuple(acceptance_criteria),
                constraints=tuple(constraints),
                input_artifact_ids=tuple(source.content_artifact_id for source in sources),
                sensitivity=sensitivity,
                created_at=occurred_at,
                workflow_id=SourceToArtifactExecutor.key,
            )
        )
        context_manifest = self.snapshots.put_context(
            ContextManifest(
                task_snapshot_id=task_snapshot.artifact_id,
                agent_snapshot_id=self.agent_snapshot_id,
                items=tuple(
                    ContextItem(
                        ordinal=ordinal,
                        source_type="source_snapshot",
                        source_ref=source.source_ref_id,
                        content_artifact_id=source.content_artifact_id,
                        allowed_use=source.allowed_use,
                        sensitivity=source.sensitivity,
                        metadata={
                            **dict(source.metadata),
                            "source_snapshot_id": source.source_snapshot_id,
                            "source_ref_id": source.source_ref_id,
                            "revision_id": source.revision_id,
                            "access_envelope_id": source.access_envelope_id,
                        },
                    )
                    for ordinal, source in enumerate(sources, start=1)
                ),
                compiler_version="source-to-artifact-task-v1",
                compiled_at=occurred_at,
            )
        )
        task = self.coordinator.submit_task(
            project_id=project_id,
            title=(title or objective)[:80],
            thread_id=thread_id,
            run_id=run_id,
            correlation_id=correlation_id,
            metadata={
                "source": "source_task_compiler",
                "workflow_id": SourceToArtifactExecutor.key,
                "source_snapshot_ids": source_ids,
                "actor": actor,
            },
            actor=actor,
            priority=priority,
            max_attempts=max_attempts,
            executor_key=SourceToArtifactExecutor.key,
            task_snapshot_id=task_snapshot.artifact_id,
            agent_snapshot_id=self.agent_snapshot_id,
            context_manifest_id=context_manifest.artifact_id,
        )
        return SourceTaskHandle(
            task=task,
            task_snapshot_id=task_snapshot.artifact_id,
            agent_snapshot_id=self.agent_snapshot_id,
            context_manifest_id=context_manifest.artifact_id,
            source_snapshot_ids=tuple(source_ids),
        )
