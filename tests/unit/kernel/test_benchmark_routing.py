"""Tests for BenchmarkRoutingService."""

from __future__ import annotations

import pytest

from hermit.kernel.verification.benchmark.models import (
    BenchmarkProfile,
    BenchmarkResultClass,
    BenchmarkVerdict,
    TaskFamily,
)
from hermit.kernel.verification.benchmark.registry import (
    BenchmarkForbiddenError,
    BenchmarkProfileRegistry,
)
from hermit.kernel.verification.benchmark.routing import (
    BenchmarkRoutingService,
    MissingBaselineError,
    ProfileFamilyMismatchError,
    VerdictAlreadyConsumedError,
    VerdictNotConsumedError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> BenchmarkProfileRegistry:
    return BenchmarkProfileRegistry()


@pytest.fixture()
def service(registry: BenchmarkProfileRegistry) -> BenchmarkRoutingService:
    return BenchmarkRoutingService(registry=registry)


# ---------------------------------------------------------------------------
# classify_task_family
# ---------------------------------------------------------------------------


class TestClassifyTaskFamily:
    def test_governance_paths(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/kernel/policy/approvals/approvals.py"],
        )
        assert family == TaskFamily.governance_mutation

    def test_runtime_paths(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/kernel/execution/executor/executor.py"],
        )
        assert family == TaskFamily.runtime_perf

    def test_surface_paths(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/surfaces/cli/main.py"],
        )
        assert family == TaskFamily.surface_integration

    def test_learning_paths(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/kernel/context/memory/governance.py"],
        )
        assert family == TaskFamily.learning_template

    def test_verification_paths_classified_as_governance(
        self, service: BenchmarkRoutingService
    ) -> None:
        """kernel/verification/ paths must classify as governance_mutation per spec."""
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/kernel/verification/benchmark/routing.py"],
        )
        assert family == TaskFamily.governance_mutation

    def test_explicit_hint_overrides(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/surfaces/cli/main.py"],
            task_family_hint="governance_mutation",
        )
        assert family == TaskFamily.governance_mutation

    def test_invalid_hint_falls_through(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/surfaces/cli/main.py"],
            task_family_hint="nonexistent_family",
        )
        # Falls through to path heuristic.
        assert family == TaskFamily.surface_integration

    def test_governance_action_class_boost(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=["approval_resolution"],
            affected_paths=[],
        )
        assert family == TaskFamily.governance_mutation

    def test_execute_command_action_class(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=["execute_command"],
            affected_paths=[],
        )
        assert family == TaskFamily.runtime_perf

    def test_no_signal_defaults_to_runtime_perf(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=[],
        )
        assert family == TaskFamily.runtime_perf

    def test_mixed_paths_highest_score_wins(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=[
                "src/hermit/kernel/policy/approvals/approvals.py",
                "src/hermit/kernel/authority/grants.py",
                "src/hermit/kernel/execution/executor/executor.py",
            ],
        )
        # Two governance paths vs one runtime path.
        assert family == TaskFamily.governance_mutation

    def test_adapter_paths_classified_as_surface(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/plugins/builtin/adapters/feishu/handler.py"],
        )
        assert family == TaskFamily.surface_integration


# ---------------------------------------------------------------------------
# resolve_profile
# ---------------------------------------------------------------------------


class TestResolveProfile:
    def test_explicit_profile_found(self, service: BenchmarkRoutingService) -> None:
        profile = service.resolve_profile(
            task_family=TaskFamily.runtime_perf,
            risk_band="low",
            explicit_profile="trustloop_governance",
        )
        assert profile is not None
        assert profile.profile_id == "trustloop_governance"

    def test_explicit_profile_not_found_falls_through(
        self, service: BenchmarkRoutingService
    ) -> None:
        profile = service.resolve_profile(
            task_family=TaskFamily.runtime_perf,
            risk_band="high",
            explicit_profile="nonexistent",
        )
        # Falls through to registry routing.
        assert profile is not None
        assert profile.profile_id == "runtime_perf"

    def test_routing_by_family_and_risk(self, service: BenchmarkRoutingService) -> None:
        profile = service.resolve_profile(
            task_family=TaskFamily.governance_mutation,
            risk_band="critical",
        )
        assert profile is not None
        assert profile.profile_id == "trustloop_governance"

    def test_low_risk_raises_forbidden(self, service: BenchmarkRoutingService) -> None:
        with pytest.raises(BenchmarkForbiddenError):
            service.resolve_profile(
                task_family=TaskFamily.governance_mutation,
                risk_band="low",
            )


