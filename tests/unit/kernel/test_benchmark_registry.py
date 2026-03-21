"""Tests for BenchmarkProfileRegistry."""

from __future__ import annotations

import pytest

from hermit.kernel.verification.benchmark.models import (
    BenchmarkProfile,
    BenchmarkResultClass,
    BenchmarkRun,
    BenchmarkVerdict,
    TaskFamily,
    VerificationRequirements,
)
from hermit.kernel.verification.benchmark.registry import (
    BenchmarkForbiddenError,
    BenchmarkProfileRegistry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> BenchmarkProfileRegistry:
    return BenchmarkProfileRegistry()


# ---------------------------------------------------------------------------
# Default profiles
# ---------------------------------------------------------------------------


class TestDefaultProfiles:
    def test_default_profiles_loaded(self, registry: BenchmarkProfileRegistry) -> None:
        profiles = registry.list_profiles()
        assert len(profiles) == 4

    def test_default_profile_ids(self, registry: BenchmarkProfileRegistry) -> None:
        ids = {p.profile_id for p in registry.list_profiles()}
        assert ids == {
            "trustloop_governance",
            "runtime_perf",
            "integration_regression",
            "template_quality",
        }

    def test_each_default_has_thresholds(self, registry: BenchmarkProfileRegistry) -> None:
        for profile in registry.list_profiles():
            assert len(profile.thresholds) > 0, f"{profile.profile_id} has no thresholds"

    def test_each_default_has_metrics(self, registry: BenchmarkProfileRegistry) -> None:
        for profile in registry.list_profiles():
            assert len(profile.metrics) > 0, f"{profile.profile_id} has no metrics"


# ---------------------------------------------------------------------------
# get_profile
# ---------------------------------------------------------------------------


class TestGetProfile:
    def test_get_existing_profile(self, registry: BenchmarkProfileRegistry) -> None:
        profile = registry.get_profile("trustloop_governance")
        assert profile is not None
        assert profile.task_family == TaskFamily.governance_mutation

    def test_get_missing_profile_returns_none(self, registry: BenchmarkProfileRegistry) -> None:
        assert registry.get_profile("nonexistent") is None


# ---------------------------------------------------------------------------
# register_profile
# ---------------------------------------------------------------------------


class TestRegisterProfile:
    def test_register_new_profile(self, registry: BenchmarkProfileRegistry) -> None:
        custom = BenchmarkProfile(
            profile_id="custom_bench",
            name="Custom Benchmark",
            task_family=TaskFamily.runtime_perf,
            description="A custom benchmark profile for testing.",
            runner_command="make custom-bench",
            metrics=["custom_metric"],
            thresholds={"custom_metric": 42.0},
        )
        registry.register_profile(custom)
        assert registry.get_profile("custom_bench") is custom
        assert len(registry.list_profiles()) == 5

    def test_register_replaces_existing(self, registry: BenchmarkProfileRegistry) -> None:
        replacement = BenchmarkProfile(
            profile_id="runtime_perf",
            name="Replaced Runtime Perf",
            task_family=TaskFamily.runtime_perf,
            description="Replaced profile.",
            runner_command="make replaced-bench",
        )
        registry.register_profile(replacement)
        fetched = registry.get_profile("runtime_perf")
        assert fetched is not None
        assert fetched.name == "Replaced Runtime Perf"
        # Total count should not change.
        assert len(registry.list_profiles()) == 4


# ---------------------------------------------------------------------------
# route_task
# ---------------------------------------------------------------------------


class TestRouteTask:
    def test_route_high_risk_governance(self, registry: BenchmarkProfileRegistry) -> None:
        profile = registry.route_task(TaskFamily.governance_mutation, risk_band="high")
        assert profile is not None
        assert profile.profile_id == "trustloop_governance"

    def test_route_critical_risk_runtime(self, registry: BenchmarkProfileRegistry) -> None:
        profile = registry.route_task(TaskFamily.runtime_perf, risk_band="critical")
        assert profile is not None
        assert profile.profile_id == "runtime_perf"

    def test_route_low_risk_raises_forbidden(self, registry: BenchmarkProfileRegistry) -> None:
        with pytest.raises(BenchmarkForbiddenError, match="forbidden"):
            registry.route_task(TaskFamily.governance_mutation, risk_band="low")

    def test_route_medium_risk_returns_profile(self, registry: BenchmarkProfileRegistry) -> None:
        profile = registry.route_task(TaskFamily.runtime_perf, risk_band="medium")
        assert profile is not None
        assert profile.profile_id == "runtime_perf"

    def test_route_surface_integration(self, registry: BenchmarkProfileRegistry) -> None:
        profile = registry.route_task(TaskFamily.surface_integration, risk_band="high")
        assert profile is not None
        assert profile.profile_id == "integration_regression"

    def test_route_learning_template(self, registry: BenchmarkProfileRegistry) -> None:
        profile = registry.route_task(TaskFamily.learning_template, risk_band="critical")
        assert profile is not None
        assert profile.profile_id == "template_quality"

    def test_route_default_risk_band_raises_forbidden(
        self, registry: BenchmarkProfileRegistry
    ) -> None:
        """Default risk_band is 'low', which is forbidden."""
        with pytest.raises(BenchmarkForbiddenError):
            registry.route_task(TaskFamily.governance_mutation)

    def test_route_unknown_family_after_removal(self, registry: BenchmarkProfileRegistry) -> None:
        """When no profile matches the family, route_task returns None."""
        # Remove the governance profile so no profile matches governance_mutation.
        registry._profiles.pop("trustloop_governance", None)
        assert registry.route_task(TaskFamily.governance_mutation, risk_band="high") is None


# ---------------------------------------------------------------------------
# Model dataclass tests
# ---------------------------------------------------------------------------


class TestModels:
    def test_task_family_values(self) -> None:
        assert TaskFamily.governance_mutation == "governance_mutation"
        assert TaskFamily.runtime_perf == "runtime_perf"
        assert TaskFamily.surface_integration == "surface_integration"
        assert TaskFamily.learning_template == "learning_template"

    def test_benchmark_profile_defaults(self) -> None:
        profile = BenchmarkProfile(
            profile_id="p1",
            name="P1",
            task_family=TaskFamily.runtime_perf,
            description="desc",
            runner_command="make test",
        )
        assert profile.metrics == []
        assert profile.thresholds == {}
        assert profile.baseline_ref is None

    def test_benchmark_run_defaults(self) -> None:
        run = BenchmarkRun(
            run_id="r1",
            profile_id="p1",
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
            baseline_ref=None,
        )
        assert run.raw_metrics == {}
        assert run.threshold_results == {}
        assert run.passed is False
        assert run.started_at == 0.0
        assert run.completed_at is None
        assert run.environment_tag is None
        assert run.commit_ref is None

    def test_benchmark_verdict_defaults(self) -> None:
        verdict = BenchmarkVerdict(
            verdict_id="v1",
            run_id="r1",
            profile_id="p1",
            task_id="t1",
            overall_passed=True,
        )
        assert verdict.regressions == []
        assert verdict.improvements == []
        assert verdict.notes == ""
        assert verdict.benchmark_result_class == BenchmarkResultClass.violated

    def test_benchmark_result_class_values(self) -> None:
        assert BenchmarkResultClass.satisfied == "satisfied"
        assert BenchmarkResultClass.violated == "violated"

    def test_verification_requirements_defaults(self) -> None:
        reqs = VerificationRequirements()
        assert reqs.functional == "required"
        assert reqs.governance_bench == "forbidden"
        assert reqs.performance_bench == "forbidden"
        assert reqs.rollback_check == "optional"
        assert reqs.reconciliation_mode == "standard"
        assert reqs.benchmark_profile is None
        assert reqs.thresholds_ref is None


# ---------------------------------------------------------------------------
# Spec compliance: trustloop_governance profile must have 7 governance metrics
# ---------------------------------------------------------------------------


class TestTrustloopGovernanceSpecCompliance:
    """Verify the trustloop_governance profile has the 7 core TrustLoop-Bench
    governance metrics defined in the spec."""

    _REQUIRED_METRICS = [
        "contract_satisfaction_rate",
        "unauthorized_effect_rate",
        "stale_authorization_execution_rate",
        "belief_calibration_under_contradiction",
        "rollback_success_rate",
        "mean_recovery_depth",
        "operator_burden_per_successful_task",
    ]

    def test_trustloop_has_exactly_7_metrics(self, registry: BenchmarkProfileRegistry) -> None:
        profile = registry.get_profile("trustloop_governance")
        assert profile is not None
        assert len(profile.metrics) == 7

    def test_trustloop_has_all_spec_metrics(self, registry: BenchmarkProfileRegistry) -> None:
        profile = registry.get_profile("trustloop_governance")
        assert profile is not None
        for metric in self._REQUIRED_METRICS:
            assert metric in profile.metrics, f"Missing spec metric: {metric}"

    def test_trustloop_has_thresholds_for_all_metrics(
        self, registry: BenchmarkProfileRegistry
    ) -> None:
        profile = registry.get_profile("trustloop_governance")
        assert profile is not None
        for metric in self._REQUIRED_METRICS:
            assert metric in profile.thresholds, f"Missing threshold for spec metric: {metric}"

    def test_trustloop_unauthorized_effect_threshold_is_zero(
        self, registry: BenchmarkProfileRegistry
    ) -> None:
        profile = registry.get_profile("trustloop_governance")
        assert profile is not None
        assert profile.thresholds["unauthorized_effect_rate"] == 0.0

    def test_trustloop_stale_authorization_threshold_is_zero(
        self, registry: BenchmarkProfileRegistry
    ) -> None:
        profile = registry.get_profile("trustloop_governance")
        assert profile is not None
        assert profile.thresholds["stale_authorization_execution_rate"] == 0.0


# ---------------------------------------------------------------------------
# Spec compliance: low risk is forbidden, not silently skipped
# ---------------------------------------------------------------------------


class TestLowRiskForbiddenSpec:
    """Spec requires: benchmark for 'low risk' should be forbidden, not just
    return None."""

    def test_low_risk_raises_benchmark_forbidden_error(
        self, registry: BenchmarkProfileRegistry
    ) -> None:
        with pytest.raises(BenchmarkForbiddenError):
            registry.route_task(TaskFamily.governance_mutation, risk_band="low")

    def test_forbidden_error_message_mentions_low(self, registry: BenchmarkProfileRegistry) -> None:
        with pytest.raises(BenchmarkForbiddenError, match="low"):
            registry.route_task(TaskFamily.runtime_perf, risk_band="low")

    def test_medium_risk_is_allowed(self, registry: BenchmarkProfileRegistry) -> None:
        profile = registry.route_task(TaskFamily.governance_mutation, risk_band="medium")
        assert profile is not None
