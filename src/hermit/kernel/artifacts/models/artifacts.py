from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ArtifactStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Write helpers                                                        #
    # ------------------------------------------------------------------ #

    def store_text(self, text: str, *, extension: str = "txt") -> tuple[str, str]:
        return self.store_bytes(text.encode("utf-8"), extension=extension)

    def store_json(self, payload: Any, *, extension: str = "json") -> tuple[str, str]:
        return self.store_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            extension=extension,
        )

    def store_bytes(self, data: bytes, *, extension: str = "bin") -> tuple[str, str]:
        content_hash = hashlib.sha256(data).hexdigest()
        subdir = self.root_dir / content_hash[:2] / content_hash[2:4]
        subdir.mkdir(parents=True, exist_ok=True)
        path = subdir / f"{content_hash}.{extension}"
        if not path.exists():
            path.write_bytes(data)
        return str(path), content_hash

    # ------------------------------------------------------------------ #
    # Read helpers                                                         #
    # ------------------------------------------------------------------ #

    def _resolve_safe(self, uri: str) -> Path:
        """Resolve *uri* to an absolute path and verify it stays within the
        store root.  Raises ``ValueError`` on path-traversal and
        ``FileNotFoundError`` when the artifact does not exist.
        """
        resolved = Path(uri).resolve()
        resolved_root = self.root_dir.resolve()
        if resolved_root not in resolved.parents and resolved != resolved_root:
            raise ValueError(f"Artifact path escapes store root: {uri}")
        if not resolved.exists():
            raise FileNotFoundError(
                f"Artifact not found in store (uri={uri!r}, resolved={resolved})"
            )
        return resolved

    def read_text(self, uri: str) -> str:
        """Read a previously stored text artifact by its URI.

        Raises:
            ValueError: if the path escapes the store root.
            FileNotFoundError: if the artifact does not exist in the store.
        """
        return self._resolve_safe(uri).read_text(encoding="utf-8")

    def read_bytes(self, uri: str) -> bytes:
        """Read a previously stored binary artifact by its URI.

        Raises:
            ValueError: if the path escapes the store root.
            FileNotFoundError: if the artifact does not exist in the store.
        """
        return self._resolve_safe(uri).read_bytes()
