from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EpisodeIndex:
    """Index entry linking a task episode to its memories and artifacts."""

    episode_id: str
    task_id: str
    memory_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    tool_names: tuple[str, ...]
    created_at: float


@dataclass(frozen=True)
class EpisodicResult:
    """A memory record found via episodic query, with context."""

    memory_id: str
    task_id: str
    claim_text: str
    match_reason: str
    episode_id: str | None = None


@dataclass
class EpisodeKnowledge:
    """Knowledge extracted from a task episode."""

    task_id: str
    summary: str = ""
    tool_names: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    outcome: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "EpisodeIndex",
    "EpisodeKnowledge",
    "EpisodicResult",
]
