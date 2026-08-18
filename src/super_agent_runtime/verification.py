"""Deterministic verifier for the Alpha Project Decision Record workflow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .artifacts import ArtifactCAS
from .errors import PermanentExecutionError
from .execution import ExecutionControl, ExecutionRequest
from .models import VerificationResult, VerificationVerdict


class DecisionRecordVerifier:
    """Validate evidence and governance fields without remote reads or model calls."""

    key = "project_decision_record_verifier"
    version = "1.0.0"
    executor_key = "source_to_project_decision_record_v1"

    def __init__(self, artifacts: ArtifactCAS) -> None:
        self.artifacts = artifacts

    def verify(
        self,
        request: ExecutionRequest,
        draft_artifact_id: str,
        control: ExecutionControl,
    ) -> VerificationResult:
        control.raise_if_cancelled()
        draft = self.artifacts.get_json(draft_artifact_id)
        if not isinstance(draft, dict):
            raise PermanentExecutionError(
                "decision_record_invalid",
                "Project Decision Record must be a JSON object",
            )
        context_sources = self._context_sources(request.context_manifest)
        citations = draft.get("citations")
        citation_items = citations if isinstance(citations, list) else []
        source_ids = draft.get("source_snapshot_ids")
        record_source_ids = set(source_ids if isinstance(source_ids, list) else [])
        context_source_ids = set(context_sources)
        citation_source_ids = {
            str(item.get("source_snapshot_id"))
            for item in citation_items
            if isinstance(item, dict) and item.get("source_snapshot_id")
        }

        checks = {
            "schema": self._schema_valid(draft),
            "evidence": bool(context_source_ids)
            and record_source_ids == context_source_ids
            and citation_source_ids == context_source_ids,
            "citation": self._citations_valid(citation_items, context_sources),
            "permission": self._permission_valid(context_sources),
            "sensitivity": draft.get("audience") == "requester_only"
            and draft.get("publishable") is False,
            "freshness": self._freshness_valid(draft, context_sources),
            "actionability": self._actionability_valid(draft),
        }
        verdict = VerificationVerdict.PASSED if all(checks.values()) else VerificationVerdict.FAILED
        report = {
            "artifact_type": "verifier_result",
            "schema_version": 1,
            "verifier": {"key": self.key, "version": self.version},
            "executor_key": request.executor_key,
            "draft_artifact_id": draft_artifact_id,
            "verdict": verdict.value,
            "checks": checks,
            "source_snapshot_ids": sorted(context_source_ids),
            "evidence_artifact_ids": sorted(
                str(value["content_artifact_id"]) for value in context_sources.values()
            ),
        }
        report_artifact = self.artifacts.put_json(report, kind="verifier_result")
        verified_artifact_id: str | None = None
        if verdict == VerificationVerdict.PASSED:
            verified_record = dict(draft)
            verified_record["verification"] = {
                "status": verdict.value,
                "verifier_result_id": report_artifact.artifact_id,
                "verifier_key": self.key,
                "verifier_version": self.version,
            }
            verified = self.artifacts.put_json(
                verified_record,
                kind="project_decision_record_verified",
            )
            verified_artifact_id = verified.artifact_id
        control.raise_if_cancelled()
        return VerificationResult(
            verifier_key=self.key,
            verifier_version=self.version,
            verdict=verdict,
            report_artifact_id=report_artifact.artifact_id,
            verified_artifact_id=verified_artifact_id,
            checks=checks,
        )

    def _context_sources(
        self,
        context: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        sources: dict[str, dict[str, Any]] = {}
        items = context.get("items", ())
        for item in items if isinstance(items, list | tuple) else ():
            if not isinstance(item, dict) or item.get("source_type") != "source_snapshot":
                continue
            metadata = item.get("metadata")
            if not isinstance(metadata, dict) or not metadata.get("source_snapshot_id"):
                continue
            sources[str(metadata["source_snapshot_id"])] = {
                "source_ref_id": metadata.get("source_ref_id"),
                "revision_id": metadata.get("revision_id"),
                "access_envelope_id": metadata.get("access_envelope_id"),
                "content_artifact_id": item.get("content_artifact_id"),
                "allowed_use": item.get("allowed_use"),
                "sensitivity": item.get("sensitivity"),
            }
        return sources

    def _schema_valid(self, draft: Mapping[str, Any]) -> bool:
        sections = draft.get("sections")
        required_sections = {
            "background_and_problem",
            "confirmed_facts",
            "decisions",
            "action_items",
            "open_questions",
            "risks",
        }
        return (
            draft.get("artifact_type") == "project_decision_record"
            and draft.get("schema_version") == 1
            and isinstance(draft.get("title"), str)
            and bool(str(draft.get("title") or "").strip())
            and isinstance(sections, dict)
            and required_sections.issubset(sections)
            and all(isinstance(sections[key], list) for key in required_sections)
        )

    def _citations_valid(
        self,
        citations: list[Any],
        sources: Mapping[str, Mapping[str, Any]],
    ) -> bool:
        if len(citations) != len(sources):
            return False
        for citation in citations:
            if not isinstance(citation, dict):
                return False
            source_id = str(citation.get("source_snapshot_id") or "")
            source = sources.get(source_id)
            if source is None:
                return False
            artifact_id = citation.get("content_artifact_id")
            if (
                citation.get("source_ref_id") != source.get("source_ref_id")
                or citation.get("revision_id") != source.get("revision_id")
                or artifact_id != source.get("content_artifact_id")
                or not isinstance(artifact_id, str)
                or not self.artifacts.exists(artifact_id, verify=True)
                or not citation.get("pointer")
            ):
                return False
        return True

    def _permission_valid(self, sources: Mapping[str, Mapping[str, Any]]) -> bool:
        return bool(sources) and all(
            source.get("allowed_use") in {"direct", "artifact_only"}
            and bool(source.get("access_envelope_id"))
            for source in sources.values()
        )

    def _freshness_valid(
        self,
        draft: Mapping[str, Any],
        sources: Mapping[str, Mapping[str, Any]],
    ) -> bool:
        validity = draft.get("validity")
        revisions = validity.get("source_revisions") if isinstance(validity, dict) else None
        if not isinstance(revisions, list):
            return False
        record_revisions = {
            str(item.get("source_snapshot_id")): str(item.get("revision_id"))
            for item in revisions
            if isinstance(item, dict) and item.get("source_snapshot_id")
        }
        expected = {
            source_id: str(source.get("revision_id") or "") for source_id, source in sources.items()
        }
        return record_revisions == expected and all(expected.values())

    def _actionability_valid(self, draft: Mapping[str, Any]) -> bool:
        sections = draft.get("sections")
        if not isinstance(sections, dict):
            return False
        return any(
            isinstance(sections.get(key), list) and bool(sections[key])
            for key in ("decisions", "action_items", "open_questions")
        )
