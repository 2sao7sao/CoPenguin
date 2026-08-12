from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from super_agent_runtime import (
    ActionStatus,
    AgentSnapshot,
    ApprovalState,
    ArtifactCAS,
    AttentionState,
    BranchStatus,
    ConcurrencyConflict,
    DesiredState,
    IdempotencyConflict,
    InboxCoordinator,
    InboxMessage,
    InboxRouteState,
    InboxRouteType,
    JobState,
    RoutingContext,
    RunState,
    SnapshotStore,
    SQLiteRuntimeRepository,
    StaleLease,
    ThreadState,
    ThreadUpdateKind,
)

BASE_TIME = datetime(2026, 8, 13, 8, tzinfo=UTC)


def _runtime(tmp_path):
    repository = SQLiteRuntimeRepository(tmp_path / "runtime.db")
    artifacts = ArtifactCAS(tmp_path / "artifacts")
    snapshots = SnapshotStore(artifacts)
    agent = snapshots.put_agent(
        AgentSnapshot(
            agent_id="copenguin-test",
            model_profile={"name": "fixture"},
            tool_registry={},
            capability_manifest={},
            created_at=BASE_TIME.isoformat(),
        )
    )
    inbox = InboxCoordinator(
        repository=repository,
        artifacts=artifacts,
        snapshots=snapshots,
        agent_snapshot_id=agent.artifact_id,
    )
    return repository, artifacts, snapshots, inbox


