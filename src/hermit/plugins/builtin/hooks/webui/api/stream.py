"""SSE stream endpoint — pushes task updates and pending approvals to the WebUI."""

from __future__ import annotations

import asyncio
import json
import time

import structlog
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

_log = structlog.get_logger()

router = APIRouter()

_POLL_INTERVAL = 1.0
_APPROVAL_INTERVAL = 2.0
_HEARTBEAT_INTERVAL = 15.0


@router.get("/stream/events")
async def stream_events() -> EventSourceResponse:
    """Server-Sent Events stream for real-time task and approval updates."""

    async def event_generator():
        last_task_states: dict[str, str] = {}
        last_approval_check = 0.0
        last_heartbeat = time.monotonic()

        while True:
            try:
                store = get_store()

                # --- Task updates ---
                tasks = store.list_tasks(limit=50)
                for task in tasks:
                    task_dict = task.__dict__
                    tid = task_dict.get("task_id", "")
                    current_status = task_dict.get("status", "")
                    updated_at = task_dict.get("updated_at", 0)
                    key = f"{current_status}:{updated_at}"

                    if tid and (tid not in last_task_states or last_task_states[tid] != key):
                        last_task_states[tid] = key
                        yield {
                            "event": "task.update",
                            "data": json.dumps(task_dict, default=str),
                        }

                # --- Pending approvals (throttled) ---
                now = time.time()
                if now - last_approval_check > _APPROVAL_INTERVAL:
                    last_approval_check = now
                    approvals = store.list_approvals(status="pending", limit=20)
                    if approvals:
                        yield {
                            "event": "approvals.pending",
                            "data": json.dumps([a.__dict__ for a in approvals], default=str),
                        }

                # --- Heartbeat ---
                mono_now = time.monotonic()
                if mono_now - last_heartbeat >= _HEARTBEAT_INTERVAL:
                    last_heartbeat = mono_now
                    yield {"event": "heartbeat", "data": ""}

            except asyncio.CancelledError:
                _log.debug("sse_stream_cancelled")
                return
            except Exception:
                # Store not available yet or transient error — silently retry
                pass

            await asyncio.sleep(_POLL_INTERVAL)

    return EventSourceResponse(event_generator())
