from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .artifacts import ArtifactCAS
from .errors import IdempotencyConflict, InvalidTransition, NotFound
from .models import (
    DeliveryDecisionRecord,
    DeliveryDecisionType,
    DeliveryRecord,
    SchedulerJob,
    ThreadProjection,
    to_timestamp,
    utc_now,
)
from .repository import SQLiteRuntimeRepository
from .snapshots import ContextItem, ContextManifest, SnapshotStore, TaskSnapshot


@dataclass(frozen=True)
class DeliveryDecisionResult:
    delivery: DeliveryRecord
    decision: DeliveryDecisionRecord
    thread: ThreadProjection
    revision_job: SchedulerJob | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class DeliveryDecisionService:
    """Compile owner decisions into immutable evidence and durable Runtime changes."""

    max_text_length = 4_000

    def __init__(
        self,
        *,
        repository: SQLiteRuntimeRepository,
        artifacts: ArtifactCAS,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.snapshots = SnapshotStore(artifacts)
        self.clock = clock

    def decide(
        self,
        delivery_id: str,
        *,
        decision: DeliveryDecisionType | str,
        actor: str,
        idempotency_key: str,
        reason: str | None = None,
        revision_request: str | None = None,
    ) -> DeliveryDecisionResult:
        delivery_id = delivery_id.strip()
        actor = actor.strip()
        idempotency_key = idempotency_key.strip()
        if not delivery_id or not actor or not idempotency_key:
            raise ValueError("delivery_id, actor, and idempotency_key are required")
        if len(idempotency_key) > 200:
            raise ValueError("idempotency_key must not exceed 200 characters")
        resolved_decision = DeliveryDecisionType(decision)
        normalized_reason = self._bounded_text(reason, field="reason")
        normalized_revision = self._bounded_text(
            revision_request,
            field="revision_request",
        )
        if resolved_decision == DeliveryDecisionType.REVISE and not normalized_revision:
            raise ValueError("revision_request is required for a revise decision")
        if resolved_decision != DeliveryDecisionType.REVISE and normalized_revision:
            raise ValueError("revision_request is only valid for a revise decision")

        decision_id = uuid5(
            NAMESPACE_URL,
            f"copenguin:delivery-decision:{delivery_id}:{idempotency_key}",
        ).hex
        decision_artifact = self.artifacts.put_json(
            {
                "artifact_type": "delivery_decision_evidence",
                "schema_version": 1,
                "decision_id": decision_id,
                "delivery_id": delivery_id,
                "decision": resolved_decision.value,
                "actor": actor,
                "idempotency_key": idempotency_key,
                "reason": normalized_reason,
                "revision_request": normalized_revision,
            },
            kind="delivery_decision_evidence",
        )

        existing = self.repository.find_delivery_decision(idempotency_key=idempotency_key)
        if existing is not None:
            if (
                existing.delivery_id != delivery_id
                or existing.decision != resolved_decision
                or existing.decision_id != decision_id
                or existing.actor != actor
                or existing.decision_artifact_id != decision_artifact.artifact_id
            ):
                raise IdempotencyConflict(
                    "Delivery decision idempotency key was reused for another decision"
                )
            return self._existing_result(existing)

        revision_run_id: str | None = None
        revision_task_snapshot_id: str | None = None
        revision_agent_snapshot_id: str | None = None
        revision_context_manifest_id: str | None = None
        if resolved_decision == DeliveryDecisionType.REVISE:
            (
                revision_run_id,
                revision_task_snapshot_id,
                revision_agent_snapshot_id,
                revision_context_manifest_id,
            ) = self._compile_revision(
                delivery_id=delivery_id,
                decision_id=decision_id,
                decision_artifact_id=decision_artifact.artifact_id,
                revision_request=normalized_revision,
            )

        delivery, record, thread, job = self.repository.decide_delivery(
            delivery_id,
            decision=resolved_decision,
            decision_id=decision_id,
            decision_artifact_id=decision_artifact.artifact_id,
            idempotency_key=idempotency_key,
            actor=actor,
            revision_run_id=revision_run_id,
            revision_task_snapshot_id=revision_task_snapshot_id,
            revision_agent_snapshot_id=revision_agent_snapshot_id,
            revision_context_manifest_id=revision_context_manifest_id,
        )
        return DeliveryDecisionResult(
            delivery=delivery,
            decision=record,
            thread=thread,
            revision_job=job,
        )

    def _compile_revision(
        self,
        *,
        delivery_id: str,
        decision_id: str,
        decision_artifact_id: str,
        revision_request: str | None,
    ) -> tuple[str, str, str, str]:
        if revision_request is None:
            raise ValueError("revision_request is required")
        delivery = self.repository.get_delivery(delivery_id)
        if delivery.state.value != "presented":
            raise InvalidTransition("only a presented Delivery may be revised")
        thread = self.repository.get_thread(delivery.thread_id)
        run = thread.run(delivery.run_id)
        if run is None:
            raise NotFound(f"run not found: {delivery.run_id}")
        if not all((run.task_snapshot_id, run.agent_snapshot_id, run.context_manifest_id)):
            raise InvalidTransition("the prior Run does not have a complete frozen manifest")
        assert run.task_snapshot_id is not None
        assert run.agent_snapshot_id is not None
        assert run.context_manifest_id is not None

        task = self.snapshots.load(run.task_snapshot_id)
        agent = self.snapshots.load(run.agent_snapshot_id)
        context = self.snapshots.load(run.context_manifest_id)
        if task.get("snapshot_type") != "task":
            raise InvalidTransition("the prior task snapshot has the wrong schema")
        if agent.get("snapshot_type") != "agent":
            raise InvalidTransition("the prior agent snapshot has the wrong schema")
        if context.get("snapshot_type") != "context_manifest":
            raise InvalidTransition("the prior context manifest has the wrong schema")
        if context.get("task_snapshot_id") != run.task_snapshot_id:
            raise InvalidTransition("the prior context is bound to a different task snapshot")
        if context.get("agent_snapshot_id") != run.agent_snapshot_id:
            raise InvalidTransition("the prior context is bound to a different agent snapshot")

        revision_run_id = uuid5(
            NAMESPACE_URL,
            f"copenguin:delivery-revision-run:{delivery_id}:{decision_id}",
        ).hex
        now = to_timestamp(self.clock())
        input_artifact_ids = tuple(
            dict.fromkeys((*task.get("input_artifact_ids", ()), decision_artifact_id))
        )
        revision_constraint = f"Revision requested for Delivery {delivery_id}: {revision_request}"
        task_snapshot = self.snapshots.put_task(
            TaskSnapshot(
                task_id=str(task.get("task_id") or delivery.thread_id),
                thread_id=delivery.thread_id,
                project_id=thread.project_id,
                objective=str(task.get("objective") or thread.title),
                domain=str(task.get("domain") or "work"),
                acceptance_criteria=tuple(
                    str(item) for item in task.get("acceptance_criteria", ())
                ),
                constraints=tuple(str(item) for item in task.get("constraints", ()))
                + (revision_constraint,),
                input_artifact_ids=tuple(str(item) for item in input_artifact_ids),
                sensitivity=str(task.get("sensitivity") or delivery.sensitivity),
                created_at=now,
                schema_version=max(2, int(task.get("schema_version", 1))),
                workflow_id=str(task.get("workflow_id") or run.executor_key),
            )
        )
        prior_items = tuple(self._context_item(item) for item in context.get("items", ()))
        next_ordinal = max((item.ordinal for item in prior_items), default=0) + 1
        revision_context = self.snapshots.put_context(
            ContextManifest(
                task_snapshot_id=task_snapshot.artifact_id,
                agent_snapshot_id=run.agent_snapshot_id,
                items=(
                    *prior_items,
                    ContextItem(
                        ordinal=next_ordinal,
                        source_type="delivery_revision_request",
                        source_ref=f"delivery:{delivery_id}",
                        content_artifact_id=decision_artifact_id,
                        allowed_use="direct",
                        sensitivity=delivery.sensitivity,
                        metadata={
                            "decision_id": decision_id,
                            "previous_delivery_id": delivery_id,
                            "supersedes_run_id": delivery.run_id,
                        },
                    ),
                ),
                compiler_version="delivery-revision-v1",
                compiled_at=now,
                schema_version=max(1, int(context.get("schema_version", 1))),
            )
        )
        return (
            revision_run_id,
            task_snapshot.artifact_id,
            run.agent_snapshot_id,
            revision_context.artifact_id,
        )

    def _existing_result(self, record: DeliveryDecisionRecord) -> DeliveryDecisionResult:
        delivery = self.repository.get_delivery(record.delivery_id)
        thread = self.repository.get_thread(record.thread_id)
        job = (
            self.repository.get_job(record.revision_run_id)
            if record.revision_run_id is not None
            else None
        )
        return DeliveryDecisionResult(
            delivery=delivery,
            decision=record,
            thread=thread,
            revision_job=job,
        )

    def _context_item(self, value: object) -> ContextItem:
        if not isinstance(value, dict):
            raise InvalidTransition("the prior context contains an invalid item")
        return ContextItem(
            ordinal=int(value.get("ordinal", 0)),
            source_type=str(value.get("source_type") or ""),
            source_ref=str(value.get("source_ref") or ""),
            content_artifact_id=str(value.get("content_artifact_id") or ""),
            allowed_use=str(value.get("allowed_use") or "direct"),
            sensitivity=str(value.get("sensitivity") or "normal"),
            metadata=dict(value.get("metadata") or {}),
        )

    def _bounded_text(self, value: str | None, *, field: str) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > self.max_text_length:
            raise ValueError(f"{field} must not exceed {self.max_text_length} characters")
        return normalized