# ---------------------------------------------------------------------------
# create_benchmark_run
# ---------------------------------------------------------------------------


class TestCreateBenchmarkRun:
    def test_run_creation(self, service: BenchmarkRoutingService) -> None:
        profile = service.registry.get_profile("trustloop_governance")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="task_001",
            step_id="step_001",
            attempt_id="attempt_001",
        )
        assert run.run_id.startswith("bench_run_")
        assert run.profile_id == "trustloop_governance"
        assert run.task_id == "task_001"
        assert run.step_id == "step_001"
        assert run.attempt_id == "attempt_001"
        assert run.baseline_ref is None
        assert run.started_at > 0.0
        assert run.completed_at is None
        assert run.passed is False

    def test_run_with_baseline(
        self,
        registry: BenchmarkProfileRegistry,
    ) -> None:
        profile = BenchmarkProfile(
            profile_id="with_baseline",
            name="Baseline Test",
            task_family=TaskFamily.runtime_perf,
            description="Has a baseline ref.",
            runner_command="make bench",
            baseline_ref="baseline_abc",
        )
        registry.register_profile(profile)
        svc = BenchmarkRoutingService(registry=registry)
        run = svc.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        assert run.baseline_ref == "baseline_abc"

    def test_run_with_environment_tag_and_commit_ref(
        self, service: BenchmarkRoutingService
    ) -> None:
        """Spec: each benchmark run must carry environment_tag and commit_ref."""
        profile = service.registry.get_profile("runtime_perf")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
            environment_tag="darwin-arm64-py3.13",
            commit_ref="abc123def",
        )
        assert run.environment_tag == "darwin-arm64-py3.13"
        assert run.commit_ref == "abc123def"

    def test_run_default_env_and_commit_are_none(self, service: BenchmarkRoutingService) -> None:
        profile = service.registry.get_profile("runtime_perf")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        assert run.environment_tag is None
        assert run.commit_ref is None


# ---------------------------------------------------------------------------
# validate_profile_family (spec error #1: "跑错 benchmark")
# ---------------------------------------------------------------------------


class TestValidateProfileFamily:
    def test_matching_family_passes(self, service: BenchmarkRoutingService) -> None:
        profile = service.registry.get_profile("trustloop_governance")
        assert profile is not None
        # Should not raise.
        service.validate_profile_family(
            profile=profile,
            task_family=TaskFamily.governance_mutation,
        )

    def test_mismatched_family_raises(self, service: BenchmarkRoutingService) -> None:
        profile = service.registry.get_profile("trustloop_governance")
        assert profile is not None
        with pytest.raises(ProfileFamilyMismatchError, match="governance_mutation"):
            service.validate_profile_family(
                profile=profile,
                task_family=TaskFamily.runtime_perf,
            )

    def test_all_default_profiles_match_their_family(
        self, service: BenchmarkRoutingService
    ) -> None:
        """Every built-in profile must pass validation for its own task_family."""
        for profile in service.registry.list_profiles():
            service.validate_profile_family(
                profile=profile,
                task_family=profile.task_family,
            )


# ---------------------------------------------------------------------------
# validate_baseline_ref (spec error #2: "结果没法比较")
# ---------------------------------------------------------------------------


