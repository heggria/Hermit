from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYNC_COMMAND = "uv sync --group dev --group typecheck --group docs --group security --group release"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _require(path: str, needle: str) -> None:
    content = _read(path)
    if needle not in content:
        raise SystemExit(f"{path} is missing expected content: {needle}")


def main() -> int:
    checks = {
        "README.md": [SYNC_COMMAND, "Python `3.13+`"],
        "README.zh-CN.md": [SYNC_COMMAND, "Python `3.13+`"],
        "docs/getting-started.md": [SYNC_COMMAND, "Python `3.13+`"],
    }
    for path, needles in checks.items():
        for needle in needles:
            _require(path, needle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
