from __future__ import annotations

import re
from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5

from .artifacts import ArtifactCAS
from .errors import IdempotencyConflict, NotFound
from .models import (
    InboxRecord,
    InboxRouteState,
    InboxRouteType,
    RunState,
    TaskSubmission,
    ThreadUpdateKind,
    ThreadUpdateSubmission,
    content_hash,
    to_timestamp,
    utc_now,
)
from .repository import SQLiteRuntimeRepository
from .snapshots import ContextItem, ContextManifest, SnapshotStore, TaskSnapshot


@dataclass(frozen=True)
class InboxMessage:
    platform: str
    message_id: str
    chat_id: str
    actor_id: str
    text: str
    created_at: str

    def __post_init__(self) -> None:
        normalized = {
            "platform": self.platform.strip().lower(),
            "message_id": self.message_id.strip(),
            "chat_id": self.chat_id.strip(),
            "actor_id": self.actor_id.strip(),
            "created_at": self.created_at.strip(),
        }
        if any(not value for value in normalized.values()):
            raise ValueError("platform, message_id, chat_id, actor_id, and created_at are required")
        if not self.text.strip():
            raise ValueError("inbound message text is required")
        for name, value in normalized.items():
            object.__setattr__(self, name, value)

    @property
    def message_key(self) -> str:
        return f"{self.platform}:{self.message_id}"

    @property
    def payload_hash(self) -> str:
        return content_hash(
            {
                "platform": self.platform,
                "message_id": self.message_id,
                "chat_id": self.chat_id,
                "actor_id": self.actor_id,
                "text": self.text,
            }
        )


@dataclass(frozen=True)
class RoutingContext:
    project_id: str
    current_thread_id: str | None = None
    active_thread_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        project_id = self.project_id.strip()
        if not project_id:
            raise ValueError("project_id is required")
        current_thread_id = self.current_thread_id.strip() if self.current_thread_id else None
        active_thread_ids = tuple(
            thread_id.strip() for thread_id in self.active_thread_ids if thread_id.strip()
        )
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "current_thread_id", current_thread_id)
        object.__setattr__(self, "active_thread_ids", active_thread_ids)


@dataclass(frozen=True)
class InboxRoute:
    route_type: InboxRouteType
    confidence: float
    rationale: str
    domain: str
    requires_confirmation: bool = False
    target_thread_id: str | None = None
    task_text: str = ""
    update_kind: ThreadUpdateKind | None = None
    candidate_thread_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class IngressResult:
    record: InboxRecord
    accepted_new: bool

    @property
    def duplicate(self) -> bool:
        return not self.accepted_new


