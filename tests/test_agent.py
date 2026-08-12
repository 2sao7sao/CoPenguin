from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from feishu_computer_agent.action_gateway import DurableComputerActionGateway
from feishu_computer_agent.agent import PrivateAssistantAgent
from feishu_computer_agent.computer import DryRunComputerProvider
from feishu_computer_agent.knowledge import NoopKnowledgeRuntime
from feishu_computer_agent.memory import NoopMemoryRuntime
from feishu_computer_agent.models import (
    ChatType,
    ComputerObservation,
    ComputerTask,
    InboundMessage,
)
from feishu_computer_agent.security import RiskClassifier
from super_agent_runtime import (
    ActionStatus,
    AgentSnapshot,
    ApprovalState,
    ArtifactCAS,
    InboxCoordinator,
    InboxMessage,
    ReceiptOutcome,
    RoutingContext,
    SnapshotStore,
    SQLiteRuntimeRepository,
)

NOW = datetime(2026, 8, 13, 8, tzinfo=UTC)


@dataclass
class AgentHarness:
    agent: PrivateAssistantAgent
    inbox: InboxCoordinator
    repository: SQLiteRuntimeRepository
    artifacts: ArtifactCAS
    gateway: DurableComputerActionGateway


class BlockingComputerProvider:
    name = "blocking"

    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, task: ComputerTask) -> ComputerObservation:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return ComputerObservation(
            ok=True,
            provider=self.name,
            summary=f"completed: {task.instruction}",
        )


class RaisingComputerProvider:
    name = "raising"

    async def run(self, task: ComputerTask) -> ComputerObservation:
        raise RuntimeError("provider connection dropped")


def _harness(
    tmp_path,
    *,
    provider=None,
    approval_required: bool = True,
) -> AgentHarness:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    artifacts = ArtifactCAS(tmp_path / "artifacts")
    snapshots = SnapshotStore(artifacts)
    agent_snapshot = snapshots.put_agent(
        AgentSnapshot(
            agent_id="copenguin-test",
            model_profile={"computer_provider": (provider or DryRunComputerProvider()).name},
            tool_registry={},
            capability_manifest={"computer": "approval_gated"},
            created_at=NOW.isoformat(),
        )
    )
    inbox = InboxCoordinator(
        repository=repository,
        artifacts=artifacts,
        snapshots=snapshots,
        agent_snapshot_id=agent_snapshot.artifact_id,
    )
    gateway = DurableComputerActionGateway(
        repository=repository,
        artifacts=artifacts,
        provider=provider or DryRunComputerProvider(),
        approval_ttl_seconds=1800,
    )
    agent = PrivateAssistantAgent(
        memory=NoopMemoryRuntime(),
        knowledge=NoopKnowledgeRuntime(),
        computer_actions=gateway,
        risk_classifier=RiskClassifier(),
        approval_required=approval_required,
        inbox=inbox,
    )
    return AgentHarness(agent, inbox, repository, artifacts, gateway)


def _message(
    text: str,
    *,
    message_id: str,
    actor_id: str = "ou_owner",
) -> InboundMessage:
    return InboundMessage(
        platform="feishu",
        message_id=message_id,
        chat_id="c1",
        chat_type=ChatType.DIRECT,
        sender_open_id=actor_id,
        text=text,
        created_at=NOW,
    )


def _route(harness: AgentHarness, message: InboundMessage):
    return harness.inbox.receive(
        InboxMessage(
            platform=message.platform,
            message_id=message.message_id,
            chat_id=message.chat_id,
            actor_id=message.actor_id,
            text=message.text,
            created_at=message.created_at.isoformat(),
        ),
        RoutingContext(project_id="personal"),
    )


async def _handle(harness: AgentHarness, message: InboundMessage):
    return await harness.agent.handle(message, inbox_record=_route(harness, message))


