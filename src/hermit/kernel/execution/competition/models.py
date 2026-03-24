from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# -- State machine transitions ------------------------------------------------

COMPETITION_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"spawning", "cancelled"},
    "spawning": {"running", "cancelled"},
    "running": {"evaluating", "cancelled"},
    "evaluating": {"decided", "cancelled"},
    "decided": set(),
    "cancelled": set(),
}

CANDIDATE_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "disqualified"},
    "running": {"completed", "failed", "disqualified"},
    "completed": {"disqualified"},
    "failed": set(),
    "disqualified": set(),
}


def validate_transition(
    current: str,
    target: str,
    transitions: dict[str, set[str]],
    *,
    label: str = "state",
) -> None:
    allowed = transitions.get(current)
    if allowed is None:
        raise ValueError(f"Unknown {label} state: {current!r}")
    if target not in allowed:
        raise ValueError(
            f"Invalid {label} transition: {current!r} -> {target!r} "
            f"(allowed: {sorted(allowed) or 'none'})"
        )


# -- Records ------------------------------------------------------------------


@dataclass
class CompetitionRecord:
    competition_id: str
    parent_task_id: str
    goal: str
    strategy: str
    candidate_count: int
    min_candidates: int
    evaluation_criteria: dict[str, Any] = field(default_factory=dict)
    scoring_weights: dict[str, float] = field(default_factory=dict)
    status: str = "draft"
    timeout_policy: str = "evaluate_completed"
    winner_task_id: str | None = None
    winner_score: float | None = None
    decision_ref: str | None = None
    evaluation_artifact_ref: str | None = None
    timeout_seconds: float | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if self.status not in COMPETITION_TRANSITIONS:
            raise ValueError(
                f"Invalid initial competition status: {self.status!r} "
                f"(valid: {sorted(COMPETITION_TRANSITIONS)})"
            )


@dataclass
class CompetitionCandidateRecord:
    candidate_id: str
    competition_id: str
    task_id: str
    label: str
    workspace_ref: str | None = None
    status: str = "pending"
    score: float | None = None
    score_breakdown: dict[str, float] = field(default_factory=dict)
    evaluation_receipt_ref: str | None = None
    promoted: bool = False
    discard_reason: str | None = None
    created_at: float = 0.0
    finished_at: float | None = None

    def __post_init__(self) -> None:
        if self.status not in CANDIDATE_TRANSITIONS:
            raise ValueError(
                f"Invalid initial candidate status: {self.status!r} "
                f"(valid: {sorted(CANDIDATE_TRANSITIONS)})"
            )


@dataclass
class CandidateScore:
    candidate_id: str
    task_id: str
    total: float
    breakdown: dict[str, float] = field(default_factory=dict)
    passed: bool = False
