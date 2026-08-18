from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from .artifacts import ArtifactCAS, ArtifactRef


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    thread_id: str
    project_id: str
    objective: str
    domain: str
    acceptance_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    input_artifact_ids: tuple[str, ...] = ()
    sensitivity: str = "normal"
    created_at: str = ""
    schema_version: int = 2
    workflow_id: str = "unassigned"


@dataclass(frozen=True)
class AgentSnapshot:
    agent_id: str
    model_profile: Mapping[str, Any]
    tool_registry: Mapping[str, Any]
    capability_manifest: Mapping[str, Any]
    memory_policy_snapshot_id: str | None = None
    kb_snapshot_id: str | None = None
    skill_registry_snapshot_id: str | None = None
    hook_registry_snapshot_id: str | None = None
    tool_permission_snapshot_id: str | None = None
    created_at: str = ""
    schema_version: int = 1


@dataclass(frozen=True)
class ContextItem:
    ordinal: int
    source_type: str
    source_ref: str
    content_artifact_id: str
    allowed_use: str = "direct"
    sensitivity: str = "normal"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextManifest:
    task_snapshot_id: str
    agent_snapshot_id: str
    items: tuple[ContextItem, ...]
    compiler_version: str
    compiled_at: str
    schema_version: int = 1


class SnapshotStore:
    """Stores immutable runtime inputs in Artifact CAS using canonical JSON."""

    def __init__(self, artifacts: ArtifactCAS) -> None:
        self.artifacts = artifacts

    def put_task(self, snapshot: TaskSnapshot) -> ArtifactRef:
        return self.artifacts.put_json(
            {"snapshot_type": "task", **asdict(snapshot)},
            kind="task_snapshot",
        )

    def put_agent(self, snapshot: AgentSnapshot) -> ArtifactRef:
        return self.artifacts.put_json(
            {"snapshot_type": "agent", **asdict(snapshot)},
            kind="agent_snapshot",
        )

    def put_context(self, manifest: ContextManifest) -> ArtifactRef:
        ordered_items = tuple(sorted(manifest.items, key=lambda item: item.ordinal))
        ordinals = [item.ordinal for item in ordered_items]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("context item ordinals must be unique")
        normalized = ContextManifest(
            task_snapshot_id=manifest.task_snapshot_id,
            agent_snapshot_id=manifest.agent_snapshot_id,
            items=ordered_items,
            compiler_version=manifest.compiler_version,
            compiled_at=manifest.compiled_at,
            schema_version=manifest.schema_version,
        )
        return self.artifacts.put_json(
            {"snapshot_type": "context_manifest", **asdict(normalized)},
            kind="context_manifest",
        )

    def load(self, artifact: ArtifactRef | str) -> dict[str, Any]:
        value = self.artifacts.get_json(artifact)
        if not isinstance(value, dict) or "snapshot_type" not in value:
            raise ValueError("artifact is not a CoPenguin snapshot")
        return value
