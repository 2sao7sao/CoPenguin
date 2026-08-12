from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request

from super_agent_runtime import (
    ActionStatus,
    AgentSnapshot,
    ApprovalState,
    ArtifactCAS,
    InboxCoordinator,
    InboxRouteType,
    NotFound,
    SnapshotStore,
    SQLiteRuntimeRepository,
    ThreadCoordinator,
)

from .agent import PrivateAssistantAgent
from .computer import build_computer_provider
from .config import Settings, load_settings
from .feishu import FeishuEventParser, FeishuMessenger, FeishuPayloadError, FeishuWebhookService
from .knowledge import build_knowledge_runtime
from .memory import build_memory_runtime
from .security import AccessController, ApprovalStore, RiskClassifier


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    memory = build_memory_runtime(settings.memory_enabled, settings.memory_dir)
    knowledge = build_knowledge_runtime(settings.knowledge_enabled, settings.kb_root)
    computer = build_computer_provider(settings)
    approvals = ApprovalStore(ttl_seconds=settings.approval_ttl_seconds)
    runtime = SQLiteRuntimeRepository(settings.runtime_database)
    artifacts = ArtifactCAS(settings.artifact_dir)
    snapshots = SnapshotStore(artifacts)
    threads = ThreadCoordinator(runtime, artifacts)
    default_agent_snapshot = snapshots.put_agent(
        AgentSnapshot(
            agent_id="copenguin-default",
            model_profile={"computer_provider": computer.name},
            tool_registry={"computer": computer.name},
            capability_manifest={"computer": "approval_gated"},
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    inbox = InboxCoordinator(
        repository=runtime,
        artifacts=artifacts,
        snapshots=snapshots,
        threads=threads,
        agent_snapshot_id=default_agent_snapshot.artifact_id,
    )
    agent = PrivateAssistantAgent(
        memory=memory,
        knowledge=knowledge,
        computer=computer,
        approvals=approvals,
        risk_classifier=RiskClassifier(),
        approval_required=settings.approval_required,
    )
    service = FeishuWebhookService(
        parser=FeishuEventParser(settings),
        access=AccessController(settings),
        agent=agent,
        messenger=FeishuMessenger(settings),
    )

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.settings = settings
    app.state.agent = agent
    app.state.approvals = approvals
    app.state.runtime = runtime
    app.state.artifacts = artifacts
    app.state.snapshots = snapshots
    app.state.threads = threads
    app.state.inbox = inbox
    app.state.default_agent_snapshot = default_agent_snapshot

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/runtime/threads")
    async def runtime_threads(
        project_id: str | None = None,
        attention_only: bool = False,
        limit: int = 100,
    ) -> dict[str, object]:
        safe_limit = max(1, min(limit, 500))
        threads = runtime.list_threads(
            project_id=project_id,
            attention_only=attention_only,
            limit=safe_limit,
        )
        return {"threads": [thread.as_dict() for thread in threads]}

    @app.get("/runtime/threads/{thread_id}")
    async def runtime_thread(thread_id: str) -> dict[str, object]:
        try:
            thread = runtime.get_thread(thread_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "thread": thread.as_dict(),
            "projection_hash": thread.projection_hash,
            "replay_verified": runtime.verify_thread_replay(thread_id),
        }

    @app.get("/runtime/actions")
    async def runtime_actions(
        status: ActionStatus | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        safe_limit = max(1, min(limit, 500))
        actions = runtime.list_action_intents(
            status=status,
            run_id=run_id,
            limit=safe_limit,
        )
        return {"actions": [action.as_dict() for action in actions]}

    @app.get("/runtime/actions/{intent_id}")
    async def runtime_action(intent_id: str) -> dict[str, object]:
        try:
            action = runtime.get_action_intent(intent_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"action": action.as_dict()}

    @app.get("/runtime/inbox")
    async def runtime_inbox(
        route_type: InboxRouteType | None = None,
        thread_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        safe_limit = max(1, min(limit, 500))
        records = runtime.list_inbox_records(
            route_type=route_type,
            thread_id=thread_id,
            limit=safe_limit,
        )
        return {"messages": [record.as_dict() for record in records]}

    @app.get("/runtime/approvals")
    async def runtime_approvals(
        status: ApprovalState | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        safe_limit = max(1, min(limit, 500))
        approvals = runtime.list_approvals(status=status, limit=safe_limit)
        return {"approvals": [approval.as_dict() for approval in approvals]}

    @app.get("/runtime/approvals/{approval_id}")
    async def runtime_approval(approval_id: str) -> dict[str, object]:
        try:
            approval = runtime.get_approval(approval_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"approval": approval.as_dict()}

    @app.post("/feishu/events")
    async def feishu_events(request: Request) -> dict[str, object]:
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise FeishuPayloadError("Expected a JSON object.")
            return await service.handle_payload(payload)
        except FeishuPayloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()
