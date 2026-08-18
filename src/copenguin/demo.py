"""Credential-free demonstration of the bounded Source-to-Artifact runtime."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from super_agent_runtime import (
    AgentSnapshot,
    ArtifactCAS,
    DecisionRecordVerifier,
    SnapshotStore,
    SourceSnapshotBinding,
    SourceToArtifactExecutor,
    SourceToArtifactTaskCompiler,
    SQLiteRuntimeRepository,
    WorkerHost,
    WorkerHostConfig,
    WorkerRunStatus,
)

DEFAULT_SOURCE: dict[str, Any] = {
    "title": "CoPenguin Alpha launch decision",
    "background": [
        "The team needs one trustworthy path before expanding agent autonomy.",
    ],
    "facts": [
        "The Alpha path is Source to Inspectable Artifact.",
        "Runtime state is stored locally and external actions remain approval-gated.",
    ],
    "decisions": [
        "Ship the bounded Source-to-Artifact workflow before autonomous learning.",
    ],
    "action_items": [
        "Review the generated decision record and its source citation.",
    ],
    "open_questions": [
        "Which real user workflow should enter the first four-week pilot?",
    ],
    "risks": [
        "A technically correct runtime may still fail to create repeat user value.",
    ],
}


def load_demo_source(path: Path | None) -> dict[str, Any]:
    if path is None:
        return dict(DEFAULT_SOURCE)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"demo source does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"demo source must be valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("demo source must contain one JSON object")
    return value


def run_source_to_artifact_demo(
    *,
    data_dir: Path,
    source: Mapping[str, Any] | None = None,
    project_id: str = "demo",
) -> dict[str, Any]:
    """Run one isolated, deterministic task without an account or network call."""

    demo_id = uuid4().hex
    runtime_dir = data_dir / "demos" / demo_id
    repository = SQLiteRuntimeRepository(runtime_dir / "runtime.db")
    artifacts = ArtifactCAS(runtime_dir / "artifacts")
    snapshots = SnapshotStore(artifacts)
    source_payload = dict(source or DEFAULT_SOURCE)
    source_artifact = artifacts.put_json(source_payload, kind="demo_source_snapshot_content")
    agent_snapshot = snapshots.put_agent(
        AgentSnapshot(
            agent_id="copenguin-demo-source-to-artifact-v1",
            model_profile={"provider": "deterministic-fixture"},
            tool_registry={},
            capability_manifest={"workflows": [SourceToArtifactExecutor.key]},
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    compiler = SourceToArtifactTaskCompiler(
        repository=repository,
        artifacts=artifacts,
        agent_snapshot_id=agent_snapshot.artifact_id,
    )
    submitted = compiler.submit(
        project_id=project_id,
        objective="Create an inspectable Project Decision Record from the selected source",
        sources=(
            SourceSnapshotBinding(
                source_snapshot_id=f"demo-source-{source_artifact.sha256[:16]}",
                source_ref_id="copenguin:built-in-demo",
                revision_id=source_artifact.sha256,
                access_envelope_id="local-user-selected-demo",
                content_artifact_id=source_artifact.artifact_id,
            ),
        ),
        actor="local-demo",
        max_attempts=1,
    )
    host = WorkerHost(
        repository=repository,
        artifacts=artifacts,
        executors=(SourceToArtifactExecutor(artifacts),),
        verifiers=(DecisionRecordVerifier(artifacts),),
        config=WorkerHostConfig(worker_id=f"demo-worker-{demo_id[:12]}"),
    )
    result = host.run_once()
    if result is None:
        raise RuntimeError("demo worker did not claim the queued task")
    if result.status != WorkerRunStatus.COMPLETED or result.output_artifact_id is None:
        raise RuntimeError(
            f"demo task did not complete: status={result.status.value} error={result.error}"
        )
    artifact = artifacts.get_json(result.output_artifact_id)
    projection = repository.get_thread(submitted.task.thread_id)
    return {
        "demo_id": demo_id,
        "runtime_dir": str(runtime_dir.resolve()),
        "thread_id": submitted.task.thread_id,
        "run_id": submitted.task.run_id,
        "status": result.status.value,
        "replay_verified": repository.verify_thread_replay(submitted.task.thread_id),
        "artifact_id": result.output_artifact_id,
        "verifier_result_artifact_id": result.verifier_result_artifact_id,
        "delivery_id": result.delivery_id,
        "outbox_id": result.outbox_id,
        "thread_state": projection.actual_state.value,
        "artifact": artifact,
    }
