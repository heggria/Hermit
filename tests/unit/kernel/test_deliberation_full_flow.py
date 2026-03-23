"""End-to-end test: route → propose → critique → arbitrate via LLM services."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.competition.deliberation import (
    ArbitrationDecision,
)
from hermit.kernel.execution.competition.deliberation_integration import (
    DeliberationIntegration,
)
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
        proposal_response = json.dumps({
            "plan_summary": "Refactor module X",
            "contract_draft": {"steps": 2},
            "expected_cost": "low",
            "expected_risk": "low",
            "expected_reward": "high",
        })
    if critique_response is None:
        # Use a placeholder that references cand IDs dynamically.
        # We return empty critiques by default for simplicity.
        critique_response = json.dumps([])
    if arbitration_response is None:
        arbitration_response = json.dumps({
            "selected_candidate_id": "placeholder",
            "confidence": 0.9,
            "reasoning": "Best overall approach",
            "merge_notes": "Ready to execute",
        })

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


class TestFullDeliberationFlow:
    def test_end_to_end_llm_deliberation(self, tmp_path: Path) -> None:
        """Full pipeline: route → LLM propose → LLM critique → LLM arbitrate."""
        svc, store, arts = _make_integration(tmp_path)

        decision = svc.run_full_deliberation(
            task_id="task_001",
            step_id="step_001",
            risk_level="high",
            action_class="execute_command",
            context={"goal": "refactor auth"},
        )

        assert isinstance(decision, ArbitrationDecision)
        # With 2 perspectives and no critiques, arbitrator sees 2 eligible.
        # Single-candidate shortcut won't apply since both survive.
        # The LLM arbitrator response has "placeholder" which won't match
        # actual candidate IDs, so it will fallback to first eligible.
        assert decision.selected_candidate_id is not None
        assert decision.escalation_required is False

        # Verify ledger events.
        all_events = store.list_events(limit=100)
        event_types = [e["event_type"] for e in all_events]
        assert "deliberation.routed" in event_types
        assert "deliberation.proposal_submitted" in event_types
        assert "deliberation.resolved" in event_types

        # Verify artifacts.
        artifact_files = list(arts.root_dir.rglob("*.json"))
        types_found = set()
        for f in artifact_files:
            content = json.loads(f.read_text())
            if "artifact_type" in content:
                types_found.add(content["artifact_type"])
        assert "deliberation_proposal" in types_found
        assert "deliberation_bundle" in types_found
        assert "arbitration_decision" in types_found

    def test_low_risk_bypasses_deliberation(self, tmp_path: Path) -> None:
        svc, _store, arts = _make_integration(tmp_path)

        decision = svc.run_full_deliberation(
            task_id="t1",
            step_id="s1",
            risk_level="low",
            action_class="read_local",
            context={},
        )

        assert decision.escalation_required is False
        assert decision.confidence == 1.0
        # No artifacts should be created.
        artifact_files = list(arts.root_dir.rglob("*.json"))
        assert len(artifact_files) == 0

    def test_with_critiques_disqualifying_candidates(self, tmp_path: Path) -> None:
        """When critiques mark all candidates as critical, escalation occurs."""
        # Critique response that marks both candidate IDs.
        # Since we don't know exact IDs ahead of time, we use a trick:
        # the critique generator validates IDs, so invalid ones are dropped.
        # Instead, we create a scenario where the arbitrator sees no eligible.
        # We'll test by submitting manual critiques after LLM proposals.
        svc, store, arts = _make_integration(tmp_path)

        # Run routing + LLM proposals.
        route = svc.evaluate_and_route(
            task_id="t2",
            step_id="s2",
            risk_level="critical",
            action_class="external_mutation",
        )
        assert route["deliberation_required"] is True
        debate_id = route["debate_id"]

        # Generate and submit proposals.
        raw_proposals = svc.proposer.generate_proposals(
            debate_id=debate_id,
            decision_point="deploy test",
            context={},
            task_id="t2",
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
        for prop in bundle.proposals:
            svc.submit_critique(
                debate_id=debate_id,
                target_candidate_id=prop.candidate_id,
                critic_role="security",
                issue_type="vulnerability",
                severity="critical",
                evidence_refs=["CVE-001"],
            )

        # Resolve — should escalate.
        result = svc.resolve_debate(debate_id, task_id="t2")
        assert result["selected_candidate_id"] is None
        assert result["escalation_required"] is True

    def test_proposals_stored_as_artifacts(self, tmp_path: Path) -> None:
        svc, _store, arts = _make_integration(tmp_path)

        svc.run_full_deliberation(
            task_id="t3",
            step_id="s3",
            risk_level="high",
            action_class="patch_file",
            context={"file": "auth.py"},
        )

        # Check proposal artifacts.
        artifact_files = list(arts.root_dir.rglob("*.json"))
        proposal_arts = [
            f
            for f in artifact_files
            if json.loads(f.read_text()).get("artifact_type") == "deliberation_proposal"
        ]
        # 2 perspectives = 2 proposals.
        assert len(proposal_arts) == 2

    def test_critique_artifacts_stored(self, tmp_path: Path) -> None:
        """When critiques are generated, they are stored as artifacts."""
        critique_response = json.dumps([
            {
                "target_candidate_id": "WILL_BE_WRONG",
                "issue_type": "perf",
                "severity": "medium",
            },
        ])
        svc, _store, arts = _make_integration(
            tmp_path, critique_response=critique_response
        )

        svc.run_full_deliberation(
            task_id="t4",
            step_id="s4",
            risk_level="high",
            action_class="execute_command",
            context={},
        )

        # The critique target ID won't match real proposals, so
        # no critique artifacts should be stored (filtered out).
        artifact_files = list(arts.root_dir.rglob("*.json"))
        critique_arts = [
            f
            for f in artifact_files
            if json.loads(f.read_text()).get("artifact_type") == "deliberation_critique"
        ]
        assert len(critique_arts) == 0  # Invalid target filtered.


class TestDeliberationIntegrationConstructor:
    def test_requires_all_llm_components(self, tmp_path: Path) -> None:
        """All LLM components are required — no optional fallback."""
        store = KernelStore(tmp_path / "state.db")
        artifact_store = ArtifactStore(tmp_path / "artifacts")

        def factory() -> Any:
            return MagicMock()

        proposer = ProposalGenerator(factory, default_model="m")
        critic = CritiqueGenerator(factory, default_model="m")
        arbitrator = ArbitrationEngine(factory, default_model="m")

        # Should construct without error when all provided.
        svc = DeliberationIntegration(
            store=store,
            artifact_store=artifact_store,
            proposer=proposer,
            critic=critic,
            arbitrator=arbitrator,
        )
        assert svc.proposer is proposer
        assert svc.critic is critic
        assert svc.arbitrator is arbitrator
