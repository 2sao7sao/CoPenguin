from __future__ import annotations

import re

from super_agent_runtime import (
    ActionStatus,
    ApprovalState,
    InboxRecord,
    InvalidTransition,
    NotFound,
)

from .action_gateway import (
    ApprovalAuthorizationError,
    ComputerActionGateway,
    ComputerActionResult,
)
from .knowledge import KnowledgeRuntime
from .memory import MemoryRuntime
from .models import (
    AgentIntent,
    AgentReply,
    InboundMessage,
    ParsedCommand,
    RiskLevel,
)
from .security import RiskClassifier


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
        computer_actions: ComputerActionGateway,
        risk_classifier: RiskClassifier,
        approval_required: bool,
    ) -> None:
        self._memory = memory
        self._knowledge = knowledge
        self._computer_actions = computer_actions
        self._risk_classifier = risk_classifier
        self._approval_required = approval_required
        self._parser = CommandParser()

    async def handle(
        self,
        message: InboundMessage,
        *,
        inbox_record: InboxRecord | None = None,
    ) -> AgentReply:
        command = self._parser.parse(message.text)
        session_id = f"{message.platform}:{message.actor_id}"

        if command.intent == AgentIntent.STATUS:
            return AgentReply(
                intent=AgentIntent.STATUS,
                text=(
                    f"Agent online. computer_provider={self._computer_actions.provider_name}; "
                    f"pending_approvals={self._computer_actions.pending_approval_count()}"
                ),
            )

        if command.intent in {AgentIntent.APPROVE, AgentIntent.DENY}:
            return await self._handle_approval_command(command, message, inbox_record)

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
            return await self._handle_computer_task(message, command.argument, inbox_record)

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
        inbox_record: InboxRecord | None,
    ) -> AgentReply:
        assert command.approval_id is not None
        if inbox_record is None:
            return AgentReply(
                intent=command.intent,
                text="Approval decision was not durably routed; refusing to apply it.",
            )
        decision = (
            ApprovalState.APPROVED
            if command.intent == AgentIntent.APPROVE
            else ApprovalState.DENIED
        )
        try:
            result = await self._computer_actions.decide(
                message=message,
                inbox_record=inbox_record,
                approval_id=command.approval_id,
                decision=decision,
            )
        except NotFound:
            return AgentReply(intent=command.intent, text="Approval request not found.")
        except ApprovalAuthorizationError:
            return AgentReply(
                intent=command.intent,
                text="This actor is not authorized to decide that approval.",
            )
        except InvalidTransition as exc:
            return AgentReply(intent=command.intent, text=f"Approval decision refused: {exc}")

        approval = result.approval
        assert approval is not None
        if command.intent == AgentIntent.DENY:
            if approval.status == ApprovalState.DENIED:
                return AgentReply(
                    intent=AgentIntent.DENY,
                    text=f"Approval {approval.approval_id} denied.",
                )
            return AgentReply(
                intent=AgentIntent.DENY,
                text=(
                    f"Approval {approval.approval_id} is {approval.status.value}; "
                    "cannot deny it."
                ),
            )

        if approval.status != ApprovalState.APPROVED:
            return AgentReply(
                intent=AgentIntent.APPROVE,
                text=(
                    f"Approval {approval.approval_id} is {approval.status.value}; "
                    "cannot execute."
                ),
            )
        return self._reply_for_action_result(
            intent=AgentIntent.APPROVE,
            approval_id=approval.approval_id,
            result=result,
        )

    async def _handle_computer_task(
        self,
        message: InboundMessage,
        instruction: str,
        inbox_record: InboxRecord | None,
    ) -> AgentReply:
        if inbox_record is None:
            return AgentReply(
                intent=AgentIntent.COMPUTER,
                text="Computer task was not durably routed; refusing to execute it.",
            )
        risk = self._risk_classifier.classify(instruction)
        requires_approval = self._approval_required or risk in {
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        }
        try:
            result = await self._computer_actions.submit(
                message=message,
                inbox_record=inbox_record,
                instruction=instruction,
                risk=risk,
                requires_approval=requires_approval,
            )
        except InvalidTransition as exc:
            return AgentReply(
                intent=AgentIntent.COMPUTER,
                text=f"Computer task refused: {exc}",
            )
        if (
            result.approval is not None
            and result.approval.status == ApprovalState.PENDING
        ):
            pending = result.approval
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
        return self._reply_for_action_result(
            intent=AgentIntent.COMPUTER,
            approval_id=result.approval.approval_id if result.approval else None,
            result=result,
        )

    def _reply_for_action_result(
        self,
        *,
        intent: AgentIntent,
        approval_id: str | None,
        result: ComputerActionResult,
    ) -> AgentReply:
        if result.observation is not None:
            return AgentReply(
                intent=intent,
                text=result.observation.summary,
                observation=result.observation,
                approval_id=approval_id,
                metadata={
                    "intent_id": result.intent.intent_id,
                    "receipt_id": result.receipt.receipt_id if result.receipt else None,
                },
            )
        if result.intent.status == ActionStatus.RECONCILE_REQUIRED:
            text = (
                f"Action {result.intent.intent_id} has an unknown provider outcome; "
                "reconciliation is required before retrying."
            )
        elif result.intent.status in {ActionStatus.EXECUTING, ActionStatus.RECOVERING}:
            text = f"Action {result.intent.intent_id} is already executing."
        else:
            text = f"Action {result.intent.intent_id} is {result.intent.status.value}."
        return AgentReply(
            intent=intent,
            text=text,
            approval_id=approval_id,
            metadata={
                "intent_id": result.intent.intent_id,
                "receipt_id": result.receipt.receipt_id if result.receipt else None,
            },
        )
