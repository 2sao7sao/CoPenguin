from __future__ import annotations

import re

from .computer import ComputerProvider
from .knowledge import KnowledgeRuntime
from .memory import MemoryRuntime
from .models import (
    AgentIntent,
    AgentReply,
    ApprovalStatus,
    ComputerTask,
    InboundMessage,
    ParsedCommand,
    RiskLevel,
)
from .security import ApprovalStore, RiskClassifier


class CommandParser:
    _approval_re = re.compile(r"^/(approve|deny)\s+([A-Za-z0-9_-]+)\s*$", re.IGNORECASE)

    def parse(self, text: str) -> ParsedCommand:
        stripped = text.strip()
        match = self._approval_re.match(stripped)
        if match:
            intent = (
                AgentIntent.APPROVE if match.group(1).lower() == "approve" else AgentIntent.DENY
            )
            return ParsedCommand(intent=intent, approval_id=match.group(2))
        if stripped.startswith("/status"):
            return ParsedCommand(intent=AgentIntent.STATUS)
        if stripped.startswith("/remember "):
            return ParsedCommand(
                intent=AgentIntent.REMEMBER, argument=stripped[len("/remember ") :].strip()
            )
        if stripped.startswith("/kb "):
            return ParsedCommand(
                intent=AgentIntent.KNOWLEDGE, argument=stripped[len("/kb ") :].strip()
            )
        if stripped.startswith("/computer "):
            return ParsedCommand(
                intent=AgentIntent.COMPUTER, argument=stripped[len("/computer ") :].strip()
            )
        if stripped.startswith("/do "):
            return ParsedCommand(
                intent=AgentIntent.COMPUTER, argument=stripped[len("/do ") :].strip()
            )
        return ParsedCommand(intent=AgentIntent.CHAT, argument=stripped)


class PrivateAssistantAgent:
    def __init__(
        self,
        *,
        memory: MemoryRuntime,
        knowledge: KnowledgeRuntime,
        computer: ComputerProvider,
        approvals: ApprovalStore,
        risk_classifier: RiskClassifier,
        approval_required: bool,
    ) -> None:
        self._memory = memory
        self._knowledge = knowledge
        self._computer = computer
        self._approvals = approvals
        self._risk_classifier = risk_classifier
        self._approval_required = approval_required
        self._parser = CommandParser()

    async def handle(self, message: InboundMessage) -> AgentReply:
        command = self._parser.parse(message.text)
        session_id = f"{message.platform}:{message.actor_id}"

        if command.intent == AgentIntent.STATUS:
            pending = [
                item for item in self._approvals.pending() if item.status == ApprovalStatus.PENDING
            ]
            return AgentReply(
                intent=AgentIntent.STATUS,
                text=(
                    f"Agent online. computer_provider={self._computer.name}; "
                    f"pending_approvals={len(pending)}"
                ),
            )

        if command.intent in {AgentIntent.APPROVE, AgentIntent.DENY}:
            return await self._handle_approval_command(command, message)

        if command.intent == AgentIntent.REMEMBER:
            result = self._memory.ingest_turn(
                session_id=session_id,
                text=command.argument,
                source="feishu_explicit_remember",
            )
            accepted = result.get("accepted_memories") or []
            return AgentReply(
                intent=AgentIntent.REMEMBER,
                text=f"Memory update received. accepted={len(accepted)}",
                metadata={"memory": result},
            )

        if command.intent == AgentIntent.KNOWLEDGE:
            result = self._knowledge.answer(
                intent="answer_with_evidence", question=command.argument
            )
            rendered = result.get("rendered") or "Knowledge runtime is not available yet."
            return AgentReply(intent=AgentIntent.KNOWLEDGE, text=rendered[:4000], metadata=result)

        if command.intent == AgentIntent.COMPUTER:
            return await self._handle_computer_task(message, command.argument)

        self._memory.ingest_turn(session_id=session_id, text=message.text, source="feishu_turn")
        context = self._memory.prompt_context(session_id=session_id, query=message.text)
        memory_hint = ""
        if context.get("enabled", True):
            sections = context.get("prompt_sections") or context.get("sections") or []
            memory_hint = f"\n\nMemory context sections: {len(sections)}"
        return AgentReply(
            intent=AgentIntent.CHAT,
            text=(
                "我已收到。当前 MVP 已接好消息、记忆和电脑任务入口；"
                "需要执行电脑任务请发 `/computer <任务>`，查看状态发 `/status`。"
                f"{memory_hint}"
            ),
            metadata={"memory_context": context},
        )

    async def _handle_approval_command(
        self,
        command: ParsedCommand,
        message: InboundMessage,
    ) -> AgentReply:
        assert command.approval_id is not None
        if command.intent == AgentIntent.DENY:
            item = self._approvals.deny(command.approval_id, message.actor_id)
            if item is None:
                return AgentReply(intent=AgentIntent.DENY, text="Approval request not found.")
            return AgentReply(intent=AgentIntent.DENY, text=f"Approval {item.approval_id} denied.")

        item = self._approvals.approve(command.approval_id, message.actor_id)
        if item is None:
            return AgentReply(intent=AgentIntent.APPROVE, text="Approval request not found.")
        if item.status != ApprovalStatus.APPROVED:
            return AgentReply(
                intent=AgentIntent.APPROVE,
                text=f"Approval {item.approval_id} is {item.status.value}; cannot execute.",
            )
        task = self._approvals.consume_approved(item.approval_id)
        if task is None:
            return AgentReply(
                intent=AgentIntent.APPROVE,
                text=f"Approval {item.approval_id} could not be consumed.",
            )
        observation = await self._computer.run(task)
        return AgentReply(
            intent=AgentIntent.APPROVE,
            text=observation.summary,
            observation=observation,
        )

    async def _handle_computer_task(self, message: InboundMessage, instruction: str) -> AgentReply:
        risk = self._risk_classifier.classify(instruction)
        task = ComputerTask(
            instruction=instruction,
            requester_id=message.actor_id,
            chat_id=message.chat_id,
            message_id=message.message_id,
            risk=risk,
        )
        if self._approval_required or risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            pending = self._approvals.create(task)
            return AgentReply(
                intent=AgentIntent.COMPUTER,
                requires_approval=True,
                approval_id=pending.approval_id,
                text=(
                    f"Computer task queued for approval.\n"
                    f"id: {pending.approval_id}\n"
                    f"risk: {risk.value}\n"
                    f"approve: /approve {pending.approval_id}\n"
                    f"deny: /deny {pending.approval_id}"
                ),
            )
        observation = await self._computer.run(task)
        return AgentReply(
            intent=AgentIntent.COMPUTER,
            text=observation.summary,
            observation=observation,
        )
