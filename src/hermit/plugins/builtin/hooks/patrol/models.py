"""Patrol data models — check results and reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PatrolCheckResult:
    """Result of a single patrol check."""

    check_name: str  # "lint" | "test" | "coverage" | "todo_scan" | "security"
    status: str  # "clean" | "issues_found" | "error"
    summary: str
    issue_count: int = 0
    issues: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    disposition: str = "report_only"  # "auto_fix" | "propose" | "report_only"


@dataclass
class PatrolReport:
    """Aggregated report from a patrol run."""

    checks: list[PatrolCheckResult] = field(default_factory=list[PatrolCheckResult])
    total_issues: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    workspace_root: str = ""
