from __future__ import annotations

from dataclasses import replace
from typing import Any

from .errors import InvalidTransition
from .models import (
    AttentionState,
    BranchProjection,
    BranchStatus,
    DesiredState,
    EventEnvelope,
    RunProjection,
    RunState,
    ThreadProjection,
    ThreadState,
    ThreadUpdateKind,
    ThreadUpdateProjection,
)

THREAD_TRANSITIONS: dict[ThreadState, frozenset[ThreadState]] = {
    ThreadState.CREATED: frozenset(
        {ThreadState.DORMANT, ThreadState.QUEUED, ThreadState.RUNNING, ThreadState.CANCELLED}
    ),
    ThreadState.DORMANT: frozenset(
        {ThreadState.QUEUED, ThreadState.RUNNING, ThreadState.CANCELLED, ThreadState.ARCHIVED}
    ),
    ThreadState.QUEUED: frozenset(
        {
            ThreadState.RUNNING,
            ThreadState.WAITING_RESOURCE,
            ThreadState.PAUSED,
            ThreadState.FAILED,
            ThreadState.CANCELLED,
        }
    ),
    ThreadState.RUNNING: frozenset(
        {
            ThreadState.DORMANT,
            ThreadState.QUEUED,
            ThreadState.WAITING_USER,
            ThreadState.WAITING_APPROVAL,
            ThreadState.WAITING_RECEIPT,
            ThreadState.WAITING_DEPENDENCY,
            ThreadState.WAITING_RESOURCE,
            ThreadState.VERIFYING,
            ThreadState.DELIVERED,
            ThreadState.FAILED,
            ThreadState.PAUSED,
            ThreadState.CANCELLED,
        }
    ),
    ThreadState.WAITING_USER: frozenset(
        {
            ThreadState.QUEUED,
            ThreadState.RUNNING,
            ThreadState.PAUSED,
            ThreadState.FAILED,
            ThreadState.CANCELLED,
        }
    ),
    ThreadState.WAITING_APPROVAL: frozenset(
        {
            ThreadState.QUEUED,
            ThreadState.RUNNING,
            ThreadState.PAUSED,
            ThreadState.FAILED,
            ThreadState.CANCELLED,
        }
    ),
    ThreadState.WAITING_RECEIPT: frozenset(
        {
            ThreadState.QUEUED,
            ThreadState.RUNNING,
            ThreadState.VERIFYING,
            ThreadState.PAUSED,
            ThreadState.FAILED,
            ThreadState.CANCELLED,
        }
    ),
    ThreadState.WAITING_DEPENDENCY: frozenset(
        {ThreadState.QUEUED, ThreadState.RUNNING, ThreadState.FAILED, ThreadState.CANCELLED}
    ),
    ThreadState.WAITING_RESOURCE: frozenset(
        {ThreadState.QUEUED, ThreadState.RUNNING, ThreadState.FAILED, ThreadState.CANCELLED}
    ),
    ThreadState.VERIFYING: frozenset(
        {
            ThreadState.QUEUED,
            ThreadState.RUNNING,
            ThreadState.DELIVERED,
            ThreadState.FAILED,
            ThreadState.PAUSED,
            ThreadState.CANCELLED,
        }
    ),
    ThreadState.DELIVERED: frozenset(
        {ThreadState.QUEUED, ThreadState.RUNNING, ThreadState.ARCHIVED}
    ),
    ThreadState.FAILED: frozenset(
        {ThreadState.QUEUED, ThreadState.RUNNING, ThreadState.CANCELLED, ThreadState.ARCHIVED}
    ),
    ThreadState.PAUSED: frozenset(
        {ThreadState.QUEUED, ThreadState.RUNNING, ThreadState.CANCELLED, ThreadState.ARCHIVED}
    ),
    ThreadState.CANCELLED: frozenset({ThreadState.ARCHIVED}),
    ThreadState.ARCHIVED: frozenset(),
}


RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.QUEUED, RunState.RUNNING, RunState.CANCELLED}),
    RunState.QUEUED: frozenset(
        {RunState.RUNNING, RunState.WAITING_RESOURCE, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.RUNNING: frozenset(
        {
            RunState.QUEUED,
            RunState.WAITING_USER,
            RunState.WAITING_APPROVAL,
            RunState.WAITING_RECEIPT,
            RunState.WAITING_DEPENDENCY,
            RunState.WAITING_RESOURCE,
            RunState.VERIFYING,
            RunState.COMPLETED,
            RunState.PARTIAL,
            RunState.FAILED,
            RunState.QUARANTINED,
            RunState.CANCELLED,
        }
    ),
    RunState.WAITING_USER: frozenset(
        {RunState.RUNNING, RunState.FAILED, RunState.QUARANTINED, RunState.CANCELLED}
    ),
    RunState.WAITING_APPROVAL: frozenset(
        {RunState.RUNNING, RunState.FAILED, RunState.QUARANTINED, RunState.CANCELLED}
    ),
    RunState.WAITING_RECEIPT: frozenset(
        {
            RunState.RUNNING,
            RunState.VERIFYING,
            RunState.FAILED,
            RunState.QUARANTINED,
            RunState.CANCELLED,
        }
    ),
    RunState.WAITING_DEPENDENCY: frozenset(
        {RunState.QUEUED, RunState.RUNNING, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.WAITING_RESOURCE: frozenset(
        {RunState.QUEUED, RunState.RUNNING, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.VERIFYING: frozenset(
        {
            RunState.RUNNING,
            RunState.COMPLETED,
            RunState.PARTIAL,
            RunState.FAILED,
            RunState.QUARANTINED,
            RunState.CANCELLED,
        }
    ),
    RunState.COMPLETED: frozenset(),
    RunState.PARTIAL: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.QUARANTINED: frozenset(),
    RunState.CANCELLED: frozenset(),
}


def _required(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise InvalidTransition(f"event payload is missing required field: {key}")
    return payload[key]


def _replace_run(
    projection: ThreadProjection,
    run: RunProjection,
) -> tuple[RunProjection, ...]:
    runs = [item for item in projection.runs if item.run_id != run.run_id]
    runs.append(run)
    return tuple(sorted(runs, key=lambda item: item.run_id))


def _replace_branch(
    projection: ThreadProjection,
    branch: BranchProjection,
) -> tuple[BranchProjection, ...]:
    branches = [item for item in projection.branches if item.branch_id != branch.branch_id]
    branches.append(branch)
    return tuple(sorted(branches, key=lambda item: item.branch_id))


def reduce_thread(
    projection: ThreadProjection | None,
    event: EventEnvelope,
) -> ThreadProjection:
    """Pure reducer for the complete Thread + Run aggregate."""
    payload = dict(event.payload)

    if event.event_type == "thread.created":
        if projection is not None:
            raise InvalidTransition("thread.created can only be the first event")
        if event.thread_id != event.stream_id:
            raise InvalidTransition("thread stream id must equal thread_id")
        return ThreadProjection(
            thread_id=event.stream_id,
            project_id=str(_required(payload, "project_id")),
            title=str(_required(payload, "title")),
            desired_state=DesiredState(payload.get("desired_state", DesiredState.RUN)),
            actual_state=ThreadState.CREATED,
            attention_state=AttentionState.NONE,
            revision=event.sequence,
            current_branch_id=str(payload.get("branch_id", "main")),
            last_event_id=event.event_id,
            created_at=event.occurred_at,
            updated_at=event.occurred_at,
            metadata=dict(payload.get("metadata") or {}),
        )

    if projection is None:
        raise InvalidTransition(f"{event.event_type} cannot be applied before thread.created")

    if event.event_type == "thread.state_changed":
        thread_source = ThreadState(_required(payload, "from"))
        thread_target = ThreadState(_required(payload, "to"))
        if projection.actual_state != thread_source:
            raise InvalidTransition(
                f"thread state is {projection.actual_state}, event expected {thread_source}"
            )
        if thread_target not in THREAD_TRANSITIONS[thread_source]:
            raise InvalidTransition(
                f"thread transition {thread_source} -> {thread_target} is not allowed"
            )
        waiting_on = tuple(str(item) for item in payload.get("waiting_on", ()))
        return replace(
            projection,
            actual_state=thread_target,
            waiting_on=waiting_on,
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    if event.event_type == "thread.desired_state_changed":
        return replace(
            projection,
            desired_state=DesiredState(_required(payload, "to")),
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    if event.event_type == "thread.attention_changed":
        return replace(
            projection,
            attention_state=AttentionState(_required(payload, "to")),
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    if event.event_type == "thread.message_appended":
        update_id = str(_required(payload, "update_id"))
        if projection.update(update_id) is not None:
            raise InvalidTransition(f"thread update already exists: {update_id}")
        update = ThreadUpdateProjection(
            update_id=update_id,
            message_key=str(_required(payload, "message_key")),
            message_artifact_id=str(_required(payload, "message_artifact_id")),
            text_artifact_id=str(_required(payload, "text_artifact_id")),
            kind=ThreadUpdateKind(_required(payload, "update_kind")),
            actor_id=str(_required(payload, "actor_id")),
            branch_id=str(
                event.branch_id or payload.get("branch_id") or projection.current_branch_id
            ),
            occurred_at=event.occurred_at,
            task_snapshot_id=payload.get("task_snapshot_id"),
            context_manifest_id=payload.get("context_manifest_id"),
            new_run_id=payload.get("new_run_id"),
        )
        return replace(
            projection,
            updates=(*projection.updates, update),
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    if event.event_type == "thread.branch_forked":
        branch_id = str(event.branch_id or _required(payload, "branch_id"))
        if projection.branch(branch_id) is not None:
            raise InvalidTransition(f"branch already exists: {branch_id}")
        source_branch_id = str(payload.get("forked_from_branch_id") or projection.current_branch_id)
        branches = projection.branches
        if projection.branch(source_branch_id) is None:
            source = BranchProjection(
                branch_id=source_branch_id,
                status=BranchStatus.SELECTED,
                created_by="runtime-migration",
                created_at=projection.created_at,
                selected_at=projection.created_at,
            )
            branches = tuple(sorted((*branches, source), key=lambda item: item.branch_id))
        branch = BranchProjection(
            branch_id=branch_id,
            status=BranchStatus.ACTIVE,
            created_by=event.actor,
            created_at=event.occurred_at,
            forked_from_branch_id=source_branch_id,
            forked_from_event_id=str(_required(payload, "forked_from_event_id")),
            base_snapshot_hash=str(_required(payload, "base_snapshot_hash")),
            reason_code=str(_required(payload, "reason_code")),
        )
        return replace(
            projection,
            branches=tuple(sorted((*branches, branch), key=lambda item: item.branch_id)),
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    if event.event_type == "thread.branch_selected":
        branch_id = str(event.branch_id or _required(payload, "branch_id"))
        target = projection.branch(branch_id)
        if target is None:
            raise InvalidTransition(f"branch does not exist: {branch_id}")
        branches = tuple(
            replace(
                item,
                status=(
                    BranchStatus.SELECTED
                    if item.branch_id == branch_id
                    else BranchStatus.ACTIVE
                    if item.status == BranchStatus.SELECTED
                    else item.status
                ),
                selected_at=(
                    event.occurred_at if item.branch_id == branch_id else item.selected_at
                ),
            )
            for item in projection.branches
        )
        return replace(
            projection,
            current_branch_id=branch_id,
            branches=branches,
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    if event.event_type == "thread.branch_rejected":
        branch_id = str(event.branch_id or _required(payload, "branch_id"))
        target = projection.branch(branch_id)
        if target is None:
            raise InvalidTransition(f"branch does not exist: {branch_id}")
        if target.status == BranchStatus.SELECTED:
            raise InvalidTransition("the selected branch cannot be rejected")
        rejected = replace(
            target,
            status=BranchStatus.REJECTED,
            rejected_at=event.occurred_at,
        )
        return replace(
            projection,
            branches=_replace_branch(projection, rejected),
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    if event.event_type == "run.created":
        run_id = str(event.run_id or _required(payload, "run_id"))
        if projection.run(run_id) is not None:
            raise InvalidTransition(f"run already exists: {run_id}")
        new_run = RunProjection(
            run_id=run_id,
            branch_id=str(event.branch_id or payload.get("branch_id") or "main"),
            executor_key=str(payload.get("executor_key") or "unassigned"),
            created_at=event.occurred_at,
            created_sequence=event.sequence,
            supersedes_run_id=payload.get("supersedes_run_id"),
        )
        return replace(
            projection,
            current_branch_id=new_run.branch_id,
            runs=_replace_run(projection, new_run),
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    if event.event_type == "run.state_changed":
        run_id = str(event.run_id or _required(payload, "run_id"))
        existing_run = projection.run(run_id)
        if existing_run is None:
            raise InvalidTransition(f"run does not exist: {run_id}")
        run_source = RunState(_required(payload, "from"))
        run_target = RunState(_required(payload, "to"))
        if existing_run.state != run_source:
            raise InvalidTransition(
                f"run state is {existing_run.state}, event expected {run_source}"
            )
        if run_target not in RUN_TRANSITIONS[run_source]:
            raise InvalidTransition(f"run transition {run_source} -> {run_target} is not allowed")
        terminal = run_target in {
            RunState.COMPLETED,
            RunState.PARTIAL,
            RunState.FAILED,
            RunState.QUARANTINED,
            RunState.CANCELLED,
        }
        updated_run = replace(
            existing_run,
            state=run_target,
            revision=existing_run.revision + 1,
            started_at=event.occurred_at
            if run_target == RunState.RUNNING and not existing_run.started_at
            else existing_run.started_at,
            completed_at=event.occurred_at if terminal else None,
            waiting_on=tuple(str(item) for item in payload.get("waiting_on", ())),
            output_artifact_id=payload.get("output_artifact_id", existing_run.output_artifact_id),
            error=payload.get("error"),
        )
        active_run_id = projection.active_run_id
        if run_target == RunState.RUNNING:
            if active_run_id not in {None, run_id}:
                raise InvalidTransition(
                    f"thread already has active main run {active_run_id}; cannot start {run_id}"
                )
            active_run_id = run_id
        elif terminal and active_run_id == run_id:
            active_run_id = None
        return replace(
            projection,
            active_run_id=active_run_id,
            runs=_replace_run(projection, updated_run),
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    if event.event_type == "run.snapshots_bound":
        run_id = str(event.run_id or _required(payload, "run_id"))
        existing_run = projection.run(run_id)
        if existing_run is None:
            raise InvalidTransition(f"run does not exist: {run_id}")
        if existing_run.state not in {RunState.CREATED, RunState.QUEUED}:
            raise InvalidTransition("snapshots must be bound before a run starts")
        if any(
            (
                existing_run.task_snapshot_id,
                existing_run.agent_snapshot_id,
                existing_run.context_manifest_id,
            )
        ):
            raise InvalidTransition(f"snapshots are already bound to run: {run_id}")
        updated_run = replace(
            existing_run,
            revision=existing_run.revision + 1,
            task_snapshot_id=str(_required(payload, "task_snapshot_id")),
            agent_snapshot_id=str(_required(payload, "agent_snapshot_id")),
            context_manifest_id=str(_required(payload, "context_manifest_id")),
        )
        return replace(
            projection,
            runs=_replace_run(projection, updated_run),
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    if event.event_type == "run.checkpoint_recorded":
        run_id = str(event.run_id or _required(payload, "run_id"))
        existing_run = projection.run(run_id)
        if existing_run is None:
            raise InvalidTransition(f"run does not exist: {run_id}")
        if existing_run.state in {
            RunState.COMPLETED,
            RunState.PARTIAL,
            RunState.FAILED,
            RunState.QUARANTINED,
            RunState.CANCELLED,
        }:
            raise InvalidTransition("terminal runs cannot record checkpoints")
        checkpoint_sequence = int(_required(payload, "checkpoint_sequence"))
        if checkpoint_sequence != existing_run.checkpoint_sequence + 1:
            raise InvalidTransition(
                "checkpoint sequence must be exactly one greater than the current sequence"
            )
        updated_run = replace(
            existing_run,
            revision=existing_run.revision + 1,
            latest_checkpoint_id=str(_required(payload, "checkpoint_id")),
            checkpoint_sequence=checkpoint_sequence,
        )
        return replace(
            projection,
            runs=_replace_run(projection, updated_run),
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    if event.event_type == "delivery.recorded":
        return replace(
            projection,
            latest_delivery_id=str(_required(payload, "delivery_id")),
            revision=event.sequence,
            last_event_id=event.event_id,
            updated_at=event.occurred_at,
        )

    raise InvalidTransition(f"unsupported thread event type: {event.event_type}")
