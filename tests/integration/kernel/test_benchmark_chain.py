"""Integration test: benchmark classify -> route -> profile -> run -> evaluate -> verdict -> consume -> reconciliation format.

Exercises the COMPLETE benchmark chain using real BenchmarkProfileRegistry
and BenchmarkRoutingService instances (no mocks).

Also tests the ReconciliationExecutor._run_benchmark_if_required integration
that wires benchmark routing into the post-execution reconciliation flow.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.reconciliation_executor import ReconciliationExecutor
from hermit.kernel.task.models.records import ExecutionContractRecord
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
def svc(registry: BenchmarkProfileRegistry) -> BenchmarkRoutingService:
    return BenchmarkRoutingService(registry=registry)


def _make_attempt_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults = {
        "conversation_id": "conv-bench",
        "task_id": "task-bench",
        "step_id": "step-bench",
        "step_attempt_id": "attempt-bench",
        "source_channel": "chat",
        "workspace_root": "/tmp/ws",
    }
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


def _make_contract_record(**overrides: Any) -> ExecutionContractRecord:
    defaults: dict[str, Any] = {
        "contract_id": "contract-bench-1",
        "task_id": "task-bench",
        "step_id": "step-bench",
        "step_attempt_id": "attempt-bench",
        "objective": "test benchmark integration",
        "success_criteria": {"action_class": "write_local"},
        "risk_budget": {"risk_level": "high"},
        "expected_effects": [
            "path:src/hermit/kernel/policy/approvals/approvals.py",
        ],
        "task_family": "governance_mutation",
        "verification_requirements": {
            "functional": "required",
            "governance_bench": "required",
            "performance_bench": "forbidden",
            "benchmark_profile": "trustloop_governance",
        },
    }
    defaults.update(overrides)
    return ExecutionContractRecord(**defaults)


def _make_executor(mock_store: MagicMock) -> ReconciliationExecutor:
    return ReconciliationExecutor(
        store=mock_store,
        artifact_store=MagicMock(),
        reconciliations=MagicMock(),
        execution_contracts=MagicMock(),
        evidence_cases=MagicMock(),
        pattern_learner=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Full chain: passing scenario
# ---------------------------------------------------------------------------


class TestBenchmarkChainPass:
    """Exercises the complete chain with metrics that satisfy all thresholds."""

    def test_full_chain_passing(self, svc: BenchmarkRoutingService) -> None:
        # 1. Classification: kernel/policy/ files -> governance_mutation
        family = svc.classify_task_family(
            action_classes=[],
            affected_paths=[
                "src/hermit/kernel/policy/approvals/approvals.py",
                "src/hermit/kernel/policy/decisions/engine.py",
            ],
        )
        assert family == TaskFamily.governance_mutation

        # 2. Routing: governance_mutation + risk_band="high" -> trustloop_governance
        profile = svc.resolve_profile(
            task_family=family,
            risk_band="high",
        )
        assert profile is not None
        assert profile.profile_id == "trustloop_governance"

        # 3. Profile validation: family matches -> no error
        svc.validate_profile_family(profile=profile, task_family=family)

        # 4. Baseline validation: default profile has no baseline_ref -> no error
        run = svc.create_benchmark_run(
            profile=profile,
            task_id="task_chain_pass",
            step_id="step_001",
            attempt_id="attempt_001",
            environment_tag="test-env",
            commit_ref="abc123",
        )
        svc.validate_baseline_ref(run=run)

        # 5. Run creation checks
        assert run.run_id.startswith("bench_run_")
        assert run.profile_id == "trustloop_governance"
        assert run.task_id == "task_chain_pass"
        assert run.started_at > 0.0
        assert run.completed_at is None

        # 6. Threshold evaluation: all passing metrics
        verdict = svc.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 0.95,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 0.95,
                "mean_recovery_depth": 3.0,
                "operator_burden_per_successful_task": 5.0,
                "belief_calibration_under_contradiction": 0.8,
            },
        )
        assert verdict.overall_passed is True
        assert verdict.benchmark_result_class == BenchmarkResultClass.satisfied
        assert verdict.regressions == []
        assert verdict.verdict_id.startswith("bench_verdict_")
        assert verdict.profile_id == "trustloop_governance"
        assert verdict.task_id == "task_chain_pass"

        # 7. Verdict consumption
        consumed = svc.mark_verdict_consumed(verdict, consumed_by="reconciliation_test")
        assert consumed.consumed is True
        assert consumed.consumed_by == "reconciliation_test"
        # Original is immutable.
        assert verdict.consumed is False

        # 8. Double-consume -> error
        with pytest.raises(VerdictAlreadyConsumedError):
            svc.mark_verdict_consumed(consumed, consumed_by="second_consumer")

        # 9. Require consumption: consumed passing verdict -> passes
        svc.require_verdict_consumption(consumed)

        # 10. Reconciliation format
        recon = svc.format_verdict_for_reconciliation(consumed)
        assert recon["reconciliation_only"] is True
        assert recon["benchmark_result_class"] == "satisfied"
        assert recon["benchmark_passed"] is True
        assert recon["benchmark_verdict_id"] == consumed.verdict_id
        assert recon["benchmark_run_id"] == consumed.run_id
        assert recon["benchmark_profile_id"] == "trustloop_governance"
        assert recon["benchmark_task_id"] == "task_chain_pass"
        assert recon["benchmark_regressions"] == []


# ---------------------------------------------------------------------------
# Full chain: failing scenario
# ---------------------------------------------------------------------------


class TestBenchmarkChainFail:
    """Exercises the complete chain with metrics that violate thresholds."""

    def test_full_chain_failing(self, svc: BenchmarkRoutingService) -> None:
        # 1. Classification
        family = svc.classify_task_family(
            action_classes=[],
            affected_paths=[
                "src/hermit/kernel/policy/approvals/approvals.py",
                "src/hermit/kernel/authority/grants.py",
            ],
        )
        assert family == TaskFamily.governance_mutation

        # 2. Routing
        profile = svc.resolve_profile(task_family=family, risk_band="high")
        assert profile is not None

        # 3. Profile validation
        svc.validate_profile_family(profile=profile, task_family=family)

        # 4. Run creation
        run = svc.create_benchmark_run(
            profile=profile,
            task_id="task_chain_fail",
            step_id="step_002",
            attempt_id="attempt_002",
        )

        # 5. Baseline validation
        svc.validate_baseline_ref(run=run)

        # 6. Threshold evaluation: unauthorized_effect_rate=0.05 exceeds threshold 0.0
        verdict = svc.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 0.95,
                "unauthorized_effect_rate": 0.05,  # threshold is 0.0 (lower is better)
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 0.95,
                "mean_recovery_depth": 3.0,
                "operator_burden_per_successful_task": 5.0,
                "belief_calibration_under_contradiction": 0.8,
            },
        )
        assert verdict.overall_passed is False
        assert verdict.benchmark_result_class == BenchmarkResultClass.violated
        assert len(verdict.regressions) > 0
        assert any("unauthorized_effect_rate" in r for r in verdict.regressions)

        # 7. Require consumption: unconsumed failed verdict -> raises
        with pytest.raises(VerdictNotConsumedError):
            svc.require_verdict_consumption(verdict)

        # 8. Consume, then require passes
        consumed = svc.mark_verdict_consumed(verdict, consumed_by="reconciliation_fail_test")
        assert consumed.consumed is True
        svc.require_verdict_consumption(consumed)

        # 9. Double-consume -> error
        with pytest.raises(VerdictAlreadyConsumedError):
            svc.mark_verdict_consumed(consumed, consumed_by="third")

        # 10. Reconciliation format
        recon = svc.format_verdict_for_reconciliation(consumed)
        assert recon["reconciliation_only"] is True
        assert recon["benchmark_result_class"] == "violated"
        assert recon["benchmark_passed"] is False
        assert len(recon["benchmark_regressions"]) > 0


# ---------------------------------------------------------------------------
# route_from_contract
# ---------------------------------------------------------------------------


class TestRouteFromContractChain:
    """Tests route_from_contract with verification_requirements dict."""

    def test_route_from_contract_governance_required(self, svc: BenchmarkRoutingService) -> None:
        profile = svc.route_from_contract(
            task_family="governance_mutation",
            verification_requirements={
                "governance_bench": "required",
                "performance_bench": "forbidden",
                "benchmark_profile": "trustloop_governance",
            },
            risk_level="high",
            action_classes=["approval_resolution"],
            affected_paths=["src/hermit/kernel/policy/approvals/approvals.py"],
        )
        assert profile is not None
        assert profile.profile_id == "trustloop_governance"
        assert profile.task_family == TaskFamily.governance_mutation

    def test_route_from_contract_heuristic_classification(
        self, svc: BenchmarkRoutingService
    ) -> None:
        """When task_family is None, heuristic classification from paths is used."""
        profile = svc.route_from_contract(
            task_family=None,
            verification_requirements={
                "governance_bench": "required",
                "performance_bench": "forbidden",
            },
            risk_level="critical",
            affected_paths=[
                "src/hermit/kernel/policy/approvals/approvals.py",
                "src/hermit/kernel/authority/workspaces.py",
            ],
        )
        assert profile is not None
        assert profile.profile_id == "trustloop_governance"

    def test_route_from_contract_performance_bench(self, svc: BenchmarkRoutingService) -> None:
        profile = svc.route_from_contract(
            task_family="runtime_perf",
            verification_requirements={
                "governance_bench": "forbidden",
                "performance_bench": "required",
                "benchmark_profile": "runtime_perf",
            },
            risk_level="high",
        )
        assert profile is not None
        assert profile.profile_id == "runtime_perf"

    def test_route_from_contract_returns_none_when_not_needed(
        self, svc: BenchmarkRoutingService
    ) -> None:
        result = svc.route_from_contract(
            task_family=None,
            verification_requirements={
                "governance_bench": "forbidden",
                "performance_bench": "forbidden",
            },
        )
        assert result is None


# ---------------------------------------------------------------------------
# Low-risk forbidden
# ---------------------------------------------------------------------------


class TestLowRiskForbidden:
    """Spec: benchmark for low-risk tasks is forbidden, not silently skipped."""

    def test_resolve_profile_low_risk_raises(self, svc: BenchmarkRoutingService) -> None:
        with pytest.raises(BenchmarkForbiddenError):
            svc.resolve_profile(
                task_family=TaskFamily.governance_mutation,
                risk_band="low",
            )

    def test_route_from_contract_low_risk_raises(self, svc: BenchmarkRoutingService) -> None:
        """Low-risk with no explicit profile falls through to route_task which raises."""
        with pytest.raises(BenchmarkForbiddenError):
            svc.route_from_contract(
                task_family="governance_mutation",
                verification_requirements={
                    "governance_bench": "required",
                    "performance_bench": "forbidden",
                },
                risk_level="low",
                affected_paths=["src/hermit/kernel/policy/approvals/approvals.py"],
            )

    def test_registry_route_task_low_risk_raises(self, registry: BenchmarkProfileRegistry) -> None:
        with pytest.raises(BenchmarkForbiddenError):
            registry.route_task(TaskFamily.runtime_perf, risk_band="low")


# ---------------------------------------------------------------------------
# Reconciliation format validation
# ---------------------------------------------------------------------------


class TestReconciliationFormat:
    """Validates the reconciliation output structure in detail."""

    def test_format_contains_all_required_fields(self, svc: BenchmarkRoutingService) -> None:
        profile = svc.resolve_profile(
            task_family=TaskFamily.governance_mutation,
            risk_band="high",
        )
        assert profile is not None
        run = svc.create_benchmark_run(
            profile=profile,
            task_id="task_recon",
            step_id="step_recon",
            attempt_id="attempt_recon",
        )
        verdict = svc.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 1.0,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 1.0,
                "mean_recovery_depth": 1.0,
                "operator_burden_per_successful_task": 2.0,
                "belief_calibration_under_contradiction": 0.95,
            },
        )
        recon = svc.format_verdict_for_reconciliation(verdict)

        required_keys = {
            "benchmark_verdict_id",
            "benchmark_run_id",
            "benchmark_profile_id",
            "benchmark_task_id",
            "benchmark_passed",
            "benchmark_regressions",
            "benchmark_improvements",
            "benchmark_notes",
            "benchmark_result_class",
            "reconciliation_only",
        }
        assert required_keys.issubset(recon.keys())
        assert recon["reconciliation_only"] is True
        assert recon["benchmark_result_class"] in ("satisfied", "violated")

    def test_format_lists_are_independent_copies(self, svc: BenchmarkRoutingService) -> None:
        """Each call to format_verdict_for_reconciliation returns independent lists."""
        profile = svc.resolve_profile(
            task_family=TaskFamily.governance_mutation,
            risk_band="high",
        )
        assert profile is not None
        run = svc.create_benchmark_run(
            profile=profile,
            task_id="task_copy",
            step_id="step_copy",
            attempt_id="attempt_copy",
        )
        verdict = svc.evaluate_thresholds(
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
        a = svc.format_verdict_for_reconciliation(verdict)
        b = svc.format_verdict_for_reconciliation(verdict)
        assert a == b
        assert a is not b
        assert a["benchmark_regressions"] is not b["benchmark_regressions"]


# ---------------------------------------------------------------------------
# ReconciliationExecutor._run_benchmark_if_required integration
# ---------------------------------------------------------------------------


class TestBenchmarkReconciliationIntegration:
    """Tests that _run_benchmark_if_required is correctly wired into
    the reconciliation executor and triggers benchmark during reconciliation."""

    def test_contract_with_verification_requirements_triggers_benchmark(self) -> None:
        """A contract with governance_bench=required triggers benchmark
        during reconciliation and produces a consumed verdict."""
        mock_store = MagicMock()
        executor = _make_executor(mock_store)
        contract = _make_contract_record()
        ctx = _make_attempt_ctx()

        verdict = executor._run_benchmark_if_required(
            contract=contract,
            receipt_id="rcpt-1",
            attempt_ctx=ctx,
        )

        assert verdict is not None
        assert verdict.overall_passed is True
        assert verdict.consumed is True
        assert verdict.consumed_by == "rcpt-1"
        assert verdict.profile_id == "trustloop_governance"
        assert verdict.task_id == "task-bench"

    def test_contract_without_verification_requirements_skips_benchmark(self) -> None:
        """A contract with no verification_requirements returns None."""
        mock_store = MagicMock()
        executor = _make_executor(mock_store)
        contract = _make_contract_record(verification_requirements=None)
        ctx = _make_attempt_ctx()

        verdict = executor._run_benchmark_if_required(
            contract=contract,
            receipt_id="rcpt-1",
            attempt_ctx=ctx,
        )

        assert verdict is None

    def test_contract_with_forbidden_bench_skips_benchmark(self) -> None:
        """A contract with governance_bench=forbidden and performance_bench=forbidden
        returns None (benchmark not applicable)."""
        mock_store = MagicMock()
        executor = _make_executor(mock_store)
        contract = _make_contract_record(
            verification_requirements={
                "functional": "required",
                "governance_bench": "forbidden",
                "performance_bench": "forbidden",
                "benchmark_profile": "none",
            },
        )
        ctx = _make_attempt_ctx()

        verdict = executor._run_benchmark_if_required(
            contract=contract,
            receipt_id="rcpt-1",
            attempt_ctx=ctx,
        )

        assert verdict is None

    def test_low_risk_contract_with_benchmark_raises_forbidden(self) -> None:
        """A low-risk contract requesting benchmark raises BenchmarkForbiddenError.

        When no explicit benchmark_profile is named, the routing service
        falls through to route_task() which raises for low-risk bands.
        """
        mock_store = MagicMock()
        executor = _make_executor(mock_store)
        contract = _make_contract_record(
            risk_budget={"risk_level": "low"},
            verification_requirements={
                "functional": "required",
                "governance_bench": "required",
                "performance_bench": "forbidden",
                # No explicit benchmark_profile — forces route_task path
                # which raises BenchmarkForbiddenError for low risk.
            },
        )
        ctx = _make_attempt_ctx()

        with pytest.raises(BenchmarkForbiddenError):
            executor._run_benchmark_if_required(
                contract=contract,
                receipt_id="rcpt-1",
                attempt_ctx=ctx,
            )

    def test_none_contract_returns_none(self) -> None:
        """When contract is None, no benchmark is run."""
        mock_store = MagicMock()
        executor = _make_executor(mock_store)
        ctx = _make_attempt_ctx()

        verdict = executor._run_benchmark_if_required(
            contract=None,
            receipt_id="rcpt-1",
            attempt_ctx=ctx,
        )

        assert verdict is None

    def test_verdict_format_matches_reconciliation_schema(self) -> None:
        """The verdict produced by _run_benchmark_if_required can be
        formatted into a valid reconciliation payload."""
        mock_store = MagicMock()
        executor = _make_executor(mock_store)
        contract = _make_contract_record()
        ctx = _make_attempt_ctx()

        verdict = executor._run_benchmark_if_required(
            contract=contract,
            receipt_id="rcpt-1",
            attempt_ctx=ctx,
        )
        assert verdict is not None

        routing = BenchmarkRoutingService()
        recon_data = routing.format_verdict_for_reconciliation(verdict)

        assert recon_data["reconciliation_only"] is True
        assert recon_data["benchmark_passed"] is True
        assert recon_data["benchmark_result_class"] == "satisfied"
        assert recon_data["benchmark_profile_id"] == "trustloop_governance"

    def test_runtime_perf_contract_triggers_runtime_benchmark(self) -> None:
        """A contract with runtime_perf task_family routes to
        the runtime_perf benchmark profile."""
        mock_store = MagicMock()
        executor = _make_executor(mock_store)
        contract = _make_contract_record(
            task_family="runtime_perf",
            expected_effects=["path:src/hermit/runtime/control/runner/runner.py"],
            verification_requirements={
                "functional": "required",
                "governance_bench": "forbidden",
                "performance_bench": "required",
                "benchmark_profile": "runtime_perf",
            },
        )
        ctx = _make_attempt_ctx()

        verdict = executor._run_benchmark_if_required(
            contract=contract,
            receipt_id="rcpt-perf",
            attempt_ctx=ctx,
        )

        assert verdict is not None
        assert verdict.profile_id == "runtime_perf"
        assert verdict.consumed is True
        assert verdict.consumed_by == "rcpt-perf"
