"""In-memory store for active tool calls — enables real-time tool visibility in WebUI."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_active: dict[str, dict[str, Any]] = {}


def set_active(task_id: str, tool_name: str, input_summary: str) -> None:
    """Record that *task_id* is currently executing *tool_name*."""
    with _lock:
        _active[task_id] = {
            "task_id": task_id,
            "tool_name": tool_name,
            "input_summary": input_summary,
            "started_at": time.time(),
        }


def clear(task_id: str) -> None:
    """Clear the active tool entry for *task_id*."""
    with _lock:
        _active.pop(task_id, None)


def get_all() -> dict[str, dict[str, Any]]:
    """Return a snapshot of all active tool calls."""
    with _lock:
        return dict(_active)
