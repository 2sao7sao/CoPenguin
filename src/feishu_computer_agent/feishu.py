from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from super_agent_runtime import IngressAdapter

from .agent import PrivateAssistantAgent
from .config import Settings
from .models import ChatType, InboundMessage
from .security import AccessController


class FeishuPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class FeishuChallenge:
    challenge: str


class OutboundMessenger(Protocol):
    async def send_text(self, *, chat_id: str, text: str) -> None: ...


class FeishuEventParser:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(self, payload: dict[str, Any]) -> InboundMessage | FeishuChallenge | None:
        if "encrypt" in payload:
            raise FeishuPayloadError(
                "Encrypted Feishu callbacks are not enabled in this MVP. "
                "Use long connection mode or disable webhook encryption for local development."
            )
        if payload.get("type") == "url_verification":
            self._verify_token(payload.get("token"))
            challenge = str(payload.get("challenge") or "")
            if not challenge:
                raise FeishuPayloadError("Missing challenge in Feishu verification payload.")
            return FeishuChallenge(challenge=challenge)

        header = payload.get("header") or {}
        if not isinstance(header, dict):
            raise FeishuPayloadError("Invalid Feishu event header.")
        self._verify_token(header.get("token"))
        event_type = header.get("event_type")
        if event_type != "im.message.receive_v1":
            return None

        event = payload.get("event") or {}
        if not isinstance(event, dict):
            raise FeishuPayloadError("Invalid Feishu event body.")
        return self._parse_message_event(header, event, payload)

    def _verify_token(self, token: Any) -> None:
        expected = self._settings.feishu_verification_token
        if expected and token != expected:
            raise FeishuPayloadError("Feishu verification token mismatch.")

    def _parse_message_event(
        self,
        header: dict[str, Any],
        event: dict[str, Any],
        raw: dict[str, Any],
    ) -> InboundMessage | None:
        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}
        message = event.get("message") or {}
        if not isinstance(sender_id, dict) or not isinstance(message, dict):
            raise FeishuPayloadError("Malformed Feishu message event.")

        chat_type = ChatType.GROUP if message.get("chat_type") == "group" else ChatType.DIRECT
        mentions = message.get("mentions") or []
        if (
            chat_type == ChatType.GROUP
            and self._settings.require_group_mention
            and not self._mentions_bot(mentions)
        ):
            return None

        text = self._extract_text(message)
        if mentions:
            for mention in mentions:
                if isinstance(mention, dict) and mention.get("key"):
                    text = text.replace(str(mention["key"]), "").strip()

        create_time = self._parse_create_time(
            header.get("create_time") or message.get("create_time")
        )
        return InboundMessage(
            message_id=str(message.get("message_id") or header.get("event_id") or ""),
            chat_id=str(message.get("chat_id") or ""),
            chat_type=chat_type,
            sender_open_id=str(sender_id.get("open_id") or ""),
            sender_union_id=sender_id.get("union_id"),
            text=text,
            raw=raw,
            created_at=create_time,
        )

    def _mentions_bot(self, mentions: Any) -> bool:
        if not mentions:
            return False
        if not self._settings.feishu_bot_open_id:
            return True
        for mention in mentions:
            if not isinstance(mention, dict):
                continue
            mention_id = mention.get("id") or {}
            if (
                isinstance(mention_id, dict)
                and mention_id.get("open_id") == self._settings.feishu_bot_open_id
            ):
                return True
        return False

    def _extract_text(self, message: dict[str, Any]) -> str:
        message_type = message.get("message_type")
        content_raw = message.get("content") or "{}"
        try:
            content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
        except json.JSONDecodeError:
            content = {"text": str(content_raw)}
        if not isinstance(content, dict):
            return str(content)
        if message_type == "text":
            return str(content.get("text") or "").strip()
        if message_type == "post":
            return self._extract_post_text(content).strip()
        return f"<{message_type or 'message'}>"

    def _extract_post_text(self, content: dict[str, Any]) -> str:
        lines: list[str] = []
        post = content.get("post") or {}
        for locale_payload in post.values() if isinstance(post, dict) else []:
            title = locale_payload.get("title") if isinstance(locale_payload, dict) else None
            if title:
                lines.append(str(title))
            for row in (
                locale_payload.get("content", []) if isinstance(locale_payload, dict) else []
            ):
                parts: list[str] = []
                for part in row if isinstance(row, list) else []:
                    if isinstance(part, dict) and part.get("text"):
                        parts.append(str(part["text"]))
                if parts:
                    lines.append("".join(parts))
        return "\n".join(lines)

    def _parse_create_time(self, value: Any) -> datetime:
        if value is None:
            return datetime.now(UTC)
        try:
            timestamp = int(value)
        except (TypeError, ValueError):
            return datetime.now(UTC)
        if timestamp > 10_000_000_000:
            timestamp = timestamp // 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)


class FeishuMessenger:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=20)
        self._tenant_token: str | None = None
        self._tenant_token_expire_at = 0.0

    async def send_text(self, *, chat_id: str, text: str) -> None:
        if not self._settings.feishu_app_id or not self._settings.feishu_app_secret:
            return
        token = await self._tenant_access_token()
        content = json.dumps({"text": text[:4000]}, ensure_ascii=False)
        response = await self._client.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={"receive_id": chat_id, "msg_type": "text", "content": content},
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") not in (0, None):
            raise RuntimeError(f"Feishu send_text failed: {body}")

    async def _tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_token and now < self._tenant_token_expire_at:
            return self._tenant_token
        response = await self._client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self._settings.feishu_app_id,
                "app_secret": self._settings.feishu_app_secret,
            },
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(f"Feishu tenant_access_token failed: {body}")
        self._tenant_token = body["tenant_access_token"]
        self._tenant_token_expire_at = now + max(60, int(body.get("expire", 7200)) - 120)
        return self._tenant_token


class FeishuWebhookService:
    def __init__(
        self,
        *,
        parser: FeishuEventParser,
        access: AccessController,
        agent: PrivateAssistantAgent,
        ingress: IngressAdapter,
        messenger: OutboundMessenger,
    ) -> None:
        self._parser = parser
        self._access = access
        self._agent = agent
        self._ingress = ingress
        self._messenger = messenger

    async def handle_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        parsed = self._parser.parse(payload)
        if isinstance(parsed, FeishuChallenge):
            return {"challenge": parsed.challenge}
        if parsed is None:
            return {"ok": True, "ignored": True}
        if not self._access.is_allowed(parsed):
            return {"ok": True, "ignored": True, "reason": "sender_not_allowed"}
        ingress = self._ingress.receive(
            message_id=parsed.message_id,
            chat_id=parsed.chat_id,
            actor_id=parsed.actor_id,
            text=parsed.text,
            created_at=parsed.created_at.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
        )
        if ingress.duplicate:
            return {
                "ok": True,
                "duplicate": True,
                "message_key": ingress.record.message_key,
                "route_type": ingress.record.route_type.value,
                "route_state": ingress.record.route_state.value,
                "thread_id": ingress.record.thread_id,
            }
        reply = await self._agent.handle(parsed, inbox_record=ingress.record)
        if reply.text:
            await self._messenger.send_text(chat_id=parsed.chat_id, text=reply.text)
        return {
            "ok": True,
            "duplicate": False,
            "message_key": ingress.record.message_key,
            "route_type": ingress.record.route_type.value,
            "route_state": ingress.record.route_state.value,
            "thread_id": ingress.record.thread_id,
            "intent": reply.intent.value,
            "requires_approval": reply.requires_approval,
        }
