"""Frozen dataclasses for benchmark results and iteration lessons."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BenchmarkErrorDetail:
    """Structured error detail from a single benchmark check category."""

    category: str  # "typecheck" | "test_failure" | "lint" | "other"
    count: int  # number of errors in this category
    summary: str  # brief summary (first 500 chars of relevant output)
    file_paths: tuple[str, ...] = ()  # affected files


@dataclass(frozen=True)
class BenchmarkResult:
    """Immutable snapshot of a single benchmark run."""

    iteration_id: str
    spec_id: str
    check_passed: bool
    test_total: int = 0
    test_passed: int = 0
    coverage: float = 0.0
    lint_violations: int = 0
    typecheck_errors: int = 0
    duration_seconds: float = 0.0
    regression_detected: bool = False
    compared_to_baseline: dict[str, Any] = field(default_factory=dict)
    statistical_analysis: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error_details: tuple[BenchmarkErrorDetail, ...] = ()
    raw_output: str = ""


@dataclass(frozen=True)
class LessonLearned:
    """A structured lesson extracted from an iteration outcome."""

    lesson_id: str
    iteration_id: str
    category: str  # "mistake" | "success_pattern" | "rollback_pattern" | "optimization"
    summary: str
    trigger_condition: str = ""
    resolution: str = ""
    applicable_files: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
