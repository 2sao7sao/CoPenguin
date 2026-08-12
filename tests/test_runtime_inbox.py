from __future__ import annotations

from super_agent_runtime import (
    AgentSnapshot,
    ArtifactCAS,
    InboxCoordinator,
    InboxMessage,
    InboxRouteType,
    RoutingContext,
    SnapshotStore,
    SQLiteRuntimeRepository,
    ThreadCoordinator,
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
        threads=ThreadCoordinator(repository, artifacts),
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
    assert first.thread_id is not None
    thread = repository.get_thread(first.thread_id)
    run = thread.runs[0]
    assert run.task_snapshot_id is not None
    assert run.agent_snapshot_id is not None
    assert run.context_manifest_id is not None
    assert len(repository.list_inbox_records()) == 1


def test_continuation_updates_current_thread_instead_of_creating_new_task(tmp_path) -> None:
    repository, inbox = _inbox(tmp_path)
    context = RoutingContext(project_id="work", current_thread_id="thread-current")

    record = inbox.receive(_message("message-2", "继续下一阶段，并换个方式处理"), context)

    assert record.route_type == InboxRouteType.THREAD_UPDATE
    assert record.thread_id == "thread-current"
    assert repository.list_threads() == []


def test_ambiguous_continuation_requests_confirmation(tmp_path) -> None:
    _, inbox = _inbox(tmp_path)
    context = RoutingContext(
        project_id="work",
        active_thread_ids=("thread-a", "thread-b"),
    )

    record = inbox.receive(_message("message-3", "继续刚才那个方案"), context)

    assert record.route_type == InboxRouteType.AMBIGUOUS
    assert record.thread_id is None
    assert record.requires_confirmation


def test_ordinary_question_remains_chat_and_control_stays_control(tmp_path) -> None:
    _, inbox = _inbox(tmp_path)
    context = RoutingContext(project_id="life")

    chat = inbox.receive(_message("message-4", "你怎么看这个想法？"), context)
    control = inbox.receive(_message("message-5", "/approve approval-1"), context)

    assert chat.route_type == InboxRouteType.CHAT
    assert control.route_type == InboxRouteType.CONTROL
