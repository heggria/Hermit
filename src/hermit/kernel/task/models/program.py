"""Program/Initiative model — groups Teams/Tasks under a high-level goal.

Spec hierarchy: 人 → Hermit Instance → Program → Team/Graph → Roles → Worker Pool → Task → Step → StepAttempt

Program lifecycle: active ↔ archived
The contract layer is referenced via ``program_contract_ref`` (analogous to
``task_contract_ref`` on TaskRecord).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

__all__ = [
    "ACTIVE_PROGRAM_STATES",
    "PROGRAM_STATE_TRANSITIONS",
    "TERMINAL_PROGRAM_STATES",
    "ProgramRecord",
    "ProgramState",
    "ProgramStatusProjection",
]


class ProgramState(StrEnum):
    """All valid program states.

    Lifecycle: active ↔ archived
    """

    active = "active"
    archived = "archived"


TERMINAL_PROGRAM_STATES: frozenset[ProgramState] = frozenset(
    {
        ProgramState.archived,
    }
)

ACTIVE_PROGRAM_STATES: frozenset[ProgramState] = frozenset(
    {
        ProgramState.active,
    }
)

# Valid state transitions — used for validation in update_program_status.
PROGRAM_STATE_TRANSITIONS: dict[ProgramState, frozenset[ProgramState]] = {
    ProgramState.active: frozenset({ProgramState.archived}),
    ProgramState.archived: frozenset({ProgramState.active}),
}


@dataclass
class ProgramRecord:
    """A high-level goal container that owns Teams/Milestone Graphs.

    The ``program_contract_ref`` field references the governing contract artifact
    (analogous to ``task_contract_ref`` on TaskRecord), establishing the contract
    layer between the control plane and execution plane as required by spec.
    """

    program_id: str
    title: str
    goal: str
    status: str = ProgramState.active
    description: str = ""
    priority: str = "normal"
    program_contract_ref: str | None = None
    budget_limits: dict[str, Any] = field(default_factory=dict)
    milestone_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=lambda: datetime.now(UTC).timestamp())
    updated_at: float = field(default_factory=lambda: datetime.now(UTC).timestamp())


@dataclass
class ProgramStatusProjection:
    program_id: str
    title: str
    overall_state: str
    progress_pct: float = 0.0
    current_phase: str = ""
    active_teams: int = 0
    queued_tasks: int = 0
    running_attempts: int = 0
    blocked_items: int = 0
    awaiting_human: bool = False
    latest_summary: str = ""
    latest_risks: list[str] = field(default_factory=list)
    latest_benchmark_status: str = ""
    last_updated_at: float = 0.0
