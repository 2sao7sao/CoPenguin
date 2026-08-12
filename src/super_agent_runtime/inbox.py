from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from .artifacts import ArtifactCAS
from .coordinator import ThreadCoordinator
from .models import InboxRecord, InboxRouteType
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

    @property
    def message_key(self) -> str:
        return f"{self.platform}:{self.message_id}"


@dataclass(frozen=True)
class RoutingContext:
    project_id: str
    current_thread_id: str | None = None
    active_thread_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class InboxRoute:
    route_type: InboxRouteType
    confidence: float
    rationale: str
    domain: str
    requires_confirmation: bool = False
    target_thread_id: str | None = None
    task_text: str = ""


class ConservativeInboxRouter:
    """Deterministic first-pass router that avoids treating every chat as a task."""

    _control = re.compile(
        r"^/(status|approve|deny|pause|resume|cancel|archive)(?:\s|$)", re.IGNORECASE
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
            return InboxRoute(
                InboxRouteType.THREAD_UPDATE,
                1.0,
                "explicit thread target",
                self._domain(text),
                target_thread_id=thread_match.group(1),
                task_text=(thread_match.group(2) or "").strip(),
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
            )
        if has_continuation and len(context.active_thread_ids) == 1:
            return InboxRoute(
                InboxRouteType.THREAD_UPDATE,
                0.82,
                "continuation cue with one active candidate thread",
                self._domain(text),
                target_thread_id=context.active_thread_ids[0],
                task_text=text,
            )
        if has_continuation:
            return InboxRoute(
                InboxRouteType.AMBIGUOUS,
                0.45,
                "continuation cue has no unambiguous target thread",
                self._domain(text),
                requires_confirmation=True,
                task_text=text,
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


class InboxCoordinator:
    """Persists every route and turns explicit task messages into durable Threads."""

    def __init__(
        self,
        *,
        repository: SQLiteRuntimeRepository,
        artifacts: ArtifactCAS,
        snapshots: SnapshotStore,
        threads: ThreadCoordinator,
        agent_snapshot_id: str,
        router: ConservativeInboxRouter | None = None,
    ) -> None:
        if not agent_snapshot_id.startswith("artifact:sha256:"):
            raise ValueError("agent_snapshot_id must be an Artifact CAS reference")
        self.repository = repository
        self.artifacts = artifacts
        self.snapshots = snapshots
        self.threads = threads
        self.agent_snapshot_id = agent_snapshot_id
        self.router = router or ConservativeInboxRouter()

    def receive(self, message: InboxMessage, context: RoutingContext) -> InboxRecord:
        route = self.router.route(message, context)
        text_artifact = self.artifacts.put_text(message.text, kind="inbox_message")
        thread_id = route.target_thread_id

        if route.route_type == InboxRouteType.NEW_TASK:
            thread_id = uuid5(NAMESPACE_URL, f"{message.message_key}:thread").hex
            run_id = uuid5(NAMESPACE_URL, f"{message.message_key}:run").hex
            objective = route.task_text or message.text.strip()
            task_snapshot = self.snapshots.put_task(
                TaskSnapshot(
                    task_id=thread_id,
                    thread_id=thread_id,
                    project_id=context.project_id,
                    objective=objective,
                    domain=route.domain,
                    input_artifact_ids=(text_artifact.artifact_id,),
                    created_at=message.created_at,
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
                            source_ref=message.message_key,
                            content_artifact_id=text_artifact.artifact_id,
                        ),
                    ),
                    compiler_version="inbox-context-v1",
                    compiled_at=message.created_at,
                )
            )
            self.threads.submit_task(
                project_id=context.project_id,
                title=self._title(objective),
                thread_id=thread_id,
                run_id=run_id,
                correlation_id=message.message_key,
                metadata={
                    "source": "inbox",
                    "source_message_key": message.message_key,
                    "domain": route.domain,
                },
                task_snapshot_id=task_snapshot.artifact_id,
                agent_snapshot_id=self.agent_snapshot_id,
                context_manifest_id=context_manifest.artifact_id,
            )

        record = InboxRecord(
            message_key=message.message_key,
            platform=message.platform,
            message_id=message.message_id,
            chat_id=message.chat_id,
            actor_id=message.actor_id,
            text_artifact_id=text_artifact.artifact_id,
            route_type=route.route_type,
            thread_id=thread_id,
            confidence=route.confidence,
            rationale=route.rationale,
            domain=route.domain,
            requires_confirmation=route.requires_confirmation,
            created_at=message.created_at,
        )
        return self.repository.record_inbox_route(record)

    def _title(self, objective: str) -> str:
        compact = " ".join(objective.split())
        return compact[:80] or "Untitled task"
