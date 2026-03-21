from __future__ import annotations

from hermit.kernel.verification.benchmark.models import BenchmarkProfile, TaskFamily

__all__ = [
    "BenchmarkForbiddenError",
    "BenchmarkProfileRegistry",
]

# Risk bands where benchmark is forbidden — routing must reject, not silently skip.
_RISK_BANDS_FORBIDDEN = ("low",)

# Risk bands that trigger benchmark routing.
_RISK_BANDS_REQUIRING_BENCH = ("medium", "high", "critical")


class BenchmarkForbiddenError(Exception):
    """Raised when benchmark is requested for a forbidden risk band."""


def _default_profiles() -> list[BenchmarkProfile]:
    """Return the four built-in benchmark profiles.

    The ``trustloop_governance`` profile covers the 7 core TrustLoop-Bench
    governance metrics defined in the spec:

    1. Contract Satisfaction Rate
    2. Unauthorized Effect Rate
    3. Stale Authorization Execution Rate
    4. Belief Calibration Under Contradiction
    5. Rollback Success Rate
    6. Mean Recovery Depth
    7. Operator Burden Per Successful Task
    """
    return [
        BenchmarkProfile(
            profile_id="trustloop_governance",
            name="Trust-Loop Governance",
            task_family=TaskFamily.governance_mutation,
            description=(
                "Validates the 7 core TrustLoop-Bench governance metrics "
                "after governance-mutation tasks."
            ),
            runner_command="make test-kernel",
            metrics=[
                "contract_satisfaction_rate",
                "unauthorized_effect_rate",
                "stale_authorization_execution_rate",
                "belief_calibration_under_contradiction",
                "rollback_success_rate",
                "mean_recovery_depth",
                "operator_burden_per_successful_task",
            ],
            thresholds={
                "contract_satisfaction_rate": 0.95,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 0.95,
                "mean_recovery_depth": 3.0,
                "operator_burden_per_successful_task": 5.0,
                "belief_calibration_under_contradiction": 0.8,
            },
        ),
        BenchmarkProfile(
            profile_id="runtime_perf",
            name="Runtime Performance",
            task_family=TaskFamily.runtime_perf,
            description="Measures executor throughput and latency for runtime-critical paths.",
            runner_command="make test-bench",
            metrics=["p50_latency_ms", "p99_latency_ms", "throughput_ops"],
            thresholds={
                "p50_latency_ms": 50.0,
                "p99_latency_ms": 200.0,
                "throughput_ops": 100.0,
            },
        ),
        BenchmarkProfile(
            profile_id="integration_regression",
            name="Integration Regression",
            task_family=TaskFamily.surface_integration,
            description="Runs surface-integration regression suite for adapters and plugins.",
            runner_command="make test",
            metrics=["pass_rate", "regression_count"],
            thresholds={
                "pass_rate": 1.0,
                "regression_count": 0.0,
            },
        ),
        BenchmarkProfile(
            profile_id="template_quality",
            name="Template Quality",
            task_family=TaskFamily.learning_template,
            description=(
                "Evaluates quality of learned templates via coverage and accuracy metrics."
            ),
            runner_command="make test-kernel",
            metrics=["template_accuracy", "coverage_delta"],
            thresholds={
                "template_accuracy": 0.85,
                "coverage_delta": 0.0,
            },
        ),
    ]


class BenchmarkProfileRegistry:
    """Routes tasks to appropriate benchmark profiles based on task family classification."""

    def __init__(self) -> None:
        self._profiles: dict[str, BenchmarkProfile] = {}
        for profile in _default_profiles():
            self._profiles[profile.profile_id] = profile

    def register_profile(self, profile: BenchmarkProfile) -> None:
        """Register a benchmark profile, replacing any existing profile with the same id."""
        self._profiles[profile.profile_id] = profile

    def get_profile(self, profile_id: str) -> BenchmarkProfile | None:
        """Return a profile by id, or None if not found."""
        return self._profiles.get(profile_id)

    def route_task(
        self,
        task_family: TaskFamily,
        risk_band: str = "low",
    ) -> BenchmarkProfile | None:
        """Select a benchmark profile for a task based on its family and risk band.

        Raises :class:`BenchmarkForbiddenError` when the risk band is ``low``
        (spec: benchmark for low risk is forbidden, not silently skipped).
        Returns ``None`` only when no matching profile exists for the family.
        """
        if risk_band in _RISK_BANDS_FORBIDDEN:
            raise BenchmarkForbiddenError(
                f"Benchmark is forbidden for risk_band='{risk_band}'. "
                "Low-risk tasks must not trigger benchmarking."
            )
        if risk_band not in _RISK_BANDS_REQUIRING_BENCH:
            return None
        for profile in self._profiles.values():
            if profile.task_family == task_family:
                return profile
        return None

    def list_profiles(self) -> list[BenchmarkProfile]:
        """Return all registered profiles in insertion order."""
        return list(self._profiles.values())
