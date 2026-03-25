"""Governed file reader with path validation and size limits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()

_MAX_READ_BYTES = 50 * 1024  # 50KB


def handle_read_file(payload: dict[str, Any]) -> str:
    """Read a file with path validation and size limit enforcement."""
    raw_path = str(payload.get("path", "")).strip()
    workspace = str(payload.get("workspace", "")).strip()

    if not raw_path:
        return "Error: 'path' is required."

    if not workspace:
        return "Error: 'workspace' is required."

    workspace_root = Path(workspace).resolve()
    target = (workspace_root / raw_path).resolve()

    # Prevent path traversal outside workspace
    if workspace_root not in target.parents and target != workspace_root:
        log.warning("read_file_path_escape", path=raw_path, workspace=workspace)
        return f"Error: path escapes workspace: {raw_path}"

    if not target.exists():
        return f"Error: file not found: {raw_path}"

    if target.is_dir():
        return f"Error: path is a directory: {raw_path}"

    file_size = target.stat().st_size
    if file_size > _MAX_READ_BYTES:
        return (
            f"Error: file too large ({file_size} bytes, max {_MAX_READ_BYTES}). "
            f"Use glob_files to find specific files, or read a smaller file."
        )

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: file is not valid UTF-8: {raw_path}"
    except OSError as exc:
        return f"Error reading file: {exc}"

    relative = target.relative_to(workspace_root)
    log.debug("read_file_ok", path=str(relative), size=len(content))
    return content
