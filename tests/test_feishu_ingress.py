from __future__ import annotations

import json

from fastapi.testclient import TestClient

from copenguin.demo import DEFAULT_SOURCE
from feishu_computer_agent.config import Settings
from feishu_computer_agent.server import create_app
from super_agent_runtime import (
    ActionStatus,
    ApprovalState,
    DecisionRecordVerifier,
    DeliveryState,
    ReceiptOutcome,
    SourceSnapshotBinding,
    SourceToArtifactExecutor,
    WorkerHost,
    WorkerHostConfig,
)


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


def _card_payload(*, approval_id: str, decision: str = "approve") -> dict[str, object]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"card-{decision}-{approval_id}",
            "event_type": "card.action.trigger",
            "create_time": "1786608000000",
        },
        "event": {
            "token": "token",
            "operator": {"open_id": "ou_owner"},
            "context": {
                "open_chat_id": "oc_owner",
                "open_message_id": "om_approval_card",
            },
            "action": {
                "value": {
                    "schema": "copenguin.approval.v1",
                    "decision": decision,
                    "approval_id": approval_id,
                }
            },
        },
    }


def _delivery_card_payload(
    *,
    delivery_id: str,
    decision: str = "accept",
    revision_request: str | None = None,
) -> dict[str, object]:
    action: dict[str, object] = {
        "value": {
            "schema": "copenguin.delivery.v1",
            "decision": decision,
            "delivery_id": delivery_id,
        }
    }
    if revision_request is not None:
        action["form_value"] = {"revision_request": revision_request}
    return {
        "schema": "2.0",
        "header": {
            "event_id": f"delivery-card-{decision}-{delivery_id}",
            "event_type": "card.action.trigger",
            "create_time": "1786608000000",
        },
        "event": {
            "token": "token",
            "operator": {"open_id": "ou_owner"},
            "context": {
                "open_chat_id": "oc_owner",
                "open_message_id": "om_delivery_card",
            },
            "action": action,
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


def test_feishu_approval_card_uses_same_durable_decision_path(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        memory_enabled=False,
        knowledge_enabled=False,
        feishu_verification_token="token",
        feishu_allowed_open_ids=frozenset({"ou_owner"}),
    )
    app = create_app(settings)
    client = TestClient(app)
    queued = client.post(
        "/feishu/events",
        json=_payload(
            text="/computer open browser",
            message_id="om_card_computer",
            event_id="event-card-computer",
        ),
    )
    approval = app.state.runtime.list_approvals(status=ApprovalState.PENDING)[0]

    decided = client.post(
        "/feishu/events",
        json=_card_payload(approval_id=approval.approval_id),
    )
    duplicate = client.post(
        "/feishu/events",
        json=_card_payload(approval_id=approval.approval_id),
    )

    assert queued.status_code == 200
    assert decided.status_code == 200
    assert decided.json()["toast"]["type"] == "success"
    assert duplicate.json()["toast"]["content"] == "Decision already recorded."
    assert app.state.runtime.get_approval(approval.approval_id).status == ApprovalState.APPROVED
    intent = app.state.runtime.get_action_intent(approval.intent_id)
    assert intent.status == ActionStatus.SUCCEEDED
    receipts = app.state.runtime.list_action_receipts(intent_id=intent.intent_id)
    assert len(receipts) == 1


def test_feishu_delivery_card_uses_replayable_delivery_decision_path(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        memory_enabled=False,
        knowledge_enabled=False,
        feishu_verification_token="token",
        feishu_allowed_open_ids=frozenset({"ou_owner"}),
    )
    app = create_app(settings)
    source = app.state.artifacts.put_json(DEFAULT_SOURCE, kind="feishu_delivery_source")
    submitted = app.state.source_tasks.submit(
        project_id="work",
        objective="Create an inspectable decision record",
        sources=(
            SourceSnapshotBinding(
                source_snapshot_id="feishu-delivery-source-1",
                source_ref_id="feishu:docx:delivery-source-1",
                revision_id=source.sha256,
                access_envelope_id="feishu-owner-selection",
                content_artifact_id=source.artifact_id,
            ),
        ),
    )
    completed = WorkerHost(
        repository=app.state.runtime,
        artifacts=app.state.artifacts,
        executors=(SourceToArtifactExecutor(app.state.artifacts),),
        verifiers=(DecisionRecordVerifier(app.state.artifacts),),
        config=WorkerHostConfig(worker_id="feishu-delivery-worker"),
    ).run_once()
    assert completed is not None and completed.delivery_id is not None
    app.state.runtime.present_delivery(completed.delivery_id, actor="feishu-outbox-test")
    client = TestClient(app)
    payload = _delivery_card_payload(delivery_id=completed.delivery_id)

    decided = client.post("/feishu/events", json=payload)
    duplicate = client.post("/feishu/events", json=payload)

    assert decided.status_code == 200
    assert decided.json()["toast"]["type"] == "success"
    assert duplicate.json()["toast"]["content"] == "Decision already recorded."
    delivery = app.state.runtime.get_delivery(completed.delivery_id)
    assert delivery.state == DeliveryState.ACCEPTED
    assert app.state.runtime.verify_delivery_replay(delivery.delivery_id) is True
    assert app.state.runtime.verify_thread_replay(submitted.task.thread_id) is True
    inbox = app.state.runtime.find_inbox_record(
        f"feishu:delivery-card-accept-{completed.delivery_id}"
    )
    assert inbox is not None and inbox.route_type.value == "control"


def test_feishu_ambiguous_continuation_waits_for_durable_route_command(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        memory_enabled=False,
        knowledge_enabled=False,
        feishu_verification_token="token",
        feishu_allowed_open_ids=frozenset({"ou_owner"}),
    )
    app = create_app(settings)
    client = TestClient(app)
    first = client.post(
        "/feishu/events",
        json=_payload(text="/task 任务 A", message_id="om_task_a", event_id="event-task-a"),
    ).json()
    second = client.post(
        "/feishu/events",
        json=_payload(text="/task 任务 B", message_id="om_task_b", event_id="event-task-b"),
    ).json()
    proposed = client.post(
        "/feishu/events",
        json=_payload(
            text="继续刚才那个方案",
            message_id="om_ambiguous",
            event_id="event-ambiguous",
        ),
    )

    assert proposed.status_code == 200
    assert proposed.json()["route_type"] == "ambiguous"
    assert proposed.json()["route_state"] == "proposed"
    assert app.state.runtime.get_thread(first["thread_id"]).updates == ()
    assert app.state.runtime.get_thread(second["thread_id"]).updates == ()

    resolved = client.post(
        "/feishu/events",
        json=_payload(
            text=f"/route feishu:om_ambiguous thread {second['thread_id']}",
            message_id="om_route_decision",
            event_id="event-route-decision",
        ),
    )

    assert resolved.status_code == 200
    assert resolved.json()["intent"] == "route"
    stored = app.state.runtime.find_inbox_record("feishu:om_ambiguous")
    assert stored is not None
    assert stored.route_state.value == "corrected"
    assert stored.thread_id == second["thread_id"]
    assert len(app.state.runtime.get_thread(second["thread_id"]).updates) == 1
