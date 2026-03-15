#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def clean_build_artifacts(repo_dir: Path) -> list[Path]:
    removed: list[Path] = []
    for name in ("build", "dist"):
        path = repo_dir / name
        if path.exists():
            shutil.rmtree(path)
            removed.append(path)
    for path in repo_dir.iterdir():
        if not path.is_dir():
            continue
        if path.name.endswith(".egg-info") or path.name.endswith(".dist-info"):
            shutil.rmtree(path)
            removed.append(path)
    return removed


def main(argv: list[str]) -> int:
    target = Path(argv[1]).resolve() if len(argv) > 1 else Path.cwd().resolve()
    clean_build_artifacts(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
