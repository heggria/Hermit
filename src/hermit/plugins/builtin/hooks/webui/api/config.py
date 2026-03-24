"""WebUI API — configuration and environment info endpoints."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter

from hermit.plugins.builtin.hooks.webui.api.deps import get_runner

router = APIRouter()

_start_time: float = time.time()


@router.get("/config/status")
async def status() -> dict:
    """Return basic environment info and uptime."""
    return {
        "host": os.environ.get("HERMIT_HOST", "127.0.0.1"),
        "port": int(os.environ.get("HERMIT_PORT", "8323")),
        "uptime_start": _start_time,
        "uptime_seconds": time.time() - _start_time,
        "pid": os.getpid(),
    }


@router.get("/config/plugins")
async def list_plugins() -> list:
    """Return loaded plugin manifests."""
    runner = get_runner()
    items = getattr(runner.pm, "manifests", None) or getattr(runner.pm, "_manifests", [])
    result = []
    for m in items:
        result.append(
            {
                "name": getattr(m, "name", ""),
                "version": getattr(m, "version", ""),
                "description": getattr(m, "description", ""),
                "builtin": getattr(m, "builtin", False),
            }
        )
    return result


@router.get("/config/schedules")
async def list_schedules() -> list:
    """Return scheduled job specs if available."""
    from hermit.plugins.builtin.hooks.webui.api.deps import get_store

    store = get_store()
    fn = getattr(store, "list_schedules", None)
    if fn is None:
        return []
    schedules = fn()
    return [s.__dict__ for s in schedules]
