from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from super_agent_runtime import (
    AgentSnapshot,
    ArtifactCAS,
    IdempotencyConflict,
    InboxCoordinator,
    InboxMessage,
    InboxRouteState,
    InboxRouteType,
    RoutingContext,
    SnapshotStore,
    SQLiteRuntimeRepository,
)

NOW = "2026-08-03T08:00:00.000000Z"


def _inbox(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    artifacts = ArtifactCAS(tmp_path / "artifacts")
    snapshots = SnapshotStore(artifacts)
    agent = snapshots.put_agent(
        AgentSnapshot(
            agent_id="copenguin-default",
            model_profile={"name": "test-model"},
            tool_registry={},
            capability_manifest={},
            created_at=NOW,
        )
    )
    coordinator = InboxCoordinator(
        repository=repository,
        artifacts=artifacts,
        snapshots=snapshots,
        agent_snapshot_id=agent.artifact_id,
    )
    return repository, coordinator


def _message(message_id: str, text: str) -> InboxMessage:
    return InboxMessage(
        platform="local",
        message_id=message_id,
        chat_id="chat-1",
        actor_id="owner",
        text=text,
        created_at=NOW,
    )


def test_explicit_task_becomes_durable_thread_with_frozen_context(tmp_path) -> None:
    repository, inbox = _inbox(tmp_path)
    context = RoutingContext(project_id="work")

    first = inbox.receive(_message("message-1", "/task 创建一份项目周报"), context)
    retry = inbox.receive(_message("message-1", "/task 创建一份项目周报"), context)

    assert retry == first
    assert first.route_type == InboxRouteType.NEW_TASK
    assert first.route_state == InboxRouteState.CONFIRMED
    assert first.thread_id is not None
    thread = repository.get_thread(first.thread_id)
    run = thread.runs[0]
    assert run.task_snapshot_id is not None
    assert run.agent_snapshot_id is not None
    assert run.context_manifest_id is not None
    assert len(repository.list_inbox_records()) == 1
    event_types = [
        event.event_type for event in repository.list_events(correlation_id=first.message_key)
    ]
    assert event_types.count("conversation.message_received") == 1
    assert event_types.count("inbox.route_proposed") == 1
    assert event_types.count("inbox.route_confirmed") == 1


def test_continuation_updates_current_thread_instead_of_creating_new_task(tmp_path) -> None:
    repository, inbox = _inbox(tmp_path)
    first = inbox.receive(
        _message("message-1", "/task 先完成方案 A"),
        RoutingContext(project_id="work"),
    )
    assert first.thread_id is not None
    context = RoutingContext(project_id="work", current_thread_id=first.thread_id)

    record = inbox.receive(_message("message-2", "继续下一阶段，并换个方式处理"), context)

    assert record.route_type == InboxRouteType.THREAD_UPDATE
    assert record.thread_id == first.thread_id
    thread = repository.get_thread(first.thread_id)
    assert len(thread.updates) == 1
    assert len(thread.runs) == 2
    assert thread.current_branch_id.startswith("branch-")


def test_ambiguous_continuation_requests_confirmation(tmp_path) -> None:
    _, inbox = _inbox(tmp_path)
    context = RoutingContext(
        project_id="work",
        active_thread_ids=("thread-a", "thread-b"),
    )

    record = inbox.receive(_message("message-3", "继续刚才那个方案"), context)

    assert record.route_type == InboxRouteType.AMBIGUOUS
    assert record.route_state == InboxRouteState.PROPOSED
    assert record.thread_id is None
    assert record.requires_confirmation


def test_ordinary_question_remains_chat_and_control_stays_control(tmp_path) -> None:
    _, inbox = _inbox(tmp_path)
    context = RoutingContext(project_id="life")

    chat = inbox.receive(_message("message-4", "你怎么看这个想法？"), context)
    control = inbox.receive(_message("message-5", "/approve approval-1"), context)

    assert chat.route_type == InboxRouteType.CHAT
    assert control.route_type == InboxRouteType.CONTROL


def test_restart_retry_returns_prior_route_without_recomputing_context(tmp_path) -> None:
    first_repository, first_inbox = _inbox(tmp_path)
    first = first_inbox.accept(
        _message("message-restart", "继续刚才那个方案"),
        RoutingContext(project_id="work", active_thread_ids=("thread-a", "thread-b")),
    )

    second_repository, second_inbox = _inbox(tmp_path)
    retry = second_inbox.accept(
        _message("message-restart", "继续刚才那个方案"),
        RoutingContext(project_id="other", current_thread_id="thread-new"),
    )

    assert first.accepted_new
    assert retry.duplicate
    assert retry.record == first.record
    assert retry.record.route_type == InboxRouteType.AMBIGUOUS
    assert len(second_repository.list_inbox_records()) == 1
    assert len(second_repository.list_events(correlation_id="local:message-restart")) == 2
    assert first_repository.list_threads() == []


def test_reusing_message_key_with_different_payload_is_rejected(tmp_path) -> None:
    repository, inbox = _inbox(tmp_path)
    context = RoutingContext(project_id="work")
    inbox.receive(_message("message-collision", "第一条消息"), context)

    with pytest.raises(IdempotencyConflict):
        inbox.receive(_message("message-collision", "被替换的消息"), context)

    assert len(repository.list_inbox_records()) == 1


def test_concurrent_retries_commit_one_route_and_one_task(tmp_path) -> None:
    _, first_inbox = _inbox(tmp_path)
    _, second_inbox = _inbox(tmp_path)
    message = _message("message-concurrent", "/task 整理一份来源报告")
    context = RoutingContext(project_id="work")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda coordinator: coordinator.accept(message, context),
                (first_inbox, second_inbox),
            )
        )

    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    assert sorted(result.accepted_new for result in results) == [False, True]
    assert len(repository.list_inbox_records()) == 1
    assert len(repository.list_threads()) == 1
    thread = repository.list_threads()[0]
    assert len(thread.runs) == 1
    assert len(repository.list_events(correlation_id=message.message_key)) == 9