class ConservativeInboxRouter:
    """Deterministic first-pass router that avoids treating every chat as a task."""

    _control = re.compile(
        r"^/(status|approve|deny|pause|resume|cancel|archive|route)(?:\s|$)", re.IGNORECASE
    )
    _thread = re.compile(r"^/thread\s+([A-Za-z0-9_-]+)(?:\s+(.*))?$", re.IGNORECASE)
    _new_task = re.compile(r"^/(task|do|computer)\s+(.+)$", re.IGNORECASE | re.DOTALL)
    _continuation_cues = (
        "继续",
        "接着",
        "下一阶段",
        "换个方式",
        "不如",
        "改成",
        "刚才",
        "这个任务",
        "这个方案",
        "continue",
        "instead",
        "change it",
        "取消任务",
        "取消这个任务",
        "停止任务",
        "停止这个任务",
        "cancel task",
        "stop task",
    )
    _task_verbs = (
        "创建",
        "整理",
        "开发",
        "修改",
        "检查",
        "安排",
        "提醒",
        "发送",
        "生成",
        "执行",
        "写一份",
        "做一个",
        "帮我做",
        "build",
        "create",
        "implement",
        "update",
        "schedule",
        "send",
        "write a",
    )

    def route(self, message: InboxMessage, context: RoutingContext) -> InboxRoute:
        text = message.text.strip()
        if self._control.match(text):
            return InboxRoute(
                InboxRouteType.CONTROL,
                1.0,
                "explicit control command",
                self._domain(text),
            )

        thread_match = self._thread.match(text)
        if thread_match:
            task_text = (thread_match.group(2) or "").strip()
            return InboxRoute(
                InboxRouteType.THREAD_UPDATE,
                1.0,
                "explicit thread target",
                self._domain(text),
                target_thread_id=thread_match.group(1),
                task_text=task_text,
                update_kind=self._update_kind(task_text),
            )

        task_match = self._new_task.match(text)
        if task_match:
            return InboxRoute(
                InboxRouteType.NEW_TASK,
                1.0,
                "explicit task command",
                self._domain(text),
                task_text=task_match.group(2).strip(),
            )

        lowered = text.lower()
        has_continuation = any(cue in lowered for cue in self._continuation_cues)
        if has_continuation and context.current_thread_id:
            return InboxRoute(
                InboxRouteType.THREAD_UPDATE,
                0.92,
                "continuation or correction cue with current thread context",
                self._domain(text),
                target_thread_id=context.current_thread_id,
                task_text=text,
                update_kind=self._update_kind(text),
            )
        if has_continuation and len(context.active_thread_ids) == 1:
            return InboxRoute(
                InboxRouteType.THREAD_UPDATE,
                0.82,
                "continuation cue with one active candidate thread",
                self._domain(text),
                target_thread_id=context.active_thread_ids[0],
                task_text=text,
                update_kind=self._update_kind(text),
            )
        if has_continuation:
            return InboxRoute(
                InboxRouteType.AMBIGUOUS,
                0.45,
                "continuation cue has no unambiguous target thread",
                self._domain(text),
                requires_confirmation=True,
                task_text=text,
                update_kind=self._update_kind(text),
                candidate_thread_ids=context.active_thread_ids,
            )

        if any(cue in lowered for cue in self._task_verbs):
            return InboxRoute(
                InboxRouteType.NEW_TASK,
                0.76,
                "task verb indicates a deliverable or external action",
                self._domain(text),
                task_text=text,
            )

        return InboxRoute(
            InboxRouteType.CHAT,
            0.72,
            "no durable-task or thread-update signal",
            self._domain(text),
            task_text=text,
        )

    def _domain(self, text: str) -> str:
        lowered = text.lower()
        if any(cue in lowered for cue in ("健康", "医生", "睡眠", "运动", "health")):
            return "health"
        if any(cue in lowered for cue in ("付款", "预算", "账单", "投资", "finance")):
            return "finance"
        if any(cue in lowered for cue in ("家人", "伴侣", "朋友", "relationship")):
            return "relationship"
        if any(cue in lowered for cue in ("项目", "代码", "文档", "会议", "工作", "repo")):
            return "work"
        return "life"

    def _update_kind(self, text: str) -> ThreadUpdateKind:
        lowered = text.strip().lower()
        if re.search(
            r"(^|\s)(/cancel|cancel task|stop task)(\s|$)|取消(?:这个)?任务|停止(?:这个)?任务|终止任务",
            lowered,
        ):
            return ThreadUpdateKind.CANCEL
        if any(
            cue in lowered
            for cue in (
                "换个方式",
                "换一种方式",
                "不如",
                "方案 b",
                "方案b",
                "instead",
                "different approach",
                "change method",
            )
        ):
            return ThreadUpdateKind.METHOD_CHANGE
        if any(
            cue in lowered
            for cue in (
                "改目标",
                "调整目标",
                "验收标准",
                "目标改成",
                "change goal",
                "acceptance criteria",
            )
        ):
            return ThreadUpdateKind.GOAL_CHANGE
        return ThreadUpdateKind.SUPPLEMENT


