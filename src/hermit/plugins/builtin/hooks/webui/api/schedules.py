"""WebUI API — scheduled jobs management endpoints."""

from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

_log = structlog.get_logger()

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ScheduleCreateRequest(BaseModel):
    name: str
    cron_expr: str
    goal: str
    policy_profile: str = "autonomous"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schedule_to_dict(job: Any) -> dict[str, Any]:
    """Convert a ScheduledJob to a JSON-safe dict with WebUI-relevant fields."""
    next_run_at = getattr(job, "next_run_at", None)
    # Recompute next_run_at from cron if missing but job is enabled
    if next_run_at is None and getattr(job, "enabled", False):
        cron_expr = getattr(job, "cron_expr", None)
        if cron_expr:
            try:
                from croniter import croniter

                next_run_at = croniter(cron_expr, time.time()).get_next(float)
            except Exception:
                next_run_at = None

    # Count history entries for this job as run_count approximation
    run_count = 0
    try:
        store = get_store()
        history_fn = getattr(store, "list_schedule_history", None)
        if history_fn is not None:
            history = history_fn(job_id=getattr(job, "id", None), limit=9999)
            run_count = len(history)
    except Exception:
        run_count = 0

    return {
        "job_id": getattr(job, "id", None),
        "name": getattr(job, "name", ""),
        "cron_expr": getattr(job, "cron_expr", None),
        "goal": getattr(job, "prompt", ""),
        "policy_profile": getattr(job, "policy_profile", "autonomous"),
        "enabled": getattr(job, "enabled", True),
        "last_run_at": getattr(job, "last_run_at", None),
        "next_run_at": next_run_at,
        "run_count": run_count,
        "schedule_type": getattr(job, "schedule_type", "cron"),
        "created_at": getattr(job, "created_at", None),
    }


# ---------------------------------------------------------------------------
# GET /schedules
# ---------------------------------------------------------------------------


@router.get("/schedules")
def list_schedules() -> list[dict[str, Any]]:
    """List all scheduled jobs."""
    try:
        store = get_store()
        fn = getattr(store, "list_schedules", None)
        if fn is None:
            return []
        jobs = fn()
        return [_schedule_to_dict(job) for job in jobs]
    except HTTPException:
        raise
    except Exception as exc:
        _log.warning("webui_schedules_list_error", error=str(exc))  # type: ignore[call-arg]
        return []


# ---------------------------------------------------------------------------
# POST /schedules
# ---------------------------------------------------------------------------


@router.post("/schedules")
def create_schedule(body: ScheduleCreateRequest) -> dict[str, Any]:
    """Create a new scheduled job."""
    name = body.name.strip()
    cron_expr = body.cron_expr.strip()
    goal = body.goal.strip()

    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty")
    if not cron_expr:
        raise HTTPException(status_code=422, detail="Cron expression must not be empty")
    if not goal:
        raise HTTPException(status_code=422, detail="Goal must not be empty")

    # Validate cron expression
    try:
        from croniter import croniter

        if not croniter.is_valid(cron_expr):
            raise HTTPException(status_code=422, detail=f"Invalid cron expression: {cron_expr!r}")
    except HTTPException:
        raise
    except Exception:
        pass  # croniter not available — skip validation

    try:
        from hermit.plugins.builtin.hooks.scheduler.models import ScheduledJob

        job = ScheduledJob.create(
            name=name,
            prompt=goal,
            schedule_type="cron",
            cron_expr=cron_expr,
        )
        # Attach policy_profile as an ad-hoc attribute so it round-trips via to_dict
        job.policy_profile = body.policy_profile  # type: ignore[attr-defined]

        store = get_store()
        create_fn = getattr(store, "create_schedule", None)
        if create_fn is None:
            raise HTTPException(status_code=503, detail="Schedule store not available")
        create_fn(job)
        return _schedule_to_dict(job)
    except HTTPException:
        raise
    except Exception as exc:
        _log.exception("webui_schedules_create_error", error=str(exc))  # type: ignore[call-arg]
        raise HTTPException(status_code=500, detail=f"Failed to create schedule: {exc}") from exc


# ---------------------------------------------------------------------------
# DELETE /schedules/{job_id}
# ---------------------------------------------------------------------------


@router.delete("/schedules/{job_id}")
def delete_schedule(job_id: str) -> dict[str, Any]:
    """Delete a scheduled job by ID."""
    try:
        store = get_store()
        get_fn = getattr(store, "get_schedule", None)
        if get_fn is not None:
            job = get_fn(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Schedule not found")

        delete_fn = getattr(store, "delete_schedule", None)
        if delete_fn is None:
            raise HTTPException(status_code=503, detail="Schedule store not available")
        deleted = delete_fn(job_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return {"job_id": job_id, "deleted": True}
    except HTTPException:
        raise
    except Exception as exc:
        _log.exception("webui_schedules_delete_error", job_id=job_id, error=str(exc))  # type: ignore[call-arg]
        raise HTTPException(status_code=500, detail=f"Failed to delete schedule: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /schedules/{job_id}/toggle
# ---------------------------------------------------------------------------


@router.post("/schedules/{job_id}/toggle")
def toggle_schedule(job_id: str) -> dict[str, Any]:
    """Enable or disable a scheduled job."""
    try:
        store = get_store()
        get_fn = getattr(store, "get_schedule", None)
        if get_fn is None:
            raise HTTPException(status_code=503, detail="Schedule store not available")
        job = get_fn(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Schedule not found")

        new_enabled = not getattr(job, "enabled", True)
        update_fn = getattr(store, "update_schedule", None)
        if update_fn is None:
            raise HTTPException(status_code=503, detail="Schedule store not available")
        updated = update_fn(job_id, enabled=new_enabled)
        if updated is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return _schedule_to_dict(updated)
    except HTTPException:
        raise
    except Exception as exc:
        _log.exception("webui_schedules_toggle_error", job_id=job_id, error=str(exc))  # type: ignore[call-arg]
        raise HTTPException(status_code=500, detail=f"Failed to toggle schedule: {exc}") from exc
