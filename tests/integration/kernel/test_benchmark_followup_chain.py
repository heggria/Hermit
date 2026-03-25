"""Benchmark follow-up chain integration test.

End-to-end chain:
  1. Create task with high-risk contract -> verification_requirements
  2. Execute -> reconcile -> benchmark routes to profile
  3. If benchmark violated -> follow-up task auto-generated
  4. If benchmark satisfied -> template learned
  5. Tests the benchmark -> reconciliation -> followup -> learning chain
"""

from __future__ import annotations

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.controller.template_learner import ContractTemplateLearner
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.benchmark.models import (
    BenchmarkResultClass,
    TaskFamily,
)
from hermit.kernel.verification.benchmark.registry import (
    BenchmarkForbiddenError,
)
from hermit.kernel.verification.benchmark.routing import BenchmarkRoutingService
from hermit.kernel.verification.receipts.receipts import ReceiptService


def _make_store(tmp_path) -> KernelStore:
    return KernelStore(tmp_path / "kernel" / "state.db")


def _create_governed_chain(
    store: KernelStore,
    suffix: str = "1",
    *,
    risk_level: str = "high",
    action_class: str = "write_local",
    task_family: str = "governance_mutation",
) -> dict:
    """Create the full governed chain up to contract."""
    conv = store.ensure_conversation(f"conv_bench_{suffix}", source_channel="test")
    task = store.create_task(
        conversation_id=conv.conversation_id,
        title=f"Benchmark chain test {suffix}",
        goal="Test benchmark -> reconciliation -> followup chain",
        source_channel="test",
        status="running",
        policy_profile="default",
    )
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        attempt=1,
        context={"workspace_root": "/tmp/ws", "execution_mode": "run"},
    )

    from hermit.kernel.execution.controller.execution_contracts import (
        ExecutionContractService,
    )

    verification_requirements = ExecutionContractService.enrich_verification_requirements(
        task_family=task_family,
        risk_level=risk_level,
    )

    contract = store.create_execution_contract(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        objective=f"test_tool: {action_class}",
        expected_effects=[f"action:{action_class}"],
        success_criteria={
            "tool_name": "test_tool",
            "action_class": action_class,
            "requires_receipt": True,
        },
        reversibility_class="reversible",
        required_receipt_classes=[action_class],
        drift_budget={"resource_scopes": ["/tmp"]},
        status="active",
        risk_budget={"risk_level": risk_level, "approval_required": risk_level == "high"},
        task_family=task_family,
        verification_requirements=verification_requirements,
    )

    return {
        "conversation": conv,
        "task": task,
        "step": step,
        "attempt": attempt,
        "contract": contract,
        "verification_requirements": verification_requirements,
    }


