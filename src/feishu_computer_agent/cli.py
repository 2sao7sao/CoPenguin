from __future__ import annotations

import argparse
import asyncio

import uvicorn

from .agent import PrivateAssistantAgent
from .computer import build_computer_provider
from .config import load_settings
from .knowledge import build_knowledge_runtime
from .memory import build_memory_runtime
from .models import ChatType, InboundMessage
from .security import ApprovalStore, RiskClassifier


def main() -> None:
    parser = argparse.ArgumentParser(prog="copenguin")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the Feishu webhook server.")
    local = subparsers.add_parser("local", help="Send one local test message through the agent.")
    local.add_argument("message")
    args = parser.parse_args()

    if args.command == "local":
        asyncio.run(_run_local(args.message))
        return

    settings = load_settings()
    uvicorn.run(
        "feishu_computer_agent.server:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


async def _run_local(text: str) -> None:
    settings = load_settings()
    agent = PrivateAssistantAgent(
        memory=build_memory_runtime(settings.memory_enabled, settings.memory_dir),
        knowledge=build_knowledge_runtime(settings.knowledge_enabled, settings.kb_root),
        computer=build_computer_provider(settings),
        approvals=ApprovalStore(ttl_seconds=settings.approval_ttl_seconds),
        risk_classifier=RiskClassifier(),
        approval_required=settings.approval_required,
    )
    reply = await agent.handle(
        InboundMessage(
            message_id="local",
            chat_id="local",
            chat_type=ChatType.DIRECT,
            sender_open_id="local-user",
            text=text,
        )
    )
    print(reply.text)


if __name__ == "__main__":
    main()
