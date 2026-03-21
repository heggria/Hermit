from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.execution.competition.deliberation import (
    ArbitrationDecision,
    CandidateProposal,
    CritiqueRecord,
    DebateBundle,
    DeliberationTrigger,
)
from hermit.kernel.execution.competition.deliberation_service import (
    DeliberationService,
)
from hermit.kernel.ledger.journal.store import KernelStore


def _make_service(tmp_path: Path) -> tuple[DeliberationService, KernelStore]:
    store = KernelStore(tmp_path / "state.db")
    svc = DeliberationService(store=store)
    return svc, store


def _make_proposal(
    candidate_id: str = "cand_1",
    proposer_role: str = "engineer",
    target_scope: str = "module_x",
) -> CandidateProposal:
    return CandidateProposal(
        candidate_id=candidate_id,
        proposer_role=proposer_role,
        target_scope=target_scope,
        plan_summary="Refactor module_x for clarity",
        contract_draft={"steps": 3},
        expected_cost="low",
        expected_risk="low",
        expected_reward="high",
    )


def _make_critique(
    target_candidate_id: str = "cand_1",
    severity: str = "medium",
    critique_id: str = "crit_1",
) -> CritiqueRecord:
    return CritiqueRecord(
        critique_id=critique_id,
        target_candidate_id=target_candidate_id,
        critic_role="reviewer",
        issue_type="correctness",
        severity=severity,
        evidence_refs=["ref_001"],
        suggested_fix="Add error handling",
    )


# -- DeliberationTrigger enum ------------------------------------------------


class TestDeliberationTrigger:
    def test_values(self) -> None:
        assert DeliberationTrigger.high_risk_planning == "high_risk_planning"
        assert DeliberationTrigger.high_risk_patch == "high_risk_patch"
        assert DeliberationTrigger.ambiguous_spec == "ambiguous_spec"
        assert DeliberationTrigger.follow_up_decision == "follow_up_decision"
        assert DeliberationTrigger.benchmark_dispute == "benchmark_dispute"
        assert DeliberationTrigger.post_execution_review == "post_execution_review"

    def test_all_members(self) -> None:
        assert len(DeliberationTrigger) == 6


# -- Dataclass construction --------------------------------------------------


class TestDataclasses:
    def test_candidate_proposal_defaults(self) -> None:
        p = CandidateProposal(
            candidate_id="c1",
            proposer_role="eng",
            target_scope="scope",
            plan_summary="summary",
        )
        assert p.contract_draft == {}
        assert p.expected_cost == ""
        assert p.created_at == 0.0

    def test_critique_record_defaults(self) -> None:
        c = CritiqueRecord(
            critique_id="cr1",
            target_candidate_id="c1",
            critic_role="rev",
            issue_type="perf",
            severity="low",
        )
        assert c.evidence_refs == []
        assert c.suggested_fix == ""

    def test_debate_bundle_defaults(self) -> None:
        b = DebateBundle(
            debate_id="d1",
            decision_point="dp",
            trigger=DeliberationTrigger.ambiguous_spec,
        )
        assert b.proposals == []
        assert b.critiques == []
        assert b.arbitration_input == {}

    def test_arbitration_decision_defaults(self) -> None:
        d = ArbitrationDecision(
            decision_id="a1",
            debate_id="d1",
        )
        assert d.selected_candidate_id is None
        assert d.rejection_reasons == []
        assert d.confidence == 0.0
        assert d.escalation_required is False


# -- should_deliberate --------------------------------------------------------


