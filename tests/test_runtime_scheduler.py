from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from super_agent_runtime import JobState, SQLiteRuntimeRepository, StaleLease


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 2, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _thread_with_run(
    repository: SQLiteRuntimeRepository,
    *,
    thread_id: str,
    run_id: str,
) -> None:
    thread = repository.create_thread(
        thread_id=thread_id,
        project_id="p1",
        title=thread_id,
    )
    repository.create_run(thread_id, run_id=run_id, expected_revision=thread.revision)


def test_scheduler_runs_different_threads_in_parallel_but_serializes_same_thread(tmp_path) -> None:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    _thread_with_run(repository, thread_id="thread-a", run_id="run-a1")
    thread_a = repository.get_thread("thread-a")
    repository.create_run("thread-a", run_id="run-a2", expected_revision=thread_a.revision)
    _thread_with_run(repository, thread_id="thread-b", run_id="run-b1")

    repository.enqueue_run(thread_id="thread-a", run_id="run-a1", priority=100)
    repository.enqueue_run(thread_id="thread-a", run_id="run-a2", priority=90)
    repository.enqueue_run(thread_id="thread-b", run_id="run-b1", priority=80)

    first = repository.claim_next_run(worker_id="worker-1")
    second = repository.claim_next_run(worker_id="worker-2")
    third = repository.claim_next_run(worker_id="worker-3")

    assert first is not None and first.run_id == "run-a1"
    assert second is not None and second.run_id == "run-b1"
    assert third is None

    repository.finish_run_claim(first, succeeded=True)
    third = repository.claim_next_run(worker_id="worker-3")

    assert third is not None and third.run_id == "run-a2"


def test_expired_worker_is_fenced_after_another_worker_reclaims_run(tmp_path) -> None:
    clock = FakeClock()
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db", clock=clock)
    _thread_with_run(repository, thread_id="thread-a", run_id="run-a1")
    repository.enqueue_run(thread_id="thread-a", run_id="run-a1")

    first = repository.claim_next_run(worker_id="worker-1", lease_seconds=10)
    assert first is not None
    clock.advance(seconds=11)
    replacement = repository.claim_next_run(worker_id="worker-2", lease_seconds=10)

    assert replacement is not None
    assert replacement.run_id == first.run_id
    assert replacement.fencing_token > first.fencing_token
    with pytest.raises(StaleLease):
        repository.heartbeat_run(first)

    completed = repository.finish_run_claim(replacement, succeeded=True)
    assert completed.state == JobState.COMPLETED


def test_failed_claim_is_retried_only_within_attempt_budget(tmp_path) -> None:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    _thread_with_run(repository, thread_id="thread-a", run_id="run-a1")
    repository.enqueue_run(thread_id="thread-a", run_id="run-a1", max_attempts=2)

    first = repository.claim_next_run(worker_id="worker-1")
    assert first is not None
    retry = repository.finish_run_claim(first, succeeded=False, error="temporary", retryable=True)
    assert retry.state == JobState.QUEUED

    second = repository.claim_next_run(worker_id="worker-2")
    assert second is not None and second.attempt == 2
    failed = repository.finish_run_claim(second, succeeded=False, error="again", retryable=True)
    assert failed.state == JobState.FAILED


def test_expired_final_attempt_is_recovered_to_failed_state(tmp_path) -> None:
    clock = FakeClock()
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db", clock=clock)
    _thread_with_run(repository, thread_id="thread-a", run_id="run-a1")
    repository.enqueue_run(thread_id="thread-a", run_id="run-a1", max_attempts=1)

    claim = repository.claim_next_run(worker_id="worker-1", lease_seconds=10)
    assert claim is not None
    clock.advance(seconds=11)

    assert repository.claim_next_run(worker_id="worker-2") is None
    failed = repository.get_job("run-a1")

    assert failed.state == JobState.FAILED
    assert failed.last_error == "worker lease expired after final attempt"
