"""Data models for competition-based execution."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _comp_id() -> str:
    return f"comp_{uuid.uuid4().hex[:12]}"


def _cand_id() -> str:
    return f"cand_{uuid.uuid4().hex[:12]}"


@dataclass
class CompetitionRecord:
    """A competition groups candidate tasks that race to solve a goal."""

    competition_id: str = field(default_factory=_comp_id)
    parent_task_id: str = ""
    conversation_id: str = ""
    goal: str = ""
    candidate_count: int = 2
    status: str = "draft"
    winner_task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None


@dataclass
class CandidateRecord:
    """A single candidate in a competition."""

    candidate_id: str = field(default_factory=_cand_id)
    competition_id: str = ""
    task_id: str = ""
    worktree_path: str | None = None
    status: str = "pending"
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
    created_at: float = field(default_factory=time.time)
