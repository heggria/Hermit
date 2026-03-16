#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import tomllib
import zipfile
from pathlib import Path


def read_project_name(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    name = str(project.get("name", "")).strip()
    if not name:
        raise SystemExit("pyproject.toml is missing [project].name")
    return name


def read_runtime_version(init_path: Path) -> str:
    text = init_path.read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', text, flags=re.MULTILINE)
    if not match:
        raise SystemExit(f"Could not find __version__ in {init_path}")
    return match.group(1)


def read_pyproject_version(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    version = str(project.get("version", "")).strip()
    if not version:
        raise SystemExit("pyproject.toml is missing [project].version")
    return version


def normalize_tag(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate that project version, git tag, and built artifacts agree."
    )
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        help="Path to pyproject.toml",
    )
    parser.add_argument(
        "--tag",
        default="",
        help="Git tag name to validate against, for example v0.1.0",
    )
    parser.add_argument(
        "--dist-dir",
        default="",
        help="Optional dist directory to validate built artifacts against",
    )
    return parser.parse_args()


def validate_tag(version: str, tag: str) -> None:
    if not tag:
        return
    normalized = normalize_tag(tag)
    if normalized != version:
        raise SystemExit(f"Version mismatch: pyproject.toml has {version}, but git tag is {tag}")


def normalized_names(project_name: str) -> set[str]:
    raw = project_name.strip()
    hyphen = re.sub(r"[-_.]+", "-", raw)
    underscore = re.sub(r"[-_.]+", "_", raw)
    return {
        raw,
        hyphen,
        underscore,
    }


def expected_artifacts(project_name: str, version: str) -> set[str]:
    names = normalized_names(project_name)
    files: set[str] = set()
    for name in names:
        files.add(f"{name}-{version}.tar.gz")
        files.add(f"{name}-{version}-py3-none-any.whl")
    return files


def validate_dist(project_name: str, version: str, dist_dir: Path) -> None:
    if not dist_dir.exists():
        raise SystemExit(f"dist directory not found: {dist_dir}")

    files = {path.name for path in dist_dir.iterdir() if path.is_file()}
    expected = expected_artifacts(project_name, version)
    matched = sorted(expected & files)
    if len(matched) < 2:
        raise SystemExit(
            f"Built artifacts do not match package={project_name} version={version}. "
            f"Found: {', '.join(sorted(files))}"
        )

    wheel_candidates = [
        dist_dir / f"{name}-{version}-py3-none-any.whl" for name in normalized_names(project_name)
    ]
    wheel_path = next((path for path in wheel_candidates if path.exists()), None)
    if wheel_path is None:
        raise SystemExit(f"Could not find wheel for package={project_name} version={version}")

    with zipfile.ZipFile(wheel_path) as zf:
        metadata_name = next(
            (name for name in zf.namelist() if re.fullmatch(r".+\.dist-info/METADATA", name)),
            None,
        )
        if metadata_name is None:
            raise SystemExit(f"Could not find METADATA inside {wheel_path.name}")
        metadata = zf.read(metadata_name).decode("utf-8")

    metadata_version = ""
    for line in metadata.splitlines():
        if line.startswith("Version: "):
            metadata_version = line.split(": ", 1)[1].strip()
            break

    if metadata_version != version:
        raise SystemExit(
            f"Wheel METADATA version mismatch: expected {version}, got {metadata_version or '<empty>'}"
        )


def main() -> int:
    args = parse_args()
    pyproject_path = Path(args.pyproject)
    project_name = read_project_name(pyproject_path)
    version = read_pyproject_version(pyproject_path)
    runtime_version = read_runtime_version(pyproject_path.parent / "src" / "hermit" / "__init__.py")
    if runtime_version != version:
        raise SystemExit(
            f"Version mismatch: pyproject.toml has {version}, but hermit.__version__ is {runtime_version}"
        )
    validate_tag(version, args.tag)
    if args.dist_dir:
        validate_dist(project_name, version, Path(args.dist_dir))
    print(f"version-check-ok {project_name} {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
