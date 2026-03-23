from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any, cast

import structlog

from hermit.kernel.context.memory.confidence import ConfidenceDecayService
from hermit.kernel.context.memory.decay import MemoryDecayService
from hermit.kernel.context.memory.decay_models import FreshnessState

if TYPE_CHECKING:
    from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()

# Weights for geometric mean combination
_DECAY_WEIGHT = 0.4
_CONFIDENCE_WEIGHT = 0.6

# Maps freshness state to a 0-1 decay score
_FRESHNESS_SCORES: dict[FreshnessState, float] = {
    FreshnessState.FRESH: 1.0,
    FreshnessState.AGING: 0.7,
    FreshnessState.STALE: 0.3,
    FreshnessState.EXPIRED: 0.05,
}


class MemoryQualityService:
    """Unified memory quality assessment combining time-decay and confidence scoring.

    Produces a single normalized 0.0-1.0 quality score per memory record
    using a weighted geometric mean of freshness-based decay and
    half-life confidence decay.
    """

    def __init__(
        self,
        *,
        decay_service: MemoryDecayService | None = None,
        confidence_service: ConfidenceDecayService | None = None,
        decay_weight: float = _DECAY_WEIGHT,
        confidence_weight: float = _CONFIDENCE_WEIGHT,
    ) -> None:
        self._decay = decay_service or MemoryDecayService()
        self._confidence = confidence_service or ConfidenceDecayService()
        self._decay_weight = decay_weight
        self._confidence_weight = confidence_weight

    def quality_score(
        self,
        memory_record: dict[str, Any],
        *,
        now: float | None = None,
    ) -> float:
        """Compute a unified 0.0-1.0 quality score for a memory record.

        Combines freshness-based decay and confidence decay via a weighted
        geometric mean: score = decay^w_d * confidence^w_c where weights
        are normalised so w_d + w_c = 1.
        """
        now = now or time.time()

        record = _to_record_proxy(memory_record)

        # Freshness component
        assessment = self._decay.evaluate_freshness(record, now=now)
        decay_score = _FRESHNESS_SCORES.get(assessment.freshness_state, 0.05)

        # Confidence component (already 0-1)
        confidence_score = self._confidence.compute_confidence(record, now=now)
        confidence_score = max(min(confidence_score, 1.0), 0.0)

        combined = _weighted_geometric_mean(
            decay_score,
            confidence_score,
            self._decay_weight,
            self._confidence_weight,
        )

        log.debug(
            "memory_quality_scored",
            memory_id=memory_record.get("memory_id"),
            decay_score=round(decay_score, 4),
            confidence_score=round(confidence_score, 4),
            quality=round(combined, 4),
        )
        return round(combined, 4)

    def batch_quality_scores(
        self,
        records: list[dict[str, Any]],
        *,
        now: float | None = None,
    ) -> list[float]:
        """Compute quality scores for a batch of memory records."""
        now = now or time.time()
        return [self.quality_score(r, now=now) for r in records]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _weighted_geometric_mean(
    a: float,
    b: float,
    w_a: float,
    w_b: float,
) -> float:
    """Weighted geometric mean with safe handling for zero values."""
    total_weight = w_a + w_b
    if total_weight <= 0:
        return 0.0
    na = w_a / total_weight
    nb = w_b / total_weight

    # Clamp to small positive to avoid log(0)
    a = max(a, 1e-10)
    b = max(b, 1e-10)

    return math.exp(na * math.log(a) + nb * math.log(b))


class _RecordProxy:
    """Lightweight proxy that exposes dict data with attribute access.

    Both MemoryDecayService and ConfidenceDecayService expect a MemoryRecord-like
    object. This proxy adapts a plain dict so the quality service can operate on
    raw dict payloads without requiring a full MemoryRecord import at runtime.
    """

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, Any]) -> None:
        self._data: dict[str, Any] = data

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError:
            return None


def _to_record_proxy(record: dict[str, Any]) -> MemoryRecord:
    """Wrap a dict as a record proxy compatible with decay/confidence services."""
    return cast(MemoryRecord, _RecordProxy(record))


__all__ = ["MemoryQualityService"]
