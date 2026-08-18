from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ArtifactCorruption, ArtifactNotFound
from .models import canonical_json


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    sha256: str
    size_bytes: int
    media_type: str
    kind: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "kind": self.kind,
        }


class ArtifactCAS:
    """Append-only filesystem content-addressed storage.

    Intrinsic identity comes only from bytes. Kind and media type describe the
    current reference and belong in the event or manifest that uses it.
    """

    _PREFIX = "artifact:sha256:"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(
        self,
        content: bytes,
        *,
        kind: str,
        media_type: str = "application/octet-stream",
    ) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        target = self._path_for_digest(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._verify_path(target, digest)
        else:
            handle, temporary_name = tempfile.mkstemp(prefix=f".{digest}.", dir=target.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            self._verify_path(target, digest)
        return ArtifactRef(
            artifact_id=f"{self._PREFIX}{digest}",
            sha256=digest,
            size_bytes=len(content),
            media_type=media_type,
            kind=kind,
        )

    def put_text(
        self,
        content: str,
        *,
        kind: str,
        media_type: str = "text/plain; charset=utf-8",
    ) -> ArtifactRef:
        return self.put_bytes(content.encode("utf-8"), kind=kind, media_type=media_type)

    def put_json(self, value: Any, *, kind: str) -> ArtifactRef:
        return self.put_bytes(
            canonical_json(value).encode("utf-8"),
            kind=kind,
            media_type="application/json",
        )

    def get_bytes(self, artifact: ArtifactRef | str) -> bytes:
        digest = self._digest_from_ref(artifact)
        path = self._path_for_digest(digest)
        if not path.is_file():
            raise ArtifactNotFound(f"artifact not found: {self._PREFIX}{digest}")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ArtifactCorruption(f"artifact hash mismatch: {self._PREFIX}{digest}")
        return content

    def get_json(self, artifact: ArtifactRef | str) -> Any:
        import json

        return json.loads(self.get_bytes(artifact).decode("utf-8"))

    def exists(self, artifact: ArtifactRef | str, *, verify: bool = False) -> bool:
        digest = self._digest_from_ref(artifact)
        path = self._path_for_digest(digest)
        if not path.is_file():
            return False
        if verify:
            self._verify_path(path, digest)
        return True

    def _digest_from_ref(self, artifact: ArtifactRef | str) -> str:
        artifact_id = artifact.artifact_id if isinstance(artifact, ArtifactRef) else artifact
        if not artifact_id.startswith(self._PREFIX):
            raise ValueError(f"unsupported artifact id: {artifact_id}")
        digest = artifact_id.removeprefix(self._PREFIX)
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"invalid sha256 artifact id: {artifact_id}")
        return digest

    def _path_for_digest(self, digest: str) -> Path:
        return self.root / "sha256" / digest[:2] / digest[2:4] / digest

    def _verify_path(self, path: Path, digest: str) -> None:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            raise ArtifactCorruption(
                f"artifact hash mismatch at {path}: expected {digest}, found {actual}"
            )
