"""Data models for the meta-loop self-iteration plugin."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IterationPhase(StrEnum):
    """Phases of a self-improvement iteration lifecycle."""

    PENDING = "pending"
    RESEARCHING = "researching"
    GENERATING_SPEC = "generating_spec"
    SPEC_APPROVAL = "spec_approval"
    DECOMPOSING = "decomposing"
    IMPLEMENTING = "implementing"
    REVIEWING = "reviewing"
    BENCHMARKING = "benchmarking"
    LEARNING = "learning"
    COMPLETED = "completed"
    FAILED = "failed"


# Ordered phase transitions for the state machine.
# Terminal phases (COMPLETED, FAILED) are not included.
PHASE_ORDER: tuple[IterationPhase, ...] = (
    IterationPhase.PENDING,
    IterationPhase.RESEARCHING,
    IterationPhase.GENERATING_SPEC,
    IterationPhase.SPEC_APPROVAL,
    IterationPhase.DECOMPOSING,
    IterationPhase.IMPLEMENTING,
    IterationPhase.REVIEWING,
    IterationPhase.BENCHMARKING,
    IterationPhase.LEARNING,
    IterationPhase.COMPLETED,
)

TERMINAL_PHASES = frozenset({IterationPhase.COMPLETED, IterationPhase.FAILED})


@dataclass(frozen=True)
class IterationState:
    """Immutable snapshot of an iteration's current state."""

    spec_id: str
    phase: IterationPhase
    attempt: int = 1
    dag_task_id: str | None = None
    error: str | None = None

    def next_phase(self) -> IterationPhase | None:
        """Return the next phase in the lifecycle, or None if terminal."""
        if self.phase in TERMINAL_PHASES:
            return None
        try:
            idx = PHASE_ORDER.index(self.phase)
        except ValueError:
            return None
        next_idx = idx + 1
        if next_idx >= len(PHASE_ORDER):
            return None
        return PHASE_ORDER[next_idx]

    @property
    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES
