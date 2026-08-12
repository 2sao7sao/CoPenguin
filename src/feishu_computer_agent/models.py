from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

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


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


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


class PendingApproval(BaseModel):
    approval_id: str = Field(default_factory=lambda: uuid4().hex[:10])
    task: ComputerTask
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    resolved_by: str | None = None
    resolved_at: datetime | None = None

    @classmethod
    def for_task(cls, task: ComputerTask, ttl_seconds: int) -> PendingApproval:
        now = datetime.now(UTC)
        return cls(task=task, created_at=now, expires_at=now + timedelta(seconds=ttl_seconds))

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at


class AgentReply(BaseModel):
    text: str
    intent: AgentIntent = AgentIntent.CHAT
    requires_approval: bool = False
    approval_id: str | None = None
    observation: ComputerObservation | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