def _message(index: int, text: str, *, actor_id: str = "owner") -> InboxMessage:
    created_at = (BASE_TIME + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")
    return InboxMessage(
        platform="local",
        message_id=f"message-{index}",
        chat_id="chat-1",
        actor_id=actor_id,
        text=text,
        created_at=created_at,
    )


def _task(inbox: InboxCoordinator, index: int, objective: str):
    record = inbox.receive(
        _message(index, f"/task {objective}"),
        RoutingContext(project_id="work"),
    )
    assert record.thread_id is not None
    return record


def test_supplement_recompiles_context_without_mutating_task_snapshot(tmp_path) -> None:
    repository, _, snapshots, inbox = _runtime(tmp_path)
    task = _task(inbox, 1, "生成一份有来源的项目决策记录")
    before = repository.get_thread(task.thread_id)
    original_run = before.runs[0]

    update = inbox.receive(
        _message(2, f"/thread {task.thread_id} 补充：还要覆盖预算约束"),
        RoutingContext(project_id="work"),
    )

    after = repository.get_thread(task.thread_id)
    appended = after.update(update.message_key)
    assert update.update_kind == ThreadUpdateKind.SUPPLEMENT
    assert appended is not None and appended.new_run_id is not None
    replacement = after.run(appended.new_run_id)
    assert replacement is not None
    assert original_run.task_snapshot_id == replacement.task_snapshot_id
    assert original_run.context_manifest_id != replacement.context_manifest_id
    assert after.run(original_run.run_id).state == RunState.CANCELLED
    assert replacement.state == RunState.QUEUED
    assert repository.get_job(original_run.run_id).state == JobState.CANCELLED
    assert repository.get_job(replacement.run_id).state == JobState.QUEUED
    context = snapshots.load(replacement.context_manifest_id)
    assert [item["source_type"] for item in context["items"]] == ["inbox", "thread_update"]
    assert repository.verify_thread_replay(task.thread_id)


def test_goal_change_creates_new_task_snapshot_and_preserves_original(tmp_path) -> None:
    repository, _, snapshots, inbox = _runtime(tmp_path)
    task = _task(inbox, 10, "整理所有来源")
    original = repository.get_thread(task.thread_id).runs[0]
    original_snapshot = snapshots.load(original.task_snapshot_id)

    inbox.receive(
        _message(11, f"/thread {task.thread_id} 改目标：只输出可执行的未决事项"),
        RoutingContext(project_id="work"),
    )

    thread = repository.get_thread(task.thread_id)
    update = thread.updates[-1]
    replacement = thread.run(update.new_run_id)
    assert update.kind == ThreadUpdateKind.GOAL_CHANGE
    assert replacement is not None
    assert replacement.task_snapshot_id != original.task_snapshot_id
    changed_snapshot = snapshots.load(replacement.task_snapshot_id)
    assert changed_snapshot["objective"] == "改目标：只输出可执行的未决事项"
    assert snapshots.load(original.task_snapshot_id) == original_snapshot
    assert original_snapshot["objective"] == "整理所有来源"


def test_method_change_forks_and_selects_new_branch_without_erasing_old_run(tmp_path) -> None:
    repository, _, _, inbox = _runtime(tmp_path)
    task = _task(inbox, 20, "先按时间线整理项目材料")
    original = repository.get_thread(task.thread_id).runs[0]

    inbox.receive(
        _message(21, f"/thread {task.thread_id} 不如换个方式，按决策主题组织"),
        RoutingContext(project_id="work"),
    )

    thread = repository.get_thread(task.thread_id)
    update = thread.updates[-1]
    replacement = thread.run(update.new_run_id)
    old_branch = thread.branch("main")
    selected = thread.branch(thread.current_branch_id)
    assert update.kind == ThreadUpdateKind.METHOD_CHANGE
    assert replacement is not None and replacement.branch_id == thread.current_branch_id
    assert thread.run(original.run_id).state == RunState.CANCELLED
    assert old_branch is not None and old_branch.status == BranchStatus.ACTIVE
    assert selected is not None and selected.status == BranchStatus.SELECTED
    assert selected.forked_from_branch_id == "main"
    assert selected.forked_from_event_id is not None
    assert selected.base_snapshot_hash is not None
    event_types = [
        event.event_type
        for event in repository.list_events(correlation_id=update.message_key, limit=100)
    ]
    assert "thread.branch_forked" in event_types
    assert "thread.branch_selected" in event_types
    assert repository.verify_thread_replay(task.thread_id)


def test_cancel_update_propagates_to_thread_run_and_scheduler_after_restart(tmp_path) -> None:
    repository, _, _, inbox = _runtime(tmp_path)
    task = _task(inbox, 30, "生成周报")
    run_id = repository.get_thread(task.thread_id).runs[0].run_id

    record = inbox.receive(
        _message(31, f"/thread {task.thread_id} 取消任务"),
        RoutingContext(project_id="work"),
    )

    assert record.update_kind == ThreadUpdateKind.CANCEL
    restarted_repository, _, _, _ = _runtime(tmp_path)
    thread = restarted_repository.get_thread(task.thread_id)
    assert thread.desired_state == DesiredState.CANCEL
    assert thread.actual_state == ThreadState.CANCELLED
    assert thread.run(run_id).state == RunState.CANCELLED
    assert restarted_repository.get_job(run_id).state == JobState.CANCELLED
    assert thread.updates[-1].new_run_id is None
    assert restarted_repository.verify_thread_replay(task.thread_id)


def test_ambiguous_route_has_no_effect_until_owner_resolves_it_once(tmp_path) -> None:
    repository, _, _, inbox = _runtime(tmp_path)
    first = _task(inbox, 40, "任务 A")
    second = _task(inbox, 41, "任务 B")
    before = {
        first.thread_id: repository.get_thread(first.thread_id).revision,
        second.thread_id: repository.get_thread(second.thread_id).revision,
    }
    ambiguous = inbox.receive(
        _message(42, "继续刚才那个方案"),
        RoutingContext(
            project_id="work",
            active_thread_ids=(first.thread_id, second.thread_id),
        ),
    )

    assert ambiguous.route_type == InboxRouteType.AMBIGUOUS
    assert ambiguous.route_state == InboxRouteState.PROPOSED
    assert ambiguous.candidate_thread_ids == (first.thread_id, second.thread_id)
    assert repository.get_thread(first.thread_id).revision == before[first.thread_id]
    assert repository.get_thread(second.thread_id).revision == before[second.thread_id]

    resolved = inbox.resolve_route(
        message_key=ambiguous.message_key,
        platform="local",
        actor_id="owner",
        decision="thread",
        target_thread_id=second.thread_id,
    )
    retry = inbox.resolve_route(
        message_key=ambiguous.message_key,
        platform="local",
        actor_id="owner",
        decision="thread",
        target_thread_id=second.thread_id,
    )

    assert retry == resolved
    assert resolved.route_state == InboxRouteState.CORRECTED
    assert resolved.thread_id == second.thread_id
    assert repository.get_thread(first.thread_id).updates == ()
    assert len(repository.get_thread(second.thread_id).updates) == 1
    events = repository.list_events(correlation_id=ambiguous.message_key, limit=100)
    assert [event.event_type for event in events].count("inbox.route_corrected") == 1
    assert [event.event_type for event in events].count("thread.message_appended") == 1


def test_only_original_actor_can_resolve_route_and_expiry_has_no_task_effect(tmp_path) -> None:
    repository, _, _, inbox = _runtime(tmp_path)
    task = _task(inbox, 50, "任务 A")
    ambiguous = inbox.receive(
        _message(51, "继续刚才那个方案"),
        RoutingContext(project_id="work", active_thread_ids=(task.thread_id, "missing")),
    )

    try:
        inbox.resolve_route(
            message_key=ambiguous.message_key,
            platform="local",
            actor_id="other",
            decision="thread",
            target_thread_id=task.thread_id,
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("another actor unexpectedly resolved the route")

    expired = inbox.resolve_route(
        message_key=ambiguous.message_key,
        platform="local",
        actor_id="owner",
        decision="expire",
    )
    retry = inbox.resolve_route(
        message_key=ambiguous.message_key,
        platform="local",
        actor_id="owner",
        decision="expire",
    )
    assert retry == expired
    assert expired.route_state == InboxRouteState.EXPIRED
    assert repository.get_thread(task.thread_id).updates == ()


def test_conflicting_concurrent_route_decisions_apply_exactly_one_update(tmp_path) -> None:
    repository, _, _, inbox = _runtime(tmp_path)
    first = _task(inbox, 60, "任务 A")
    second = _task(inbox, 61, "任务 B")
    ambiguous = inbox.receive(
        _message(62, "继续刚才那个方案"),
        RoutingContext(
            project_id="work",
            active_thread_ids=(first.thread_id, second.thread_id),
        ),
    )

    def resolve(thread_id: str):
        try:
            return inbox.resolve_route(
                message_key=ambiguous.message_key,
                platform="local",
                actor_id="owner",
                decision="thread",
                target_thread_id=thread_id,
            )
        except (ConcurrencyConflict, IdempotencyConflict) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(resolve, (first.thread_id, second.thread_id)))

    assert sum(not isinstance(result, Exception) for result in results) == 1
    stored = repository.find_inbox_record(ambiguous.message_key)
    assert stored is not None and stored.thread_id in {first.thread_id, second.thread_id}
    update_count = sum(
        len(repository.get_thread(thread_id).updates)
        for thread_id in (first.thread_id, second.thread_id)
    )
    assert update_count == 1


def test_ambiguous_route_can_be_corrected_to_a_new_task(tmp_path) -> None:
    repository, _, _, inbox = _runtime(tmp_path)
    ambiguous = inbox.receive(
        _message(70, "继续完成那份决策记录"),
        RoutingContext(project_id="work", active_thread_ids=("unknown-a", "unknown-b")),
    )

    resolved = inbox.resolve_route(
        message_key=ambiguous.message_key,
        platform="local",
        actor_id="owner",
        decision="new_task",
    )

    assert resolved.route_type == InboxRouteType.NEW_TASK
    assert resolved.route_state == InboxRouteState.CORRECTED
    assert resolved.thread_id is not None
    thread = repository.get_thread(resolved.thread_id)
    assert len(thread.runs) == 1
    assert thread.runs[0].task_snapshot_id is not None
    assert repository.verify_thread_replay(resolved.thread_id)


def test_update_fences_a_running_worker_and_returns_thread_to_queue(tmp_path) -> None:
    repository, _, _, inbox = _runtime(tmp_path)
    task = _task(inbox, 80, "生成项目报告")
    claim = repository.claim_next_run(worker_id="worker-old", lease_seconds=60)
    assert claim is not None
    running = repository.start_claimed_run(claim)
    assert running.actual_state == ThreadState.RUNNING

    inbox.receive(
        _message(81, f"/thread {task.thread_id} 补充：加入最新风险"),
        RoutingContext(project_id="work"),
    )

    updated = repository.get_thread(task.thread_id)
    assert updated.actual_state == ThreadState.QUEUED
    assert updated.run(claim.run_id).state == RunState.CANCELLED
    with pytest.raises(StaleLease):
        repository.heartbeat_run(claim)


def test_cancellation_revokes_pending_action_and_approval_before_provider_execution(
    tmp_path,
) -> None:
    repository, artifacts, _, inbox = _runtime(tmp_path)
    task = _task(inbox, 90, "发布项目记录")
    thread = repository.get_thread(task.thread_id)
    run = thread.runs[0]
    request = artifacts.put_json({"draft": "decision"}, kind="action_request")
    intent = repository.create_action_intent(
        thread_id=task.thread_id,
        run_id=run.run_id,
        capability="feishu.wiki.publish",
        request_artifact_id=request.artifact_id,
        payload_hash=request.sha256,
        idempotency_key="publish:cancel-test",
        requires_approval=True,
    )
    approval = repository.create_approval(
        intent_id=intent.intent_id,
        risk_level="high",
        requested_by="copenguin",
        reason="external write",
    )
    assert repository.get_thread(task.thread_id).attention_state == AttentionState.NEEDS_APPROVAL

    inbox.receive(
        _message(91, f"/thread {task.thread_id} 取消任务"),
        RoutingContext(project_id="work"),
    )

    assert repository.get_action_intent(intent.intent_id).status == ActionStatus.CANCELLED
    assert repository.get_approval(approval.approval_id).status == ApprovalState.CANCELLED
    assert repository.get_thread(task.thread_id).attention_state == AttentionState.NONE
    event_types = [
        event.event_type
        for event in repository.list_events(correlation_id="local:message-91", limit=100)
    ]
    assert "action.cancelled" in event_types
    assert "approval.cancelled" in event_types
