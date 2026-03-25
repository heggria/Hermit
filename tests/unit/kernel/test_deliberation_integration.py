from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.competition.deliberation import (
    DeliberationTrigger,
)
from hermit.kernel.execution.competition.deliberation_integration import (
    DeliberationIntegration,
)
from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
from hermit.kernel.execution.competition.llm_critic import CritiqueGenerator
from hermit.kernel.execution.competition.llm_proposer import ProposalGenerator
from hermit.kernel.ledger.journal.store import KernelStore


def _make_arbitrator() -> ArbitrationEngine:
    """Create an ArbitrationEngine with a mock provider that falls back."""
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

    return ArbitrationEngine(factory, default_model="test-model")


def _make_integration(
    tmp_path: Path,
) -> tuple[DeliberationIntegration, KernelStore, ArtifactStore]:
    store = KernelStore(tmp_path / "state.db")
    artifact_store = ArtifactStore(tmp_path / "artifacts")

    def factory() -> Any:
        return MagicMock()

    proposer = ProposalGenerator(factory, default_model="test-model")
    critic = CritiqueGenerator(factory, default_model="test-model")
    arbitrator = _make_arbitrator()

    svc = DeliberationIntegration(
        store=store,
        artifact_store=artifact_store,
        proposer=proposer,
        critic=critic,
        arbitrator=arbitrator,
    )
    return svc, store, artifact_store


# -- evaluate_and_route -------------------------------------------------------


class TestEvaluateAndRoute:
    def test_low_risk_bypasses_deliberation(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="low",
            action_class="read_local",
        )
        assert result["deliberation_required"] is False
        assert result["debate_id"] is None

    def test_medium_risk_normal_step_bypasses(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="medium",
            action_class="read_local",
        )
        assert result["deliberation_required"] is False

    def test_high_risk_triggers_deliberation(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="high",
            action_class="execute_command",
        )
        assert result["deliberation_required"] is True
        assert result["debate_id"] is not None
        assert result["debate_id"].startswith("debate_")

    def test_critical_risk_triggers_deliberation(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="critical",
            action_class="delegate_execution",
        )
        assert result["deliberation_required"] is True
        assert result["debate_id"] is not None

    def test_medium_risk_planning_triggers_deliberation(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="medium",
            action_class="execute_command",
        )
        assert result["deliberation_required"] is True

    def test_medium_risk_patch_triggers_deliberation(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="medium",
            action_class="patch_file",
        )
        assert result["deliberation_required"] is True

    def test_medium_risk_deploy_triggers_deliberation(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="medium",
            action_class="external_mutation",
        )
        assert result["deliberation_required"] is True

    def test_medium_risk_rollback_triggers_deliberation(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="medium",
            action_class="rollback",
        )
        assert result["deliberation_required"] is True

    def test_routing_appends_event_to_ledger(self, tmp_path: Path) -> None:
        svc, store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="high",
            action_class="patch_file",
        )
        events = store.list_events(event_type="deliberation.routed")
        assert len(events) >= 1
        assert events[0]["entity_id"] == result["debate_id"]

    def test_trigger_mapping_for_patch(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="high",
            action_class="patch_file",
        )
        debate_id = result["debate_id"]
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert bundle.trigger == DeliberationTrigger.high_risk_patch

    def test_trigger_mapping_for_planning(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="high",
            action_class="execute_command",
        )
        debate_id = result["debate_id"]
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert bundle.trigger == DeliberationTrigger.high_risk_planning

    def test_trigger_mapping_falls_back_to_high_risk_planning(
        self,
        tmp_path: Path,
    ) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        # "critical" risk with scheduler_mutation — maps to high_risk_planning
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="critical",
            action_class="scheduler_mutation",
        )
        debate_id = result["debate_id"]
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert bundle.trigger == DeliberationTrigger.high_risk_planning


# -- submit_proposal ----------------------------------------------------------


