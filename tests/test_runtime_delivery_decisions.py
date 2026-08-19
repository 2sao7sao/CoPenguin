from __future__ import annotations

import sqlite3

import pytest

from copenguin.demo import DEFAULT_SOURCE
from super_agent_runtime import (
    AgentSnapshot,
    ArtifactCAS,
    AttentionState,
    DecisionRecordVerifier,
    DeliveryDecisionService,
    DeliveryDecisionType,
    DeliveryState,
    DesiredState,
    IdempotencyConflict,
    InvalidTransition,
    JobState,
    RunState,
    SnapshotStore,
    SourceSnapshotBinding,
    SourceToArtifactExecutor,
    SourceToArtifactTaskCompiler,
    SQLiteRuntimeRepository,
    ThreadState,
    WorkerHost,
    WorkerHostConfig,
    WorkerRunStatus,
)


def _prepared_delivery(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    artifacts = ArtifactCAS(tmp_path / "artifacts")
    snapshots = SnapshotStore(artifacts)
    agent = snapshots.put_agent(
        AgentSnapshot(
            agent_id="delivery-decision-test-agent",
            model_profile={"provider": "deterministic-fixture"},
            tool_registry={},
            capability_manifest={"workflows": [SourceToArtifactExecutor.key]},
            created_at="2026-08-19T00:00:00Z",
        )
    )
    source = artifacts.put_json(DEFAULT_SOURCE, kind="delivery_decision_test_source")
    submitted = SourceToArtifactTaskCompiler(
        repository=repository,
        artifacts=artifacts,
        agent_snapshot_id=agent.artifact_id,
    ).submit(
        project_id="work",
        objective="Create an inspectable decision record",
        sources=(
            SourceSnapshotBinding(
                source_snapshot_id="delivery-source-1",
                source_ref_id="test:delivery-source-1",
                revision_id=source.sha256,
                access_envelope_id="local-owner",
                content_artifact_id=source.artifact_id,
            ),
        ),
        thread_id="thread-delivery",
        run_id="run-delivery-v1",
        max_attempts=2,
    )
    host = WorkerHost(
        repository=repository,
        artifacts=artifacts,
        executors=(SourceToArtifactExecutor(artifacts),),
        verifiers=(DecisionRecordVerifier(artifacts),),
        config=WorkerHostConfig(worker_id="delivery-test-worker"),
    )
    completed = host.run_once()
    assert completed is not None and completed.status == WorkerRunStatus.COMPLETED
    assert completed.delivery_id is not None
    service = DeliveryDecisionService(repository=repository, artifacts=artifacts)
    return repository, artifacts, submitted, host, service, completed.delivery_id


def test_accept_decision_is_idempotent_durable_and_replayable(tmp_path) -> None:
    repository, _, submitted, _, service, delivery_id = _prepared_delivery(tmp_path)
    repository.present_delivery(delivery_id, actor="local-control-room")

    result = service.decide(
        delivery_id,
        decision=DeliveryDecisionType.ACCEPT,
        actor="owner",
        idempotency_key="local:accept:1",
        reason="The evidence and decision are correct.",
    )
    retry = service.decide(
        delivery_id,
        decision="accept",
        actor="owner",
        idempotency_key="local:accept:1",
        reason="The evidence and decision are correct.",
    )

    assert result.delivery.state == DeliveryState.ACCEPTED
    assert result.decision.decision == DeliveryDecisionType.ACCEPT
    assert result.revision_job is None
    assert retry.decision.decision_id == result.decision.decision_id
    thread = repository.get_thread(submitted.task.thread_id)
    run = thread.run(submitted.task.run_id)
    assert run is not None and run.state == RunState.COMPLETED
    assert thread.actual_state == ThreadState.DELIVERED
    assert thread.attention_state == AttentionState.NONE
    assert repository.verify_thread_replay(thread.thread_id) is True
    assert repository.verify_delivery_replay(delivery_id) is True
    event_types = {event.event_type for event in repository.list_events(limit=1_000)}
    assert "delivery.decision_recorded" in event_types
    assert not any(
        event_type.startswith(("publish.", "memory.", "knowledge.")) for event_type in event_types
    )


@pytest.mark.parametrize(
    ("decision", "expected_state", "expected_desired_state"),
    [
        (DeliveryDecisionType.REJECT, DeliveryState.REJECTED, DesiredState.RUN),
        (DeliveryDecisionType.DEFER, DeliveryState.DEFERRED, DesiredState.RUN),
        (DeliveryDecisionType.TAKE_OVER, DeliveryState.TAKEN_OVER, DesiredState.PAUSE),
    ],
)
def test_terminal_owner_decisions_clear_attention_without_side_effects(
    tmp_path,
    decision,
    expected_state,
    expected_desired_state,
) -> None:
    repository, _, submitted, _, service, delivery_id = _prepared_delivery(tmp_path)
    repository.present_delivery(delivery_id, actor="local-control-room")

    result = service.decide(
        delivery_id,
        decision=decision,
        actor="owner",
        idempotency_key=f"local:{decision.value}:1",
    )

    assert result.delivery.state == expected_state
    thread = repository.get_thread(submitted.task.thread_id)
    assert thread.attention_state == AttentionState.NONE
    assert thread.desired_state == expected_desired_state
    assert len(thread.runs) == 1
    assert repository.verify_thread_replay(thread.thread_id) is True
    assert repository.verify_delivery_replay(delivery_id) is True


def test_revision_preserves_delivery_and_atomically_enqueues_snapshot_bound_run(tmp_path) -> None:
    repository, artifacts, submitted, host, service, delivery_id = _prepared_delivery(tmp_path)
    repository.present_delivery(delivery_id, actor="local-control-room")
    original_thread = repository.get_thread(submitted.task.thread_id)
    original_run = original_thread.run(submitted.task.run_id)
    assert original_run is not None

    result = service.decide(
        delivery_id,
        decision="revise",
        actor="owner",
        idempotency_key="local:revise:1",
        revision_request="Make the first action item explicitly owned by the Alpha lead.",
    )

    assert result.delivery.state == DeliveryState.REVISION_REQUESTED
    assert result.revision_job is not None
    assert result.revision_job.state == JobState.QUEUED
    assert result.decision.revision_run_id == result.revision_job.run_id
    revised_thread = repository.get_thread(submitted.task.thread_id)
    revised_run = revised_thread.run(result.revision_job.run_id)
    assert revised_run is not None and revised_run.state == RunState.QUEUED
    assert revised_run.supersedes_run_id == submitted.task.run_id
    assert revised_run.task_snapshot_id != original_run.task_snapshot_id
    assert revised_run.context_manifest_id != original_run.context_manifest_id
    assert revised_run.agent_snapshot_id == original_run.agent_snapshot_id
    assert revised_thread.actual_state == ThreadState.QUEUED
    assert revised_thread.attention_state == AttentionState.NONE
    revised_task = artifacts.get_json(revised_run.task_snapshot_id)
    revised_context = artifacts.get_json(revised_run.context_manifest_id)
    assert any("Alpha lead" in item for item in revised_task["constraints"])
    assert revised_context["items"][-1]["source_type"] == "delivery_revision_request"
    assert revised_context["items"][-1]["content_artifact_id"] == (
        result.decision.decision_artifact_id
    )
    assert repository.verify_thread_replay(revised_thread.thread_id) is True
    assert repository.verify_delivery_replay(delivery_id) is True

    completed = host.run_once()

    assert completed is not None and completed.status == WorkerRunStatus.COMPLETED
    assert completed.delivery_id is not None and completed.delivery_id != delivery_id
    second_delivery = repository.get_delivery(completed.delivery_id)
    assert second_delivery.version == 2
    assert second_delivery.previous_delivery_id == delivery_id
    assert repository.get_delivery(delivery_id).state == DeliveryState.REVISION_REQUESTED
    assert repository.verify_delivery_replay(second_delivery.delivery_id) is True


def test_revision_transaction_rolls_back_decision_run_and_scheduler_together(tmp_path) -> None:
    repository, _, submitted, _, service, delivery_id = _prepared_delivery(tmp_path)
    repository.present_delivery(delivery_id, actor="local-control-room")
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_revision_job
            BEFORE INSERT ON scheduler_jobs
            WHEN NEW.run_id != 'run-delivery-v1'
            BEGIN
                SELECT RAISE(ABORT, 'injected revision enqueue failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected revision enqueue failure"):
        service.decide(
            delivery_id,
            decision="revise",
            actor="owner",
            idempotency_key="local:revise:rollback",
            revision_request="Use a different owner.",
        )

    delivery = repository.get_delivery(delivery_id)
    thread = repository.get_thread(submitted.task.thread_id)
    assert delivery.state == DeliveryState.PRESENTED
    assert delivery.decision_id is None
    assert len(thread.runs) == 1
    assert thread.actual_state == ThreadState.DELIVERED
    assert thread.attention_state == AttentionState.DELIVERY_READY
    assert repository.find_delivery_decision(idempotency_key="local:revise:rollback") is None
    assert "delivery.decision_recorded" not in {
        event.event_type for event in repository.list_events(limit=1_000)
    }
    assert repository.verify_thread_replay(thread.thread_id) is True
    assert repository.verify_delivery_replay(delivery_id) is True


def test_decision_requires_presentation_and_revision_instruction(tmp_path) -> None:
    repository, _, _, _, service, delivery_id = _prepared_delivery(tmp_path)

    with pytest.raises(InvalidTransition, match="presented"):
        service.decide(
            delivery_id,
            decision="accept",
            actor="owner",
            idempotency_key="local:too-early",
        )

    repository.present_delivery(delivery_id, actor="local-control-room")
    with pytest.raises(ValueError, match="revision_request"):
        service.decide(
            delivery_id,
            decision="revise",
            actor="owner",
            idempotency_key="local:missing-revision",
        )


def test_decision_idempotency_key_cannot_be_reused_with_different_evidence(tmp_path) -> None:
    repository, _, _, _, service, delivery_id = _prepared_delivery(tmp_path)
    repository.present_delivery(delivery_id, actor="local-control-room")
    service.decide(
        delivery_id,
        decision="accept",
        actor="owner",
        idempotency_key="local:fixed-key",
        reason="Accepted after review.",
    )

    with pytest.raises(IdempotencyConflict, match="reused"):
        service.decide(
            delivery_id,
            decision="accept",
            actor="owner",
            idempotency_key="local:fixed-key",
            reason="Different evidence under the same key.",
        )


def test_schema_v9_migrates_existing_delivery_projection_in_place(tmp_path) -> None:
    database = tmp_path / "legacy-runtime.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE deliveries (
                delivery_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                run_id TEXT NOT NULL UNIQUE,
                version INTEGER NOT NULL,
                state TEXT NOT NULL,
                summary_artifact_id TEXT NOT NULL,
                primary_artifact_id TEXT NOT NULL,
                supporting_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                verifier_result_artifact_id TEXT NOT NULL,
                previous_delivery_id TEXT,
                allowed_decisions_json TEXT NOT NULL DEFAULT '[]',
                sensitivity TEXT NOT NULL,
                export_policy TEXT NOT NULL,
                created_at TEXT NOT NULL,
                presented_at TEXT,
                decided_at TEXT,
                UNIQUE(thread_id, version)
            )
            """
        )

    SQLiteRuntimeRepository(database)

    with sqlite3.connect(database) as connection:
        delivery_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(deliveries)").fetchall()
        }
        decision_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(delivery_decisions)").fetchall()
        }
        migrations = {
            int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")
        }
    assert {
        "decision_id",
        "decision_artifact_id",
        "decision_actor",
        "revision_run_id",
    }.issubset(delivery_columns)
    assert {"decision_id", "delivery_id", "idempotency_key"}.issubset(decision_columns)
    assert 9 in migrations