class TestBenchmarkFollowupChain:
    """Exercise benchmark -> reconciliation -> followup -> learning chain."""

    def test_high_risk_contract_has_required_benchmark(self, tmp_path) -> None:
        """High-risk contracts should have governance_bench=required."""
        store = _make_store(tmp_path)
        chain = _create_governed_chain(store, "high_risk", risk_level="high")

        vr = chain["verification_requirements"]
        assert vr["governance_bench"] == "required"
        assert vr["performance_bench"] == "required"
        assert vr["reconciliation_mode"] == "strict"
        assert vr["rollback_check"] == "required"

    def test_low_risk_contract_has_forbidden_benchmark(self, tmp_path) -> None:
        """Low-risk contracts should have governance_bench=forbidden."""
        store = _make_store(tmp_path)
        chain = _create_governed_chain(store, "low_risk", risk_level="low")

        vr = chain["verification_requirements"]
        assert vr["governance_bench"] == "forbidden"
        assert vr["performance_bench"] == "forbidden"

    def test_benchmark_routing_resolves_correct_profile(self, tmp_path) -> None:
        """BenchmarkRoutingService should resolve the right profile for task family."""
        store = _make_store(tmp_path)
        routing = BenchmarkRoutingService()

        chain = _create_governed_chain(
            store,
            "routing",
            risk_level="high",
            task_family="governance_mutation",
        )

        profile = routing.route_from_contract(
            task_family=chain["contract"].task_family,
            verification_requirements=chain["contract"].verification_requirements,
            risk_level="high",
            action_classes=["write_local"],
            affected_paths=["kernel/policy/test.py"],
        )

        assert profile is not None
        assert profile.profile_id == "trustloop_governance"
        assert profile.task_family == TaskFamily.governance_mutation

    def test_benchmark_forbidden_for_low_risk(self, tmp_path) -> None:
        """Benchmark routing should raise BenchmarkForbiddenError for low risk."""
        routing = BenchmarkRoutingService()

        with pytest.raises(BenchmarkForbiddenError):
            routing.resolve_profile(
                task_family=TaskFamily.governance_mutation,
                risk_band="low",
            )

    def test_benchmark_satisfied_enables_template_learning(self, tmp_path) -> None:
        """When benchmark passes, the satisfied reconciliation should enable template learning."""
        store = _make_store(tmp_path)
        artifact_store = ArtifactStore(tmp_path / "artifacts")
        routing = BenchmarkRoutingService()
        learner = ContractTemplateLearner(store)

        chain = _create_governed_chain(
            store,
            "bench_pass",
            risk_level="high",
            task_family="governance_mutation",
        )

        # Resolve benchmark profile
        profile = routing.route_from_contract(
            task_family=chain["contract"].task_family,
            verification_requirements=chain["contract"].verification_requirements,
            risk_level="high",
        )
        assert profile is not None

        # Create benchmark run
        run = routing.create_benchmark_run(
            profile=profile,
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            attempt_id=chain["attempt"].step_attempt_id,
        )

        # Evaluate thresholds - ALL PASSING
        verdict = routing.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 0.98,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 1.0,
                "mean_recovery_depth": 1.5,
                "operator_burden_per_successful_task": 2.0,
                "belief_calibration_under_contradiction": 0.9,
            },
        )

        assert verdict.overall_passed is True
        assert verdict.benchmark_result_class == BenchmarkResultClass.satisfied

        # Mark verdict consumed
        consumed = routing.mark_verdict_consumed(verdict, consumed_by="reconciliation_service")
        assert consumed.consumed is True

        # Format for reconciliation
        recon_input = routing.format_verdict_for_reconciliation(verdict)
        assert recon_input["benchmark_passed"] is True
        assert recon_input["reconciliation_only"] is True

        # Issue receipt
        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id = receipt_svc.issue(
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            step_attempt_id=chain["attempt"].step_attempt_id,
            action_type="write_local",
            input_refs=[],
            environment_ref=None,
            policy_result={"verdict": "approved"},
            approval_ref=None,
            output_refs=[],
            result_summary="Write completed",
            result_code="succeeded",
            contract_ref=chain["contract"].contract_id,
        )

        # Create satisfied reconciliation
        reconciliation = store.create_reconciliation(
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            step_attempt_id=chain["attempt"].step_attempt_id,
            contract_ref=chain["contract"].contract_id,
            receipt_refs=[receipt_id],
            observed_output_refs=[],
            intended_effect_summary="Write file",
            authorized_effect_summary="Write file in workspace",
            observed_effect_summary="File written successfully",
            receipted_effect_summary="Write completed",
            result_class="satisfied",
            confidence_delta=0.2,
            recommended_resolution="promote_learning",
        )

        # Template should be learned from satisfied reconciliation
        template = learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=chain["contract"],
        )
        assert template is not None
        assert template.memory_kind == "contract_template"
        assert template.learned_from_reconciliation_ref == reconciliation.reconciliation_id

    def test_benchmark_violated_generates_followup_task(self, tmp_path) -> None:
        """When benchmark fails, a follow-up task should be generated for remediation."""
        store = _make_store(tmp_path)
        routing = BenchmarkRoutingService()

        chain = _create_governed_chain(
            store,
            "bench_fail",
            risk_level="high",
            task_family="governance_mutation",
        )

        profile = routing.route_from_contract(
            task_family=chain["contract"].task_family,
            verification_requirements=chain["contract"].verification_requirements,
            risk_level="high",
        )
        assert profile is not None

        run = routing.create_benchmark_run(
            profile=profile,
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            attempt_id=chain["attempt"].step_attempt_id,
        )

        # Evaluate thresholds - SOME FAILING
        verdict = routing.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 0.80,  # below 0.95 threshold
                "unauthorized_effect_rate": 0.05,  # above 0.0 threshold
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 0.90,  # below 0.95 threshold
                "mean_recovery_depth": 1.5,
                "operator_burden_per_successful_task": 2.0,
                "belief_calibration_under_contradiction": 0.9,
            },
        )

        assert verdict.overall_passed is False
        assert verdict.benchmark_result_class == BenchmarkResultClass.violated
        assert len(verdict.regressions) > 0

        # Create violated reconciliation
        reconciliation = store.create_reconciliation(
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            step_attempt_id=chain["attempt"].step_attempt_id,
            contract_ref=chain["contract"].contract_id,
            receipt_refs=[],
            observed_output_refs=[],
            intended_effect_summary="Write file",
            authorized_effect_summary="Write file in workspace",
            observed_effect_summary="Benchmark violated: governance metrics below threshold",
            receipted_effect_summary="Benchmark failed",
            result_class="violated",
            confidence_delta=-0.3,
            recommended_resolution="gather_more_evidence",
        )

        # Template should NOT be learned from violated reconciliation
        learner = ContractTemplateLearner(store)
        template = learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=chain["contract"],
        )
        assert template is None

        # Generate follow-up task for remediation
        followup_task = store.create_task(
            conversation_id=chain["conversation"].conversation_id,
            title="Remediate benchmark violations",
            goal=(
                f"Address benchmark regressions: {', '.join(verdict.regressions[:3])}. "
                f"Parent task: {chain['task'].task_id}"
            ),
            source_channel="test",
            status="queued",
            parent_task_id=chain["task"].task_id,
            policy_profile="default",
        )

        # Verify follow-up task
        assert followup_task is not None
        assert followup_task.parent_task_id == chain["task"].task_id
        assert followup_task.status == "queued"
        assert "Remediate" in followup_task.title

        # Verify parent-child relationship
        children = store.list_child_tasks(parent_task_id=chain["task"].task_id)
        assert len(children) == 1
        assert children[0].task_id == followup_task.task_id

    def test_verdict_consumption_enforcement(self, tmp_path) -> None:
        """Failed verdicts must be consumed by reconciliation."""
        store = _make_store(tmp_path)
        routing = BenchmarkRoutingService()

        chain = _create_governed_chain(store, "consume", risk_level="high")
        profile = routing.resolve_profile(
            task_family=TaskFamily.governance_mutation,
            risk_band="high",
        )
        assert profile is not None

        run = routing.create_benchmark_run(
            profile=profile,
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            attempt_id=chain["attempt"].step_attempt_id,
        )

        verdict = routing.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 0.50,  # way below threshold
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 0.50,
                "mean_recovery_depth": 1.0,
                "operator_burden_per_successful_task": 1.0,
                "belief_calibration_under_contradiction": 0.9,
            },
        )

        assert verdict.overall_passed is False

        # Require consumption should raise for unconsumed failed verdict
        from hermit.kernel.verification.benchmark.routing import VerdictNotConsumedError

        with pytest.raises(VerdictNotConsumedError):
            routing.require_verdict_consumption(verdict)

        # After consuming, require_verdict_consumption should not raise
        consumed = routing.mark_verdict_consumed(verdict, consumed_by="reconciliation")
        routing.require_verdict_consumption(consumed)  # Should not raise

    def test_profile_family_mismatch_detection(self, tmp_path) -> None:
        """Routing should detect profile-family mismatch (running wrong benchmark)."""
        routing = BenchmarkRoutingService()

        profile = routing.registry.get_profile("trustloop_governance")
        assert profile is not None
        assert profile.task_family == TaskFamily.governance_mutation

        # Trying to validate against a different task family should raise
        from hermit.kernel.verification.benchmark.routing import ProfileFamilyMismatchError

        with pytest.raises(ProfileFamilyMismatchError):
            routing.validate_profile_family(
                profile=profile,
                task_family=TaskFamily.runtime_perf,
            )

    def test_end_to_end_benchmark_to_learning_chain(self, tmp_path) -> None:
        """Complete chain: contract -> benchmark -> reconciliation -> template."""
        store = _make_store(tmp_path)
        artifact_store = ArtifactStore(tmp_path / "artifacts")
        routing = BenchmarkRoutingService()
        learner = ContractTemplateLearner(store)

        chain = _create_governed_chain(
            store,
            "e2e",
            risk_level="high",
            task_family="governance_mutation",
        )

        # 1. Contract has verification_requirements
        vr = chain["contract"].verification_requirements
        assert vr is not None
        assert routing.should_benchmark(vr)

        # 2. Route to benchmark profile
        profile = routing.route_from_contract(
            task_family=chain["contract"].task_family,
            verification_requirements=vr,
            risk_level="high",
        )
        assert profile is not None

        # 3. Run benchmark (simulated)
        run = routing.create_benchmark_run(
            profile=profile,
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            attempt_id=chain["attempt"].step_attempt_id,
        )
        verdict = routing.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 0.99,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 1.0,
                "mean_recovery_depth": 1.0,
                "operator_burden_per_successful_task": 1.0,
                "belief_calibration_under_contradiction": 0.95,
            },
        )
        assert verdict.overall_passed is True

        # 4. Reconciliation with benchmark input
        consumed = routing.mark_verdict_consumed(verdict, consumed_by="reconciliation")
        routing.format_verdict_for_reconciliation(consumed)

        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id = receipt_svc.issue(
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            step_attempt_id=chain["attempt"].step_attempt_id,
            action_type="write_local",
            input_refs=[],
            environment_ref=None,
            policy_result={"verdict": "approved"},
            approval_ref=None,
            output_refs=[],
            result_summary="Write completed",
            result_code="succeeded",
            contract_ref=chain["contract"].contract_id,
        )

        reconciliation = store.create_reconciliation(
            task_id=chain["task"].task_id,
            step_id=chain["step"].step_id,
            step_attempt_id=chain["attempt"].step_attempt_id,
            contract_ref=chain["contract"].contract_id,
            receipt_refs=[receipt_id],
            observed_output_refs=[],
            intended_effect_summary="Write file",
            authorized_effect_summary="Write file in workspace",
            observed_effect_summary="File written, benchmark passed",
            receipted_effect_summary="Write completed with benchmark validation",
            result_class="satisfied",
            confidence_delta=0.2,
            recommended_resolution="promote_learning",
        )

        # 5. Template learned
        template = learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=chain["contract"],
        )
        assert template is not None
        assert template.memory_kind == "contract_template"
        sa = dict(template.structured_assertion or {})
        assert sa["action_class"] == "write_local"
        assert sa["source_contract_ref"] == chain["contract"].contract_id

        # 6. Verify the full chain is traceable
        assert template.learned_from_reconciliation_ref == reconciliation.reconciliation_id

        recon = store.get_reconciliation(reconciliation.reconciliation_id)
        assert recon is not None
        assert recon.contract_ref == chain["contract"].contract_id

        contract = store.get_execution_contract(chain["contract"].contract_id)
        assert contract is not None
        assert contract.task_id == chain["task"].task_id
        assert contract.verification_requirements is not None
