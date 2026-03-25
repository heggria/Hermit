"""Reconciliation API router — lists reconciliation decisions and failure recovery logs."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

_log = structlog.get_logger()

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return record
    d = dict(record.__dict__)
    d.pop("_sa_instance_state", None)
    return d


def _reconciliation_summary(record: Any) -> dict[str, Any]:
    d = _record_dict(record)
    return {
        "reconciliation_id": d.get("reconciliation_id"),
        "task_id": d.get("task_id"),
        "step_id": d.get("step_id"),
        "step_attempt_id": d.get("step_attempt_id"),
        "contract_ref": d.get("contract_ref"),
        "result_class": d.get("result_class", "ambiguous"),
        "recommended_resolution": d.get("recommended_resolution", ""),
        "authorized_effect_summary": d.get("authorized_effect_summary", ""),
        "observed_effect_summary": d.get("observed_effect_summary", ""),
        "receipt_refs": d.get("receipt_refs") or [],
        "observed_output_refs": d.get("observed_output_refs") or [],
        "confidence_delta": d.get("confidence_delta", 0.0),
        "operator_summary": d.get("operator_summary"),
        "created_at": d.get("created_at"),
    }


# ---------------------------------------------------------------------------
# GET /reconciliation
# ---------------------------------------------------------------------------


@router.get("/reconciliation")
def list_reconciliations(
    task_id: str | None = Query(None, description="Filter by task ID"),
    result_class: str | None = Query(None, description="Filter by result class"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
) -> dict[str, Any]:
    """List reconciliation records with optional filters and pagination."""
    store = get_store()

    try:
        kwargs: dict[str, Any] = {"limit": limit + offset}
        if task_id:
            kwargs["task_id"] = task_id
        records = store.list_reconciliations(**kwargs)
    except Exception as exc:
        _log.warning("webui.reconciliation.list_error", error=str(exc))
        records = []

    records = records[offset:]

    if result_class:
        records = [r for r in records if getattr(r, "result_class", "") == result_class]

    summaries = [_reconciliation_summary(r) for r in records]

    # Compute verdict distribution for header badges
    distribution: dict[str, int] = {}
    for s in summaries:
        rc = s.get("result_class") or "ambiguous"
        distribution[rc] = distribution.get(rc, 0) + 1

    return {
        "reconciliations": summaries,
        "total": len(summaries),
        "limit": limit,
        "offset": offset,
        "distribution": distribution,
    }


# ---------------------------------------------------------------------------
# GET /reconciliation/{record_id}
# ---------------------------------------------------------------------------


@router.get("/reconciliation/{record_id}")
def get_reconciliation(record_id: str) -> dict[str, Any]:
    """Get full detail for a single reconciliation record."""
    store = get_store()

    try:
        record = store.get_reconciliation(record_id)
    except Exception as exc:
        _log.warning("webui.reconciliation.get_error", record_id=record_id, error=str(exc))
        record = None

    if record is None:
        raise HTTPException(status_code=404, detail="Reconciliation record not found")

    d = _record_dict(record)
    return {
        "reconciliation_id": d.get("reconciliation_id"),
        "task_id": d.get("task_id"),
        "step_id": d.get("step_id"),
        "step_attempt_id": d.get("step_attempt_id"),
        "contract_ref": d.get("contract_ref"),
        "result_class": d.get("result_class", "ambiguous"),
        "recommended_resolution": d.get("recommended_resolution", ""),
        "intended_effect_summary": d.get("intended_effect_summary", ""),
        "authorized_effect_summary": d.get("authorized_effect_summary", ""),
        "observed_effect_summary": d.get("observed_effect_summary", ""),
        "receipted_effect_summary": d.get("receipted_effect_summary", ""),
        "receipt_refs": d.get("receipt_refs") or [],
        "observed_output_refs": d.get("observed_output_refs") or [],
        "confidence_delta": d.get("confidence_delta", 0.0),
        "rollback_recommendation_ref": d.get("rollback_recommendation_ref"),
        "invalidated_belief_refs": d.get("invalidated_belief_refs") or [],
        "superseded_memory_refs": d.get("superseded_memory_refs") or [],
        "promoted_template_ref": d.get("promoted_template_ref"),
        "promoted_memory_refs": d.get("promoted_memory_refs") or [],
        "operator_summary": d.get("operator_summary"),
        "final_state_witness_ref": d.get("final_state_witness_ref"),
        "created_at": d.get("created_at"),
    }
