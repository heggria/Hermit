"""Patterns API router — learned execution patterns and contract templates."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Query

from hermit.plugins.builtin.hooks.webui.api.deps import get_store

_log = structlog.get_logger()

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _memory_to_pattern(record: Any) -> dict[str, Any]:
    """Extract pattern summary from a contract_template memory record."""
    sa: dict[str, Any] = {}
    raw = getattr(record, "structured_assertion", None)
    if isinstance(raw, dict):
        sa = raw

    return {
        "pattern_id": getattr(record, "memory_id", ""),
        "action_type": sa.get("action_class", ""),
        "tool_name": sa.get("tool_name", ""),
        "hit_count": int(sa.get("invocation_count", 0)),
        "success_count": int(sa.get("success_count", 0)),
        "failure_count": int(sa.get("failure_count", 0)),
        "avg_success_rate": float(sa.get("success_rate", 0.0)),
        "risk_level": sa.get("risk_level", ""),
        "reversibility_class": sa.get("reversibility_class", ""),
        "scope_kind": getattr(record, "scope_kind", ""),
        "fingerprint": sa.get("fingerprint", ""),
        "last_seen_at": sa.get("last_used_at"),
        "last_failure_at": sa.get("last_failure_at"),
        "status": getattr(record, "status", ""),
        "confidence": float(getattr(record, "confidence", 0.0) or 0.0),
    }


def _memory_to_template(record: Any) -> dict[str, Any]:
    """Extract template summary from a contract_template memory record."""
    sa: dict[str, Any] = {}
    raw = getattr(record, "structured_assertion", None)
    if isinstance(raw, dict):
        sa = raw

    claim_text = getattr(record, "claim_text", "") or ""
    memory_id: str = getattr(record, "memory_id", "") or ""

    return {
        "template_id": memory_id,
        "template_id_short": memory_id[:8] if len(memory_id) >= 8 else memory_id,
        "action_type": sa.get("action_class", ""),
        "tool_name": sa.get("tool_name", ""),
        "pattern_description": claim_text,
        "confidence": float(getattr(record, "confidence", 0.0) or 0.0),
        "usage_count": int(sa.get("invocation_count", 0)),
        "success_rate": float(sa.get("success_rate", 0.0)),
        "risk_level": sa.get("risk_level", ""),
        "scope_kind": getattr(record, "scope_kind", ""),
        "fingerprint": sa.get("fingerprint", ""),
        "expected_effects": list(sa.get("expected_effects", [])),
        "source_contract_ref": sa.get("source_contract_ref", ""),
        "created_at": getattr(record, "created_at", None),
        "last_used_at": sa.get("last_used_at"),
        "status": getattr(record, "status", ""),
    }


# ---------------------------------------------------------------------------
# GET /patterns
# ---------------------------------------------------------------------------


@router.get("/patterns")
def list_patterns(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List learned execution patterns (contract_template memory records)."""
    store = get_store()
    records: list[Any] = []
    try:
        records = store.list_memory_records(memory_kind="contract_template", limit=limit + offset)
    except Exception as exc:
        _log.warning("patterns.list_error", error=str(exc))

    records = records[offset:]

    # Sort by invocation_count descending
    def _hit_count(r: Any) -> int:
        sa = getattr(r, "structured_assertion", None) or {}
        return int(sa.get("invocation_count", 0)) if isinstance(sa, dict) else 0

    records_sorted = sorted(records, key=_hit_count, reverse=True)
    patterns = [_memory_to_pattern(r) for r in records_sorted]

    return {"patterns": patterns, "total": len(patterns)}


# ---------------------------------------------------------------------------
# GET /patterns/templates
# ---------------------------------------------------------------------------


@router.get("/patterns/templates")
def list_templates(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List contract templates learned by the ContractTemplateLearner.

    Returns active templates only, sorted by confidence descending.
    Falls back to all contract_template records if active filtering fails.
    """
    store = get_store()
    records: list[Any] = []

    try:
        records = store.list_memory_records(
            memory_kind="contract_template", status="active", limit=limit + offset
        )
    except Exception:
        pass

    if not records:
        try:
            records = store.list_memory_records(
                memory_kind="contract_template", limit=limit + offset
            )
        except Exception as exc:
            _log.warning("patterns.templates_list_error", error=str(exc))

    records = records[offset:]

    def _confidence(r: Any) -> float:
        return float(getattr(r, "confidence", 0.0) or 0.0)

    records_sorted = sorted(records, key=_confidence, reverse=True)
    templates = [_memory_to_template(r) for r in records_sorted]

    return {"templates": templates, "total": len(templates)}
