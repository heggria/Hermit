#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import tomllib
from pathlib import Path


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def try_git(*args: str) -> str:
    try:
        return git(*args)
    except subprocess.CalledProcessError:
        return ""


def read_project_meta(pyproject_path: Path) -> tuple[str, str]:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    return str(project.get("name", "project")).strip(), str(project.get("version", "")).strip()


def previous_tag(current_tag: str) -> str:
    tags = [line.strip() for line in try_git("tag", "--sort=-creatordate").splitlines() if line.strip()]
    filtered = [tag for tag in tags if tag != current_tag]
    return filtered[0] if filtered else ""


def commit_lines(base: str, head: str) -> list[str]:
    revspec = f"{base}..{head}" if base else head
    output = try_git("log", "--no-merges", "--pretty=format:%h%x09%s", revspec)
    if not output:
        return []
    return [line for line in output.splitlines() if line.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate release notes from git history.")
    parser.add_argument("--tag", required=True, help="Release tag, for example v0.1.0")
    parser.add_argument("--pyproject", default="pyproject.toml", help="Path to pyproject.toml")
    parser.add_argument(
        "--output",
        default="dist/RELEASE_NOTES.md",
        help="Where to write the generated release notes",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_name, version = read_project_meta(Path(args.pyproject))
    prev_tag = previous_tag(args.tag)
    lines = commit_lines(prev_tag, args.tag)

    body: list[str] = [
        f"# {project_name} {version}",
        "",
        "## Summary",
        "",
        f"- Release tag: `{args.tag}`",
        f"- Version: `{version}`",
    ]
    if prev_tag:
        body.append(f"- Previous tag: `{prev_tag}`")
    body.extend(["", "## Changes", ""])

    if lines:
        for line in lines:
            sha, subject = line.split("\t", 1)
            body.append(f"- `{sha}` {subject}")
    else:
        body.append("- No commit summary available.")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
