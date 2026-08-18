from fastapi.testclient import TestClient

from copenguin.demo import DEFAULT_SOURCE
from feishu_computer_agent.config import Settings
from feishu_computer_agent.server import create_app
from super_agent_runtime import (
    DecisionRecordVerifier,
    InboxMessage,
    RoutingContext,
    SourceSnapshotBinding,
    SourceToArtifactExecutor,
    WorkerHost,
    WorkerHostConfig,
)


def test_runtime_sidebar_and_detail_endpoints(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    runtime = app.state.runtime
    thread = runtime.create_thread(
        thread_id="thread-1",
        project_id="personal",
        title="Plan tomorrow",
    )
    client = TestClient(app)

    sidebar = client.get("/runtime/threads", params={"project_id": "personal"})
    detail = client.get(f"/runtime/threads/{thread.thread_id}")

    assert sidebar.status_code == 200
    assert sidebar.json()["threads"][0]["thread_id"] == "thread-1"
    assert detail.status_code == 200
    assert detail.json()["replay_verified"] is True


def test_runtime_scheduler_job_endpoint_exposes_executor_routing(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    runtime = app.state.runtime
    thread = runtime.create_thread(thread_id="thread-job", project_id="work", title="Worker job")
    thread = runtime.create_run(
        thread.thread_id,
        run_id="run-job",
        executor_key="fixture-executor",
        expected_revision=thread.revision,
    )
    runtime.enqueue_run(thread_id=thread.thread_id, run_id="run-job")
    client = TestClient(app)

    listing = client.get("/runtime/jobs", params={"executor_key": "fixture-executor"})
    detail = client.get("/runtime/jobs/run-job")

    assert listing.status_code == 200
    assert listing.json()["jobs"][0]["executor_key"] == "fixture-executor"
    assert detail.status_code == 200
    assert detail.json()["job"]["state"] == "queued"


def test_runtime_exposes_verified_steps_delivery_and_outbox(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    artifacts = app.state.artifacts
    source = artifacts.put_json(DEFAULT_SOURCE, kind="api_test_source")
    submitted = app.state.source_tasks.submit(
        project_id="work",
        objective="Create an inspectable decision record",
        sources=(
            SourceSnapshotBinding(
                source_snapshot_id="api-source-1",
                source_ref_id="test:api-source-1",
                revision_id=source.sha256,
                access_envelope_id="local-owner",
                content_artifact_id=source.artifact_id,
            ),
        ),
    )
    result = WorkerHost(
        repository=app.state.runtime,
        artifacts=artifacts,
        executors=(SourceToArtifactExecutor(artifacts),),
        verifiers=(DecisionRecordVerifier(artifacts),),
        config=WorkerHostConfig(worker_id="api-worker"),
    ).run_once()
    assert result is not None and result.delivery_id is not None
    client = TestClient(app, client=("127.0.0.1", 50000))

    steps = client.get(f"/runtime/runs/{submitted.task.run_id}/steps")
    deliveries = client.get(
        "/runtime/deliveries",
        params={"thread_id": submitted.task.thread_id},
    )
    detail = client.get(f"/runtime/deliveries/{result.delivery_id}")
    presented = client.post(f"/runtime/deliveries/{result.delivery_id}/present")
    outbox = client.get("/runtime/outbox", params={"state": "pending"})

    assert steps.status_code == 200
    assert [item["kind"] for item in steps.json()["steps"]] == ["transform", "verifier"]
    assert deliveries.json()["deliveries"][0]["state"] == "prepared"
    assert detail.json()["delivery"]["primary_artifact_id"] == result.output_artifact_id
    assert presented.json()["delivery"]["state"] == "presented"
    assert len(outbox.json()["items"]) == 1


def test_runtime_detail_returns_not_found(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    client = TestClient(app)

    response = client.get("/runtime/threads/missing")

    assert response.status_code == 404


def test_runtime_action_projection_endpoint(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    runtime = app.state.runtime
    thread = runtime.create_thread(thread_id="thread-1", project_id="personal", title="Send draft")
    thread = runtime.create_run(thread.thread_id, run_id="run-1", expected_revision=thread.revision)
    request = app.state.artifacts.put_json({"draft": "hello"}, kind="action_request")
    intent = runtime.create_action_intent(
        thread_id=thread.thread_id,
        run_id="run-1",
        capability="email.draft",
        request_artifact_id=request.artifact_id,
        payload_hash=request.sha256,
        idempotency_key="draft:1",
    )
    client = TestClient(app)

    response = client.get("/runtime/actions", params={"status": "pending"})
    detail = client.get(f"/runtime/actions/{intent.intent_id}")

    assert response.status_code == 200
    assert response.json()["actions"][0]["intent_id"] == intent.intent_id
    assert detail.json()["action"]["status"] == "pending"
    assert detail.json()["receipts"] == []


def test_runtime_inbox_and_approval_projection_endpoints(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    inbox_record = app.state.inbox.receive(
        InboxMessage(
            platform="local",
            message_id="message-1",
            chat_id="chat-1",
            actor_id="owner",
            text="/task 写一份计划",
            created_at="2026-08-03T08:00:00.000000Z",
        ),
        RoutingContext(project_id="personal"),
    )
    assert inbox_record.thread_id is not None
    thread = app.state.runtime.get_thread(inbox_record.thread_id)
    run = thread.runs[0]
    request = app.state.artifacts.put_json({"draft": "hello"}, kind="action_request")
    intent = app.state.runtime.create_action_intent(
        thread_id=thread.thread_id,
        run_id=run.run_id,
        capability="email.send",
        request_artifact_id=request.artifact_id,
        payload_hash=request.sha256,
        idempotency_key="email:api-test",
        requires_approval=True,
    )
    approval = app.state.runtime.create_approval(
        intent_id=intent.intent_id,
        risk_level="high",
        requested_by="copenguin",
        reason="external write",
    )
    client = TestClient(app)

    inbox_response = client.get("/runtime/inbox", params={"route_type": "new_task"})
    approval_response = client.get("/runtime/approvals", params={"status": "pending"})

    assert inbox_response.json()["messages"][0]["thread_id"] == thread.thread_id
    assert approval_response.json()["approvals"][0]["approval_id"] == approval.approval_id


def test_local_ingress_endpoint_deduplicates_retries(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    client = TestClient(app, client=("127.0.0.1", 50000))
    payload = {
        "message_id": "local-ui-1",
        "chat_id": "control-room",
        "actor_id": "owner",
        "project_id": "work",
        "text": "/task 从这些资料生成一份可检查报告",
        "created_at": "2026-08-13T08:00:00Z",
    }

    first = client.post("/runtime/inbox", json=payload)
    retry = client.post("/runtime/inbox", json=payload)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert first.json()["accepted_new"] is True
    assert retry.json()["duplicate"] is True
    assert retry.json()["message"] == first.json()["message"]
    assert retry.json()["message"]["project_id"] == "work"
    assert retry.json()["message"]["route_state"] == "confirmed"
    assert len(app.state.runtime.list_inbox_records()) == 1
    assert len(app.state.runtime.list_threads()) == 1


def test_local_ingress_endpoint_rejects_non_loopback_clients(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    client = TestClient(app, client=("203.0.113.9", 50000))

    response = client.post(
        "/runtime/inbox",
        json={"message_id": "remote-1", "text": "/task should not be accepted"},
    )

    assert response.status_code == 403
    assert app.state.runtime.list_inbox_records() == []


def test_local_route_decision_applies_ambiguous_message_exactly_once(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    client = TestClient(app, client=("127.0.0.1", 50000))

    first = client.post(
        "/runtime/inbox",
        json={
            "message_id": "task-a",
            "chat_id": "control-room",
            "actor_id": "owner",
            "project_id": "work",
            "text": "/task 任务 A",
        },
    ).json()["message"]
    second = client.post(
        "/runtime/inbox",
        json={
            "message_id": "task-b",
            "chat_id": "control-room",
            "actor_id": "owner",
            "project_id": "work",
            "text": "/task 任务 B",
        },
    ).json()["message"]
    proposed = client.post(
        "/runtime/inbox",
        json={
            "message_id": "ambiguous-1",
            "chat_id": "control-room",
            "actor_id": "owner",
            "project_id": "work",
            "text": "继续刚才那个方案",
        },
    )

    assert proposed.status_code == 200
    proposed_message = proposed.json()["message"]
    assert proposed_message["route_state"] == "proposed"
    assert set(proposed_message["candidate_thread_ids"]) == {
        first["thread_id"],
        second["thread_id"],
    }

    decision_payload = {
        "decision": "thread",
        "platform": "local",
        "actor_id": "owner",
        "thread_id": second["thread_id"],
        "update_kind": "supplement",
    }
    resolved = client.post(
        "/runtime/inbox/local:ambiguous-1/decision",
        json=decision_payload,
    )
    retry = client.post(
        "/runtime/inbox/local:ambiguous-1/decision",
        json=decision_payload,
    )

    assert resolved.status_code == 200
    assert retry.status_code == 200
    assert resolved.json()["message"]["route_state"] == "corrected"
    assert retry.json() == resolved.json()
    assert len(app.state.runtime.get_thread(second["thread_id"]).updates) == 1
    assert app.state.runtime.get_thread(first["thread_id"]).updates == ()
    detail = client.get(f"/runtime/threads/{second['thread_id']}")
    assert detail.status_code == 200
    assert detail.json()["thread"]["updates"][0]["message_key"] == "local:ambiguous-1"
    assert detail.json()["replay_verified"] is True
