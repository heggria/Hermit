"""WebUI API — memory analytics and aggregate statistics endpoint."""

from __future__ import annotations

import time
from collections import Counter

from fastapi import APIRouter

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

router = APIRouter()

_24H = 86400.0


@router.get("/memory/stats")
async def memory_stats() -> dict:
    """Aggregate memory statistics across all memory records."""
    store = get_store()

    try:
        records = store.list_memory_records(limit=5000)
    except Exception:
        records = []

    total = len(records)
    now = time.time()
    cutoff = now - _24H

    by_status: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    confidence_sum = 0.0
    high_confidence_count = 0
    low_confidence_count = 0
    evidence_backed_count = 0
    recent_promotions = 0

    for rec in records:
        # Status
        status = getattr(rec, "status", "unknown") or "unknown"
        by_status[status] += 1

        # Category
        category = getattr(rec, "category", "unknown") or "unknown"
        by_category[category] += 1

        # Confidence
        confidence = float(getattr(rec, "confidence", 0.5) or 0.5)
        confidence_sum += confidence
        if confidence > 0.8:
            high_confidence_count += 1
        elif confidence < 0.3:
            low_confidence_count += 1

        # Evidence-backed
        evidence_refs = getattr(rec, "evidence_refs", None) or []
        if evidence_refs:
            evidence_backed_count += 1

        # Recent promotions (created in last 24h)
        created_at = getattr(rec, "created_at", None)
        if created_at is not None and float(created_at) >= cutoff:
            recent_promotions += 1

    avg_confidence = round(confidence_sum / total, 4) if total > 0 else 0.0

    return {
        "total": total,
        "by_status": dict(by_status),
        "by_category": dict(by_category),
        "avg_confidence": avg_confidence,
        "high_confidence_count": high_confidence_count,
        "low_confidence_count": low_confidence_count,
        "evidence_backed_count": evidence_backed_count,
        "recent_promotions": recent_promotions,
    }
