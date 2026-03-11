#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[A-Za-z0-9.\-]+)?$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bump Hermit version across tracked files.")
    parser.add_argument("version", help="New version, for example 0.1.1")
    parser.add_argument(
        "--package-name",
        default="",
        help="Optional new distribution package name, for example hermit-agent",
    )
    parser.add_argument(
        "--update-lock",
        action="store_true",
        help="Run `uv lock` after updating versioned files.",
    )
    return parser.parse_args()


def replace_in_file(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Could not update version in {path}")
    path.write_text(updated, encoding="utf-8")


def main() -> int:
    args = parse_args()
    version = args.version.strip()
    if not SEMVER_RE.fullmatch(version):
        raise SystemExit(f"Invalid version: {version}")
    package_name = args.package_name.strip()

    repo = Path(__file__).resolve().parents[1]
    if package_name:
        replace_in_file(
            repo / "pyproject.toml",
            r'^name = "[^"]+"$',
            f'name = "{package_name}"',
        )
    replace_in_file(
        repo / "pyproject.toml",
        r'^version = "[^"]+"$',
        f'version = "{version}"',
    )
    replace_in_file(
        repo / "hermit" / "__init__.py",
        r'^__version__ = "[^"]+"$',
        f'__version__ = "{version}"',
    )

    if args.update_lock:
        subprocess.run(["uv", "lock"], cwd=repo, check=True)

    if package_name:
        print(f"package-renamed {package_name}")
    print(f"version-bumped {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
