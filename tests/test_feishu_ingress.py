from __future__ import annotations

import json

from fastapi.testclient import TestClient

from feishu_computer_agent.config import Settings
from feishu_computer_agent.server import create_app


def _payload() -> dict[str, object]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": "event-1",
            "event_type": "im.message.receive_v1",
            "token": "token",
            "create_time": "1786608000000",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_owner"}},
            "message": {
                "message_id": "om_v2_001",
                "chat_id": "oc_owner",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps(
                    {"text": "/task 从来源生成一份可检查的产物"},
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
