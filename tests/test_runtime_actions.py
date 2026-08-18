from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from super_agent_runtime import (
    ActionStatus,
    ArtifactCAS,
    IdempotencyConflict,
    ReceiptOutcome,
    ReconciliationRequired,
    SQLiteRuntimeRepository,
    StaleLease,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 3, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _runtime_with_run(tmp_path, *, clock=None):
    repository = (
        SQLiteRuntimeRepository(tmp_path / "runtime.db", clock=clock)
        if clock
        else SQLiteRuntimeRepository(tmp_path / "runtime.db")
    )
    thread = repository.create_thread(
        thread_id="thread-1", project_id="personal", title="External action"
    )
    repository.create_run(thread.thread_id, run_id="run-1", expected_revision=thread.revision)
    artifacts = ArtifactCAS(tmp_path / "artifacts")
    request = artifacts.put_json(
        {"recipient": "user@example.test", "subject": "Draft only"},
        kind="action_request",
    )
    return repository, request


def test_action_intent_is_durable_and_idempotent_before_execution(tmp_path) -> None:
    repository, request = _runtime_with_run(tmp_path)

    first = repository.create_action_intent(
        intent_id="intent-1",
        thread_id="thread-1",
        run_id="run-1",
        capability="email.send",
        request_artifact_id=request.artifact_id,
        payload_hash=request.sha256,
        idempotency_key="email:message-1",
    )
    retry = repository.create_action_intent(
        thread_id="thread-1",
        run_id="run-1",
        capability="email.send",
        request_artifact_id=request.artifact_id,
        payload_hash=request.sha256,
        idempotency_key="email:message-1",
    )

    assert first == retry
    assert first.status == ActionStatus.PENDING

    with pytest.raises(IdempotencyConflict):
        repository.create_action_intent(
            thread_id="thread-1",
            run_id="run-1",
            capability="calendar.create",
            request_artifact_id=request.artifact_id,
            payload_hash=request.sha256,
            idempotency_key="email:message-1",
        )


def test_success_receipt_closes_action_and_retry_is_idempotent(tmp_path) -> None:
    repository, request = _runtime_with_run(tmp_path)
    intent = repository.create_action_intent(
        thread_id="thread-1",
        run_id="run-1",
        capability="email.send",
        request_artifact_id=request.artifact_id,
        payload_hash=request.sha256,
        idempotency_key="email:message-1",
    )
    claim = repository.claim_action(intent.intent_id, worker_id="worker-1")

    receipt = repository.record_action_receipt(
        claim,
        receipt_id="receipt-1",
        outcome=ReceiptOutcome.SUCCEEDED,
        provider="test-email",
        external_reference="provider-message-1",
        evidence={"accepted": True},
    )
    retry = repository.record_action_receipt(
        claim,
        receipt_id="receipt-1",
        outcome=ReceiptOutcome.SUCCEEDED,
        provider="test-email",
        external_reference="provider-message-1",
        evidence={"accepted": True},
    )

    assert retry == receipt
    assert repository.get_action_intent(intent.intent_id).status == ActionStatus.SUCCEEDED
    assert [
        event.event_type
        for event in repository.list_events(run_id="run-1")
        if event.stream_type == "action"
    ] == [
        "action.intent_created",
        "action.execution_claimed",
        "action.receipt_recorded",
    ]


def test_crash_requires_reconciliation_instead_of_blind_side_effect_retry(tmp_path) -> None:
    clock = FakeClock()
    repository, request = _runtime_with_run(tmp_path, clock=clock)
    intent = repository.create_action_intent(
        thread_id="thread-1",
        run_id="run-1",
        capability="calendar.update",
        request_artifact_id=request.artifact_id,
        payload_hash=request.sha256,
        idempotency_key="calendar:event-1:revision-7",
    )
    old_claim = repository.claim_action(intent.intent_id, worker_id="worker-old", lease_seconds=10)
    clock.advance(seconds=11)

    recovered = repository.recover_incomplete_actions()

    assert [item.intent_id for item in recovered] == [intent.intent_id]
    assert recovered[0].status == ActionStatus.RECONCILE_REQUIRED
    with pytest.raises(ReconciliationRequired):
        repository.claim_action(intent.intent_id, worker_id="worker-new")

    reconciliation = repository.claim_action(
        intent.intent_id,
        worker_id="worker-new",
        for_reconciliation=True,
    )
    repository.record_action_receipt(
        reconciliation,
        outcome=ReceiptOutcome.NOT_FOUND,
        provider="test-calendar",
        evidence={"provider_lookup": "not_found"},
    )

    assert repository.get_action_intent(intent.intent_id).status == ActionStatus.PENDING
    new_claim = repository.claim_action(intent.intent_id, worker_id="worker-new")
    assert new_claim.fencing_token > old_claim.fencing_token
    with pytest.raises(StaleLease):
        repository.record_action_receipt(
            old_claim,
            outcome=ReceiptOutcome.SUCCEEDED,
            provider="stale-worker",
        )

    repository.record_action_receipt(
        new_claim,
        outcome=ReceiptOutcome.SUCCEEDED,
        provider="test-calendar",
        external_reference="event-1-revision-8",
    )
    assert repository.get_action_intent(intent.intent_id).status == ActionStatus.SUCCEEDED
