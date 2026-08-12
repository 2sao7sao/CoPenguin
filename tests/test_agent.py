import asyncio

from feishu_computer_agent.agent import PrivateAssistantAgent
from feishu_computer_agent.computer import DryRunComputerProvider
from feishu_computer_agent.knowledge import NoopKnowledgeRuntime
from feishu_computer_agent.memory import NoopMemoryRuntime
from feishu_computer_agent.models import ChatType, InboundMessage
from feishu_computer_agent.security import ApprovalStore, RiskClassifier


def _message(text: str) -> InboundMessage:
    return InboundMessage(
        message_id="m1",
        chat_id="c1",
        chat_type=ChatType.DIRECT,
        sender_open_id="ou_owner",
        text=text,
    )


def test_computer_task_requires_approval_then_executes_dry_run() -> None:
    asyncio.run(_test_computer_task_requires_approval_then_executes_dry_run())


async def _test_computer_task_requires_approval_then_executes_dry_run() -> None:
    agent = PrivateAssistantAgent(
        memory=NoopMemoryRuntime(),
        knowledge=NoopKnowledgeRuntime(),
        computer=DryRunComputerProvider(),
        approvals=ApprovalStore(ttl_seconds=1800),
        risk_classifier=RiskClassifier(),
        approval_required=True,
    )

    queued = await agent.handle(_message("/computer open calendar and find tomorrow meetings"))

    assert queued.requires_approval
    assert queued.approval_id
    assert "/approve" in queued.text

    approved = await agent.handle(_message(f"/approve {queued.approval_id}"))

    assert approved.observation is not None
    assert approved.observation.ok
    assert "Dry-run" in approved.text


def test_status_reports_pending_approvals() -> None:
    asyncio.run(_test_status_reports_pending_approvals())


async def _test_status_reports_pending_approvals() -> None:
    agent = PrivateAssistantAgent(
        memory=NoopMemoryRuntime(),
        knowledge=NoopKnowledgeRuntime(),
        computer=DryRunComputerProvider(),
        approvals=ApprovalStore(ttl_seconds=1800),
        risk_classifier=RiskClassifier(),
        approval_required=True,
    )

    await agent.handle(_message("/computer open browser"))
    status = await agent.handle(_message("/status"))

    assert "pending_approvals=1" in status.text
