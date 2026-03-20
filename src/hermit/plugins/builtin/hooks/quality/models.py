"""Immutable data models for code review findings and test plans."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FindingSeverity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class ReviewFinding:
    """A single review finding from the governed reviewer."""

    severity: FindingSeverity
    category: str  # "lint" | "import" | "naming" | "test" | "todo" | "init"
    message: str
    file_path: str = ""
    line: int = 0


@dataclass(frozen=True)
class ReviewReport:
    """Aggregated report from a governed review run."""

    findings: tuple[ReviewFinding, ...] = ()
    passed: bool = True
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class TestPlan:
    """A test skeleton plan for a source file."""

    test_file: str
    source_file: str
    functions: tuple[str, ...] = ()
    skeleton: str = ""