class TestShouldDeliberate:
    def test_high_risk_always_deliberates(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        assert svc.should_deliberate("high", "any_kind") is True
        assert svc.should_deliberate("critical", "trivial") is True

    def test_medium_risk_with_deliberation_step(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        assert svc.should_deliberate("medium", "planning") is True
        assert svc.should_deliberate("medium", "patch") is True
        assert svc.should_deliberate("medium", "deploy") is True
        assert svc.should_deliberate("medium", "rollback") is True

    def test_medium_risk_with_normal_step(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        assert svc.should_deliberate("medium", "read_file") is False

    def test_low_risk_does_not_deliberate(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        assert svc.should_deliberate("low", "planning") is False
        assert svc.should_deliberate("low", "patch") is False


# -- Debate lifecycle ---------------------------------------------------------


class TestDebateLifecycle:
    def test_create_debate(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate(
            decision_point="Should we refactor?",
            trigger=DeliberationTrigger.ambiguous_spec,
        )
        assert bundle.debate_id.startswith("debate_")
        assert bundle.decision_point == "Should we refactor?"
        assert bundle.trigger == DeliberationTrigger.ambiguous_spec
        assert bundle.proposals == []
        assert bundle.critiques == []

    def test_get_debate(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_planning)
        retrieved = svc.get_debate(bundle.debate_id)
        assert retrieved is bundle

    def test_get_debate_not_found(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        assert svc.get_debate("nonexistent") is None

    def test_add_proposal(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_patch)
        proposal = _make_proposal()
        svc.add_proposal(bundle.debate_id, proposal)
        assert len(bundle.proposals) == 1
        assert bundle.proposals[0].candidate_id == "cand_1"

    def test_add_multiple_proposals(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_patch)
        svc.add_proposal(bundle.debate_id, _make_proposal("c1"))
        svc.add_proposal(bundle.debate_id, _make_proposal("c2"))
        assert len(bundle.proposals) == 2

    def test_add_proposal_unknown_debate(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        with pytest.raises(ValueError, match="Debate not found"):
            svc.add_proposal("bad_id", _make_proposal())

    def test_add_critique(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.follow_up_decision)
        critique = _make_critique()
        svc.add_critique(bundle.debate_id, critique)
        assert len(bundle.critiques) == 1
        assert bundle.critiques[0].severity == "medium"

    def test_add_critique_unknown_debate(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        with pytest.raises(ValueError, match="Debate not found"):
            svc.add_critique("bad_id", _make_critique())


# -- Arbitration --------------------------------------------------------------


class TestArbitration:
    def test_arbitrate_selects_first_eligible(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_planning)
        svc.add_proposal(bundle.debate_id, _make_proposal("c1", "engineer"))
        svc.add_proposal(bundle.debate_id, _make_proposal("c2", "architect"))

        decision = svc.arbitrate(bundle.debate_id)
        assert decision.selected_candidate_id == "c1"
        assert decision.escalation_required is False
        assert decision.confidence == 1.0
        assert decision.decided_at > 0

    def test_arbitrate_skips_critically_critiqued(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_patch)
        svc.add_proposal(bundle.debate_id, _make_proposal("c1"))
        svc.add_proposal(bundle.debate_id, _make_proposal("c2"))
        # Critical critique on c1
        svc.add_critique(
            bundle.debate_id,
            _make_critique("c1", severity="critical", critique_id="cr1"),
        )

        decision = svc.arbitrate(bundle.debate_id)
        assert decision.selected_candidate_id == "c2"
        assert "c1" in decision.rejection_reasons[0]

    def test_arbitrate_escalates_when_all_critically_critiqued(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.benchmark_dispute)
        svc.add_proposal(bundle.debate_id, _make_proposal("c1"))
        svc.add_critique(
            bundle.debate_id,
            _make_critique("c1", severity="critical", critique_id="cr1"),
        )

        decision = svc.arbitrate(bundle.debate_id)
        assert decision.selected_candidate_id is None
        assert decision.escalation_required is True
        assert decision.confidence == 0.0

    def test_arbitrate_no_proposals_escalates(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.ambiguous_spec)
        # No proposals added at all.
        decision = svc.arbitrate(bundle.debate_id)
        assert decision.selected_candidate_id is None
        assert decision.escalation_required is True

    def test_arbitrate_unknown_debate(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        with pytest.raises(ValueError, match="Debate not found"):
            svc.arbitrate("bad_id")

    def test_arbitrate_confidence_decreases_with_critiques(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_planning)
        svc.add_proposal(bundle.debate_id, _make_proposal("c1"))
        # Add non-critical critiques targeting c1
        svc.add_critique(
            bundle.debate_id,
            _make_critique("c1", severity="medium", critique_id="cr1"),
        )
        svc.add_critique(
            bundle.debate_id,
            _make_critique("c1", severity="low", critique_id="cr2"),
        )

        decision = svc.arbitrate(bundle.debate_id)
        assert decision.selected_candidate_id == "c1"
        # 2 critiques out of 2 target the winner: confidence = 1 - 2/2 = 0.0
        assert decision.confidence == 0.0

    def test_arbitrate_merge_notes_populated(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.follow_up_decision)
        svc.add_proposal(
            bundle.debate_id,
            _make_proposal("c1", proposer_role="security", target_scope="auth"),
        )
        decision = svc.arbitrate(bundle.debate_id)
        assert "security" in decision.merge_notes
        assert "auth" in decision.merge_notes

    def test_non_critical_critiques_do_not_disqualify(self, tmp_path: Path) -> None:
        svc, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_planning)
        svc.add_proposal(bundle.debate_id, _make_proposal("c1"))
        svc.add_critique(
            bundle.debate_id,
            _make_critique("c1", severity="medium", critique_id="cr1"),
        )
        svc.add_critique(
            bundle.debate_id,
            _make_critique("c1", severity="low", critique_id="cr2"),
        )

        decision = svc.arbitrate(bundle.debate_id)
        # c1 is still selected despite non-critical critiques
        assert decision.selected_candidate_id == "c1"
        assert decision.escalation_required is False
