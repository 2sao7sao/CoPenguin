from __future__ import annotations

import re

from .config import Settings
from .models import InboundMessage, RiskLevel


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
