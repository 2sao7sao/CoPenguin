from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from super_agent_runtime import DeliveryDecisionService, DeliveryDecisionType, IngressAdapter

from .agent import PrivateAssistantAgent
from .config import Settings
from .models import ChatType, InboundMessage
from .security import AccessController


class FeishuPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class FeishuChallenge:
    challenge: str


@dataclass(frozen=True)
class FeishuDeliveryDecision:
    message: InboundMessage
    delivery_id: str
    decision: DeliveryDecisionType
    idempotency_key: str
    revision_request: str | None = None


class OutboundMessenger(Protocol):
    async def send_text(self, *, chat_id: str, text: str) -> None: ...

    async def send_approval_card(
        self,
        *,
        chat_id: str,
        text: str,
        approval_id: str,
    ) -> None: ...

    async def send_delivery_card(
        self,
        *,
        chat_id: str,
        text: str,
        delivery_id: str,
    ) -> None: ...


class FeishuEventParser:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(
        self,
        payload: dict[str, Any],
        *,
        trusted_long_connection: bool = False,
    ) -> InboundMessage | FeishuChallenge | None:
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
        if not trusted_long_connection:
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
        if not expected:
            raise FeishuPayloadError("FEISHU_VERIFICATION_TOKEN is required for webhook callbacks.")
        if token != expected:
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


class FeishuCardActionParser:
    """Parse signed approval and Delivery card actions without executing them."""

    _ENTITY_ID = re.compile(r"^[A-Za-z0-9_-]+$")

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._message_parser = FeishuEventParser(settings)

    def parse(
        self,
        payload: dict[str, Any],
        *,
        trusted_long_connection: bool = False,
    ) -> InboundMessage | FeishuDeliveryDecision:
        header = payload.get("header") or {}
        event = payload.get("event") or {}
        if not isinstance(header, dict) or not isinstance(event, dict):
            raise FeishuPayloadError("Malformed Feishu card callback.")
        expected_token = self._settings.feishu_verification_token
        received_token = event.get("token") or header.get("token")
        if not trusted_long_connection:
            if not expected_token:
                raise FeishuPayloadError(
                    "FEISHU_VERIFICATION_TOKEN is required for webhook callbacks."
                )
            if received_token != expected_token:
                raise FeishuPayloadError("Feishu card verification token mismatch.")
        operator = event.get("operator") or {}
        action = event.get("action") or {}
        context = event.get("context") or {}
        if not isinstance(operator, dict) or not isinstance(action, dict):
            raise FeishuPayloadError("Malformed Feishu card action.")
        if not isinstance(context, dict):
            raise FeishuPayloadError("Malformed Feishu card context.")
        value = action.get("value") or {}
        if not isinstance(value, dict):
            raise FeishuPayloadError("Unsupported Feishu card action schema.")
        open_id = str(operator.get("open_id") or "")
        chat_id = str(context.get("open_chat_id") or "")
        event_id = str(header.get("event_id") or "")
        open_message_id = str(context.get("open_message_id") or "")
        if not open_id or not chat_id:
            raise FeishuPayloadError("Card callback identity and chat context are required.")
        schema = value.get("schema")
        if schema == "copenguin.approval.v1":
            decision = str(value.get("decision") or "").lower()
            if decision not in {"approve", "deny"}:
                raise FeishuPayloadError("Card decision must be approve or deny.")
            approval_id = str(value.get("approval_id") or "")
            if not self._ENTITY_ID.fullmatch(approval_id):
                raise FeishuPayloadError("Card approval id is invalid.")
            message_id = event_id or (f"card-{open_message_id}-{approval_id}-{decision}-{open_id}")
            if not message_id:
                raise FeishuPayloadError("Card callback identity and chat context are required.")
            return InboundMessage(
                platform="feishu",
                message_id=message_id,
                chat_id=chat_id,
                chat_type=ChatType.DIRECT,
                sender_open_id=open_id,
                sender_union_id=operator.get("union_id"),
                text=f"/{decision} {approval_id}",
                raw=payload,
                created_at=self._message_parser._parse_create_time(header.get("create_time")),
            )

        if schema != "copenguin.delivery.v1":
            raise FeishuPayloadError("Unsupported Feishu card action schema.")
        delivery_id = str(value.get("delivery_id") or "")
        if not self._ENTITY_ID.fullmatch(delivery_id):
            raise FeishuPayloadError("Card Delivery id is invalid.")
        try:
            delivery_decision = DeliveryDecisionType(str(value.get("decision") or "").lower())
        except ValueError as exc:
            raise FeishuPayloadError("Unsupported Delivery decision.") from exc
        form_value = action.get("form_value") or event.get("form_value") or {}
        if not isinstance(form_value, dict):
            raise FeishuPayloadError("Malformed Feishu card form values.")
        revision_request = str(
            value.get("revision_request") or form_value.get("revision_request") or ""
        ).strip()
        if delivery_decision == DeliveryDecisionType.REVISE and not revision_request:
            raise FeishuPayloadError("A revision request is required before choosing revise.")
        if len(revision_request) > DeliveryDecisionService.max_text_length:
            raise FeishuPayloadError("The revision request is too long.")
        message_id = event_id or (
            f"card-{open_message_id}-{delivery_id}-{delivery_decision.value}-{open_id}"
        )
        if not message_id:
            raise FeishuPayloadError("Card callback identity and chat context are required.")
        message = InboundMessage(
            platform="feishu",
            message_id=message_id,
            chat_id=chat_id,
            chat_type=ChatType.DIRECT,
            sender_open_id=open_id,
            sender_union_id=operator.get("union_id"),
            text=f"/delivery {delivery_decision.value} {delivery_id}",
            raw=payload,
            created_at=self._message_parser._parse_create_time(header.get("create_time")),
        )
        return FeishuDeliveryDecision(
            message=message,
            delivery_id=delivery_id,
            decision=delivery_decision,
            idempotency_key=f"feishu:{message_id}",
            revision_request=revision_request or None,
        )


