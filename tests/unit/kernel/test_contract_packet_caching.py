"""Tests verifying to_contract_packet reads cached decisions, not re-arbitrating."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.competition.deliberation_integration import (
    DeliberationIntegration,
)
from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
from hermit.kernel.execution.competition.llm_critic import CritiqueGenerator
from hermit.kernel.execution.competition.llm_proposer import ProposalGenerator
from hermit.kernel.ledger.journal.store import KernelStore


def _make_tracking_arbitrator() -> tuple[ArbitrationEngine, MagicMock]:
    """Create an ArbitrationEngine with a mock provider that tracks call count.

    Returns (engine, provider_mock) so tests can inspect how many times
    the LLM provider was actually invoked.
    """
    response = json.dumps({
        "selected_candidate_id": "placeholder",
        "confidence": 0.8,
        "reasoning": "test",
    })

    provider = MagicMock()
    provider.generate.return_value = SimpleNamespace(
        content=[{"type": "text", "text": response}]
    )

    def factory() -> Any:
        return provider

    engine = ArbitrationEngine(factory, default_model="test-model")
    return engine, provider


def _make_integration_with_tracking(
    tmp_path: Path,
) -> tuple[DeliberationIntegration, KernelStore, ArtifactStore, MagicMock]:
    """Create a DeliberationIntegration with a tracked provider mock.

    Returns (svc, store, artifact_store, provider_mock).
    """
    store = KernelStore(tmp_path / "state.db")
    artifact_store = ArtifactStore(tmp_path / "artifacts")

    def factory() -> Any:
        return MagicMock()

    proposer = ProposalGenerator(factory, default_model="test-model")
    critic = CritiqueGenerator(factory, default_model="test-model")
    arbitrator, provider_mock = _make_tracking_arbitrator()

    svc = DeliberationIntegration(
        store=store,
        artifact_store=artifact_store,
        proposer=proposer,
        critic=critic,
        arbitrator=arbitrator,
    )
    return svc, store, artifact_store, provider_mock


def _setup_debate_with_two_proposals(
    svc: DeliberationIntegration,
) -> tuple[str, str, str]:
    """Open a debate, submit two proposals, add a critical critique to the second.

    Returns (debate_id, candidate_a_id, candidate_b_id).
    Candidate A is the expected winner (no critical critique).
    Candidate B has a critical critique and should be rejected.
    """
    result = svc.evaluate_and_route(
        task_id="t1",
        step_id="s1",
        risk_level="high",
        action_class="execute_command",
    )
    debate_id = result["debate_id"]
    assert debate_id is not None

    cand_a = svc.submit_proposal(
        debate_id=debate_id,
        proposer_role="engineer",
        plan_summary="Safe refactor in 3 steps",
        contract_draft={"steps": 3, "scope": "auth"},
        expected_cost="low",
        expected_risk="low",
    )
    cand_b = svc.submit_proposal(
        debate_id=debate_id,
        proposer_role="architect",
        plan_summary="Full rewrite",
        contract_draft={"steps": 1},
        expected_cost="high",
        expected_risk="high",
    )
    # Add a critical critique to candidate B so candidate A wins.
    svc.submit_critique(
        debate_id=debate_id,
        target_candidate_id=cand_b,
        critic_role="security",
        issue_type="vulnerability",
        severity="critical",
    )
    return debate_id, cand_a, cand_b


class TestContractPacketCaching:
    """Verify that to_contract_packet uses cached arbitration results after fix #2."""

    def test_to_contract_after_resolve_uses_cached_decision(
        self, tmp_path: Path
    ) -> None:
        """After resolve_debate(), to_contract_packet() must NOT call the LLM again.

        The provider mock tracks call count. resolve_debate() triggers one
        arbitration (which may or may not hit the LLM depending on candidate
        count). to_contract_packet() should read the cached decision, so the
        provider call count must not increase between the two calls.
        """
        svc, _store, _arts, provider_mock = _make_integration_with_tracking(tmp_path)
        debate_id, cand_a, _cand_b = _setup_debate_with_two_proposals(svc)

        # resolve_debate triggers arbitration
        svc.resolve_debate(debate_id, task_id="t1")
        calls_after_resolve = provider_mock.generate.call_count

        # to_contract_packet should use cached decision, not re-arbitrate
        contract = svc.to_contract_packet(debate_id=debate_id, task_id="task_42")
        calls_after_contract = provider_mock.generate.call_count

        assert calls_after_contract == calls_after_resolve, (
            f"Provider was called {calls_after_contract - calls_after_resolve} "
            f"additional time(s) during to_contract_packet — expected 0 (cached)"
        )
        assert contract.task_id == "task_42"

    def test_to_contract_returns_consistent_winner(self, tmp_path: Path) -> None:
        """resolve_debate() selects candidate A, then to_contract_packet() must
        also use candidate A — not a potentially different LLM result.

        This test verifies that the cached decision is actually used for the
        contract, ensuring the winning candidate_id and plan_summary match.
        """
        svc, _store, _arts, _provider_mock = _make_integration_with_tracking(tmp_path)
        debate_id, cand_a, _cand_b = _setup_debate_with_two_proposals(svc)

        decision = svc.resolve_debate(debate_id, task_id="t1")
        assert decision["selected_candidate_id"] == cand_a

        contract = svc.to_contract_packet(debate_id=debate_id, task_id="task_42")

        # The contract goal and scope must come from candidate A's proposal.
        assert contract.goal == "Safe refactor in 3 steps"
        assert contract.scope == {"steps": 3, "scope": "auth"}
        assert contract.risk_band == "low"
        assert contract.task_id == "task_42"

    def test_to_contract_without_resolve_still_works(self, tmp_path: Path) -> None:
        """Calling to_contract_packet() without a prior resolve_debate() should
        fall through to live arbitration (no cached decision available).

        This ensures backward compatibility — the cache is an optimization,
        not a hard requirement.
        """
        svc, _store, _arts, provider_mock = _make_integration_with_tracking(tmp_path)
        debate_id, cand_a, _cand_b = _setup_debate_with_two_proposals(svc)

        # Skip resolve_debate — go straight to to_contract_packet.
        # The cache is empty, so it should fall through to live arbitration.
        contract = svc.to_contract_packet(debate_id=debate_id, task_id="task_42")

        # With only one eligible candidate (cand_a), arbitration is deterministic
        # and does not need the LLM. The contract should still be valid.
        assert contract.task_id == "task_42"
        assert contract.goal == "Safe refactor in 3 steps"
        assert contract.scope == {"steps": 3, "scope": "auth"}
        assert contract.risk_band == "low"
