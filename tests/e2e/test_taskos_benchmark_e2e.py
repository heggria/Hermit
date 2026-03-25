"""E2E tests: benchmark/verification chain from contract through reconciliation.

Exercises the COMPLETE benchmark chain using real BenchmarkProfileRegistry
and BenchmarkRoutingService instances (no mocks).

Tests 13–16 of the Task-OS benchmark verification suite.
"""

from __future__ import annotations

import pytest

from hermit.kernel.verification.benchmark.models import (
    BenchmarkResultClass,
    TaskFamily,
)
from hermit.kernel.verification.benchmark.registry import (
    BenchmarkForbiddenError,
    BenchmarkProfileRegistry,
)
from hermit.kernel.verification.benchmark.routing import (
    BenchmarkRoutingService,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> BenchmarkProfileRegistry:
    return BenchmarkProfileRegistry()


@pytest.fixture()
def svc(registry: BenchmarkProfileRegistry) -> BenchmarkRoutingService:
    return BenchmarkRoutingService(registry=registry)


# ---------------------------------------------------------------------------
# Test 13: contract with verification triggers benchmark verdict
# ---------------------------------------------------------------------------


class TestContractWithVerificationTriggersBenchmarkVerdict:
    """Test 13: Full chain from classification through consumed verdict
    formatted for reconciliation."""

    def test_contract_with_verification_triggers_benchmark_verdict(
        self, registry: BenchmarkProfileRegistry, svc: BenchmarkRoutingService
    ) -> None:
        # 1. Classify a governance_mutation task (paths containing "kernel/policy/")
        family = svc.classify_task_family(
            action_classes=[],
            affected_paths=[
                "src/hermit/kernel/policy/guards/rules_shell.py",
                "src/hermit/kernel/policy/approvals/approvals.py",
            ],
        )
        assert family == TaskFamily.governance_mutation

        # 2. Resolve profile -> trustloop_governance
        profile = svc.resolve_profile(
            task_family=family,
            risk_band="high",
        )
        assert profile is not None
        assert profile.profile_id == "trustloop_governance"

        # 3. Create BenchmarkRun
        run = svc.create_benchmark_run(
            profile=profile,
            task_id="task-e2e-13",
            step_id="step-e2e-13",
            attempt_id="attempt-e2e-13",
            environment_tag="e2e-test",
            commit_ref="e2e-abc123",
        )
        assert run.run_id.startswith("bench_run_")
        assert run.profile_id == "trustloop_governance"
        assert run.started_at > 0.0
        assert run.completed_at is None

        # 4. Evaluate thresholds with passing metrics
        verdict = svc.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 0.98,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 0.97,
                "mean_recovery_depth": 2.5,
                "operator_burden_per_successful_task": 4.0,
                "belief_calibration_under_contradiction": 0.85,
            },
        )
        assert verdict.overall_passed is True
        assert verdict.benchmark_result_class == BenchmarkResultClass.satisfied

        # 5. Mark verdict consumed
        consumed = svc.mark_verdict_consumed(verdict, consumed_by="reconciliation-e2e-13")
        assert consumed.consumed is True
        assert consumed.consumed_by == "reconciliation-e2e-13"
        # Original is immutable — not mutated.
        assert verdict.consumed is False

        # 6. Format for reconciliation
        recon = svc.format_verdict_for_reconciliation(consumed)

        # 7. Verify final assertions
        assert consumed.benchmark_result_class == BenchmarkResultClass.satisfied
        assert recon["reconciliation_only"] is True
        assert recon["benchmark_result_class"] == "satisfied"
        assert recon["benchmark_passed"] is True
        assert recon["benchmark_verdict_id"] == consumed.verdict_id
        assert recon["benchmark_run_id"] == consumed.run_id
        assert recon["benchmark_profile_id"] == "trustloop_governance"
        assert recon["benchmark_task_id"] == "task-e2e-13"
        assert recon["benchmark_regressions"] == []


# ---------------------------------------------------------------------------
# Test 14: low-risk benchmark raises forbidden
# ---------------------------------------------------------------------------


