from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "BenchmarkProfile",
    "BenchmarkResultClass",
    "BenchmarkRun",
    "BenchmarkVerdict",
    "TaskFamily",
    "VerificationRequirements",
]


class TaskFamily(StrEnum):
    governance_mutation = "governance_mutation"
    runtime_perf = "runtime_perf"
    surface_integration = "surface_integration"
    learning_template = "learning_template"


@dataclass
class BenchmarkProfile:
    profile_id: str
    name: str
    task_family: TaskFamily
    description: str
    runner_command: str
    metrics: list[str] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)
    baseline_ref: str | None = None


class BenchmarkResultClass(StrEnum):
    """Verdict classification for reconciliation integration."""

    satisfied = "satisfied"
    violated = "violated"


@dataclass
class BenchmarkRun:
    run_id: str
    profile_id: str
    task_id: str
    step_id: str
    attempt_id: str
    baseline_ref: str | None
    raw_metrics: dict[str, float] = field(default_factory=dict)
    threshold_results: dict[str, bool] = field(default_factory=dict)
    passed: bool = False
    started_at: float = 0.0
    completed_at: float | None = None
    environment_tag: str | None = None
    commit_ref: str | None = None


@dataclass
class BenchmarkVerdict:
    verdict_id: str
    run_id: str
    profile_id: str
    task_id: str
    overall_passed: bool
    benchmark_result_class: BenchmarkResultClass = BenchmarkResultClass.violated
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    notes: str = ""
    consumed: bool = False
    consumed_by: str | None = None


@dataclass
class VerificationRequirements:
    functional: str = "required"
    governance_bench: str = "forbidden"
    performance_bench: str = "forbidden"
    rollback_check: str = "optional"
    reconciliation_mode: str = "standard"
    benchmark_profile: str | None = None
    thresholds_ref: str | None = None
