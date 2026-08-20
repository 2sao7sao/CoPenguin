"""Composed, read-only projections for the local CoPenguin Control Room."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from super_agent_runtime import (
    ApprovalState,
    ArtifactCAS,
    ArtifactCorruption,
    ArtifactNotFound,
    AttentionState,
    InboxRouteState,
    NotFound,
    SQLiteRuntimeRepository,
    ThreadProjection,
)

from .config import Settings


class ControlRoomReadModel:
    """Translate Runtime projections into one owner-readable local view.

    This class deliberately has no write methods. Durable decisions continue to
    use the Runtime ingress, presentation, and Delivery-decision services.
    """

    max_artifact_preview_bytes = 256_000

    def __init__(
        self,
        *,
        repository: SQLiteRuntimeRepository,
        artifacts: ArtifactCAS,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.settings = settings

    def overview(self) -> dict[str, Any]:
        threads = self.repository.list_threads(limit=500)
        inbox = self.repository.list_inbox_records(limit=100)
        self.repository.expire_pending_approvals()
        approvals = self.repository.list_approvals(status=ApprovalState.PENDING, limit=100)

        thread_summaries = [self._thread_summary(thread) for thread in threads]
        attention: list[dict[str, Any]] = []
        for thread in threads:
            if thread.attention_state == AttentionState.NONE:
                continue
            attention.append(
                {
                    "attention_id": f"thread:{thread.thread_id}:{thread.attention_state.value}",
                    "kind": self._attention_kind(thread.attention_state),
                    "thread_id": thread.thread_id,
                    "project_id": thread.project_id,
                    "title": thread.title,
                    "state": thread.attention_state.value,
                    "updated_at": thread.updated_at,
                    "delivery_id": thread.latest_delivery_id,
                }
            )

        for record in inbox:
            if record.route_state != InboxRouteState.PROPOSED:
                continue
            attention.append(
                {
                    "attention_id": f"route:{record.message_key}",
                    "kind": "route",
                    "thread_id": record.thread_id,
                    "project_id": record.project_id,
                    "title": "确认消息归属",
                    "state": record.route_state.value,
                    "updated_at": record.updated_at or record.created_at,
                    "message_key": record.message_key,
                    "candidate_thread_ids": list(record.candidate_thread_ids),
                    "rationale": record.rationale,
                }
            )

        for approval in approvals:
            try:
                intent = self.repository.get_action_intent(approval.intent_id)
            except NotFound:
                continue
            try:
                thread = self.repository.get_thread(intent.thread_id)
                title = thread.title
                project_id = thread.project_id
            except NotFound:
                title = intent.capability
                project_id = "unknown"
            attention.append(
                {
                    "attention_id": f"approval:{approval.approval_id}",
                    "kind": "approval",
                    "thread_id": intent.thread_id,
                    "project_id": project_id,
                    "title": title,
                    "state": approval.status.value,
                    "updated_at": approval.updated_at,
                    "approval_id": approval.approval_id,
                    "capability": intent.capability,
                    "risk_level": approval.risk_level,
                }
            )

        attention.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        recent_inbox = [
            {
                "message_key": record.message_key,
                "project_id": record.project_id,
                "route_type": record.route_type.value,
                "route_state": record.route_state.value,
                "thread_id": record.thread_id,
                "confidence": record.confidence,
                "rationale": record.rationale,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            }
            for record in inbox[:40]
        ]
        return {
            "generated_at": self._now(),
            "threads": thread_summaries,
            "attention": attention,
            "inbox": recent_inbox,
            "counts": {
                "threads": len(threads),
                "active": sum(
                    thread.actual_state.value
                    in {
                        "created",
                        "queued",
                        "running",
                        "verifying",
                        "waiting_user",
                        "waiting_approval",
                        "waiting_receipt",
                        "waiting_dependency",
                        "waiting_resource",
                    }
                    for thread in threads
                ),
                "attention": len(attention),
                "delivered": sum(thread.actual_state.value == "delivered" for thread in threads),
            },
            "capabilities": self._capabilities(),
        }

    def thread_detail(self, thread_id: str) -> dict[str, Any]:
        thread = self.repository.get_thread(thread_id)
        latest_run = (
            max(thread.runs, key=lambda item: item.created_sequence) if thread.runs else None
        )
        task: dict[str, Any] = {}
        if latest_run is not None and latest_run.task_snapshot_id:
            try:
                task_value = self.artifacts.get_json(latest_run.task_snapshot_id)
            except (ArtifactCorruption, ArtifactNotFound, ValueError):
                task_value = None
            if isinstance(task_value, dict) and task_value.get("snapshot_type") == "task":
                task = {
                    "objective": str(task_value.get("objective") or thread.title),
                    "acceptance_criteria": list(task_value.get("acceptance_criteria") or ()),
                    "constraints": list(task_value.get("constraints") or ()),
                    "sensitivity": str(task_value.get("sensitivity") or "normal"),
                    "workflow_id": str(task_value.get("workflow_id") or latest_run.executor_key),
                }
        runs: list[dict[str, Any]] = []
        for run in sorted(thread.runs, key=lambda item: item.created_sequence, reverse=True):
            try:
                job = self.repository.get_job(run.run_id).as_dict()
            except NotFound:
                job = None
            runs.append(
                {
                    "run": asdict(run),
                    "job": job,
                    "steps": [
                        step.as_dict()
                        for step in self.repository.list_steps(run_id=run.run_id, limit=500)
                    ],
                }
            )

        deliveries = []
        for delivery in self.repository.list_deliveries(thread_id=thread_id, limit=100):
            deliveries.append(
                {
                    "delivery": delivery.as_dict(),
                    "primary_artifact": self.artifact_summary(delivery.primary_artifact_id),
                    "summary_artifact": self.artifact_summary(delivery.summary_artifact_id),
                    "verifier_artifact": self.artifact_summary(
                        delivery.verifier_result_artifact_id
                    ),
                    "supporting_artifacts": [
                        self.artifact_summary(artifact_id)
                        for artifact_id in delivery.supporting_artifact_ids
                    ],
                }
            )

        actions = [
            action.as_dict()
            for action in self.repository.list_action_intents(limit=500)
            if action.thread_id == thread_id
        ]
        return {
            "generated_at": self._now(),
            "thread": thread.as_dict(),
            "task": task,
            "projection_hash": thread.projection_hash,
            "replay_verified": self.repository.verify_thread_replay(thread_id),
            "runs": runs,
            "deliveries": deliveries,
            "actions": actions,
        }

    def artifact_summary(self, artifact_id: str) -> dict[str, Any]:
        content = self.artifacts.get_bytes(artifact_id)
        result: dict[str, Any] = {
            "artifact_id": artifact_id,
            "sha256": artifact_id.rsplit(":", 1)[-1],
            "size_bytes": len(content),
            "format": "binary",
            "title": "Local Artifact",
        }
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError:
            return result
        try:
            value = json.loads(decoded)
        except json.JSONDecodeError:
            result.update(
                {
                    "format": "text",
                    "title": self._first_text_line(decoded) or "Text Artifact",
                }
            )
            return result
        result["format"] = "json"
        if not isinstance(value, dict):
            result["title"] = "JSON Artifact"
            return result
        artifact_type = str(value.get("artifact_type") or "json_artifact")
        sections = value.get("sections")
        citations = value.get("citations")
        checks = value.get("checks")
        section_counts = (
            {
                str(key): len(items) if isinstance(items, list) else 0
                for key, items in sections.items()
            }
            if isinstance(sections, dict)
            else {}
        )
        result.update(
            {
                "artifact_type": artifact_type,
                "title": str(value.get("title") or self._artifact_type_label(artifact_type)),
                "purpose": str(value.get("purpose") or ""),
                "verification": value.get("verification"),
                "verdict": value.get("verdict"),
                "checks": checks if isinstance(checks, dict) else {},
                "citation_count": len(citations) if isinstance(citations, list) else 0,
                "section_counts": section_counts,
            }
        )
        return result

    def artifact_preview(self, artifact_id: str) -> dict[str, Any]:
        content = self.artifacts.get_bytes(artifact_id)
        summary = self.artifact_summary(artifact_id)
        if len(content) > self.max_artifact_preview_bytes:
            preview = content[: self.max_artifact_preview_bytes].decode("utf-8", errors="replace")
            return {
                **summary,
                "content": preview,
                "truncated": True,
                "preview_bytes": self.max_artifact_preview_bytes,
            }
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError:
            return {
                **summary,
                "content": None,
                "truncated": False,
                "preview_bytes": len(content),
            }
        try:
            value: Any = json.loads(decoded)
        except json.JSONDecodeError:
            value = decoded
        return {
            **summary,
            "content": value,
            "truncated": False,
            "preview_bytes": len(content),
        }

    def _thread_summary(self, thread: ThreadProjection) -> dict[str, Any]:
        current_run = None
        latest_delivery_state = None
        if thread.active_run_id:
            current_run = thread.run(thread.active_run_id)
        if current_run is None and thread.runs:
            current_run = max(thread.runs, key=lambda item: item.created_sequence)
        if thread.latest_delivery_id:
            try:
                latest_delivery_state = self.repository.get_delivery(
                    thread.latest_delivery_id
                ).state.value
            except NotFound:
                latest_delivery_state = "missing"
        return {
            "thread_id": thread.thread_id,
            "project_id": thread.project_id,
            "title": thread.title,
            "desired_state": thread.desired_state.value,
            "actual_state": thread.actual_state.value,
            "attention_state": thread.attention_state.value,
            "current_branch_id": thread.current_branch_id,
            "active_run_id": thread.active_run_id,
            "latest_delivery_id": thread.latest_delivery_id,
            "latest_delivery_state": latest_delivery_state,
            "created_at": thread.created_at,
            "updated_at": thread.updated_at,
            "run_count": len(thread.runs),
            "current_run": (
                {
                    "run_id": current_run.run_id,
                    "state": current_run.state.value,
                    "executor_key": current_run.executor_key,
                    "created_at": current_run.created_at,
                    "completed_at": current_run.completed_at,
                }
                if current_run is not None
                else None
            ),
        }

    def _capabilities(self) -> dict[str, Any]:
        return {
            "memory": {
                "enabled": self.settings.memory_enabled,
                "mode": "governed_adapter" if self.settings.memory_enabled else "disabled",
                "editable": False,
            },
            "knowledge": {
                "enabled": self.settings.knowledge_enabled,
                "mode": "governed_adapter" if self.settings.knowledge_enabled else "disabled",
                "editable": False,
            },
            "computer": {
                "provider": self.settings.computer_provider,
                "approval_required": self.settings.approval_required,
                "local_shell_enabled": self.settings.local_shell_enabled,
                "macos_shortcuts_enabled": self.settings.macos_shortcuts_enabled,
            },
            "control_room": {
                "transport": "loopback_only",
                "session_authentication": False,
                "artifact_download_authorization": False,
            },
        }

    def _attention_kind(self, state: AttentionState) -> str:
        return {
            AttentionState.NEEDS_INPUT: "input",
            AttentionState.NEEDS_APPROVAL: "approval",
            AttentionState.HAS_CONFLICT: "conflict",
            AttentionState.DELIVERY_READY: "delivery",
            AttentionState.FAILED: "failure",
            AttentionState.NONE: "none",
        }[state]

    def _first_text_line(self, value: str) -> str:
        return next((line.strip() for line in value.splitlines() if line.strip()), "")[:120]

    def _artifact_type_label(self, artifact_type: str) -> str:
        return {
            "project_decision_record": "Project Decision Record",
            "verifier_result": "Verification Result",
            "delivery_decision_evidence": "Delivery Decision Evidence",
        }.get(artifact_type, artifact_type.replace("_", " ").title())

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
