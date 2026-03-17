from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ArtifactStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

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

    def read_text(self, uri: str) -> str:
        return Path(uri).read_text(encoding="utf-8")
