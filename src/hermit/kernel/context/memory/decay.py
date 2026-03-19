from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import structlog

from hermit.kernel.context.memory.decay_models import (
    DecaySweepReport,
    DecaySweepTransition,
    FreshnessAssessment,
    FreshnessState,
)

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()

# Freshness thresholds as fractions of TTL consumed
_FRESH_THRESHOLD = 0.50  # < 50% TTL consumed → fresh
_AGING_THRESHOLD = 0.75  # 50-75% → aging
_STALE_THRESHOLD = 0.90  # 75-90% → stale
# > 90% → expired

# Default TTL in seconds when memory has no explicit expires_at
_DEFAULT_TTL_SECONDS: dict[str, int] = {
    "volatile_fact": 24 * 60 * 60,
    "task_state": 7 * 24 * 60 * 60,
    "user_preference": 365 * 24 * 60 * 60,
    "project_convention": 180 * 24 * 60 * 60,
    "tooling_environment": 120 * 24 * 60 * 60,
}
_FALLBACK_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days


class MemoryDecayService:
    """Four-state memory decay governance.

    Evaluates memory freshness on a continuous spectrum rather than
    the binary active/expired model. Supports quarantine (soft-delete)
    and revival of memories with new evidence.
    """

    def evaluate_freshness(
        self,
        memory: MemoryRecord,
        *,
        now: float | None = None,
    ) -> FreshnessAssessment:
        """Assess the freshness state of a single memory record."""
        now = now or time.time()
        created_at = memory.created_at or now
        last_accessed_at = _last_accessed(memory)

        ttl_seconds = self._effective_ttl(memory)
        age_seconds = now - created_at
        ttl_days = ttl_seconds / 86400.0
        age_days = age_seconds / 86400.0
        pct_consumed = min(age_seconds / ttl_seconds, 1.0) if ttl_seconds > 0 else 1.0
        pct_remaining = max(1.0 - pct_consumed, 0.0)

        last_accessed_days_ago: float | None = None
        if last_accessed_at is not None:
            last_accessed_days_ago = (now - last_accessed_at) / 86400.0

        state = self._state_from_pct(pct_consumed)

        return FreshnessAssessment(
            memory_id=memory.memory_id,
            freshness_state=state,
            age_days=age_days,
            ttl_days=ttl_days,
            pct_remaining=pct_remaining,
            last_accessed_days_ago=last_accessed_days_ago,
        )

    def run_decay_sweep(
        self,
        store: KernelStore,
        *,
        now: float | None = None,
    ) -> DecaySweepReport:
        """Sweep all active memories, update freshness_class, collect quarantine candidates."""
        now = now or time.time()
        sweep_id = f"sweep-{uuid.uuid4().hex[:12]}"
        records = store.list_memory_records(status="active", limit=5000)

        transitions: list[DecaySweepTransition] = []
        quarantine_candidates: list[str] = []

        for record in records:
            if record.retention_class == "audit":
                continue

            assessment = self.evaluate_freshness(record, now=now)
            new_state = assessment.freshness_state.value
            old_state = _freshness_class(record)

            if old_state != new_state:
                transitions.append(
                    DecaySweepTransition(
                        memory_id=record.memory_id,
                        previous_state=old_state,
                        new_state=new_state,
                    )
                )
                store.update_memory_record(
                    record.memory_id,
                    structured_assertion={
                        **dict(record.structured_assertion or {}),
                        "freshness_class": new_state,
                        "last_decay_sweep": sweep_id,
                    },
                )

            if assessment.freshness_state == FreshnessState.EXPIRED:
                quarantine_candidates.append(record.memory_id)

        report = DecaySweepReport(
            sweep_id=sweep_id,
            swept_at=now,
            total_evaluated=len(records),
            transitions=transitions,
            quarantine_candidates=quarantine_candidates,
        )
        log.info(
            "decay_sweep_complete",
            sweep_id=sweep_id,
            evaluated=report.total_evaluated,
            transitions=len(transitions),
            quarantine_candidates=len(quarantine_candidates),
        )
        return report

    def quarantine(
        self,
        store: KernelStore,
        memory_id: str,
        reason: str,
    ) -> bool:
        """Move an expired memory to quarantine status."""
        record = store.get_memory_record(memory_id)
        if record is None or record.status != "active":
            return False
        store.update_memory_record(
            memory_id,
            status="quarantined",
            invalidation_reason=f"decay_quarantine: {reason}",
            invalidated_at=time.time(),
        )
        log.info("memory_quarantined", memory_id=memory_id, reason=reason)
        return True

    def revive(
        self,
        store: KernelStore,
        memory_id: str,
        new_evidence_refs: list[str],
    ) -> bool:
        """Revive a quarantined memory with fresh evidence, resetting its decay clock."""
        record = store.get_memory_record(memory_id)
        if record is None or record.status != "quarantined":
            return False
        now = time.time()
        existing_evidence = list(record.evidence_refs or [])
        merged_evidence = existing_evidence + [
            ref for ref in new_evidence_refs if ref not in existing_evidence
        ]
        store.update_memory_record(
            memory_id,
            status="active",
            invalidation_reason=None,
            invalidated_at=None,
            last_validated_at=now,
            validation_basis=f"revived with {len(new_evidence_refs)} new evidence refs",
            structured_assertion={
                **dict(record.structured_assertion or {}),
                "freshness_class": FreshnessState.FRESH.value,
                "evidence_refs": merged_evidence,
                "revived_at": now,
            },
        )
        log.info(
            "memory_revived",
            memory_id=memory_id,
            new_evidence_count=len(new_evidence_refs),
        )
        return True

    @staticmethod
    def _effective_ttl(memory: MemoryRecord) -> float:
        """Determine the TTL for a memory, using explicit expires_at or category defaults."""
        if memory.expires_at is not None and memory.created_at is not None:
            return max(memory.expires_at - memory.created_at, 1.0)
        retention = memory.retention_class or "volatile_fact"
        return float(_DEFAULT_TTL_SECONDS.get(retention, _FALLBACK_TTL_SECONDS))

    @staticmethod
    def _state_from_pct(pct_consumed: float) -> FreshnessState:
        if pct_consumed < _FRESH_THRESHOLD:
            return FreshnessState.FRESH
        if pct_consumed < _AGING_THRESHOLD:
            return FreshnessState.AGING
        if pct_consumed < _STALE_THRESHOLD:
            return FreshnessState.STALE
        return FreshnessState.EXPIRED


def _last_accessed(memory: MemoryRecord) -> float | None:
    """Extract last_accessed_at from structured_assertion or fall back to last_validated_at."""
    assertion = dict(memory.structured_assertion or {})
    val = assertion.get("last_accessed_at")
    if val is not None:
        return float(val)
    return memory.last_validated_at


def _freshness_class(memory: MemoryRecord) -> str | None:
    """Extract freshness_class from structured_assertion."""
    assertion = dict(memory.structured_assertion or {})
    val = assertion.get("freshness_class")
    return str(val) if val is not None else None


__all__ = ["MemoryDecayService"]
