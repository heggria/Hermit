"""Tests for slot exhaustion edge cases in LLM services."""

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
from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
from hermit.kernel.execution.competition.llm_critic import CritiqueGenerator
from hermit.kernel.execution.competition.llm_proposer import (
    ProposalGenerator,
    ProposalPerspective,
)
from hermit.kernel.execution.workers.models import (
    WorkerPoolConfig,
    WorkerRole,
    WorkerSlotConfig,
)
from hermit.kernel.execution.workers.pool import WorkerPoolManager
from hermit.kernel.ledger.journal.store import KernelStore

# -- helpers ------------------------------------------------------------------


def _make_provider(text: str) -> MagicMock:
    provider = MagicMock()
    provider.generate.return_value = SimpleNamespace(content=[{"type": "text", "text": text}])
    return provider


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


def _make_bundle(
    *,
    proposals: list[CandidateProposal] | None = None,
    critiques: list[CritiqueRecord] | None = None,
) -> DebateBundle:
    return DebateBundle(
        debate_id="debate_slot",
        decision_point="Should we refactor?",
        trigger=DeliberationTrigger.high_risk_planning,
        proposals=proposals or [],
        critiques=critiques or [],
    )


# -- test cases ---------------------------------------------------------------


class TestArbitratorSlotUnavailable:
    """ArbitrationEngine returns fallback when verifier slots exhausted."""

    def test_arbitrator_slot_unavailable_returns_fallback(self, tmp_path: Path) -> None:
        """Pool with max_active=0 for verifier returns fallback with confidence=0.5."""
        response = json.dumps(
            {
                "selected_candidate_id": "c1",
                "confidence": 0.9,
                "reasoning": "should not be reached",
            }
        )

        def factory() -> Any:
            return _make_provider(response)

        engine = ArbitrationEngine(factory, default_model="test-model")
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")

        config = WorkerPoolConfig(
            pool_id="empty_verifier",
            team_id="test",
            slots={
                WorkerRole.verifier: WorkerSlotConfig(role=WorkerRole.verifier, max_active=0),
            },
        )
        pool = WorkerPoolManager(config)

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

        assert decision.confidence == 0.5
        assert decision.selected_candidate_id == "c1"
        assert "[fallback]" in decision.merge_notes
        assert decision.escalation_required is False


class TestCriticSlotUnavailable:
    """CritiqueGenerator returns empty list when reviewer slots exhausted."""

    def test_critic_slot_unavailable_returns_empty(self, tmp_path: Path) -> None:
        """Pool with max_active=0 for reviewer produces no critiques."""
        response = json.dumps(
            [
                {
                    "target_candidate_id": "cand_1",
                    "issue_type": "security",
                    "severity": "high",
                    "suggested_fix": "should not be reached",
                }
            ]
        )

        def factory() -> Any:
            return _make_provider(response)

        gen = CritiqueGenerator(factory, default_model="test-model", max_workers=2)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")

        config = WorkerPoolConfig(
            pool_id="empty_reviewer",
            team_id="test",
            slots={
                WorkerRole.reviewer: WorkerSlotConfig(role=WorkerRole.reviewer, max_active=0),
            },
        )
        pool = WorkerPoolManager(config)

        proposals = [
            CandidateProposal(
                candidate_id="cand_1",
                proposer_role="eng",
                target_scope="scope",
                plan_summary="Plan A",
            ),
        ]

        critiques = gen.generate_critiques(
            proposals,
            {},
            task_id="t1",
            debate_id="d1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )

        assert critiques == []


class TestProposerSlotUnavailable:
    """ProposalGenerator returns empty list when planner slots exhausted."""

    def test_proposer_slot_unavailable_returns_empty(self, tmp_path: Path) -> None:
        """Pool with max_active=0 for planner produces no proposals."""
        response = json.dumps({"plan_summary": "should not be reached"})

        def factory() -> Any:
            return _make_provider(response)

        gen = ProposalGenerator(factory, default_model="test-model", max_workers=2)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")

        config = WorkerPoolConfig(
            pool_id="empty_planner",
            team_id="test",
            slots={
                WorkerRole.planner: WorkerSlotConfig(role=WorkerRole.planner, max_active=0),
            },
        )
        pool = WorkerPoolManager(config)

        proposals = gen.generate_proposals(
            "debate_slot",
            "Should we refactor?",
            {},
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )

        assert proposals == []


class TestSlotRelease:
    """Slots are properly released after parallel execution and on exceptions."""

    def test_all_slots_released_after_parallel_execution(self, tmp_path: Path) -> None:
        """Run proposer with 3 perspectives and verify all slots released."""
        response = json.dumps(
            {
                "plan_summary": "parallel plan",
                "contract_draft": {"steps": 1},
                "expected_cost": "low",
                "expected_risk": "low",
                "expected_reward": "medium",
            }
        )

        def factory() -> Any:
            return _make_provider(response)

        perspectives = (
            ProposalPerspective(role="alpha", system_prompt="alpha"),
            ProposalPerspective(role="beta", system_prompt="beta"),
            ProposalPerspective(role="gamma", system_prompt="gamma"),
        )
        gen = ProposalGenerator(
            factory,
            default_model="test-model",
            max_workers=3,
            perspectives=perspectives,
        )
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")

        config = WorkerPoolConfig(
            pool_id="parallel",
            team_id="test",
            slots={
                WorkerRole.planner: WorkerSlotConfig(role=WorkerRole.planner, max_active=3),
            },
        )
        pool = WorkerPoolManager(config)

        proposals = gen.generate_proposals(
            "debate_parallel",
            "Three-way decision",
            {},
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )

        assert len(proposals) == 3
        status = pool.get_status()
        assert status.active_slots == 0
        assert status.idle_slots == 3

    def test_slots_released_on_provider_exception(self, tmp_path: Path) -> None:
        """Provider raises an exception; verify slots are still released."""

        def failing_factory() -> Any:
            provider = MagicMock()
            provider.generate.side_effect = RuntimeError("provider exploded")
            return provider

        gen = ProposalGenerator(failing_factory, default_model="test-model", max_workers=3)
        store = KernelStore(tmp_path / "state.db")
        arts = ArtifactStore(tmp_path / "artifacts")

        config = WorkerPoolConfig(
            pool_id="exception",
            team_id="test",
            slots={
                WorkerRole.planner: WorkerSlotConfig(role=WorkerRole.planner, max_active=3),
            },
        )
        pool = WorkerPoolManager(config)

        proposals = gen.generate_proposals(
            "debate_exc",
            "Will fail",
            {},
            task_id="t1",
            pool=pool,
            store=store,
            artifact_store=arts,
        )

        assert proposals == []
        status = pool.get_status()
        assert status.active_slots == 0
        assert status.idle_slots == 3
