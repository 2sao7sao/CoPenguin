from __future__ import annotations

import ipaddress
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from super_agent_runtime import (
    ActionStatus,
    AgentSnapshot,
    ApprovalState,
    ArtifactCAS,
    ConcurrencyConflict,
    DeliveryState,
    IdempotencyConflict,
    InboxCoordinator,
    InboxRouteType,
    IngressAdapter,
    InvalidTransition,
    JobState,
    NotFound,
    OutboxState,
    SnapshotStore,
    SourceToArtifactExecutor,
    SourceToArtifactTaskCompiler,
    SQLiteRuntimeRepository,
    ThreadCoordinator,
    ThreadUpdateKind,
)

from .action_gateway import DurableComputerActionGateway
from .agent import PrivateAssistantAgent
from .computer import build_computer_provider
from .config import Settings, load_settings
from .feishu import (
    FeishuCardActionParser,
    FeishuEventParser,
    FeishuMessenger,
    FeishuPayloadError,
    FeishuWebhookService,
)
from .knowledge import build_knowledge_runtime
from .memory import build_memory_runtime
from .security import AccessController, RiskClassifier


class LocalIngressRequest(BaseModel):
    message_id: str
    text: str
    project_id: str | None = None
    chat_id: str = "local"
    actor_id: str = "owner"
    current_thread_id: str | None = None
    active_thread_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RouteDecisionRequest(BaseModel):
    decision: str
    actor_id: str = "owner"
    platform: str = "local"
    thread_id: str | None = None
    update_kind: ThreadUpdateKind | None = None
    reason: str = "user_route_decision"


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _is_loopback(request: Request) -> bool:
    if request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    memory = build_memory_runtime(settings.memory_enabled, settings.memory_dir)
    knowledge = build_knowledge_runtime(settings.knowledge_enabled, settings.kb_root)
    computer = build_computer_provider(settings)
    runtime = SQLiteRuntimeRepository(settings.runtime_database)
    artifacts = ArtifactCAS(settings.artifact_dir)
    computer_actions = DurableComputerActionGateway(
        repository=runtime,
        artifacts=artifacts,
        provider=computer,
        approval_ttl_seconds=settings.approval_ttl_seconds,
    )
    snapshots = SnapshotStore(artifacts)
    threads = ThreadCoordinator(runtime, artifacts)
    default_agent_snapshot = snapshots.put_agent(
        AgentSnapshot(
            agent_id="copenguin-default",
            model_profile={"computer_provider": computer.name},
            tool_registry={"computer": computer.name},
            capability_manifest={
                "computer": "approval_gated",
                "workflows": [SourceToArtifactExecutor.key],
            },
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    inbox = InboxCoordinator(
        repository=runtime,
        artifacts=artifacts,
        snapshots=snapshots,
        agent_snapshot_id=default_agent_snapshot.artifact_id,
    )
    source_tasks = SourceToArtifactTaskCompiler(
        repository=runtime,
        artifacts=artifacts,
        agent_snapshot_id=default_agent_snapshot.artifact_id,
    )
    feishu_ingress = IngressAdapter(
        platform="feishu",
        coordinator=inbox,
        default_project_id=settings.default_project_id,
    )
    local_ingress = IngressAdapter(
        platform="local",
        coordinator=inbox,
        default_project_id=settings.default_project_id,
    )
    agent = PrivateAssistantAgent(
        memory=memory,
        knowledge=knowledge,
        computer_actions=computer_actions,
        risk_classifier=RiskClassifier(),
        approval_required=settings.approval_required,
        inbox=inbox,
    )
    service = FeishuWebhookService(
        parser=FeishuEventParser(settings),
        access=AccessController(settings),
        agent=agent,
        ingress=feishu_ingress,
        messenger=FeishuMessenger(settings),
        card_parser=FeishuCardActionParser(settings),
    )

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.settings = settings
    app.state.agent = agent
    app.state.computer_actions = computer_actions
    app.state.runtime = runtime
    app.state.artifacts = artifacts
    app.state.snapshots = snapshots
    app.state.threads = threads
    app.state.inbox = inbox
    app.state.source_tasks = source_tasks
    app.state.feishu_ingress = feishu_ingress
    app.state.local_ingress = local_ingress
    app.state.default_agent_snapshot = default_agent_snapshot
    app.state.feishu_service = service

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

    @app.get("/runtime/jobs")
    async def runtime_jobs(
        state: JobState | None = None,
        executor_key: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        safe_limit = max(1, min(limit, 500))
        jobs = runtime.list_jobs(
            state=state,
            executor_key=executor_key,
            limit=safe_limit,
        )
        return {"jobs": [job.as_dict() for job in jobs]}

    @app.get("/runtime/jobs/{run_id}")
    async def runtime_job(run_id: str) -> dict[str, object]:
        try:
            job = runtime.get_job(run_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"job": job.as_dict()}

    @app.get("/runtime/runs/{run_id}/steps")
    async def runtime_steps(run_id: str, limit: int = 100) -> dict[str, object]:
        safe_limit = max(1, min(limit, 500))
        steps = runtime.list_steps(run_id=run_id, limit=safe_limit)
        return {"steps": [step.as_dict() for step in steps]}

    @app.get("/runtime/deliveries")
    async def runtime_deliveries(
        thread_id: str | None = None,
        state: DeliveryState | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        safe_limit = max(1, min(limit, 500))
        deliveries = runtime.list_deliveries(
            thread_id=thread_id,
            state=state,
            limit=safe_limit,
        )
        return {"deliveries": [delivery.as_dict() for delivery in deliveries]}

    @app.get("/runtime/deliveries/{delivery_id}")
    async def runtime_delivery(delivery_id: str) -> dict[str, object]:
        try:
            delivery = runtime.get_delivery(delivery_id)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"delivery": delivery.as_dict()}

    @app.post("/runtime/deliveries/{delivery_id}/present")
    async def runtime_present_delivery(
        delivery_id: str,
        request: Request,
    ) -> dict[str, object]:
        if not _is_loopback(request):
            raise HTTPException(status_code=403, detail="Delivery presentation requires loopback")
        try:
            delivery = runtime.present_delivery(delivery_id, actor="local-control-plane")
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidTransition as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"delivery": delivery.as_dict()}

    @app.get("/runtime/outbox")
    async def runtime_outbox(
        state: OutboxState | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        safe_limit = max(1, min(limit, 500))
        items = runtime.list_outbox(state=state, limit=safe_limit)
        return {"items": [item.as_dict() for item in items]}

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
        receipts = runtime.list_action_receipts(intent_id=intent_id)
        return {
            "action": action.as_dict(),
            "receipts": [receipt.as_dict() for receipt in receipts],
        }

    @app.get("/runtime/inbox")
    async def runtime_inbox(
        route_type: InboxRouteType | None = None,
        thread_id: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        safe_limit = max(1, min(limit, 500))
        records = runtime.list_inbox_records(
            route_type=route_type,
            thread_id=thread_id,
            project_id=project_id,
            limit=safe_limit,
        )
        return {"messages": [record.as_dict() for record in records]}

    @app.post("/runtime/inbox")
    async def runtime_accept_inbox(
        payload: LocalIngressRequest,
        request: Request,
    ) -> dict[str, object]:
        if not _is_loopback(request):
            raise HTTPException(status_code=403, detail="local ingress requires a loopback client")
        try:
            result = local_ingress.receive(
                message_id=payload.message_id,
                chat_id=payload.chat_id,
                actor_id=payload.actor_id,
                text=payload.text,
                created_at=_timestamp(payload.created_at),
                project_id=payload.project_id,
                current_thread_id=payload.current_thread_id,
                active_thread_ids=payload.active_thread_ids,
            )
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ConcurrencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidTransition as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "accepted_new": result.accepted_new,
            "duplicate": result.duplicate,
            "message": result.record.as_dict(),
        }

    @app.post("/runtime/inbox/{message_key:path}/decision")
    async def runtime_decide_inbox_route(
        message_key: str,
        payload: RouteDecisionRequest,
        request: Request,
    ) -> dict[str, object]:
        if not _is_loopback(request):
            raise HTTPException(status_code=403, detail="route decisions require a loopback client")
        try:
            record = inbox.resolve_route(
                message_key=message_key,
                platform=payload.platform,
                actor_id=payload.actor_id,
                decision=payload.decision,
                target_thread_id=payload.thread_id,
                update_kind=payload.update_kind,
                reason=payload.reason,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ConcurrencyConflict, IdempotencyConflict) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (InvalidTransition, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"message": record.as_dict()}

    @app.get("/runtime/approvals")
    async def runtime_approvals(
        status: ApprovalState | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        runtime.expire_pending_approvals()
        safe_limit = max(1, min(limit, 500))
        approvals = runtime.list_approvals(status=status, limit=safe_limit)
        return {"approvals": [approval.as_dict() for approval in approvals]}

    @app.get("/runtime/approvals/{approval_id}")
    async def runtime_approval(approval_id: str) -> dict[str, object]:
        runtime.expire_pending_approvals()
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
            event = payload.get("event")
            if isinstance(event, dict) and "action" in event and "operator" in event:
                return await service.handle_card_action(payload)
            return await service.handle_payload(payload)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ConcurrencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidTransition as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FeishuPayloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()