def test_inbox_route_and_first_task_roll_back_together(tmp_path, monkeypatch) -> None:
    repository, inbox = _inbox(tmp_path)

    def fail_submission(*args, **kwargs):
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(repository, "_submit_task_in_transaction", fail_submission)

    with pytest.raises(RuntimeError, match="injected transaction failure"):
        inbox.receive(
            _message("message-rollback", "/task 生成一份材料摘要"),
            RoutingContext(project_id="work"),
        )

    assert repository.list_inbox_records() == []
    assert repository.list_threads() == []
    assert repository.list_events(correlation_id="local:message-rollback") == []


def test_v2_001_migration_reads_legacy_inbox_rows(tmp_path) -> None:
    database_path = tmp_path / "runtime.db"
    text_artifact = ArtifactCAS(tmp_path / "artifacts").put_text(
        "legacy message",
        kind="inbox_message",
    )
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE inbox_messages (
                message_key TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                message_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                text_artifact_id TEXT NOT NULL,
                route_type TEXT NOT NULL,
                thread_id TEXT,
                confidence REAL NOT NULL,
                rationale TEXT NOT NULL,
                domain TEXT NOT NULL,
                requires_confirmation INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO inbox_messages(
                message_key, platform, message_id, chat_id, actor_id,
                text_artifact_id, route_type, thread_id, confidence,
                rationale, domain, requires_confirmation, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "local:legacy",
                "local",
                "legacy",
                "chat-legacy",
                "owner",
                text_artifact.artifact_id,
                "ambiguous",
                None,
                1.0,
                "legacy route",
                "life",
                1,
                NOW,
            ),
        )

    repository = SQLiteRuntimeRepository(database_path)
    migrated = repository.find_inbox_record("local:legacy")

    assert migrated is not None
    assert migrated.payload_hash == ""
    assert migrated.project_id == ""
    assert migrated.route_state == InboxRouteState.PROPOSED
    assert migrated.message_artifact_id == text_artifact.artifact_id
