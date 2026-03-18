from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()

# Half-life in days per retention class
_HALF_LIFE_DAYS: dict[str, float] = {
    "user_preference": 180.0,
    "project_convention": 90.0,
    "tooling_environment": 60.0,
    "volatile_fact": 14.0,
    "task_state": 7.0,
}
_DEFAULT_HALF_LIFE_DAYS = 30.0


@dataclass
class ConfidenceReport:
    """Summary of batch confidence recomputation."""

    recomputed_at: float
    total_evaluated: int = 0
    below_threshold: int = 0
    refreshed: int = 0


class ConfidenceDecayService:
    """Half-life based confidence decay model.

    Effective confidence = base_confidence * (0.5 ** (age / half_life))
    where age resets on each reference (last_accessed_at update).
    """

    def compute_confidence(
        self,
        memory: MemoryRecord,
        *,
        now: float | None = None,
    ) -> float:
        """Compute effective confidence using half-life decay."""
        now = now or time.time()
        base = memory.confidence
        half_life = self._half_life_for(memory)

        # Use last_accessed_at from structured_assertion, or last_validated_at, or created_at
        assertion = dict(memory.structured_assertion or {})
        last_accessed = assertion.get("last_accessed_at")
        if last_accessed is not None:
            reference_time = float(last_accessed)
        elif memory.last_validated_at is not None:
            reference_time = memory.last_validated_at
        else:
            reference_time = memory.created_at or now

        age_days = (now - reference_time) / 86400.0
        if age_days <= 0:
            return base

        decay_factor = math.pow(0.5, age_days / half_life)
        return round(base * decay_factor, 4)

    def refresh_on_reference(
        self,
        memory_id: str,
        store: KernelStore,
        *,
        now: float | None = None,
    ) -> None:
        """Update last_accessed_at to reset the decay clock when a memory is referenced."""
        now = now or time.time()
        record = store.get_memory_record(memory_id)
        if record is None or record.status != "active":
            return
        store.update_memory_record(
            memory_id,
            last_validated_at=now,
            structured_assertion={
                **dict(record.structured_assertion or {}),
                "last_accessed_at": now,
            },
        )

    def batch_recompute(
        self,
        store: KernelStore,
        *,
        now: float | None = None,
        low_confidence_threshold: float = 0.1,
    ) -> ConfidenceReport:
        """Batch recompute effective confidence for all active memories."""
        now = now or time.time()
        records = store.list_memory_records(status="active", limit=5000)

        report = ConfidenceReport(recomputed_at=now, total_evaluated=len(records))

        for record in records:
            effective = self.compute_confidence(record, now=now)
            if effective < low_confidence_threshold:
                report.below_threshold += 1

            # Store effective confidence in structured_assertion for retrieval scoring
            store.update_memory_record(
                record.memory_id,
                structured_assertion={
                    **dict(record.structured_assertion or {}),
                    "effective_confidence": effective,
                    "confidence_computed_at": now,
                },
            )

        log.info(
            "confidence_batch_recompute",
            total=report.total_evaluated,
            below_threshold=report.below_threshold,
        )
        return report

    @staticmethod
    def _half_life_for(memory: MemoryRecord) -> float:
        retention = memory.retention_class or "volatile_fact"
        return _HALF_LIFE_DAYS.get(retention, _DEFAULT_HALF_LIFE_DAYS)


__all__ = ["ConfidenceDecayService", "ConfidenceReport"]
