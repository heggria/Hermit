from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
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
from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
from hermit.kernel.execution.workers.models import (
    WorkerPoolConfig,
    WorkerRole,
    WorkerSlotConfig,
)
from hermit.kernel.execution.workers.pool import WorkerPoolManager
from hermit.kernel.ledger.journal.store import KernelStore


def _make_pool() -> WorkerPoolManager:
    config = WorkerPoolConfig(
        pool_id="test-pool",
        team_id="test",
        slots={
            WorkerRole.planner: WorkerSlotConfig(role=WorkerRole.planner, max_active=3),
            WorkerRole.reviewer: WorkerSlotConfig(role=WorkerRole.reviewer, max_active=3),
            WorkerRole.verifier: WorkerSlotConfig(role=WorkerRole.verifier, max_active=1),
        },
    )
    return WorkerPoolManager(config)


def _make_arbitrator(response_text: str | None = None) -> ArbitrationEngine:
    if response_text is None:
        response_text = json.dumps({
            "selected_candidate_id": "placeholder",
            "confidence": 0.8,
            "reasoning": "test",
        })

    def factory() -> Any:
        p = MagicMock()
        p.generate.return_value = SimpleNamespace(
            content=[{"type": "text", "text": response_text}]
        )
        return p

    return ArbitrationEngine(factory, default_model="test-model")


def _make_service(
    tmp_path: Path,
) -> tuple[DeliberationService, KernelStore, ArtifactStore, WorkerPoolManager]:
    store = KernelStore(tmp_path / "state.db")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    pool = _make_pool()
    svc = DeliberationService(store=store, arbitrator=_make_arbitrator())
    return svc, store, artifact_store, pool


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
        assert len(DeliberationTrigger) == 7


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
    def test_high_risk_mutation_deliberates(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        assert svc.should_deliberate("high", "execute_command") is True
        assert svc.should_deliberate("critical", "write_local") is True
        assert svc.should_deliberate("critical", "rollback") is True

    def test_high_risk_readonly_skips(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        assert svc.should_deliberate("critical", "read_local") is False
        assert svc.should_deliberate("high", "execute_command_readonly") is False
        assert svc.should_deliberate("critical", "network_read") is False
        assert svc.should_deliberate("high", "delegate_reasoning") is False

    def test_medium_risk_mutation_deliberates(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        assert svc.should_deliberate("medium", "execute_command") is True
        assert svc.should_deliberate("medium", "write_local") is True
        assert svc.should_deliberate("medium", "patch_file") is True
        assert svc.should_deliberate("medium", "rollback") is True
        assert svc.should_deliberate("medium", "vcs_mutation") is True

    def test_medium_risk_readonly_skips(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        assert svc.should_deliberate("medium", "read_local") is False
        assert svc.should_deliberate("medium", "execute_command_readonly") is False

    def test_low_risk_never_deliberates(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        assert svc.should_deliberate("low", "execute_command") is False
        assert svc.should_deliberate("low", "write_local") is False
        assert svc.should_deliberate("low", "read_local") is False

    def test_unknown_action_class_skips(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        assert svc.should_deliberate("critical", "unknown") is False
        assert svc.should_deliberate("high", "some_future_action") is False


# -- Debate lifecycle ---------------------------------------------------------


class TestDebateLifecycle:
    def test_create_debate(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        bundle = svc.create_debate(
            decision_point="Should we refactor?",
            trigger=DeliberationTrigger.ambiguous_spec,
        )
        assert bundle.debate_id.startswith("debate_")
        assert bundle.decision_point == "Should we refactor?"
        assert bundle.trigger == DeliberationTrigger.ambiguous_spec

    def test_get_debate(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_planning)
        assert svc.get_debate(bundle.debate_id) is bundle

    def test_get_debate_not_found(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        assert svc.get_debate("nonexistent") is None

    def test_add_proposal(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_patch)
        svc.add_proposal(bundle.debate_id, _make_proposal())
        assert len(bundle.proposals) == 1

    def test_add_proposal_unknown_debate(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        with pytest.raises(ValueError, match="Debate not found"):
            svc.add_proposal("bad_id", _make_proposal())

    def test_add_critique(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.follow_up_decision)
        svc.add_critique(bundle.debate_id, _make_critique())
        assert len(bundle.critiques) == 1

    def test_add_critique_unknown_debate(self, tmp_path: Path) -> None:
        svc, _, _, _ = _make_service(tmp_path)
        with pytest.raises(ValueError, match="Debate not found"):
            svc.add_critique("bad_id", _make_critique())


# -- Arbitration --------------------------------------------------------------


class TestArbitration:
    def test_arbitrate_selects_first_eligible(self, tmp_path: Path) -> None:
        svc, store, arts, pool = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_planning)
        svc.add_proposal(bundle.debate_id, _make_proposal("c1", "engineer"))
        svc.add_proposal(bundle.debate_id, _make_proposal("c2", "architect"))

        decision = svc.arbitrate(
            bundle.debate_id, task_id="t1", pool=pool, store=store, artifact_store=arts,
        )
        assert decision.selected_candidate_id == "c1"
        assert decision.escalation_required is False

    def test_arbitrate_skips_critically_critiqued(self, tmp_path: Path) -> None:
        svc, store, arts, pool = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_patch)
        svc.add_proposal(bundle.debate_id, _make_proposal("c1"))
        svc.add_proposal(bundle.debate_id, _make_proposal("c2"))
        svc.add_critique(
            bundle.debate_id, _make_critique("c1", severity="critical", critique_id="cr1"),
        )

        decision = svc.arbitrate(
            bundle.debate_id, task_id="t1", pool=pool, store=store, artifact_store=arts,
        )
        assert decision.selected_candidate_id == "c2"

    def test_arbitrate_escalates_when_all_critical(self, tmp_path: Path) -> None:
        svc, store, arts, pool = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.benchmark_dispute)
        svc.add_proposal(bundle.debate_id, _make_proposal("c1"))
        svc.add_critique(
            bundle.debate_id, _make_critique("c1", severity="critical", critique_id="cr1"),
        )

        decision = svc.arbitrate(
            bundle.debate_id, task_id="t1", pool=pool, store=store, artifact_store=arts,
        )
        assert decision.selected_candidate_id is None
        assert decision.escalation_required is True

    def test_arbitrate_no_proposals_escalates(self, tmp_path: Path) -> None:
        svc, store, arts, pool = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.ambiguous_spec)

        decision = svc.arbitrate(
            bundle.debate_id, task_id="t1", pool=pool, store=store, artifact_store=arts,
        )
        assert decision.escalation_required is True

    def test_arbitrate_unknown_debate(self, tmp_path: Path) -> None:
        svc, store, arts, pool = _make_service(tmp_path)
        with pytest.raises(ValueError, match="Debate not found"):
            svc.arbitrate("bad_id", task_id="t1", pool=pool, store=store, artifact_store=arts)

    def test_non_critical_critiques_do_not_disqualify(self, tmp_path: Path) -> None:
        svc, store, arts, pool = _make_service(tmp_path)
        bundle = svc.create_debate("dp", DeliberationTrigger.high_risk_planning)
        svc.add_proposal(bundle.debate_id, _make_proposal("c1"))
        svc.add_critique(
            bundle.debate_id, _make_critique("c1", severity="medium", critique_id="cr1"),
        )

        decision = svc.arbitrate(
            bundle.debate_id, task_id="t1", pool=pool, store=store, artifact_store=arts,
        )
        assert decision.selected_candidate_id == "c1"
        assert decision.escalation_required is False
