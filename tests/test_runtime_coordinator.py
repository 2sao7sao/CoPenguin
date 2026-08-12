from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from super_agent_runtime import (
    ArtifactCAS,
    JobState,
    SQLiteRuntimeRepository,
    StaleLease,
    ThreadCoordinator,
    ThreadState,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 3, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def test_task_submission_atomically_creates_thread_run_and_job(tmp_path) -> None:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    coordinator = ThreadCoordinator(repository, ArtifactCAS(tmp_path / "artifacts"))

    handle = coordinator.submit_task(
        project_id="personal",
        title="Plan tomorrow",
        thread_id="thread-1",
        run_id="run-1",
        correlation_id="message-1",
        task_snapshot_id="artifact:sha256:" + "1" * 64,
        agent_snapshot_id="artifact:sha256:" + "2" * 64,
        context_manifest_id="artifact:sha256:" + "3" * 64,
    )
    retry = coordinator.submit_task(
        project_id="personal",
        title="Plan tomorrow",
        thread_id="thread-1",
        run_id="run-1",
        correlation_id="message-1",
    )

    thread = repository.get_thread(handle.thread_id)
    run = thread.run(handle.run_id)
    assert retry == handle
    assert thread.actual_state == ThreadState.QUEUED
    assert run is not None and run.task_snapshot_id is not None
    assert repository.get_job(handle.run_id).state == JobState.QUEUED
    assert repository.verify_thread_replay(handle.thread_id)


def test_reclaimed_worker_resumes_latest_checkpoint_and_stale_worker_is_fenced(
    tmp_path,
) -> None:
    clock = FakeClock()
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db", clock=clock)
    coordinator = ThreadCoordinator(repository, ArtifactCAS(tmp_path / "artifacts"))
    handle = coordinator.submit_task(
        project_id="work",
        title="Build report",
        thread_id="thread-1",
        run_id="run-1",
        task_snapshot_id="artifact:sha256:" + "1" * 64,
        agent_snapshot_id="artifact:sha256:" + "2" * 64,
        context_manifest_id="artifact:sha256:" + "3" * 64,
    )
    first = coordinator.claim_next(worker_id="worker-old", lease_seconds=10)
    assert first is not None
    first, checkpoint = coordinator.save_checkpoint(first, {"completed_steps": ["collect"]})

    clock.advance(seconds=11)
    replacement = coordinator.claim_next(worker_id="worker-new", lease_seconds=30)

    assert replacement is not None
    assert replacement.claim.run_id == handle.run_id
    assert replacement.claim.fencing_token > first.claim.fencing_token
    assert replacement.checkpoint_id == checkpoint.artifact_id
    restored = coordinator.load_checkpoint(replacement)
    assert restored is not None
    assert restored["state"] == {"completed_steps": ["collect"]}

    with pytest.raises(StaleLease):
        coordinator.save_checkpoint(first, {"completed_steps": ["collect", "write"]})
