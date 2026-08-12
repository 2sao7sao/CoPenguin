from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import uvicorn

from super_agent_runtime import (
    AgentSnapshot,
    ArtifactCAS,
    InboxCoordinator,
    IngressAdapter,
    SnapshotStore,
    SQLiteRuntimeRepository,
)

from .action_gateway import DurableComputerActionGateway
from .agent import PrivateAssistantAgent
from .computer import build_computer_provider
from .config import load_settings
from .knowledge import build_knowledge_runtime
from .memory import build_memory_runtime
from .models import ChatType, InboundMessage
from .security import RiskClassifier


def main() -> None:
    parser = argparse.ArgumentParser(prog="copenguin")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the Feishu webhook server.")
    local = subparsers.add_parser("local", help="Send one local test message through the agent.")
    local.add_argument("message")
    local.add_argument("--message-id", help="Stable channel id used to retry the same message.")
    local.add_argument("--project", help="Project id for the durable Inbox route.")
    args = parser.parse_args()

    if args.command == "local":
        asyncio.run(_run_local(args.message, message_id=args.message_id, project_id=args.project))
        return

    settings = load_settings()
    uvicorn.run(
        "feishu_computer_agent.server:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


async def _run_local(
    text: str,
    *,
    message_id: str | None = None,
    project_id: str | None = None,
) -> None:
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    computer = build_computer_provider(settings)
    memory = build_memory_runtime(settings.memory_enabled, settings.memory_dir)
    knowledge = build_knowledge_runtime(settings.knowledge_enabled, settings.kb_root)
    runtime = SQLiteRuntimeRepository(settings.runtime_database)
    artifacts = ArtifactCAS(settings.artifact_dir)
    computer_actions = DurableComputerActionGateway(
        repository=runtime,
        artifacts=artifacts,
        provider=computer,
        approval_ttl_seconds=settings.approval_ttl_seconds,
    )
    snapshots = SnapshotStore(artifacts)
    created_at = datetime.now(UTC)
    agent_snapshot = snapshots.put_agent(
        AgentSnapshot(
            agent_id="copenguin-default",
            model_profile={"computer_provider": computer.name},
            tool_registry={"computer": computer.name},
            capability_manifest={"computer": "approval_gated"},
            created_at=created_at.isoformat(),
        )
    )
    inbox = InboxCoordinator(
        repository=runtime,
        artifacts=artifacts,
        snapshots=snapshots,
        agent_snapshot_id=agent_snapshot.artifact_id,
    )
    ingress = IngressAdapter(
        platform="local",
        coordinator=inbox,
        default_project_id=settings.default_project_id,
    )
    resolved_message_id = message_id or uuid4().hex
    accepted = ingress.receive(
        message_id=resolved_message_id,
        chat_id="local",
        actor_id="local-user",
        text=text,
        created_at=created_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        project_id=project_id,
    )
    if accepted.duplicate:
        print(
            f"Message already accepted: {accepted.record.message_key} "
            f"route={accepted.record.route_type.value}"
        )
        return

    agent = PrivateAssistantAgent(
        memory=memory,
        knowledge=knowledge,
        computer_actions=computer_actions,
        risk_classifier=RiskClassifier(),
        approval_required=settings.approval_required,
        inbox=inbox,
    )
    reply = await agent.handle(
        InboundMessage(
            platform="local",
            message_id=resolved_message_id,
            chat_id="local",
            chat_type=ChatType.DIRECT,
            sender_open_id="local-user",
            text=text,
            created_at=created_at,
        ),
        inbox_record=accepted.record,
    )
    print(reply.text)


if __name__ == "__main__":
    main()
