from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "ACTIVE_MILESTONE_STATES",
    "ACTIVE_TEAM_STATES",
    "MILESTONE_STATE_TRANSITIONS",
    "TEAM_STATE_TRANSITIONS",
    "TERMINAL_MILESTONE_STATES",
    "TERMINAL_TEAM_STATES",
    "MilestoneRecord",
    "MilestoneState",
    "RoleSlotSpec",
    "TeamRecord",
    "TeamState",
    "TeamStatusProjection",
]


class TeamState(StrEnum):
    """All valid team states.

    Spec ref: constructor.md — ``overall_state: running | blocked | paused | completed | failed``
    and worker.md — ``cancelled``.
    """

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    ARCHIVED = "archived"
    DISBANDED = "disbanded"


class MilestoneState(StrEnum):
    """All valid milestone states.

    Spec ref: team.md — milestone lifecycle includes ``verification_failed``,
    ``superseded``; worker.md — ``cancelled``.
    """

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


TERMINAL_TEAM_STATES: frozenset[TeamState] = frozenset(
    {
        TeamState.COMPLETED,
        TeamState.FAILED,
        TeamState.ARCHIVED,
        TeamState.DISBANDED,
    }
)

ACTIVE_TEAM_STATES: frozenset[TeamState] = frozenset(
    {
        TeamState.ACTIVE,
        TeamState.PAUSED,
        TeamState.BLOCKED,
    }
)

TERMINAL_MILESTONE_STATES: frozenset[MilestoneState] = frozenset(
    {
        MilestoneState.COMPLETED,
        MilestoneState.FAILED,
        MilestoneState.SKIPPED,
    }
)

ACTIVE_MILESTONE_STATES: frozenset[MilestoneState] = frozenset(
    {
        MilestoneState.PENDING,
        MilestoneState.ACTIVE,
        MilestoneState.BLOCKED,
    }
)

# Valid state transitions — used for validation in update_team_status / update_milestone_status.
TEAM_STATE_TRANSITIONS: dict[TeamState, frozenset[TeamState]] = {
    TeamState.ACTIVE: frozenset(
        {
            TeamState.PAUSED,
            TeamState.BLOCKED,
            TeamState.COMPLETED,
            TeamState.FAILED,
            TeamState.ARCHIVED,
            TeamState.DISBANDED,
        }
    ),
    TeamState.PAUSED: frozenset({TeamState.ACTIVE, TeamState.ARCHIVED, TeamState.DISBANDED}),
    TeamState.BLOCKED: frozenset({TeamState.ACTIVE, TeamState.FAILED, TeamState.ARCHIVED}),
    TeamState.COMPLETED: frozenset({TeamState.ARCHIVED}),
    TeamState.FAILED: frozenset({TeamState.ARCHIVED}),
    TeamState.ARCHIVED: frozenset({TeamState.ACTIVE}),
    TeamState.DISBANDED: frozenset(),
}

MILESTONE_STATE_TRANSITIONS: dict[MilestoneState, frozenset[MilestoneState]] = {
    MilestoneState.PENDING: frozenset({MilestoneState.ACTIVE, MilestoneState.SKIPPED}),
    MilestoneState.ACTIVE: frozenset(
        {MilestoneState.COMPLETED, MilestoneState.BLOCKED, MilestoneState.FAILED}
    ),
    MilestoneState.BLOCKED: frozenset(
        {MilestoneState.ACTIVE, MilestoneState.FAILED, MilestoneState.SKIPPED}
    ),
    MilestoneState.COMPLETED: frozenset(),
    MilestoneState.FAILED: frozenset(),
    MilestoneState.SKIPPED: frozenset(),
}


@dataclass
class RoleSlotSpec:
    """Per-role slot specification inside a team's role_assembly.

    Spec ref: constructor.md — ``local role assembly`` maps WorkerRole to
    count/config; worker.md — ``Prompt -> Program -> Milestone Graph ->
    Role Assembly -> Attempts``.
    """

    role: str
    count: int = 1
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class TeamRecord:
    team_id: str
    program_id: str
    title: str
    workspace_id: str
    status: str
    role_assembly: dict[str, RoleSlotSpec] = field(default_factory=dict)
    context_boundary: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MilestoneRecord:
    milestone_id: str
    team_id: str
    title: str
    description: str
    status: str
    dependency_ids: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    created_at: float = 0.0
    completed_at: float | None = None


@dataclass
class TeamStatusProjection:
    team_id: str
    title: str
    state: str
    workspace: str
    active_workers: int = 0
    milestone_progress: str = "0/0"
    blockers: list[str] = field(default_factory=list)