class FeishuMessenger:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=20)
        self._tenant_token: str | None = None
        self._tenant_token_expire_at = 0.0

    async def send_text(self, *, chat_id: str, text: str) -> None:
        await self._send_message(
            chat_id=chat_id,
            msg_type="text",
            content={"text": text[:4000]},
        )

    async def send_approval_card(
        self,
        *,
        chat_id: str,
        text: str,
        approval_id: str,
    ) -> None:
        await self._send_message(
            chat_id=chat_id,
            msg_type="interactive",
            content=self.approval_card(text=text, approval_id=approval_id),
        )

    async def send_delivery_card(
        self,
        *,
        chat_id: str,
        text: str,
        delivery_id: str,
    ) -> None:
        await self._send_message(
            chat_id=chat_id,
            msg_type="interactive",
            content=self.delivery_card(text=text, delivery_id=delivery_id),
        )

    def approval_card(self, *, text: str, approval_id: str) -> dict[str, Any]:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"tag": "plain_text", "content": "CoPenguin action approval"},
            },
            "elements": [
                {"tag": "markdown", "content": text[:3000]},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "Approve"},
                            "type": "primary",
                            "value": {
                                "schema": "copenguin.approval.v1",
                                "decision": "approve",
                                "approval_id": approval_id,
                            },
                            "confirm": {
                                "title": {"tag": "plain_text", "content": "Approve action?"},
                                "text": {
                                    "tag": "plain_text",
                                    "content": "CoPenguin will execute the exact bound action.",
                                },
                            },
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "Deny"},
                            "type": "danger",
                            "value": {
                                "schema": "copenguin.approval.v1",
                                "decision": "deny",
                                "approval_id": approval_id,
                            },
                        },
                    ],
                },
            ],
        }

    def delivery_card(self, *, text: str, delivery_id: str) -> dict[str, Any]:
        def button(
            label: str,
            decision: DeliveryDecisionType,
            *,
            button_type: str = "default",
        ) -> dict[str, Any]:
            return {
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "type": button_type,
                "value": {
                    "schema": "copenguin.delivery.v1",
                    "decision": decision.value,
                    "delivery_id": delivery_id,
                },
            }

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "CoPenguin Delivery review"},
            },
            "elements": [
                {"tag": "markdown", "content": text[:3000]},
                {
                    "tag": "input",
                    "name": "revision_request",
                    "placeholder": {
                        "tag": "plain_text",
                        "content": "Describe the change before choosing Revise",
                    },
                },
                {
                    "tag": "action",
                    "actions": [
                        button("Accept", DeliveryDecisionType.ACCEPT, button_type="primary"),
                        button("Revise", DeliveryDecisionType.REVISE),
                        button("Reject", DeliveryDecisionType.REJECT, button_type="danger"),
                        button("Later", DeliveryDecisionType.DEFER),
                        button("Take over", DeliveryDecisionType.TAKE_OVER),
                    ],
                },
            ],
        }

    async def _send_message(
        self,
        *,
        chat_id: str,
        msg_type: str,
        content: dict[str, Any],
    ) -> None:
        if not self._settings.feishu_app_id or not self._settings.feishu_app_secret:
            return
        token = await self._tenant_access_token()
        response = await self._client.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": chat_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False),
            },
        )
        response.raise_for_status()
        body = response.json()
        if body.get("code") not in (0, None):
            raise RuntimeError(f"Feishu send message failed: {body}")

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
        token = body.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Feishu tenant_access_token response did not include a token")
        self._tenant_token = token
        self._tenant_token_expire_at = now + max(60, int(body.get("expire", 7200)) - 120)
        return token


