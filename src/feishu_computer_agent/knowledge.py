from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class KnowledgeRuntime(Protocol):
    def answer(self, *, intent: str, question: str) -> dict[str, Any]: ...


class NoopKnowledgeRuntime:
    def answer(self, *, intent: str, question: str) -> dict[str, Any]:
        return {"enabled": False, "rendered": ""}


class EvolveKBRuntime:
    def __init__(self, repo_root: Path) -> None:
        from evolvekb.skills.runtime import PlaybookRuntime

        self._repo_root = repo_root
        self._runtime = PlaybookRuntime(repo_root)

    def answer(self, *, intent: str, question: str) -> dict[str, Any]:
        result = self._runtime.run(
            intent=intent,
            question=question,
            settings_arg="settings/reference.yaml",
            write_side_effects=False,
        )
        return {
            "enabled": True,
            "rendered": getattr(result, "rendered", ""),
            "trace_id": getattr(getattr(result, "trace", None), "id", None),
        }


def build_knowledge_runtime(enabled: bool, repo_root: Path) -> KnowledgeRuntime:
    if not enabled:
        return NoopKnowledgeRuntime()
    if not repo_root.exists():
        return NoopKnowledgeRuntime()
    try:
        return EvolveKBRuntime(repo_root)
    except (ImportError, FileNotFoundError):
        return NoopKnowledgeRuntime()
