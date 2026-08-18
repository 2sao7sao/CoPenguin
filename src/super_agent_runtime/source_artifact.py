from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .artifacts import ArtifactCAS
from .errors import PermanentExecutionError
from .execution import ExecutionControl, ExecutionRequest, ExecutionResult


@dataclass(frozen=True)
class SourceToArtifactLimits:
    max_sources: int = 8
    max_total_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if self.max_sources < 1:
            raise ValueError("max_sources must be at least 1")
        if self.max_total_bytes < 1:
            raise ValueError("max_total_bytes must be at least 1")


class SourceToArtifactExecutor:
    """Deterministic V2-004 fixture: frozen SourceSnapshots to one draft record.

    This Executor performs no remote reads, model calls, publication, memory
    promotion, or KB promotion. It only transforms explicitly bound CAS inputs.
    """

    key = "source_to_project_decision_record_v1"
    version = "1.0.0"

    def __init__(
        self,
        artifacts: ArtifactCAS,
        *,
        limits: SourceToArtifactLimits | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.limits = limits or SourceToArtifactLimits()

    def execute(
        self,
        request: ExecutionRequest,
        control: ExecutionControl,
    ) -> ExecutionResult:
        checkpoint = dict(request.checkpoint_state or {})
        if checkpoint.get("phase") == "artifact_written":
            artifact_id = checkpoint.get("draft_artifact_id")
            if not isinstance(artifact_id, str) or not self.artifacts.exists(
                artifact_id, verify=True
            ):
                raise PermanentExecutionError(
                    "checkpoint_output_missing",
                    "The checkpointed Project Decision Record is missing or corrupt",
                )
            return ExecutionResult(
                output_artifact_id=artifact_id,
                metadata={"resumed": True, "source_count": checkpoint.get("source_count", 0)},
            )

        control.raise_if_cancelled()
        raw_items = request.context_manifest.get("items", ())
        source_items = [item for item in raw_items if item.get("source_type") == "source_snapshot"]
        if not source_items:
            raise PermanentExecutionError(
                "source_snapshot_missing",
                "The frozen ContextManifest does not contain a SourceSnapshot",
            )
        if len(source_items) > self.limits.max_sources:
            raise PermanentExecutionError(
                "source_budget_exceeded",
                f"Source count exceeds the bounded limit of {self.limits.max_sources}",
            )

        task_inputs = set(request.task_snapshot.get("input_artifact_ids", ()))
        loaded_sources: list[dict[str, Any]] = []
        total_bytes = 0
        for item in sorted(source_items, key=lambda value: int(value.get("ordinal", 0))):
            control.raise_if_cancelled()
            artifact_id = item.get("content_artifact_id")
            if not isinstance(artifact_id, str) or artifact_id not in task_inputs:
                raise PermanentExecutionError(
                    "source_not_bound_to_task",
                    "A Context source is not listed in the frozen Task inputs",
                )
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                raise PermanentExecutionError(
                    "source_metadata_missing", "SourceSnapshot metadata is required"
                )
            required = (
                "source_snapshot_id",
                "source_ref_id",
                "revision_id",
                "access_envelope_id",
            )
            if any(not metadata.get(key) for key in required):
                raise PermanentExecutionError(
                    "source_metadata_incomplete",
                    "SourceSnapshot identity, revision, and access evidence are required",
                )
            if item.get("allowed_use") not in {"direct", "artifact_only"}:
                raise PermanentExecutionError(
                    "source_use_not_allowed",
                    "SourceSnapshot is not authorized for Artifact generation",
                )
            content = self.artifacts.get_bytes(artifact_id)
            total_bytes += len(content)
            if total_bytes > self.limits.max_total_bytes:
                raise PermanentExecutionError(
                    "source_budget_exceeded",
                    f"Source bytes exceed the bounded limit of {self.limits.max_total_bytes}",
                )
            try:
                payload = json.loads(content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PermanentExecutionError(
                    "source_fixture_invalid",
                    "The deterministic fixture source must be a UTF-8 JSON object",
                ) from exc
            if not isinstance(payload, dict):
                raise PermanentExecutionError(
                    "source_fixture_invalid",
                    "The deterministic fixture source must be a JSON object",
                )
            loaded_sources.append(
                {
                    "ordinal": int(item.get("ordinal", 0)),
                    "artifact_id": artifact_id,
                    "source_snapshot_id": str(metadata["source_snapshot_id"]),
                    "source_ref_id": str(metadata["source_ref_id"]),
                    "revision_id": str(metadata["revision_id"]),
                    "access_envelope_id": str(metadata["access_envelope_id"]),
                    "payload": payload,
                }
            )

        source_ids = [source["source_snapshot_id"] for source in loaded_sources]
        control.checkpoint(
            {
                "phase": "sources_loaded",
                "source_snapshot_ids": source_ids,
                "source_count": len(loaded_sources),
                "total_source_bytes": total_bytes,
            }
        )
        control.raise_if_cancelled()

        record = {
            "artifact_type": "project_decision_record",
            "schema_version": 1,
            "project_id": request.project_id,
            "title": self._title(request, loaded_sources),
            "audience": "requester_only",
            "purpose": str(request.task_snapshot.get("objective") or ""),
            "source_snapshot_ids": source_ids,
            "sections": {
                "background_and_problem": self._collect(loaded_sources, "background"),
                "confirmed_facts": self._collect(loaded_sources, "facts"),
                "decisions": self._collect(loaded_sources, "decisions"),
                "action_items": self._collect(loaded_sources, "action_items"),
                "open_questions": self._collect(loaded_sources, "open_questions"),
                "risks": self._collect(loaded_sources, "risks"),
            },
            "citations": [
                {
                    "source_snapshot_id": source["source_snapshot_id"],
                    "source_ref_id": source["source_ref_id"],
                    "revision_id": source["revision_id"],
                    "content_artifact_id": source["artifact_id"],
                    "pointer": "$",
                }
                for source in loaded_sources
            ],
            "validity": {
                "source_revisions": [
                    {
                        "source_snapshot_id": source["source_snapshot_id"],
                        "revision_id": source["revision_id"],
                    }
                    for source in loaded_sources
                ],
                "next_review_at": None,
            },
            "verification": {
                "status": "pending_v2_005",
                "verifier_result_id": None,
            },
            "publishable": False,
            "executor": {"key": self.key, "version": self.version},
            "execution_inputs": {
                "task_snapshot_id": request.context_manifest.get("task_snapshot_id"),
                "agent_snapshot_id": request.context_manifest.get("agent_snapshot_id"),
                "context_compiler_version": request.context_manifest.get("compiler_version"),
            },
        }
        output = self.artifacts.put_json(record, kind="project_decision_record_draft")
        control.checkpoint(
            {
                "phase": "artifact_written",
                "draft_artifact_id": output.artifact_id,
                "source_snapshot_ids": source_ids,
                "source_count": len(loaded_sources),
                "total_source_bytes": total_bytes,
            }
        )
        return ExecutionResult(
            output_artifact_id=output.artifact_id,
            metadata={"resumed": False, "source_count": len(loaded_sources)},
        )

    def _title(
        self,
        request: ExecutionRequest,
        sources: list[dict[str, Any]],
    ) -> str:
        source_title = sources[0]["payload"].get("title") if len(sources) == 1 else None
        return str(
            source_title or request.task_snapshot.get("objective") or "Project Decision Record"
        )

    def _collect(self, sources: list[dict[str, Any]], field: str) -> list[Any]:
        collected: list[Any] = []
        for source in sources:
            value = source["payload"].get(field, ())
            if value is None:
                continue
            if not isinstance(value, list):
                raise PermanentExecutionError(
                    "source_fixture_invalid",
                    f"Fixture field {field} must be an array",
                )
            collected.extend(value)
        return collected
