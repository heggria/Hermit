from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from hermit.kernel.context.memory.lineage import MemoryLineageService

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()

_PITFALL_CONFIDENCE_MULTIPLIER = 4.0
_PITFALL_CONFIDENCE_CAP = 0.95


@dataclass(frozen=True)
class PitfallCandidate:
    """A memory identified as a potential anti-pattern to invert."""

    memory_id: str
    claim_text: str
    failure_rate: float
    decision_count: int
    category: str


class AntiPatternService:
    """Detects and inverts high-failure memories into pitfall warnings.

    Depends on MemoryLineageService to find memories with high failure rates.
    """

    def __init__(self, lineage_service: MemoryLineageService | None = None) -> None:
        self._lineage = lineage_service or MemoryLineageService()

    def detect_pitfalls(
        self,
        store: KernelStore,
        *,
        min_decisions: int = 5,
        failure_rate_threshold: float = 0.5,
    ) -> list[PitfallCandidate]:
        """Find memories with failure_rate > threshold and decision_count >= min_decisions."""
        stale = self._lineage.find_stale_influencers(
            store,
            min_decisions=min_decisions,
            failure_rate_threshold=failure_rate_threshold,
        )
        candidates = [
            PitfallCandidate(
                memory_id=s.memory_id,
                claim_text=s.claim_text,
                failure_rate=s.failure_rate,
                decision_count=s.decision_count,
                category=s.category,
            )
            for s in stale
        ]
        if candidates:
            log.info("pitfall_candidates_detected", count=len(candidates))
        return candidates

    def invert_to_pitfall(
        self,
        memory_id: str,
        store: KernelStore,
        *,
        task_id: str = "",
        conversation_id: str | None = None,
    ) -> MemoryRecord | None:
        """Invert a failing memory into a pitfall_warning, invalidating the original."""
        original = store.get_memory_record(memory_id)
        if original is None or original.status != "active":
            return None

        pitfall_confidence = min(
            original.confidence * _PITFALL_CONFIDENCE_MULTIPLIER,
            _PITFALL_CONFIDENCE_CAP,
        )
        pitfall_text = f"PITFALL: {original.claim_text}"

        # Invalidate original
        store.update_memory_record(
            memory_id,
            status="invalidated",
            invalidation_reason="inverted_to_pitfall",
            invalidated_at=time.time(),
        )

        # Create pitfall warning
        pitfall = store.create_memory_record(
            task_id=task_id or original.task_id,
            conversation_id=conversation_id or original.conversation_id,
            category=original.category,
            claim_text=pitfall_text,
            structured_assertion={
                "original_memory_id": memory_id,
                "original_claim": original.claim_text,
                "inverted_at": time.time(),
                "pitfall_type": "failure_inversion",
            },
            scope_kind=original.scope_kind,
            scope_ref=original.scope_ref,
            promotion_reason="pitfall_inversion",
            retention_class="pitfall_warning",
            memory_kind="pitfall_warning",
            confidence=pitfall_confidence,
            trust_tier="durable",
        )

        log.info(
            "memory_inverted_to_pitfall",
            original_id=memory_id,
            pitfall_id=pitfall.memory_id,
            confidence=pitfall_confidence,
        )
        return pitfall


__all__ = ["AntiPatternService", "PitfallCandidate"]