class InboxCoordinator:
    """Persists every route and turns explicit task messages into durable Threads."""

    def __init__(
        self,
        *,
        repository: SQLiteRuntimeRepository,
        artifacts: ArtifactCAS,
        snapshots: SnapshotStore,
        agent_snapshot_id: str,
        router: ConservativeInboxRouter | None = None,
    ) -> None:
        if not agent_snapshot_id.startswith("artifact:sha256:"):
            raise ValueError("agent_snapshot_id must be an Artifact CAS reference")
        self.repository = repository
        self.artifacts = artifacts
        self.snapshots = snapshots
        self.agent_snapshot_id = agent_snapshot_id
        self.router = router or ConservativeInboxRouter()

    def receive(self, message: InboxMessage, context: RoutingContext) -> InboxRecord:
        return self.accept(message, context).record

    def accept(self, message: InboxMessage, context: RoutingContext) -> IngressResult:
        text_artifact = self.artifacts.put_text(message.text, kind="inbox_message")
        existing = self.repository.find_inbox_record(message.message_key)
        if existing is not None:
            stable_identity = (
                existing.platform,
                existing.message_id,
                existing.chat_id,
                existing.actor_id,
                existing.text_artifact_id,
            )
            candidate_identity = (
                message.platform,
                message.message_id,
                message.chat_id,
                message.actor_id,
                text_artifact.artifact_id,
            )
            if (
                existing.payload_hash and existing.payload_hash != message.payload_hash
            ) or stable_identity != candidate_identity:
                raise IdempotencyConflict(
                    f"inbox message key was reused with a different payload: {message.message_key}"
                )
            return IngressResult(record=existing, accepted_new=False)

        message_artifact = self.artifacts.put_json(
            {
                "message_key": message.message_key,
                "payload_hash": message.payload_hash,
                "platform": message.platform,
                "message_id": message.message_id,
                "chat_id": message.chat_id,
                "actor_id": message.actor_id,
                "text_artifact_id": text_artifact.artifact_id,
                "created_at": message.created_at,
            },
            kind="inbox_message_envelope",
        )
        route = self.router.route(message, context)
        thread_id = route.target_thread_id
        if route.route_type == InboxRouteType.NEW_TASK:
            thread_id = uuid5(NAMESPACE_URL, f"{message.message_key}:thread").hex

        route_state = (
            InboxRouteState.PROPOSED if route.requires_confirmation else InboxRouteState.CONFIRMED
        )
        record = InboxRecord(
            message_key=message.message_key,
            payload_hash=message.payload_hash,
            platform=message.platform,
            message_id=message.message_id,
            chat_id=message.chat_id,
            actor_id=message.actor_id,
            project_id=context.project_id,
            message_artifact_id=message_artifact.artifact_id,
            text_artifact_id=text_artifact.artifact_id,
            route_type=route.route_type,
            route_state=route_state,
            thread_id=thread_id,
            confidence=route.confidence,
            rationale=route.rationale,
            domain=route.domain,
            requires_confirmation=route.requires_confirmation,
            created_at=message.created_at,
            update_kind=route.update_kind,
            candidate_thread_ids=route.candidate_thread_ids,
            updated_at=message.created_at,
        )
        task_submission: TaskSubmission | None = None
        thread_update_submission: ThreadUpdateSubmission | None = None
        if record.route_type == InboxRouteType.NEW_TASK:
            task_submission = self._prepare_task_submission(
                record,
                objective=route.task_text or message.text.strip(),
            )
        elif (
            record.route_type == InboxRouteType.THREAD_UPDATE
            and record.route_state == InboxRouteState.CONFIRMED
        ):
            thread_update_submission = self._prepare_thread_update(
                record,
                task_text=route.task_text,
                update_kind=route.update_kind or ThreadUpdateKind.SUPPLEMENT,
                occurred_at=message.created_at,
            )
        stored, accepted_new = self.repository.accept_inbox_route(
            record,
            task_submission=task_submission,
            thread_update_submission=thread_update_submission,
        )
        return IngressResult(record=stored, accepted_new=accepted_new)

    def resolve_route(
        self,
        *,
        message_key: str,
        platform: str,
        actor_id: str,
        decision: str,
        target_thread_id: str | None = None,
        update_kind: ThreadUpdateKind | None = None,
        reason: str = "user_route_decision",
    ) -> InboxRecord:
        stored = self.repository.find_inbox_record(message_key)
        if stored is None:
            raise NotFound(f"inbox message not found: {message_key}")
        platform = platform.strip().lower()
        actor_id = actor_id.strip()
        if stored.platform != platform or stored.actor_id != actor_id:
            raise PermissionError("only the original channel actor may resolve this route")

        decision = decision.strip().lower()
        decision = {
            "new": "new_task",
            "dismiss": "expire",
        }.get(decision, decision)
        target_thread_id = target_thread_id.strip() if target_thread_id else None
        if stored.route_state != InboxRouteState.PROPOSED:
            same_decision = (
                decision == "new_task"
                and stored.route_type == InboxRouteType.NEW_TASK
                or decision == "thread"
                and stored.route_type == InboxRouteType.THREAD_UPDATE
                and stored.thread_id == target_thread_id
                and (update_kind is None or stored.update_kind == update_kind)
                or decision == "expire"
                and stored.route_state == InboxRouteState.EXPIRED
            )
            if same_decision:
                return stored
            raise IdempotencyConflict(
                f"inbox route is already resolved as {stored.route_state.value}"
            )

        resolved_at = to_timestamp(utc_now())
        resolver = f"{platform}:{actor_id}"
        task_submission: TaskSubmission | None = None
        thread_update_submission: ThreadUpdateSubmission | None = None
        if decision == "expire":
            resolved = replace(
                stored,
                route_state=InboxRouteState.EXPIRED,
                requires_confirmation=False,
                updated_at=resolved_at,
                resolved_by=resolver,
                resolution_reason=reason,
            )
        elif decision == "new_task":
            thread_id = uuid5(NAMESPACE_URL, f"{stored.message_key}:thread").hex
            resolved = replace(
                stored,
                route_type=InboxRouteType.NEW_TASK,
                route_state=InboxRouteState.CORRECTED,
                thread_id=thread_id,
                confidence=1.0,
                rationale="user explicitly selected a new Task",
                requires_confirmation=False,
                update_kind=None,
                updated_at=resolved_at,
                resolved_by=resolver,
                resolution_reason=reason,
            )
            task_submission = self._prepare_task_submission(
                resolved,
                objective=self._message_text(stored),
            )
        elif decision == "thread":
            if target_thread_id is None:
                raise ValueError("target_thread_id is required for a Thread route")
            kind = update_kind or stored.update_kind or ThreadUpdateKind.SUPPLEMENT
            resolved = replace(
                stored,
                route_type=InboxRouteType.THREAD_UPDATE,
                route_state=InboxRouteState.CORRECTED,
                thread_id=target_thread_id,
                confidence=1.0,
                rationale="user explicitly selected a target Thread",
                requires_confirmation=False,
                update_kind=kind,
                updated_at=resolved_at,
                resolved_by=resolver,
                resolution_reason=reason,
            )
            thread_update_submission = self._prepare_thread_update(
                resolved,
                task_text=self._message_text(stored),
                update_kind=kind,
                occurred_at=resolved_at,
            )
        else:
            raise ValueError("decision must be thread, new_task, or expire")

        return self.repository.resolve_inbox_route(
            resolved,
            resolver=resolver,
            resolution_reason=reason,
            task_submission=task_submission,
            thread_update_submission=thread_update_submission,
        )

    def _prepare_task_submission(
        self,
        record: InboxRecord,
        *,
        objective: str,
    ) -> TaskSubmission:
        if record.thread_id is None:
            raise ValueError("a new Task route requires a thread id")
        run_id = uuid5(NAMESPACE_URL, f"{record.message_key}:run").hex
        task_snapshot = self.snapshots.put_task(
            TaskSnapshot(
                task_id=record.thread_id,
                thread_id=record.thread_id,
                project_id=record.project_id,
                objective=objective,
                domain=record.domain,
                input_artifact_ids=(record.text_artifact_id,),
                created_at=record.updated_at or record.created_at,
            )
        )
        context_manifest = self.snapshots.put_context(
            ContextManifest(
                task_snapshot_id=task_snapshot.artifact_id,
                agent_snapshot_id=self.agent_snapshot_id,
                items=(
                    ContextItem(
                        ordinal=1,
                        source_type="inbox",
                        source_ref=record.message_key,
                        content_artifact_id=record.text_artifact_id,
                    ),
                ),
                compiler_version="inbox-context-v1",
                compiled_at=record.updated_at or record.created_at,
            )
        )
        return TaskSubmission(
            project_id=record.project_id,
            title=self._title(objective),
            thread_id=record.thread_id,
            run_id=run_id,
            correlation_id=record.message_key,
            metadata={
                "source": "inbox",
                "source_message_key": record.message_key,
                "domain": record.domain,
            },
            task_snapshot_id=task_snapshot.artifact_id,
            agent_snapshot_id=self.agent_snapshot_id,
            context_manifest_id=context_manifest.artifact_id,
        )

    def _prepare_thread_update(
        self,
        record: InboxRecord,
        *,
        task_text: str,
        update_kind: ThreadUpdateKind,
        occurred_at: str,
    ) -> ThreadUpdateSubmission:
        if record.thread_id is None:
            raise ValueError("a Thread update requires a target thread")
        task_text = task_text.strip()
        if not task_text:
            raise ValueError("Thread update text is required")
        thread = self.repository.get_thread(record.thread_id)
        if thread.project_id != record.project_id:
            raise ValueError("Thread update cannot cross Project boundaries")
        actor = f"{record.platform}:{record.actor_id}"
        if update_kind == ThreadUpdateKind.CANCEL:
            return ThreadUpdateSubmission(
                project_id=record.project_id,
                thread_id=record.thread_id,
                message_key=record.message_key,
                actor=actor,
                message_artifact_id=record.message_artifact_id,
                text_artifact_id=record.text_artifact_id,
                update_kind=update_kind,
                expected_revision=thread.revision,
                occurred_at=occurred_at,
            )

        terminal = {
            RunState.COMPLETED,
            RunState.PARTIAL,
            RunState.FAILED,
            RunState.QUARANTINED,
            RunState.CANCELLED,
        }
        branch_runs = [run for run in thread.runs if run.branch_id == thread.current_branch_id]
        basis_runs = branch_runs or list(thread.runs)
        basis = (
            max(
                basis_runs,
                key=lambda run: (run.created_sequence, run.created_at, run.run_id),
            )
            if basis_runs
            else None
        )
        base_task = (
            self.snapshots.load(basis.task_snapshot_id)
            if basis is not None and basis.task_snapshot_id is not None
            else None
        )
        base_context = (
            self.snapshots.load(basis.context_manifest_id)
            if basis is not None and basis.context_manifest_id is not None
            else None
        )
        agent_snapshot_id = (
            basis.agent_snapshot_id
            if basis is not None and basis.agent_snapshot_id is not None
            else self.agent_snapshot_id
        )

        objective = str(base_task.get("objective") if base_task else thread.title)
        acceptance_criteria = tuple(
            str(item) for item in (base_task or {}).get("acceptance_criteria", ())
        )
        constraints = tuple(str(item) for item in (base_task or {}).get("constraints", ()))
        input_artifact_ids = tuple(
            str(item) for item in (base_task or {}).get("input_artifact_ids", ())
        )
        task_snapshot_id = basis.task_snapshot_id if basis is not None else None
        if update_kind == ThreadUpdateKind.GOAL_CHANGE:
            objective = task_text
        if update_kind == ThreadUpdateKind.METHOD_CHANGE:
            constraints = (*constraints, f"User-selected method change: {task_text}")
        if task_snapshot_id is None or update_kind in {
            ThreadUpdateKind.GOAL_CHANGE,
            ThreadUpdateKind.METHOD_CHANGE,
        }:
            task_snapshot = self.snapshots.put_task(
                TaskSnapshot(
                    task_id=str((base_task or {}).get("task_id") or record.thread_id),
                    thread_id=record.thread_id,
                    project_id=record.project_id,
                    objective=objective,
                    domain=str((base_task or {}).get("domain") or record.domain),
                    acceptance_criteria=acceptance_criteria,
                    constraints=constraints,
                    input_artifact_ids=tuple(
                        dict.fromkeys((*input_artifact_ids, record.text_artifact_id))
                    ),
                    sensitivity=str((base_task or {}).get("sensitivity") or "normal"),
                    created_at=occurred_at,
                    schema_version=int((base_task or {}).get("schema_version", 1)),
                )
            )
            task_snapshot_id = task_snapshot.artifact_id
        assert task_snapshot_id is not None

        context_items = [
            ContextItem(
                ordinal=int(item["ordinal"]),
                source_type=str(item["source_type"]),
                source_ref=str(item["source_ref"]),
                content_artifact_id=str(item["content_artifact_id"]),
                allowed_use=str(item.get("allowed_use", "direct")),
                sensitivity=str(item.get("sensitivity", "normal")),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in (base_context or {}).get("items", ())
        ]
        next_ordinal = max((item.ordinal for item in context_items), default=0) + 1
        context_items.append(
            ContextItem(
                ordinal=next_ordinal,
                source_type="thread_update",
                source_ref=record.message_key,
                content_artifact_id=record.text_artifact_id,
                metadata={"update_kind": update_kind.value},
            )
        )
        context_manifest = self.snapshots.put_context(
            ContextManifest(
                task_snapshot_id=task_snapshot_id,
                agent_snapshot_id=agent_snapshot_id,
                items=tuple(context_items),
                compiler_version="thread-update-context-v1",
                compiled_at=occurred_at,
            )
        )
        new_run_id = uuid5(NAMESPACE_URL, f"{record.message_key}:thread-update:run").hex
        supersedes = tuple(
            run.run_id
            for run in thread.runs
            if run.branch_id == thread.current_branch_id and run.state not in terminal
        )
        branch_id = thread.current_branch_id
        forked_from_branch_id: str | None = None
        forked_from_event_id: str | None = None
        base_snapshot_hash: str | None = None
        reason_code: str | None = None
        if update_kind == ThreadUpdateKind.METHOD_CHANGE:
            branch_id = f"branch-{uuid5(NAMESPACE_URL, f'{record.message_key}:branch').hex[:12]}"
            forked_from_branch_id = thread.current_branch_id
            forked_from_event_id = thread.last_event_id
            base_snapshot_hash = thread.projection_hash
            reason_code = "user_method_change"

        return ThreadUpdateSubmission(
            project_id=record.project_id,
            thread_id=record.thread_id,
            message_key=record.message_key,
            actor=actor,
            message_artifact_id=record.message_artifact_id,
            text_artifact_id=record.text_artifact_id,
            update_kind=update_kind,
            expected_revision=thread.revision,
            occurred_at=occurred_at,
            new_run_id=new_run_id,
            branch_id=branch_id,
            forked_from_branch_id=forked_from_branch_id,
            forked_from_event_id=forked_from_event_id,
            base_snapshot_hash=base_snapshot_hash,
            reason_code=reason_code,
            supersedes_run_id=basis.run_id if basis is not None else None,
            supersedes_run_ids=supersedes,
            task_snapshot_id=task_snapshot_id,
            agent_snapshot_id=agent_snapshot_id,
            context_manifest_id=context_manifest.artifact_id,
        )

    def _message_text(self, record: InboxRecord) -> str:
        return self.artifacts.get_bytes(record.text_artifact_id).decode("utf-8")

    def _title(self, objective: str) -> str:
        compact = " ".join(objective.split())
        return compact[:80] or "Untitled task"


class IngressAdapter:
    """Normalizes one channel into the durable Inbox without calling Providers."""

    def __init__(
        self,
        *,
        platform: str,
        coordinator: InboxCoordinator,
        default_project_id: str,
    ) -> None:
        platform = platform.strip().lower()
        default_project_id = default_project_id.strip()
        if not platform or not default_project_id:
            raise ValueError("platform and default_project_id are required")
        self.platform = platform
        self.coordinator = coordinator
        self.default_project_id = default_project_id

    def receive(
        self,
        *,
        message_id: str,
        chat_id: str,
        actor_id: str,
        text: str,
        created_at: str,
        project_id: str | None = None,
        current_thread_id: str | None = None,
        active_thread_ids: tuple[str, ...] = (),
    ) -> IngressResult:
        resolved_project_id = (project_id or self.default_project_id).strip()
        if current_thread_id is None and not active_thread_ids:
            active_thread_ids = self.coordinator.repository.list_active_thread_ids_for_chat(
                platform=self.platform,
                chat_id=chat_id,
                project_id=resolved_project_id,
            )
        message = InboxMessage(
            platform=self.platform,
            message_id=message_id,
            chat_id=chat_id,
            actor_id=actor_id,
            text=text,
            created_at=created_at,
        )
        context = RoutingContext(
            project_id=resolved_project_id,
            current_thread_id=current_thread_id,
            active_thread_ids=active_thread_ids,
        )
        return self.coordinator.accept(message, context)
