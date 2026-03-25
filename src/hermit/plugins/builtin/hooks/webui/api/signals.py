"""WebUI API — evidence signal endpoints."""

from __future__ import annotations

import time
from collections import Counter

from fastapi import APIRouter, HTTPException, Query

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

router = APIRouter()


@router.get("/signals/stats")
async def signal_stats() -> dict:
    """Aggregate statistics across all evidence signals."""
    store = get_store()
    try:
        records = store.list_signals(limit=5000)
    except Exception:
        records = []

    total = len(records)
    now = time.time()
    cutoff_24h = now - 86400.0

    by_disposition: Counter[str] = Counter()
    by_risk: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    confidence_sum = 0.0
    high_risk_count = 0
    recent_count = 0

    for rec in records:
        disposition = getattr(rec, "disposition", "unknown") or "unknown"
        by_disposition[disposition] += 1

        risk = getattr(rec, "risk_level", "unknown") or "unknown"
        by_risk[risk] += 1
        if risk in ("high", "critical"):
            high_risk_count += 1

        source = getattr(rec, "source_kind", "unknown") or "unknown"
        by_source[source] += 1

        confidence_sum += float(getattr(rec, "confidence", 0.5) or 0.5)

        created_at = getattr(rec, "created_at", None)
        if created_at is not None and float(created_at) >= cutoff_24h:
            recent_count += 1

    avg_confidence = round(confidence_sum / total, 4) if total > 0 else 0.0
    pending_count = by_disposition.get("pending", 0)

    return {
        "total": total,
        "pending_count": pending_count,
        "high_risk_count": high_risk_count,
        "avg_confidence": avg_confidence,
        "recent_count": recent_count,
        "by_disposition": dict(by_disposition),
        "by_risk": dict(by_risk),
        "by_source": dict(by_source),
    }


@router.get("/signals")
async def list_signals(limit: int = Query(50, ge=1)) -> list:
    """List recent evidence signals, newest first."""
    store = get_store()
    signals = store.list_signals(limit=limit)
    return [s.__dict__ for s in signals]


@router.get("/signals/actionable")
async def actionable_signals(limit: int = Query(50, ge=1)) -> list:
    """List pending, non-expired, non-steering signals."""
    store = get_store()
    fn = getattr(store, "actionable_signals", None)
    if fn is None:
        raise HTTPException(status_code=501, detail="actionable_signals not available")
    signals = fn(limit=limit)
    return [s.__dict__ for s in signals]


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: str) -> dict:
    """Retrieve a single signal by ID."""
    store = get_store()
    signal = store.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal.__dict__


@router.post("/signals/{signal_id}/act")
async def act_on_signal(signal_id: str) -> dict:
    """Mark a signal as acted upon."""
    store = get_store()
    signal = store.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    store.update_signal_disposition(signal_id, "acted", acted_at=time.time())
    return {"status": "acted", "signal_id": signal_id}


@router.post("/signals/{signal_id}/suppress")
async def suppress_signal(signal_id: str) -> dict:
    """Mark a signal as suppressed."""
    store = get_store()
    signal = store.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    store.update_signal_disposition(signal_id, "suppressed")
    return {"status": "suppressed", "signal_id": signal_id}
