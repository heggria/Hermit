from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.competition.deliberation import (
    CandidateProposal,
    CritiqueRecord,
    DebateBundle,
    DeliberationTrigger,
)
from hermit.kernel.execution.competition.llm_arbitrator import (
    ArbitrationEngine,
    _parse_json_response,
)
from hermit.kernel.execution.workers.models import (
    WorkerPoolConfig,
    WorkerRole,
    WorkerSlotConfig,
)
from hermit.kernel.execution.workers.pool import WorkerPoolManager
from hermit.kernel.ledger.journal.store import KernelStore


def _make_provider(text: str) -> MagicMock:
    provider = MagicMock()
    provider.generate.return_value = SimpleNamespace(content=[{"type": "text", "text": text}])
    return provider


def _make_pool() -> WorkerPoolManager:
    config = WorkerPoolConfig(
        pool_id="test",
        team_id="test",
        slots={
            WorkerRole.verifier: WorkerSlotConfig(role=WorkerRole.verifier, max_active=1),
        },
    )
    return WorkerPoolManager(config)


def _make_engine(response_text: str) -> ArbitrationEngine:
    def factory() -> Any:
        return _make_provider(response_text)

    return ArbitrationEngine(factory, default_model="test-model")


def _make_bundle(
    *,
    proposals: list[CandidateProposal] | None = None,
    critiques: list[CritiqueRecord] | None = None,
) -> DebateBundle:
    return DebateBundle(
        debate_id="debate_1",
        decision_point="Should we refactor?",
        trigger=DeliberationTrigger.high_risk_planning,
        proposals=proposals or [],
        critiques=critiques or [],
    )


def _make_proposal(cid: str = "c1", role: str = "engineer") -> CandidateProposal:
    return CandidateProposal(
        candidate_id=cid,
        proposer_role=role,
        target_scope="scope",
        plan_summary=f"Plan by {role}",
        contract_draft={"steps": 1},
        expected_cost="low",
        expected_risk="low",
        expected_reward="high",
    )


def _make_critique(target: str, severity: str = "medium") -> CritiqueRecord:
    return CritiqueRecord(
        critique_id=f"crit_{target}",
        target_candidate_id=target,
        critic_role="reviewer",
        issue_type="correctness",
        severity=severity,
    )


class TestParseJsonResponse:
    def test_valid_json(self) -> None:
        assert _parse_json_response('{"k": "v"}')["k"] == "v"

    def test_code_fenced(self) -> None:
        assert _parse_json_response('```json\n{"k": "v"}\n```')["k"] == "v"

    def test_invalid_returns_empty(self) -> None:
        assert _parse_json_response("not json") == {}


class TestArbitrationEngine:
    def test_no_proposals_escalates(self, tmp_path: Path) -> None:
        engine = _make_engine("{}")
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        decision = engine.arbitrate(
            _make_bundle(),
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )
        assert decision.escalation_required is True

    def test_all_critically_critiqued_escalates(self, tmp_path: Path) -> None:
        engine = _make_engine("{}")
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        bundle = _make_bundle(
            proposals=[_make_proposal("c1"), _make_proposal("c2", "architect")],
            critiques=[_make_critique("c1", "critical"), _make_critique("c2", "critical")],
        )
        decision = engine.arbitrate(
            bundle,
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )
        assert decision.escalation_required is True
        assert len(decision.rejection_reasons) == 2

    def test_single_eligible_no_llm_call(self, tmp_path: Path) -> None:
        engine = _make_engine("should not be called")
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        bundle = _make_bundle(
            proposals=[_make_proposal("c1"), _make_proposal("c2", "architect")],
            critiques=[_make_critique("c2", "critical")],
        )
        decision = engine.arbitrate(
            bundle,
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )
        assert decision.selected_candidate_id == "c1"
        assert decision.confidence == 1.0

    def test_llm_selects_candidate(self, tmp_path: Path) -> None:
        response = json.dumps(
            {
                "selected_candidate_id": "c2",
                "confidence": 0.85,
                "reasoning": "c2 is better balanced",
                "merge_notes": "proceed with caution",
            }
        )
        engine = _make_engine(response)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        bundle = _make_bundle(
            proposals=[_make_proposal("c1"), _make_proposal("c2", "architect")],
        )
        decision = engine.arbitrate(
            bundle,
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )
        assert decision.selected_candidate_id == "c2"
        assert decision.confidence == 0.85
        assert "c2 is better balanced" in decision.merge_notes

        # Verify step was created and slot released.
        assert pool.get_status().active_slots == 0

    def test_llm_invalid_selection_falls_back(self, tmp_path: Path) -> None:
        response = json.dumps({"selected_candidate_id": "nonexistent", "confidence": 0.9})
        engine = _make_engine(response)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        bundle = _make_bundle(
            proposals=[_make_proposal("c1"), _make_proposal("c2", "architect")],
        )
        decision = engine.arbitrate(
            bundle,
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )
        assert decision.selected_candidate_id == "c1"
        assert "[fallback]" in decision.merge_notes

    def test_llm_exception_falls_back(self, tmp_path: Path) -> None:
        def failing_factory() -> Any:
            p = MagicMock()
            p.generate.side_effect = RuntimeError("LLM down")
            return p

        engine = ArbitrationEngine(failing_factory, default_model="test-model")
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        bundle = _make_bundle(
            proposals=[_make_proposal("c1"), _make_proposal("c2", "architect")],
        )
        decision = engine.arbitrate(
            bundle,
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )
        assert decision.selected_candidate_id == "c1"
        assert "[fallback]" in decision.merge_notes
        assert pool.get_status().active_slots == 0

    def test_confidence_clamped(self, tmp_path: Path) -> None:
        response = json.dumps({"selected_candidate_id": "c1", "confidence": 1.5})
        engine = _make_engine(response)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")
        pool = _make_pool()

        bundle = _make_bundle(proposals=[_make_proposal("c1"), _make_proposal("c2")])
        decision = engine.arbitrate(
            bundle,
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )
        assert decision.confidence == 1.0
