"""Immutable data models for spec generation and task decomposition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GeneratedSpec:
    """A structured specification produced from a goal and optional research."""

    spec_id: str
    title: str
    goal: str
    constraints: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    file_plan: tuple[dict[str, str], ...] = ()  # [{path, action, reason}]
    research_ref: str = ""
    trust_zone: str = "normal"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecompositionPlan:
    """A DAG of steps derived from a GeneratedSpec."""

    spec_id: str
    steps: tuple[dict[str, Any], ...]  # StepNode-compatible dicts
    dependency_graph: dict[str, list[str]] = field(default_factory=dict)
    estimated_duration_minutes: int = 0
