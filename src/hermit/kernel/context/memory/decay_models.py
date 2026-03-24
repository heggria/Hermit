from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FreshnessState(StrEnum):
    """Four-state freshness model for memory decay governance."""

    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    EXPIRED = "expired"


@dataclass(frozen=True)
class FreshnessAssessment:
    """Result of evaluating a single memory's freshness."""

    memory_id: str
    freshness_state: FreshnessState
    age_days: float
    ttl_days: float
    pct_remaining: float
    last_accessed_days_ago: float | None


@dataclass(frozen=True)
class DecaySweepTransition:
    """A single freshness state transition observed during a sweep."""

    memory_id: str
    # Use FreshnessState instead of raw str so callers cannot pass arbitrary
    # strings and type-checkers can validate every transition.
    previous_state: FreshnessState | None
    new_state: FreshnessState


@dataclass
class DecaySweepReport:
    """Summary of a decay sweep across the memory store."""

    sweep_id: str
    swept_at: float
    total_evaluated: int = 0
    # Use `list` directly as the factory — `lambda: list[T]()` is redundant;
    # `list[T]` is not a typed constructor and the lambda adds no value.
    transitions: list[DecaySweepTransition] = field(default_factory=list)
    quarantine_candidates: list[str] = field(default_factory=list)


__all__ = [
    "DecaySweepReport",
    "DecaySweepTransition",
    "FreshnessAssessment",
    "FreshnessState",
]
