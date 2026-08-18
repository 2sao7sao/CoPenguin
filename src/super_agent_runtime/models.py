from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class DesiredState(StrEnum):
    RUN = "run"
    PAUSE = "pause"
    CANCEL = "cancel"
    ARCHIVE = "archive"


class ThreadState(StrEnum):
    CREATED = "created"
    DORMANT = "dormant"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_RECEIPT = "waiting_receipt"
    WAITING_DEPENDENCY = "waiting_dependency"
    WAITING_RESOURCE = "waiting_resource"
    VERIFYING = "verifying"
    DELIVERED = "delivered"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class AttentionState(StrEnum):
    NONE = "none"
    NEEDS_INPUT = "needs_input"
    NEEDS_APPROVAL = "needs_approval"
    HAS_CONFLICT = "has_conflict"
    DELIVERY_READY = "delivery_ready"
    FAILED = "failed"


class RunState(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_RECEIPT = "waiting_receipt"
    WAITING_DEPENDENCY = "waiting_dependency"
    WAITING_RESOURCE = "waiting_resource"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    CANCELLED = "cancelled"


class JobState(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AccessMode(StrEnum):
    READ = "read"
    WRITE = "write"
    EXCLUSIVE = "exclusive"


class ActionStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"
    RECONCILE_REQUIRED = "reconcile_required"
    RECOVERING = "recovering"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    QUARANTINED = "quarantined"


class ReceiptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


class InboxRouteType(StrEnum):
    CHAT = "chat"
    NEW_TASK = "new_task"
    THREAD_UPDATE = "thread_update"
    CONTROL = "control"
    AMBIGUOUS = "ambiguous"


class InboxRouteState(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    EXPIRED = "expired"


class ThreadUpdateKind(StrEnum):
    SUPPLEMENT = "supplement"
    GOAL_CHANGE = "goal_change"
    METHOD_CHANGE = "method_change"
    CANCEL = "cancel"


class BranchStatus(StrEnum):
    ACTIVE = "active"
    SELECTED = "selected"
    REJECTED = "rejected"
    MERGED = "merged"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class EventDraft:
    stream_type: str
    stream_id: str
    event_type: str
    actor: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid4().hex)
    project_id: str | None = None
    thread_id: str | None = None
    branch_id: str | None = None
    run_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    occurred_at: str = field(default_factory=lambda: to_timestamp(utc_now()))
    schema_version: int = 1

    def normalized(self) -> EventDraft:
        """Freeze a caller-owned payload into a canonical, JSON-compatible mapping."""
        payload = json.loads(canonical_json(dict(self.payload)))
        return EventDraft(
            stream_type=self.stream_type,
            stream_id=self.stream_id,
            event_type=self.event_type,
            actor=self.actor,
            payload=MappingProxyType(payload),
            event_id=self.event_id,
            project_id=self.project_id,
            thread_id=self.thread_id,
            branch_id=self.branch_id,
            run_id=self.run_id,
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
            occurred_at=self.occurred_at,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True)
class EventEnvelope:
    global_position: int
    sequence: int
    event_id: str
    stream_type: str
    stream_id: str
    event_type: str
    actor: str
    payload: Mapping[str, Any]
    occurred_at: str
    schema_version: int = 1
    project_id: str | None = None
    thread_id: str | None = None
    branch_id: str | None = None
    run_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None


@dataclass(frozen=True)
class RunProjection:
    run_id: str
    branch_id: str
    executor_key: str = "unassigned"
    state: RunState = RunState.CREATED
    revision: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    waiting_on: tuple[str, ...] = ()
    output_artifact_id: str | None = None
    task_snapshot_id: str | None = None
    agent_snapshot_id: str | None = None
    context_manifest_id: str | None = None
    latest_checkpoint_id: str | None = None
    checkpoint_sequence: int = 0
    error: str | None = None
    created_at: str = ""
    created_sequence: int = 0
    supersedes_run_id: str | None = None


@dataclass(frozen=True)
class BranchProjection:
    branch_id: str
    status: BranchStatus
    created_by: str
    created_at: str
    forked_from_branch_id: str | None = None
    forked_from_event_id: str | None = None
    base_snapshot_hash: str | None = None
    reason_code: str | None = None
    selected_at: str | None = None
    rejected_at: str | None = None


@dataclass(frozen=True)
class ThreadUpdateProjection:
    update_id: str
    message_key: str
    message_artifact_id: str
    text_artifact_id: str
    kind: ThreadUpdateKind
    actor_id: str
    branch_id: str
    occurred_at: str
    task_snapshot_id: str | None = None
    context_manifest_id: str | None = None
    new_run_id: str | None = None


@dataclass(frozen=True)
class ThreadProjection:
    thread_id: str
    project_id: str
    title: str
    desired_state: DesiredState = DesiredState.RUN
    actual_state: ThreadState = ThreadState.CREATED
    attention_state: AttentionState = AttentionState.NONE
    revision: int = 0
    current_branch_id: str = "main"
    active_run_id: str | None = None
    waiting_on: tuple[str, ...] = ()
    latest_delivery_id: str | None = None
    last_event_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    runs: tuple[RunProjection, ...] = ()
    branches: tuple[BranchProjection, ...] = ()
    updates: tuple[ThreadUpdateProjection, ...] = ()

    def run(self, run_id: str) -> RunProjection | None:
        return next((item for item in self.runs if item.run_id == run_id), None)

    def branch(self, branch_id: str) -> BranchProjection | None:
        return next((item for item in self.branches if item.branch_id == branch_id), None)

    def update(self, update_id: str) -> ThreadUpdateProjection | None:
        return next((item for item in self.updates if item.update_id == update_id), None)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def projection_hash(self) -> str:
        return content_hash(self.as_dict())


@dataclass(frozen=True)
class SchedulerJob:
    run_id: str
    thread_id: str
    state: JobState
    priority: int
    available_at: str
    attempts: int
    max_attempts: int
    executor_key: str = "unassigned"
    lease_owner: str | None = None
    lease_id: str | None = None
    fencing_token: int = 0
    lease_expires_at: str | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkerClaim:
    run_id: str
    thread_id: str
    worker_id: str
    lease_id: str
    fencing_token: int
    expires_at: str
    attempt: int


@dataclass(frozen=True)
class ResourceLease:
    lease_id: str
    resource_key: str
    mode: AccessMode
    owner_run_id: str
    thread_id: str
    fencing_token: int
    acquired_at: str
    expires_at: str


@dataclass(frozen=True)
class ActionIntent:
    intent_id: str
    idempotency_key: str
    thread_id: str
    run_id: str
    capability: str
    request_artifact_id: str
    payload_hash: str
    status: ActionStatus
    created_at: str
    updated_at: str
    requires_approval: bool = False
    fencing_token: int = 0
    lease_owner: str | None = None
    lease_id: str | None = None
    lease_expires_at: str | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActionClaim:
    intent_id: str
    idempotency_key: str
    thread_id: str
    run_id: str
    capability: str
    request_artifact_id: str
    worker_id: str
    lease_id: str
    fencing_token: int
    expires_at: str
    reconciliation: bool = False


@dataclass(frozen=True)
class ActionReceipt:
    receipt_id: str
    intent_id: str
    outcome: ReceiptOutcome
    provider: str
    occurred_at: str
    response_artifact_id: str | None = None
    external_reference: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InboxRecord:
    message_key: str
    payload_hash: str
    platform: str
    message_id: str
    chat_id: str
    actor_id: str
    project_id: str
    message_artifact_id: str
    text_artifact_id: str
    route_type: InboxRouteType
    route_state: InboxRouteState
    confidence: float
    rationale: str
    domain: str
    requires_confirmation: bool
    created_at: str
    thread_id: str | None = None
    update_kind: ThreadUpdateKind | None = None
    candidate_thread_ids: tuple[str, ...] = ()
    updated_at: str = ""
    resolved_by: str | None = None
    resolution_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskSubmission:
    project_id: str
    title: str
    thread_id: str
    run_id: str
    branch_id: str = "main"
    actor: str = "inbox-router"
    correlation_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    priority: int = 0
    max_attempts: int = 3
    executor_key: str = "unassigned"
    task_snapshot_id: str | None = None
    agent_snapshot_id: str | None = None
    context_manifest_id: str | None = None


@dataclass(frozen=True)
class ThreadUpdateSubmission:
    project_id: str
    thread_id: str
    message_key: str
    actor: str
    message_artifact_id: str
    text_artifact_id: str
    update_kind: ThreadUpdateKind
    expected_revision: int
    occurred_at: str
    new_run_id: str | None = None
    branch_id: str | None = None
    forked_from_branch_id: str | None = None
    forked_from_event_id: str | None = None
    base_snapshot_hash: str | None = None
    reason_code: str | None = None
    supersedes_run_id: str | None = None
    supersedes_run_ids: tuple[str, ...] = ()
    task_snapshot_id: str | None = None
    agent_snapshot_id: str | None = None
    context_manifest_id: str | None = None
    priority: int = 0
    max_attempts: int = 3
    executor_key: str = "unassigned"


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    intent_id: str
    status: ApprovalState
    risk_level: str
    requested_by: str
    reason: str
    created_at: str
    expires_at: str
    updated_at: str
    policy_snapshot_id: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None
    decision_evidence_artifact_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