class TestValidateBaselineRef:
    def test_no_baseline_required_passes(self, service: BenchmarkRoutingService) -> None:
        """Default profiles have no baseline_ref so validation passes."""
        profile = service.registry.get_profile("trustloop_governance")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        # Should not raise.
        service.validate_baseline_ref(run=run)

    def test_missing_baseline_raises(self, registry: BenchmarkProfileRegistry) -> None:
        from dataclasses import replace as dc_replace

        profile = BenchmarkProfile(
            profile_id="needs_baseline",
            name="Needs Baseline",
            task_family=TaskFamily.runtime_perf,
            description="Requires a baseline.",
            runner_command="make bench",
            baseline_ref="baseline_abc",
        )
        registry.register_profile(profile)
        svc = BenchmarkRoutingService(registry=registry)
        run = svc.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        run_no_baseline = dc_replace(run, baseline_ref=None)
        with pytest.raises(MissingBaselineError, match="baseline_abc"):
            svc.validate_baseline_ref(run=run_no_baseline)

    def test_baseline_present_passes(self, registry: BenchmarkProfileRegistry) -> None:
        profile = BenchmarkProfile(
            profile_id="has_baseline",
            name="Has Baseline",
            task_family=TaskFamily.runtime_perf,
            description="Has a baseline.",
            runner_command="make bench",
            baseline_ref="baseline_xyz",
        )
        registry.register_profile(profile)
        svc = BenchmarkRoutingService(registry=registry)
        run = svc.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        # baseline_ref is set from profile, so this should pass.
        svc.validate_baseline_ref(run=run)


# ---------------------------------------------------------------------------
# evaluate_thresholds
# ---------------------------------------------------------------------------


