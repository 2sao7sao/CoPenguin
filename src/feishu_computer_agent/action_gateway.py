from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from super_agent_runtime import (
    ActionIntent,
    ActionReceipt,
    ActionStatus,
    ApprovalRequest,
    ApprovalState,
    ArtifactCAS,
    InboxRecord,
    InboxRouteState,
    InboxRouteType,
    InvalidTransition,
    ReceiptOutcome,
    ReconciliationRequired,
    SQLiteRuntimeRepository,
)

from .computer import ComputerProvider
from .models import ComputerObservation, ComputerTask, InboundMessage, RiskLevel


class ApprovalAuthorizationError(PermissionError):
    """The actor is not allowed to decide this exact action approval."""


@dataclass(frozen=True)
class ComputerActionResult:
    intent: ActionIntent
    approval: ApprovalRequest | None = None
    receipt: ActionReceipt | None = None
    observation: ComputerObservation | None = None


class ComputerActionGateway(Protocol):
    @property
    def provider_name(self) -> str: ...

    def pending_approval_count(self) -> int: ...

    async def submit(
        self,
        *,
        message: InboundMessage,
        inbox_record: InboxRecord,
        instruction: str,
        risk: RiskLevel,
        requires_approval: bool,
    ) -> ComputerActionResult: ...

    async def decide(
        self,
        *,
        message: InboundMessage,
        inbox_record: InboxRecord,
        approval_id: str,
        decision: ApprovalState,
    ) -> ComputerActionResult: ...


