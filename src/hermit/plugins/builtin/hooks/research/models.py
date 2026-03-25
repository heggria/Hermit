"""Immutable data models for research findings and reports."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResearchFinding:
    """A single research finding from any strategy."""

    source: str  # "codebase" | "web" | "doc" | "git_history"
    title: str
    content: str
    relevance: float  # 0.0-1.0
    url: str = ""
    file_path: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchReport:
    """Aggregated report from a research pipeline run."""

    goal: str
    findings: tuple[ResearchFinding, ...] = ()
    suggested_approach: str = ""
    knowledge_gaps: tuple[str, ...] = ()
    query_count: int = 0
    duration_seconds: float = 0.0