class TestEvaluateThresholds:
    def test_all_thresholds_pass(self, service: BenchmarkRoutingService) -> None:
        profile = service.registry.get_profile("trustloop_governance")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        verdict = service.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 1.0,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 1.0,
                "mean_recovery_depth": 1.0,
                "operator_burden_per_successful_task": 2.0,
                "belief_calibration_under_contradiction": 0.9,
            },
        )
        assert verdict.overall_passed is True
        assert verdict.regressions == []
        assert verdict.verdict_id.startswith("bench_verdict_")

    def test_verdict_has_profile_and_task_ids(self, service: BenchmarkRoutingService) -> None:
        """Verdict must carry profile_id and task_id for traceability."""
        profile = service.registry.get_profile("runtime_perf")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="task_xyz",
            step_id="s1",
            attempt_id="a1",
        )
        verdict = service.evaluate_thresholds(
            run=run,
            raw_metrics={
                "p50_latency_ms": 10.0,
                "p99_latency_ms": 50.0,
                "throughput_ops": 200.0,
            },
        )
        assert verdict.profile_id == "runtime_perf"
        assert verdict.task_id == "task_xyz"
        assert verdict.benchmark_result_class == BenchmarkResultClass.satisfied

    def test_regression_detected(self, service: BenchmarkRoutingService) -> None:
        profile = service.registry.get_profile("trustloop_governance")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        verdict = service.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 0.80,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 1.0,
                "mean_recovery_depth": 1.0,
                "operator_burden_per_successful_task": 2.0,
                "belief_calibration_under_contradiction": 0.9,
            },
        )
        assert verdict.overall_passed is False
        assert len(verdict.regressions) == 1
        assert "contract_satisfaction_rate" in verdict.regressions[0]

    def test_latency_lower_is_better(self, service: BenchmarkRoutingService) -> None:
        profile = service.registry.get_profile("runtime_perf")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        # p50_latency_ms threshold is 50.0; 60.0 should fail.
        verdict = service.evaluate_thresholds(
            run=run,
            raw_metrics={
                "p50_latency_ms": 60.0,
                "p99_latency_ms": 100.0,
                "throughput_ops": 150.0,
            },
        )
        assert verdict.overall_passed is False
        assert any("p50_latency_ms" in r for r in verdict.regressions)

    def test_missing_metric_counts_as_regression(self, service: BenchmarkRoutingService) -> None:
        profile = service.registry.get_profile("trustloop_governance")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        verdict = service.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 1.0,
                "unauthorized_effect_rate": 0.0,
                # stale_authorization_execution_rate missing
                "rollback_success_rate": 1.0,
                "mean_recovery_depth": 1.0,
                "operator_burden_per_successful_task": 2.0,
                "belief_calibration_under_contradiction": 0.9,
            },
        )
        assert verdict.overall_passed is False
        assert any("stale_authorization_execution_rate" in r for r in verdict.regressions)
        assert any("missing" in r for r in verdict.regressions)

    def test_no_thresholds_passes(self, service: BenchmarkRoutingService) -> None:
        profile = BenchmarkProfile(
            profile_id="no_thresh",
            name="No Thresholds",
            task_family=TaskFamily.runtime_perf,
            description="Empty thresholds.",
            runner_command="echo ok",
        )
        service.registry.register_profile(profile)
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        verdict = service.evaluate_thresholds(run=run, raw_metrics={"foo": 42.0})
        assert verdict.overall_passed is True
        assert verdict.regressions == []

    def test_significant_improvement_detected(self, service: BenchmarkRoutingService) -> None:
        profile = service.registry.get_profile("integration_regression")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        # regression_count threshold is 0.0 (lower is better).
        # pass_rate threshold is 1.0 — providing 1.5 would be a 50% improvement.
        verdict = service.evaluate_thresholds(
            run=run,
            raw_metrics={
                "pass_rate": 1.5,
                "regression_count": 0.0,
            },
        )
        assert verdict.overall_passed is True
        assert len(verdict.improvements) >= 1

    def test_verdict_notes_contain_profile(self, service: BenchmarkRoutingService) -> None:
        profile = service.registry.get_profile("trustloop_governance")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        verdict = service.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 1.0,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 1.0,
                "mean_recovery_depth": 1.0,
                "operator_burden_per_successful_task": 2.0,
                "belief_calibration_under_contradiction": 1.0,
            },
        )
        assert "trustloop_governance" in verdict.notes

    def test_error_metric_lower_is_better(self, service: BenchmarkRoutingService) -> None:
        """Spec: 'error = lower is better'. Any metric containing 'error' should
        use lower-is-better semantics, not just 'error_rate'."""
        profile = BenchmarkProfile(
            profile_id="error_test",
            name="Error Test",
            task_family=TaskFamily.governance_mutation,
            description="Tests error metric semantics.",
            runner_command="echo ok",
            thresholds={"total_error_count": 5.0},
        )
        service.registry.register_profile(profile)
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        # 3.0 <= 5.0 should pass (lower is better).
        verdict_pass = service.evaluate_thresholds(run=run, raw_metrics={"total_error_count": 3.0})
        assert verdict_pass.overall_passed is True

        # 10.0 > 5.0 should fail.
        run2 = service.create_benchmark_run(
            profile=profile,
            task_id="t2",
            step_id="s2",
            attempt_id="a2",
        )
        verdict_fail = service.evaluate_thresholds(
            run=run2, raw_metrics={"total_error_count": 10.0}
        )
        assert verdict_fail.overall_passed is False

    def test_unauthorized_effect_rate_lower_is_better(
        self, service: BenchmarkRoutingService
    ) -> None:
        """unauthorized_effect_rate threshold is 0.0; any value > 0 must fail."""
        profile = service.registry.get_profile("trustloop_governance")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="t1",
            step_id="s1",
            attempt_id="a1",
        )
        metrics = {
            "contract_satisfaction_rate": 1.0,
            "unauthorized_effect_rate": 0.01,
            "stale_authorization_execution_rate": 0.0,
            "rollback_success_rate": 1.0,
            "mean_recovery_depth": 1.0,
            "operator_burden_per_successful_task": 2.0,
            "belief_calibration_under_contradiction": 0.9,
        }
        verdict = service.evaluate_thresholds(run=run, raw_metrics=metrics)
        assert verdict.overall_passed is False
        assert any("unauthorized_effect_rate" in r for r in verdict.regressions)


# ---------------------------------------------------------------------------
# should_benchmark
# ---------------------------------------------------------------------------


