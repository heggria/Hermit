"""Governed glob search — find files matching patterns within workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

_MAX_RESULTS = 200


def handle_glob_files(payload: dict[str, Any]) -> str:
    """Glob for files matching a pattern, returning paths relative to workspace root."""
    pattern = str(payload.get("pattern", "")).strip()
    workspace = str(payload.get("workspace", "")).strip()

    if not pattern:
        return "Error: 'pattern' is required."

    if not workspace:
        return "Error: 'workspace' is required."

    workspace_root = Path(workspace).resolve()

    if not workspace_root.is_dir():
        return f"Error: workspace not found: {workspace}"

    try:
        matches = sorted(workspace_root.glob(pattern))
    except ValueError as exc:
        return f"Error: invalid glob pattern: {exc}"

    # Filter to files only, within workspace
    results: list[str] = []
    for match in matches:
        resolved = match.resolve()
        if not resolved.is_file():
            continue
        if workspace_root not in resolved.parents and resolved != workspace_root:
            continue
        try:
            results.append(str(resolved.relative_to(workspace_root)))
        except ValueError:
            continue

    if not results:
        return f"No files matching '{pattern}'."

    total = len(results)
    truncated = results[:_MAX_RESULTS]

    log.debug("glob_files_ok", pattern=pattern, total=total, returned=len(truncated))

    lines = [f"Found {total} file(s) matching '{pattern}':"]
    lines.extend(truncated)

    if total > _MAX_RESULTS:
        lines.append(f"... and {total - _MAX_RESULTS} more (truncated)")

    return "\n".join(lines)
