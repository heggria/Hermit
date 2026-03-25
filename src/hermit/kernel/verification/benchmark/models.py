from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "BenchmarkProfile",
    "BenchmarkResultClass",
    "BenchmarkRun",
    "BenchmarkVerdict",
    "RequirementLevel",
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


# Sentinel used to detect when benchmark_result_class was not explicitly provided.
_UNSET_RESULT_CLASS: object = object()


@dataclass
class BenchmarkVerdict:
    """A fully resolved verdict produced after a benchmark run.

    ``benchmark_result_class`` is automatically derived from ``overall_passed``
    when left unspecified.  Pass it explicitly only when you need to override
    that logic (e.g. to mark a partial verdict).
    """

    verdict_id: str
    run_id: str
    profile_id: str
    task_id: str
    overall_passed: bool
    # Use the sentinel default so __post_init__ can detect "not set".
    benchmark_result_class: BenchmarkResultClass | object = field(
        default_factory=lambda: _UNSET_RESULT_CLASS
    )
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    notes: str = ""
    consumed: bool = False
    consumed_by: str | None = None

    def __post_init__(self) -> None:
        # Derive benchmark_result_class from overall_passed when not explicitly set.
        if self.benchmark_result_class is _UNSET_RESULT_CLASS:
            self.benchmark_result_class = (
                BenchmarkResultClass.satisfied
                if self.overall_passed
                else BenchmarkResultClass.violated
            )
        elif not isinstance(self.benchmark_result_class, BenchmarkResultClass):
            raise TypeError(
                f"benchmark_result_class must be a BenchmarkResultClass member, "
                f"got {type(self.benchmark_result_class)!r}"
            )


class RequirementLevel(StrEnum):
    """Valid sentinel values for VerificationRequirements fields.

    Using a StrEnum instead of bare string literals prevents silent typo bugs
    (e.g. ``"requried"`` would previously pass unnoticed).
    """

    required = "required"
    optional = "optional"
    forbidden = "forbidden"


@dataclass
class VerificationRequirements:
    functional: RequirementLevel = RequirementLevel.required
    governance_bench: RequirementLevel = RequirementLevel.forbidden
    performance_bench: RequirementLevel = RequirementLevel.forbidden
    rollback_check: RequirementLevel = RequirementLevel.optional
    reconciliation_mode: str = "standard"
    benchmark_profile: str | None = None
    thresholds_ref: str | None = None
