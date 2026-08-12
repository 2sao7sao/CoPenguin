from __future__ import annotations

import re

from super_agent_runtime import (
    ActionStatus,
    ApprovalState,
    ConcurrencyConflict,
    IdempotencyConflict,
    InboxCoordinator,
    InboxRecord,
    InboxRouteState,
    InboxRouteType,
    InvalidTransition,
    NotFound,
    ThreadUpdateKind,
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
        if stripped.lower().startswith("/route"):
            parts = stripped.split()
            decision = parts[2].lower() if len(parts) >= 3 else None
            return ParsedCommand(
                intent=AgentIntent.ROUTE,
                route_message_key=parts[1] if len(parts) >= 2 else None,
                route_decision=decision,
                route_thread_id=(parts[3] if decision == "thread" and len(parts) >= 4 else None),
                route_update_kind=(parts[4] if decision == "thread" and len(parts) >= 5 else None),
            )
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
        inbox: InboxCoordinator | None = None,
    ) -> None:
        self._memory = memory
        self._knowledge = knowledge
        self._computer_actions = computer_actions
        self._risk_classifier = risk_classifier
        self._approval_required = approval_required
        self._inbox = inbox
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

        if command.intent == AgentIntent.ROUTE:
            return self._handle_route_command(command, message, inbox_record)

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

        if inbox_record is not None and inbox_record.route_state == InboxRouteState.PROPOSED:
            return self._route_confirmation_reply(inbox_record)

        if inbox_record is not None and inbox_record.route_type == InboxRouteType.THREAD_UPDATE:
            return self._thread_update_reply(inbox_record)

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

    def _handle_route_command(
        self,
        command: ParsedCommand,
        message: InboundMessage,
        inbox_record: InboxRecord | None,
    ) -> AgentReply:
        if inbox_record is None or self._inbox is None:
            return AgentReply(
                intent=AgentIntent.ROUTE,
                text="Route decision was not durably routed; refusing to apply it.",
            )
        if command.route_message_key is None or command.route_decision is None:
            return AgentReply(
                intent=AgentIntent.ROUTE,
                text=(
                    "用法：/route <message-key> thread <thread-id> [supplement|goal|method|cancel]，"
                    "或 /route <message-key> new|dismiss"
                ),
            )
        decision_map = {"new": "new_task", "dismiss": "expire", "thread": "thread"}
        decision = decision_map.get(command.route_decision)
        if decision is None:
            return AgentReply(
                intent=AgentIntent.ROUTE,
                text="Route decision must be thread, new, or dismiss.",
            )
        kind_map = {
            "supplement": ThreadUpdateKind.SUPPLEMENT,
            "goal": ThreadUpdateKind.GOAL_CHANGE,
            "method": ThreadUpdateKind.METHOD_CHANGE,
            "cancel": ThreadUpdateKind.CANCEL,
        }
        update_kind = None
        if command.route_update_kind is not None:
            update_kind = kind_map.get(command.route_update_kind.lower())
            if update_kind is None:
                return AgentReply(
                    intent=AgentIntent.ROUTE,
                    text="Thread update kind must be supplement, goal, method, or cancel.",
                )
        message_key = command.route_message_key
        if ":" not in message_key:
            message_key = f"{message.platform}:{message_key}"
        try:
            resolved = self._inbox.resolve_route(
                message_key=message_key,
                platform=message.platform,
                actor_id=message.actor_id,
                decision=decision,
                target_thread_id=command.route_thread_id,
                update_kind=update_kind,
                reason="user_route_command",
            )
        except PermissionError:
            return AgentReply(
                intent=AgentIntent.ROUTE,
                text="This actor is not authorized to resolve that route.",
            )
        except (ConcurrencyConflict, IdempotencyConflict, InvalidTransition) as exc:
            return AgentReply(intent=AgentIntent.ROUTE, text=f"Route decision refused: {exc}")
        except (NotFound, ValueError) as exc:
            return AgentReply(intent=AgentIntent.ROUTE, text=f"Route decision invalid: {exc}")

        if resolved.route_state == InboxRouteState.EXPIRED:
            text = f"已忽略 {resolved.message_key}；它没有创建或更新任务。"
        elif resolved.route_type == InboxRouteType.NEW_TASK:
            text = f"已将 {resolved.message_key} 改为新任务：{resolved.thread_id}。"
        else:
            text = (
                f"已将 {resolved.message_key} 归入任务 {resolved.thread_id}，"
                f"语义为 {resolved.update_kind.value if resolved.update_kind else 'supplement'}。"
            )
        return AgentReply(
            intent=AgentIntent.ROUTE,
            text=text,
            metadata={"route": resolved.as_dict()},
        )

    def _route_confirmation_reply(self, record: InboxRecord) -> AgentReply:
        candidates = "、".join(record.candidate_thread_ids) or "无明确候选"
        commands = [
            f"新建任务：/route {record.message_key} new",
            f"忽略：/route {record.message_key} dismiss",
        ]
        commands.extend(
            f"归入 {thread_id}：/route {record.message_key} thread {thread_id}"
            for thread_id in record.candidate_thread_ids
        )
        return AgentReply(
            intent=AgentIntent.ROUTE,
            text=(
                "这条消息可能是在继续某个任务，但目标不明确，因此尚未启动或修改任何任务。\n"
                f"候选任务：{candidates}\n" + "\n".join(commands)
            ),
            metadata={"route": record.as_dict()},
        )

    def _thread_update_reply(self, record: InboxRecord) -> AgentReply:
        assert record.thread_id is not None
        kind = record.update_kind or ThreadUpdateKind.SUPPLEMENT
        if kind == ThreadUpdateKind.CANCEL:
            text = f"任务 {record.thread_id} 已取消；排队或运行中的 Run 已留下取消记录。"
        else:
            text = (
                f"更新已归入任务 {record.thread_id}（{kind.value}）；"
                "旧 Run 与快照保留，新 Run 使用冻结后的新上下文。"
            )
        return AgentReply(
            intent=AgentIntent.ROUTE,
            text=text,
            metadata={"route": record.as_dict()},
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
                    f"Approval {approval.approval_id} is {approval.status.value}; cannot deny it."
                ),
            )

        if approval.status != ApprovalState.APPROVED:
            return AgentReply(
                intent=AgentIntent.APPROVE,
                text=(
                    f"Approval {approval.approval_id} is {approval.status.value}; cannot execute."
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
        if result.approval is not None and result.approval.status == ApprovalState.PENDING:
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
