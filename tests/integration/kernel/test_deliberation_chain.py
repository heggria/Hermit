"""Integration tests for the complete deliberation chain.

Exercises the full pipeline:
  evaluate_and_route → proposals → critiques → arbitrate → contract
  + dispatch-level deliberation gating

Uses real KernelStore (file-backed) and real ArtifactStore (tmp_path).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.competition.deliberation_integration import (
    DeliberationIntegration,
)
from hermit.kernel.execution.controller.supervisor_protocol import TaskContractPacket
from hermit.kernel.ledger.journal.store import KernelStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def stores(tmp_path: Path) -> tuple[KernelStore, ArtifactStore]:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifact_store = ArtifactStore(tmp_path / "kernel" / "artifacts")
    return store, artifact_store


@pytest.fixture()
def integration(stores: tuple[KernelStore, ArtifactStore]) -> DeliberationIntegration:
    store, artifact_store = stores
    return DeliberationIntegration(store, artifact_store)


# ---------------------------------------------------------------------------
# 1. Pre-execution competition — full debate with winner
# ---------------------------------------------------------------------------


class TestPreExecutionCompetition:
    """Full deliberation chain: route → propose → critique → resolve → contract."""

    def test_high_risk_triggers_deliberation(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        result = integration.evaluate_and_route(
            task_id="task_001",
            step_id="step_001",
            risk_band="high",
            step_kind="planning",
        )
        assert result["deliberation_required"] is True
        assert result["debate_id"] is not None
        assert result["debate_id"].startswith("debate_")

    def test_full_competition_planner_a_wins(
        self,
        integration: DeliberationIntegration,
        stores: tuple[KernelStore, ArtifactStore],
    ) -> None:
        store, _artifact_store = stores

        # Step 1: Route — high risk triggers deliberation
        route_result = integration.evaluate_and_route(
            task_id="task_001",
            step_id="step_001",
            risk_band="high",
            step_kind="planning",
        )
        assert route_result["deliberation_required"] is True
        debate_id = route_result["debate_id"]

        # Step 2: Submit two proposals (planner_A and planner_B)
        candidate_a = integration.submit_proposal(
            debate_id=debate_id,
            proposer_role="planner_A",
            plan_summary="Incremental refactoring with backward compatibility",
            contract_draft={"approach": "incremental", "phases": 3},
            expected_cost="low",
            expected_risk="low",
        )
        assert candidate_a.startswith("dlb_cand_")

        candidate_b = integration.submit_proposal(
            debate_id=debate_id,
            proposer_role="planner_B",
            plan_summary="Big-bang rewrite of the module",
            contract_draft={"approach": "rewrite", "phases": 1},
            expected_cost="high",
            expected_risk="high",
        )
        assert candidate_b.startswith("dlb_cand_")
        assert candidate_a != candidate_b

        # Step 3: Verifier submits a critical critique against planner_B
        critique_id = integration.submit_critique(
            debate_id=debate_id,
            target_candidate_id=candidate_b,
            critic_role="verifier",
            issue_type="risk_assessment",
            severity="critical",
            evidence_refs=["evidence_001"],
            suggested_fix="Reduce scope to avoid service outage",
        )
        assert critique_id.startswith("dlb_crit_")

        # Step 4: Submit executor feasibility feedback for planner_A
        feasibility_id = integration.submit_executor_feasibility(
            debate_id=debate_id,
            target_candidate_id=candidate_a,
            executor_role="executor",
            feasibility_assessment="Tooling and workspace ready",
            is_feasible=True,
            estimated_cost="2 hours",
        )
        assert feasibility_id.startswith("dlb_crit_")

        # Step 5: Resolve debate — planner_A should win (B disqualified by critical critique)
        decision = integration.resolve_debate(debate_id)
        assert decision["selected_candidate_id"] == candidate_a
        assert decision["escalation_required"] is False
        assert decision["confidence"] > 0.0
        # B should appear in rejection reasons
        assert any(candidate_b in r for r in decision["rejection_reasons"])

        # Step 6: Convert to TaskContractPacket
        contract = integration.to_contract_packet(
            debate_id=debate_id,
            task_id="task_001",
        )
        assert isinstance(contract, TaskContractPacket)
        assert contract.task_id == "task_001"
        assert contract.goal == "Incremental refactoring with backward compatibility"
        assert contract.risk_band == "low"  # from winner's expected_risk
        assert contract.scope == {"approach": "incremental", "phases": 3}

        # Verify ledger events were recorded
        routed_count = store.count_events_by_type(
            entity_type="deliberation",
            entity_id=debate_id,
            event_type="deliberation.routed",
        )
        assert routed_count == 1

        proposal_count = store.count_events_by_type(
            entity_type="deliberation",
            entity_id=debate_id,
            event_type="deliberation.proposal_submitted",
        )
        assert proposal_count == 2

        critique_count = store.count_events_by_type(
            entity_type="deliberation",
            entity_id=debate_id,
            event_type="deliberation.critique_submitted",
        )
        # 1 verifier critique + 1 executor feasibility (also stored as critique)
        assert critique_count == 2

        resolved_count = store.count_events_by_type(
            entity_type="deliberation",
            entity_id=debate_id,
            event_type="deliberation.resolved",
        )
        assert resolved_count == 1

        contract_emitted_count = store.count_events_by_type(
            entity_type="deliberation",
            entity_id=debate_id,
            event_type="deliberation.contract_emitted",
        )
        assert contract_emitted_count == 1


# ---------------------------------------------------------------------------
# 2. Post-execution adversarial review
# ---------------------------------------------------------------------------


class TestPostExecutionAdversarialReview:
    """Open a post-execution review debate, submit findings, verify summary."""

    def test_post_execution_review_flow(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        # Open a post-execution review
        review_debate_id = integration.open_post_execution_review(
            task_id="task_002",
            decision_point="Verify module rewrite satisfies spec",
        )
        assert review_debate_id.startswith("debate_")

        # Submit an adversarial review
        review_id = integration.submit_post_execution_review(
            debate_id=review_debate_id,
            task_id="task_002",
            reviewer_role="adversarial_reviewer",
            challenge_type="spec_compliance",
            finding="Output format does not match spec section 3.2",
            severity="high",
            evidence_refs=["artifact_ref_output_diff"],
            recommendation="re_execute",
        )
        assert review_id.startswith("dlb_review_")

        # Submit a second review
        review_id_2 = integration.submit_post_execution_review(
            debate_id=review_debate_id,
            task_id="task_002",
            reviewer_role="benchmark_auditor",
            challenge_type="benchmark_interpretation",
            finding="Latency benchmark was run on warm cache only",
            severity="medium",
            recommendation="accept_with_followups",
        )
        assert review_id_2.startswith("dlb_review_")

        # Get debate summary — verify review count is included
        summary = integration.get_debate_summary(review_debate_id)
        assert summary["debate_id"] == review_debate_id
        assert summary["post_execution_review_count"] == 2
        assert summary["proposal_count"] == 0
        assert summary["critique_count"] == 0
        assert summary["trigger"] == "post_execution_review"

        # Verify individual review details in summary
        review_roles = {r["reviewer_role"] for r in summary["post_execution_reviews"]}
        assert "adversarial_reviewer" in review_roles
        assert "benchmark_auditor" in review_roles


# ---------------------------------------------------------------------------
# 3. Low-risk bypass — no deliberation, no artifacts
# ---------------------------------------------------------------------------


class TestLowRiskBypass:
    """Low risk + non-critical step kind should bypass deliberation entirely."""

    def test_low_risk_bypasses_deliberation(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        result = integration.evaluate_and_route(
            task_id="task_003",
            step_id="step_003",
            risk_band="low",
            step_kind="respond",
        )
        assert result["deliberation_required"] is False
        assert result["debate_id"] is None

    def test_low_risk_no_debate_created(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        """Ensure no debate bundle exists after a low-risk bypass."""
        integration.evaluate_and_route(
            task_id="task_004",
            step_id="step_004",
            risk_band="low",
            step_kind="respond",
        )
        # DeliberationService should have no debates tracked
        assert len(integration.deliberation._debates) == 0

    def test_medium_risk_non_critical_step_bypasses(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        """Medium risk with a non-critical step kind (e.g. respond) should also bypass."""
        result = integration.evaluate_and_route(
            task_id="task_005",
            step_id="step_005",
            risk_band="medium",
            step_kind="respond",
        )
        assert result["deliberation_required"] is False
        assert result["debate_id"] is None

    def test_medium_risk_planning_triggers_deliberation(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        """Medium risk + planning step kind should trigger deliberation."""
        result = integration.evaluate_and_route(
            task_id="task_006",
            step_id="step_006",
            risk_band="medium",
            step_kind="planning",
        )
        assert result["deliberation_required"] is True
        assert result["debate_id"] is not None


# ---------------------------------------------------------------------------
# 4. Artifact verification — all debate artifacts persisted in ArtifactStore
# ---------------------------------------------------------------------------


class TestArtifactVerification:
    """After a full debate, verify ALL proposals, critiques, and decision are
    stored as artifacts in the ArtifactStore and can be read back."""

    def test_artifacts_persisted_after_full_debate(
        self,
        integration: DeliberationIntegration,
        stores: tuple[KernelStore, ArtifactStore],
    ) -> None:
        _store, artifact_store = stores

        # Run a full debate
        route = integration.evaluate_and_route(
            task_id="task_art",
            step_id="step_art",
            risk_band="high",
            step_kind="patch",
        )
        debate_id = route["debate_id"]

        cid_a = integration.submit_proposal(
            debate_id=debate_id,
            proposer_role="planner_alpha",
            plan_summary="Safe patching approach",
            contract_draft={"files": ["a.py"]},
            expected_cost="low",
            expected_risk="low",
        )

        cid_b = integration.submit_proposal(
            debate_id=debate_id,
            proposer_role="planner_beta",
            plan_summary="Aggressive patching",
            contract_draft={"files": ["a.py", "b.py", "c.py"]},
            expected_cost="medium",
            expected_risk="high",
        )

        integration.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cid_b,
            critic_role="security_reviewer",
            issue_type="security_risk",
            severity="critical",
            suggested_fix="Reduce file scope",
        )

        integration.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cid_a,
            critic_role="performance_reviewer",
            issue_type="performance",
            severity="low",
            suggested_fix="Consider caching",
        )

        integration.resolve_debate(debate_id)

        # Now scan artifact store for all persisted artifacts
        artifact_files = list(artifact_store.root_dir.rglob("*.json"))
        assert len(artifact_files) >= 5, (
            f"Expected at least 5 artifacts (2 proposals + 2 critiques + 1 bundle + 1 decision), "
            f"found {len(artifact_files)}"
        )

        # Read and classify artifacts by type
        artifact_types: dict[str, int] = {}
        for f in artifact_files:
            content = json.loads(f.read_text(encoding="utf-8"))
            atype = content.get("artifact_type", "unknown")
            artifact_types[atype] = artifact_types.get(atype, 0) + 1

        assert artifact_types.get("deliberation_proposal", 0) == 2
        assert artifact_types.get("deliberation_critique", 0) == 2
        assert artifact_types.get("deliberation_bundle", 0) == 1
        assert artifact_types.get("arbitration_decision", 0) == 1

        # Verify bundle artifact contains the full debate record
        bundle_files = [
            f
            for f in artifact_files
            if json.loads(f.read_text(encoding="utf-8")).get("artifact_type")
            == "deliberation_bundle"
        ]
        assert len(bundle_files) == 1
        bundle_content = json.loads(bundle_files[0].read_text(encoding="utf-8"))
        assert bundle_content["debate_id"] == debate_id
        assert len(bundle_content["proposals"]) == 2
        assert len(bundle_content["critiques"]) == 2
        assert bundle_content["decision"]["selected_candidate_id"] == cid_a
        assert bundle_content["decision"]["escalation_required"] is False

        # Verify decision artifact
        decision_files = [
            f
            for f in artifact_files
            if json.loads(f.read_text(encoding="utf-8")).get("artifact_type")
            == "arbitration_decision"
        ]
        assert len(decision_files) == 1
        decision_content = json.loads(decision_files[0].read_text(encoding="utf-8"))
        assert decision_content["debate_id"] == debate_id
        assert decision_content["selected_candidate_id"] == cid_a


# ---------------------------------------------------------------------------
# 5. Escalation — all candidates critically critiqued
# ---------------------------------------------------------------------------


class TestEscalation:
    """When ALL candidates have critical critiques, arbitration should escalate."""

    def test_all_critically_critiqued_triggers_escalation(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        route = integration.evaluate_and_route(
            task_id="task_esc",
            step_id="step_esc",
            risk_band="critical",
            step_kind="deploy",
        )
        debate_id = route["debate_id"]
        assert route["deliberation_required"] is True

        # Submit two proposals
        cid_x = integration.submit_proposal(
            debate_id=debate_id,
            proposer_role="planner_X",
            plan_summary="Deploy via blue-green",
            contract_draft={"strategy": "blue_green"},
            expected_cost="medium",
            expected_risk="medium",
        )

        cid_y = integration.submit_proposal(
            debate_id=debate_id,
            proposer_role="planner_Y",
            plan_summary="Deploy via canary",
            contract_draft={"strategy": "canary"},
            expected_cost="medium",
            expected_risk="medium",
        )

        # Critically critique BOTH candidates
        integration.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cid_x,
            critic_role="ops_reviewer",
            issue_type="operational_risk",
            severity="critical",
            suggested_fix="Blue-green requires double capacity we don't have",
        )

        integration.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cid_y,
            critic_role="ops_reviewer",
            issue_type="operational_risk",
            severity="critical",
            suggested_fix="Canary rollback time exceeds SLA budget",
        )

        # Resolve — should escalate
        decision = integration.resolve_debate(debate_id)
        assert decision["escalation_required"] is True
        assert decision["selected_candidate_id"] is None
        assert decision["confidence"] == 0.0
        assert len(decision["rejection_reasons"]) == 2

        # Attempting to convert to contract should raise
        with pytest.raises(ValueError, match="requires escalation"):
            integration.to_contract_packet(
                debate_id=debate_id,
                task_id="task_esc",
            )

    def test_single_candidate_critically_critiqued_escalation(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        """Single proposal, critically critiqued => escalation (no eligible candidates)."""
        route = integration.evaluate_and_route(
            task_id="task_single_esc",
            step_id="step_single_esc",
            risk_band="high",
            step_kind="rollback",
        )
        debate_id = route["debate_id"]

        cid_only = integration.submit_proposal(
            debate_id=debate_id,
            proposer_role="planner_sole",
            plan_summary="Emergency rollback",
            contract_draft={"action": "rollback"},
            expected_cost="low",
            expected_risk="high",
        )

        integration.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cid_only,
            critic_role="safety_reviewer",
            issue_type="data_loss_risk",
            severity="critical",
            evidence_refs=["evidence_data_dependency"],
        )

        decision = integration.resolve_debate(debate_id)
        assert decision["escalation_required"] is True
        assert decision["selected_candidate_id"] is None


# ---------------------------------------------------------------------------
# 6. Edge cases and invariants
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Additional edge cases for deliberation chain invariants."""

    def test_debate_not_found_raises(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        """Operations on non-existent debate_id should raise ValueError."""
        with pytest.raises(ValueError, match="Debate not found"):
            integration.submit_proposal(
                debate_id="debate_nonexistent",
                proposer_role="planner",
                plan_summary="test",
                contract_draft={},
                expected_cost="low",
                expected_risk="low",
            )

    def test_debate_summary_for_nonexistent_returns_error(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        summary = integration.get_debate_summary("debate_ghost")
        assert "error" in summary

    def test_no_critiques_full_confidence(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        """Winner with zero critiques should have confidence=1.0."""
        route = integration.evaluate_and_route(
            task_id="task_conf",
            step_id="step_conf",
            risk_band="high",
            step_kind="planning",
        )
        debate_id = route["debate_id"]

        integration.submit_proposal(
            debate_id=debate_id,
            proposer_role="sole_planner",
            plan_summary="Straightforward plan",
            contract_draft={"simple": True},
            expected_cost="low",
            expected_risk="low",
        )

        decision = integration.resolve_debate(debate_id)
        assert decision["confidence"] == 1.0
        assert decision["escalation_required"] is False

    def test_executor_infeasible_feedback_creates_high_severity_critique(
        self,
        integration: DeliberationIntegration,
    ) -> None:
        """Executor marking a candidate as infeasible creates a 'high' severity critique."""
        route = integration.evaluate_and_route(
            task_id="task_feas",
            step_id="step_feas",
            risk_band="high",
            step_kind="planning",
        )
        debate_id = route["debate_id"]

        cid = integration.submit_proposal(
            debate_id=debate_id,
            proposer_role="planner",
            plan_summary="Plan requiring missing tool",
            contract_draft={},
            expected_cost="low",
            expected_risk="low",
        )

        integration.submit_executor_feasibility(
            debate_id=debate_id,
            target_candidate_id=cid,
            executor_role="executor",
            feasibility_assessment="Required tool 'foo' not available",
            is_feasible=False,
            workspace_conflicts=["lock on /workspace/build"],
            tool_chain_issues=["missing: foo"],
        )

        # Verify the critique was recorded with high severity
        bundle = integration.deliberation.get_debate(debate_id)
        assert bundle is not None
        executor_critiques = [c for c in bundle.critiques if c.critic_role == "executor"]
        assert len(executor_critiques) == 1
        assert executor_critiques[0].severity == "high"
        assert executor_critiques[0].issue_type == "feasibility"
        assert "workspace_conflict:lock on /workspace/build" in executor_critiques[0].evidence_refs
        assert "tool_chain_issue:missing: foo" in executor_critiques[0].evidence_refs


# ---------------------------------------------------------------------------
# 7. Dispatch-level deliberation gating
# ---------------------------------------------------------------------------


class TestDispatchDeliberationGating:
    """Verify that KernelDispatchService.check_deliberation_needed correctly
    gates high-risk steps and bypasses low-risk steps at the dispatch layer.

    Uses a real KernelStore with real tasks/steps/attempts to exercise the
    full integration between the dispatch service and the deliberation check.
    """

    def _make_runner(self, store: KernelStore) -> Any:
        """Build a minimal fake runner with a real KernelStore."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        return SimpleNamespace(
            task_controller=SimpleNamespace(store=store),
            process_claimed_attempt=MagicMock(),
        )

    def _create_attempt(
        self,
        store: KernelStore,
        *,
        step_kind: str,
        risk_band: str,
    ) -> tuple[str, str, str]:
        """Create a task + step + step_attempt and return their IDs.

        Returns ``(task_id, step_id, step_attempt_id)``.
        """
        task = store.create_task(
            conversation_id="conv_delib_dispatch",
            title="Deliberation dispatch test",
            goal="Test dispatch gating",
            source_channel="test",
            status="running",
        )
        step = store.create_step(
            task_id=task.task_id,
            kind=step_kind,
            status="running",
        )
        attempt = store.create_step_attempt(
            task_id=task.task_id,
            step_id=step.step_id,
            status="running",
            context={"risk_band": risk_band, "ingress_metadata": {"dispatch_mode": "async"}},
        )
        return task.task_id, step.step_id, attempt.step_attempt_id

    def test_high_risk_step_triggers_deliberation(
        self,
        stores: tuple[KernelStore, ArtifactStore],
    ) -> None:
        """A step with risk_band='high' and a mutation action_class should be gated."""
        store, _artifact_store = stores
        from hermit.kernel.execution.coordination.dispatch import KernelDispatchService

        runner = self._make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, step_id, step_attempt_id = self._create_attempt(
            store,
            step_kind="write_local",
            risk_band="high",
        )

        result = dispatch.check_deliberation_needed(step_attempt_id)

        assert result is True

        # Verify attempt was moved to deliberation_pending
        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "deliberation_pending"
        assert updated_attempt.waiting_reason == "deliberation_required"
        ctx = updated_attempt.context or {}
        assert ctx.get("deliberation_risk_band") == "high"
        assert ctx.get("deliberation_step_kind") == "write_local"

        # Verify step was moved to deliberation_pending
        updated_step = store.get_step(step_id)
        assert updated_step is not None
        assert updated_step.status == "deliberation_pending"

        # Verify ledger event was recorded
        event_count = store.count_events_by_type(
            entity_type="step_attempt",
            entity_id=step_attempt_id,
            event_type="dispatch.deliberation_required",
        )
        assert event_count == 1

    def test_critical_risk_step_triggers_deliberation(
        self,
        stores: tuple[KernelStore, ArtifactStore],
    ) -> None:
        """A step with risk_band='critical' and a mutation action_class should be gated."""
        store, _artifact_store = stores
        from hermit.kernel.execution.coordination.dispatch import KernelDispatchService

        runner = self._make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, _step_id, step_attempt_id = self._create_attempt(
            store,
            step_kind="external_mutation",
            risk_band="critical",
        )

        result = dispatch.check_deliberation_needed(step_attempt_id)
        assert result is True

        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "deliberation_pending"

    def test_low_risk_step_bypasses_deliberation(
        self,
        stores: tuple[KernelStore, ArtifactStore],
    ) -> None:
        """A step with risk_band='low' and a non-critical kind should bypass deliberation."""
        store, _artifact_store = stores
        from hermit.kernel.execution.coordination.dispatch import KernelDispatchService

        runner = self._make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, _step_id, step_attempt_id = self._create_attempt(
            store,
            step_kind="execute",
            risk_band="low",
        )

        result = dispatch.check_deliberation_needed(step_attempt_id)
        assert result is False

        # Verify attempt status was NOT changed
        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "running"

    def test_medium_risk_mutation_triggers_deliberation(
        self,
        stores: tuple[KernelStore, ArtifactStore],
    ) -> None:
        """Medium risk + mutation action_class should trigger deliberation at dispatch level."""
        store, _artifact_store = stores
        from hermit.kernel.execution.coordination.dispatch import KernelDispatchService

        runner = self._make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, _step_id, step_attempt_id = self._create_attempt(
            store,
            step_kind="write_local",
            risk_band="medium",
        )

        result = dispatch.check_deliberation_needed(step_attempt_id)
        assert result is True

        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "deliberation_pending"

    def test_medium_risk_readonly_bypasses_deliberation(
        self,
        stores: tuple[KernelStore, ArtifactStore],
    ) -> None:
        """Medium risk + readonly action_class should bypass deliberation."""
        store, _artifact_store = stores
        from hermit.kernel.execution.coordination.dispatch import KernelDispatchService

        runner = self._make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        _task_id, _step_id, step_attempt_id = self._create_attempt(
            store,
            step_kind="read_local",
            risk_band="medium",
        )

        result = dispatch.check_deliberation_needed(step_attempt_id)
        assert result is False

        updated_attempt = store.get_step_attempt(step_attempt_id)
        assert updated_attempt is not None
        assert updated_attempt.status == "running"

    def test_no_risk_band_in_context_defaults_to_low(
        self,
        stores: tuple[KernelStore, ArtifactStore],
    ) -> None:
        """When no risk_band is set in context, it should default to 'low' and bypass."""
        store, _artifact_store = stores
        from hermit.kernel.execution.coordination.dispatch import KernelDispatchService

        runner = self._make_runner(store)
        dispatch = KernelDispatchService(runner, worker_count=2)

        task = store.create_task(
            conversation_id="conv_no_risk",
            title="No risk band test",
            goal="Test default",
            source_channel="test",
            status="running",
        )
        step = store.create_step(
            task_id=task.task_id,
            kind="execute",
            status="running",
        )
        attempt = store.create_step_attempt(
            task_id=task.task_id,
            step_id=step.step_id,
            status="running",
            context={},
        )

        result = dispatch.check_deliberation_needed(attempt.step_attempt_id)
        assert result is False
