"""Data models for the meta-loop v2 pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PipelinePhase(StrEnum):
    """Phases of the 3-phase LLM-native iteration pipeline."""

    PENDING = "pending"
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    REVIEWING = "reviewing"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"


PHASE_ORDER: tuple[PipelinePhase, ...] = (
    PipelinePhase.PENDING,
    PipelinePhase.PLANNING,
    PipelinePhase.IMPLEMENTING,
    PipelinePhase.REVIEWING,
    PipelinePhase.ACCEPTED,
)

TERMINAL_PHASES = frozenset(
    {
        PipelinePhase.ACCEPTED,
        PipelinePhase.REJECTED,
        PipelinePhase.FAILED,
    }
)

# Explicit transition map — supports the REVIEWING → IMPLEMENTING revision loop
ALLOWED_TRANSITIONS: dict[PipelinePhase, frozenset[PipelinePhase]] = {
    PipelinePhase.PENDING: frozenset({PipelinePhase.PLANNING, PipelinePhase.FAILED}),
    PipelinePhase.PLANNING: frozenset(
        {
            PipelinePhase.IMPLEMENTING,
            PipelinePhase.REJECTED,  # approval denied
            PipelinePhase.FAILED,
        }
    ),
    PipelinePhase.IMPLEMENTING: frozenset(
        {
            PipelinePhase.REVIEWING,
            PipelinePhase.IMPLEMENTING,  # self-transition for dag_task_id write
            PipelinePhase.FAILED,
        }
    ),
    PipelinePhase.REVIEWING: frozenset(
        {
            PipelinePhase.IMPLEMENTING,  # REVISION LOOP
            PipelinePhase.ACCEPTED,
            PipelinePhase.REJECTED,
            PipelinePhase.FAILED,
        }
    ),
}

# 3 cycles = initial implementation + 2 revision attempts.
MAX_REVISION_CYCLES = 3
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class IterationState:
    """Immutable snapshot of an iteration's current state."""

    spec_id: str
    phase: PipelinePhase
    attempt: int = 1
    revision_cycle: int = 0
    dag_task_id: str | None = None
    plan_artifact_ref: str | None = None
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES

    @property
    def can_revise(self) -> bool:
        return self.revision_cycle < MAX_REVISION_CYCLES
