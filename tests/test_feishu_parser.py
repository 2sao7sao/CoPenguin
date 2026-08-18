import json

import pytest

from feishu_computer_agent.config import Settings
from feishu_computer_agent.feishu import (
    FeishuCardActionParser,
    FeishuChallenge,
    FeishuEventParser,
    FeishuMessenger,
    FeishuPayloadError,
)
from feishu_computer_agent.models import ChatType


def test_parse_url_verification() -> None:
    parser = FeishuEventParser(Settings(feishu_verification_token="token"))

    parsed = parser.parse({"type": "url_verification", "token": "token", "challenge": "abc"})

    assert isinstance(parsed, FeishuChallenge)
    assert parsed.challenge == "abc"


def test_rejects_wrong_verification_token() -> None:
    parser = FeishuEventParser(Settings(feishu_verification_token="token"))

    with pytest.raises(FeishuPayloadError):
        parser.parse({"type": "url_verification", "token": "wrong", "challenge": "abc"})


def test_webhook_rejects_callbacks_when_verification_token_is_not_configured() -> None:
    parser = FeishuEventParser(Settings())

    with pytest.raises(FeishuPayloadError, match="FEISHU_VERIFICATION_TOKEN is required"):
        parser.parse({"type": "url_verification", "token": "token", "challenge": "abc"})


def test_parse_text_message_event() -> None:
    parser = FeishuEventParser(Settings(feishu_verification_token="token"))
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "e1",
            "event_type": "im.message.receive_v1",
            "token": "token",
            "create_time": "1710000000000",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_owner", "union_id": "on_owner"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": "hello"}, ensure_ascii=False),
            },
        },
    }

    parsed = parser.parse(payload)

    assert parsed is not None
    assert not isinstance(parsed, FeishuChallenge)
    assert parsed.chat_type == ChatType.DIRECT
    assert parsed.text == "hello"
    assert parsed.actor_id == "on_owner"


def test_group_message_without_mention_is_ignored_when_required() -> None:
    parser = FeishuEventParser(Settings(feishu_verification_token="token"))
    payload = {
        "schema": "2.0",
        "header": {"event_id": "e1", "event_type": "im.message.receive_v1", "token": "token"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_owner"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "hello"}, ensure_ascii=False),
            },
        },
    }

    assert parser.parse(payload) is None


def test_group_message_requires_bot_mention_when_bot_open_id_is_configured() -> None:
    parser = FeishuEventParser(
        Settings(feishu_verification_token="token", feishu_bot_open_id="ou_bot")
    )
    payload = {
        "schema": "2.0",
        "header": {"event_id": "e1", "event_type": "im.message.receive_v1", "token": "token"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_owner"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "@_user_1 hello"}, ensure_ascii=False),
                "mentions": [{"key": "@_user_1", "id": {"open_id": "ou_someone_else"}}],
            },
        },
    }

    assert parser.parse(payload) is None

    payload["event"]["message"]["mentions"] = [{"key": "@_user_1", "id": {"open_id": "ou_bot"}}]
    parsed = parser.parse(payload)

    assert parsed is not None
    assert not isinstance(parsed, FeishuChallenge)
    assert parsed.text == "hello"


def test_parse_approval_card_action_as_control_message() -> None:
    parser = FeishuCardActionParser(Settings(feishu_verification_token="token"))
    parsed = parser.parse(
        {
            "header": {"event_id": "card-event-1", "create_time": "1786608000000"},
            "event": {
                "token": "token",
                "operator": {"open_id": "ou_owner", "union_id": "on_owner"},
                "context": {"open_chat_id": "oc_owner", "open_message_id": "om_card"},
                "action": {
                    "value": {
                        "schema": "copenguin.approval.v1",
                        "decision": "approve",
                        "approval_id": "approval-123",
                    }
                },
            },
        }
    )

    assert parsed.message_id == "card-event-1"
    assert parsed.text == "/approve approval-123"
    assert parsed.actor_id == "on_owner"


def test_card_action_rejects_wrong_verification_token() -> None:
    parser = FeishuCardActionParser(Settings(feishu_verification_token="token"))

    with pytest.raises(FeishuPayloadError, match="token mismatch"):
        parser.parse(
            {
                "header": {"event_id": "card-event-1"},
                "event": {
                    "token": "wrong",
                    "operator": {"open_id": "ou_owner"},
                    "context": {"open_chat_id": "oc_owner"},
                    "action": {
                        "value": {
                            "schema": "copenguin.approval.v1",
                            "decision": "approve",
                            "approval_id": "approval-123",
                        }
                    },
                },
            }
        )


def test_card_action_rejects_webhook_without_configured_token() -> None:
    parser = FeishuCardActionParser(Settings())

    with pytest.raises(FeishuPayloadError, match="FEISHU_VERIFICATION_TOKEN is required"):
        parser.parse(
            {
                "header": {"event_id": "card-event-1"},
                "event": {
                    "token": "token",
                    "operator": {"open_id": "ou_owner"},
                    "context": {"open_chat_id": "oc_owner"},
                    "action": {
                        "value": {
                            "schema": "copenguin.approval.v1",
                            "decision": "approve",
                            "approval_id": "approval-123",
                        }
                    },
                },
            }
        )


def test_approval_card_binds_both_buttons_to_exact_approval() -> None:
    card = FeishuMessenger(Settings()).approval_card(
        text="Approve this bounded action?",
        approval_id="approval-123",
    )

    actions = card["elements"][1]["actions"]
    assert [action["value"]["decision"] for action in actions] == ["approve", "deny"]
    assert {action["value"]["approval_id"] for action in actions} == {"approval-123"}
    assert {action["value"]["schema"] for action in actions} == {"copenguin.approval.v1"}