class TestSubmitProposal:
    def _open_debate(self, svc: DeliberationIntegration) -> str:
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="high",
            action_class="execute_command",
        )
        debate_id = result["debate_id"]
        assert debate_id is not None
        return debate_id

    def test_returns_candidate_id(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id = self._open_debate(svc)
        cand_id = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Refactor auth module",
            contract_draft={"steps": 3},
            expected_cost="low",
            expected_risk="medium",
        )
        assert cand_id.startswith("dlb_cand_")

    def test_proposal_stored_in_debate(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id = self._open_debate(svc)
        cand_id = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="architect",
            plan_summary="Redesign API layer",
            contract_draft={"endpoints": 5},
            expected_cost="high",
            expected_risk="high",
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert len(bundle.proposals) == 1
        assert bundle.proposals[0].candidate_id == cand_id

    def test_proposal_stored_as_artifact(self, tmp_path: Path) -> None:
        svc, _store, arts = _make_integration(tmp_path)
        debate_id = self._open_debate(svc)
        svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Add caching layer",
            contract_draft={"cache_type": "redis"},
            expected_cost="medium",
            expected_risk="low",
        )
        # Verify an artifact file was written in the artifact store
        artifact_files = list(arts.root_dir.rglob("*.json"))
        assert len(artifact_files) >= 1
        content = json.loads(artifact_files[0].read_text())
        assert content["artifact_type"] == "deliberation_proposal"
        assert content["debate_id"] == debate_id
        assert content["proposer_role"] == "engineer"

    def test_multiple_proposals_in_same_debate(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id = self._open_debate(svc)
        c1 = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Plan A",
            contract_draft={},
            expected_cost="low",
            expected_risk="low",
        )
        c2 = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="architect",
            plan_summary="Plan B",
            contract_draft={},
            expected_cost="medium",
            expected_risk="medium",
        )
        assert c1 != c2
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert len(bundle.proposals) == 2

    def test_proposal_on_unknown_debate_raises(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        with pytest.raises(ValueError, match="Debate not found"):
            svc.submit_proposal(
                debate_id="nonexistent",
                proposer_role="engineer",
                plan_summary="Plan",
                contract_draft={},
                expected_cost="low",
                expected_risk="low",
            )

    def test_proposal_event_appended(self, tmp_path: Path) -> None:
        svc, store, _arts = _make_integration(tmp_path)
        debate_id = self._open_debate(svc)
        svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Plan A",
            contract_draft={},
            expected_cost="low",
            expected_risk="low",
        )
        events = store.list_events(event_type="deliberation.proposal_submitted")
        assert len(events) == 1
        assert events[0]["payload"]["proposer_role"] == "engineer"

    def test_expected_reward_passed_through(self, tmp_path: Path) -> None:
        svc, _store, arts = _make_integration(tmp_path)
        debate_id = self._open_debate(svc)
        svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Plan with reward",
            contract_draft={},
            expected_cost="low",
            expected_risk="low",
            expected_reward="high",
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert bundle.proposals[0].expected_reward == "high"

        # Verify artifact also contains expected_reward
        artifact_files = list(arts.root_dir.rglob("*.json"))
        proposal_artifacts = [
            f
            for f in artifact_files
            if json.loads(f.read_text()).get("artifact_type") == "deliberation_proposal"
        ]
        assert len(proposal_artifacts) == 1
        content = json.loads(proposal_artifacts[0].read_text())
        assert content["expected_reward"] == "high"

    def test_expected_reward_defaults_to_empty(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id = self._open_debate(svc)
        svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Plan without reward",
            contract_draft={},
            expected_cost="low",
            expected_risk="low",
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert bundle.proposals[0].expected_reward == ""

    def test_expected_reward_in_summary(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id = self._open_debate(svc)
        svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Plan X",
            contract_draft={},
            expected_cost="low",
            expected_risk="low",
            expected_reward="critical_improvement",
        )
        summary = svc.get_debate_summary(debate_id)
        assert summary["proposals"][0]["expected_reward"] == "critical_improvement"


# -- submit_critique ----------------------------------------------------------


class TestSubmitCritique:
    def _setup_debate_with_proposal(
        self,
        svc: DeliberationIntegration,
    ) -> tuple[str, str]:
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="high",
            action_class="patch_file",
        )
        debate_id = result["debate_id"]
        assert debate_id is not None
        cand_id = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Plan A",
            contract_draft={},
            expected_cost="low",
            expected_risk="low",
        )
        return debate_id, cand_id

    def test_returns_critique_id(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        crit_id = svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            critic_role="reviewer",
            issue_type="correctness",
            severity="medium",
        )
        assert crit_id.startswith("dlb_crit_")

    def test_critique_stored_in_debate(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        crit_id = svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            critic_role="security",
            issue_type="vulnerability",
            severity="critical",
            evidence_refs=["CVE-2024-001"],
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert len(bundle.critiques) == 1
        assert bundle.critiques[0].critique_id == crit_id
        assert bundle.critiques[0].evidence_refs == ["CVE-2024-001"]

    def test_critique_stored_as_artifact(self, tmp_path: Path) -> None:
        svc, _store, arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            critic_role="reviewer",
            issue_type="performance",
            severity="high",
        )
        # Find critique artifact (skip proposal artifact)
        artifact_files = list(arts.root_dir.rglob("*.json"))
        critique_artifacts = [
            f
            for f in artifact_files
            if json.loads(f.read_text()).get("artifact_type") == "deliberation_critique"
        ]
        assert len(critique_artifacts) == 1
        content = json.loads(critique_artifacts[0].read_text())
        assert content["severity"] == "high"
        assert content["issue_type"] == "performance"

    def test_critique_with_no_evidence_refs(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        crit_id = svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            critic_role="reviewer",
            issue_type="style",
            severity="low",
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        matching = [c for c in bundle.critiques if c.critique_id == crit_id]
        assert len(matching) == 1
        assert matching[0].evidence_refs == []

    def test_critique_on_unknown_debate_raises(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        with pytest.raises(ValueError, match="Debate not found"):
            svc.submit_critique(
                debate_id="nonexistent",
                target_candidate_id="c1",
                critic_role="reviewer",
                issue_type="correctness",
                severity="medium",
            )

    def test_critique_event_appended(self, tmp_path: Path) -> None:
        svc, store, _arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            critic_role="reviewer",
            issue_type="correctness",
            severity="critical",
        )
        events = store.list_events(event_type="deliberation.critique_submitted")
        assert len(events) == 1
        assert events[0]["payload"]["severity"] == "critical"

    def test_suggested_fix_passed_through(self, tmp_path: Path) -> None:
        svc, _store, arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            critic_role="reviewer",
            issue_type="correctness",
            severity="medium",
            suggested_fix="Add input validation",
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert bundle.critiques[0].suggested_fix == "Add input validation"

        # Verify artifact also contains suggested_fix
        artifact_files = list(arts.root_dir.rglob("*.json"))
        critique_artifacts = [
            f
            for f in artifact_files
            if json.loads(f.read_text()).get("artifact_type") == "deliberation_critique"
        ]
        assert len(critique_artifacts) == 1
        content = json.loads(critique_artifacts[0].read_text())
        assert content["suggested_fix"] == "Add input validation"

    def test_suggested_fix_defaults_to_empty(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            critic_role="reviewer",
            issue_type="style",
            severity="low",
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert bundle.critiques[0].suggested_fix == ""


# -- resolve_debate -----------------------------------------------------------


class TestResolveDebate:
    def _setup_debate_with_proposals(
        self,
        svc: DeliberationIntegration,
    ) -> tuple[str, str, str]:
        """Create a debate with two proposals, return (debate_id, cand1, cand2)."""
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="high",
            action_class="execute_command",
        )
        debate_id = result["debate_id"]
        assert debate_id is not None
        c1 = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Plan A",
            contract_draft={"steps": 2},
            expected_cost="low",
            expected_risk="low",
        )
        c2 = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="architect",
            plan_summary="Plan B",
            contract_draft={"steps": 4},
            expected_cost="medium",
            expected_risk="medium",
        )
        return debate_id, c1, c2

    def test_resolve_returns_decision_dict(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, c1, _c2 = self._setup_debate_with_proposals(svc)
        decision = svc.resolve_debate(debate_id, task_id="t1")
        assert isinstance(decision, dict)
        assert decision["debate_id"] == debate_id
        assert decision["selected_candidate_id"] == c1
        assert decision["escalation_required"] is False

    def test_resolve_selects_uncritiqued_candidate(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, c1, c2 = self._setup_debate_with_proposals(svc)
        # Add critical critique to c1
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=c1,
            critic_role="security",
            issue_type="vulnerability",
            severity="critical",
        )
        decision = svc.resolve_debate(debate_id, task_id="t1")
        assert decision["selected_candidate_id"] == c2

    def test_resolve_escalates_when_all_critical(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, c1, c2 = self._setup_debate_with_proposals(svc)
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=c1,
            critic_role="reviewer",
            issue_type="fatal",
            severity="critical",
        )
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=c2,
            critic_role="reviewer",
            issue_type="fatal",
            severity="critical",
        )
        decision = svc.resolve_debate(debate_id, task_id="t1")
        assert decision["selected_candidate_id"] is None
        assert decision["escalation_required"] is True

    def test_resolve_stores_bundle_artifact(self, tmp_path: Path) -> None:
        svc, _store, arts = _make_integration(tmp_path)
        debate_id, _c1, _c2 = self._setup_debate_with_proposals(svc)
        svc.resolve_debate(debate_id, task_id="t1")

        artifact_files = list(arts.root_dir.rglob("*.json"))
        bundle_artifacts = [
            f
            for f in artifact_files
            if json.loads(f.read_text()).get("artifact_type") == "deliberation_bundle"
        ]
        assert len(bundle_artifacts) == 1
        content = json.loads(bundle_artifacts[0].read_text())
        assert content["debate_id"] == debate_id
        assert len(content["proposals"]) == 2
        assert "decision" in content

    def test_resolve_stores_decision_artifact(self, tmp_path: Path) -> None:
        svc, _store, arts = _make_integration(tmp_path)
        debate_id, _c1, _c2 = self._setup_debate_with_proposals(svc)
        svc.resolve_debate(debate_id, task_id="t1")

        artifact_files = list(arts.root_dir.rglob("*.json"))
        decision_artifacts = [
            f
            for f in artifact_files
            if json.loads(f.read_text()).get("artifact_type") == "arbitration_decision"
        ]
        assert len(decision_artifacts) == 1
        content = json.loads(decision_artifacts[0].read_text())
        assert content["debate_id"] == debate_id
        assert "decision_id" in content

    def test_resolve_appends_event(self, tmp_path: Path) -> None:
        svc, store, _arts = _make_integration(tmp_path)
        debate_id, _c1, _c2 = self._setup_debate_with_proposals(svc)
        svc.resolve_debate(debate_id, task_id="t1")

        events = store.list_events(event_type="deliberation.resolved")
        assert len(events) == 1
        payload = events[0]["payload"]
        assert "bundle_artifact_ref" in payload
        assert "decision_artifact_ref" in payload
        assert "confidence" in payload

    def test_resolve_unknown_debate_raises(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        with pytest.raises(ValueError, match="Debate not found"):
            svc.resolve_debate("nonexistent")


# -- get_debate_summary -------------------------------------------------------


class TestGetDebateSummary:
    def test_summary_of_empty_debate(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="high",
            action_class="execute_command",
        )
        debate_id = result["debate_id"]
        assert debate_id is not None

        summary = svc.get_debate_summary(debate_id)
        assert summary["debate_id"] == debate_id
        assert summary["proposal_count"] == 0
        assert summary["critique_count"] == 0
        assert summary["critical_critique_count"] == 0
        assert summary["proposals"] == []
        assert summary["critiques"] == []
        assert summary["post_execution_review_count"] == 0
        assert summary["critical_review_count"] == 0
        assert summary["post_execution_reviews"] == []

    def test_summary_with_proposals_and_critiques(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="critical",
            action_class="external_mutation",
        )
        debate_id = result["debate_id"]
        assert debate_id is not None

        cand_id = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Deploy v2",
            contract_draft={"version": "2.0"},
            expected_cost="medium",
            expected_risk="high",
        )
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            critic_role="security",
            issue_type="risk",
            severity="critical",
        )
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            critic_role="ops",
            issue_type="capacity",
            severity="medium",
        )

        summary = svc.get_debate_summary(debate_id)
        assert summary["proposal_count"] == 1
        assert summary["critique_count"] == 2
        assert summary["critical_critique_count"] == 1
        assert len(summary["proposals"]) == 1
        assert summary["proposals"][0]["proposer_role"] == "engineer"
        assert len(summary["critiques"]) == 2

    def test_summary_unknown_debate(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        summary = svc.get_debate_summary("nonexistent")
        assert "error" in summary


# -- Full lifecycle ------------------------------------------------------------


class TestFullLifecycle:
    def test_end_to_end_deliberation(self, tmp_path: Path) -> None:
        """Full flow: route -> propose -> critique -> resolve -> verify artifacts."""
        svc, store, arts = _make_integration(tmp_path)

        # 1. Route
        route_result = svc.evaluate_and_route(
            task_id="task_001",
            step_id="step_001",
            risk_level="high",
            action_class="execute_command",
        )
        assert route_result["deliberation_required"] is True
        debate_id = route_result["debate_id"]

        # 2. Submit proposals (planner and executor participate)
        c1 = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="planner",
            plan_summary="Conservative refactor in 3 steps",
            contract_draft={"steps": 3, "risk": "low"},
            expected_cost="low",
            expected_risk="low",
        )
        c2 = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="executor",
            plan_summary="Aggressive rewrite in 1 step",
            contract_draft={"steps": 1, "risk": "high"},
            expected_cost="high",
            expected_risk="high",
        )

        # 3. Submit critiques (adversarial review)
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=c2,
            critic_role="security_reviewer",
            issue_type="risk_too_high",
            severity="critical",
            evidence_refs=["audit_report_2024"],
        )
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=c1,
            critic_role="performance_reviewer",
            issue_type="slow_rollout",
            severity="low",
        )

        # 4. Check summary before resolution
        summary = svc.get_debate_summary(debate_id)
        assert summary["proposal_count"] == 2
        assert summary["critique_count"] == 2
        assert summary["critical_critique_count"] == 1

        # 5. Resolve
        decision = svc.resolve_debate(debate_id, task_id="t1")
        # c2 should be rejected (critical critique), c1 selected
        assert decision["selected_candidate_id"] == c1
        assert decision["escalation_required"] is False
        assert decision["confidence"] > 0.0

        # 6. Verify artifacts are stored
        all_artifacts = list(arts.root_dir.rglob("*.json"))
        types_found = set()
        for f in all_artifacts:
            content = json.loads(f.read_text())
            if "artifact_type" in content:
                types_found.add(content["artifact_type"])

        assert "deliberation_proposal" in types_found
        assert "deliberation_critique" in types_found
        assert "deliberation_bundle" in types_found
        assert "arbitration_decision" in types_found

        # 7. Verify events in ledger
        all_events = store.list_events(limit=100)
        event_types = [e["event_type"] for e in all_events]
        assert "deliberation.routed" in event_types
        assert "deliberation.proposal_submitted" in event_types
        assert "deliberation.critique_submitted" in event_types
        assert "deliberation.resolved" in event_types

    def test_bypass_does_not_create_artifacts(self, tmp_path: Path) -> None:
        """Low-risk tasks should produce no deliberation artifacts."""
        svc, _store, arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="low",
            action_class="read_local",
        )
        assert result["deliberation_required"] is False
        artifact_files = list(arts.root_dir.rglob("*.json"))
        assert len(artifact_files) == 0


