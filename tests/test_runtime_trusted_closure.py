from __future__ import annotations

import sqlite3

import pytest

from copenguin.demo import DEFAULT_SOURCE
from super_agent_runtime import (
    AgentSnapshot,
    ArtifactCAS,
    DecisionRecordVerifier,
    JobState,
    RunState,
    SnapshotStore,
    SourceSnapshotBinding,
    SourceToArtifactExecutor,
    SourceToArtifactTaskCompiler,
    SQLiteRuntimeRepository,
    StepState,
    ThreadCoordinator,
    ThreadState,
    WorkerHost,
    WorkerHostConfig,
    WorkerRunStatus,
)


def _queued_source_task(tmp_path, source=None):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    artifacts = ArtifactCAS(tmp_path / "artifacts")
    snapshots = SnapshotStore(artifacts)
    agent = snapshots.put_agent(
        AgentSnapshot(
            agent_id="closure-test-agent",
            model_profile={"provider": "deterministic-fixture"},
            tool_registry={},
            capability_manifest={"workflows": [SourceToArtifactExecutor.key]},
            created_at="2026-08-18T00:00:00Z",
        )
    )
    source_artifact = artifacts.put_json(
        dict(DEFAULT_SOURCE if source is None else source),
        kind="test_source",
    )
    compiler = SourceToArtifactTaskCompiler(
        repository=repository,
        artifacts=artifacts,
        agent_snapshot_id=agent.artifact_id,
    )
    handle = compiler.submit(
        project_id="test-project",
        objective="Create an inspectable decision record",
        sources=(
            SourceSnapshotBinding(
                source_snapshot_id="source-1",
                source_ref_id="test:source-1",
                revision_id=source_artifact.sha256,
                access_envelope_id="test-owner-selection",
                content_artifact_id=source_artifact.artifact_id,
            ),
        ),
        thread_id="thread-closure",
        run_id="run-closure",
        max_attempts=1,
    )
    return repository, artifacts, handle


def _verified_host(repository, artifacts) -> WorkerHost:
    return WorkerHost(
        repository=repository,
        artifacts=artifacts,
        executors=(SourceToArtifactExecutor(artifacts),),
        verifiers=(DecisionRecordVerifier(artifacts),),
        config=WorkerHostConfig(worker_id="closure-worker"),
    )


def test_verified_worker_records_steps_delivery_attention_and_outbox(tmp_path) -> None:
    repository, artifacts, handle = _queued_source_task(tmp_path)

    result = _verified_host(repository, artifacts).run_once()

    assert result is not None and result.status == WorkerRunStatus.COMPLETED
    assert result.delivery_id is not None
    assert result.outbox_id is not None
    assert result.verifier_result_artifact_id is not None
    steps = repository.list_steps(run_id=handle.task.run_id)
    assert [step.state for step in steps] == [StepState.SUCCEEDED, StepState.SUCCEEDED]
    assert [step.kind.value for step in steps] == ["transform", "verifier"]
    delivery = repository.get_delivery(result.delivery_id)
    assert delivery.primary_artifact_id == result.output_artifact_id
    assert delivery.verifier_result_artifact_id == result.verifier_result_artifact_id
    assert delivery.state.value == "prepared"
    outbox = repository.list_outbox()
    assert len(outbox) == 1
    assert outbox[0].outbox_id == result.outbox_id
    assert outbox[0].state.value == "pending"
    thread = repository.get_thread(handle.task.thread_id)
    run = thread.run(handle.task.run_id)
    assert run is not None and run.state == RunState.COMPLETED
    assert thread.actual_state == ThreadState.DELIVERED
    assert thread.attention_state.value == "delivery_ready"
    assert thread.latest_delivery_id == result.delivery_id
    assert repository.get_job(handle.task.run_id).state == JobState.COMPLETED
    assert repository.verify_thread_replay(handle.task.thread_id) is True
    events = repository.list_events(run_id=handle.task.run_id, limit=100)
    event_types = {event.event_type for event in events}
    assert {
        "step.created",
        "step.started",
        "step.output_recorded",
        "step.succeeded",
        "delivery.prepared",
        "delivery.notification_enqueued",
        "scheduler.run_completed",
    }.issubset(event_types)


def test_failed_verifier_never_prepares_delivery(tmp_path) -> None:
    source = {
        "title": "Incomplete decision record",
        "background": [],
        "facts": [],
        "decisions": [],
        "action_items": [],
        "open_questions": [],
        "risks": [],
    }
    repository, artifacts, handle = _queued_source_task(tmp_path, source)

    result = _verified_host(repository, artifacts).run_once()

    assert result is not None and result.status == WorkerRunStatus.FAILED
    assert result.error_code == "verification_failed"
    assert repository.list_deliveries() == []
    assert repository.list_outbox() == []
    steps = repository.list_steps(run_id=handle.task.run_id)
    assert [step.state for step in steps] == [StepState.SUCCEEDED, StepState.FAILED]
    assert steps[1].verifier_result_artifact_id is not None
    report = artifacts.get_json(steps[1].verifier_result_artifact_id)
    assert report["checks"]["actionability"] is False
    thread = repository.get_thread(handle.task.thread_id)
    assert thread.actual_state == ThreadState.FAILED
    assert repository.get_job(handle.task.run_id).state == JobState.FAILED


def test_finalize_delivery_rolls_back_every_terminal_surface_on_outbox_failure(tmp_path) -> None:
    repository, artifacts, handle = _queued_source_task(tmp_path)
    active = ThreadCoordinator(repository, artifacts).claim_next(
        worker_id="atomic-worker",
        lease_seconds=30,
    )
    assert active is not None
    primary = artifacts.put_json({"artifact_type": "verified"}, kind="verified")
    report = artifacts.put_json({"verdict": "passed"}, kind="verifier_result")
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_outbox_insert
            BEFORE INSERT ON runtime_outbox
            BEGIN
                SELECT RAISE(ABORT, 'injected outbox failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected outbox failure"):
        repository.finalize_claimed_delivery(
            active.claim,
            executor_key=SourceToArtifactExecutor.key,
            executor_version=SourceToArtifactExecutor.version,
            primary_artifact_id=primary.artifact_id,
            verifier_result_artifact_id=report.artifact_id,
        )

    thread = repository.get_thread(handle.task.thread_id)
    run = thread.run(handle.task.run_id)
    assert run is not None and run.state == RunState.RUNNING
    assert thread.actual_state == ThreadState.RUNNING
    assert thread.latest_delivery_id is None
    assert repository.get_job(handle.task.run_id).state == JobState.CLAIMED
    assert repository.list_deliveries() == []
    assert repository.list_outbox() == []
    assert repository.verify_thread_replay(handle.task.thread_id) is True
    event_types = {
        event.event_type for event in repository.list_events(run_id=handle.task.run_id, limit=100)
    }
    assert "delivery.prepared" not in event_types
    assert "scheduler.run_completed" not in event_types