class TestLowRiskBenchmarkRaisesForbidden:
    """Test 14: Benchmark routing for low-risk tasks must raise
    BenchmarkForbiddenError, never silently skip."""

    def test_low_risk_benchmark_raises_forbidden(self, registry: BenchmarkProfileRegistry) -> None:
        # Call route_task with risk_band="low" — must raise, not return None.
        with pytest.raises(BenchmarkForbiddenError) as exc_info:
            registry.route_task(TaskFamily.governance_mutation, risk_band="low")

        # Verify error message indicates low-risk benchmark is forbidden.
        error_msg = str(exc_info.value)
        assert "low" in error_msg.lower()
        assert "forbidden" in error_msg.lower()

    def test_low_risk_via_routing_service_also_raises(self, svc: BenchmarkRoutingService) -> None:
        """The routing service propagates the forbidden error from the registry."""
        with pytest.raises(BenchmarkForbiddenError) as exc_info:
            svc.resolve_profile(
                task_family=TaskFamily.runtime_perf,
                risk_band="low",
            )

        error_msg = str(exc_info.value)
        assert "forbidden" in error_msg.lower()


# ---------------------------------------------------------------------------
# Test 15: governance task routes to trustloop profile
# ---------------------------------------------------------------------------


class TestGovernanceTaskRoutesToTrustloopProfile:
    """Test 15: Governance-mutation tasks with kernel/policy/ paths classify
    correctly and resolve to the trustloop_governance profile."""

    def test_governance_task_routes_to_trustloop_profile(
        self, svc: BenchmarkRoutingService
    ) -> None:
        # 1. Classify paths containing kernel/policy/
        family = svc.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/kernel/policy/guards/rules_shell.py"],
        )
        assert family == TaskFamily.governance_mutation

        # 2. Resolve profile for risk_band="high"
        profile = svc.resolve_profile(
            task_family=family,
            risk_band="high",
        )
        assert profile is not None
        assert profile.profile_id == "trustloop_governance"

        # 3. Verify profile metrics include the required governance metrics
        assert "contract_satisfaction_rate" in profile.metrics
        assert "unauthorized_effect_rate" in profile.metrics

        # Also verify additional governance metrics are present
        expected_governance_metrics = {
            "contract_satisfaction_rate",
            "unauthorized_effect_rate",
            "stale_authorization_execution_rate",
            "belief_calibration_under_contradiction",
            "rollback_success_rate",
            "mean_recovery_depth",
            "operator_burden_per_successful_task",
        }
        assert expected_governance_metrics.issubset(set(profile.metrics))


# ---------------------------------------------------------------------------
# Test 16: runtime perf threshold evaluation with failure
# ---------------------------------------------------------------------------


class TestRuntimePerfThresholdEvaluationWithFailure:
    """Test 16: A runtime_perf task with metrics that violate thresholds
    produces a violated verdict with regression details."""

    def test_runtime_perf_threshold_evaluation_with_failure(
        self, svc: BenchmarkRoutingService
    ) -> None:
        # 1. Classify paths containing runtime/control/runner/
        family = svc.classify_task_family(
            action_classes=[],
            affected_paths=["src/hermit/runtime/control/runner/runner.py"],
        )
        assert family == TaskFamily.runtime_perf

        # 2. Resolve profile -> runtime_perf
        profile = svc.resolve_profile(
            task_family=family,
            risk_band="high",
        )
        assert profile is not None
        assert profile.profile_id == "runtime_perf"

        # 3. Create benchmark run
        run = svc.create_benchmark_run(
            profile=profile,
            task_id="task-e2e-16",
            step_id="step-e2e-16",
            attempt_id="attempt-e2e-16",
        )

        # 4. Evaluate with FAILING metrics: p99_latency=999.0 vs threshold 200.0
        verdict = svc.evaluate_thresholds(
            run=run,
            raw_metrics={
                "p50_latency_ms": 999.0,  # threshold 50.0 — violated (lower is better)
                "p99_latency_ms": 999.0,  # threshold 200.0 — violated (lower is better)
                "throughput_ops": 10.0,  # threshold 100.0 — violated (higher is better)
            },
        )

        # 5. Verify verdict is violated
        assert verdict.overall_passed is False
        assert verdict.benchmark_result_class == BenchmarkResultClass.violated

        # 6. Verify violated threshold details appear in regressions
        assert len(verdict.regressions) > 0

        regression_text = " ".join(verdict.regressions)
        assert "p99_latency_ms" in regression_text
        assert "999.0" in regression_text

        # All three metrics should be regressed
        assert any("p50_latency_ms" in r for r in verdict.regressions)
        assert any("p99_latency_ms" in r for r in verdict.regressions)
        assert any("throughput_ops" in r for r in verdict.regressions)
