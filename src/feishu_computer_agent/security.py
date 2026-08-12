from __future__ import annotations

import re
from datetime import UTC, datetime
from threading import Lock

from .config import Settings
from .models import ApprovalStatus, ComputerTask, InboundMessage, PendingApproval, RiskLevel


class AccessController:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def is_allowed(self, message: InboundMessage) -> bool:
        if self._settings.trust_all_feishu_users_for_dev:
            return True
        if message.sender_open_id in self._settings.feishu_allowed_open_ids:
            return True
        return bool(
            message.sender_union_id
            and message.sender_union_id in self._settings.feishu_allowed_union_ids
        )


class RiskClassifier:
    _critical_patterns = [
        r"\brm\s+-rf\b",
        r"\bmkfs\b",
        r"\bdiskutil\s+erase\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bdd\s+if=",
        r"删除.*全部",
        r"清空.*磁盘",
        r"转账",
        r"付款",
    ]
    _high_patterns = [
        r"\bsudo\b",
        r"\bcurl\b.*\|\s*(bash|sh)",
        r"\bchmod\s+777\b",
        r"\bkillall\b",
        r"\bgit\s+push\b",
        r"\b发送\b.*\b邮件\b",
        r"\b发布\b",
    ]

    def classify(self, instruction: str) -> RiskLevel:
        normalized = instruction.strip().lower()
        if any(
            re.search(pattern, normalized, re.IGNORECASE) for pattern in self._critical_patterns
        ):
            return RiskLevel.CRITICAL
        if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in self._high_patterns):
            return RiskLevel.HIGH
        if normalized.startswith("shell:"):
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM


class ApprovalStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._items: dict[str, PendingApproval] = {}
        self._lock = Lock()

    def create(self, task: ComputerTask) -> PendingApproval:
        pending = PendingApproval.for_task(task, ttl_seconds=self._ttl_seconds)
        with self._lock:
            self._items[pending.approval_id] = pending
        return pending

    def get(self, approval_id: str) -> PendingApproval | None:
        with self._lock:
            item = self._items.get(approval_id)
            if item and item.is_expired():
                item.status = ApprovalStatus.EXPIRED
            return item

    def approve(self, approval_id: str, actor_id: str) -> PendingApproval | None:
        return self._resolve(approval_id, actor_id, ApprovalStatus.APPROVED)

    def deny(self, approval_id: str, actor_id: str) -> PendingApproval | None:
        return self._resolve(approval_id, actor_id, ApprovalStatus.DENIED)

    def _resolve(
        self,
        approval_id: str,
        actor_id: str,
        status: ApprovalStatus,
    ) -> PendingApproval | None:
        with self._lock:
            item = self._items.get(approval_id)
            if item is None:
                return None
            if item.is_expired():
                item.status = ApprovalStatus.EXPIRED
                return item
            if item.status != ApprovalStatus.PENDING:
                return item
            item.status = status
            item.resolved_by = actor_id
            item.resolved_at = datetime.now(UTC)
            return item

    def consume_approved(self, approval_id: str) -> ComputerTask | None:
        with self._lock:
            item = self._items.get(approval_id)
            if item is None:
                return None
            if item.is_expired():
                item.status = ApprovalStatus.EXPIRED
                return None
            if item.status != ApprovalStatus.APPROVED:
                return None
            self._items.pop(approval_id, None)
            return item.task

    def pending(self) -> list[PendingApproval]:
        now = datetime.now(UTC)
        with self._lock:
            for item in self._items.values():
                if item.status == ApprovalStatus.PENDING and item.is_expired(now):
                    item.status = ApprovalStatus.EXPIRED
            return list(self._items.values())