def test_computer_approval_and_execution_survive_restart(tmp_path) -> None:
    async def scenario() -> None:
        first = _harness(tmp_path)
        queued = await _handle(
            first,
            _message(
                "/computer open calendar and find tomorrow meetings",
                message_id="computer-1",
            ),
        )

        assert queued.requires_approval
        assert queued.approval_id
        assert "/approve" in queued.text
        approval = first.repository.get_approval(queued.approval_id)
        intent = first.repository.get_action_intent(approval.intent_id)
        assert approval.status == ApprovalState.PENDING
        assert approval.policy_snapshot_id is not None
        assert intent.status == ActionStatus.PENDING
        assert first.artifacts.exists(intent.request_artifact_id, verify=True)

        restarted = _harness(tmp_path)
        approved = await _handle(
            restarted,
            _message(f"/approve {queued.approval_id}", message_id="approval-1"),
        )

        assert approved.observation is not None
        assert approved.observation.ok
        assert "Dry-run" in approved.text
        durable_approval = restarted.repository.get_approval(queued.approval_id)
        durable_intent = restarted.repository.get_action_intent(intent.intent_id)
        receipts = restarted.repository.list_action_receipts(intent_id=intent.intent_id)
        assert durable_approval.status == ApprovalState.APPROVED
        assert durable_approval.resolved_by == "feishu:ou_owner"
        assert durable_approval.decision_evidence_artifact_id is not None
        assert restarted.artifacts.exists(
            durable_approval.decision_evidence_artifact_id,
            verify=True,
        )
        assert durable_intent.status == ActionStatus.SUCCEEDED
        assert len(receipts) == 1
        assert receipts[0].outcome == ReceiptOutcome.SUCCEEDED
        assert receipts[0].response_artifact_id is not None

    asyncio.run(scenario())


def test_status_reports_durable_pending_approvals_after_restart(tmp_path) -> None:
    async def scenario() -> None:
        first = _harness(tmp_path)
        await _handle(first, _message("/computer open browser", message_id="computer-1"))

        restarted = _harness(tmp_path)
        status = await _handle(restarted, _message("/status", message_id="status-1"))

        assert "pending_approvals=1" in status.text

    asyncio.run(scenario())