class DurableComputerActionGateway:
    """Binds the compatibility computer command to durable Runtime safety primitives."""

    _CAPABILITY = "computer.execute"
    _REQUEST_SCHEMA = "copenguin.computer_action_request.v1"
    _DECISION_SCHEMA = "copenguin.approval_decision_evidence.v1"
    _POLICY_SCHEMA = "copenguin.approval_policy.v1"

    def __init__(
        self,
        *,
        repository: SQLiteRuntimeRepository,
        artifacts: ArtifactCAS,
        provider: ComputerProvider,
        approval_ttl_seconds: int,
    ) -> None:
        if approval_ttl_seconds < 1:
            raise ValueError("approval_ttl_seconds must be at least 1")
        self._repository = repository
        self._artifacts = artifacts
        self._provider = provider
        self._approval_ttl_seconds = approval_ttl_seconds
        self._worker_id = f"compatibility-computer-gateway:{provider.name}"
        self._policy_snapshot = artifacts.put_json(
            {
                "schema": self._POLICY_SCHEMA,
                "policy_id": "computer-requester-only-v1",
                "capability": self._CAPABILITY,
                "approver_rule": "requester_only",
                "approval_ttl_seconds": approval_ttl_seconds,
            },
            kind="approval_policy_snapshot",
        )
        self.recover_missing_approvals()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def policy_snapshot_id(self) -> str:
        return self._policy_snapshot.artifact_id

    def pending_approval_count(self) -> int:
        self.recover_missing_approvals()
        self._repository.expire_pending_approvals()
        return len(
            self._repository.list_approvals(status=ApprovalState.PENDING, limit=10_000)
        )

    def recover_missing_approvals(self) -> list[ApprovalRequest]:
        recovered: list[ApprovalRequest] = []
        intents = self._repository.list_action_intents(
            status=ActionStatus.PENDING,
            limit=10_000,
        )
        for intent in intents:
            if not intent.requires_approval or intent.capability != self._CAPABILITY:
                continue
            if self._repository.find_approval_for_intent(intent.intent_id) is not None:
                continue
            task, _ = self._load_request(intent)
            recovered.append(
                self._repository.create_approval(
                    intent_id=intent.intent_id,
                    risk_level=task.risk.value,
                    requested_by="copenguin:computer-action-gateway",
                    reason=f"capability={self._CAPABILITY}; risk={task.risk.value}",
                    ttl_seconds=self._approval_ttl_seconds,
                    policy_snapshot_id=self._policy_snapshot.artifact_id,
                )
            )
        return recovered

    async def submit(
        self,
        *,
        message: InboundMessage,
        inbox_record: InboxRecord,
        instruction: str,
        risk: RiskLevel,
        requires_approval: bool,
    ) -> ComputerActionResult:
        self._validate_inbox_binding(
            message,
            inbox_record,
            expected_route=InboxRouteType.NEW_TASK,
        )
        if inbox_record.thread_id is None:
            raise InvalidTransition("computer action requires a durable TaskThread")
        run_id = self._resolve_run_id(inbox_record)
        task = ComputerTask(
            instruction=instruction,
            requester_id=self._actor_principal(message),
            chat_id=message.chat_id,
            message_id=message.message_id,
            risk=risk,
            created_at=message.created_at,
        )
        request = self._artifacts.put_json(
            {
                "schema": self._REQUEST_SCHEMA,
                "source_message_key": inbox_record.message_key,
                "task": task.model_dump(mode="json"),
            },
            kind="computer_action_request",
        )
        intent = self._repository.create_action_intent(
            thread_id=inbox_record.thread_id,
            run_id=run_id,
            capability=self._CAPABILITY,
            request_artifact_id=request.artifact_id,
            payload_hash=request.sha256,
            idempotency_key=f"computer:{inbox_record.message_key}",
            requires_approval=requires_approval,
            actor=self._actor_principal(message),
            correlation_id=inbox_record.message_key,
        )
        if requires_approval:
            approval = self._repository.create_approval(
                intent_id=intent.intent_id,
                risk_level=risk.value,
                requested_by="copenguin:computer-action-gateway",
                reason=f"capability={self._CAPABILITY}; risk={risk.value}",
                ttl_seconds=self._approval_ttl_seconds,
                policy_snapshot_id=self._policy_snapshot.artifact_id,
            )
            if approval.status == ApprovalState.APPROVED:
                return await self._execute_or_resume(intent, approval=approval)
            return self._current_result(intent, approval=approval)
        return await self._execute_or_resume(intent, approval=None)

    async def decide(
        self,
        *,
        message: InboundMessage,
        inbox_record: InboxRecord,
        approval_id: str,
        decision: ApprovalState,
    ) -> ComputerActionResult:
        if decision not in {ApprovalState.APPROVED, ApprovalState.DENIED}:
            raise ValueError("decision must be approved or denied")
        self._validate_inbox_binding(
            message,
            inbox_record,
            expected_route=InboxRouteType.CONTROL,
        )
        approval = self._repository.get_approval(approval_id)
        intent = self._repository.get_action_intent(approval.intent_id)
        task, _ = self._load_request(intent)
        self._authorize_decision(
            actor_principal=self._actor_principal(message),
            task=task,
            approval=approval,
            intent=intent,
        )

        if approval.status == ApprovalState.PENDING:
            evidence = self._artifacts.put_json(
                {
                    "schema": self._DECISION_SCHEMA,
                    "approval_id": approval_id,
                    "decision": decision.value,
                    "actor_principal": self._actor_principal(message),
                    "message_key": inbox_record.message_key,
                    "message_artifact_id": inbox_record.message_artifact_id,
                    "policy_snapshot_id": approval.policy_snapshot_id,
                    "created_at": message.created_at.isoformat(),
                },
                kind="approval_decision_evidence",
            )
            try:
                approval = self._repository.decide_approval(
                    approval_id,
                    decision=decision,
                    actor=self._actor_principal(message),
                    decision_evidence_artifact_id=evidence.artifact_id,
                )
            except InvalidTransition:
                approval = self._repository.get_approval(approval_id)
        elif approval.status != decision:
            return self._current_result(intent, approval=approval)

        intent = self._repository.get_action_intent(intent.intent_id)
        if approval.status != ApprovalState.APPROVED:
            return self._current_result(intent, approval=approval)
        return await self._execute_or_resume(intent, approval=approval)

    def _validate_inbox_binding(
        self,
        message: InboundMessage,
        record: InboxRecord,
        *,
        expected_route: InboxRouteType,
    ) -> None:
        expected_key = f"{message.platform.strip().lower()}:{message.message_id.strip()}"
        identity = (record.message_key, record.chat_id, record.actor_id)
        expected = (expected_key, message.chat_id, message.actor_id)
        if identity != expected:
            raise InvalidTransition("message does not match its durable Inbox record")
        if record.route_type != expected_route or record.route_state != InboxRouteState.CONFIRMED:
            raise InvalidTransition(
                f"message route is {record.route_type.value}; expected {expected_route.value}"
            )

    def _resolve_run_id(self, record: InboxRecord) -> str:
        run_ids = {
            event.run_id
            for event in self._repository.list_events(
                correlation_id=record.message_key,
                limit=10_000,
            )
            if event.thread_id == record.thread_id and event.run_id is not None
        }
        if len(run_ids) != 1:
            raise InvalidTransition(
                f"inbox task must resolve to exactly one Run; found {len(run_ids)}"
            )
        return next(iter(run_ids))

    def _actor_principal(self, message: InboundMessage) -> str:
        return f"{message.platform.strip().lower()}:{message.actor_id}"

    def _load_request(self, intent: ActionIntent) -> tuple[ComputerTask, str]:
        if intent.capability != self._CAPABILITY:
            raise InvalidTransition(
                f"approval belongs to unsupported capability: {intent.capability}"
            )
        payload = self._artifacts.get_json(intent.request_artifact_id)
        if not isinstance(payload, dict) or payload.get("schema") != self._REQUEST_SCHEMA:
            raise InvalidTransition("computer action request has an unsupported schema")
        source_message_key = str(payload.get("source_message_key") or "")
        if not source_message_key:
            raise InvalidTransition("computer action request is missing its source message")
        task = ComputerTask.model_validate(payload.get("task"))
        return task, source_message_key

    def _authorize_decision(
        self,
        *,
        actor_principal: str,
        task: ComputerTask,
        approval: ApprovalRequest,
        intent: ActionIntent,
    ) -> None:
        if approval.intent_id != intent.intent_id:
            raise InvalidTransition("approval is not bound to the requested action")
        if approval.policy_snapshot_id is None:
            raise ApprovalAuthorizationError("approval has no bound policy snapshot")
        policy = self._artifacts.get_json(approval.policy_snapshot_id)
        if (
            not isinstance(policy, dict)
            or policy.get("schema") != self._POLICY_SCHEMA
            or policy.get("policy_id") != "computer-requester-only-v1"
            or policy.get("capability") != intent.capability
            or policy.get("approver_rule") != "requester_only"
        ):
            raise ApprovalAuthorizationError("approval policy is not recognized by this gateway")
        if actor_principal != task.requester_id:
            raise ApprovalAuthorizationError(
                "only the actor who requested this computer action may decide it"
            )

    async def _execute_or_resume(
        self,
        intent: ActionIntent,
        *,
        approval: ApprovalRequest | None,
    ) -> ComputerActionResult:
        intent = self._repository.get_action_intent(intent.intent_id)
        if intent.status != ActionStatus.PENDING:
            return self._current_result(intent, approval=approval)
        try:
            claim = self._repository.claim_action(
                intent.intent_id,
                worker_id=self._worker_id,
            )
        except (InvalidTransition, ReconciliationRequired):
            intent = self._repository.get_action_intent(intent.intent_id)
            return self._current_result(intent, approval=approval)

        task, source_message_key = self._load_request(intent)
        receipt_id = uuid5(
            NAMESPACE_URL,
            f"{intent.intent_id}:receipt:{claim.fencing_token}",
        ).hex
        try:
            observation = await self._provider.run(task)
        except Exception as exc:  # provider outcome is uncertain after invocation
            receipt = self._repository.record_action_receipt(
                claim,
                receipt_id=receipt_id,
                outcome=ReceiptOutcome.UNKNOWN,
                provider=self._provider.name,
                evidence={
                    "approval_id": approval.approval_id if approval else None,
                    "exception_type": type(exc).__name__,
                    "policy_snapshot_id": approval.policy_snapshot_id if approval else None,
                    "source_message_key": source_message_key,
                },
            )
            return ComputerActionResult(
                intent=self._repository.get_action_intent(intent.intent_id),
                approval=approval,
                receipt=receipt,
            )

        response = self._artifacts.put_json(
            observation.model_dump(mode="json"),
            kind="computer_observation",
        )
        receipt = self._repository.record_action_receipt(
            claim,
            receipt_id=receipt_id,
            outcome=ReceiptOutcome.SUCCEEDED if observation.ok else ReceiptOutcome.FAILED,
            provider=self._provider.name,
            response_artifact_id=response.artifact_id,
            evidence={
                "approval_id": approval.approval_id if approval else None,
                "policy_snapshot_id": approval.policy_snapshot_id if approval else None,
                "risk": task.risk.value,
                "source_message_key": source_message_key,
            },
        )
        return ComputerActionResult(
            intent=self._repository.get_action_intent(intent.intent_id),
            approval=approval,
            receipt=receipt,
            observation=observation,
        )

    def _current_result(
        self,
        intent: ActionIntent,
        *,
        approval: ApprovalRequest | None,
    ) -> ComputerActionResult:
        intent = self._repository.get_action_intent(intent.intent_id)
        receipts = self._repository.list_action_receipts(intent_id=intent.intent_id, limit=1)
        receipt = receipts[0] if receipts else None
        observation: ComputerObservation | None = None
        if receipt and receipt.response_artifact_id:
            observation = ComputerObservation.model_validate(
                self._artifacts.get_json(receipt.response_artifact_id)
            )
        return ComputerActionResult(
            intent=intent,
            approval=approval,
            receipt=receipt,
            observation=observation,
        )
