from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from .errors import (
    ConcurrencyConflict,
    IdempotencyConflict,
    InvalidTransition,
    NotFound,
    ReconciliationRequired,
    ResourceConflict,
    StaleLease,
)
from .models import (
    AccessMode,
    ActionClaim,
    ActionIntent,
    ActionReceipt,
    ActionStatus,
    ApprovalRequest,
    ApprovalState,
    AttentionState,
    BranchProjection,
    BranchStatus,
    DeliveryRecord,
    DeliveryState,
    DesiredState,
    EventDraft,
    EventEnvelope,
    InboxRecord,
    InboxRouteState,
    InboxRouteType,
    JobState,
    OutboxItem,
    OutboxState,
    ReceiptOutcome,
    ResourceLease,
    RunProjection,
    RunState,
    SchedulerJob,
    StepKind,
    StepRecord,
    StepState,
    TaskSubmission,
    ThreadProjection,
    ThreadState,
    ThreadUpdateKind,
    ThreadUpdateProjection,
    ThreadUpdateSubmission,
    WorkerClaim,
    canonical_json,
    to_timestamp,
    utc_now,
)
from .reducer import reduce_thread


class SQLiteRuntimeRepository:
    """SQLite-backed event journal, projections, scheduler, and lease coordinator."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database_path = Path(database_path)
        self._clock = clock
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runtime_events (
                    global_position INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    stream_type TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    project_id TEXT,
                    thread_id TEXT,
                    branch_id TEXT,
                    run_id TEXT,
                    correlation_id TEXT,
                    causation_id TEXT,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    UNIQUE(stream_type, stream_id, sequence)
                );

                CREATE INDEX IF NOT EXISTS idx_runtime_events_thread
                    ON runtime_events(thread_id, global_position);
                CREATE INDEX IF NOT EXISTS idx_runtime_events_correlation
                    ON runtime_events(correlation_id, global_position);
                CREATE INDEX IF NOT EXISTS idx_runtime_events_run
                    ON runtime_events(run_id, global_position);

                CREATE TABLE IF NOT EXISTS thread_projections (
                    thread_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    desired_state TEXT NOT NULL,
                    actual_state TEXT NOT NULL,
                    attention_state TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    active_run_id TEXT,
                    latest_delivery_id TEXT,
                    updated_at TEXT NOT NULL,
                    projection_json TEXT NOT NULL,
                    projection_hash TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_thread_projection_sidebar
                    ON thread_projections(project_id, attention_state, actual_state, updated_at DESC);

                CREATE TABLE IF NOT EXISTS scheduler_jobs (
                    run_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    executor_key TEXT NOT NULL DEFAULT 'unassigned',
                    state TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    lease_owner TEXT,
                    lease_id TEXT,
                    fencing_token INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_scheduler_runnable
                    ON scheduler_jobs(state, available_at, priority DESC);
                CREATE INDEX IF NOT EXISTS idx_scheduler_thread
                    ON scheduler_jobs(thread_id, state);
                CREATE TABLE IF NOT EXISTS resource_fences (
                    resource_key TEXT PRIMARY KEY,
                    last_token INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS resource_leases (
                    lease_id TEXT PRIMARY KEY,
                    resource_key TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    owner_run_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    released_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_resource_active
                    ON resource_leases(resource_key, status, expires_at);

                CREATE TABLE IF NOT EXISTS action_intents (
                    intent_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    thread_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    request_artifact_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    requires_approval INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_id TEXT,
                    lease_expires_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_action_recovery
                    ON action_intents(status, lease_expires_at, updated_at);
                CREATE INDEX IF NOT EXISTS idx_action_run
                    ON action_intents(run_id, status, updated_at);

                CREATE TABLE IF NOT EXISTS action_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    response_artifact_id TEXT,
                    external_reference TEXT,
                    evidence_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    FOREIGN KEY(intent_id) REFERENCES action_intents(intent_id)
                );

                CREATE INDEX IF NOT EXISTS idx_receipt_intent
                    ON action_receipts(intent_id, occurred_at);

                CREATE TABLE IF NOT EXISTS inbox_messages (
                    message_key TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    message_artifact_id TEXT NOT NULL,
                    text_artifact_id TEXT NOT NULL,
                    route_type TEXT NOT NULL,
                    route_state TEXT NOT NULL,
                    thread_id TEXT,
                    confidence REAL NOT NULL,
                    rationale TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    requires_confirmation INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    update_kind TEXT,
                    candidate_thread_ids_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL,
                    resolved_by TEXT,
                    resolution_reason TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_inbox_route
                    ON inbox_messages(route_type, created_at);
                CREATE INDEX IF NOT EXISTS idx_inbox_thread
                    ON inbox_messages(thread_id, created_at);

                CREATE TABLE IF NOT EXISTS runtime_approvals (
                    approval_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    policy_snapshot_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_by TEXT,
                    resolved_at TEXT,
                    decision_evidence_artifact_id TEXT,
                    FOREIGN KEY(intent_id) REFERENCES action_intents(intent_id)
                );

                CREATE INDEX IF NOT EXISTS idx_approval_attention
                    ON runtime_approvals(status, expires_at, updated_at);

                CREATE TABLE IF NOT EXISTS runtime_steps (
                    step_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    provider_key TEXT NOT NULL,
                    provider_version TEXT NOT NULL,
                    input_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    output_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    budget_json TEXT NOT NULL DEFAULT '{}',
                    config_snapshot_id TEXT,
                    verifier_result_artifact_id TEXT,
                    error_code TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE(run_id, ordinal, attempt)
                );

                CREATE INDEX IF NOT EXISTS idx_steps_run
                    ON runtime_steps(run_id, ordinal, attempt);

                CREATE TABLE IF NOT EXISTS deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    version INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    summary_artifact_id TEXT NOT NULL,
                    primary_artifact_id TEXT NOT NULL,
                    supporting_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    verifier_result_artifact_id TEXT NOT NULL,
                    previous_delivery_id TEXT,
                    allowed_decisions_json TEXT NOT NULL DEFAULT '[]',
                    sensitivity TEXT NOT NULL,
                    export_policy TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    presented_at TEXT,
                    decided_at TEXT,
                    UNIQUE(thread_id, version)
                );

                CREATE INDEX IF NOT EXISTS idx_deliveries_thread
                    ON deliveries(thread_id, version DESC);
                CREATE INDEX IF NOT EXISTS idx_deliveries_state
                    ON deliveries(state, created_at);

                CREATE TABLE IF NOT EXISTS runtime_outbox (
                    outbox_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    thread_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    payload_artifact_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_id TEXT,
                    lease_expires_at TEXT,
                    receipt_artifact_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_outbox_dispatch
                    ON runtime_outbox(state, available_at, created_at);
                """
            )
            action_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(action_intents)").fetchall()
            }
            if "requires_approval" not in action_columns:
                connection.execute(
                    "ALTER TABLE action_intents "
                    "ADD COLUMN requires_approval INTEGER NOT NULL DEFAULT 0"
                )
            scheduler_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(scheduler_jobs)").fetchall()
            }
            if "executor_key" not in scheduler_columns:
                connection.execute(
                    "ALTER TABLE scheduler_jobs "
                    "ADD COLUMN executor_key TEXT NOT NULL DEFAULT 'unassigned'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduler_executor "
                "ON scheduler_jobs(executor_key, state, available_at, priority DESC)"
            )
            inbox_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(inbox_messages)").fetchall()
            }
            if "payload_hash" not in inbox_columns:
                connection.execute(
                    "ALTER TABLE inbox_messages ADD COLUMN payload_hash TEXT NOT NULL DEFAULT ''"
                )
            if "project_id" not in inbox_columns:
                connection.execute(
                    "ALTER TABLE inbox_messages ADD COLUMN project_id TEXT NOT NULL DEFAULT ''"
                )
            if "route_state" not in inbox_columns:
                connection.execute(
                    "ALTER TABLE inbox_messages "
                    "ADD COLUMN route_state TEXT NOT NULL DEFAULT 'confirmed'"
                )
                connection.execute(
                    "UPDATE inbox_messages SET route_state = 'proposed' "
                    "WHERE requires_confirmation = 1"
                )
            if "message_artifact_id" not in inbox_columns:
                connection.execute(
                    "ALTER TABLE inbox_messages "
                    "ADD COLUMN message_artifact_id TEXT NOT NULL DEFAULT ''"
                )
            if "update_kind" not in inbox_columns:
                connection.execute("ALTER TABLE inbox_messages ADD COLUMN update_kind TEXT")
            if "candidate_thread_ids_json" not in inbox_columns:
                connection.execute(
                    "ALTER TABLE inbox_messages "
                    "ADD COLUMN candidate_thread_ids_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "updated_at" not in inbox_columns:
                connection.execute(
                    "ALTER TABLE inbox_messages ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    "UPDATE inbox_messages SET updated_at = created_at WHERE updated_at = ''"
                )
            if "resolved_by" not in inbox_columns:
                connection.execute("ALTER TABLE inbox_messages ADD COLUMN resolved_by TEXT")
            if "resolution_reason" not in inbox_columns:
                connection.execute("ALTER TABLE inbox_messages ADD COLUMN resolution_reason TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_inbox_project "
                "ON inbox_messages(project_id, created_at)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (1, to_timestamp(self._clock())),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (2, to_timestamp(self._clock())),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (3, to_timestamp(self._clock())),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (4, to_timestamp(self._clock())),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (5, to_timestamp(self._clock())),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (6, to_timestamp(self._clock())),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (7, to_timestamp(self._clock())),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (8, to_timestamp(self._clock())),
            )

    # ------------------------------------------------------------------
    # Thread event journal and deterministic projection
    # ------------------------------------------------------------------

    def create_thread(
        self,
        *,
        project_id: str,
        title: str,
        thread_id: str | None = None,
        branch_id: str = "main",
        actor: str = "user",
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> ThreadProjection:
        thread_id = thread_id or uuid4().hex
        draft = EventDraft(
            event_id=event_id or uuid4().hex,
            stream_type="thread",
            stream_id=thread_id,
            project_id=project_id,
            thread_id=thread_id,
            branch_id=branch_id,
            correlation_id=correlation_id,
            event_type="thread.created",
            actor=actor,
            occurred_at=to_timestamp(self._clock()),
            payload={
                "project_id": project_id,
                "title": title,
                "branch_id": branch_id,
                "metadata": metadata or {},
            },
        )
        return self.append_thread_events([draft], expected_revision=0)

    def submit_task(
        self,
        *,
        project_id: str,
        title: str,
        thread_id: str,
        run_id: str,
        branch_id: str = "main",
        actor: str = "inbox-router",
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        executor_key: str = "unassigned",
        task_snapshot_id: str | None = None,
        agent_snapshot_id: str | None = None,
        context_manifest_id: str | None = None,
    ) -> ThreadProjection:
        """Atomically create a Thread, its first Run, and its scheduler job."""
        submission = TaskSubmission(
            project_id=project_id,
            title=title,
            thread_id=thread_id,
            run_id=run_id,
            branch_id=branch_id,
            actor=actor,
            correlation_id=correlation_id,
            metadata=metadata or {},
            priority=priority,
            max_attempts=max_attempts,
            executor_key=executor_key,
            task_snapshot_id=task_snapshot_id,
            agent_snapshot_id=agent_snapshot_id,
            context_manifest_id=context_manifest_id,
        )
        with self._transaction() as connection:
            return self._submit_task_in_transaction(
                connection,
                submission,
                now=to_timestamp(self._clock()),
            )

    def _submit_task_in_transaction(
        self,
        connection: sqlite3.Connection,
        submission: TaskSubmission,
        *,
        now: str,
    ) -> ThreadProjection:
        snapshot_ids = (
            submission.task_snapshot_id,
            submission.agent_snapshot_id,
            submission.context_manifest_id,
        )
        if any(snapshot_ids) and not all(snapshot_ids):
            raise ValueError("task, agent, and context snapshot ids must be provided together")
        if submission.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not submission.executor_key.strip():
            raise ValueError("executor_key is required")

        existing = self._get_thread_in_transaction(connection, submission.thread_id)
        if existing is not None:
            run = existing.run(submission.run_id)
            snapshots_match = not any(snapshot_ids) or (
                run is not None
                and (
                    run.task_snapshot_id,
                    run.agent_snapshot_id,
                    run.context_manifest_id,
                )
                == snapshot_ids
            )
            executor_matches = (
                submission.executor_key == "unassigned"
                or run is not None
                and run.executor_key == submission.executor_key
            )
            if (
                existing.project_id == submission.project_id
                and existing.title == submission.title
                and run is not None
                and snapshots_match
                and executor_matches
            ):
                return existing
            raise IdempotencyConflict(
                f"thread id {submission.thread_id} was reused for a different task submission"
            )

        drafts = [
            EventDraft(
                stream_type="thread",
                stream_id=submission.thread_id,
                project_id=submission.project_id,
                thread_id=submission.thread_id,
                branch_id=submission.branch_id,
                correlation_id=submission.correlation_id,
                event_type="thread.created",
                actor=submission.actor,
                occurred_at=now,
                payload={
                    "project_id": submission.project_id,
                    "title": submission.title,
                    "branch_id": submission.branch_id,
                    "metadata": dict(submission.metadata),
                },
            ),
            EventDraft(
                stream_type="thread",
                stream_id=submission.thread_id,
                project_id=submission.project_id,
                thread_id=submission.thread_id,
                branch_id=submission.branch_id,
                run_id=submission.run_id,
                correlation_id=submission.correlation_id,
                event_type="run.created",
                actor=submission.actor,
                occurred_at=now,
                payload={
                    "run_id": submission.run_id,
                    "branch_id": submission.branch_id,
                    "executor_key": submission.executor_key,
                },
            ),
        ]
        if all(snapshot_ids):
            drafts.append(
                EventDraft(
                    stream_type="thread",
                    stream_id=submission.thread_id,
                    project_id=submission.project_id,
                    thread_id=submission.thread_id,
                    branch_id=submission.branch_id,
                    run_id=submission.run_id,
                    correlation_id=submission.correlation_id,
                    event_type="run.snapshots_bound",
                    actor="context-compiler",
                    occurred_at=now,
                    payload={
                        "run_id": submission.run_id,
                        "task_snapshot_id": submission.task_snapshot_id,
                        "agent_snapshot_id": submission.agent_snapshot_id,
                        "context_manifest_id": submission.context_manifest_id,
                    },
                )
            )
        drafts.extend(
            [
                EventDraft(
                    stream_type="thread",
                    stream_id=submission.thread_id,
                    project_id=submission.project_id,
                    thread_id=submission.thread_id,
                    branch_id=submission.branch_id,
                    run_id=submission.run_id,
                    correlation_id=submission.correlation_id,
                    event_type="run.state_changed",
                    actor="scheduler",
                    occurred_at=now,
                    payload={
                        "run_id": submission.run_id,
                        "from": RunState.CREATED.value,
                        "to": RunState.QUEUED.value,
                    },
                ),
                EventDraft(
                    stream_type="thread",
                    stream_id=submission.thread_id,
                    project_id=submission.project_id,
                    thread_id=submission.thread_id,
                    branch_id=submission.branch_id,
                    run_id=submission.run_id,
                    correlation_id=submission.correlation_id,
                    event_type="thread.state_changed",
                    actor="scheduler",
                    occurred_at=now,
                    payload={
                        "from": ThreadState.CREATED.value,
                        "to": ThreadState.QUEUED.value,
                    },
                ),
            ]
        )
        projection: ThreadProjection | None = None
        for draft in drafts:
            event = self._insert_event(connection, draft)
            projection = reduce_thread(projection, event)
        assert projection is not None
        self._upsert_thread_projection(connection, projection)
        connection.execute(
            """
            INSERT INTO scheduler_jobs(
                run_id, thread_id, executor_key, state, priority, available_at, attempts,
                max_attempts, fencing_token, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?)
            """,
            (
                submission.run_id,
                submission.thread_id,
                submission.executor_key,
                JobState.QUEUED.value,
                submission.priority,
                now,
                submission.max_attempts,
                now,
                now,
            ),
        )
        self._insert_event(
            connection,
            EventDraft(
                stream_type="scheduler-run",
                stream_id=submission.run_id,
                project_id=submission.project_id,
                thread_id=submission.thread_id,
                branch_id=submission.branch_id,
                run_id=submission.run_id,
                correlation_id=submission.correlation_id,
                event_type="scheduler.run_enqueued",
                actor="scheduler",
                occurred_at=now,
                payload={
                    "priority": submission.priority,
                    "available_at": now,
                    "max_attempts": submission.max_attempts,
                    "executor_key": submission.executor_key,
                },
            ),
        )
        return projection

    def append_thread_event(
        self,
        draft: EventDraft,
        *,
        expected_revision: int,
    ) -> ThreadProjection:
        return self.append_thread_events([draft], expected_revision=expected_revision)

    def append_thread_events(
        self,
        drafts: Iterable[EventDraft],
        *,
        expected_revision: int,
    ) -> ThreadProjection:
        normalized = [draft.normalized() for draft in drafts]
        if not normalized:
            raise ValueError("at least one event is required")
        stream_ids = {(draft.stream_type, draft.stream_id) for draft in normalized}
        if stream_ids != {("thread", normalized[0].stream_id)}:
            raise ValueError("all events must target the same thread stream")
        if any(draft.thread_id != normalized[0].stream_id for draft in normalized):
            raise ValueError("thread event thread_id must match stream_id")

        with self._transaction() as connection:
            existing = [self._event_by_id(connection, draft.event_id) for draft in normalized]
            if all(item is not None for item in existing):
                for draft, envelope in zip(normalized, existing, strict=True):
                    assert envelope is not None
                    self._assert_same_event(draft, envelope)
                projection = self._get_thread_in_transaction(connection, normalized[0].stream_id)
                if projection is None:
                    raise NotFound(f"thread projection missing: {normalized[0].stream_id}")
                return projection
            if any(item is not None for item in existing):
                raise IdempotencyConflict("event batch partially overlaps existing event ids")

            projection = self._get_thread_in_transaction(connection, normalized[0].stream_id)
            actual_revision = projection.revision if projection else 0
            if actual_revision != expected_revision:
                raise ConcurrencyConflict(
                    f"thread {normalized[0].stream_id} revision is {actual_revision}; "
                    f"expected {expected_revision}"
                )

            for draft in normalized:
                envelope = self._insert_event(connection, draft)
                projection = reduce_thread(projection, envelope)

            assert projection is not None
            self._upsert_thread_projection(connection, projection)
            return projection

    def transition_thread(
        self,
        thread_id: str,
        target: ThreadState,
        *,
        expected_revision: int,
        actor: str,
        reason: str | None = None,
        waiting_on: Iterable[str] = (),
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ThreadProjection:
        current = self.get_thread(thread_id)
        draft = EventDraft(
            stream_type="thread",
            stream_id=thread_id,
            thread_id=thread_id,
            project_id=current.project_id,
            branch_id=current.current_branch_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            event_type="thread.state_changed",
            actor=actor,
            occurred_at=to_timestamp(self._clock()),
            payload={
                "from": current.actual_state.value,
                "to": target.value,
                "reason": reason,
                "waiting_on": list(waiting_on),
            },
        )
        return self.append_thread_event(draft, expected_revision=expected_revision)

    def set_desired_state(
        self,
        thread_id: str,
        target: DesiredState,
        *,
        expected_revision: int,
        actor: str,
    ) -> ThreadProjection:
        current = self.get_thread(thread_id)
        draft = EventDraft(
            stream_type="thread",
            stream_id=thread_id,
            thread_id=thread_id,
            project_id=current.project_id,
            event_type="thread.desired_state_changed",
            actor=actor,
            occurred_at=to_timestamp(self._clock()),
            payload={"from": current.desired_state.value, "to": target.value},
        )
        return self.append_thread_event(draft, expected_revision=expected_revision)

    def set_attention(
        self,
        thread_id: str,
        target: AttentionState,
        *,
        expected_revision: int,
        actor: str,
        reason: str | None = None,
    ) -> ThreadProjection:
        current = self.get_thread(thread_id)
        draft = EventDraft(
            stream_type="thread",
            stream_id=thread_id,
            thread_id=thread_id,
            project_id=current.project_id,
            event_type="thread.attention_changed",
            actor=actor,
            occurred_at=to_timestamp(self._clock()),
            payload={"from": current.attention_state.value, "to": target.value, "reason": reason},
        )
        return self.append_thread_event(draft, expected_revision=expected_revision)

    def create_run(
        self,
        thread_id: str,
        *,
        expected_revision: int,
        run_id: str | None = None,
        branch_id: str = "main",
        executor_key: str = "unassigned",
        actor: str = "runtime",
        correlation_id: str | None = None,
    ) -> ThreadProjection:
        current = self.get_thread(thread_id)
        run_id = run_id or uuid4().hex
        draft = EventDraft(
            stream_type="thread",
            stream_id=thread_id,
            project_id=current.project_id,
            thread_id=thread_id,
            branch_id=branch_id,
            run_id=run_id,
            correlation_id=correlation_id,
            event_type="run.created",
            actor=actor,
            occurred_at=to_timestamp(self._clock()),
            payload={
                "run_id": run_id,
                "branch_id": branch_id,
                "executor_key": executor_key,
            },
        )
        return self.append_thread_event(draft, expected_revision=expected_revision)

    def transition_run(
        self,
        thread_id: str,
        run_id: str,
        target: RunState,
        *,
        expected_revision: int,
        actor: str,
        reason: str | None = None,
        waiting_on: Iterable[str] = (),
        output_artifact_id: str | None = None,
        error: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ThreadProjection:
        current = self.get_thread(thread_id)
        run = current.run(run_id)
        if run is None:
            raise NotFound(f"run not found: {run_id}")
        draft = EventDraft(
            stream_type="thread",
            stream_id=thread_id,
            project_id=current.project_id,
            thread_id=thread_id,
            branch_id=run.branch_id,
            run_id=run_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            event_type="run.state_changed",
            actor=actor,
            occurred_at=to_timestamp(self._clock()),
            payload={
                "run_id": run_id,
                "from": run.state.value,
                "to": target.value,
                "reason": reason,
                "waiting_on": list(waiting_on),
                "output_artifact_id": output_artifact_id,
                "error": error,
            },
        )
        return self.append_thread_event(draft, expected_revision=expected_revision)

    def bind_run_snapshots(
        self,
        thread_id: str,
        run_id: str,
        *,
        task_snapshot_id: str,
        agent_snapshot_id: str,
        context_manifest_id: str,
        expected_revision: int,
        actor: str = "context-compiler",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ThreadProjection:
        current = self.get_thread(thread_id)
        run = current.run(run_id)
        if run is None:
            raise NotFound(f"run not found: {run_id}")
        draft = EventDraft(
            stream_type="thread",
            stream_id=thread_id,
            project_id=current.project_id,
            thread_id=thread_id,
            branch_id=run.branch_id,
            run_id=run_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            event_type="run.snapshots_bound",
            actor=actor,
            occurred_at=to_timestamp(self._clock()),
            payload={
                "run_id": run_id,
                "task_snapshot_id": task_snapshot_id,
                "agent_snapshot_id": agent_snapshot_id,
                "context_manifest_id": context_manifest_id,
            },
        )
        return self.append_thread_event(draft, expected_revision=expected_revision)

    def record_run_checkpoint(
        self,
        thread_id: str,
        run_id: str,
        *,
        checkpoint_id: str,
        expected_revision: int,
        actor: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ThreadProjection:
        if not checkpoint_id.startswith("artifact:sha256:"):
            raise ValueError("checkpoint_id must be an Artifact CAS reference")
        current = self.get_thread(thread_id)
        run = current.run(run_id)
        if run is None:
            raise NotFound(f"run not found: {run_id}")
        draft = EventDraft(
            stream_type="thread",
            stream_id=thread_id,
            project_id=current.project_id,
            thread_id=thread_id,
            branch_id=run.branch_id,
            run_id=run_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            event_type="run.checkpoint_recorded",
            actor=actor,
            occurred_at=to_timestamp(self._clock()),
            payload={
                "run_id": run_id,
                "checkpoint_id": checkpoint_id,
                "checkpoint_sequence": run.checkpoint_sequence + 1,
            },
        )
        return self.append_thread_event(draft, expected_revision=expected_revision)

    def record_claimed_run_checkpoint(
        self,
        claim: WorkerClaim,
        *,
        checkpoint_id: str,
    ) -> ThreadProjection:
        if not checkpoint_id.startswith("artifact:sha256:"):
            raise ValueError("checkpoint_id must be an Artifact CAS reference")
        now = to_timestamp(self._clock())
        with self._transaction() as connection:
            self._assert_live_worker_claim(connection, claim, now)
            projection = self._get_thread_in_transaction(connection, claim.thread_id)
            if projection is None:
                raise NotFound(f"thread not found: {claim.thread_id}")
            run = projection.run(claim.run_id)
            if run is None:
                raise NotFound(f"run not found: {claim.run_id}")
            if run.state != RunState.RUNNING or projection.active_run_id != claim.run_id:
                raise InvalidTransition("only the active claimed run can record a checkpoint")
            event = self._insert_event(
                connection,
                EventDraft(
                    stream_type="thread",
                    stream_id=claim.thread_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    event_type="run.checkpoint_recorded",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "run_id": claim.run_id,
                        "checkpoint_id": checkpoint_id,
                        "checkpoint_sequence": run.checkpoint_sequence + 1,
                        "fencing_token": claim.fencing_token,
                    },
                ),
            )
            projection = reduce_thread(projection, event)
            self._upsert_thread_projection(connection, projection)
            return projection

    def record_delivery(
        self,
        thread_id: str,
        *,
        delivery_id: str,
        expected_revision: int,
        actor: str,
        artifact_ids: Iterable[str] = (),
    ) -> ThreadProjection:
        current = self.get_thread(thread_id)
        draft = EventDraft(
            stream_type="thread",
            stream_id=thread_id,
            project_id=current.project_id,
            thread_id=thread_id,
            branch_id=current.current_branch_id,
            run_id=current.active_run_id,
            event_type="delivery.recorded",
            actor=actor,
            occurred_at=to_timestamp(self._clock()),
            payload={"delivery_id": delivery_id, "artifact_ids": list(artifact_ids)},
        )
        return self.append_thread_event(draft, expected_revision=expected_revision)

    def get_thread(self, thread_id: str) -> ThreadProjection:
        with self._connect() as connection:
            projection = self._get_thread_in_transaction(connection, thread_id)
        if projection is None:
            raise NotFound(f"thread not found: {thread_id}")
        return projection

    def list_threads(
        self,
        *,
        project_id: str | None = None,
        attention_only: bool = False,
        limit: int = 100,
    ) -> list[ThreadProjection]:
        clauses: list[str] = []
        values: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            values.append(project_id)
        if attention_only:
            clauses.append("attention_state != ?")
            values.append(AttentionState.NONE.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT projection_json FROM thread_projections {where} "
                "ORDER BY updated_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [self._projection_from_json(str(row["projection_json"])) for row in rows]

    def list_events(
        self,
        *,
        thread_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
        after_position: int = 0,
        limit: int = 1000,
    ) -> list[EventEnvelope]:
        clauses = ["global_position > ?"]
        values: list[Any] = [after_position]
        if thread_id:
            clauses.append("thread_id = ?")
            values.append(thread_id)
        if run_id:
            clauses.append("run_id = ?")
            values.append(run_id)
        if correlation_id:
            clauses.append("correlation_id = ?")
            values.append(correlation_id)
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runtime_events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY global_position ASC LIMIT ?",
                values,
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def replay_thread(self, thread_id: str) -> ThreadProjection:
        events = [
            event
            for event in self.list_events(thread_id=thread_id, limit=1_000_000)
            if event.stream_type == "thread" and event.stream_id == thread_id
        ]
        if not events:
            raise NotFound(f"thread event stream not found: {thread_id}")
        projection: ThreadProjection | None = None
        for event in events:
            projection = reduce_thread(projection, event)
        assert projection is not None
        return projection

    def verify_thread_replay(self, thread_id: str) -> bool:
        stored = self.get_thread(thread_id)
        replayed = self.replay_thread(thread_id)
        return stored.projection_hash == replayed.projection_hash

    # ------------------------------------------------------------------
    # Durable scheduler with worker lease fencing
    # ------------------------------------------------------------------

    def enqueue_run(
        self,
        *,
        thread_id: str,
        run_id: str,
        priority: int = 0,
        available_at: datetime | None = None,
        max_attempts: int = 3,
        executor_key: str | None = None,
        actor: str = "scheduler",
    ) -> SchedulerJob:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        now = to_timestamp(self._clock())
        available = to_timestamp(available_at or self._clock())
        with self._transaction() as connection:
            projection = self._get_thread_in_transaction(connection, thread_id)
            if projection is None:
                raise NotFound(f"thread not found: {thread_id}")
            run_projection = projection.run(run_id)
            if run_projection is None:
                raise NotFound(f"run not found in thread {thread_id}: {run_id}")
            resolved_executor_key = executor_key or run_projection.executor_key
            if not resolved_executor_key.strip():
                raise ValueError("executor_key is required")
            existing = connection.execute(
                "SELECT * FROM scheduler_jobs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["thread_id"]) != thread_id
                    or str(existing["executor_key"]) != resolved_executor_key
                ):
                    raise IdempotencyConflict(
                        f"run id was already enqueued with different routing: {run_id}"
                    )
                return self._row_to_job(existing)
            connection.execute(
                """
                INSERT INTO scheduler_jobs(
                    run_id, thread_id, executor_key, state, priority, available_at, attempts,
                    max_attempts, fencing_token, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?)
                """,
                (
                    run_id,
                    thread_id,
                    resolved_executor_key,
                    JobState.QUEUED.value,
                    priority,
                    available,
                    max_attempts,
                    now,
                    now,
                ),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="scheduler-run",
                    stream_id=run_id,
                    project_id=projection.project_id,
                    thread_id=thread_id,
                    branch_id=run_projection.branch_id,
                    run_id=run_id,
                    event_type="scheduler.run_enqueued",
                    actor=actor,
                    occurred_at=now,
                    payload={
                        "priority": priority,
                        "available_at": available,
                        "max_attempts": max_attempts,
                        "executor_key": resolved_executor_key,
                    },
                ).normalized(),
            )
            row = connection.execute(
                "SELECT * FROM scheduler_jobs WHERE run_id = ?", (run_id,)
            ).fetchone()
            assert row is not None
            return self._row_to_job(row)

    def claim_next_run(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        executor_keys: Iterable[str] | None = None,
    ) -> WorkerClaim | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        now_dt = self._clock()
        now = to_timestamp(now_dt)
        expires_at = to_timestamp(now_dt + timedelta(seconds=lease_seconds))
        resolved_executor_keys = (
            tuple(dict.fromkeys(key.strip() for key in executor_keys if key.strip()))
            if executor_keys is not None
            else None
        )
        if resolved_executor_keys == ():
            return None
        executor_clause = ""
        executor_values: list[str] = []
        if resolved_executor_keys is not None:
            placeholders = ", ".join("?" for _ in resolved_executor_keys)
            executor_clause = f"AND candidate.executor_key IN ({placeholders})"
            executor_values.extend(resolved_executor_keys)
        with self._transaction() as connection:
            self._fail_exhausted_worker_claims(connection, now)
            row = connection.execute(
                f"""
                SELECT candidate.*
                FROM scheduler_jobs AS candidate
                WHERE (
                    (candidate.state = ? AND candidate.available_at <= ?)
                    OR
                    (candidate.state = ? AND candidate.lease_expires_at <= ?)
                )
                AND candidate.attempts < candidate.max_attempts
                {executor_clause}
                AND NOT EXISTS (
                    SELECT 1 FROM scheduler_jobs AS active
                    WHERE active.thread_id = candidate.thread_id
                      AND active.run_id != candidate.run_id
                      AND active.state = ?
                      AND active.lease_expires_at > ?
                )
                ORDER BY candidate.priority DESC, candidate.available_at ASC, candidate.created_at ASC
                LIMIT 1
                """,
                (
                    JobState.QUEUED.value,
                    now,
                    JobState.CLAIMED.value,
                    now,
                    *executor_values,
                    JobState.CLAIMED.value,
                    now,
                ),
            ).fetchone()
            if row is None:
                return None
            lease_id = uuid4().hex
            fencing_token = int(row["fencing_token"]) + 1
            attempt = int(row["attempts"]) + 1
            was_reclaimed = str(row["state"]) == JobState.CLAIMED.value
            connection.execute(
                """
                UPDATE scheduler_jobs
                SET state = ?, attempts = ?, lease_owner = ?, lease_id = ?, fencing_token = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    JobState.CLAIMED.value,
                    attempt,
                    worker_id,
                    lease_id,
                    fencing_token,
                    expires_at,
                    now,
                    row["run_id"],
                ),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="scheduler-run",
                    stream_id=str(row["run_id"]),
                    thread_id=str(row["thread_id"]),
                    run_id=str(row["run_id"]),
                    event_type=(
                        "scheduler.run_reclaimed" if was_reclaimed else "scheduler.run_claimed"
                    ),
                    actor=worker_id,
                    occurred_at=now,
                    payload={
                        "lease_id": lease_id,
                        "fencing_token": fencing_token,
                        "expires_at": expires_at,
                        "attempt": attempt,
                        "executor_key": str(row["executor_key"]),
                    },
                ).normalized(),
            )
            return WorkerClaim(
                run_id=str(row["run_id"]),
                thread_id=str(row["thread_id"]),
                worker_id=worker_id,
                lease_id=lease_id,
                fencing_token=fencing_token,
                expires_at=expires_at,
                attempt=attempt,
            )

    def start_claimed_run(self, claim: WorkerClaim) -> ThreadProjection:
        """Bind a live scheduler claim to the Thread/Run state in one transaction."""
        now = to_timestamp(self._clock())
        with self._transaction() as connection:
            self._assert_live_worker_claim(connection, claim, now)
            projection = self._get_thread_in_transaction(connection, claim.thread_id)
            if projection is None:
                raise NotFound(f"thread not found: {claim.thread_id}")
            run = projection.run(claim.run_id)
            if run is None:
                raise NotFound(f"run not found: {claim.run_id}")
            if not all(
                (
                    run.task_snapshot_id,
                    run.agent_snapshot_id,
                    run.context_manifest_id,
                )
            ):
                raise InvalidTransition("run snapshots must be bound before execution starts")
            if projection.desired_state != DesiredState.RUN:
                raise InvalidTransition(
                    f"thread desired state is {projection.desired_state}; run cannot start"
                )
            if run.state == RunState.RUNNING and projection.actual_state == ThreadState.RUNNING:
                return projection
            drafts: list[EventDraft] = []
            if projection.actual_state != ThreadState.RUNNING:
                drafts.append(
                    EventDraft(
                        stream_type="thread",
                        stream_id=claim.thread_id,
                        project_id=projection.project_id,
                        thread_id=claim.thread_id,
                        branch_id=run.branch_id,
                        run_id=claim.run_id,
                        event_type="thread.state_changed",
                        actor=claim.worker_id,
                        occurred_at=now,
                        payload={
                            "from": projection.actual_state.value,
                            "to": ThreadState.RUNNING.value,
                        },
                    )
                )
            if run.state != RunState.RUNNING:
                drafts.append(
                    EventDraft(
                        stream_type="thread",
                        stream_id=claim.thread_id,
                        project_id=projection.project_id,
                        thread_id=claim.thread_id,
                        branch_id=run.branch_id,
                        run_id=claim.run_id,
                        event_type="run.state_changed",
                        actor=claim.worker_id,
                        occurred_at=now,
                        payload={
                            "run_id": claim.run_id,
                            "from": run.state.value,
                            "to": RunState.RUNNING.value,
                        },
                    )
                )
            for draft in drafts:
                event = self._insert_event(connection, draft)
                projection = reduce_thread(projection, event)
            self._upsert_thread_projection(connection, projection)
            return projection

    def heartbeat_run(self, claim: WorkerClaim, *, lease_seconds: int = 30) -> WorkerClaim:
        now_dt = self._clock()
        now = to_timestamp(now_dt)
        expires_at = to_timestamp(now_dt + timedelta(seconds=lease_seconds))
        with self._transaction() as connection:
            row = self._assert_live_worker_claim(connection, claim, now)
            connection.execute(
                "UPDATE scheduler_jobs SET lease_expires_at = ?, updated_at = ? WHERE run_id = ?",
                (expires_at, now, claim.run_id),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="scheduler-run",
                    stream_id=claim.run_id,
                    thread_id=claim.thread_id,
                    run_id=claim.run_id,
                    event_type="scheduler.run_heartbeat",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "lease_id": claim.lease_id,
                        "fencing_token": claim.fencing_token,
                        "expires_at": expires_at,
                    },
                ).normalized(),
            )
            return WorkerClaim(
                run_id=claim.run_id,
                thread_id=claim.thread_id,
                worker_id=claim.worker_id,
                lease_id=claim.lease_id,
                fencing_token=claim.fencing_token,
                expires_at=expires_at,
                attempt=int(row["attempts"]),
            )

    def finish_run_claim(
        self,
        claim: WorkerClaim,
        *,
        succeeded: bool,
        error: str | None = None,
        retryable: bool = True,
        retry_delay_seconds: int = 0,
    ) -> SchedulerJob:
        now_dt = self._clock()
        now = to_timestamp(now_dt)
        with self._transaction() as connection:
            row = self._assert_live_worker_claim(connection, claim, now)
            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])
            if succeeded:
                state = JobState.COMPLETED
                event_type = "scheduler.run_completed"
                available_at = str(row["available_at"])
            elif retryable and attempts < max_attempts:
                state = JobState.QUEUED
                event_type = "scheduler.run_retry_scheduled"
                available_at = to_timestamp(now_dt + timedelta(seconds=max(0, retry_delay_seconds)))
            else:
                state = JobState.FAILED
                event_type = "scheduler.run_failed"
                available_at = str(row["available_at"])
            connection.execute(
                """
                UPDATE scheduler_jobs
                SET state = ?, available_at = ?, lease_owner = NULL, lease_id = NULL,
                    lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (state.value, available_at, error, now, claim.run_id),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="scheduler-run",
                    stream_id=claim.run_id,
                    thread_id=claim.thread_id,
                    run_id=claim.run_id,
                    event_type=event_type,
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "lease_id": claim.lease_id,
                        "fencing_token": claim.fencing_token,
                        "attempt": attempts,
                        "error": error,
                        "available_at": available_at,
                    },
                ).normalized(),
            )
            updated = connection.execute(
                "SELECT * FROM scheduler_jobs WHERE run_id = ?", (claim.run_id,)
            ).fetchone()
            assert updated is not None
            return self._row_to_job(updated)

    def finish_claimed_execution(
        self,
        claim: WorkerClaim,
        *,
        executor_key: str,
        executor_version: str,
        succeeded: bool,
        output_artifact_id: str | None = None,
        error_code: str | None = None,
        error: str | None = None,
        retryable: bool = True,
        retry_delay_seconds: int = 0,
    ) -> tuple[SchedulerJob, ThreadProjection]:
        """Fence and atomically settle the scheduler, Run, and Thread execution subset.

        Delivery, Attention-on-success, and Outbox finalization intentionally remain
        outside this V2-004 subset and are joined by V2-006. A successful Artifact
        therefore returns the Thread to DORMANT rather than claiming it was delivered.
        """

        executor_key = executor_key.strip()
        executor_version = executor_version.strip()
        if not executor_key or not executor_version:
            raise ValueError("executor_key and executor_version are required")
        if succeeded:
            if output_artifact_id is None or not output_artifact_id.startswith("artifact:sha256:"):
                raise ValueError("a successful execution requires an output Artifact reference")
            if error_code is not None or error is not None:
                raise ValueError("a successful execution cannot include an error")
        elif not error_code or not error:
            raise ValueError("a failed execution requires an error code and message")

        now_dt = self._clock()
        now = to_timestamp(now_dt)
        with self._transaction() as connection:
            row = self._assert_live_worker_claim(connection, claim, now)
            projection = self._get_thread_in_transaction(connection, claim.thread_id)
            if projection is None:
                raise NotFound(f"thread not found: {claim.thread_id}")
            run = projection.run(claim.run_id)
            if run is None:
                raise NotFound(f"run not found: {claim.run_id}")
            if run.state != RunState.RUNNING or projection.active_run_id != claim.run_id:
                raise InvalidTransition("only the active claimed Run may finish execution")
            if run.executor_key != executor_key or str(row["executor_key"]) != executor_key:
                raise InvalidTransition(
                    f"claimed Run requires executor {run.executor_key}; got {executor_key}"
                )

            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])
            terminal_failure = not succeeded and (not retryable or attempts >= max_attempts)
            if succeeded:
                job_state = JobState.COMPLETED
                run_target = RunState.COMPLETED
                thread_target = ThreadState.DORMANT
                scheduler_event_type = "scheduler.run_completed"
                available_at = str(row["available_at"])
                last_error = None
            elif terminal_failure:
                job_state = JobState.FAILED
                run_target = RunState.FAILED
                thread_target = ThreadState.FAILED
                scheduler_event_type = "scheduler.run_failed"
                available_at = str(row["available_at"])
                last_error = f"{error_code}: {error}"
            else:
                job_state = JobState.QUEUED
                run_target = RunState.QUEUED
                thread_target = ThreadState.QUEUED
                scheduler_event_type = "scheduler.run_retry_scheduled"
                available_at = to_timestamp(now_dt + timedelta(seconds=max(0, retry_delay_seconds)))
                last_error = f"{error_code}: {error}"

            correlation_id = f"worker-claim:{claim.lease_id}"
            run_event = self._insert_event(
                connection,
                EventDraft(
                    stream_type="thread",
                    stream_id=claim.thread_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    correlation_id=correlation_id,
                    event_type="run.state_changed",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "run_id": claim.run_id,
                        "from": run.state.value,
                        "to": run_target.value,
                        "output_artifact_id": output_artifact_id,
                        "error": error,
                        "error_code": error_code,
                        "executor_key": executor_key,
                        "executor_version": executor_version,
                        "fencing_token": claim.fencing_token,
                    },
                ),
            )
            projection = reduce_thread(projection, run_event)
            thread_event = self._insert_event(
                connection,
                EventDraft(
                    stream_type="thread",
                    stream_id=claim.thread_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    correlation_id=correlation_id,
                    causation_id=run_event.event_id,
                    event_type="thread.state_changed",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "from": projection.actual_state.value,
                        "to": thread_target.value,
                        "reason": (
                            "artifact_ready_pending_delivery"
                            if succeeded
                            else "executor_failed"
                            if terminal_failure
                            else "executor_retry_scheduled"
                        ),
                    },
                ),
            )
            projection = reduce_thread(projection, thread_event)
            last_cause_id = thread_event.event_id
            if terminal_failure and projection.attention_state != AttentionState.FAILED:
                attention_event = self._insert_event(
                    connection,
                    EventDraft(
                        stream_type="thread",
                        stream_id=claim.thread_id,
                        project_id=projection.project_id,
                        thread_id=claim.thread_id,
                        branch_id=run.branch_id,
                        run_id=claim.run_id,
                        correlation_id=correlation_id,
                        causation_id=thread_event.event_id,
                        event_type="thread.attention_changed",
                        actor=claim.worker_id,
                        occurred_at=now,
                        payload={
                            "from": projection.attention_state.value,
                            "to": AttentionState.FAILED.value,
                            "reason": error_code,
                        },
                    ),
                )
                projection = reduce_thread(projection, attention_event)
                last_cause_id = attention_event.event_id
            self._upsert_thread_projection(connection, projection)

            connection.execute(
                """
                UPDATE scheduler_jobs
                SET state = ?, available_at = ?, lease_owner = NULL, lease_id = NULL,
                    lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (job_state.value, available_at, last_error, now, claim.run_id),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="scheduler-run",
                    stream_id=claim.run_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    correlation_id=correlation_id,
                    causation_id=last_cause_id,
                    event_type=scheduler_event_type,
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "lease_id": claim.lease_id,
                        "fencing_token": claim.fencing_token,
                        "attempt": attempts,
                        "executor_key": executor_key,
                        "executor_version": executor_version,
                        "output_artifact_id": output_artifact_id,
                        "error_code": error_code,
                        "error": error,
                        "available_at": available_at,
                    },
                ).normalized(),
            )
            updated = connection.execute(
                "SELECT * FROM scheduler_jobs WHERE run_id = ?", (claim.run_id,)
            ).fetchone()
            assert updated is not None
            return self._row_to_job(updated), projection

    def begin_claimed_step(
        self,
        claim: WorkerClaim,
        *,
        step_id: str,
        ordinal: int,
        kind: StepKind,
        provider_key: str,
        provider_version: str,
        input_artifact_ids: Iterable[str] = (),
        budget: dict[str, Any] | None = None,
        config_snapshot_id: str | None = None,
    ) -> StepRecord:
        """Create and start one replay-visible Step under a live Worker claim."""

        step_id = step_id.strip()
        provider_key = provider_key.strip()
        provider_version = provider_version.strip()
        inputs = tuple(input_artifact_ids)
        if not step_id or not provider_key or not provider_version:
            raise ValueError("step_id, provider_key, and provider_version are required")
        if ordinal < 1:
            raise ValueError("step ordinal must be at least 1")
        if any(not value.startswith("artifact:sha256:") for value in inputs):
            raise ValueError("Step inputs must be Artifact CAS references")
        now = to_timestamp(self._clock())
        with self._transaction() as connection:
            self._assert_live_worker_claim(connection, claim, now)
            existing = connection.execute(
                "SELECT * FROM runtime_steps WHERE step_id = ?", (step_id,)
            ).fetchone()
            if existing is not None:
                record = self._row_to_step(existing)
                expected = (
                    claim.thread_id,
                    claim.run_id,
                    ordinal,
                    kind,
                    claim.attempt,
                    provider_key,
                    provider_version,
                    inputs,
                )
                actual = (
                    record.thread_id,
                    record.run_id,
                    record.ordinal,
                    record.kind,
                    record.attempt,
                    record.provider_key,
                    record.provider_version,
                    record.input_artifact_ids,
                )
                if actual != expected:
                    raise IdempotencyConflict(f"step id was reused: {step_id}")
                return record
            projection = self._get_thread_in_transaction(connection, claim.thread_id)
            if projection is None:
                raise NotFound(f"thread not found: {claim.thread_id}")
            run = projection.run(claim.run_id)
            if run is None or run.state != RunState.RUNNING:
                raise InvalidTransition("Steps require the active running Run")
            payload = {
                "step_id": step_id,
                "ordinal": ordinal,
                "kind": kind.value,
                "attempt": claim.attempt,
                "provider_key": provider_key,
                "provider_version": provider_version,
                "input_artifact_ids": list(inputs),
                "budget": dict(budget or {}),
                "config_snapshot_id": config_snapshot_id,
                "fencing_token": claim.fencing_token,
            }
            created = self._insert_event(
                connection,
                EventDraft(
                    stream_type="run-step",
                    stream_id=step_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    event_type="step.created",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload=payload,
                ),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="run-step",
                    stream_id=step_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    causation_id=created.event_id,
                    event_type="step.started",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={"step_id": step_id, "fencing_token": claim.fencing_token},
                ),
            )
            connection.execute(
                """
                INSERT INTO runtime_steps(
                    step_id, thread_id, run_id, ordinal, kind, state, attempt,
                    provider_key, provider_version, input_artifact_ids_json,
                    output_artifact_ids_json, budget_json, config_snapshot_id,
                    created_at, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?)
                """,
                (
                    step_id,
                    claim.thread_id,
                    claim.run_id,
                    ordinal,
                    kind.value,
                    StepState.RUNNING.value,
                    claim.attempt,
                    provider_key,
                    provider_version,
                    canonical_json(inputs),
                    canonical_json(budget or {}),
                    config_snapshot_id,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM runtime_steps WHERE step_id = ?", (step_id,)
            ).fetchone()
            assert row is not None
            return self._row_to_step(row)

    def finish_claimed_step(
        self,
        claim: WorkerClaim,
        *,
        step_id: str,
        succeeded: bool,
        output_artifact_ids: Iterable[str] = (),
        verifier_result_artifact_id: str | None = None,
        error_code: str | None = None,
        error: str | None = None,
    ) -> StepRecord:
        """Finish one Step while preserving the Worker lease fence."""

        outputs = tuple(output_artifact_ids)
        if any(not value.startswith("artifact:sha256:") for value in outputs):
            raise ValueError("Step outputs must be Artifact CAS references")
        if verifier_result_artifact_id is not None and not verifier_result_artifact_id.startswith(
            "artifact:sha256:"
        ):
            raise ValueError("verifier result must be an Artifact CAS reference")
        if succeeded and (error_code is not None or error is not None):
            raise ValueError("a successful Step cannot include an error")
        if not succeeded and (not error_code or not error):
            raise ValueError("a failed Step requires an error code and message")
        now = to_timestamp(self._clock())
        with self._transaction() as connection:
            self._assert_live_worker_claim(connection, claim, now)
            row = connection.execute(
                "SELECT * FROM runtime_steps WHERE step_id = ?", (step_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"step not found: {step_id}")
            record = self._row_to_step(row)
            if record.thread_id != claim.thread_id or record.run_id != claim.run_id:
                raise InvalidTransition("Step does not belong to the claimed Run")
            if record.state != StepState.RUNNING:
                raise InvalidTransition("only a running Step may finish")
            projection = self._get_thread_in_transaction(connection, claim.thread_id)
            if projection is None:
                raise NotFound(f"thread not found: {claim.thread_id}")
            run = projection.run(claim.run_id)
            if run is None:
                raise NotFound(f"run not found: {claim.run_id}")
            last_cause_id: str | None = None
            if outputs:
                output_event = self._insert_event(
                    connection,
                    EventDraft(
                        stream_type="run-step",
                        stream_id=step_id,
                        project_id=projection.project_id,
                        thread_id=claim.thread_id,
                        branch_id=run.branch_id,
                        run_id=claim.run_id,
                        event_type="step.output_recorded",
                        actor=claim.worker_id,
                        occurred_at=now,
                        payload={
                            "step_id": step_id,
                            "output_artifact_ids": list(outputs),
                            "verifier_result_artifact_id": verifier_result_artifact_id,
                        },
                    ),
                )
                last_cause_id = output_event.event_id
            target = StepState.SUCCEEDED if succeeded else StepState.FAILED
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="run-step",
                    stream_id=step_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    causation_id=last_cause_id,
                    event_type="step.succeeded" if succeeded else "step.failed",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "step_id": step_id,
                        "error_code": error_code,
                        "error": error,
                        "fencing_token": claim.fencing_token,
                    },
                ),
            )
            connection.execute(
                """
                UPDATE runtime_steps
                SET state = ?, output_artifact_ids_json = ?,
                    verifier_result_artifact_id = ?, error_code = ?, error = ?, completed_at = ?
                WHERE step_id = ?
                """,
                (
                    target.value,
                    canonical_json(outputs),
                    verifier_result_artifact_id,
                    error_code,
                    error,
                    now,
                    step_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM runtime_steps WHERE step_id = ?", (step_id,)
            ).fetchone()
            assert updated is not None
            return self._row_to_step(updated)

    def finalize_claimed_delivery(
        self,
        claim: WorkerClaim,
        *,
        executor_key: str,
        executor_version: str,
        primary_artifact_id: str,
        verifier_result_artifact_id: str,
        summary_artifact_id: str | None = None,
        supporting_artifact_ids: Iterable[str] = (),
        source_refs: Iterable[str] = (),
        sensitivity: str = "normal",
        export_policy: str = "local_only",
        notification_channel: str = "local",
        notification_destination: str | None = None,
    ) -> tuple[SchedulerJob, ThreadProjection, DeliveryRecord, OutboxItem]:
        """Atomically close scheduler, Run, Thread, Delivery, Attention, and Outbox."""

        summary_artifact_id = summary_artifact_id or primary_artifact_id
        supporting = tuple(dict.fromkeys(supporting_artifact_ids))
        source_refs = tuple(dict.fromkeys(value.strip() for value in source_refs if value.strip()))
        artifact_ids = (
            primary_artifact_id,
            summary_artifact_id,
            verifier_result_artifact_id,
            *supporting,
        )
        if any(not value.startswith("artifact:sha256:") for value in artifact_ids):
            raise ValueError("Delivery fields must contain Artifact CAS references")
        now = to_timestamp(self._clock())
        with self._transaction() as connection:
            job_row = self._assert_live_worker_claim(connection, claim, now)
            projection = self._get_thread_in_transaction(connection, claim.thread_id)
            if projection is None:
                raise NotFound(f"thread not found: {claim.thread_id}")
            run = projection.run(claim.run_id)
            if run is None:
                raise NotFound(f"run not found: {claim.run_id}")
            if run.state != RunState.RUNNING or projection.active_run_id != claim.run_id:
                raise InvalidTransition("only the active claimed Run may prepare a Delivery")
            if run.executor_key != executor_key or str(job_row["executor_key"]) != executor_key:
                raise InvalidTransition("Delivery executor does not match the claimed Run")
            prior = connection.execute(
                "SELECT * FROM deliveries WHERE thread_id = ? ORDER BY version DESC LIMIT 1",
                (claim.thread_id,),
            ).fetchone()
            version = int(prior["version"]) + 1 if prior is not None else 1
            previous_delivery_id = str(prior["delivery_id"]) if prior is not None else None
            delivery_id = uuid5(
                NAMESPACE_URL,
                f"copenguin:{claim.thread_id}:{claim.run_id}:delivery:{version}",
            ).hex
            allowed_decisions = ("accept", "revise", "reject", "defer", "take_over")
            delivery_event = self._insert_event(
                connection,
                EventDraft(
                    stream_type="delivery",
                    stream_id=delivery_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    correlation_id=f"worker-claim:{claim.lease_id}",
                    event_type="delivery.prepared",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "delivery_id": delivery_id,
                        "version": version,
                        "primary_artifact_id": primary_artifact_id,
                        "summary_artifact_id": summary_artifact_id,
                        "supporting_artifact_ids": list(supporting),
                        "verifier_result_artifact_id": verifier_result_artifact_id,
                        "source_refs": list(source_refs),
                        "previous_delivery_id": previous_delivery_id,
                        "allowed_decisions": list(allowed_decisions),
                        "sensitivity": sensitivity,
                        "export_policy": export_policy,
                    },
                ),
            )
            recorded = self._insert_event(
                connection,
                EventDraft(
                    stream_type="thread",
                    stream_id=claim.thread_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    correlation_id=f"worker-claim:{claim.lease_id}",
                    causation_id=delivery_event.event_id,
                    event_type="delivery.recorded",
                    actor=claim.worker_id,
                    occurred_at=now,
                    schema_version=2,
                    payload={
                        "delivery_id": delivery_id,
                        "artifact_ids": [primary_artifact_id, *supporting],
                    },
                ),
            )
            projection = reduce_thread(projection, recorded)
            run_event = self._insert_event(
                connection,
                EventDraft(
                    stream_type="thread",
                    stream_id=claim.thread_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    correlation_id=f"worker-claim:{claim.lease_id}",
                    causation_id=recorded.event_id,
                    event_type="run.state_changed",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "run_id": claim.run_id,
                        "from": RunState.RUNNING.value,
                        "to": RunState.COMPLETED.value,
                        "output_artifact_id": primary_artifact_id,
                        "executor_key": executor_key,
                        "executor_version": executor_version,
                        "verifier_result_artifact_id": verifier_result_artifact_id,
                        "fencing_token": claim.fencing_token,
                    },
                ),
            )
            projection = reduce_thread(projection, run_event)
            thread_event = self._insert_event(
                connection,
                EventDraft(
                    stream_type="thread",
                    stream_id=claim.thread_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    correlation_id=f"worker-claim:{claim.lease_id}",
                    causation_id=run_event.event_id,
                    event_type="thread.state_changed",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "from": projection.actual_state.value,
                        "to": ThreadState.DELIVERED.value,
                        "reason": "verified_delivery_prepared",
                    },
                ),
            )
            projection = reduce_thread(projection, thread_event)
            attention_event = self._insert_event(
                connection,
                EventDraft(
                    stream_type="thread",
                    stream_id=claim.thread_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    correlation_id=f"worker-claim:{claim.lease_id}",
                    causation_id=thread_event.event_id,
                    event_type="thread.attention_changed",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "from": projection.attention_state.value,
                        "to": AttentionState.DELIVERY_READY.value,
                        "reason": "delivery_requires_owner_decision",
                    },
                ),
            )
            projection = reduce_thread(projection, attention_event)
            self._upsert_thread_projection(connection, projection)
            connection.execute(
                """
                INSERT INTO deliveries(
                    delivery_id, thread_id, run_id, version, state, summary_artifact_id,
                    primary_artifact_id, supporting_artifact_ids_json, source_refs_json,
                    verifier_result_artifact_id, previous_delivery_id,
                    allowed_decisions_json, sensitivity, export_policy, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery_id,
                    claim.thread_id,
                    claim.run_id,
                    version,
                    DeliveryState.PREPARED.value,
                    summary_artifact_id,
                    primary_artifact_id,
                    canonical_json(supporting),
                    canonical_json(source_refs),
                    verifier_result_artifact_id,
                    previous_delivery_id,
                    canonical_json(allowed_decisions),
                    sensitivity,
                    export_policy,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE scheduler_jobs
                SET state = ?, lease_owner = NULL, lease_id = NULL,
                    lease_expires_at = NULL, last_error = NULL, updated_at = ?
                WHERE run_id = ?
                """,
                (JobState.COMPLETED.value, now, claim.run_id),
            )
            scheduler_event = self._insert_event(
                connection,
                EventDraft(
                    stream_type="scheduler-run",
                    stream_id=claim.run_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    correlation_id=f"worker-claim:{claim.lease_id}",
                    causation_id=attention_event.event_id,
                    event_type="scheduler.run_completed",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "lease_id": claim.lease_id,
                        "fencing_token": claim.fencing_token,
                        "attempt": int(job_row["attempts"]),
                        "executor_key": executor_key,
                        "executor_version": executor_version,
                        "output_artifact_id": primary_artifact_id,
                        "delivery_id": delivery_id,
                    },
                ),
            )
            outbox_id = uuid5(NAMESPACE_URL, f"copenguin:{delivery_id}:delivery-ready").hex
            destination = notification_destination or f"thread:{claim.thread_id}"
            idempotency_key = f"delivery:{delivery_id}:ready:v1"
            connection.execute(
                """
                INSERT INTO runtime_outbox(
                    outbox_id, idempotency_key, thread_id, run_id, kind, channel,
                    destination, payload_artifact_id, state, attempts, available_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    outbox_id,
                    idempotency_key,
                    claim.thread_id,
                    claim.run_id,
                    "delivery_ready",
                    notification_channel,
                    destination,
                    summary_artifact_id,
                    OutboxState.PENDING.value,
                    now,
                    now,
                    now,
                ),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="delivery",
                    stream_id=delivery_id,
                    project_id=projection.project_id,
                    thread_id=claim.thread_id,
                    branch_id=run.branch_id,
                    run_id=claim.run_id,
                    causation_id=scheduler_event.event_id,
                    event_type="delivery.notification_enqueued",
                    actor="outbox",
                    occurred_at=now,
                    payload={
                        "delivery_id": delivery_id,
                        "outbox_id": outbox_id,
                        "idempotency_key": idempotency_key,
                        "channel": notification_channel,
                        "destination": destination,
                    },
                ),
            )
            job = connection.execute(
                "SELECT * FROM scheduler_jobs WHERE run_id = ?", (claim.run_id,)
            ).fetchone()
            delivery = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            outbox = connection.execute(
                "SELECT * FROM runtime_outbox WHERE outbox_id = ?", (outbox_id,)
            ).fetchone()
            assert job is not None and delivery is not None and outbox is not None
            return (
                self._row_to_job(job),
                projection,
                self._row_to_delivery(delivery),
                self._row_to_outbox(outbox),
            )

    def get_step(self, step_id: str) -> StepRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_steps WHERE step_id = ?", (step_id,)
            ).fetchone()
        if row is None:
            raise NotFound(f"step not found: {step_id}")
        return self._row_to_step(row)

    def list_steps(self, *, run_id: str, limit: int = 100) -> list[StepRecord]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runtime_steps WHERE run_id = ?
                ORDER BY ordinal ASC, attempt ASC LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return [self._row_to_step(row) for row in rows]

    def get_delivery(self, delivery_id: str) -> DeliveryRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
        if row is None:
            raise NotFound(f"delivery not found: {delivery_id}")
        return self._row_to_delivery(row)

    def present_delivery(self, delivery_id: str, *, actor: str) -> DeliveryRecord:
        """Record that a prepared Delivery crossed into an owner-visible surface."""

        actor = actor.strip()
        if not actor:
            raise ValueError("actor is required")
        now = to_timestamp(self._clock())
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"delivery not found: {delivery_id}")
            delivery = self._row_to_delivery(row)
            if delivery.state == DeliveryState.PRESENTED:
                return delivery
            if delivery.state != DeliveryState.PREPARED:
                raise InvalidTransition("only a prepared Delivery may be presented")
            projection = self._get_thread_in_transaction(connection, delivery.thread_id)
            if projection is None:
                raise NotFound(f"thread not found: {delivery.thread_id}")
            run = projection.run(delivery.run_id)
            if run is None:
                raise NotFound(f"run not found: {delivery.run_id}")
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="delivery",
                    stream_id=delivery.delivery_id,
                    project_id=projection.project_id,
                    thread_id=delivery.thread_id,
                    branch_id=run.branch_id,
                    run_id=delivery.run_id,
                    event_type="delivery.presented",
                    actor=actor,
                    occurred_at=now,
                    payload={"delivery_id": delivery.delivery_id},
                ),
            )
            connection.execute(
                "UPDATE deliveries SET state = ?, presented_at = ? WHERE delivery_id = ?",
                (DeliveryState.PRESENTED.value, now, delivery.delivery_id),
            )
            updated = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery.delivery_id,)
            ).fetchone()
            assert updated is not None
            return self._row_to_delivery(updated)

    def list_deliveries(
        self,
        *,
        thread_id: str | None = None,
        state: DeliveryState | None = None,
        limit: int = 100,
    ) -> list[DeliveryRecord]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        clauses: list[str] = []
        values: list[Any] = []
        if thread_id is not None:
            clauses.append("thread_id = ?")
            values.append(thread_id)
        if state is not None:
            clauses.append("state = ?")
            values.append(state.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM deliveries {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
                values,
            ).fetchall()
        return [self._row_to_delivery(row) for row in rows]

    def list_outbox(
        self,
        *,
        state: OutboxState | None = None,
        limit: int = 100,
    ) -> list[OutboxItem]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if state is None:
            query = "SELECT * FROM runtime_outbox ORDER BY created_at DESC LIMIT ?"
            values: tuple[Any, ...] = (limit,)
        else:
            query = "SELECT * FROM runtime_outbox WHERE state = ? ORDER BY created_at DESC LIMIT ?"
            values = (state.value, limit)
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._row_to_outbox(row) for row in rows]

    def get_job(self, run_id: str) -> SchedulerJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scheduler_jobs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise NotFound(f"scheduler job not found: {run_id}")
        return self._row_to_job(row)

    def list_jobs(
        self,
        *,
        state: JobState | None = None,
        executor_key: str | None = None,
        limit: int = 100,
    ) -> list[SchedulerJob]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        clauses: list[str] = []
        values: list[Any] = []
        if state is not None:
            clauses.append("state = ?")
            values.append(state.value)
        if executor_key is not None:
            clauses.append("executor_key = ?")
            values.append(executor_key.strip())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM scheduler_jobs {where}
                ORDER BY updated_at DESC, priority DESC, run_id ASC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    # ------------------------------------------------------------------
    # Cross-thread resource leases
    # ------------------------------------------------------------------

    def acquire_resource(
        self,
        *,
        resource_key: str,
        mode: AccessMode,
        owner_run_id: str,
        thread_id: str,
        lease_seconds: int = 30,
        actor: str = "resource-coordinator",
    ) -> ResourceLease:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        now_dt = self._clock()
        now = to_timestamp(now_dt)
        expires_at = to_timestamp(now_dt + timedelta(seconds=lease_seconds))
        with self._transaction() as connection:
            self._expire_resource_leases(connection, resource_key, now)
            active = connection.execute(
                """
                SELECT * FROM resource_leases
                WHERE resource_key = ? AND status = 'active' AND expires_at > ?
                ORDER BY fencing_token
                """,
                (resource_key, now),
            ).fetchall()
            conflicts = [
                row
                for row in active
                if mode != AccessMode.READ or str(row["mode"]) != AccessMode.READ.value
            ]
            if conflicts:
                owners = ", ".join(str(row["owner_run_id"]) for row in conflicts)
                raise ResourceConflict(
                    f"resource {resource_key!r} is held by incompatible run(s): {owners}"
                )

            fence = connection.execute(
                "SELECT last_token FROM resource_fences WHERE resource_key = ?",
                (resource_key,),
            ).fetchone()
            fencing_token = (int(fence["last_token"]) if fence else 0) + 1
            connection.execute(
                """
                INSERT INTO resource_fences(resource_key, last_token) VALUES (?, ?)
                ON CONFLICT(resource_key) DO UPDATE SET last_token = excluded.last_token
                """,
                (resource_key, fencing_token),
            )
            lease_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO resource_leases(
                    lease_id, resource_key, mode, owner_run_id, thread_id,
                    fencing_token, acquired_at, expires_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    lease_id,
                    resource_key,
                    mode.value,
                    owner_run_id,
                    thread_id,
                    fencing_token,
                    now,
                    expires_at,
                ),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="resource",
                    stream_id=resource_key,
                    thread_id=thread_id,
                    run_id=owner_run_id,
                    event_type="resource.lease_acquired",
                    actor=actor,
                    occurred_at=now,
                    payload={
                        "lease_id": lease_id,
                        "mode": mode.value,
                        "fencing_token": fencing_token,
                        "expires_at": expires_at,
                    },
                ).normalized(),
            )
            return ResourceLease(
                lease_id=lease_id,
                resource_key=resource_key,
                mode=mode,
                owner_run_id=owner_run_id,
                thread_id=thread_id,
                fencing_token=fencing_token,
                acquired_at=now,
                expires_at=expires_at,
            )

    def renew_resource(self, lease: ResourceLease, *, lease_seconds: int = 30) -> ResourceLease:
        now_dt = self._clock()
        now = to_timestamp(now_dt)
        expires_at = to_timestamp(now_dt + timedelta(seconds=lease_seconds))
        with self._transaction() as connection:
            row = self._assert_live_resource_lease(connection, lease, now)
            connection.execute(
                "UPDATE resource_leases SET expires_at = ? WHERE lease_id = ?",
                (expires_at, lease.lease_id),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="resource",
                    stream_id=lease.resource_key,
                    thread_id=lease.thread_id,
                    run_id=lease.owner_run_id,
                    event_type="resource.lease_renewed",
                    actor=lease.owner_run_id,
                    occurred_at=now,
                    payload={
                        "lease_id": lease.lease_id,
                        "fencing_token": lease.fencing_token,
                        "expires_at": expires_at,
                    },
                ).normalized(),
            )
            return ResourceLease(
                lease_id=lease.lease_id,
                resource_key=lease.resource_key,
                mode=AccessMode(str(row["mode"])),
                owner_run_id=lease.owner_run_id,
                thread_id=lease.thread_id,
                fencing_token=lease.fencing_token,
                acquired_at=str(row["acquired_at"]),
                expires_at=expires_at,
            )

    def release_resource(self, lease: ResourceLease) -> None:
        now = to_timestamp(self._clock())
        with self._transaction() as connection:
            self._assert_live_resource_lease(connection, lease, now)
            connection.execute(
                "UPDATE resource_leases SET status = 'released', released_at = ? WHERE lease_id = ?",
                (now, lease.lease_id),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="resource",
                    stream_id=lease.resource_key,
                    thread_id=lease.thread_id,
                    run_id=lease.owner_run_id,
                    event_type="resource.lease_released",
                    actor=lease.owner_run_id,
                    occurred_at=now,
                    payload={
                        "lease_id": lease.lease_id,
                        "fencing_token": lease.fencing_token,
                    },
                ).normalized(),
            )

    def active_resource_leases(self, resource_key: str) -> list[ResourceLease]:
        now = to_timestamp(self._clock())
        with self._transaction() as connection:
            self._expire_resource_leases(connection, resource_key, now)
            rows = connection.execute(
                """
                SELECT * FROM resource_leases
                WHERE resource_key = ? AND status = 'active' AND expires_at > ?
                ORDER BY fencing_token
                """,
                (resource_key, now),
            ).fetchall()
        return [self._row_to_resource_lease(row) for row in rows]

    # ------------------------------------------------------------------
    # Unified Inbox routing journal
    # ------------------------------------------------------------------

    def find_inbox_record(self, message_key: str) -> InboxRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM inbox_messages WHERE message_key = ?",
                (message_key,),
            ).fetchone()
        return self._row_to_inbox_record(row) if row is not None else None

    def accept_inbox_route(
        self,
        record: InboxRecord,
        *,
        task_submission: TaskSubmission | None = None,
        thread_update_submission: ThreadUpdateSubmission | None = None,
    ) -> tuple[InboxRecord, bool]:
        """Atomically persist one inbound route and its confirmed durable effect."""
        if not record.text_artifact_id.startswith("artifact:sha256:"):
            raise ValueError("text_artifact_id must be an Artifact CAS reference")
        if not record.message_artifact_id.startswith("artifact:sha256:"):
            raise ValueError("message_artifact_id must be an Artifact CAS reference")
        if task_submission is not None:
            if record.route_type != InboxRouteType.NEW_TASK:
                raise ValueError("only a new-task route may include a Task submission")
            if record.route_state != InboxRouteState.CONFIRMED:
                raise ValueError("a Task submission requires a confirmed route")
            if record.thread_id != task_submission.thread_id:
                raise ValueError("inbox route and Task submission must target the same thread")
            if record.project_id != task_submission.project_id:
                raise ValueError("inbox route and Task submission must target the same project")
            if task_submission.correlation_id != record.message_key:
                raise ValueError("Task submission correlation must use the inbound message key")
        if thread_update_submission is not None:
            if record.route_type != InboxRouteType.THREAD_UPDATE:
                raise ValueError("only a thread-update route may include a Thread update")
            if record.route_state not in {
                InboxRouteState.CONFIRMED,
                InboxRouteState.CORRECTED,
            }:
                raise ValueError("a Thread update requires a confirmed or corrected route")
            if record.thread_id != thread_update_submission.thread_id:
                raise ValueError("inbox route and Thread update must target the same thread")
            if record.project_id != thread_update_submission.project_id:
                raise ValueError("inbox route and Thread update must target the same project")
            if record.message_key != thread_update_submission.message_key:
                raise ValueError("Thread update correlation must use the inbound message key")
            if record.update_kind != thread_update_submission.update_kind:
                raise ValueError("inbox route and Thread update kind must match")
        if task_submission is not None and thread_update_submission is not None:
            raise ValueError("one inbound route cannot create a Task and update a Thread")

        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM inbox_messages WHERE message_key = ?",
                (record.message_key,),
            ).fetchone()
            if existing is not None:
                stored = self._row_to_inbox_record(existing)
                self._assert_same_inbound(record, stored)
                return stored, False

            connection.execute(
                """
                INSERT INTO inbox_messages(
                    message_key, payload_hash, platform, message_id, chat_id, actor_id,
                    project_id, message_artifact_id, text_artifact_id, route_type,
                    route_state, thread_id,
                    confidence, rationale, domain, requires_confirmation, created_at,
                    update_kind, candidate_thread_ids_json, updated_at,
                    resolved_by, resolution_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.message_key,
                    record.payload_hash,
                    record.platform,
                    record.message_id,
                    record.chat_id,
                    record.actor_id,
                    record.project_id,
                    record.message_artifact_id,
                    record.text_artifact_id,
                    record.route_type.value,
                    record.route_state.value,
                    record.thread_id,
                    record.confidence,
                    record.rationale,
                    record.domain,
                    int(record.requires_confirmation),
                    record.created_at,
                    record.update_kind.value if record.update_kind is not None else None,
                    canonical_json(list(record.candidate_thread_ids)),
                    record.updated_at or record.created_at,
                    record.resolved_by,
                    record.resolution_reason,
                ),
            )
            received = self._insert_event(
                connection,
                EventDraft(
                    stream_type="conversation",
                    stream_id=f"{record.platform}:{record.chat_id}",
                    project_id=record.project_id,
                    correlation_id=record.message_key,
                    event_type="conversation.message_received",
                    actor=record.actor_id,
                    occurred_at=record.created_at,
                    payload={
                        "message_key": record.message_key,
                        "payload_hash": record.payload_hash,
                        "message_artifact_id": record.message_artifact_id,
                        "text_artifact_id": record.text_artifact_id,
                        "platform": record.platform,
                        "message_id": record.message_id,
                        "chat_id": record.chat_id,
                    },
                ),
            )
            proposed = self._insert_event(
                connection,
                EventDraft(
                    stream_type="inbox",
                    stream_id=record.message_key,
                    project_id=record.project_id,
                    thread_id=record.thread_id,
                    correlation_id=record.message_key,
                    causation_id=received.event_id,
                    event_type="inbox.route_proposed",
                    actor="inbox-router",
                    occurred_at=record.created_at,
                    payload={
                        "route_type": record.route_type.value,
                        "route_state": record.route_state.value,
                        "thread_id": record.thread_id,
                        "confidence": record.confidence,
                        "rationale": record.rationale,
                        "domain": record.domain,
                        "requires_confirmation": record.requires_confirmation,
                        "update_kind": (
                            record.update_kind.value if record.update_kind is not None else None
                        ),
                        "candidate_thread_ids": list(record.candidate_thread_ids),
                    },
                ),
            )
            if record.route_state == InboxRouteState.CONFIRMED:
                self._insert_event(
                    connection,
                    EventDraft(
                        stream_type="inbox",
                        stream_id=record.message_key,
                        project_id=record.project_id,
                        thread_id=record.thread_id,
                        correlation_id=record.message_key,
                        causation_id=proposed.event_id,
                        event_type="inbox.route_confirmed",
                        actor="inbox-policy",
                        occurred_at=record.created_at,
                        payload={
                            "route_type": record.route_type.value,
                            "thread_id": record.thread_id,
                            "reason": "deterministic-policy-threshold",
                        },
                    ),
                )
            if record.route_type == InboxRouteType.CHAT:
                self._insert_event(
                    connection,
                    EventDraft(
                        stream_type="conversation",
                        stream_id=f"{record.platform}:{record.chat_id}",
                        project_id=record.project_id,
                        correlation_id=record.message_key,
                        causation_id=proposed.event_id,
                        event_type="conversation.message_appended",
                        actor=record.actor_id,
                        occurred_at=record.created_at,
                        payload={
                            "message_key": record.message_key,
                            "message_artifact_id": record.message_artifact_id,
                            "text_artifact_id": record.text_artifact_id,
                        },
                    ),
                )
            if task_submission is not None:
                self._submit_task_in_transaction(
                    connection,
                    task_submission,
                    now=to_timestamp(self._clock()),
                )
            if thread_update_submission is not None:
                self._apply_thread_update_in_transaction(connection, thread_update_submission)
            return record, True

    def resolve_inbox_route(
        self,
        record: InboxRecord,
        *,
        resolver: str,
        resolution_reason: str,
        task_submission: TaskSubmission | None = None,
        thread_update_submission: ThreadUpdateSubmission | None = None,
    ) -> InboxRecord:
        """Resolve one proposed route and commit its effect in the same transaction."""
        if record.route_state not in {
            InboxRouteState.CONFIRMED,
            InboxRouteState.CORRECTED,
            InboxRouteState.EXPIRED,
        }:
            raise ValueError("a route resolution must be confirmed, corrected, or expired")
        if not resolver.strip() or not resolution_reason.strip():
            raise ValueError("resolver and resolution_reason are required")
        if record.resolved_by != resolver:
            raise ValueError("resolved_by must match the route resolver")
        if not record.updated_at:
            raise ValueError("a route resolution requires updated_at")
        if record.requires_confirmation:
            raise ValueError("a resolved Inbox record cannot still require confirmation")
        if record.route_state == InboxRouteState.EXPIRED:
            if task_submission is not None or thread_update_submission is not None:
                raise ValueError("an expired route cannot apply a durable effect")
        elif record.route_type == InboxRouteType.NEW_TASK:
            if task_submission is None or thread_update_submission is not None:
                raise ValueError("a resolved new-task route requires one Task submission")
        elif record.route_type == InboxRouteType.THREAD_UPDATE:
            if thread_update_submission is None or task_submission is not None:
                raise ValueError("a resolved Thread route requires one Thread update")
        elif task_submission is not None or thread_update_submission is not None:
            raise ValueError("the resolved route type does not accept a Task effect")

        if task_submission is not None:
            if record.thread_id != task_submission.thread_id:
                raise ValueError("inbox route and Task submission must target the same thread")
            if record.project_id != task_submission.project_id:
                raise ValueError("inbox route and Task submission must target the same project")
            if task_submission.correlation_id != record.message_key:
                raise ValueError("Task submission correlation must use the inbound message key")
        if thread_update_submission is not None:
            if record.thread_id != thread_update_submission.thread_id:
                raise ValueError("inbox route and Thread update must target the same thread")
            if record.project_id != thread_update_submission.project_id:
                raise ValueError("inbox route and Thread update must target the same project")
            if record.message_key != thread_update_submission.message_key:
                raise ValueError("Thread update correlation must use the inbound message key")
            if record.update_kind != thread_update_submission.update_kind:
                raise ValueError("inbox route and Thread update kind must match")

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM inbox_messages WHERE message_key = ?",
                (record.message_key,),
            ).fetchone()
            if row is None:
                raise NotFound(f"inbox message not found: {record.message_key}")
            stored = self._row_to_inbox_record(row)
            self._assert_same_inbound(record, stored)
            expected_resolver = f"{stored.platform}:{stored.actor_id}"
            if resolver != expected_resolver:
                raise PermissionError("only the original channel actor may resolve this route")
            if (
                record.project_id != stored.project_id
                or record.message_artifact_id != stored.message_artifact_id
            ):
                raise IdempotencyConflict("route resolution changed the durable inbound envelope")
            if stored.route_state != InboxRouteState.PROPOSED:
                if self._same_route_resolution(stored, record):
                    return stored
                raise ConcurrencyConflict(
                    f"inbox route {record.message_key} is already {stored.route_state.value}"
                )

            connection.execute(
                """
                UPDATE inbox_messages
                SET route_type = ?, route_state = ?, thread_id = ?, confidence = ?,
                    rationale = ?, domain = ?, requires_confirmation = 0,
                    update_kind = ?, candidate_thread_ids_json = ?, updated_at = ?,
                    resolved_by = ?, resolution_reason = ?
                WHERE message_key = ? AND route_state = ?
                """,
                (
                    record.route_type.value,
                    record.route_state.value,
                    record.thread_id,
                    record.confidence,
                    record.rationale,
                    record.domain,
                    record.update_kind.value if record.update_kind is not None else None,
                    canonical_json(list(record.candidate_thread_ids)),
                    record.updated_at,
                    resolver,
                    resolution_reason,
                    record.message_key,
                    InboxRouteState.PROPOSED.value,
                ),
            )
            cause = connection.execute(
                """
                SELECT event_id FROM runtime_events
                WHERE stream_type = 'inbox' AND stream_id = ?
                ORDER BY sequence DESC LIMIT 1
                """,
                (record.message_key,),
            ).fetchone()
            event_type = {
                InboxRouteState.CONFIRMED: "inbox.route_confirmed",
                InboxRouteState.CORRECTED: "inbox.route_corrected",
                InboxRouteState.EXPIRED: "inbox.route_expired",
            }[record.route_state]
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="inbox",
                    stream_id=record.message_key,
                    project_id=record.project_id,
                    thread_id=record.thread_id,
                    correlation_id=record.message_key,
                    causation_id=str(cause["event_id"]) if cause is not None else None,
                    event_type=event_type,
                    actor=resolver,
                    occurred_at=record.updated_at,
                    payload={
                        "from_route_type": stored.route_type.value,
                        "to_route_type": record.route_type.value,
                        "route_state": record.route_state.value,
                        "thread_id": record.thread_id,
                        "update_kind": (
                            record.update_kind.value if record.update_kind is not None else None
                        ),
                        "reason": resolution_reason,
                    },
                ),
            )
            if task_submission is not None:
                self._submit_task_in_transaction(
                    connection,
                    task_submission,
                    now=record.updated_at,
                )
            if thread_update_submission is not None:
                self._apply_thread_update_in_transaction(connection, thread_update_submission)
            resolved = connection.execute(
                "SELECT * FROM inbox_messages WHERE message_key = ?",
                (record.message_key,),
            ).fetchone()
            assert resolved is not None
            return self._row_to_inbox_record(resolved)

    def _same_route_resolution(self, stored: InboxRecord, candidate: InboxRecord) -> bool:
        return (
            stored.route_type == candidate.route_type
            and stored.route_state == candidate.route_state
            and stored.thread_id == candidate.thread_id
            and stored.update_kind == candidate.update_kind
            and stored.resolved_by == candidate.resolved_by
        )

    def _apply_thread_update_in_transaction(
        self,
        connection: sqlite3.Connection,
        submission: ThreadUpdateSubmission,
    ) -> ThreadProjection:
        if not submission.message_artifact_id.startswith("artifact:sha256:"):
            raise ValueError("message_artifact_id must be an Artifact CAS reference")
        if not submission.text_artifact_id.startswith("artifact:sha256:"):
            raise ValueError("text_artifact_id must be an Artifact CAS reference")
        if submission.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not submission.executor_key.strip():
            raise ValueError("executor_key is required")
        projection = self._get_thread_in_transaction(connection, submission.thread_id)
        if projection is None:
            raise NotFound(f"thread not found: {submission.thread_id}")
        if projection.project_id != submission.project_id:
            raise InvalidTransition("Thread update cannot cross Project boundaries")
        if projection.revision != submission.expected_revision:
            raise ConcurrencyConflict(
                f"thread {submission.thread_id} revision is {projection.revision}; "
                f"expected {submission.expected_revision}"
            )
        if projection.actual_state in {ThreadState.CANCELLED, ThreadState.ARCHIVED}:
            raise InvalidTransition(
                "cancelled or archived Threads cannot receive executable updates"
            )
        if (
            submission.update_kind != ThreadUpdateKind.CANCEL
            and projection.desired_state != DesiredState.RUN
        ):
            raise InvalidTransition(
                f"thread desired state is {projection.desired_state}; update cannot enqueue a Run"
            )

        snapshot_ids = (
            submission.task_snapshot_id,
            submission.agent_snapshot_id,
            submission.context_manifest_id,
        )
        if submission.update_kind == ThreadUpdateKind.CANCEL:
            if submission.new_run_id is not None or any(snapshot_ids):
                raise ValueError("a cancellation update cannot create a Run")
        elif submission.new_run_id is None or not all(snapshot_ids):
            raise ValueError("an executable Thread update requires a Run and frozen snapshots")

        source_branch_id = projection.current_branch_id
        message_event = self._insert_event(
            connection,
            EventDraft(
                stream_type="thread",
                stream_id=submission.thread_id,
                project_id=submission.project_id,
                thread_id=submission.thread_id,
                branch_id=source_branch_id,
                correlation_id=submission.message_key,
                event_type="thread.message_appended",
                actor=submission.actor,
                occurred_at=submission.occurred_at,
                payload={
                    "update_id": submission.message_key,
                    "message_key": submission.message_key,
                    "message_artifact_id": submission.message_artifact_id,
                    "text_artifact_id": submission.text_artifact_id,
                    "update_kind": submission.update_kind.value,
                    "actor_id": submission.actor,
                    "branch_id": source_branch_id,
                    "task_snapshot_id": submission.task_snapshot_id,
                    "context_manifest_id": submission.context_manifest_id,
                    "new_run_id": submission.new_run_id,
                },
            ),
        )
        projection = reduce_thread(projection, message_event)

        if submission.update_kind == ThreadUpdateKind.CANCEL:
            if projection.actual_state == ThreadState.DELIVERED:
                raise InvalidTransition("a delivered Thread cannot be cancelled as active work")
            if projection.desired_state != DesiredState.CANCEL:
                event = self._insert_event(
                    connection,
                    EventDraft(
                        stream_type="thread",
                        stream_id=submission.thread_id,
                        project_id=submission.project_id,
                        thread_id=submission.thread_id,
                        branch_id=projection.current_branch_id,
                        correlation_id=submission.message_key,
                        causation_id=message_event.event_id,
                        event_type="thread.desired_state_changed",
                        actor=submission.actor,
                        occurred_at=submission.occurred_at,
                        payload={
                            "from": projection.desired_state.value,
                            "to": DesiredState.CANCEL.value,
                            "reason": "user_cancelled",
                        },
                    ),
                )
                projection = reduce_thread(projection, event)
            projection = self._cancel_runs_for_update(
                connection,
                projection,
                tuple(run.run_id for run in projection.runs),
                actor=submission.actor,
                correlation_id=submission.message_key,
                reason="user_cancelled",
                occurred_at=submission.occurred_at,
            )
            if projection.actual_state != ThreadState.CANCELLED:
                event = self._insert_event(
                    connection,
                    EventDraft(
                        stream_type="thread",
                        stream_id=submission.thread_id,
                        project_id=submission.project_id,
                        thread_id=submission.thread_id,
                        branch_id=projection.current_branch_id,
                        correlation_id=submission.message_key,
                        event_type="thread.state_changed",
                        actor=submission.actor,
                        occurred_at=submission.occurred_at,
                        payload={
                            "from": projection.actual_state.value,
                            "to": ThreadState.CANCELLED.value,
                            "reason": "user_cancelled",
                        },
                    ),
                )
                projection = reduce_thread(projection, event)
            self._upsert_thread_projection(connection, projection)
            return projection

        projection = self._cancel_runs_for_update(
            connection,
            projection,
            submission.supersedes_run_ids,
            actor=submission.actor,
            correlation_id=submission.message_key,
            reason=f"superseded_by_{submission.update_kind.value}",
            occurred_at=submission.occurred_at,
        )

        target_branch_id = submission.branch_id or projection.current_branch_id
        if submission.forked_from_branch_id is not None:
            if not all(
                (
                    submission.forked_from_event_id,
                    submission.base_snapshot_hash,
                    submission.reason_code,
                )
            ):
                raise ValueError("a forked branch requires event, snapshot, and reason evidence")
            forked = self._insert_event(
                connection,
                EventDraft(
                    stream_type="thread",
                    stream_id=submission.thread_id,
                    project_id=submission.project_id,
                    thread_id=submission.thread_id,
                    branch_id=target_branch_id,
                    correlation_id=submission.message_key,
                    causation_id=message_event.event_id,
                    event_type="thread.branch_forked",
                    actor=submission.actor,
                    occurred_at=submission.occurred_at,
                    payload={
                        "branch_id": target_branch_id,
                        "forked_from_branch_id": submission.forked_from_branch_id,
                        "forked_from_event_id": submission.forked_from_event_id,
                        "base_snapshot_hash": submission.base_snapshot_hash,
                        "reason_code": submission.reason_code,
                    },
                ),
            )
            projection = reduce_thread(projection, forked)
            selected = self._insert_event(
                connection,
                EventDraft(
                    stream_type="thread",
                    stream_id=submission.thread_id,
                    project_id=submission.project_id,
                    thread_id=submission.thread_id,
                    branch_id=target_branch_id,
                    correlation_id=submission.message_key,
                    causation_id=forked.event_id,
                    event_type="thread.branch_selected",
                    actor=submission.actor,
                    occurred_at=submission.occurred_at,
                    payload={
                        "branch_id": target_branch_id,
                        "reason_code": submission.reason_code,
                    },
                ),
            )
            projection = reduce_thread(projection, selected)

        assert submission.new_run_id is not None
        run_created = self._insert_event(
            connection,
            EventDraft(
                stream_type="thread",
                stream_id=submission.thread_id,
                project_id=submission.project_id,
                thread_id=submission.thread_id,
                branch_id=target_branch_id,
                run_id=submission.new_run_id,
                correlation_id=submission.message_key,
                causation_id=message_event.event_id,
                event_type="run.created",
                actor=submission.actor,
                occurred_at=submission.occurred_at,
                payload={
                    "run_id": submission.new_run_id,
                    "branch_id": target_branch_id,
                    "supersedes_run_id": submission.supersedes_run_id,
                    "executor_key": submission.executor_key,
                },
            ),
        )
        projection = reduce_thread(projection, run_created)
        snapshots_bound = self._insert_event(
            connection,
            EventDraft(
                stream_type="thread",
                stream_id=submission.thread_id,
                project_id=submission.project_id,
                thread_id=submission.thread_id,
                branch_id=target_branch_id,
                run_id=submission.new_run_id,
                correlation_id=submission.message_key,
                causation_id=run_created.event_id,
                event_type="run.snapshots_bound",
                actor="context-compiler",
                occurred_at=submission.occurred_at,
                payload={
                    "run_id": submission.new_run_id,
                    "task_snapshot_id": submission.task_snapshot_id,
                    "agent_snapshot_id": submission.agent_snapshot_id,
                    "context_manifest_id": submission.context_manifest_id,
                },
            ),
        )
        projection = reduce_thread(projection, snapshots_bound)
        queued = self._insert_event(
            connection,
            EventDraft(
                stream_type="thread",
                stream_id=submission.thread_id,
                project_id=submission.project_id,
                thread_id=submission.thread_id,
                branch_id=target_branch_id,
                run_id=submission.new_run_id,
                correlation_id=submission.message_key,
                causation_id=snapshots_bound.event_id,
                event_type="run.state_changed",
                actor="scheduler",
                occurred_at=submission.occurred_at,
                payload={
                    "run_id": submission.new_run_id,
                    "from": RunState.CREATED.value,
                    "to": RunState.QUEUED.value,
                },
            ),
        )
        projection = reduce_thread(projection, queued)
        if projection.actual_state in {
            ThreadState.CREATED,
            ThreadState.DORMANT,
            ThreadState.RUNNING,
            ThreadState.WAITING_USER,
            ThreadState.WAITING_APPROVAL,
            ThreadState.WAITING_RECEIPT,
            ThreadState.WAITING_DEPENDENCY,
            ThreadState.WAITING_RESOURCE,
            ThreadState.VERIFYING,
            ThreadState.DELIVERED,
            ThreadState.FAILED,
            ThreadState.PAUSED,
        }:
            event = self._insert_event(
                connection,
                EventDraft(
                    stream_type="thread",
                    stream_id=submission.thread_id,
                    project_id=submission.project_id,
                    thread_id=submission.thread_id,
                    branch_id=target_branch_id,
                    run_id=submission.new_run_id,
                    correlation_id=submission.message_key,
                    causation_id=queued.event_id,
                    event_type="thread.state_changed",
                    actor="scheduler",
                    occurred_at=submission.occurred_at,
                    payload={
                        "from": projection.actual_state.value,
                        "to": ThreadState.QUEUED.value,
                    },
                ),
            )
            projection = reduce_thread(projection, event)

        self._upsert_thread_projection(connection, projection)
        connection.execute(
            """
            INSERT INTO scheduler_jobs(
                run_id, thread_id, executor_key, state, priority, available_at, attempts,
                max_attempts, fencing_token, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?)
            """,
            (
                submission.new_run_id,
                submission.thread_id,
                submission.executor_key,
                JobState.QUEUED.value,
                submission.priority,
                submission.occurred_at,
                submission.max_attempts,
                submission.occurred_at,
                submission.occurred_at,
            ),
        )
        self._insert_event(
            connection,
            EventDraft(
                stream_type="scheduler-run",
                stream_id=submission.new_run_id,
                project_id=submission.project_id,
                thread_id=submission.thread_id,
                branch_id=target_branch_id,
                run_id=submission.new_run_id,
                correlation_id=submission.message_key,
                event_type="scheduler.run_enqueued",
                actor="scheduler",
                occurred_at=submission.occurred_at,
                payload={
                    "priority": submission.priority,
                    "available_at": submission.occurred_at,
                    "max_attempts": submission.max_attempts,
                    "executor_key": submission.executor_key,
                },
            ),
        )
        return projection

    def _cancel_runs_for_update(
        self,
        connection: sqlite3.Connection,
        projection: ThreadProjection,
        run_ids: Iterable[str],
        *,
        actor: str,
        correlation_id: str,
        reason: str,
        occurred_at: str,
    ) -> ThreadProjection:
        terminal = {
            RunState.COMPLETED,
            RunState.PARTIAL,
            RunState.FAILED,
            RunState.QUARANTINED,
            RunState.CANCELLED,
        }
        for run_id in dict.fromkeys(run_ids):
            run = projection.run(run_id)
            if run is None or run.state in terminal:
                continue
            self._cancel_pending_run_actions(
                connection,
                run_id=run.run_id,
                thread_id=projection.thread_id,
                actor=actor,
                reason=reason,
                occurred_at=occurred_at,
                correlation_id=correlation_id,
            )
            event = self._insert_event(
                connection,
                EventDraft(
                    stream_type="thread",
                    stream_id=projection.thread_id,
                    project_id=projection.project_id,
                    thread_id=projection.thread_id,
                    branch_id=run.branch_id,
                    run_id=run.run_id,
                    correlation_id=correlation_id,
                    event_type="run.state_changed",
                    actor=actor,
                    occurred_at=occurred_at,
                    payload={
                        "run_id": run.run_id,
                        "from": run.state.value,
                        "to": RunState.CANCELLED.value,
                        "reason": reason,
                    },
                ),
            )
            projection = reduce_thread(projection, event)
            job = connection.execute(
                "SELECT * FROM scheduler_jobs WHERE run_id = ?",
                (run.run_id,),
            ).fetchone()
            if job is not None and str(job["state"]) in {
                JobState.QUEUED.value,
                JobState.CLAIMED.value,
            }:
                connection.execute(
                    """
                    UPDATE scheduler_jobs
                    SET state = ?, lease_owner = NULL, lease_id = NULL,
                        lease_expires_at = NULL, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (JobState.CANCELLED.value, occurred_at, run.run_id),
                )
                self._insert_event(
                    connection,
                    EventDraft(
                        stream_type="scheduler-run",
                        stream_id=run.run_id,
                        project_id=projection.project_id,
                        thread_id=projection.thread_id,
                        branch_id=run.branch_id,
                        run_id=run.run_id,
                        correlation_id=correlation_id,
                        event_type="scheduler.run_cancelled",
                        actor=actor,
                        occurred_at=occurred_at,
                        payload={
                            "from": str(job["state"]),
                            "reason": reason,
                            "fencing_token": int(job["fencing_token"]),
                        },
                    ),
                )
        if projection.attention_state == AttentionState.NEEDS_APPROVAL:
            pending = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM runtime_approvals AS approval
                JOIN action_intents AS intent ON intent.intent_id = approval.intent_id
                WHERE intent.thread_id = ? AND approval.status = ?
                """,
                (projection.thread_id, ApprovalState.PENDING.value),
            ).fetchone()
            assert pending is not None
            if int(pending["count"]) == 0:
                event = self._insert_event(
                    connection,
                    EventDraft(
                        stream_type="thread",
                        stream_id=projection.thread_id,
                        project_id=projection.project_id,
                        thread_id=projection.thread_id,
                        branch_id=projection.current_branch_id,
                        run_id=projection.active_run_id,
                        correlation_id=correlation_id,
                        event_type="thread.attention_changed",
                        actor=actor,
                        occurred_at=occurred_at,
                        payload={
                            "from": projection.attention_state.value,
                            "to": AttentionState.NONE.value,
                            "reason": "superseded approvals cancelled",
                        },
                    ),
                )
                projection = reduce_thread(projection, event)
        return projection

    def _cancel_pending_run_actions(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        thread_id: str,
        actor: str,
        reason: str,
        occurred_at: str,
        correlation_id: str,
    ) -> None:
        intents = connection.execute(
            "SELECT * FROM action_intents WHERE run_id = ? AND status = ?",
            (run_id, ActionStatus.PENDING.value),
        ).fetchall()
        for intent in intents:
            approvals = connection.execute(
                """
                SELECT * FROM runtime_approvals
                WHERE intent_id = ? AND status = ?
                """,
                (intent["intent_id"], ApprovalState.PENDING.value),
            ).fetchall()
            for approval in approvals:
                connection.execute(
                    """
                    UPDATE runtime_approvals
                    SET status = ?, updated_at = ?, resolved_by = ?, resolved_at = ?
                    WHERE approval_id = ? AND status = ?
                    """,
                    (
                        ApprovalState.CANCELLED.value,
                        occurred_at,
                        actor,
                        occurred_at,
                        approval["approval_id"],
                        ApprovalState.PENDING.value,
                    ),
                )
                self._insert_event(
                    connection,
                    EventDraft(
                        stream_type="approval",
                        stream_id=str(approval["approval_id"]),
                        thread_id=thread_id,
                        run_id=run_id,
                        correlation_id=correlation_id,
                        event_type="approval.cancelled",
                        actor=actor,
                        occurred_at=occurred_at,
                        payload={
                            "approval_id": approval["approval_id"],
                            "intent_id": intent["intent_id"],
                            "reason": reason,
                        },
                    ),
                )
            connection.execute(
                """
                UPDATE action_intents
                SET status = ?, last_error = ?, updated_at = ?
                WHERE intent_id = ? AND status = ?
                """,
                (
                    ActionStatus.CANCELLED.value,
                    reason,
                    occurred_at,
                    intent["intent_id"],
                    ActionStatus.PENDING.value,
                ),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="action",
                    stream_id=str(intent["intent_id"]),
                    thread_id=thread_id,
                    run_id=run_id,
                    correlation_id=correlation_id,
                    event_type="action.cancelled",
                    actor=actor,
                    occurred_at=occurred_at,
                    payload={
                        "intent_id": intent["intent_id"],
                        "reason": reason,
                    },
                ),
            )

    def record_inbox_route(self, record: InboxRecord) -> InboxRecord:
        if record.route_state in {InboxRouteState.CONFIRMED, InboxRouteState.CORRECTED} and (
            record.route_type in {InboxRouteType.NEW_TASK, InboxRouteType.THREAD_UPDATE}
        ):
            raise ValueError(
                "confirmed Task routes must use accept_inbox_route with their durable effect"
            )
        stored, _ = self.accept_inbox_route(record)
        return stored

    def _assert_same_inbound(self, candidate: InboxRecord, stored: InboxRecord) -> None:
        stable_candidate = (
            candidate.platform,
            candidate.message_id,
            candidate.chat_id,
            candidate.actor_id,
            candidate.text_artifact_id,
        )
        stable_stored = (
            stored.platform,
            stored.message_id,
            stored.chat_id,
            stored.actor_id,
            stored.text_artifact_id,
        )
        payload_conflict = bool(stored.payload_hash) and (
            stored.payload_hash != candidate.payload_hash
        )
        if payload_conflict or stable_candidate != stable_stored:
            raise IdempotencyConflict(
                f"inbox message key was reused with a different payload: {candidate.message_key}"
            )

    def list_inbox_records(
        self,
        *,
        route_type: InboxRouteType | None = None,
        thread_id: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[InboxRecord]:
        clauses: list[str] = []
        values: list[Any] = []
        if route_type is not None:
            clauses.append("route_type = ?")
            values.append(route_type.value)
        if thread_id is not None:
            clauses.append("thread_id = ?")
            values.append(thread_id)
        if project_id is not None:
            clauses.append("project_id = ?")
            values.append(project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM inbox_messages {where} ORDER BY created_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [self._row_to_inbox_record(row) for row in rows]

    def list_active_thread_ids_for_chat(
        self,
        *,
        platform: str,
        chat_id: str,
        project_id: str,
        limit: int = 10,
    ) -> tuple[str, ...]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT inbox.thread_id, MAX(inbox.updated_at) AS latest_at
                FROM inbox_messages AS inbox
                JOIN thread_projections AS thread ON thread.thread_id = inbox.thread_id
                WHERE inbox.platform = ? AND inbox.chat_id = ? AND inbox.project_id = ?
                  AND inbox.thread_id IS NOT NULL
                  AND inbox.route_state IN (?, ?)
                  AND thread.actual_state NOT IN (?, ?)
                  AND thread.desired_state = ?
                GROUP BY inbox.thread_id
                ORDER BY latest_at DESC, inbox.thread_id ASC
                LIMIT ?
                """,
                (
                    platform,
                    chat_id,
                    project_id,
                    InboxRouteState.CONFIRMED.value,
                    InboxRouteState.CORRECTED.value,
                    ThreadState.CANCELLED.value,
                    ThreadState.ARCHIVED.value,
                    DesiredState.RUN.value,
                    limit,
                ),
            ).fetchall()
        return tuple(str(row["thread_id"]) for row in rows)

    # ------------------------------------------------------------------
    # Persistent approval gate for external action Intents
    # ------------------------------------------------------------------

    def create_approval(
        self,
        *,
        intent_id: str,
        risk_level: str,
        requested_by: str,
        reason: str,
        ttl_seconds: int = 1800,
        policy_snapshot_id: str | None = None,
        approval_id: str | None = None,
    ) -> ApprovalRequest:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be at least 1")
        if policy_snapshot_id is not None and not policy_snapshot_id.startswith("artifact:sha256:"):
            raise ValueError("policy_snapshot_id must be an Artifact CAS reference")
        now_dt = self._clock()
        now = to_timestamp(now_dt)
        expires_at = to_timestamp(now_dt + timedelta(seconds=ttl_seconds))
        approval_id = approval_id or uuid4().hex
        with self._transaction() as connection:
            intent_row = connection.execute(
                "SELECT * FROM action_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if intent_row is None:
                raise NotFound(f"action intent not found: {intent_id}")
            if not bool(intent_row["requires_approval"]):
                raise InvalidTransition(f"action intent does not require approval: {intent_id}")
            existing = connection.execute(
                "SELECT * FROM runtime_approvals WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if existing is not None:
                approval = self._row_to_approval(existing)
                requested = (risk_level, requested_by, reason, policy_snapshot_id)
                stored = (
                    approval.risk_level,
                    approval.requested_by,
                    approval.reason,
                    approval.policy_snapshot_id,
                )
                if requested != stored:
                    raise IdempotencyConflict(
                        f"approval for intent {intent_id} was requested with different content"
                    )
                return approval
            connection.execute(
                """
                INSERT INTO runtime_approvals(
                    approval_id, intent_id, status, risk_level, requested_by, reason,
                    policy_snapshot_id, created_at, expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    intent_id,
                    ApprovalState.PENDING.value,
                    risk_level,
                    requested_by,
                    reason,
                    policy_snapshot_id,
                    now,
                    expires_at,
                    now,
                ),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="approval",
                    stream_id=approval_id,
                    thread_id=str(intent_row["thread_id"]),
                    run_id=str(intent_row["run_id"]),
                    event_type="approval.requested",
                    actor=requested_by,
                    occurred_at=now,
                    payload={
                        "approval_id": approval_id,
                        "intent_id": intent_id,
                        "risk_level": risk_level,
                        "reason": reason,
                        "policy_snapshot_id": policy_snapshot_id,
                        "expires_at": expires_at,
                    },
                ),
            )
            self._set_thread_attention_in_transaction(
                connection,
                thread_id=str(intent_row["thread_id"]),
                target=AttentionState.NEEDS_APPROVAL,
                actor="approval-gate",
                reason=f"approval pending: {approval_id}",
                occurred_at=now,
            )
            row = connection.execute(
                "SELECT * FROM runtime_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            assert row is not None
            return self._row_to_approval(row)

    def decide_approval(
        self,
        approval_id: str,
        *,
        decision: ApprovalState,
        actor: str,
        decision_evidence_artifact_id: str | None = None,
    ) -> ApprovalRequest:
        self.expire_pending_approvals()
        if decision not in {ApprovalState.APPROVED, ApprovalState.DENIED}:
            raise ValueError("decision must be approved or denied")
        if (
            decision_evidence_artifact_id is not None
            and not decision_evidence_artifact_id.startswith("artifact:sha256:")
        ):
            raise ValueError("decision_evidence_artifact_id must be an Artifact CAS reference")
        now = to_timestamp(self._clock())
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"approval not found: {approval_id}")
            current = ApprovalState(str(row["status"]))
            if current == decision:
                return self._row_to_approval(row)
            if current != ApprovalState.PENDING:
                raise InvalidTransition(f"approval is already {current}: {approval_id}")
            connection.execute(
                """
                UPDATE runtime_approvals
                SET status = ?, resolved_by = ?, resolved_at = ?, updated_at = ?,
                    decision_evidence_artifact_id = ?
                WHERE approval_id = ?
                """,
                (
                    decision.value,
                    actor,
                    now,
                    now,
                    decision_evidence_artifact_id,
                    approval_id,
                ),
            )
            intent_row = connection.execute(
                "SELECT * FROM action_intents WHERE intent_id = ?", (row["intent_id"],)
            ).fetchone()
            assert intent_row is not None
            if decision == ApprovalState.DENIED:
                connection.execute(
                    """
                    UPDATE action_intents
                    SET status = ?, last_error = ?, updated_at = ?
                    WHERE intent_id = ? AND status = ?
                    """,
                    (
                        ActionStatus.CANCELLED.value,
                        "action denied by approval gate",
                        now,
                        row["intent_id"],
                        ActionStatus.PENDING.value,
                    ),
                )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="approval",
                    stream_id=approval_id,
                    thread_id=str(intent_row["thread_id"]),
                    run_id=str(intent_row["run_id"]),
                    event_type=f"approval.{decision.value}",
                    actor=actor,
                    occurred_at=now,
                    payload={
                        "approval_id": approval_id,
                        "intent_id": row["intent_id"],
                        "decision_evidence_artifact_id": decision_evidence_artifact_id,
                    },
                ),
            )
            pending_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM runtime_approvals AS approval
                JOIN action_intents AS intent ON intent.intent_id = approval.intent_id
                WHERE intent.thread_id = ? AND approval.status = ?
                """,
                (intent_row["thread_id"], ApprovalState.PENDING.value),
            ).fetchone()
            assert pending_count is not None
            if int(pending_count["count"]) == 0:
                self._set_thread_attention_in_transaction(
                    connection,
                    thread_id=str(intent_row["thread_id"]),
                    target=AttentionState.NONE,
                    actor="approval-gate",
                    reason=f"approval resolved: {approval_id}",
                    occurred_at=now,
                )
            updated = connection.execute(
                "SELECT * FROM runtime_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            assert updated is not None
            return self._row_to_approval(updated)

    def expire_pending_approvals(self) -> list[ApprovalRequest]:
        now = to_timestamp(self._clock())
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runtime_approvals
                WHERE status = ? AND expires_at <= ? ORDER BY expires_at
                """,
                (ApprovalState.PENDING.value, now),
            ).fetchall()
            expired: list[ApprovalRequest] = []
            affected_threads: set[str] = set()
            for row in rows:
                intent_row = connection.execute(
                    "SELECT * FROM action_intents WHERE intent_id = ?", (row["intent_id"],)
                ).fetchone()
                assert intent_row is not None
                affected_threads.add(str(intent_row["thread_id"]))
                expired.append(self._expire_approval_row(connection, row, now))
            for thread_id in affected_threads:
                pending = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM runtime_approvals AS approval
                    JOIN action_intents AS intent ON intent.intent_id = approval.intent_id
                    WHERE intent.thread_id = ? AND approval.status = ?
                    """,
                    (thread_id, ApprovalState.PENDING.value),
                ).fetchone()
                assert pending is not None
                if int(pending["count"]) == 0:
                    self._set_thread_attention_in_transaction(
                        connection,
                        thread_id=thread_id,
                        target=AttentionState.NONE,
                        actor="approval-gate",
                        reason="all pending approvals expired or resolved",
                        occurred_at=now,
                    )
            return expired

    def get_approval(self, approval_id: str) -> ApprovalRequest:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        if row is None:
            raise NotFound(f"approval not found: {approval_id}")
        return self._row_to_approval(row)

    def find_approval_for_intent(self, intent_id: str) -> ApprovalRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_approvals WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        return self._row_to_approval(row) if row is not None else None

    def list_approvals(
        self,
        *,
        status: ApprovalState | None = None,
        limit: int = 100,
    ) -> list[ApprovalRequest]:
        where = "WHERE status = ?" if status is not None else ""
        values: list[Any] = [status.value] if status is not None else []
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM runtime_approvals {where} ORDER BY updated_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [self._row_to_approval(row) for row in rows]

    # ------------------------------------------------------------------
    # Intent -> external action -> Receipt safety boundary
    # ------------------------------------------------------------------

    def create_action_intent(
        self,
        *,
        thread_id: str,
        run_id: str,
        capability: str,
        request_artifact_id: str,
        payload_hash: str,
        idempotency_key: str,
        requires_approval: bool = False,
        actor: str = "runtime",
        intent_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ActionIntent:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if not request_artifact_id.startswith("artifact:sha256:"):
            raise ValueError("request_artifact_id must be an Artifact CAS reference")
        artifact_hash = request_artifact_id.removeprefix("artifact:sha256:")
        if payload_hash != artifact_hash:
            raise ValueError("payload_hash must match the action request artifact id")
        now = to_timestamp(self._clock())
        intent_id = intent_id or uuid4().hex
        with self._transaction() as connection:
            projection = self._get_thread_in_transaction(connection, thread_id)
            if projection is None:
                raise NotFound(f"thread not found: {thread_id}")
            run = projection.run(run_id)
            if run is None:
                raise NotFound(f"run not found in thread {thread_id}: {run_id}")
            if run.state in {
                RunState.COMPLETED,
                RunState.PARTIAL,
                RunState.FAILED,
                RunState.QUARANTINED,
                RunState.CANCELLED,
            }:
                raise InvalidTransition(
                    f"cannot create an external action for terminal run {run_id}: {run.state}"
                )
            existing = connection.execute(
                "SELECT * FROM action_intents WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                intent = self._row_to_action_intent(existing)
                comparable = (
                    intent.thread_id,
                    intent.run_id,
                    intent.capability,
                    intent.request_artifact_id,
                    intent.payload_hash,
                    intent.requires_approval,
                )
                requested = (
                    thread_id,
                    run_id,
                    capability,
                    request_artifact_id,
                    payload_hash,
                    requires_approval,
                )
                if comparable != requested:
                    raise IdempotencyConflict(
                        f"action idempotency key was reused with different content: "
                        f"{idempotency_key}"
                    )
                return intent
            connection.execute(
                """
                INSERT INTO action_intents(
                    intent_id, idempotency_key, thread_id, run_id, capability,
                    request_artifact_id, payload_hash, requires_approval,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent_id,
                    idempotency_key,
                    thread_id,
                    run_id,
                    capability,
                    request_artifact_id,
                    payload_hash,
                    int(requires_approval),
                    ActionStatus.PENDING.value,
                    now,
                    now,
                ),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="action",
                    stream_id=intent_id,
                    project_id=projection.project_id,
                    thread_id=thread_id,
                    branch_id=run.branch_id,
                    run_id=run_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    event_type="action.intent_created",
                    actor=actor,
                    occurred_at=now,
                    payload={
                        "intent_id": intent_id,
                        "idempotency_key": idempotency_key,
                        "capability": capability,
                        "request_artifact_id": request_artifact_id,
                        "payload_hash": payload_hash,
                        "requires_approval": requires_approval,
                    },
                ).normalized(),
            )
            row = connection.execute(
                "SELECT * FROM action_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            assert row is not None
            return self._row_to_action_intent(row)

    def claim_action(
        self,
        intent_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        for_reconciliation: bool = False,
    ) -> ActionClaim:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        if not for_reconciliation:
            self.expire_pending_approvals()
        now_dt = self._clock()
        now = to_timestamp(now_dt)
        expires_at = to_timestamp(now_dt + timedelta(seconds=lease_seconds))
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM action_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"action intent not found: {intent_id}")
            status = ActionStatus(str(row["status"]))
            if (
                status in {ActionStatus.EXECUTING, ActionStatus.RECOVERING}
                and str(row["lease_expires_at"]) <= now
            ):
                self._mark_action_reconcile_required(
                    connection,
                    row,
                    now=now,
                    reason="action lease expired before a durable receipt",
                )
                row = connection.execute(
                    "SELECT * FROM action_intents WHERE intent_id = ?", (intent_id,)
                ).fetchone()
                assert row is not None
                status = ActionStatus(str(row["status"]))

            if not for_reconciliation and bool(row["requires_approval"]):
                approval_row = connection.execute(
                    "SELECT * FROM runtime_approvals WHERE intent_id = ?", (intent_id,)
                ).fetchone()
                if approval_row is None:
                    raise InvalidTransition(f"action {intent_id} requires approval")
                approval_status = ApprovalState(str(approval_row["status"]))
                if approval_status != ApprovalState.APPROVED:
                    raise InvalidTransition(f"action {intent_id} approval is {approval_status}")

            required_status = (
                ActionStatus.RECONCILE_REQUIRED if for_reconciliation else ActionStatus.PENDING
            )
            if status != required_status:
                if status == ActionStatus.RECONCILE_REQUIRED:
                    raise ReconciliationRequired(
                        f"action {intent_id} may have executed; claim it for reconciliation"
                    )
                raise InvalidTransition(
                    f"action {intent_id} is {status}; expected {required_status}"
                )

            target_status = (
                ActionStatus.RECOVERING if for_reconciliation else ActionStatus.EXECUTING
            )
            lease_id = uuid4().hex
            fencing_token = int(row["fencing_token"]) + 1
            connection.execute(
                """
                UPDATE action_intents
                SET status = ?, fencing_token = ?, lease_owner = ?, lease_id = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE intent_id = ?
                """,
                (
                    target_status.value,
                    fencing_token,
                    worker_id,
                    lease_id,
                    expires_at,
                    now,
                    intent_id,
                ),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="action",
                    stream_id=intent_id,
                    thread_id=str(row["thread_id"]),
                    run_id=str(row["run_id"]),
                    event_type=(
                        "action.reconciliation_claimed"
                        if for_reconciliation
                        else "action.execution_claimed"
                    ),
                    actor=worker_id,
                    occurred_at=now,
                    payload={
                        "lease_id": lease_id,
                        "fencing_token": fencing_token,
                        "expires_at": expires_at,
                    },
                ).normalized(),
            )
            return ActionClaim(
                intent_id=intent_id,
                idempotency_key=str(row["idempotency_key"]),
                thread_id=str(row["thread_id"]),
                run_id=str(row["run_id"]),
                capability=str(row["capability"]),
                request_artifact_id=str(row["request_artifact_id"]),
                worker_id=worker_id,
                lease_id=lease_id,
                fencing_token=fencing_token,
                expires_at=expires_at,
                reconciliation=for_reconciliation,
            )

    def record_action_receipt(
        self,
        claim: ActionClaim,
        *,
        outcome: ReceiptOutcome,
        provider: str,
        response_artifact_id: str | None = None,
        external_reference: str | None = None,
        evidence: dict[str, Any] | None = None,
        receipt_id: str | None = None,
    ) -> ActionReceipt:
        if not provider.strip():
            raise ValueError("provider is required")
        if response_artifact_id is not None and not response_artifact_id.startswith(
            "artifact:sha256:"
        ):
            raise ValueError("response_artifact_id must be an Artifact CAS reference")
        now = to_timestamp(self._clock())
        receipt_id = receipt_id or uuid4().hex
        evidence = evidence or {}
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM action_receipts WHERE receipt_id = ?", (receipt_id,)
            ).fetchone()
            if existing is not None:
                receipt = self._row_to_action_receipt(existing)
                requested = (
                    claim.intent_id,
                    outcome,
                    provider,
                    response_artifact_id,
                    external_reference,
                    canonical_json(evidence),
                )
                stored = (
                    receipt.intent_id,
                    receipt.outcome,
                    receipt.provider,
                    receipt.response_artifact_id,
                    receipt.external_reference,
                    canonical_json(dict(receipt.evidence)),
                )
                if stored != requested:
                    raise IdempotencyConflict(
                        f"receipt id was reused with different content: {receipt_id}"
                    )
                return receipt

            self._assert_live_action_claim(connection, claim, now)
            if outcome == ReceiptOutcome.SUCCEEDED:
                target = ActionStatus.SUCCEEDED
            elif outcome == ReceiptOutcome.FAILED:
                target = ActionStatus.FAILED
            elif outcome == ReceiptOutcome.NOT_FOUND:
                target = ActionStatus.PENDING
            else:
                target = ActionStatus.RECONCILE_REQUIRED
            connection.execute(
                """
                INSERT INTO action_receipts(
                    receipt_id, intent_id, outcome, provider, response_artifact_id,
                    external_reference, evidence_json, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    claim.intent_id,
                    outcome.value,
                    provider,
                    response_artifact_id,
                    external_reference,
                    canonical_json(evidence),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE action_intents
                SET status = ?, lease_owner = NULL, lease_id = NULL,
                    lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE intent_id = ?
                """,
                (
                    target.value,
                    None
                    if target != ActionStatus.RECONCILE_REQUIRED
                    else "receipt was inconclusive",
                    now,
                    claim.intent_id,
                ),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="action",
                    stream_id=claim.intent_id,
                    thread_id=claim.thread_id,
                    run_id=claim.run_id,
                    event_type="action.receipt_recorded",
                    actor=claim.worker_id,
                    occurred_at=now,
                    payload={
                        "receipt_id": receipt_id,
                        "outcome": outcome.value,
                        "provider": provider,
                        "response_artifact_id": response_artifact_id,
                        "external_reference": external_reference,
                        "next_status": target.value,
                        "lease_id": claim.lease_id,
                        "fencing_token": claim.fencing_token,
                    },
                ).normalized(),
            )
            receipt_row = connection.execute(
                "SELECT * FROM action_receipts WHERE receipt_id = ?", (receipt_id,)
            ).fetchone()
            assert receipt_row is not None
            return self._row_to_action_receipt(receipt_row)

    def recover_incomplete_actions(self) -> list[ActionIntent]:
        now = to_timestamp(self._clock())
        recovered_ids: list[str] = []
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM action_intents
                WHERE status IN (?, ?) AND lease_expires_at <= ?
                ORDER BY updated_at
                """,
                (ActionStatus.EXECUTING.value, ActionStatus.RECOVERING.value, now),
            ).fetchall()
            for row in rows:
                self._mark_action_reconcile_required(
                    connection,
                    row,
                    now=now,
                    reason="action lease expired before a durable receipt",
                )
                recovered_ids.append(str(row["intent_id"]))
            recovered: list[ActionIntent] = []
            for intent_id in recovered_ids:
                row = connection.execute(
                    "SELECT * FROM action_intents WHERE intent_id = ?", (intent_id,)
                ).fetchone()
                assert row is not None
                recovered.append(self._row_to_action_intent(row))
            return recovered

    def get_action_intent(self, intent_id: str) -> ActionIntent:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM action_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        if row is None:
            raise NotFound(f"action intent not found: {intent_id}")
        return self._row_to_action_intent(row)

    def list_action_intents(
        self,
        *,
        status: ActionStatus | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[ActionIntent]:
        clauses: list[str] = []
        values: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            values.append(status.value)
        if run_id is not None:
            clauses.append("run_id = ?")
            values.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM action_intents {where} ORDER BY updated_at LIMIT ?",
                values,
            ).fetchall()
        return [self._row_to_action_intent(row) for row in rows]

    def list_action_receipts(
        self,
        *,
        intent_id: str,
        limit: int = 100,
    ) -> list[ActionReceipt]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM action_receipts
                WHERE intent_id = ?
                ORDER BY occurred_at DESC, receipt_id DESC
                LIMIT ?
                """,
                (intent_id, limit),
            ).fetchall()
        return [self._row_to_action_receipt(row) for row in rows]

    # ------------------------------------------------------------------
    # Internal serialization and transaction helpers
    # ------------------------------------------------------------------

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        draft: EventDraft,
    ) -> EventEnvelope:
        normalized = draft.normalized()
        row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) AS last_sequence
            FROM runtime_events WHERE stream_type = ? AND stream_id = ?
            """,
            (normalized.stream_type, normalized.stream_id),
        ).fetchone()
        sequence = int(row["last_sequence"]) + 1
        cursor = connection.execute(
            """
            INSERT INTO runtime_events(
                event_id, stream_type, stream_id, sequence, project_id, thread_id,
                branch_id, run_id, correlation_id, causation_id, event_type,
                actor, occurred_at, payload_json, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized.event_id,
                normalized.stream_type,
                normalized.stream_id,
                sequence,
                normalized.project_id,
                normalized.thread_id,
                normalized.branch_id,
                normalized.run_id,
                normalized.correlation_id,
                normalized.causation_id,
                normalized.event_type,
                normalized.actor,
                normalized.occurred_at,
                canonical_json(dict(normalized.payload)),
                normalized.schema_version,
            ),
        )
        global_position = cursor.lastrowid
        if global_position is None:
            raise RuntimeError("SQLite did not return a global event position")
        return EventEnvelope(
            global_position=int(global_position),
            sequence=sequence,
            event_id=normalized.event_id,
            stream_type=normalized.stream_type,
            stream_id=normalized.stream_id,
            project_id=normalized.project_id,
            thread_id=normalized.thread_id,
            branch_id=normalized.branch_id,
            run_id=normalized.run_id,
            correlation_id=normalized.correlation_id,
            causation_id=normalized.causation_id,
            event_type=normalized.event_type,
            actor=normalized.actor,
            occurred_at=normalized.occurred_at,
            payload=dict(normalized.payload),
            schema_version=normalized.schema_version,
        )

    def _event_by_id(
        self,
        connection: sqlite3.Connection,
        event_id: str,
    ) -> EventEnvelope | None:
        row = connection.execute(
            "SELECT * FROM runtime_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return self._row_to_event(row) if row else None

    def _assert_same_event(self, draft: EventDraft, envelope: EventEnvelope) -> None:
        comparable_draft = {
            "stream_type": draft.stream_type,
            "stream_id": draft.stream_id,
            "project_id": draft.project_id,
            "thread_id": draft.thread_id,
            "branch_id": draft.branch_id,
            "run_id": draft.run_id,
            "correlation_id": draft.correlation_id,
            "causation_id": draft.causation_id,
            "event_type": draft.event_type,
            "actor": draft.actor,
            "occurred_at": draft.occurred_at,
            "payload": dict(draft.payload),
            "schema_version": draft.schema_version,
        }
        comparable_stored = {
            "stream_type": envelope.stream_type,
            "stream_id": envelope.stream_id,
            "project_id": envelope.project_id,
            "thread_id": envelope.thread_id,
            "branch_id": envelope.branch_id,
            "run_id": envelope.run_id,
            "correlation_id": envelope.correlation_id,
            "causation_id": envelope.causation_id,
            "event_type": envelope.event_type,
            "actor": envelope.actor,
            "occurred_at": envelope.occurred_at,
            "payload": dict(envelope.payload),
            "schema_version": envelope.schema_version,
        }
        if canonical_json(comparable_draft) != canonical_json(comparable_stored):
            raise IdempotencyConflict(
                f"event id {draft.event_id} was reused with different content"
            )

    def _get_thread_in_transaction(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
    ) -> ThreadProjection | None:
        row = connection.execute(
            "SELECT projection_json FROM thread_projections WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return self._projection_from_json(str(row["projection_json"])) if row else None

    def _upsert_thread_projection(
        self,
        connection: sqlite3.Connection,
        projection: ThreadProjection,
    ) -> None:
        projection_json = canonical_json(projection.as_dict())
        connection.execute(
            """
            INSERT INTO thread_projections(
                thread_id, project_id, title, desired_state, actual_state, attention_state,
                revision, active_run_id, latest_delivery_id, updated_at,
                projection_json, projection_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                project_id = excluded.project_id,
                title = excluded.title,
                desired_state = excluded.desired_state,
                actual_state = excluded.actual_state,
                attention_state = excluded.attention_state,
                revision = excluded.revision,
                active_run_id = excluded.active_run_id,
                latest_delivery_id = excluded.latest_delivery_id,
                updated_at = excluded.updated_at,
                projection_json = excluded.projection_json,
                projection_hash = excluded.projection_hash
            """,
            (
                projection.thread_id,
                projection.project_id,
                projection.title,
                projection.desired_state.value,
                projection.actual_state.value,
                projection.attention_state.value,
                projection.revision,
                projection.active_run_id,
                projection.latest_delivery_id,
                projection.updated_at,
                projection_json,
                projection.projection_hash,
            ),
        )

    def _projection_from_json(self, value: str) -> ThreadProjection:
        data = json.loads(value)
        runs = tuple(
            RunProjection(
                run_id=item["run_id"],
                branch_id=item["branch_id"],
                executor_key=str(item.get("executor_key") or "unassigned"),
                state=RunState(item["state"]),
                revision=int(item["revision"]),
                started_at=item.get("started_at"),
                completed_at=item.get("completed_at"),
                waiting_on=tuple(item.get("waiting_on") or ()),
                output_artifact_id=item.get("output_artifact_id"),
                task_snapshot_id=item.get("task_snapshot_id"),
                agent_snapshot_id=item.get("agent_snapshot_id"),
                context_manifest_id=item.get("context_manifest_id"),
                latest_checkpoint_id=item.get("latest_checkpoint_id"),
                checkpoint_sequence=int(item.get("checkpoint_sequence", 0)),
                error=item.get("error"),
                created_at=item.get("created_at", ""),
                created_sequence=int(item.get("created_sequence", 0)),
                supersedes_run_id=item.get("supersedes_run_id"),
            )
            for item in data.get("runs", [])
        )
        branches = tuple(
            BranchProjection(
                branch_id=item["branch_id"],
                status=BranchStatus(item["status"]),
                created_by=item["created_by"],
                created_at=item["created_at"],
                forked_from_branch_id=item.get("forked_from_branch_id"),
                forked_from_event_id=item.get("forked_from_event_id"),
                base_snapshot_hash=item.get("base_snapshot_hash"),
                reason_code=item.get("reason_code"),
                selected_at=item.get("selected_at"),
                rejected_at=item.get("rejected_at"),
            )
            for item in data.get("branches", [])
        )
        updates = tuple(
            ThreadUpdateProjection(
                update_id=item["update_id"],
                message_key=item["message_key"],
                message_artifact_id=item["message_artifact_id"],
                text_artifact_id=item["text_artifact_id"],
                kind=ThreadUpdateKind(item["kind"]),
                actor_id=item["actor_id"],
                branch_id=item["branch_id"],
                occurred_at=item["occurred_at"],
                task_snapshot_id=item.get("task_snapshot_id"),
                context_manifest_id=item.get("context_manifest_id"),
                new_run_id=item.get("new_run_id"),
            )
            for item in data.get("updates", [])
        )
        return ThreadProjection(
            thread_id=data["thread_id"],
            project_id=data["project_id"],
            title=data["title"],
            desired_state=DesiredState(data["desired_state"]),
            actual_state=ThreadState(data["actual_state"]),
            attention_state=AttentionState(data["attention_state"]),
            revision=int(data["revision"]),
            current_branch_id=data.get("current_branch_id", "main"),
            active_run_id=data.get("active_run_id"),
            waiting_on=tuple(data.get("waiting_on") or ()),
            latest_delivery_id=data.get("latest_delivery_id"),
            last_event_id=data.get("last_event_id"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            metadata=dict(data.get("metadata") or {}),
            runs=runs,
            branches=branches,
            updates=updates,
        )

    def _row_to_event(self, row: sqlite3.Row) -> EventEnvelope:
        return EventEnvelope(
            global_position=int(row["global_position"]),
            sequence=int(row["sequence"]),
            event_id=str(row["event_id"]),
            stream_type=str(row["stream_type"]),
            stream_id=str(row["stream_id"]),
            project_id=row["project_id"],
            thread_id=row["thread_id"],
            branch_id=row["branch_id"],
            run_id=row["run_id"],
            correlation_id=row["correlation_id"],
            causation_id=row["causation_id"],
            event_type=str(row["event_type"]),
            actor=str(row["actor"]),
            occurred_at=str(row["occurred_at"]),
            payload=json.loads(str(row["payload_json"])),
            schema_version=int(row["schema_version"]),
        )

    def _row_to_job(self, row: sqlite3.Row) -> SchedulerJob:
        return SchedulerJob(
            run_id=str(row["run_id"]),
            thread_id=str(row["thread_id"]),
            state=JobState(str(row["state"])),
            priority=int(row["priority"]),
            available_at=str(row["available_at"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            executor_key=str(row["executor_key"]),
            lease_owner=row["lease_owner"],
            lease_id=row["lease_id"],
            fencing_token=int(row["fencing_token"]),
            lease_expires_at=row["lease_expires_at"],
            last_error=row["last_error"],
        )

    def _row_to_step(self, row: sqlite3.Row) -> StepRecord:
        return StepRecord(
            step_id=str(row["step_id"]),
            thread_id=str(row["thread_id"]),
            run_id=str(row["run_id"]),
            ordinal=int(row["ordinal"]),
            kind=StepKind(str(row["kind"])),
            state=StepState(str(row["state"])),
            attempt=int(row["attempt"]),
            provider_key=str(row["provider_key"]),
            provider_version=str(row["provider_version"]),
            input_artifact_ids=tuple(json.loads(str(row["input_artifact_ids_json"]))),
            output_artifact_ids=tuple(json.loads(str(row["output_artifact_ids_json"]))),
            budget=dict(json.loads(str(row["budget_json"]))),
            config_snapshot_id=row["config_snapshot_id"],
            verifier_result_artifact_id=row["verifier_result_artifact_id"],
            error_code=row["error_code"],
            error=row["error"],
            created_at=str(row["created_at"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    def _row_to_delivery(self, row: sqlite3.Row) -> DeliveryRecord:
        return DeliveryRecord(
            delivery_id=str(row["delivery_id"]),
            thread_id=str(row["thread_id"]),
            run_id=str(row["run_id"]),
            version=int(row["version"]),
            state=DeliveryState(str(row["state"])),
            summary_artifact_id=str(row["summary_artifact_id"]),
            primary_artifact_id=str(row["primary_artifact_id"]),
            supporting_artifact_ids=tuple(json.loads(str(row["supporting_artifact_ids_json"]))),
            source_refs=tuple(json.loads(str(row["source_refs_json"]))),
            verifier_result_artifact_id=str(row["verifier_result_artifact_id"]),
            previous_delivery_id=row["previous_delivery_id"],
            allowed_decisions=tuple(json.loads(str(row["allowed_decisions_json"]))),
            sensitivity=str(row["sensitivity"]),
            export_policy=str(row["export_policy"]),
            created_at=str(row["created_at"]),
            presented_at=row["presented_at"],
            decided_at=row["decided_at"],
        )

    def _row_to_outbox(self, row: sqlite3.Row) -> OutboxItem:
        return OutboxItem(
            outbox_id=str(row["outbox_id"]),
            idempotency_key=str(row["idempotency_key"]),
            thread_id=str(row["thread_id"]),
            run_id=str(row["run_id"]),
            kind=str(row["kind"]),
            channel=str(row["channel"]),
            destination=str(row["destination"]),
            payload_artifact_id=str(row["payload_artifact_id"]),
            state=OutboxState(str(row["state"])),
            attempts=int(row["attempts"]),
            available_at=str(row["available_at"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            lease_owner=row["lease_owner"],
            lease_id=row["lease_id"],
            lease_expires_at=row["lease_expires_at"],
            receipt_artifact_id=row["receipt_artifact_id"],
            last_error=row["last_error"],
        )

    def _row_to_resource_lease(self, row: sqlite3.Row) -> ResourceLease:
        return ResourceLease(
            lease_id=str(row["lease_id"]),
            resource_key=str(row["resource_key"]),
            mode=AccessMode(str(row["mode"])),
            owner_run_id=str(row["owner_run_id"]),
            thread_id=str(row["thread_id"]),
            fencing_token=int(row["fencing_token"]),
            acquired_at=str(row["acquired_at"]),
            expires_at=str(row["expires_at"]),
        )

    def _row_to_action_intent(self, row: sqlite3.Row) -> ActionIntent:
        return ActionIntent(
            intent_id=str(row["intent_id"]),
            idempotency_key=str(row["idempotency_key"]),
            thread_id=str(row["thread_id"]),
            run_id=str(row["run_id"]),
            capability=str(row["capability"]),
            request_artifact_id=str(row["request_artifact_id"]),
            payload_hash=str(row["payload_hash"]),
            status=ActionStatus(str(row["status"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            requires_approval=bool(row["requires_approval"]),
            fencing_token=int(row["fencing_token"]),
            lease_owner=row["lease_owner"],
            lease_id=row["lease_id"],
            lease_expires_at=row["lease_expires_at"],
            last_error=row["last_error"],
        )

    def _row_to_action_receipt(self, row: sqlite3.Row) -> ActionReceipt:
        return ActionReceipt(
            receipt_id=str(row["receipt_id"]),
            intent_id=str(row["intent_id"]),
            outcome=ReceiptOutcome(str(row["outcome"])),
            provider=str(row["provider"]),
            occurred_at=str(row["occurred_at"]),
            response_artifact_id=row["response_artifact_id"],
            external_reference=row["external_reference"],
            evidence=json.loads(str(row["evidence_json"])),
        )

    def _row_to_inbox_record(self, row: sqlite3.Row) -> InboxRecord:
        return InboxRecord(
            message_key=str(row["message_key"]),
            payload_hash=str(row["payload_hash"]),
            platform=str(row["platform"]),
            message_id=str(row["message_id"]),
            chat_id=str(row["chat_id"]),
            actor_id=str(row["actor_id"]),
            project_id=str(row["project_id"]),
            message_artifact_id=(
                str(row["message_artifact_id"])
                if row["message_artifact_id"]
                else str(row["text_artifact_id"])
            ),
            text_artifact_id=str(row["text_artifact_id"]),
            route_type=InboxRouteType(str(row["route_type"])),
            route_state=InboxRouteState(str(row["route_state"])),
            thread_id=row["thread_id"],
            confidence=float(row["confidence"]),
            rationale=str(row["rationale"]),
            domain=str(row["domain"]),
            requires_confirmation=bool(row["requires_confirmation"]),
            created_at=str(row["created_at"]),
            update_kind=(ThreadUpdateKind(str(row["update_kind"])) if row["update_kind"] else None),
            candidate_thread_ids=tuple(
                str(item) for item in json.loads(str(row["candidate_thread_ids_json"] or "[]"))
            ),
            updated_at=str(row["updated_at"] or row["created_at"]),
            resolved_by=row["resolved_by"],
            resolution_reason=row["resolution_reason"],
        )

    def _row_to_approval(self, row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(
            approval_id=str(row["approval_id"]),
            intent_id=str(row["intent_id"]),
            status=ApprovalState(str(row["status"])),
            risk_level=str(row["risk_level"]),
            requested_by=str(row["requested_by"]),
            reason=str(row["reason"]),
            policy_snapshot_id=row["policy_snapshot_id"],
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            updated_at=str(row["updated_at"]),
            resolved_by=row["resolved_by"],
            resolved_at=row["resolved_at"],
            decision_evidence_artifact_id=row["decision_evidence_artifact_id"],
        )

    def _set_thread_attention_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        thread_id: str,
        target: AttentionState,
        actor: str,
        reason: str,
        occurred_at: str,
    ) -> None:
        projection = self._get_thread_in_transaction(connection, thread_id)
        if projection is None:
            raise NotFound(f"thread not found: {thread_id}")
        if projection.attention_state == target:
            return
        event = self._insert_event(
            connection,
            EventDraft(
                stream_type="thread",
                stream_id=thread_id,
                project_id=projection.project_id,
                thread_id=thread_id,
                branch_id=projection.current_branch_id,
                run_id=projection.active_run_id,
                event_type="thread.attention_changed",
                actor=actor,
                occurred_at=occurred_at,
                payload={
                    "from": projection.attention_state.value,
                    "to": target.value,
                    "reason": reason,
                },
            ),
        )
        projection = reduce_thread(projection, event)
        self._upsert_thread_projection(connection, projection)

    def _expire_approval_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        now: str,
    ) -> ApprovalRequest:
        connection.execute(
            """
            UPDATE runtime_approvals
            SET status = ?, updated_at = ?, resolved_at = ?
            WHERE approval_id = ? AND status = ?
            """,
            (
                ApprovalState.EXPIRED.value,
                now,
                now,
                row["approval_id"],
                ApprovalState.PENDING.value,
            ),
        )
        connection.execute(
            """
            UPDATE action_intents
            SET status = ?, last_error = ?, updated_at = ?
            WHERE intent_id = ? AND status = ?
            """,
            (
                ActionStatus.CANCELLED.value,
                "approval expired",
                now,
                row["intent_id"],
                ActionStatus.PENDING.value,
            ),
        )
        intent_row = connection.execute(
            "SELECT * FROM action_intents WHERE intent_id = ?", (row["intent_id"],)
        ).fetchone()
        assert intent_row is not None
        self._insert_event(
            connection,
            EventDraft(
                stream_type="approval",
                stream_id=str(row["approval_id"]),
                thread_id=str(intent_row["thread_id"]),
                run_id=str(intent_row["run_id"]),
                event_type="approval.expired",
                actor="approval-gate",
                occurred_at=now,
                payload={
                    "approval_id": row["approval_id"],
                    "intent_id": row["intent_id"],
                },
            ),
        )
        updated = connection.execute(
            "SELECT * FROM runtime_approvals WHERE approval_id = ?", (row["approval_id"],)
        ).fetchone()
        assert updated is not None
        return self._row_to_approval(updated)

    def _assert_live_worker_claim(
        self,
        connection: sqlite3.Connection,
        claim: WorkerClaim,
        now: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM scheduler_jobs WHERE run_id = ?", (claim.run_id,)
        ).fetchone()
        if (
            row is None
            or str(row["state"]) != JobState.CLAIMED.value
            or row["lease_owner"] != claim.worker_id
            or row["lease_id"] != claim.lease_id
            or int(row["fencing_token"]) != claim.fencing_token
            or str(row["lease_expires_at"]) <= now
        ):
            raise StaleLease(
                f"worker claim is stale for run {claim.run_id} token {claim.fencing_token}"
            )
        return row

    def _assert_live_action_claim(
        self,
        connection: sqlite3.Connection,
        claim: ActionClaim,
        now: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM action_intents WHERE intent_id = ?", (claim.intent_id,)
        ).fetchone()
        expected_status = (
            ActionStatus.RECOVERING if claim.reconciliation else ActionStatus.EXECUTING
        )
        if (
            row is None
            or str(row["status"]) != expected_status.value
            or row["lease_owner"] != claim.worker_id
            or row["lease_id"] != claim.lease_id
            or int(row["fencing_token"]) != claim.fencing_token
            or str(row["lease_expires_at"]) <= now
        ):
            raise StaleLease(
                f"action claim is stale for {claim.intent_id} token {claim.fencing_token}"
            )
        return row

    def _mark_action_reconcile_required(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        now: str,
        reason: str,
    ) -> None:
        connection.execute(
            """
            UPDATE action_intents
            SET status = ?, lease_owner = NULL, lease_id = NULL,
                lease_expires_at = NULL, last_error = ?, updated_at = ?
            WHERE intent_id = ?
            """,
            (
                ActionStatus.RECONCILE_REQUIRED.value,
                reason,
                now,
                row["intent_id"],
            ),
        )
        self._insert_event(
            connection,
            EventDraft(
                stream_type="action",
                stream_id=str(row["intent_id"]),
                thread_id=str(row["thread_id"]),
                run_id=str(row["run_id"]),
                event_type="action.reconciliation_required",
                actor="recovery",
                occurred_at=now,
                payload={
                    "reason": reason,
                    "previous_status": str(row["status"]),
                    "lease_id": row["lease_id"],
                    "fencing_token": int(row["fencing_token"]),
                },
            ).normalized(),
        )

    def _fail_exhausted_worker_claims(
        self,
        connection: sqlite3.Connection,
        now: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM scheduler_jobs
            WHERE state = ? AND lease_expires_at <= ? AND attempts >= max_attempts
            """,
            (JobState.CLAIMED.value, now),
        ).fetchall()
        for row in rows:
            error = "worker lease expired after final attempt"
            connection.execute(
                """
                UPDATE scheduler_jobs
                SET state = ?, lease_owner = NULL, lease_id = NULL,
                    lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (JobState.FAILED.value, error, now, row["run_id"]),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="scheduler-run",
                    stream_id=str(row["run_id"]),
                    thread_id=str(row["thread_id"]),
                    run_id=str(row["run_id"]),
                    event_type="scheduler.run_failed",
                    actor="scheduler-recovery",
                    occurred_at=now,
                    payload={
                        "lease_id": row["lease_id"],
                        "fencing_token": int(row["fencing_token"]),
                        "attempt": int(row["attempts"]),
                        "error": error,
                    },
                ).normalized(),
            )

    def _assert_live_resource_lease(
        self,
        connection: sqlite3.Connection,
        lease: ResourceLease,
        now: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM resource_leases WHERE lease_id = ?", (lease.lease_id,)
        ).fetchone()
        if (
            row is None
            or str(row["status"]) != "active"
            or str(row["resource_key"]) != lease.resource_key
            or str(row["owner_run_id"]) != lease.owner_run_id
            or int(row["fencing_token"]) != lease.fencing_token
            or str(row["expires_at"]) <= now
        ):
            raise StaleLease(
                f"resource lease is stale for {lease.resource_key} token {lease.fencing_token}"
            )
        return row

    def _expire_resource_leases(
        self,
        connection: sqlite3.Connection,
        resource_key: str,
        now: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT * FROM resource_leases
            WHERE resource_key = ? AND status = 'active' AND expires_at <= ?
            """,
            (resource_key, now),
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE resource_leases SET status = 'expired', released_at = ? WHERE lease_id = ?",
                (now, row["lease_id"]),
            )
            self._insert_event(
                connection,
                EventDraft(
                    stream_type="resource",
                    stream_id=resource_key,
                    thread_id=str(row["thread_id"]),
                    run_id=str(row["owner_run_id"]),
                    event_type="resource.lease_expired",
                    actor="resource-coordinator",
                    occurred_at=now,
                    payload={
                        "lease_id": row["lease_id"],
                        "fencing_token": int(row["fencing_token"]),
                    },
                ).normalized(),
            )