class TestShouldBenchmark:
    def test_none_requirements(self, service: BenchmarkRoutingService) -> None:
        assert service.should_benchmark(None) is False

    def test_all_forbidden(self, service: BenchmarkRoutingService) -> None:
        assert (
            service.should_benchmark(
                {
                    "governance_bench": "forbidden",
                    "performance_bench": "forbidden",
                }
            )
            is False
        )

    def test_governance_required(self, service: BenchmarkRoutingService) -> None:
        assert (
            service.should_benchmark(
                {
                    "governance_bench": "required",
                    "performance_bench": "forbidden",
                }
            )
            is True
        )

    def test_performance_optional(self, service: BenchmarkRoutingService) -> None:
        assert (
            service.should_benchmark(
                {
                    "governance_bench": "forbidden",
                    "performance_bench": "optional",
                }
            )
            is True
        )

    def test_explicit_profile(self, service: BenchmarkRoutingService) -> None:
        assert (
            service.should_benchmark(
                {
                    "benchmark_profile": "trustloop_governance",
                }
            )
            is True
        )

    def test_empty_profile_string(self, service: BenchmarkRoutingService) -> None:
        assert (
            service.should_benchmark(
                {
                    "benchmark_profile": "",
                    "governance_bench": "forbidden",
                    "performance_bench": "forbidden",
                }
            )
            is False
        )

    def test_empty_dict(self, service: BenchmarkRoutingService) -> None:
        assert service.should_benchmark({}) is False

    def test_benchmark_profile_none_string(self, service: BenchmarkRoutingService) -> None:
        """benchmark_profile='none' must not trigger benchmarking."""
        assert (
            service.should_benchmark(
                {
                    "benchmark_profile": "none",
                    "governance_bench": "forbidden",
                    "performance_bench": "forbidden",
                }
            )
            is False
        )

    def test_benchmark_profile_none_string_with_bench_enabled(
        self, service: BenchmarkRoutingService
    ) -> None:
        """benchmark_profile='none' but governance_bench='required' still triggers."""
        assert (
            service.should_benchmark(
                {
                    "benchmark_profile": "none",
                    "governance_bench": "required",
                    "performance_bench": "forbidden",
                }
            )
            is True
        )


# ---------------------------------------------------------------------------
# format_verdict_for_reconciliation
# ---------------------------------------------------------------------------


class TestFormatVerdictForReconciliation:
    def test_passed_verdict_format(self, service: BenchmarkRoutingService) -> None:
        verdict = BenchmarkVerdict(
            verdict_id="v_001",
            run_id="r_001",
            profile_id="trustloop_governance",
            task_id="task_001",
            overall_passed=True,
            benchmark_result_class=BenchmarkResultClass.satisfied,
            improvements=["throughput: 150 vs threshold 100"],
            notes="All good.",
        )
        result = service.format_verdict_for_reconciliation(verdict)
        assert result["benchmark_verdict_id"] == "v_001"
        assert result["benchmark_run_id"] == "r_001"
        assert result["benchmark_passed"] is True
        assert result["benchmark_result_class"] == "satisfied"
        assert result["benchmark_regressions"] == []
        assert len(result["benchmark_improvements"]) == 1
        assert result["benchmark_notes"] == "All good."
        assert result["reconciliation_only"] is True
        assert result["benchmark_profile_id"] == "trustloop_governance"
        assert result["benchmark_task_id"] == "task_001"

    def test_failed_verdict_format(self, service: BenchmarkRoutingService) -> None:
        verdict = BenchmarkVerdict(
            verdict_id="v_002",
            run_id="r_002",
            profile_id="trustloop_governance",
            task_id="task_002",
            overall_passed=False,
            regressions=["chain_integrity: 0.8 vs threshold 1.0"],
        )
        result = service.format_verdict_for_reconciliation(verdict)
        assert result["benchmark_passed"] is False
        assert result["benchmark_result_class"] == "violated"
        assert len(result["benchmark_regressions"]) == 1
        assert result["reconciliation_only"] is True

    def test_format_returns_new_dict(self, service: BenchmarkRoutingService) -> None:
        verdict = BenchmarkVerdict(
            verdict_id="v_003",
            run_id="r_003",
            profile_id="runtime_perf",
            task_id="task_003",
            overall_passed=True,
            benchmark_result_class=BenchmarkResultClass.satisfied,
        )
        a = service.format_verdict_for_reconciliation(verdict)
        b = service.format_verdict_for_reconciliation(verdict)
        assert a == b
        assert a is not b
        assert a["benchmark_regressions"] is not b["benchmark_regressions"]