def test_restart_recovers_action_intent_created_before_approval(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        first = _harness(tmp_path)
        message = _message("/computer open browser", message_id="computer-1")
        record = _route(first, message)

        def fail_approval_creation(**kwargs):
            raise RuntimeError("injected crash before approval commit")

        monkeypatch.setattr(first.repository, "create_approval", fail_approval_creation)
        with pytest.raises(RuntimeError, match="injected crash"):
            await first.gateway.submit(
                message=message,
                inbox_record=record,
                instruction="open browser",
                risk=RiskClassifier().classify("open browser"),
                requires_approval=True,
            )

        intents = first.repository.list_action_intents()
        assert len(intents) == 1
        assert first.repository.find_approval_for_intent(intents[0].intent_id) is None

        restarted = _harness(tmp_path)
        recovered = restarted.repository.find_approval_for_intent(intents[0].intent_id)
        assert recovered is not None
        assert recovered.status == ApprovalState.PENDING
        assert restarted.gateway.pending_approval_count() == 1

    asyncio.run(scenario())


def test_non_requester_cannot_decide_computer_approval(tmp_path) -> None:
    async def scenario() -> None:
        harness = _harness(tmp_path)
        queued = await _handle(
            harness,
            _message("/computer open browser", message_id="computer-1"),
        )
        assert queued.approval_id

        refused = await _handle(
            harness,
            _message(
                f"/approve {queued.approval_id}",
                message_id="approval-1",
                actor_id="ou_other",
            ),
        )

        approval = harness.repository.get_approval(queued.approval_id)
        intent = harness.repository.get_action_intent(approval.intent_id)
        assert "not authorized" in refused.text
        assert approval.status == ApprovalState.PENDING
        assert intent.status == ActionStatus.PENDING
        assert harness.repository.list_action_receipts(intent_id=intent.intent_id) == []

    asyncio.run(scenario())


def test_denial_is_durable_and_never_calls_provider(tmp_path) -> None:
    async def scenario() -> None:
        provider = BlockingComputerProvider()
        first = _harness(tmp_path, provider=provider)
        queued = await _handle(
            first,
            _message("/computer open browser", message_id="computer-1"),
        )
        assert queued.approval_id

        restarted = _harness(tmp_path, provider=provider)
        denied = await _handle(
            restarted,
            _message(f"/deny {queued.approval_id}", message_id="denial-1"),
        )

        approval = restarted.repository.get_approval(queued.approval_id)
        intent = restarted.repository.get_action_intent(approval.intent_id)
        assert "denied" in denied.text
        assert approval.status == ApprovalState.DENIED
        assert intent.status == ActionStatus.CANCELLED
        assert provider.calls == 0
        assert restarted.repository.list_action_receipts(intent_id=intent.intent_id) == []

    asyncio.run(scenario())


def test_repeated_approval_after_success_reads_receipt_without_reexecution(tmp_path) -> None:
    async def scenario() -> None:
        first = _harness(tmp_path)
        queued = await _handle(
            first,
            _message("/computer open browser", message_id="computer-1"),
        )
        assert queued.approval_id
        completed = await _handle(
            first,
            _message(f"/approve {queued.approval_id}", message_id="approval-1"),
        )
        assert completed.observation is not None

        restarted = _harness(tmp_path, provider=RaisingComputerProvider())
        repeated = await _handle(
            restarted,
            _message(f"/approve {queued.approval_id}", message_id="approval-2"),
        )

        approval = restarted.repository.get_approval(queued.approval_id)
        receipts = restarted.repository.list_action_receipts(intent_id=approval.intent_id)
        assert repeated.observation is not None
        assert repeated.observation.summary == completed.observation.summary
        assert len(receipts) == 1
        assert receipts[0].outcome == ReceiptOutcome.SUCCEEDED

    asyncio.run(scenario())


def test_concurrent_approval_messages_execute_provider_once(tmp_path) -> None:
    async def scenario() -> None:
        provider = BlockingComputerProvider()
        harness = _harness(tmp_path, provider=provider)
        queued = await _handle(
            harness,
            _message("/computer open browser", message_id="computer-1"),
        )
        assert queued.approval_id
        first_message = _message(f"/approve {queued.approval_id}", message_id="approval-1")
        second_message = _message(f"/approve {queued.approval_id}", message_id="approval-2")
        first_record = _route(harness, first_message)
        second_record = _route(harness, second_message)

        first_task = asyncio.create_task(
            harness.agent.handle(first_message, inbox_record=first_record)
        )
        await provider.started.wait()
        second_reply = await harness.agent.handle(second_message, inbox_record=second_record)
        provider.release.set()
        first_reply = await first_task

        approval = harness.repository.get_approval(queued.approval_id)
        receipts = harness.repository.list_action_receipts(intent_id=approval.intent_id)
        assert provider.calls == 1
        assert "already executing" in second_reply.text
        assert first_reply.observation is not None
        assert len(receipts) == 1

    asyncio.run(scenario())


def test_known_no_approval_path_still_creates_intent_and_receipt(tmp_path) -> None:
    async def scenario() -> None:
        harness = _harness(tmp_path, approval_required=False)

        reply = await _handle(
            harness,
            _message("/computer open browser", message_id="computer-1"),
        )

        intents = harness.repository.list_action_intents()
        assert reply.observation is not None
        assert not reply.requires_approval
        assert len(intents) == 1
        assert intents[0].status == ActionStatus.SUCCEEDED
        assert len(harness.repository.list_action_receipts(intent_id=intents[0].intent_id)) == 1
        assert harness.repository.list_approvals() == []

    asyncio.run(scenario())


def test_high_risk_action_still_requires_approval_when_default_gate_is_disabled(tmp_path) -> None:
    async def scenario() -> None:
        harness = _harness(tmp_path, approval_required=False)

        reply = await _handle(
            harness,
            _message("/computer shell: pwd", message_id="computer-1"),
        )

        assert reply.requires_approval
        assert reply.approval_id is not None
        assert harness.repository.get_approval(reply.approval_id).risk_level == "high"

    asyncio.run(scenario())


def test_provider_exception_requires_reconciliation_instead_of_retry(tmp_path) -> None:
    async def scenario() -> None:
        harness = _harness(tmp_path, provider=RaisingComputerProvider())
        queued = await _handle(
            harness,
            _message("/computer open browser", message_id="computer-1"),
        )
        assert queued.approval_id

        reply = await _handle(
            harness,
            _message(f"/approve {queued.approval_id}", message_id="approval-1"),
        )

        approval = harness.repository.get_approval(queued.approval_id)
        intent = harness.repository.get_action_intent(approval.intent_id)
        receipts = harness.repository.list_action_receipts(intent_id=intent.intent_id)
        assert "reconciliation is required" in reply.text
        assert intent.status == ActionStatus.RECONCILE_REQUIRED
        assert len(receipts) == 1
        assert receipts[0].outcome == ReceiptOutcome.UNKNOWN
        assert receipts[0].evidence["exception_type"] == "RuntimeError"
        assert "provider connection dropped" not in str(receipts[0].evidence)

    asyncio.run(scenario())


def test_computer_and_approval_commands_fail_closed_without_durable_ingress(tmp_path) -> None:
    async def scenario() -> None:
        harness = _harness(tmp_path)

        computer = await harness.agent.handle(
            _message("/computer open browser", message_id="computer-1")
        )
        approval = await harness.agent.handle(_message("/approve missing", message_id="approval-1"))

        assert "not durably routed" in computer.text
        assert "not durably routed" in approval.text
        assert harness.repository.list_action_intents() == []

    asyncio.run(scenario())
