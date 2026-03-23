"""Tests verifying task_id parameter flows through resolve_debate to events."""
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


def _make_arbitrator() -> ArbitrationEngine:
    """Create an ArbitrationEngine with a mock provider that falls back."""
    response = json.dumps({
        "selected_candidate_id": "placeholder",
        "confidence": 0.8,
        "reasoning": "test",
    })

    def factory() -> Any:
        p = MagicMock()
        p.generate.return_value = SimpleNamespace(
            content=[{"type": "text", "text": response}]
        )
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


def _open_debate_with_proposals(
    svc: DeliberationIntegration,
) -> tuple[str, str, str]:
    """Open a debate with two proposals. Returns (debate_id, cand1, cand2)."""
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


class TestResolveTaskIdFlow:
    """Verify that task_id flows through resolve_debate into the resolved event."""

    def test_resolve_with_task_id_flows_to_event(self, tmp_path: Path) -> None:
        """Call resolve_debate(debate_id, task_id='task_42'), verify the
        deliberation.resolved event carries the task_id."""
        svc, store, _arts = _make_integration(tmp_path)
        debate_id, _c1, _c2 = _open_debate_with_proposals(svc)

        svc.resolve_debate(debate_id, task_id="task_42")

        events = store.list_events(event_type="deliberation.resolved")
        assert len(events) == 1

        resolved_event = events[0]
        assert resolved_event["task_id"] == "task_42"

    def test_resolve_without_task_id_defaults_gracefully(self, tmp_path: Path) -> None:
        """Call resolve_debate(debate_id) with no task_id.
        Verify it does not crash and the event is still emitted."""
        svc, store, _arts = _make_integration(tmp_path)
        debate_id, _c1, _c2 = _open_debate_with_proposals(svc)

        # No task_id passed — should default gracefully.
        decision = svc.resolve_debate(debate_id)

        assert isinstance(decision, dict)
        assert "decision_id" in decision
        assert "debate_id" in decision

        events = store.list_events(event_type="deliberation.resolved")
        assert len(events) == 1

    def test_run_full_deliberation_passes_task_id(self, tmp_path: Path) -> None:
        """Run the full deliberation flow (route -> propose -> critique -> resolve)
        with task_id='task_99' and verify the deliberation.resolved event has
        that task_id present."""
        svc, store, _arts = _make_integration(tmp_path)

        # 1. Route — opens a debate
        route = svc.evaluate_and_route(
            task_id="task_99",
            step_id="step_1",
            risk_level="high",
            action_class="execute_command",
        )
        assert route["deliberation_required"] is True
        debate_id = route["debate_id"]

        # 2. Submit proposals
        c1 = svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="planner",
            plan_summary="Conservative approach",
            contract_draft={"steps": 3},
            expected_cost="low",
            expected_risk="low",
        )
        svc.submit_proposal(
            debate_id=debate_id,
            proposer_role="architect",
            plan_summary="Aggressive rewrite",
            contract_draft={"steps": 1},
            expected_cost="high",
            expected_risk="high",
        )

        # 3. Submit a critique against the second proposal
        svc.submit_critique(
            debate_id=debate_id,
            target_candidate_id=c1,
            critic_role="reviewer",
            issue_type="style",
            severity="low",
        )

        # 4. Resolve with explicit task_id
        decision = svc.resolve_debate(debate_id, task_id="task_99")
        assert isinstance(decision, dict)

        # 5. Verify the resolved event carries task_id
        events = store.list_events(event_type="deliberation.resolved")
        assert len(events) == 1

        resolved_event = events[0]
        assert resolved_event["task_id"] == "task_99"
