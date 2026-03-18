from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from hermit.kernel.context.memory.anti_pattern import AntiPatternService
from hermit.kernel.context.memory.decay import MemoryDecayService
from hermit.kernel.context.memory.reflect import ReflectionService

if TYPE_CHECKING:
    from hermit.kernel.context.memory.embeddings import EmbeddingService
    from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()

_DEDUP_SIMILARITY_THRESHOLD = 0.9
_STRENGTHEN_REFERENCE_THRESHOLD = 3
_STRENGTHEN_INCREMENT = 0.1
_CONFIDENCE_CAP = 0.95
_CONSOLIDATION_CRON = "17 3 * * *"


@dataclass
class ConsolidationReport:
    """Summary of a complete consolidation cycle."""

    consolidated_at: float
    merged_count: int = 0
    strengthened_count: int = 0
    decayed_count: int = 0
    new_insights_count: int = 0
    new_pitfalls_count: int = 0


class ConsolidationService:
    """Dream Cycle: periodic consolidation of the memory store.

    Runs five passes:
    1. Dedup — merge semantically identical memories
    2. Strengthen — boost frequently referenced memories
    3. Decay — run decay sweep
    4. Reflect — synthesize higher-order insights
    5. Anti-pattern — detect and invert failing memories
    """

    def __init__(
        self,
        *,
        decay_service: MemoryDecayService | None = None,
        reflection_service: ReflectionService | None = None,
        anti_pattern_service: AntiPatternService | None = None,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self._decay = decay_service or MemoryDecayService()
        self._reflect = reflection_service or ReflectionService()
        self._anti_pattern = anti_pattern_service or AntiPatternService()
        self._embeddings = embedding_service

    def run_consolidation(
        self,
        store: KernelStore,
    ) -> ConsolidationReport:
        """Run a complete consolidation cycle."""
        now = time.time()
        report = ConsolidationReport(consolidated_at=now)

        log.info("consolidation_cycle_start")

        # Pass 1: Dedup
        report.merged_count = self._dedup_pass(store)

        # Pass 2: Strengthen
        report.strengthened_count = self._strengthen_pass(store)

        # Pass 3: Decay
        sweep = self._decay.run_decay_sweep(store, now=now)
        report.decayed_count = len(sweep.transitions)

        # Pass 4: Reflect
        insights = self._reflect.reflect(store, limit=20)
        for insight in insights:
            promoted = self._reflect.promote_insight(insight, store)
            if promoted is not None:
                report.new_insights_count += 1

        # Pass 5: Anti-pattern
        pitfalls = self._anti_pattern.detect_pitfalls(store)
        for candidate in pitfalls[:5]:
            inverted = self._anti_pattern.invert_to_pitfall(candidate.memory_id, store)
            if inverted is not None:
                report.new_pitfalls_count += 1

        log.info(
            "consolidation_cycle_complete",
            merged=report.merged_count,
            strengthened=report.strengthened_count,
            decayed=report.decayed_count,
            insights=report.new_insights_count,
            pitfalls=report.new_pitfalls_count,
        )
        return report

    def _dedup_pass(self, store: KernelStore) -> int:
        """Merge semantically duplicate memories."""
        records = store.list_memory_records(status="active", limit=2000)
        durable = [r for r in records if r.memory_kind in {"durable_fact", "pitfall_warning"}]

        merged = 0
        seen: set[str] = set()

        for i, a in enumerate(durable):
            if a.memory_id in seen:
                continue
            for b in durable[i + 1 :]:
                if b.memory_id in seen:
                    continue
                sim = self._text_similarity(a.claim_text, b.claim_text)
                if sim >= _DEDUP_SIMILARITY_THRESHOLD:
                    # Keep higher confidence, invalidate lower
                    winner, loser = (a, b) if a.confidence >= b.confidence else (b, a)
                    store.update_memory_record(
                        loser.memory_id,
                        status="invalidated",
                        invalidation_reason=f"dedup_merged_into:{winner.memory_id}",
                        invalidated_at=time.time(),
                    )
                    seen.add(loser.memory_id)
                    merged += 1

        return merged

    def _strengthen_pass(self, store: KernelStore) -> int:
        """Boost confidence for frequently referenced memories."""
        records = store.list_memory_records(status="active", limit=2000)
        strengthened = 0

        for record in records:
            if record.memory_kind in {"episode_index", "influence_link"}:
                continue
            assertion = dict(record.structured_assertion or {})
            ref_count = assertion.get("reference_count", 0)
            if not isinstance(ref_count, int):
                continue
            if ref_count >= _STRENGTHEN_REFERENCE_THRESHOLD:
                new_confidence = min(
                    record.confidence + _STRENGTHEN_INCREMENT,
                    _CONFIDENCE_CAP,
                )
                if new_confidence > record.confidence:
                    store.update_memory_record(
                        record.memory_id,
                        structured_assertion={
                            **assertion,
                            "strengthened_at": time.time(),
                            "previous_confidence": record.confidence,
                        },
                    )
                    strengthened += 1

        return strengthened

    def _text_similarity(self, a: str, b: str) -> float:
        """Compute text similarity, using embeddings if available, else token overlap."""
        if self._embeddings is not None:
            try:
                vec_a = self._embeddings.embed(a)
                vec_b = self._embeddings.embed(b)
                return self._embeddings.similarity(vec_a, vec_b)
            except Exception:
                pass

        # Token overlap fallback
        tokens_a = set(a.lower().split())
        tokens_b = set(b.lower().split())
        if not tokens_a or not tokens_b:
            return 0.0
        overlap = len(tokens_a & tokens_b)
        return overlap / len(tokens_a | tokens_b)


__all__ = ["ConsolidationReport", "ConsolidationService"]