# ---------------------------------------------------------------------------
# End-to-end routing flow
# ---------------------------------------------------------------------------


class TestEndToEndFlow:
    """Integration-style tests that exercise the full routing pipeline."""

    def test_full_routing_pass_flow(self, service: BenchmarkRoutingService) -> None:
        """Plan -> classify -> resolve -> create run -> evaluate -> format."""
        reqs = {
            "governance_bench": "required",
            "performance_bench": "forbidden",
            "benchmark_profile": None,
        }
        assert service.should_benchmark(reqs) is True

        family = service.classify_task_family(
            action_classes=["approval_resolution"],
            affected_paths=["src/hermit/kernel/policy/approvals/approvals.py"],
        )
        assert family == TaskFamily.governance_mutation

        profile = service.resolve_profile(
            task_family=family,
            risk_band="high",
        )
        assert profile is not None

        run = service.create_benchmark_run(
            profile=profile,
            task_id="task_e2e",
            step_id="step_e2e",
            attempt_id="attempt_e2e",
        )
        assert run.profile_id == profile.profile_id

        verdict = service.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 1.0,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 1.0,
                "mean_recovery_depth": 1.0,
                "operator_burden_per_successful_task": 2.0,
                "belief_calibration_under_contradiction": 0.9,
            },
        )
        assert verdict.overall_passed is True

        reconciliation_input = service.format_verdict_for_reconciliation(verdict)
        assert reconciliation_input["benchmark_result_class"] == "satisfied"

    def test_full_routing_fail_flow(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/kernel/execution/executor/executor.py"],
        )
        assert family == TaskFamily.runtime_perf

        profile = service.resolve_profile(
            task_family=family,
            risk_band="critical",
        )
        assert profile is not None

        run = service.create_benchmark_run(
            profile=profile,
            task_id="task_fail",
            step_id="step_fail",
            attempt_id="attempt_fail",
        )

        verdict = service.evaluate_thresholds(
            run=run,
            raw_metrics={
                "p50_latency_ms": 200.0,  # way over 50.0 threshold
                "p99_latency_ms": 500.0,  # way over 200.0 threshold
                "throughput_ops": 50.0,  # under 100.0 threshold
            },
        )
        assert verdict.overall_passed is False
        assert len(verdict.regressions) == 3

        reconciliation_input = service.format_verdict_for_reconciliation(verdict)
        assert reconciliation_input["benchmark_result_class"] == "violated"

    def test_forbidden_when_risk_band_low(self, service: BenchmarkRoutingService) -> None:
        family = service.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/kernel/policy/approvals/approvals.py"],
        )
        with pytest.raises(BenchmarkForbiddenError):
            service.resolve_profile(
                task_family=family,
                risk_band="low",
            )

    def test_default_registry_created_when_none(self) -> None:
        svc = BenchmarkRoutingService(registry=None)
        assert svc.registry is not None
        assert len(svc.registry.list_profiles()) == 4


# ---------------------------------------------------------------------------
# route_from_contract
# ---------------------------------------------------------------------------


