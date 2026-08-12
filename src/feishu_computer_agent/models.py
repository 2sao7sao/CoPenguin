from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ChatType(StrEnum):
    DIRECT = "direct"
    GROUP = "group"


class AgentIntent(StrEnum):
    CHAT = "chat"
    COMPUTER = "computer"
    REMEMBER = "remember"
    KNOWLEDGE = "knowledge"
    APPROVE = "approve"
    DENY = "deny"
    STATUS = "status"
    ROUTE = "route"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class InboundMessage(BaseModel):
    platform: str = "feishu"
    message_id: str
    chat_id: str
    chat_type: ChatType
    sender_open_id: str
    sender_union_id: str | None = None
    text: str
    raw: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def actor_id(self) -> str:
        return self.sender_union_id or self.sender_open_id


class ParsedCommand(BaseModel):
    intent: AgentIntent
    argument: str = ""
    approval_id: str | None = None
    route_message_key: str | None = None
    route_decision: str | None = None
    route_thread_id: str | None = None
    route_update_kind: str | None = None


class ComputerTask(BaseModel):
    instruction: str
    requester_id: str
    chat_id: str
    message_id: str
    risk: RiskLevel
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ComputerObservation(BaseModel):
    ok: bool
    summary: str
    provider: str
    details: dict[str, Any] = Field(default_factory=dict)


class AgentReply(BaseModel):
    text: str
    intent: AgentIntent = AgentIntent.CHAT
    requires_approval: bool = False
    approval_id: str | None = None
    observation: ComputerObservation | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
