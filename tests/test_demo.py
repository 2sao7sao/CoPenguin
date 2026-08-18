from __future__ import annotations

import pytest

from copenguin.demo import load_demo_source, run_source_to_artifact_demo


def test_credential_free_demo_produces_replayable_artifact(tmp_path) -> None:
    result = run_source_to_artifact_demo(data_dir=tmp_path)

    assert result["status"] == "completed"
    assert result["replay_verified"] is True
    assert result["thread_state"] == "delivered"
    assert result["artifact"]["artifact_type"] == "project_decision_record"
    assert result["artifact"]["publishable"] is False
    assert result["artifact"]["verification"]["status"] == "passed"
    assert result["artifact"]["citations"][0]["source_ref_id"] == "copenguin:built-in-demo"
    assert result["delivery_id"]
    assert result["outbox_id"]


def test_demo_source_rejects_non_object_json(tmp_path) -> None:
    source = tmp_path / "source.json"
    source.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="one JSON object"):
        load_demo_source(source)
