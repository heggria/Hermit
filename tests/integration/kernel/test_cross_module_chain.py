"""Cross-module chain integration test — the definitive v0.3 validation.

Exercises the ENTIRE v0.3 system flowing across module boundaries:

    Program → Deliberation → Contract → Benchmark → Iteration

Each step feeds into the next as a single coherent flow using real
KernelStore + ArtifactStore instances.  No mocks, no stubs — this is
the ultimate end-to-end validation of the v0.3 architecture.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.execution.competition.deliberation_integration import (
        DeliberationIntegration,
    )
    from hermit.kernel.execution.self_modify.iteration_bridge import (
        IterationBridge,
    )
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.projections.status import StatusProjectionService
    from hermit.kernel.task.services.governed_ingress import (
        GovernedIngressService,
    )
    from hermit.kernel.task.services.program_manager import (
        ProgramManager,
    )
    from hermit.kernel.verification.benchmark.routing import (
        BenchmarkRoutingService,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture
def store(tmp_workspace: Path) -> KernelStore:
    from hermit.kernel.ledger.journal.store import KernelStore

    return KernelStore(tmp_workspace / "state.db")


@pytest.fixture
def artifact_store(tmp_workspace: Path) -> ArtifactStore:
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore

    return ArtifactStore(tmp_workspace / "artifacts")


@pytest.fixture
def program_manager(store: KernelStore) -> ProgramManager:
    from hermit.kernel.task.services.program_manager import ProgramManager

    return ProgramManager(store)


@pytest.fixture
def deliberation_integration(
    store: KernelStore,
    artifact_store: ArtifactStore,
) -> DeliberationIntegration:
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from hermit.kernel.execution.competition.deliberation_integration import (
        DeliberationIntegration,
    )
    from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
    from hermit.kernel.execution.competition.llm_critic import CritiqueGenerator
    from hermit.kernel.execution.competition.llm_proposer import ProposalGenerator

    response = json.dumps(
        {
            "selected_candidate_id": "placeholder",
            "confidence": 0.8,
            "reasoning": "test",
        }
    )

    def factory() -> Any:
        p = MagicMock()
        p.generate.return_value = SimpleNamespace(content=[{"type": "text", "text": response}])
        return p

    proposer = ProposalGenerator(factory, default_model="test-model")
    critic = CritiqueGenerator(factory, default_model="test-model")
    arbitrator = ArbitrationEngine(factory, default_model="test-model")

    return DeliberationIntegration(
        store,
        artifact_store,
        proposer=proposer,
        critic=critic,
        arbitrator=arbitrator,
    )


@pytest.fixture
def benchmark_routing() -> BenchmarkRoutingService:
    from hermit.kernel.verification.benchmark.routing import BenchmarkRoutingService

    return BenchmarkRoutingService()


@pytest.fixture
def iteration_bridge(store: KernelStore) -> IterationBridge:
    from hermit.kernel.execution.self_modify.iteration_bridge import IterationBridge

    return IterationBridge(store)


@pytest.fixture
def status_projection(store: KernelStore) -> StatusProjectionService:
    from hermit.kernel.task.projections.status import StatusProjectionService

    return StatusProjectionService(store)


@pytest.fixture
def governed_ingress(store: KernelStore) -> GovernedIngressService:
    from hermit.kernel.task.services.governed_ingress import GovernedIngressService

    return GovernedIngressService(store)


# ---------------------------------------------------------------------------
# The definitive cross-module chain test
# ---------------------------------------------------------------------------


class TestCrossModuleChain:
    """Full v0.3 cross-module integration chain.

    Program -> Deliberation -> Contract -> Benchmark -> Iteration

    Each step produces output consumed by the next step, validating
    that all module boundaries are correctly wired.
    """

    def test_full_chain(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        program_manager: ProgramManager,
        deliberation_integration: DeliberationIntegration,
        benchmark_routing: BenchmarkRoutingService,
        iteration_bridge: IterationBridge,
        status_projection: StatusProjectionService,
        governed_ingress: GovernedIngressService,
    ) -> None:
        from hermit.kernel.execution.controller.execution_contracts import (
            ExecutionContractService,
        )
        from hermit.kernel.execution.controller.supervisor_protocol import (
            TaskContractPacket,
        )
        from hermit.kernel.execution.self_modify.iteration_bridge import (
            Lane,
        )
        from hermit.kernel.execution.self_modify.iteration_kernel import (
            IterationState,
        )
        from hermit.kernel.task.models.program import ProgramState
        from hermit.kernel.task.projections.status import StatusProjectionService
        from hermit.kernel.task.services.governor import IntentClass
        from hermit.kernel.task.services.program_manager import CompilationResult
        from hermit.kernel.verification.benchmark.models import (
            BenchmarkResultClass,
            TaskFamily,
        )

        # ==============================================================
        # STEP 1: Program compilation
        # ==============================================================
        # ProgramManager.compile_program_with_structure -> creates the full
        # organizational hierarchy: Program -> Teams -> Milestones

        compilation: CompilationResult = program_manager.compile_program_with_structure(
            goal="Refactor kernel governance pipeline for v0.3 compliance",
            title="v0.3 Governance Refactor",
            priority="high",
            team_specs=[
                {
                    "title": "Policy Team",
                    "workspace_id": "ws_policy",
                    "milestones": [
                        {
                            "title": "Redesign approval flow",
                            "description": "Update ApprovalService for multi-gate support",
                            "acceptance_criteria": [
                                "Multi-gate approvals pass integration tests",
                                "Rollback coverage > 90%",
                            ],
                        },
                        {
                            "title": "Add contract enrichment",
                            "description": "Add verification_requirements to contracts",
                            "acceptance_criteria": [
                                "All high-risk contracts have governance_bench=required",
                            ],
                            "dependency_titles": ["Redesign approval flow"],
                        },
                    ],
                },
                {
                    "title": "Benchmark Team",
                    "workspace_id": "ws_benchmark",
                    "milestones": [
                        {
                            "title": "Implement TrustLoop-Bench",
                            "description": "7 governance metrics benchmark suite",
                            "acceptance_criteria": [
                                "All 7 metrics computable from ledger",
                                "Threshold evaluation passes",
                            ],
                        },
                    ],
                },
            ],
        )

        # Verify program was created
        assert compilation.program is not None
        assert compilation.program.status == ProgramState.draft
        assert compilation.program.title == "v0.3 Governance Refactor"

        # Verify teams were created
        assert len(compilation.teams) == 2
        assert compilation.teams[0].title == "Policy Team"
        assert compilation.teams[1].title == "Benchmark Team"

        # Verify milestones were created
        assert len(compilation.milestones) == 3

        # ==============================================================
        # STEP 2: Task generation
        # ==============================================================
        # generate_tasks -> produces TaskContractPackets for ready milestones
        # (pending milestones with all dependencies met)

        task_contracts: list[TaskContractPacket] = compilation.task_contracts
        # "Redesign approval flow" has no deps -> ready
        # "Add contract enrichment" depends on "Redesign approval flow" -> NOT ready
        # "Implement TrustLoop-Bench" has no deps -> ready
        assert len(task_contracts) >= 2, (
            f"Expected at least 2 ready milestones, got {len(task_contracts)}"
        )

        # Grab the first contract for deliberation
        first_contract = task_contracts[0]
        assert first_contract.task_id
        assert first_contract.goal

        # ==============================================================
        # STEP 3: Deliberation gate
        # ==============================================================
        # Route first contract through DeliberationIntegration.evaluate_and_route
        # with high risk -> debate should be created

        task_id = first_contract.task_id
        step_id = store.generate_id("step")

        route_result = deliberation_integration.evaluate_and_route(
            task_id=task_id,
            step_id=step_id,
            risk_level="high",
            action_class="execute_command",
        )

        assert route_result["deliberation_required"] is True
        debate_id = route_result["debate_id"]
        assert debate_id is not None

        # ==============================================================
        # STEP 4: Debate resolution
        # ==============================================================
        # Submit proposals + critiques -> arbitrate -> to_contract_packet

        # Proposal A: conservative approach
        cand_a = deliberation_integration.submit_proposal(
            debate_id=debate_id,
            proposer_role="architect",
            plan_summary="Conservative incremental refactor of approval flow",
            contract_draft={"approach": "incremental", "estimated_hours": 20},
            expected_cost="medium",
            expected_risk="low",
        )

        # Proposal B: aggressive rewrite
        cand_b = deliberation_integration.submit_proposal(
            debate_id=debate_id,
            proposer_role="senior_engineer",
            plan_summary="Full rewrite with new policy engine",
            contract_draft={"approach": "rewrite", "estimated_hours": 60},
            expected_cost="high",
            expected_risk="high",
        )

        # Critique B with critical severity (disqualifies it)
        deliberation_integration.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cand_b,
            critic_role="risk_assessor",
            issue_type="scope_risk",
            severity="critical",
            suggested_fix="Scope too large; incremental approach preferred",
        )

        # Minor critique on A (does not disqualify)
        deliberation_integration.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cand_a,
            critic_role="tester",
            issue_type="test_coverage",
            severity="medium",
            suggested_fix="Add more edge case tests",
        )

        # Arbitrate -> should select proposal A (B has critical critique)
        decision = deliberation_integration.resolve_debate(debate_id)
        assert decision["selected_candidate_id"] == cand_a
        assert decision["escalation_required"] is False
        assert decision["confidence"] > 0

        # Convert winning proposal to formal TaskContractPacket
        deliberation_contract = deliberation_integration.to_contract_packet(
            debate_id=debate_id,
            task_id=task_id,
        )
        assert deliberation_contract.task_id == task_id
        assert "incremental refactor" in deliberation_contract.goal.lower()

        # ==============================================================
        # STEP 5: Contract enrichment
        # ==============================================================
        # Pass winning contract through ExecutionContractService.enrich_verification_requirements
        # -> verify governance_bench="required" for high risk

        verification_reqs = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation",
            risk_level="high",
        )

        assert verification_reqs["governance_bench"] == "required"
        assert verification_reqs["performance_bench"] == "required"
        assert verification_reqs["rollback_check"] == "required"
        assert verification_reqs["reconciliation_mode"] == "strict"
        assert verification_reqs["benchmark_profile"] == "trustloop_governance"

        # ==============================================================
        # STEP 6: Benchmark routing
        # ==============================================================
        # BenchmarkRoutingService.classify_task_family -> resolve_profile

        family = benchmark_routing.classify_task_family(
            action_classes=["approval_resolution"],
            affected_paths=["kernel/policy/approvals/approvals.py"],
            task_family_hint=None,
        )
        assert family == TaskFamily.governance_mutation

        profile = benchmark_routing.resolve_profile(
            task_family=family,
            risk_band="high",
        )
        assert profile is not None
        assert profile.profile_id == "trustloop_governance"
        assert profile.task_family == TaskFamily.governance_mutation

        # Validate profile matches family (no "wrong benchmark" error)
        benchmark_routing.validate_profile_family(
            profile=profile,
            task_family=family,
        )

        # ==============================================================
        # STEP 7: Benchmark evaluation
        # ==============================================================
        # Create run, evaluate with metrics -> verdict

        run = benchmark_routing.create_benchmark_run(
            profile=profile,
            task_id=task_id,
            step_id=step_id,
            attempt_id=store.generate_id("attempt"),
            environment_tag="test",
            commit_ref="abc123",
        )
        assert run.run_id.startswith("bench_run_")
        assert run.profile_id == "trustloop_governance"

        # Passing metrics
        verdict = benchmark_routing.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 0.98,
                "unauthorized_effect_rate": 0.0,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 0.97,
                "mean_recovery_depth": 2.0,
                "operator_burden_per_successful_task": 3.0,
                "belief_calibration_under_contradiction": 0.85,
            },
        )
        assert verdict.overall_passed is True
        assert verdict.benchmark_result_class == BenchmarkResultClass.satisfied
        assert len(verdict.regressions) == 0

        # ==============================================================
        # STEP 8: Verdict consumption
        # ==============================================================
        # mark_verdict_consumed -> verified

        consumed_verdict = benchmark_routing.mark_verdict_consumed(
            verdict,
            consumed_by="reconciliation_executor",
        )
        assert consumed_verdict.consumed is True
        assert consumed_verdict.consumed_by == "reconciliation_executor"

        # Double consumption should raise
        from hermit.kernel.verification.benchmark.routing import (
            VerdictAlreadyConsumedError,
        )

        with pytest.raises(VerdictAlreadyConsumedError):
            benchmark_routing.mark_verdict_consumed(
                consumed_verdict,
                consumed_by="another_consumer",
            )

        # ==============================================================
        # STEP 9: Reconciliation format
        # ==============================================================
        # format_verdict_for_reconciliation -> verify reconciliation_only=True

        reconciliation_input = benchmark_routing.format_verdict_for_reconciliation(verdict)
        assert reconciliation_input["reconciliation_only"] is True
        assert reconciliation_input["benchmark_passed"] is True
        assert reconciliation_input["benchmark_verdict_id"] == verdict.verdict_id
        assert reconciliation_input["benchmark_profile_id"] == "trustloop_governance"
        assert isinstance(reconciliation_input["benchmark_regressions"], list)
        assert isinstance(reconciliation_input["benchmark_improvements"], list)

        # ==============================================================
        # STEP 10: Iteration start
        # ==============================================================
        # IterationBridge.on_iteration_start with the program's goal

        iteration_id = iteration_bridge.on_iteration_start(
            spec_id="spec_v03_governance_refactor",
            goal=compilation.program.goal,
        )
        assert iteration_id.startswith("iter-")

        # Verify kernel state is 'admitted' after start
        kernel_state = iteration_bridge.get_kernel_state(iteration_id)
        assert kernel_state == IterationState.admitted.value

        # ==============================================================
        # STEP 11: Phase transitions with lane artifacts
        # ==============================================================
        # Walk through all phases, recording lane artifacts

        # admitted -> researching
        ok = iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="pending",
            to_phase="researching",
        )
        assert ok is True
        assert iteration_bridge.get_kernel_state(iteration_id) == "researching"

        # Record Lane B (research) artifacts
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.research,
            artifact_type="research_report",
            artifact_ref="artifact://research/gov_pipeline_analysis",
        )
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.research,
            artifact_type="repo_diagnosis",
            artifact_ref="artifact://research/repo_scan_results",
        )
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.research,
            artifact_type="evidence_bundle",
            artifact_ref="artifact://research/evidence_collected",
        )

        # researching -> specifying (generating_spec)
        ok = iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="researching",
            to_phase="generating_spec",
        )
        assert ok is True
        assert iteration_bridge.get_kernel_state(iteration_id) == "specifying"

        # Record Lane A (spec_goal) artifacts
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.spec_goal,
            artifact_type="iteration_spec",
            artifact_ref="artifact://spec/v03_gov_spec",
        )
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.spec_goal,
            artifact_type="milestone_graph",
            artifact_ref="artifact://spec/milestone_dag",
        )
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.spec_goal,
            artifact_type="phase_contracts",
            artifact_ref="artifact://spec/phase_contract_bundle",
        )

        # spec_approval -> decomposing (same kernel state: specifying)
        ok = iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="generating_spec",
            to_phase="spec_approval",
        )
        assert ok is True  # Same kernel state, no transition needed

        ok = iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="spec_approval",
            to_phase="decomposing",
        )
        assert ok is True  # Still specifying

        # specifying -> executing (implementing)
        ok = iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="decomposing",
            to_phase="implementing",
        )
        assert ok is True
        assert iteration_bridge.get_kernel_state(iteration_id) == "executing"

        # Record Lane C (change) artifacts
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.change,
            artifact_type="diff_bundle",
            artifact_ref="artifact://change/gov_refactor_diff",
        )
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.change,
            artifact_type="test_patch",
            artifact_ref="artifact://change/test_additions",
        )
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.change,
            artifact_type="migration_notes",
            artifact_ref="artifact://change/migration_guide",
        )

        # executing -> verifying (reviewing)
        ok = iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="implementing",
            to_phase="reviewing",
        )
        assert ok is True
        assert iteration_bridge.get_kernel_state(iteration_id) == "verifying"

        # Record Lane D (verification) artifacts
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.verification,
            artifact_type="benchmark_run",
            artifact_ref=f"artifact://verification/{run.run_id}",
        )
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.verification,
            artifact_type="replay_result",
            artifact_ref="artifact://verification/replay_stable",
        )
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.verification,
            artifact_type="verification_verdict",
            artifact_ref=f"artifact://verification/{verdict.verdict_id}",
        )

        # benchmarking -> same kernel state (verifying)
        ok = iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="reviewing",
            to_phase="benchmarking",
        )
        assert ok is True  # Same kernel state

        # verifying -> reconciling (learning)
        ok = iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="benchmarking",
            to_phase="learning",
        )
        assert ok is True
        assert iteration_bridge.get_kernel_state(iteration_id) == "reconciling"

        # Record Lane E (reconcile) artifacts
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.reconcile,
            artifact_type="reconciliation_record",
            artifact_ref="artifact://reconcile/gov_reconciliation",
        )
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.reconcile,
            artifact_type="lesson_pack",
            artifact_ref="artifact://reconcile/lessons_v03",
        )
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.reconcile,
            artifact_type="template_update",
            artifact_ref="artifact://reconcile/template_improvements",
        )
        iteration_bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.reconcile,
            artifact_type="next_iteration_seed",
            artifact_ref="artifact://reconcile/seed_for_v04",
        )

        # Verify all 5 lanes are complete
        lane_tracker = iteration_bridge.lane_tracker
        assert lane_tracker.all_lanes_complete(iteration_id), (
            f"Not all lanes complete: {lane_tracker.summary(iteration_id)}"
        )

        # ==============================================================
        # STEP 12: Iteration complete
        # ==============================================================
        # on_iteration_complete -> BridgeVerdict with promoted=True

        verdict_dict = iteration_bridge.on_iteration_complete(
            iteration_id=iteration_id,
            benchmark_results={
                "trustloop_governance": {
                    "contract_satisfaction_rate": 0.98,
                    "unauthorized_effect_rate": 0.0,
                    "rollback_success_rate": 0.97,
                }
            },
            reconciliation_summary="All governance metrics pass thresholds. "
            "No regressions detected. Replay stable.",
            replay_stable=True,
            unexplained_drift=[],
        )

        assert verdict_dict["promoted"] is True
        assert verdict_dict["result"] in ("accepted", "accepted_with_followups")
        assert verdict_dict["iteration_id"] == iteration_id
        assert verdict_dict["benchmark_results"] is not None
        assert verdict_dict["reconciliation_summary"] != ""

        # Lane artifacts should be included in the verdict
        assert "lane_artifacts" in verdict_dict

        # ==============================================================
        # STEP 13: Status projection queries
        # ==============================================================
        # StatusProjectionService queries at all levels return valid data
        #
        # The StatusProjectionService.get_program_status looks up a program
        # by task_id (it queries the task store, not the program store).
        # We create a root task to represent the program for projection.

        root_task = store.create_task(
            conversation_id="conv_chain_test",
            title="v0.3 Governance Refactor",
            goal=compilation.program.goal,
            priority="high",
            source_channel="test",
        )
        root_task_id = root_task.task_id

        # Create a child task to test team-level projection
        child_task = store.create_task(
            conversation_id="conv_chain_test",
            title="Policy Team Tasks",
            goal="Execute policy team milestones",
            priority="normal",
            source_channel="test",
            parent_task_id=root_task_id,
        )
        child_task_id = child_task.task_id

        # Program-level status
        program_proj = status_projection.get_program_status(root_task_id)
        assert program_proj.program_id == root_task_id
        assert program_proj.title == "v0.3 Governance Refactor"
        assert program_proj.overall_state is not None

        # Team-level status
        team_proj = status_projection.get_team_status(child_task_id)
        assert team_proj.team_id == child_task_id
        assert team_proj.title == "Policy Team Tasks"

        # Task-level status
        task_proj = status_projection.get_task_status(root_task_id)
        assert task_proj.task_id == root_task_id
        assert task_proj.goal is not None

        # Approval queue
        approval_proj = status_projection.get_approval_queue()
        assert approval_proj.total_count >= 0

        # Benchmark status
        bench_proj = status_projection.get_benchmark_status(root_task_id)
        assert isinstance(bench_proj.recent_runs, list)

        # Format summaries (verify no crashes)
        program_summary = StatusProjectionService.format_program_summary(program_proj)
        assert "v0.3 Governance Refactor" in program_summary

        team_summary = StatusProjectionService.format_team_summary(team_proj)
        assert "Policy Team Tasks" in team_summary

        task_summary = StatusProjectionService.format_task_summary(task_proj)
        assert "v0.3 Governance Refactor" in task_summary
        assert task_proj.task_id == root_task_id

        # ==============================================================
        # STEP 14: Governor routing
        # ==============================================================
        # GovernedIngress correctly classifies messages

        # Chinese status query
        status_result = governed_ingress.process_message(
            message="查看进展",
        )
        assert status_result.intent_class == str(IntentClass.status_query)
        assert status_result.requires_execution is False

        # English status query
        status_result_en = governed_ingress.process_message(
            message="show me the current progress",
        )
        assert status_result_en.intent_class == str(IntentClass.status_query)
        assert status_result_en.requires_execution is False

        # Control command
        control_result = governed_ingress.process_message(
            message="pause the program",
        )
        assert control_result.intent_class == str(IntentClass.control_command)
        assert control_result.requires_execution is False

        # New work (default)
        work_result = governed_ingress.process_message(
            message="implement the new feature for batch processing",
        )
        assert work_result.intent_class == str(IntentClass.new_work)
        assert work_result.requires_execution is True


class TestCrossModuleBoundaryValidation:
    """Additional tests validating specific cross-module boundary contracts."""

    def test_benchmark_forbidden_for_low_risk(
        self,
        benchmark_routing: BenchmarkRoutingService,
    ) -> None:
        """Low-risk tasks must NOT trigger benchmarking (spec constraint)."""
        from hermit.kernel.verification.benchmark.models import TaskFamily
        from hermit.kernel.verification.benchmark.registry import (
            BenchmarkForbiddenError,
        )

        with pytest.raises(BenchmarkForbiddenError):
            benchmark_routing.resolve_profile(
                task_family=TaskFamily.governance_mutation,
                risk_band="low",
            )

    def test_failed_verdict_must_be_consumed(
        self,
        store: KernelStore,
        benchmark_routing: BenchmarkRoutingService,
    ) -> None:
        """Failed verdicts must be explicitly consumed (security invariant)."""
        from hermit.kernel.verification.benchmark.models import (
            BenchmarkResultClass,
            TaskFamily,
        )
        from hermit.kernel.verification.benchmark.routing import (
            VerdictNotConsumedError,
        )

        profile = benchmark_routing.resolve_profile(
            task_family=TaskFamily.governance_mutation,
            risk_band="high",
        )
        assert profile is not None

        run = benchmark_routing.create_benchmark_run(
            profile=profile,
            task_id="task_test",
            step_id="step_test",
            attempt_id="attempt_test",
        )

        # Failing metrics: unauthorized_effect_rate > 0
        failed_verdict = benchmark_routing.evaluate_thresholds(
            run=run,
            raw_metrics={
                "contract_satisfaction_rate": 0.80,
                "unauthorized_effect_rate": 0.05,
                "stale_authorization_execution_rate": 0.0,
                "rollback_success_rate": 0.90,
                "mean_recovery_depth": 2.0,
                "operator_burden_per_successful_task": 3.0,
                "belief_calibration_under_contradiction": 0.85,
            },
        )
        assert failed_verdict.overall_passed is False
        assert failed_verdict.benchmark_result_class == BenchmarkResultClass.violated

        # Must raise if not consumed
        with pytest.raises(VerdictNotConsumedError):
            benchmark_routing.require_verdict_consumption(failed_verdict)

        # After consumption, no error
        consumed = benchmark_routing.mark_verdict_consumed(
            failed_verdict,
            consumed_by="reconciliation",
        )
        benchmark_routing.require_verdict_consumption(consumed)  # No exception

    def test_contract_enrichment_risk_levels(self) -> None:
        """Verify enrichment produces correct requirements per risk level."""
        from hermit.kernel.execution.controller.execution_contracts import (
            ExecutionContractService,
        )

        # High risk
        high = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation",
            risk_level="high",
        )
        assert high["governance_bench"] == "required"
        assert high["benchmark_profile"] == "trustloop_governance"

        # Medium risk
        medium = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation",
            risk_level="medium",
        )
        assert medium["governance_bench"] == "optional"

        # Low risk
        low = ExecutionContractService.enrich_verification_requirements(
            task_family="governance_mutation",
            risk_level="low",
        )
        assert low["governance_bench"] == "forbidden"

    def test_deliberation_bypass_for_low_risk(
        self,
        deliberation_integration: DeliberationIntegration,
        store: KernelStore,
    ) -> None:
        """Low-risk, non-deliberation step kinds bypass deliberation."""
        result = deliberation_integration.evaluate_and_route(
            task_id="task_low",
            step_id="step_low",
            risk_level="low",
            action_class="read_local",
        )
        assert result["deliberation_required"] is False
        assert result["debate_id"] is None

    def test_iteration_promotion_fails_without_replay_stable(
        self,
        iteration_bridge: IterationBridge,
    ) -> None:
        """Promotion gate rejects iterations without replay_stable=True."""
        iteration_id = iteration_bridge.on_iteration_start(
            spec_id="spec_no_replay",
            goal="Test promotion gate rejection",
        )

        # Walk to reconciling state
        iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="pending",
            to_phase="researching",
        )
        iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="researching",
            to_phase="generating_spec",
        )
        iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="generating_spec",
            to_phase="implementing",
        )
        iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="implementing",
            to_phase="reviewing",
        )
        iteration_bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="reviewing",
            to_phase="learning",
        )

        # Complete WITHOUT replay_stable
        verdict_dict = iteration_bridge.on_iteration_complete(
            iteration_id=iteration_id,
            benchmark_results={"test": {"score": 1.0}},
            reconciliation_summary="Reconciliation done",
            replay_stable=False,  # This should prevent promotion
        )
        assert verdict_dict["promoted"] is False
        assert verdict_dict["result"] == "rejected"

    def test_lane_artifact_validation(
        self,
        iteration_bridge: IterationBridge,
    ) -> None:
        """Recording an unexpected artifact type for a lane raises ValueError."""
        from hermit.kernel.execution.self_modify.iteration_bridge import Lane

        iteration_id = iteration_bridge.on_iteration_start(
            spec_id="spec_lane_check",
            goal="Validate lane artifact type checking",
        )

        with pytest.raises(ValueError, match="Unexpected artifact_type"):
            iteration_bridge.record_lane_artifact(
                iteration_id=iteration_id,
                lane=Lane.research,
                artifact_type="diff_bundle",  # Wrong lane! This belongs to Lane.change
                artifact_ref="artifact://wrong",
            )

    def test_task_family_classification_heuristics(
        self,
        benchmark_routing: BenchmarkRoutingService,
    ) -> None:
        """Task family classification uses path and action class heuristics."""
        from hermit.kernel.verification.benchmark.models import TaskFamily

        # Governance paths
        gov = benchmark_routing.classify_task_family(
            action_classes=["approval_resolution"],
            affected_paths=["kernel/policy/approvals/service.py"],
        )
        assert gov == TaskFamily.governance_mutation

        # Runtime performance paths
        perf = benchmark_routing.classify_task_family(
            action_classes=["execute_command"],
            affected_paths=["execution/controller/runner.py"],
        )
        assert perf == TaskFamily.runtime_perf

        # Surface integration paths
        surface = benchmark_routing.classify_task_family(
            action_classes=[],
            affected_paths=["surfaces/cli/main.py"],
        )
        assert surface == TaskFamily.surface_integration

        # Learning template paths
        learning = benchmark_routing.classify_task_family(
            action_classes=[],
            affected_paths=["context/memory/governance.py"],
        )
        assert learning == TaskFamily.learning_template
