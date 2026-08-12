from __future__ import annotations

from datetime import UTC, datetime

import pytest

from super_agent_runtime import (
    AgentSnapshot,
    ArtifactCAS,
    ArtifactCorruption,
    ContextItem,
    ContextManifest,
    InvalidTransition,
    RunState,
    SnapshotStore,
    SQLiteRuntimeRepository,
    TaskSnapshot,
)

NOW = datetime(2026, 8, 3, tzinfo=UTC).isoformat()


def test_artifact_cas_deduplicates_and_detects_corruption(tmp_path) -> None:
    artifacts = ArtifactCAS(tmp_path / "artifacts")

    first = artifacts.put_text("same content", kind="model_output")
    second = artifacts.put_text("same content", kind="evidence")

    assert first.artifact_id == second.artifact_id
    assert artifacts.get_bytes(first) == b"same content"

    stored = tmp_path / "artifacts" / "sha256" / first.sha256[:2] / first.sha256[2:4] / first.sha256
    stored.write_bytes(b"tampered")

    with pytest.raises(ArtifactCorruption):
        artifacts.get_bytes(first)


def test_snapshots_are_canonical_and_context_order_is_explicit(tmp_path) -> None:
    artifacts = ArtifactCAS(tmp_path / "artifacts")
    snapshots = SnapshotStore(artifacts)
    source_a = artifacts.put_text("memory A", kind="context_item")
    source_b = artifacts.put_text("knowledge B", kind="context_item")
    task_ref = snapshots.put_task(
        TaskSnapshot(
            task_id="task-1",
            thread_id="thread-1",
            project_id="personal",
            objective="Plan tomorrow",
            domain="life",
            acceptance_criteria=("calendar conflicts checked",),
            created_at=NOW,
        )
    )
    agent_ref = snapshots.put_agent(
        AgentSnapshot(
            agent_id="copenguin-default",
            model_profile={"name": "test-model", "temperature": 0},
            tool_registry={"calendar.read": "v1"},
            capability_manifest={"calendar.read": "read_only"},
            created_at=NOW,
        )
    )
    first = snapshots.put_context(
        ContextManifest(
            task_snapshot_id=task_ref.artifact_id,
            agent_snapshot_id=agent_ref.artifact_id,
            items=(
                ContextItem(2, "kb", "kb:item-b", source_b.artifact_id),
                ContextItem(1, "memory", "memory:item-a", source_a.artifact_id),
            ),
            compiler_version="context-v1",
            compiled_at=NOW,
        )
    )
    second = snapshots.put_context(
        ContextManifest(
            task_snapshot_id=task_ref.artifact_id,
            agent_snapshot_id=agent_ref.artifact_id,
            items=(
                ContextItem(1, "memory", "memory:item-a", source_a.artifact_id),
                ContextItem(2, "kb", "kb:item-b", source_b.artifact_id),
            ),
            compiler_version="context-v1",
            compiled_at=NOW,
        )
    )

    assert first.artifact_id == second.artifact_id
    loaded = snapshots.load(first)
    assert [item["source_type"] for item in loaded["items"]] == ["memory", "kb"]


def test_run_binds_exact_snapshot_inputs_before_execution(tmp_path) -> None:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    thread = repository.create_thread(
        thread_id="thread-1", project_id="personal", title="Snapshot binding"
    )
    thread = repository.create_run(
        thread.thread_id, run_id="run-1", expected_revision=thread.revision
    )
    thread = repository.bind_run_snapshots(
        thread.thread_id,
        "run-1",
        task_snapshot_id="artifact:sha256:" + "1" * 64,
        agent_snapshot_id="artifact:sha256:" + "2" * 64,
        context_manifest_id="artifact:sha256:" + "3" * 64,
        expected_revision=thread.revision,
    )

    run = thread.run("run-1")
    assert run is not None
    assert run.task_snapshot_id == "artifact:sha256:" + "1" * 64
    assert repository.verify_thread_replay(thread.thread_id)

    with pytest.raises(InvalidTransition):
        repository.bind_run_snapshots(
            thread.thread_id,
            "run-1",
            task_snapshot_id="artifact:sha256:" + "4" * 64,
            agent_snapshot_id="artifact:sha256:" + "5" * 64,
            context_manifest_id="artifact:sha256:" + "6" * 64,
            expected_revision=thread.revision,
        )

    thread = repository.transition_run(
        thread.thread_id,
        "run-1",
        RunState.RUNNING,
        expected_revision=thread.revision,
        actor="worker",
    )
    assert thread.run("run-1").state == RunState.RUNNING
