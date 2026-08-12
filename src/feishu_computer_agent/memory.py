from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class MemoryRuntime(Protocol):
    def ingest_turn(self, *, session_id: str, text: str, source: str) -> dict[str, Any]: ...

    def prompt_context(self, *, session_id: str, query: str) -> dict[str, Any]: ...


class NoopMemoryRuntime:
    def ingest_turn(self, *, session_id: str, text: str, source: str) -> dict[str, Any]:
        return {"enabled": False, "accepted_memories": []}

    def prompt_context(self, *, session_id: str, query: str) -> dict[str, Any]:
        return {"enabled": False, "prompt_sections": []}


class EvolveMemoryRuntime:
    def __init__(self, root_dir: Path) -> None:
        from memory_system.persistence import DiskSessionRepository
        from memory_system.service import SessionMemoryRuntime

        self._repository = DiskSessionRepository(root_dir)
        self._runtime_cls = SessionMemoryRuntime
        self._runtimes: dict[str, Any] = {}

    def _runtime(self, session_id: str) -> Any:
        if session_id not in self._runtimes:
            self._runtimes[session_id] = self._runtime_cls(
                session_id=session_id,
                repository=self._repository,
            )
        return self._runtimes[session_id]

    def ingest_turn(self, *, session_id: str, text: str, source: str) -> dict[str, Any]:
        return self._runtime(session_id).ingest_turn(
            text=text,
            source=source,
            timestamp=datetime.now(UTC),
        )

    def prompt_context(self, *, session_id: str, query: str) -> dict[str, Any]:
        return self._runtime(session_id).prompt_context(
            query_text=query,
            timestamp=datetime.now(UTC),
        )


def build_memory_runtime(enabled: bool, root_dir: Path) -> MemoryRuntime:
    if not enabled:
        return NoopMemoryRuntime()
    try:
        return EvolveMemoryRuntime(root_dir)
    except ImportError:
        return NoopMemoryRuntime()