class TestRouteFromContract:
    """Tests for the convenience method that reads verification_requirements."""

    def test_returns_none_when_no_requirements(self, service: BenchmarkRoutingService) -> None:
        result = service.route_from_contract(
            task_family=None,
            verification_requirements=None,
        )
        assert result is None

    def test_returns_none_when_benchmark_not_needed(self, service: BenchmarkRoutingService) -> None:
        result = service.route_from_contract(
            task_family="governance_mutation",
            verification_requirements={
                "functional": "required",
                "governance_bench": "forbidden",
                "performance_bench": "forbidden",
                "benchmark_profile": "none",
            },
        )
        assert result is None

    def test_routes_with_explicit_profile(self, service: BenchmarkRoutingService) -> None:
        result = service.route_from_contract(
            task_family="governance_mutation",
            verification_requirements={
                "governance_bench": "required",
                "benchmark_profile": "trustloop_governance",
            },
            risk_level="high",
        )
        assert result is not None
        assert result.profile_id == "trustloop_governance"

    def test_routes_with_governance_bench_required(self, service: BenchmarkRoutingService) -> None:
        result = service.route_from_contract(
            task_family="governance_mutation",
            verification_requirements={
                "governance_bench": "required",
                "performance_bench": "forbidden",
                "benchmark_profile": "none",
            },
            risk_level="high",
        )
        assert result is not None
        assert result.profile_id == "trustloop_governance"

    def test_routes_with_heuristic_when_no_family(self, service: BenchmarkRoutingService) -> None:
        result = service.route_from_contract(
            task_family=None,
            verification_requirements={
                "governance_bench": "optional",
                "performance_bench": "forbidden",
            },
            risk_level="medium",
            action_classes=["approval_resolution"],
            affected_paths=["src/hermit/kernel/policy/approvals/approvals.py"],
        )
        assert result is not None
        assert result.profile_id == "trustloop_governance"

    def test_routes_runtime_perf_family(self, service: BenchmarkRoutingService) -> None:
        result = service.route_from_contract(
            task_family="runtime_perf",
            verification_requirements={
                "performance_bench": "required",
                "benchmark_profile": "runtime_perf",
            },
            risk_level="high",
        )
        assert result is not None
        assert result.profile_id == "runtime_perf"


# ---------------------------------------------------------------------------
# mark_verdict_consumed
# ---------------------------------------------------------------------------


class TestMarkVerdictConsumed:
    def test_consume_verdict_returns_new_instance(self, service: BenchmarkRoutingService) -> None:
        verdict = BenchmarkVerdict(
            verdict_id="v_100",
            run_id="r_100",
            profile_id="trustloop_governance",
            task_id="task_100",
            overall_passed=True,
            benchmark_result_class=BenchmarkResultClass.satisfied,
        )
        consumed = service.mark_verdict_consumed(verdict, consumed_by="reconciliation_001")
        assert consumed.consumed is True
        assert consumed.consumed_by == "reconciliation_001"
        # Original is unchanged (immutable pattern).
        assert verdict.consumed is False
        assert verdict.consumed_by is None

    def test_consume_verdict_preserves_fields(self, service: BenchmarkRoutingService) -> None:
        verdict = BenchmarkVerdict(
            verdict_id="v_101",
            run_id="r_101",
            profile_id="runtime_perf",
            task_id="task_101",
            overall_passed=False,
            benchmark_result_class=BenchmarkResultClass.violated,
            regressions=["latency: 200 vs threshold 50"],
            notes="Some notes.",
        )
        consumed = service.mark_verdict_consumed(verdict, consumed_by="recon_x")
        assert consumed.verdict_id == "v_101"
        assert consumed.overall_passed is False
        assert consumed.regressions == ["latency: 200 vs threshold 50"]
        assert consumed.notes == "Some notes."

    def test_double_consume_raises(self, service: BenchmarkRoutingService) -> None:
        verdict = BenchmarkVerdict(
            verdict_id="v_102",
            run_id="r_102",
            profile_id="trustloop_governance",
            task_id="task_102",
            overall_passed=True,
            benchmark_result_class=BenchmarkResultClass.satisfied,
            consumed=True,
            consumed_by="first_consumer",
        )
        with pytest.raises(VerdictAlreadyConsumedError, match="first_consumer"):
            service.mark_verdict_consumed(verdict, consumed_by="second_consumer")

    def test_consume_failed_verdict(self, service: BenchmarkRoutingService) -> None:
        verdict = BenchmarkVerdict(
            verdict_id="v_103",
            run_id="r_103",
            profile_id="trustloop_governance",
            task_id="task_103",
            overall_passed=False,
            benchmark_result_class=BenchmarkResultClass.violated,
            regressions=["chain_integrity: 0.5 vs threshold 1.0"],
        )
        consumed = service.mark_verdict_consumed(verdict, consumed_by="recon_fail")
        assert consumed.consumed is True
        assert consumed.consumed_by == "recon_fail"
        assert consumed.overall_passed is False


