"""WebUI API — evidence signal endpoints."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

router = APIRouter()


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
