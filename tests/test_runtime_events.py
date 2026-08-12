from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from super_agent_runtime import (
    AttentionState,
    ConcurrencyConflict,
    EventDraft,
    IdempotencyConflict,
    InvalidTransition,
    RunState,
    SQLiteRuntimeRepository,
    ThreadState,
)


def test_thread_and_run_history_replays_to_identical_projection(tmp_path) -> None:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    thread = repository.create_thread(
        thread_id="thread-1",
        project_id="personal-agent",
        title="Build durable runtime",
        correlation_id="request-1",
    )
    thread = repository.transition_thread(
        thread.thread_id,
        ThreadState.QUEUED,
        expected_revision=thread.revision,
        actor="router",
    )
    thread = repository.create_run(
        thread.thread_id,
        run_id="run-1",
        branch_id="event-sourcing",
        expected_revision=thread.revision,
    )
    thread = repository.transition_run(
        thread.thread_id,
        "run-1",
        RunState.QUEUED,
        expected_revision=thread.revision,
        actor="scheduler",
    )
    thread = repository.transition_thread(
        thread.thread_id,
        ThreadState.RUNNING,
        expected_revision=thread.revision,
        actor="worker-1",
    )
    thread = repository.transition_run(
        thread.thread_id,
        "run-1",
        RunState.RUNNING,
        expected_revision=thread.revision,
        actor="worker-1",
    )
    thread = repository.set_attention(
        thread.thread_id,
        AttentionState.NEEDS_INPUT,
        expected_revision=thread.revision,
        actor="runtime",
        reason="Need a product decision",
    )

    replayed = repository.replay_thread("thread-1")
    events = repository.list_events(thread_id="thread-1")

    assert repository.verify_thread_replay("thread-1")
    assert replayed.projection_hash == thread.projection_hash
    assert replayed.run("run-1") is not None
    assert replayed.run("run-1").state == RunState.RUNNING
    assert [event.sequence for event in events] == list(range(1, 8))
    assert events[0].correlation_id == "request-1"


def test_optimistic_revision_allows_only_one_thread_writer(tmp_path) -> None:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    thread = repository.create_thread(
        thread_id="thread-1", project_id="p1", title="Only one writer"
    )

    def write(target: ThreadState) -> ThreadState:
        return repository.transition_thread(
            "thread-1",
            target,
            expected_revision=thread.revision,
            actor=target.value,
        ).actual_state

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(write, ThreadState.QUEUED),
            executor.submit(write, ThreadState.RUNNING),
        ]

    outcomes: list[ThreadState | type[Exception]] = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except Exception as exc:  # noqa: BLE001 - the assertion checks the exact runtime error
            outcomes.append(type(exc))

    assert sum(isinstance(item, ThreadState) for item in outcomes) == 1
    assert outcomes.count(ConcurrencyConflict) == 1
    assert repository.verify_thread_replay("thread-1")


def test_terminal_run_cannot_be_restarted(tmp_path) -> None:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    thread = repository.create_thread(project_id="p1", title="Terminal run")
    thread = repository.create_run(
        thread.thread_id, run_id="run-1", expected_revision=thread.revision
    )
    thread = repository.transition_run(
        thread.thread_id,
        "run-1",
        RunState.RUNNING,
        expected_revision=thread.revision,
        actor="worker",
    )
    thread = repository.transition_run(
        thread.thread_id,
        "run-1",
        RunState.COMPLETED,
        expected_revision=thread.revision,
        actor="worker",
    )

    with pytest.raises(InvalidTransition):
        repository.transition_run(
            thread.thread_id,
            "run-1",
            RunState.RUNNING,
            expected_revision=thread.revision,
            actor="worker",
        )


def test_event_id_retry_is_idempotent_but_content_reuse_is_rejected(tmp_path) -> None:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    timestamp = "2026-08-02T00:00:00.000000Z"
    draft = EventDraft(
        event_id="event-1",
        stream_type="thread",
        stream_id="thread-1",
        project_id="p1",
        thread_id="thread-1",
        event_type="thread.created",
        actor="user",
        occurred_at=timestamp,
        payload={"project_id": "p1", "title": "Idempotency", "branch_id": "main"},
    )

    first = repository.append_thread_event(draft, expected_revision=0)
    retry = repository.append_thread_event(draft, expected_revision=0)

    assert retry.projection_hash == first.projection_hash
    assert len(repository.list_events(thread_id="thread-1")) == 1

    conflicting = EventDraft(
        event_id="event-1",
        stream_type="thread",
        stream_id="thread-1",
        project_id="p1",
        thread_id="thread-1",
        event_type="thread.created",
        actor="user",
        occurred_at=timestamp,
        payload={"project_id": "p1", "title": "Different title", "branch_id": "main"},
    )
    with pytest.raises(IdempotencyConflict):
        repository.append_thread_event(conflicting, expected_revision=0)


def test_sidebar_projection_can_filter_attention_without_replaying_history(tmp_path) -> None:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    quiet = repository.create_thread(project_id="p1", title="Quiet")
    needs_input = repository.create_thread(project_id="p1", title="Needs input")
    repository.create_thread(project_id="p2", title="Other project")
    repository.set_attention(
        needs_input.thread_id,
        AttentionState.NEEDS_INPUT,
        expected_revision=needs_input.revision,
        actor="runtime",
    )

    projected = repository.list_threads(project_id="p1", attention_only=True)

    assert quiet.attention_state == AttentionState.NONE
    assert [item.title for item in projected] == ["Needs input"]
