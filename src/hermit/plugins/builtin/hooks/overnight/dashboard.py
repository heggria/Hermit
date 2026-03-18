"""FastAPI router for overnight dashboard endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore


def create_overnight_router(store: KernelStore) -> APIRouter:
    from hermit.plugins.builtin.hooks.overnight.report import OvernightReportService

    router = APIRouter(prefix="/overnight", tags=["overnight"])
    service = OvernightReportService(store)

    @router.get("/latest")
    async def _latest(lookback: int = 12) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        summary = service.generate(lookback_hours=lookback)
        return service.format_dashboard_json(summary)

    @router.get("/history")
    async def _history() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "not_implemented", "message": "History endpoint coming soon"}

    @router.get("/signals")
    async def _signals(limit: int = 50) -> list[dict[str, Any]]:  # pyright: ignore[reportUnusedFunction]
        if not hasattr(store, "list_signals"):
            return []
        sigs = store.list_signals(limit=limit)
        return [
            {
                "signal_id": s.signal_id,
                "source_kind": s.source_kind,
                "summary": s.summary,
                "disposition": s.disposition,
                "risk_level": s.risk_level,
                "created_at": s.created_at,
            }
            for s in sigs
        ]

    return router
