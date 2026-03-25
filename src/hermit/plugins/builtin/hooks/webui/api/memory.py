"""WebUI API — memory record endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

router = APIRouter()


@router.get("/memories/search")
async def search_memories(q: str = Query(..., min_length=1), limit: int = Query(50, ge=1)) -> list:
    """Search memory records by substring match on content and summary fields."""
    store = get_store()
    records = store.list_memory_records(limit=500)
    query_lower = q.lower()
    matched = []
    for rec in records:
        text = (getattr(rec, "content", "") or "") + " " + (getattr(rec, "summary", "") or "")
        if query_lower in text.lower():
            matched.append(rec.__dict__)
            if len(matched) >= limit:
                break
    return matched


@router.get("/memories/{memory_id}")
async def get_memory(memory_id: str) -> dict:
    """Retrieve a single memory record by ID."""
    store = get_store()
    record = store.get_memory_record(memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return record.__dict__


@router.get("/memories")
async def list_memories(limit: int = Query(50, ge=1)) -> list:
    """List recent memory records."""
    store = get_store()
    records = store.list_memory_records(limit=limit)
    return [r.__dict__ for r in records]
