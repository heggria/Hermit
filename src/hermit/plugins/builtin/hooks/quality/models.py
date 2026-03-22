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


@dataclass(frozen=True)
class ReviewPerspective:
    """Defines a single reviewer's focus area and evaluation criteria."""

    role: str  # "security" | "logic" | "architecture" | "test" | "regression"
    system_prompt_template: str
    severity_weight: float  # How much this perspective's findings weigh
    required: bool  # Must produce findings for verdict to be valid
    timeout_seconds: float  # Per-reviewer LLM call timeout
    model: str = ""  # Override model for this perspective (empty = use default)


@dataclass(frozen=True)
class ReviewerFinding:
    """A single finding from one council reviewer."""

    reviewer_role: str
    category: str
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    file_path: str
    line_start: int = 0
    line_end: int = 0
    message: str = ""
    suggested_fix: str = ""
    confidence: float = 0.0
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CouncilVerdict:
    """The synthesized output of the full review council."""

    verdict: str  # "accept" | "revise" | "reject"
    council_id: str
    reviewer_count: int = 0
    finding_count: int = 0
    critical_count: int = 0
    high_count: int = 0
    findings: tuple[ReviewerFinding, ...] = ()
    lint_passed: bool = True
    consensus_score: float = 0.0
    revision_directive: str = ""
    duration_seconds: float = 0.0
    decided_at: float = 0.0


@dataclass(frozen=True)
class RevisionDirective:
    """Structured feedback from council to implementer for revision cycles."""

    spec_id: str
    council_id: str
    revision_cycle: int = 0
    findings_to_fix: tuple[ReviewerFinding, ...] = ()
    priority_order: tuple[str, ...] = ()  # File paths in fix order
    max_revision_cycles: int = 3
    narrative: str = ""  # Human-readable summary for LLM implementer
