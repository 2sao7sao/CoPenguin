from __future__ import annotations

import argparse
import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import uvicorn

from super_agent_runtime import (
    AgentSnapshot,
    ArtifactCAS,
    InboxCoordinator,
    IngressAdapter,
    SnapshotStore,
    SourceSnapshotBinding,
    SourceToArtifactExecutor,
    SourceToArtifactTaskCompiler,
    SQLiteRuntimeRepository,
    WorkerHost,
    WorkerHostConfig,
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
    worker = subparsers.add_parser("worker", help="Run the bounded Source-to-Artifact worker.")
    worker_mode = worker.add_mutually_exclusive_group()
    worker_mode.add_argument("--once", action="store_true", help="Run one bounded batch.")
    worker_mode.add_argument("--max-jobs", type=int, help="Run up to this many jobs, then exit.")
    worker.add_argument("--concurrency", type=int, help="Maximum parallel Runs in this process.")
    worker.add_argument("--poll-seconds", type=float, default=0.5)
    source_task = subparsers.add_parser(
        "source-task",
        help="Queue one explicitly selected, pre-captured JSON source fixture.",
    )
    source_task.add_argument("source_file", type=Path)
    source_task.add_argument("--project", default=None)
    source_task.add_argument(
        "--objective",
        default="Create an inspectable Project Decision Record from the selected source",
    )
    source_task.add_argument("--source-ref")
    source_task.add_argument("--source-snapshot-id")
    source_task.add_argument("--revision")
    source_task.add_argument("--access-envelope", default="local-user-selected")
    artifact = subparsers.add_parser("artifact", help="Print one local Artifact by id.")
    artifact.add_argument("artifact_id")
    args = parser.parse_args()

    if args.command == "local":
        asyncio.run(_run_local(args.message, message_id=args.message_id, project_id=args.project))
        return
    if args.command == "worker":
        _run_worker(
            once=args.once,
            max_jobs=args.max_jobs,
            concurrency=args.concurrency,
            poll_seconds=args.poll_seconds,
        )
        return
    if args.command == "source-task":
        _queue_source_task(
            args.source_file,
            project_id=args.project,
            objective=args.objective,
            source_ref=args.source_ref,
            source_snapshot_id=args.source_snapshot_id,
            revision=args.revision,
            access_envelope_id=args.access_envelope,
        )
        return
    if args.command == "artifact":
        _print_artifact(args.artifact_id)
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
            capability_manifest={
                "computer": "approval_gated",
                "workflows": [SourceToArtifactExecutor.key],
            },
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


def _source_agent_snapshot(artifacts: ArtifactCAS) -> str:
    snapshots = SnapshotStore(artifacts)
    snapshot = snapshots.put_agent(
        AgentSnapshot(
            agent_id="copenguin-source-to-artifact-v1",
            model_profile={"provider": "deterministic-fixture"},
            tool_registry={},
            capability_manifest={"workflows": [SourceToArtifactExecutor.key]},
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    return snapshot.artifact_id


def _run_worker(
    *,
    once: bool,
    max_jobs: int | None,
    concurrency: int | None,
    poll_seconds: float,
) -> None:
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    repository = SQLiteRuntimeRepository(settings.runtime_database)
    artifacts = ArtifactCAS(settings.artifact_dir)
    host = WorkerHost(
        repository=repository,
        artifacts=artifacts,
        executors=(SourceToArtifactExecutor(artifacts),),
        config=WorkerHostConfig(
            worker_id=f"local-worker-{uuid4().hex[:12]}",
            concurrency=(concurrency if concurrency is not None else settings.worker_concurrency),
            lease_seconds=settings.worker_lease_seconds,
            heartbeat_interval_seconds=settings.worker_heartbeat_interval_seconds,
            retry_delay_seconds=settings.worker_retry_delay_seconds,
        ),
    )

    def emit(results) -> None:
        for result in results:
            print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))

    if once:
        emit(host.run_batch())
        return
    if max_jobs is not None:
        emit(host.run_until_idle(max_jobs=max_jobs))
        return
    stop = threading.Event()
    try:
        host.serve(stop, poll_interval_seconds=poll_seconds)
    except KeyboardInterrupt:
        stop.set()


def _queue_source_task(
    source_file: Path,
    *,
    project_id: str | None,
    objective: str,
    source_ref: str | None,
    source_snapshot_id: str | None,
    revision: str | None,
    access_envelope_id: str,
) -> None:
    if not source_file.is_file():
        raise SystemExit(f"Source file not found: {source_file}")
    content = source_file.read_bytes()
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("Source fixture must be a UTF-8 JSON document") from exc
    if not isinstance(value, dict):
        raise SystemExit("Source fixture must contain one JSON object")

    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    repository = SQLiteRuntimeRepository(settings.runtime_database)
    artifacts = ArtifactCAS(settings.artifact_dir)
    source = artifacts.put_bytes(
        content,
        kind="source_snapshot_content",
        media_type="application/json",
    )
    compiler = SourceToArtifactTaskCompiler(
        repository=repository,
        artifacts=artifacts,
        agent_snapshot_id=_source_agent_snapshot(artifacts),
    )
    submitted = compiler.submit(
        project_id=project_id or settings.default_project_id,
        objective=objective,
        sources=(
            SourceSnapshotBinding(
                source_snapshot_id=source_snapshot_id or f"source-{source.sha256[:16]}",
                source_ref_id=source_ref or f"local-file:{source_file.resolve()}",
                revision_id=revision or source.sha256,
                access_envelope_id=access_envelope_id,
                content_artifact_id=source.artifact_id,
            ),
        ),
    )
    print(
        json.dumps(
            {
                "thread_id": submitted.task.thread_id,
                "run_id": submitted.task.run_id,
                "source_snapshot_ids": submitted.source_snapshot_ids,
                "status": "queued",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _print_artifact(artifact_id: str) -> None:
    artifacts = ArtifactCAS(load_settings().artifact_dir)
    content = artifacts.get_bytes(artifact_id)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(content.decode("utf-8", errors="replace"))
        return
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