# -- to_contract_packet -------------------------------------------------------


class TestToContractPacket:
    def _setup_resolved_debate(
        self,
        svc: DeliberationIntegration,
    ) -> tuple[str, str, str]:
        """Create and resolve a debate. Returns (debate_id, winner_id, loser_id)."""
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="high",
            action_class="execute_command",
        )
        debate_id = result["debate_id"]
        assert debate_id is not None
        c1 = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Conservative refactor",
            contract_draft={"steps": 3, "scope": "auth"},
            expected_cost="low",
            expected_risk="low",
        )
        c2 = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="architect",
            plan_summary="Full rewrite",
            contract_draft={"steps": 1},
            expected_cost="high",
            expected_risk="high",
        )
        # Make c2 lose by adding a critical critique
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=c2,
            critic_role="reviewer",
            issue_type="risk",
            severity="critical",
        )
        svc.resolve_debate(debate_id, task_id="t1")
        return debate_id, c1, c2

    def test_returns_task_contract_packet(self, tmp_path: Path) -> None:
        from hermit.kernel.execution.controller.supervisor_protocol import (
            TaskContractPacket,
        )

        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, _c1, _c2 = self._setup_resolved_debate(svc)
        contract = svc.to_contract_packet(debate_id=debate_id, task_id="task_42")
        assert isinstance(contract, TaskContractPacket)
        assert contract.task_id == "task_42"

    def test_contract_goal_from_winning_proposal(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, _c1, _c2 = self._setup_resolved_debate(svc)
        contract = svc.to_contract_packet(debate_id=debate_id, task_id="task_42")
        assert contract.goal == "Conservative refactor"

    def test_contract_scope_from_winning_proposal(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, _c1, _c2 = self._setup_resolved_debate(svc)
        contract = svc.to_contract_packet(debate_id=debate_id, task_id="task_42")
        assert contract.scope == {"steps": 3, "scope": "auth"}

    def test_contract_risk_band_from_winning_proposal(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, _c1, _c2 = self._setup_resolved_debate(svc)
        contract = svc.to_contract_packet(debate_id=debate_id, task_id="task_42")
        assert contract.risk_band == "low"

    def test_contract_emits_event(self, tmp_path: Path) -> None:
        svc, store, _arts = _make_integration(tmp_path)
        debate_id, _c1, _c2 = self._setup_resolved_debate(svc)
        svc.to_contract_packet(debate_id=debate_id, task_id="task_42")
        events = store.list_events(event_type="deliberation.contract_emitted")
        assert len(events) == 1
        assert events[0]["payload"]["contract_task_id"] == "task_42"

    def test_contract_raises_on_escalation(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="high",
            action_class="execute_command",
        )
        debate_id = result["debate_id"]
        assert debate_id is not None
        c1 = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="engineer",
            plan_summary="Plan A",
            contract_draft={},
            expected_cost="low",
            expected_risk="low",
        )
        # Make all candidates critically flawed
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=c1,
            critic_role="reviewer",
            issue_type="fatal",
            severity="critical",
        )
        svc.resolve_debate(debate_id, task_id="t1")
        with pytest.raises(ValueError, match="requires escalation"):
            svc.to_contract_packet(debate_id=debate_id, task_id="task_42")

    def test_contract_raises_on_unknown_debate(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        with pytest.raises(ValueError, match="Debate not found"):
            svc.to_contract_packet(debate_id="nonexistent", task_id="task_42")


# -- submit_executor_feasibility ----------------------------------------------


class TestSubmitExecutorFeasibility:
    def _setup_debate_with_proposal(
        self,
        svc: DeliberationIntegration,
    ) -> tuple[str, str]:
        result = svc.evaluate_and_route(
            task_id="t1",
            step_id="s1",
            risk_level="high",
            action_class="patch_file",
        )
        debate_id = result["debate_id"]
        assert debate_id is not None
        cand_id = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="planner",
            plan_summary="Plan A",
            contract_draft={},
            expected_cost="low",
            expected_risk="low",
        )
        return debate_id, cand_id

    def test_feasible_produces_low_severity_critique(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        crit_id = svc.submit_executor_feasibility(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            feasibility_assessment="Looks good",
            is_feasible=True,
        )
        assert crit_id.startswith("dlb_crit_")
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        matching = [c for c in bundle.critiques if c.critique_id == crit_id]
        assert len(matching) == 1
        assert matching[0].severity == "low"
        assert matching[0].issue_type == "feasibility"
        assert matching[0].critic_role == "executor"

    def test_not_feasible_produces_high_severity(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        crit_id = svc.submit_executor_feasibility(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            feasibility_assessment="Cannot execute: missing tool",
            is_feasible=False,
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        matching = [c for c in bundle.critiques if c.critique_id == crit_id]
        assert matching[0].severity == "high"

    def test_workspace_conflicts_in_evidence(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        svc.submit_executor_feasibility(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            workspace_conflicts=["file_a.py", "file_b.py"],
            is_feasible=False,
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        crit = bundle.critiques[-1]
        assert "workspace_conflict:file_a.py" in crit.evidence_refs
        assert "workspace_conflict:file_b.py" in crit.evidence_refs

    def test_tool_chain_issues_in_evidence(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        svc.submit_executor_feasibility(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            tool_chain_issues=["ruff not installed"],
            is_feasible=False,
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        crit = bundle.critiques[-1]
        assert "tool_chain_issue:ruff not installed" in crit.evidence_refs

    def test_estimated_cost_in_suggested_fix(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        svc.submit_executor_feasibility(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            feasibility_assessment="Doable but expensive",
            estimated_cost="$500",
            is_feasible=True,
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        crit = bundle.critiques[-1]
        assert "[estimated_cost=$500]" in crit.suggested_fix

    def test_custom_executor_role(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        svc.submit_executor_feasibility(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            executor_role="sandbox_executor",
            is_feasible=True,
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert bundle.critiques[-1].critic_role == "sandbox_executor"

    def test_stores_as_artifact(self, tmp_path: Path) -> None:
        svc, _store, arts = _make_integration(tmp_path)
        debate_id, cand_id = self._setup_debate_with_proposal(svc)
        svc.submit_executor_feasibility(
            debate_id=debate_id,
            target_candidate_id=cand_id,
            is_feasible=True,
        )
        artifact_files = list(arts.root_dir.rglob("*.json"))
        critique_artifacts = [
            f
            for f in artifact_files
            if json.loads(f.read_text()).get("artifact_type") == "deliberation_critique"
        ]
        assert len(critique_artifacts) >= 1
        content = json.loads(critique_artifacts[0].read_text())
        assert content["issue_type"] == "feasibility"


# -- Post-execution adversarial review ----------------------------------------


class TestPostExecutionReview:
    def _open_review_debate(self, svc: DeliberationIntegration) -> str:
        return svc.open_post_execution_review(
            task_id="task_99",
            decision_point="Verify patch satisfies spec for task_99",
        )

    def test_open_returns_debate_id(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id = self._open_review_debate(svc)
        assert debate_id.startswith("debate_")

    def test_open_creates_debate_with_post_execution_trigger(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id = self._open_review_debate(svc)
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert bundle.trigger == DeliberationTrigger.post_execution_review

    def test_open_appends_event(self, tmp_path: Path) -> None:
        svc, store, _arts = _make_integration(tmp_path)
        debate_id = self._open_review_debate(svc)
        events = store.list_events(event_type="deliberation.post_execution_review_opened")
        assert len(events) == 1
        assert events[0]["entity_id"] == debate_id
        assert events[0]["payload"]["trigger"] == "post_execution_review"

    def test_submit_review_returns_review_id(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id = self._open_review_debate(svc)
        review_id = svc.submit_post_execution_review(
            debate_id=debate_id,
            task_id="task_99",
            reviewer_role="adversarial_reviewer",
            challenge_type="spec_compliance",
            finding="Patch does not handle edge case X",
            severity="high",
        )
        assert review_id.startswith("dlb_review_")

    def test_review_stored_in_debate(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id = self._open_review_debate(svc)
        review_id = svc.submit_post_execution_review(
            debate_id=debate_id,
            task_id="task_99",
            reviewer_role="risk_judge",
            challenge_type="risk_assessment",
            finding="Risk was underestimated",
            severity="critical",
            evidence_refs=["benchmark_run_42"],
            recommendation="re_execute",
        )
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert len(bundle.post_execution_reviews) == 1
        review = bundle.post_execution_reviews[0]
        assert review.review_id == review_id
        assert review.challenge_type == "risk_assessment"
        assert review.severity == "critical"
        assert review.evidence_refs == ["benchmark_run_42"]
        assert review.recommendation == "re_execute"

    def test_review_stored_as_artifact(self, tmp_path: Path) -> None:
        svc, _store, arts = _make_integration(tmp_path)
        debate_id = self._open_review_debate(svc)
        svc.submit_post_execution_review(
            debate_id=debate_id,
            task_id="task_99",
            reviewer_role="spec_checker",
            challenge_type="benchmark_interpretation",
            finding="Benchmark score misleading",
            severity="medium",
        )
        artifact_files = list(arts.root_dir.rglob("*.json"))
        review_artifacts = [
            f
            for f in artifact_files
            if json.loads(f.read_text()).get("artifact_type") == "post_execution_review"
        ]
        assert len(review_artifacts) == 1
        content = json.loads(review_artifacts[0].read_text())
        assert content["challenge_type"] == "benchmark_interpretation"
        assert content["task_id"] == "task_99"

    def test_review_appends_event(self, tmp_path: Path) -> None:
        svc, store, _arts = _make_integration(tmp_path)
        debate_id = self._open_review_debate(svc)
        svc.submit_post_execution_review(
            debate_id=debate_id,
            task_id="task_99",
            reviewer_role="adversarial_reviewer",
            challenge_type="spec_compliance",
            finding="Missing test coverage",
            severity="high",
            recommendation="reject",
        )
        events = store.list_events(event_type="deliberation.post_execution_review_submitted")
        assert len(events) == 1
        payload = events[0]["payload"]
        assert payload["challenge_type"] == "spec_compliance"
        assert payload["severity"] == "high"
        assert payload["recommendation"] == "reject"
        assert "artifact_ref" in payload

    def test_review_on_unknown_debate_raises(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        with pytest.raises(ValueError, match="Debate not found"):
            svc.submit_post_execution_review(
                debate_id="nonexistent",
                task_id="task_99",
                reviewer_role="reviewer",
                challenge_type="spec_compliance",
                finding="Problem found",
                severity="high",
            )

    def test_multiple_reviews_in_same_debate(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id = self._open_review_debate(svc)
        r1 = svc.submit_post_execution_review(
            debate_id=debate_id,
            task_id="task_99",
            reviewer_role="reviewer_a",
            challenge_type="spec_compliance",
            finding="Patch looks good",
            severity="low",
        )
        r2 = svc.submit_post_execution_review(
            debate_id=debate_id,
            task_id="task_99",
            reviewer_role="reviewer_b",
            challenge_type="risk_assessment",
            finding="Edge case risk",
            severity="high",
        )
        assert r1 != r2
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert len(bundle.post_execution_reviews) == 2

    def test_summary_includes_reviews(self, tmp_path: Path) -> None:
        svc, _store, _arts = _make_integration(tmp_path)
        debate_id = self._open_review_debate(svc)
        svc.submit_post_execution_review(
            debate_id=debate_id,
            task_id="task_99",
            reviewer_role="adversarial_reviewer",
            challenge_type="spec_compliance",
            finding="Missing edge case",
            severity="critical",
            recommendation="reject",
        )
        summary = svc.get_debate_summary(debate_id)
        assert summary["post_execution_review_count"] == 1
        assert summary["critical_review_count"] == 1
        assert len(summary["post_execution_reviews"]) == 1
        review_summary = summary["post_execution_reviews"][0]
        assert review_summary["challenge_type"] == "spec_compliance"
        assert review_summary["recommendation"] == "reject"

    def test_bundle_artifact_includes_reviews(self, tmp_path: Path) -> None:
        """When resolving a post-execution review debate, reviews appear in bundle."""
        svc, _store, arts = _make_integration(tmp_path)
        debate_id = self._open_review_debate(svc)
        # Add a proposal so we can resolve
        svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="reviewer",
            plan_summary="Accept with followups",
            contract_draft={"action": "accept_with_followups"},
            expected_cost="low",
            expected_risk="low",
        )
        svc.submit_post_execution_review(
            debate_id=debate_id,
            task_id="task_99",
            reviewer_role="adversarial_reviewer",
            challenge_type="spec_compliance",
            finding="Minor gap found",
            severity="medium",
        )
        svc.resolve_debate(debate_id, task_id="t1")

        artifact_files = list(arts.root_dir.rglob("*.json"))
        bundle_artifacts = [
            f
            for f in artifact_files
            if json.loads(f.read_text()).get("artifact_type") == "deliberation_bundle"
        ]
        assert len(bundle_artifacts) == 1
        content = json.loads(bundle_artifacts[0].read_text())
        assert "post_execution_reviews" in content
        assert len(content["post_execution_reviews"]) == 1

    def test_full_post_execution_lifecycle(self, tmp_path: Path) -> None:
        """Complete post-execution review flow with all 4 boundaries verified."""
        svc, store, arts = _make_integration(tmp_path)

        # 1. Open post-execution review debate
        debate_id = svc.open_post_execution_review(
            task_id="task_100",
            decision_point="Verify execution results for task_100",
        )

        # 2. Submit adversarial reviews (discussion boundary)
        svc.submit_post_execution_review(
            debate_id=debate_id,
            task_id="task_100",
            reviewer_role="reviewer_pass",
            challenge_type="spec_compliance",
            finding="All acceptance criteria met",
            severity="low",
            recommendation="accept",
        )
        svc.submit_post_execution_review(
            debate_id=debate_id,
            task_id="task_100",
            reviewer_role="reviewer_challenge",
            challenge_type="benchmark_interpretation",
            finding="Benchmark improvement may be noise",
            severity="high",
            evidence_refs=["benchmark_run_1", "benchmark_run_2"],
            recommendation="re_execute",
        )

        # 3. Submit a proposal for verdict (decision boundary)
        svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="arbitrator",
            plan_summary="Accept with followup benchmark",
            contract_draft={"verdict": "accepted_with_followups"},
            expected_cost="low",
            expected_risk="medium",
        )

        # 4. Resolve (decision boundary - only winner enters execution)
        decision = svc.resolve_debate(debate_id, task_id="t1")
        assert decision["selected_candidate_id"] is not None
        assert decision["escalation_required"] is False

        # 5. Verify all 4 boundaries
        # Discussion boundary: reviews stored as artifacts
        all_artifacts = list(arts.root_dir.rglob("*.json"))
        types_found = {json.loads(f.read_text()).get("artifact_type") for f in all_artifacts}
        assert "post_execution_review" in types_found
        assert "deliberation_bundle" in types_found

        # Audit boundary: events in ledger
        all_events = store.list_events(limit=100)
        event_types = [e["event_type"] for e in all_events]
        assert "deliberation.post_execution_review_opened" in event_types
        assert "deliberation.post_execution_review_submitted" in event_types
        assert "deliberation.resolved" in event_types

        # Summary reflects post-execution reviews
        summary = svc.get_debate_summary(debate_id)
        assert summary["post_execution_review_count"] == 2
        assert summary["critical_review_count"] == 0  # high != critical
