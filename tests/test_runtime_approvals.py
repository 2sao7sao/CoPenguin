from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from super_agent_runtime import (
    ActionStatus,
    ApprovalState,
    ArtifactCAS,
    AttentionState,
    InvalidTransition,
    SQLiteRuntimeRepository,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 3, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _approval_runtime(tmp_path, clock=None):
    repository = (
        SQLiteRuntimeRepository(tmp_path / "runtime.db", clock=clock)
        if clock
        else SQLiteRuntimeRepository(tmp_path / "runtime.db")
    )
    thread = repository.create_thread(thread_id="thread-1", project_id="personal", title="Approval")
    repository.create_run(thread.thread_id, run_id="run-1", expected_revision=thread.revision)
    request = ArtifactCAS(tmp_path / "artifacts").put_json(
        {"recipient": "person@example.test", "body": "hello"},
        kind="action_request",
    )
    intent = repository.create_action_intent(
        thread_id="thread-1",
        run_id="run-1",
        capability="email.send",
        request_artifact_id=request.artifact_id,
        payload_hash=request.sha256,
        idempotency_key="email:approval-test",
        requires_approval=True,
    )
    return repository, intent


def test_action_cannot_execute_until_persistent_approval_is_granted(tmp_path) -> None:
    repository, intent = _approval_runtime(tmp_path)

    with pytest.raises(InvalidTransition):
        repository.claim_action(intent.intent_id, worker_id="worker")

    approval = repository.create_approval(
        approval_id="approval-1",
        intent_id=intent.intent_id,
        risk_level="high",
        requested_by="copenguin",
        reason="Sending email affects another person",
    )

    assert approval.status == ApprovalState.PENDING
    assert repository.get_thread("thread-1").attention_state == AttentionState.NEEDS_APPROVAL
    with pytest.raises(InvalidTransition):
        repository.claim_action(intent.intent_id, worker_id="worker")

    approved = repository.decide_approval(
        approval.approval_id,
        decision=ApprovalState.APPROVED,
        actor="owner",
    )
    claim = repository.claim_action(intent.intent_id, worker_id="worker")

    assert approved.status == ApprovalState.APPROVED
    assert claim.intent_id == intent.intent_id
    assert repository.get_thread("thread-1").attention_state == AttentionState.NONE


def test_denied_approval_cancels_action_intent(tmp_path) -> None:
    repository, intent = _approval_runtime(tmp_path)
    approval = repository.create_approval(
        intent_id=intent.intent_id,
        risk_level="high",
        requested_by="copenguin",
        reason="External write",
    )

    denied = repository.decide_approval(
        approval.approval_id,
        decision=ApprovalState.DENIED,
        actor="owner",
    )

    assert denied.status == ApprovalState.DENIED
    assert repository.get_action_intent(intent.intent_id).status == ActionStatus.CANCELLED
    with pytest.raises(InvalidTransition):
        repository.claim_action(intent.intent_id, worker_id="worker")


def test_expired_approval_is_durable_and_cannot_be_approved_late(tmp_path) -> None:
    clock = FakeClock()
    repository, intent = _approval_runtime(tmp_path, clock=clock)
    approval = repository.create_approval(
        intent_id=intent.intent_id,
        risk_level="high",
        requested_by="copenguin",
        reason="External write",
        ttl_seconds=10,
    )
    clock.advance(seconds=11)

    expired = repository.expire_pending_approvals()

    assert [item.approval_id for item in expired] == [approval.approval_id]
    assert repository.get_approval(approval.approval_id).status == ApprovalState.EXPIRED
    assert repository.get_action_intent(intent.intent_id).status == ActionStatus.CANCELLED
    assert repository.get_thread("thread-1").attention_state == AttentionState.NONE
    with pytest.raises(InvalidTransition):
        repository.decide_approval(
            approval.approval_id,
            decision=ApprovalState.APPROVED,
            actor="owner",
        )
