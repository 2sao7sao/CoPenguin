import json

import pytest

from feishu_computer_agent.config import Settings
from feishu_computer_agent.feishu import FeishuChallenge, FeishuEventParser, FeishuPayloadError
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