class FeishuWebhookService:
    def __init__(
        self,
        *,
        parser: FeishuEventParser,
        access: AccessController,
        agent: PrivateAssistantAgent,
        ingress: IngressAdapter,
        messenger: OutboundMessenger,
        card_parser: FeishuCardActionParser | None = None,
        delivery_decisions: DeliveryDecisionService | None = None,
    ) -> None:
        self._parser = parser
        self._access = access
        self._agent = agent
        self._ingress = ingress
        self._messenger = messenger
        self._card_parser = card_parser
        self._delivery_decisions = delivery_decisions

    async def handle_payload(
        self,
        payload: dict[str, Any],
        *,
        trusted_long_connection: bool = False,
    ) -> dict[str, Any]:
        parsed = self._parser.parse(
            payload,
            trusted_long_connection=trusted_long_connection,
        )
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
            if reply.requires_approval and reply.approval_id:
                await self._messenger.send_approval_card(
                    chat_id=parsed.chat_id,
                    text=reply.text,
                    approval_id=reply.approval_id,
                )
            else:
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

    async def handle_card_action(
        self,
        payload: dict[str, Any],
        *,
        trusted_long_connection: bool = False,
    ) -> dict[str, Any]:
        if self._card_parser is None:
            raise FeishuPayloadError("Feishu card actions are not configured.")
        parsed = self._card_parser.parse(
            payload,
            trusted_long_connection=trusted_long_connection,
        )
        message = parsed.message if isinstance(parsed, FeishuDeliveryDecision) else parsed
        if not self._access.is_allowed(message):
            return {"toast": {"type": "error", "content": "This actor cannot decide the action."}}
        ingress = self._ingress.receive(
            message_id=message.message_id,
            chat_id=message.chat_id,
            actor_id=message.actor_id,
            text=message.text,
            created_at=message.created_at.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
        )
        if isinstance(parsed, FeishuDeliveryDecision):
            if self._delivery_decisions is None:
                raise FeishuPayloadError("Delivery decisions are not configured.")
            existing = self._delivery_decisions.repository.find_delivery_decision(
                idempotency_key=parsed.idempotency_key
            )
            if ingress.duplicate and existing is not None:
                return {"toast": {"type": "info", "content": "Decision already recorded."}}
            result = self._delivery_decisions.decide(
                parsed.delivery_id,
                decision=parsed.decision,
                actor=message.actor_id,
                idempotency_key=parsed.idempotency_key,
                revision_request=parsed.revision_request,
            )
            return {
                "toast": {
                    "type": "success",
                    "content": f"Delivery {parsed.decision.value} recorded.",
                },
                "delivery_id": result.delivery.delivery_id,
                "decision_id": result.decision.decision_id,
                "revision_run_id": result.decision.revision_run_id,
            }
        if ingress.duplicate:
            return {"toast": {"type": "info", "content": "Decision already recorded."}}
        reply = await self._agent.handle(message, inbox_record=ingress.record)
        if reply.text:
            await self._messenger.send_text(chat_id=message.chat_id, text=reply.text)
        toast_type = "success" if reply.observation is not None else "info"
        return {"toast": {"type": toast_type, "content": reply.text[:120]}}
