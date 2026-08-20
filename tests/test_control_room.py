from pathlib import Path

from fastapi.testclient import TestClient

from copenguin.demo import DEFAULT_SOURCE
from feishu_computer_agent.config import Settings
from feishu_computer_agent.server import create_app
from super_agent_runtime import (
    DecisionRecordVerifier,
    SourceSnapshotBinding,
    SourceToArtifactExecutor,
    WorkerHost,
    WorkerHostConfig,
)


def _app_with_delivery(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    source = app.state.artifacts.put_json(DEFAULT_SOURCE, kind="control_room_test_source")
    submitted = app.state.source_tasks.submit(
        project_id="work",
        objective="Create an inspectable project decision record",
        sources=(
            SourceSnapshotBinding(
                source_snapshot_id="control-room-source-1",
                source_ref_id="test:control-room-source-1",
                revision_id=source.sha256,
                access_envelope_id="local-owner",
                content_artifact_id=source.artifact_id,
            ),
        ),
        thread_id="thread-control-room-delivery",
        run_id="run-control-room-delivery",
    )
    completed = WorkerHost(
        repository=app.state.runtime,
        artifacts=app.state.artifacts,
        executors=(SourceToArtifactExecutor(app.state.artifacts),),
        verifiers=(DecisionRecordVerifier(app.state.artifacts),),
        config=WorkerHostConfig(worker_id="control-room-test-worker"),
    ).run_once()
    assert completed is not None and completed.delivery_id is not None
    other = app.state.runtime.create_thread(
        thread_id="thread-control-room-other",
        project_id="personal",
        title="Plan a quiet weekend",
    )
    return app, submitted, completed, other


def test_control_room_page_is_loopback_only_and_uses_repo_brand(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    local = TestClient(app, client=("127.0.0.1", 50000))
    remote = TestClient(app, client=("203.0.113.9", 50000))

    page = local.get("/control-room")
    stylesheet = local.get("/control-room/static/control-room.css")
    script = local.get("/control-room/static/control-room.js")
    logo = local.get("/control-room/static/copenguin-logo.svg")

    assert page.status_code == 200
    assert "本地任务控制室" in page.text
    assert "需要我处理" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert stylesheet.status_code == 200
    assert script.status_code == 200
    assert "交给 CoPenguin" in page.text
    assert "Delivery" in script.text
    assert logo.content == (Path("assets") / "copenguin-logo.svg").read_bytes()
    assert remote.get("/control-room").status_code == 403
    assert remote.get("/control-room/api/overview").status_code == 403


def test_control_room_composes_parallel_threads_without_cross_thread_data(tmp_path) -> None:
    app, submitted, completed, other = _app_with_delivery(tmp_path)
    client = TestClient(app, client=("127.0.0.1", 50000))

    overview_response = client.get("/control-room/api/overview")
    detail_response = client.get(f"/control-room/api/threads/{submitted.task.thread_id}")

    assert overview_response.status_code == 200
    overview = overview_response.json()
    assert {item["thread_id"] for item in overview["threads"]} == {
        submitted.task.thread_id,
        other.thread_id,
    }
    assert overview["counts"] == {
        "threads": 2,
        "active": 1,
        "attention": 1,
        "delivered": 1,
    }
    assert overview["attention"][0]["kind"] == "delivery"
    assert overview["attention"][0]["thread_id"] == submitted.task.thread_id

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["thread"]["thread_id"] == submitted.task.thread_id
    assert detail["task"]["objective"] == "Create an inspectable project decision record"
    assert detail["replay_verified"] is True
    assert [item["run"]["run_id"] for item in detail["runs"]] == [submitted.task.run_id]
    assert [step["kind"] for step in detail["runs"][0]["steps"]] == [
        "transform",
        "verifier",
    ]
    assert [item["delivery"]["delivery_id"] for item in detail["deliveries"]] == [
        completed.delivery_id
    ]
    assert all(
        other.thread_id not in str(value) for key, value in detail.items() if key != "generated_at"
    )


def test_control_room_artifact_preview_and_delivery_decision_use_runtime_truth(
    tmp_path,
) -> None:
    app, submitted, completed, _ = _app_with_delivery(tmp_path)
    client = TestClient(app, client=("127.0.0.1", 50000))
    delivery = app.state.runtime.get_delivery(completed.delivery_id)

    artifact_response = client.get(f"/control-room/api/artifacts/{delivery.primary_artifact_id}")
    presented = client.post(f"/runtime/deliveries/{completed.delivery_id}/present")
    decided = client.post(
        f"/runtime/deliveries/{completed.delivery_id}/decision",
        json={
            "decision": "accept",
            "actor_id": "owner",
            "idempotency_key": "control-room-test:accept:1",
            "reason": "Reviewed in the local Control Room.",
        },
    )
    refreshed = client.get(f"/control-room/api/threads/{submitted.task.thread_id}")

    assert artifact_response.status_code == 200
    artifact = artifact_response.json()
    assert artifact["artifact_id"] == delivery.primary_artifact_id
    assert artifact["sha256"] == delivery.primary_artifact_id.rsplit(":", 1)[-1]
    assert artifact["format"] == "json"
    assert artifact["artifact_type"] == "project_decision_record"
    assert artifact["truncated"] is False
    assert isinstance(artifact["content"], dict)
    assert presented.json()["delivery"]["state"] == "presented"
    assert decided.json()["delivery"]["state"] == "accepted"
    assert refreshed.json()["deliveries"][0]["delivery"]["state"] == "accepted"
    assert refreshed.json()["thread"]["attention_state"] == "none"
    assert app.state.runtime.verify_thread_replay(submitted.task.thread_id) is True


def test_control_room_returns_bounded_errors_for_unknown_resources(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, memory_enabled=False, knowledge_enabled=False))
    client = TestClient(app, client=("127.0.0.1", 50000))

    assert client.get("/control-room/api/threads/missing").status_code == 404
    assert client.get("/control-room/api/artifacts/sha256:not-a-digest").status_code in {
        400,
        404,
    }
