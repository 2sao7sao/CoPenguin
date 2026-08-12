from __future__ import annotations

import json

from fastapi.testclient import TestClient

from feishu_computer_agent.config import Settings
from feishu_computer_agent.server import create_app
from super_agent_runtime import ActionStatus, ApprovalState, ReceiptOutcome


def _payload(
    *,
    text: str = "/task 从来源生成一份可检查的产物",
    message_id: str = "om_v2_001",
    event_id: str = "event-1",
    actor_id: str = "ou_owner",
) -> dict[str, object]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "token": "token",
            "create_time": "1786608000000",
        },
        "event": {
            "sender": {"sender_id": {"open_id": actor_id}},
            "message": {
                "message_id": message_id,
                "chat_id": "oc_owner",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps(
                    {"text": text},
                    ensure_ascii=False,
                ),
            },
        },
    }


def test_feishu_retry_after_restart_keeps_one_route_and_one_task(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        memory_enabled=False,
        knowledge_enabled=False,
        feishu_verification_token="token",
        feishu_allowed_open_ids=frozenset({"ou_owner"}),
    )
    first_app = create_app(settings)
    first = TestClient(first_app).post("/feishu/events", json=_payload())

    restarted_app = create_app(settings)
    retry = TestClient(restarted_app).post("/feishu/events", json=_payload())

    assert first.status_code == 200
    assert retry.status_code == 200
    assert first.json()["duplicate"] is False
    assert retry.json()["duplicate"] is True
    assert first.json()["message_key"] == "feishu:om_v2_001"
    assert retry.json()["thread_id"] == first.json()["thread_id"]
    assert len(restarted_app.state.runtime.list_inbox_records()) == 1
    assert len(restarted_app.state.runtime.list_threads()) == 1


def test_feishu_computer_approval_uses_durable_runtime_after_restart(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        memory_enabled=False,
        knowledge_enabled=False,
        feishu_verification_token="token",
        feishu_allowed_open_ids=frozenset({"ou_owner"}),
    )
    first_app = create_app(settings)
    queued = TestClient(first_app).post(
        "/feishu/events",
        json=_payload(
            text="/computer open browser",
            message_id="om_computer_1",
            event_id="event-computer-1",
        ),
    )

    assert queued.status_code == 200
    assert queued.json()["requires_approval"] is True
    approvals = first_app.state.runtime.list_approvals(status=ApprovalState.PENDING)
    assert len(approvals) == 1
    approval = approvals[0]
    intent = first_app.state.runtime.get_action_intent(approval.intent_id)
    assert intent.status == ActionStatus.PENDING
    assert not hasattr(first_app.state, "approvals")

    restarted_app = create_app(settings)
    restarted_client = TestClient(restarted_app)
    approved = restarted_client.post(
        "/feishu/events",
        json=_payload(
            text=f"/approve {approval.approval_id}",
            message_id="om_approval_1",
            event_id="event-approval-1",
        ),
    )

    assert approved.status_code == 200
    assert approved.json()["intent"] == "approve"
    stored_approval = restarted_app.state.runtime.get_approval(approval.approval_id)
    stored_intent = restarted_app.state.runtime.get_action_intent(intent.intent_id)
    receipts = restarted_app.state.runtime.list_action_receipts(intent_id=intent.intent_id)
    assert stored_approval.status == ApprovalState.APPROVED
    assert stored_intent.status == ActionStatus.SUCCEEDED
    assert len(receipts) == 1
    assert receipts[0].outcome == ReceiptOutcome.SUCCEEDED
    detail = restarted_client.get(f"/runtime/actions/{intent.intent_id}")
    assert detail.status_code == 200
    assert detail.json()["receipts"][0]["outcome"] == "succeeded"
