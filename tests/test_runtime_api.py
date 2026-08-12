from fastapi.testclient import TestClient

from feishu_computer_agent.config import Settings
from feishu_computer_agent.server import create_app
from super_agent_runtime import InboxMessage, RoutingContext


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
