"""Tests verifying Team and Milestone lifecycle in run_full_deliberation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.competition.deliberation_integration import DeliberationIntegration
from hermit.kernel.execution.competition.llm_arbitrator import ArbitrationEngine
from hermit.kernel.execution.competition.llm_critic import CriticRole, CritiqueGenerator
from hermit.kernel.execution.competition.llm_proposer import ProposalGenerator, ProposalPerspective
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


class TestTeamMilestoneLifecycle:
    """Verify that run_full_deliberation creates and completes Team + Milestone records."""

    def test_team_created_on_deliberation(self, tmp_path: Path) -> None:
        """High-risk deliberation creates exactly 1 team linked to the task_id (program_id)."""
        svc, store, _arts = _make_integration(tmp_path)
        task_id = "task_tm_001"

        svc.run_full_deliberation(
            task_id=task_id,
            step_id="step_001",
            risk_level="high",
            action_class="execute_command",
            context={"goal": "refactor auth"},
        )

        teams = store.list_teams_by_program(program_id=task_id)
        assert len(teams) == 1

        team = teams[0]
        assert team.program_id == task_id
        assert "proposer" in team.role_assembly
        assert "critic" in team.role_assembly
        assert "arbitrator" in team.role_assembly
        # Verify role_assembly slot specs.
        assert team.role_assembly["proposer"].role == "planner"
        assert team.role_assembly["proposer"].count == 3
        assert team.role_assembly["critic"].role == "reviewer"
        assert team.role_assembly["arbitrator"].role == "verifier"
        assert team.role_assembly["arbitrator"].count == 1

    def test_three_milestones_created(self, tmp_path: Path) -> None:
        """Deliberation creates 3 milestones: Proposals, Critiques, Arbitration."""
        svc, store, _arts = _make_integration(tmp_path)
        task_id = "task_tm_002"

        svc.run_full_deliberation(
            task_id=task_id,
            step_id="step_002",
            risk_level="high",
            action_class="execute_command",
            context={},
        )

        teams = store.list_teams_by_program(program_id=task_id)
        assert len(teams) == 1
        team = teams[0]

        milestones = store.list_milestones_by_team(team_id=team.team_id)
        assert len(milestones) == 3

        titles = [m.title for m in milestones]
        assert "Generate Proposals" in titles
        assert "Generate Critiques" in titles
        assert "Arbitration" in titles

    def test_milestones_completed_after_deliberation(self, tmp_path: Path) -> None:
        """All 3 milestones have status='completed' and completed_at set after deliberation."""
        svc, store, _arts = _make_integration(tmp_path)
        task_id = "task_tm_003"

        svc.run_full_deliberation(
            task_id=task_id,
            step_id="step_003",
            risk_level="high",
            action_class="patch_file",
            context={"file": "auth.py"},
        )

        teams = store.list_teams_by_program(program_id=task_id)
        team = teams[0]
        milestones = store.list_milestones_by_team(team_id=team.team_id)

        for ms in milestones:
            assert ms.status == "completed", f"Milestone '{ms.title}' not completed"
            assert ms.completed_at is not None, f"Milestone '{ms.title}' missing completed_at"

    def test_team_completed_after_deliberation(self, tmp_path: Path) -> None:
        """Team status transitions to 'completed' after deliberation finishes."""
        svc, store, _arts = _make_integration(tmp_path)
        task_id = "task_tm_004"

        svc.run_full_deliberation(
            task_id=task_id,
            step_id="step_004",
            risk_level="high",
            action_class="execute_command",
            context={},
        )

        teams = store.list_teams_by_program(program_id=task_id)
        assert len(teams) == 1
        assert teams[0].status == "completed"

    def test_team_created_event_in_ledger(self, tmp_path: Path) -> None:
        """A 'deliberation.team_created' event is emitted in the ledger."""
        svc, store, _arts = _make_integration(tmp_path)
        task_id = "task_tm_005"

        svc.run_full_deliberation(
            task_id=task_id,
            step_id="step_005",
            risk_level="high",
            action_class="execute_command",
            context={},
        )

        all_events = store.list_events(limit=200)
        event_types = [e["event_type"] for e in all_events]
        assert "deliberation.team_created" in event_types

        # Find the specific event and verify its payload references the team.
        team_events = [e for e in all_events if e["event_type"] == "deliberation.team_created"]
        assert len(team_events) == 1
        payload = team_events[0].get("payload", {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert "team_id" in payload
