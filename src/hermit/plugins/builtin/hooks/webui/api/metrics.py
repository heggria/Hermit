"""WebUI API — governance metrics and task summary endpoints."""

from __future__ import annotations

import time
from collections import Counter

from fastapi import APIRouter, Query

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

router = APIRouter()


@router.get("/metrics/governance")
async def governance_metrics(hours: float = Query(24, ge=0.1)) -> dict:
    """Compute governance metrics over a rolling time window."""
    from hermit.kernel.analytics.engine import AnalyticsEngine

    store = get_store()
    engine = AnalyticsEngine(store)
    now = time.time()
    window_start = now - (hours * 3600)
    metrics = engine.compute_metrics(window_start=window_start, window_end=now)
    result = metrics.__dict__.copy()
    # Serialise nested dataclass entries so JSON encoding works
    result["risk_entries"] = [e.__dict__ for e in result.get("risk_entries", [])]
    return result


@router.get("/metrics/summary")
async def task_summary() -> dict:
    """Aggregate task counts grouped by status."""
    store = get_store()
    tasks = store.list_tasks(limit=5000)
    counts: Counter[str] = Counter()
    for t in tasks:
        counts[t.status] += 1
    return {
        "total": len(tasks),
        "by_status": dict(counts),
    }