# ---------------------------------------------------------------------------
# require_verdict_consumption
# ---------------------------------------------------------------------------


class TestRequireVerdictConsumption:
    def test_passing_verdict_unconsumed_ok(self, service: BenchmarkRoutingService) -> None:
        """Passing verdicts do not need consumption enforcement."""
        verdict = BenchmarkVerdict(
            verdict_id="v_200",
            run_id="r_200",
            profile_id="trustloop_governance",
            task_id="task_200",
            overall_passed=True,
            benchmark_result_class=BenchmarkResultClass.satisfied,
            consumed=False,
        )
        # Should not raise.
        service.require_verdict_consumption(verdict)

    def test_failing_verdict_unconsumed_raises(self, service: BenchmarkRoutingService) -> None:
        verdict = BenchmarkVerdict(
            verdict_id="v_201",
            run_id="r_201",
            profile_id="trustloop_governance",
            task_id="task_201",
            overall_passed=False,
            benchmark_result_class=BenchmarkResultClass.violated,
            consumed=False,
        )
        with pytest.raises(VerdictNotConsumedError, match="v_201"):
            service.require_verdict_consumption(verdict)

    def test_failing_verdict_consumed_ok(self, service: BenchmarkRoutingService) -> None:
        verdict = BenchmarkVerdict(
            verdict_id="v_202",
            run_id="r_202",
            profile_id="trustloop_governance",
            task_id="task_202",
            overall_passed=False,
            benchmark_result_class=BenchmarkResultClass.violated,
            consumed=True,
            consumed_by="reconciliation_abc",
        )
        # Should not raise.
        service.require_verdict_consumption(verdict)

    def test_passing_verdict_consumed_ok(self, service: BenchmarkRoutingService) -> None:
        verdict = BenchmarkVerdict(
            verdict_id="v_203",
            run_id="r_203",
            profile_id="trustloop_governance",
            task_id="task_203",
            overall_passed=True,
            benchmark_result_class=BenchmarkResultClass.satisfied,
            consumed=True,
            consumed_by="recon_pass",
        )
        # Should not raise.
        service.require_verdict_consumption(verdict)


# ---------------------------------------------------------------------------
# Verdict consumption end-to-end flow
# ---------------------------------------------------------------------------


class TestVerdictConsumptionEndToEnd:
    def test_evaluate_then_consume_flow(self, service: BenchmarkRoutingService) -> None:
        """Full flow: evaluate -> consume -> require."""
        profile = service.registry.get_profile("trustloop_governance")
        assert profile is not None
        run = service.create_benchmark_run(
            profile=profile,
            task_id="task_e2e_consume",
            step_id="step_001",
            attempt_id="attempt_001",
        )
        # Trigger a failed verdict.
        verdict = service.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 0.5,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 1.0,
                "mean_recovery_depth": 1.0,
                "operator_burden_per_successful_task": 2.0,
                "belief_calibration_under_contradiction": 0.9,
            },
        )
        assert verdict.overall_passed is False
        assert verdict.consumed is False

        # Unconsumed failed verdict must raise.
        with pytest.raises(VerdictNotConsumedError):
            service.require_verdict_consumption(verdict)

        # Consume the verdict.
        consumed = service.mark_verdict_consumed(verdict, consumed_by="reconciliation_e2e")
        assert consumed.consumed is True

        # Now requirement passes.
        service.require_verdict_consumption(consumed)

        # Double consumption is blocked.
        with pytest.raises(VerdictAlreadyConsumedError):
            service.mark_verdict_consumed(consumed, consumed_by="another")
