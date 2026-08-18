from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from super_agent_runtime import (
    AccessMode,
    ResourceConflict,
    SQLiteRuntimeRepository,
    StaleLease,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 2, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def test_readers_can_share_resource_but_writer_waits(tmp_path) -> None:
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    first = repository.acquire_resource(
        resource_key="file:/notes/plan.md",
        mode=AccessMode.READ,
        owner_run_id="run-1",
        thread_id="thread-1",
    )
    second = repository.acquire_resource(
        resource_key="file:/notes/plan.md",
        mode=AccessMode.READ,
        owner_run_id="run-2",
        thread_id="thread-2",
    )

    with pytest.raises(ResourceConflict):
        repository.acquire_resource(
            resource_key="file:/notes/plan.md",
            mode=AccessMode.WRITE,
            owner_run_id="run-3",
            thread_id="thread-3",
        )

    repository.release_resource(first)
    repository.release_resource(second)
    writer = repository.acquire_resource(
        resource_key="file:/notes/plan.md",
        mode=AccessMode.WRITE,
        owner_run_id="run-3",
        thread_id="thread-3",
    )

    assert writer.fencing_token > second.fencing_token
    assert repository.active_resource_leases("file:/notes/plan.md") == [writer]


def test_expired_resource_lease_cannot_commit_after_new_owner(tmp_path) -> None:
    clock = FakeClock()
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db", clock=clock)
    old = repository.acquire_resource(
        resource_key="calendar:event-1",
        mode=AccessMode.EXCLUSIVE,
        owner_run_id="run-old",
        thread_id="thread-old",
        lease_seconds=10,
    )
    clock.advance(seconds=11)
    new = repository.acquire_resource(
        resource_key="calendar:event-1",
        mode=AccessMode.EXCLUSIVE,
        owner_run_id="run-new",
        thread_id="thread-new",
    )

    assert new.fencing_token > old.fencing_token
    with pytest.raises(StaleLease):
        repository.release_resource(old)

    resource_events = [
        event
        for event in repository.list_events(run_id="run-old")
        if event.stream_type == "resource"
    ]
    assert [event.event_type for event in resource_events] == [
        "resource.lease_acquired",
        "resource.lease_expired",
    ]
