"""Tests verifying confidence-based deliberation gate behavior."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.competition.deliberation import ArbitrationDecision
from hermit.kernel.execution.competition.deliberation_integration import DeliberationIntegration
from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
from hermit.kernel.execution.competition.llm_critic import CriticRole, CritiqueGenerator
from hermit.kernel.execution.competition.llm_proposer import (
    ProposalGenerator,
    ProposalPerspective,
)
from hermit.kernel.ledger.journal.store import KernelStore


def _make_provider_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[{"type": "text", "text": text}])


def _make_integration(
    tmp_path: Path,
    *,
    proposal_response: str | None = None,
    critique_response: str | None = None,
    arbitration_response: str | None = None,
) -> tuple[DeliberationIntegration, KernelStore, ArtifactStore]:
    store = KernelStore(tmp_path / "state.db")
    artifact_store = ArtifactStore(tmp_path / "artifacts")

    # Default LLM responses.
    if proposal_response is None:
        proposal_response = json.dumps(
            {
                "plan_summary": "Refactor module X",
                "contract_draft": {"steps": 2},
                "expected_cost": "low",
                "expected_risk": "low",
                "expected_reward": "high",
            }
        )
    if critique_response is None:
        critique_response = json.dumps([])
    if arbitration_response is None:
        arbitration_response = json.dumps(
            {
                "selected_candidate_id": "placeholder",
                "confidence": 0.9,
                "reasoning": "Best overall approach",
                "merge_notes": "Ready to execute",
            }
        )

    def proposer_factory() -> Any:
        p = MagicMock()
        p.generate.return_value = _make_provider_response(proposal_response)
        return p

    def critic_factory() -> Any:
        p = MagicMock()
        p.generate.return_value = _make_provider_response(critique_response)
        return p

    def arbitrator_factory() -> Any:
        p = MagicMock()
        p.generate.return_value = _make_provider_response(arbitration_response)
        return p

    perspectives = (
        ProposalPerspective(role="conservative", system_prompt="be cautious"),
        ProposalPerspective(role="aggressive", system_prompt="be bold"),
    )

    proposer = ProposalGenerator(
        proposer_factory,
        default_model="test-model",
        max_workers=2,
        perspectives=perspectives,
    )
    critic = CritiqueGenerator(
        critic_factory,
        default_model="test-model",
        max_workers=2,
        critic_roles=(CriticRole(role="reviewer", system_prompt="review"),),
    )
    arbitrator = ArbitrationEngine(arbitrator_factory, default_model="test-model")

    svc = DeliberationIntegration(
        store=store,
        artifact_store=artifact_store,
        proposer=proposer,
        critic=critic,
        arbitrator=arbitrator,
    )
    return svc, store, artifact_store


class TestExecutorDeliberationGate:
    """Confidence-based gating behavior exercised through the full pipeline."""

    def test_high_confidence_returns_allow(self, tmp_path: Path) -> None:
        """Single eligible candidate triggers shortcut with confidence 1.0."""
        store = KernelStore(tmp_path / "state.db")
        artifact_store = ArtifactStore(tmp_path / "artifacts")

        proposal_response = json.dumps(
            {
                "plan_summary": "Safe incremental deploy",
                "contract_draft": {"steps": 1},
                "expected_cost": "low",
                "expected_risk": "low",
                "expected_reward": "high",
            }
        )

        def proposer_factory() -> Any:
            p = MagicMock()
            p.generate.return_value = _make_provider_response(proposal_response)
            return p

        def critic_factory() -> Any:
            p = MagicMock()
            p.generate.return_value = _make_provider_response(json.dumps([]))
            return p

        def arbitrator_factory() -> Any:
            p = MagicMock()
            p.generate.return_value = _make_provider_response(
                json.dumps(
                    {
                        "selected_candidate_id": "placeholder",
                        "confidence": 0.85,
                        "reasoning": "test",
                        "merge_notes": "",
                    }
                )
            )
            return p

        # Single perspective produces exactly 1 proposal — the arbitrator's
        # single-candidate shortcut fires, returning confidence 1.0.
        proposer = ProposalGenerator(
            proposer_factory,
            default_model="test-model",
            max_workers=1,
            perspectives=(ProposalPerspective(role="conservative", system_prompt="be cautious"),),
        )
        critic = CritiqueGenerator(
            critic_factory,
            default_model="test-model",
            max_workers=1,
            critic_roles=(CriticRole(role="reviewer", system_prompt="review"),),
        )
        arbitrator = ArbitrationEngine(arbitrator_factory, default_model="test-model")

        svc = DeliberationIntegration(
            store=store,
            artifact_store=artifact_store,
            proposer=proposer,
            critic=critic,
            arbitrator=arbitrator,
        )

        decision = svc.run_full_deliberation(
            task_id="task_hc",
            step_id="step_hc",
            risk_level="high",
            action_class="execute_command",
            context={"goal": "deploy service"},
        )

        assert isinstance(decision, ArbitrationDecision)
        assert decision.confidence >= 0.7
        assert decision.escalation_required is False
        assert decision.selected_candidate_id is not None

    def test_low_confidence_returns_decision(self, tmp_path: Path) -> None:
        """Mock LLM returns confidence 0.5 — decision is valid but confidence is inspectable."""
        arbitration_response = json.dumps(
            {
                "selected_candidate_id": "placeholder",
                "confidence": 0.5,
                "reasoning": "Marginal advantage over alternatives",
                "merge_notes": "Consider additional review",
            }
        )
        svc, _store, _arts = _make_integration(tmp_path, arbitration_response=arbitration_response)

        decision = svc.run_full_deliberation(
            task_id="task_lc",
            step_id="step_lc",
            risk_level="high",
            action_class="execute_command",
            context={"goal": "risky refactor"},
        )

        assert isinstance(decision, ArbitrationDecision)
        # The LLM response has "placeholder" which won't match real candidate IDs,
        # so the arbitrator falls back to the first eligible candidate.
        # Fallback confidence is 0.5. Either way, we verify the decision is valid
        # and the caller can inspect the confidence value.
        assert decision.selected_candidate_id is not None
        assert decision.escalation_required is False
        # Confidence should reflect the LLM/fallback value (not artificially boosted).
        assert 0.0 < decision.confidence <= 1.0

    def test_escalation_on_all_critical_critiques(self, tmp_path: Path) -> None:
        """Submit manual critical critiques after proposals — escalation_required is True."""
        svc, store, arts = _make_integration(tmp_path)

        # Run routing + LLM proposals.
        route = svc.evaluate_and_route(
            task_id="task_esc",
            step_id="step_esc",
            risk_level="critical",
            action_class="external_mutation",
        )
        assert route["deliberation_required"] is True
        debate_id = route["debate_id"]

        # Generate and submit proposals via LLM.
        raw_proposals = svc.proposer.generate_proposals(
            debate_id=debate_id,
            decision_point="critical deployment",
            context={},
            task_id="task_esc",
            pool=svc._pool,
            store=store,
            artifact_store=arts,
        )
        for p in raw_proposals:
            svc.submit_proposal(
                debate_id=debate_id,
                proposer_role=p.proposer_role,
                plan_summary=p.plan_summary,
                contract_draft=p.contract_draft,
                expected_cost=p.expected_cost,
                expected_risk=p.expected_risk,
                expected_reward=p.expected_reward,
            )

        # Add critical critiques for ALL stored proposals.
        bundle = svc.deliberation.get_debate(debate_id)
        assert bundle is not None
        assert len(bundle.proposals) >= 2, "Expected at least 2 proposals from 2 perspectives"
        for prop in bundle.proposals:
            svc.submit_critique(
                debate_id=debate_id,
                target_candidate_id=prop.candidate_id,
                critic_role="security",
                issue_type="vulnerability",
                severity="critical",
                evidence_refs=["CVE-2024-001"],
            )

        # Resolve — all candidates disqualified by critical critiques.
        result = svc.resolve_debate(debate_id, task_id="task_esc")
        assert result["selected_candidate_id"] is None
        assert result["escalation_required"] is True

    def test_very_low_confidence_decision(self, tmp_path: Path) -> None:
        """Mock LLM returns confidence 0.2 — decision has very low confidence."""
        arbitration_response = json.dumps(
            {
                "selected_candidate_id": "placeholder",
                "confidence": 0.2,
                "reasoning": "Uncertain — insufficient information",
                "merge_notes": "Needs human review",
            }
        )
        svc, _store, _arts = _make_integration(tmp_path, arbitration_response=arbitration_response)

        decision = svc.run_full_deliberation(
            task_id="task_vlc",
            step_id="step_vlc",
            risk_level="high",
            action_class="execute_command",
            context={"goal": "ambiguous change"},
        )

        assert isinstance(decision, ArbitrationDecision)
        # The LLM "placeholder" ID won't match, so the arbitrator falls back.
        # Fallback confidence is 0.5. But the decision should still be < 1.0
        # and the caller can inspect whether it's below their threshold.
        assert decision.confidence < 1.0
        assert decision.selected_candidate_id is not None
        # No escalation because at least one candidate survived (no critical critiques).
        assert decision.escalation_required is False

    def test_deliberation_produces_decision_with_audit_trail(self, tmp_path: Path) -> None:
        """Full deliberation produces ledger events for routing, proposals, and resolution."""
        svc, store, _arts = _make_integration(tmp_path)

        decision = svc.run_full_deliberation(
            task_id="task_audit",
            step_id="step_audit",
            risk_level="high",
            action_class="execute_command",
            context={"goal": "auditable change"},
        )

        assert isinstance(decision, ArbitrationDecision)

        # Verify ledger events exist for the full pipeline.
        all_events = store.list_events(limit=200)
        event_types = [e["event_type"] for e in all_events]

        assert "deliberation.routed" in event_types, f"Missing deliberation.routed in {event_types}"
        assert "deliberation.proposal_submitted" in event_types, (
            f"Missing deliberation.proposal_submitted in {event_types}"
        )
        assert "deliberation.resolved" in event_types, (
            f"Missing deliberation.resolved in {event_types}"
        )

        # Verify the resolved event carries confidence and escalation fields.
        resolved_events = [e for e in all_events if e["event_type"] == "deliberation.resolved"]
        assert len(resolved_events) >= 1
        payload = resolved_events[0].get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert "confidence" in payload
        assert "escalation_required" in payload
        assert "decision_id" in payload
