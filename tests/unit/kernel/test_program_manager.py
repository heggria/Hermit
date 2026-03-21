"""Comprehensive tests for ProgramManager — the Control Plane program compiler."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.execution.controller.supervisor_protocol import (
    TaskContractPacket,
    create_verdict,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.program import (
    ProgramRecord,
    ProgramState,
)
from hermit.kernel.task.models.team import (
    MilestoneState,
    TeamState,
)
from hermit.kernel.task.services.program_manager import (
    BackgroundWorkItem,
    CompilationResult,
    FollowUpTask,
    ProgramManager,
    ProgramManagerError,
)


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture
def pm(store: KernelStore) -> ProgramManager:
    return ProgramManager(store)


# ---------------------------------------------------------------------------
# compile_program
# ---------------------------------------------------------------------------


class TestCompileProgram:
    def test_basic_compilation(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Ship v1 of the product")
        assert isinstance(program, ProgramRecord)
        assert program.program_id.startswith("program_")
        assert program.goal == "Ship v1 of the product"
        assert program.status == ProgramState.draft
        assert program.priority == "normal"

    def test_title_defaults_to_goal(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Build the auth module")
        assert program.title == "Build the auth module"

    def test_explicit_title(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Build auth", title="Auth Module v2")
        assert program.title == "Auth Module v2"
        assert program.goal == "Build auth"

    def test_long_goal_title_truncation(self, pm: ProgramManager) -> None:
        long_goal = "A" * 200
        program = pm.compile_program(goal=long_goal)
        assert len(program.title) <= 120

    def test_custom_priority(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Urgent fix", priority="high")
        assert program.priority == "high"

    def test_budget_limits(self, pm: ProgramManager) -> None:
        budget = {"tokens": 100_000, "cost_usd": 5.0}
        program = pm.compile_program(goal="Budget test", budget_limits=budget)
        assert program.budget_limits == budget

    def test_metadata(self, pm: ProgramManager) -> None:
        meta = {"requested_by": "user_abc", "source": "mcp"}
        program = pm.compile_program(goal="Meta test", metadata=meta)
        assert program.metadata == meta

    def test_all_fields(self, pm: ProgramManager) -> None:
        program = pm.compile_program(
            goal="Full compilation",
            title="Full Program",
            priority="high",
            budget_limits={"tokens": 50_000},
            metadata={"env": "prod"},
        )
        assert program.title == "Full Program"
        assert program.goal == "Full compilation"
        assert program.priority == "high"
        assert program.budget_limits == {"tokens": 50_000}
        assert program.metadata == {"env": "prod"}
        assert program.status == ProgramState.draft

    def test_compile_emits_event(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Event test")
        events = store.list_events(event_type="program.created")
        assert len(events) >= 1
        assert events[0]["entity_id"] == program.program_id

    def test_empty_goal_gets_fallback_title(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="")
        assert program.title == "Untitled program"


# ---------------------------------------------------------------------------
# add_team
# ---------------------------------------------------------------------------


class TestAddTeam:
    def test_add_team_to_draft_program(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Team test")
        team = pm.add_team(program_id=program.program_id, title="Alpha Team")
        assert team.team_id.startswith("team_")
        assert team.program_id == program.program_id
        assert team.title == "Alpha Team"
        assert team.status == TeamState.ACTIVE

    def test_default_workspace_id(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="WS test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        assert team.workspace_id == f"ws_{program.program_id}"

    def test_explicit_workspace_id(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="WS test")
        team = pm.add_team(
            program_id=program.program_id,
            title="Team A",
            workspace_id="custom_ws_1",
        )
        assert team.workspace_id == "custom_ws_1"

    def test_role_assembly(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Role test")
        roles = {"lead": "architect", "worker": "coder", "reviewer": "reviewer"}
        team = pm.add_team(
            program_id=program.program_id,
            title="Team A",
            role_assembly=roles,
        )
        # Legacy string values are wrapped into RoleSlotSpec on round-trip.
        from hermit.kernel.task.models.team import RoleSlotSpec

        assert isinstance(team.role_assembly["lead"], RoleSlotSpec)
        assert team.role_assembly["lead"].config == {"legacy_value": "architect"}
        assert team.role_assembly["worker"].config == {"legacy_value": "coder"}
        assert team.role_assembly["reviewer"].config == {"legacy_value": "reviewer"}

    def test_context_boundary(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Boundary test")
        boundary = ["src/hermit/kernel/", "src/hermit/runtime/"]
        team = pm.add_team(
            program_id=program.program_id,
            title="Team A",
            context_boundary=boundary,
        )
        assert team.context_boundary == boundary

    def test_add_team_to_active_program(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Active test")
        pm.activate_program(program.program_id)
        team = pm.add_team(program_id=program.program_id, title="Late Team")
        assert team.title == "Late Team"

    def test_add_team_to_paused_program(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Paused test")
        pm.activate_program(program.program_id)
        pm.pause_program(program.program_id)
        team = pm.add_team(program_id=program.program_id, title="Paused Team")
        assert team.title == "Paused Team"

    def test_add_team_to_completed_program_raises(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Completed test")
        pm.activate_program(program.program_id)
        pm.complete_program(program.program_id)
        with pytest.raises(ProgramManagerError, match="terminal state"):
            pm.add_team(program_id=program.program_id, title="Too Late")

    def test_add_team_to_failed_program_raises(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Failed test")
        pm.activate_program(program.program_id)
        pm.fail_program(program.program_id)
        with pytest.raises(ProgramManagerError, match="terminal state"):
            pm.add_team(program_id=program.program_id, title="Too Late")

    def test_add_team_nonexistent_program_raises(self, pm: ProgramManager) -> None:
        with pytest.raises(ProgramManagerError, match="Program not found"):
            pm.add_team(program_id="program_nonexistent", title="Ghost Team")

    def test_multiple_teams(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Multi team")
        t1 = pm.add_team(program_id=program.program_id, title="Team A")
        t2 = pm.add_team(
            program_id=program.program_id,
            title="Team B",
            workspace_id="ws_b",
        )
        teams = store.list_teams_by_program(program_id=program.program_id)
        assert len(teams) == 2
        team_ids = {t.team_id for t in teams}
        assert t1.team_id in team_ids
        assert t2.team_id in team_ids


# ---------------------------------------------------------------------------
# add_milestone
# ---------------------------------------------------------------------------


class TestAddMilestone:
    def test_basic_milestone(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Milestone test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms = pm.add_milestone(team_id=team.team_id, title="MVP")
        assert ms.milestone_id.startswith("milestone_")
        assert ms.team_id == team.team_id
        assert ms.title == "MVP"
        assert ms.status == MilestoneState.PENDING
        assert ms.description == ""
        assert ms.dependency_ids == []
        assert ms.acceptance_criteria == []

    def test_milestone_with_description(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Desc test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms = pm.add_milestone(
            team_id=team.team_id,
            title="MVP",
            description="Minimum viable product with core features",
        )
        assert ms.description == "Minimum viable product with core features"

    def test_milestone_with_acceptance_criteria(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Criteria test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        criteria = ["All tests pass", "Coverage > 80%", "No critical bugs"]
        ms = pm.add_milestone(
            team_id=team.team_id,
            title="Release",
            acceptance_criteria=criteria,
        )
        assert ms.acceptance_criteria == criteria

    def test_milestone_with_dependencies(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Dep test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms1 = pm.add_milestone(team_id=team.team_id, title="Research")
        ms2 = pm.add_milestone(
            team_id=team.team_id,
            title="Implementation",
            dependency_ids=[ms1.milestone_id],
        )
        assert ms2.dependency_ids == [ms1.milestone_id]

    def test_milestone_dependency_chain(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Chain test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms1 = pm.add_milestone(team_id=team.team_id, title="Phase 1")
        ms2 = pm.add_milestone(
            team_id=team.team_id,
            title="Phase 2",
            dependency_ids=[ms1.milestone_id],
        )
        ms3 = pm.add_milestone(
            team_id=team.team_id,
            title="Phase 3",
            dependency_ids=[ms2.milestone_id],
        )
        assert ms3.dependency_ids == [ms2.milestone_id]

    def test_milestone_multiple_dependencies(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Multi dep test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms1 = pm.add_milestone(team_id=team.team_id, title="Research")
        ms2 = pm.add_milestone(team_id=team.team_id, title="Design")
        ms3 = pm.add_milestone(
            team_id=team.team_id,
            title="Implementation",
            dependency_ids=[ms1.milestone_id, ms2.milestone_id],
        )
        assert set(ms3.dependency_ids) == {ms1.milestone_id, ms2.milestone_id}

    def test_milestone_invalid_dependency_raises(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Invalid dep test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        with pytest.raises(ProgramManagerError, match="dependency not found"):
            pm.add_milestone(
                team_id=team.team_id,
                title="Bad",
                dependency_ids=["milestone_nonexistent"],
            )

    def test_milestone_nonexistent_team_raises(self, pm: ProgramManager) -> None:
        with pytest.raises(ProgramManagerError, match="Team not found"):
            pm.add_milestone(team_id="team_nonexistent", title="Ghost")

    def test_milestone_registered_with_program(
        self, pm: ProgramManager, store: KernelStore
    ) -> None:
        program = pm.compile_program(goal="Registration test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms = pm.add_milestone(team_id=team.team_id, title="MS 1")
        updated = store.get_program(program.program_id)
        assert updated is not None
        assert ms.milestone_id in updated.milestone_ids

    def test_multiple_milestones_registered(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Multi registration")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms1 = pm.add_milestone(team_id=team.team_id, title="MS 1")
        ms2 = pm.add_milestone(team_id=team.team_id, title="MS 2")
        updated = store.get_program(program.program_id)
        assert updated is not None
        assert ms1.milestone_id in updated.milestone_ids
        assert ms2.milestone_id in updated.milestone_ids


# ---------------------------------------------------------------------------
# Program lifecycle
# ---------------------------------------------------------------------------


class TestProgramLifecycle:
    def test_activate_from_draft(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Activate test")
        pm.activate_program(program.program_id)
        updated = store.get_program(program.program_id)
        assert updated is not None
        assert updated.status == ProgramState.active

    def test_pause_active_program(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Pause test")
        pm.activate_program(program.program_id)
        pm.pause_program(program.program_id)
        updated = store.get_program(program.program_id)
        assert updated is not None
        assert updated.status == ProgramState.paused

    def test_resume_paused_program(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Resume test")
        pm.activate_program(program.program_id)
        pm.pause_program(program.program_id)
        pm.resume_program(program.program_id)
        updated = store.get_program(program.program_id)
        assert updated is not None
        assert updated.status == ProgramState.active

    def test_complete_active_program(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Complete test")
        pm.activate_program(program.program_id)
        pm.complete_program(program.program_id)
        updated = store.get_program(program.program_id)
        assert updated is not None
        assert updated.status == ProgramState.completed

    def test_fail_active_program(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Fail test")
        pm.activate_program(program.program_id)
        pm.fail_program(program.program_id)
        updated = store.get_program(program.program_id)
        assert updated is not None
        assert updated.status == ProgramState.failed

    def test_fail_draft_program(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Draft fail test")
        pm.fail_program(program.program_id)
        updated = store.get_program(program.program_id)
        assert updated is not None
        assert updated.status == ProgramState.failed

    def test_fail_paused_program(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Paused fail test")
        pm.activate_program(program.program_id)
        pm.pause_program(program.program_id)
        pm.fail_program(program.program_id)
        updated = store.get_program(program.program_id)
        assert updated is not None
        assert updated.status == ProgramState.failed

    def test_full_lifecycle(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(
            goal="Full lifecycle",
            title="Lifecycle Program",
            priority="high",
        )
        team = pm.add_team(program_id=program.program_id, title="Core Team")
        ms1 = pm.add_milestone(team_id=team.team_id, title="Research")
        pm.add_milestone(
            team_id=team.team_id,
            title="Implementation",
            dependency_ids=[ms1.milestone_id],
            acceptance_criteria=["All tests pass"],
        )
        pm.activate_program(program.program_id)
        pm.pause_program(program.program_id)
        pm.resume_program(program.program_id)
        pm.complete_program(program.program_id)

        final = store.get_program(program.program_id)
        assert final is not None
        assert final.status == ProgramState.completed
        assert final.priority == "high"
        assert len(final.milestone_ids) == 2


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    def test_activate_completed_raises(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Bad transition")
        pm.activate_program(program.program_id)
        pm.complete_program(program.program_id)
        with pytest.raises(ProgramManagerError, match="Invalid program transition"):
            pm.activate_program(program.program_id)

    def test_pause_draft_raises(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Bad transition")
        with pytest.raises(ProgramManagerError, match="Invalid program transition"):
            pm.pause_program(program.program_id)

    def test_complete_draft_raises(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Bad transition")
        with pytest.raises(ProgramManagerError, match="Invalid program transition"):
            pm.complete_program(program.program_id)

    def test_resume_draft_raises(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Bad transition")
        with pytest.raises(ProgramManagerError, match="Cannot resume program"):
            pm.resume_program(program.program_id)

    def test_pause_completed_raises(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Bad transition")
        pm.activate_program(program.program_id)
        pm.complete_program(program.program_id)
        with pytest.raises(ProgramManagerError, match="Invalid program transition"):
            pm.pause_program(program.program_id)

    def test_complete_failed_raises(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Bad transition")
        pm.activate_program(program.program_id)
        pm.fail_program(program.program_id)
        with pytest.raises(ProgramManagerError, match="Invalid program transition"):
            pm.complete_program(program.program_id)

    def test_nonexistent_program_raises(self, pm: ProgramManager) -> None:
        with pytest.raises(ProgramManagerError, match="Program not found"):
            pm.activate_program("program_nonexistent")


# ---------------------------------------------------------------------------
# get_program_with_teams
# ---------------------------------------------------------------------------


class TestGetProgramWithTeams:
    def test_empty_program(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Empty test")
        result = pm.get_program_with_teams(program.program_id)
        assert result["program_id"] == program.program_id
        assert result["goal"] == "Empty test"
        assert result["status"] == ProgramState.draft
        assert result["teams"] == []

    def test_program_with_teams_and_milestones(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Full structure")
        team_a = pm.add_team(
            program_id=program.program_id,
            title="Team A",
            role_assembly={"lead": "agent_1"},
            context_boundary=["src/module_a"],
        )
        team_b = pm.add_team(
            program_id=program.program_id,
            title="Team B",
            workspace_id="ws_custom",
        )
        ms1 = pm.add_milestone(
            team_id=team_a.team_id,
            title="Research",
            description="Initial research",
            acceptance_criteria=["Report delivered"],
        )
        pm.add_milestone(
            team_id=team_a.team_id,
            title="Implementation",
            dependency_ids=[ms1.milestone_id],
        )
        pm.add_milestone(team_id=team_b.team_id, title="Testing")

        result = pm.get_program_with_teams(program.program_id)
        assert len(result["teams"]) == 2

        # Find team_a in result (ordering may vary)
        team_a_data = next(t for t in result["teams"] if t["team_id"] == team_a.team_id)
        assert team_a_data["title"] == "Team A"
        assert team_a_data["role_assembly"] == {
            "lead": {"role": "lead", "count": 1, "config": {"legacy_value": "agent_1"}}
        }
        assert team_a_data["context_boundary"] == ["src/module_a"]
        assert len(team_a_data["milestones"]) == 2

        # Check milestone ordering (created_at ASC)
        ms_titles = [m["title"] for m in team_a_data["milestones"]]
        assert ms_titles == ["Research", "Implementation"]

        # Check dependency data
        impl_ms = next(m for m in team_a_data["milestones"] if m["title"] == "Implementation")
        assert impl_ms["dependency_ids"] == [ms1.milestone_id]

        # Check team_b
        team_b_data = next(t for t in result["teams"] if t["team_id"] == team_b.team_id)
        assert team_b_data["workspace_id"] == "ws_custom"
        assert len(team_b_data["milestones"]) == 1
        assert team_b_data["milestones"][0]["title"] == "Testing"

    def test_nonexistent_program_raises(self, pm: ProgramManager) -> None:
        with pytest.raises(ProgramManagerError, match="Program not found"):
            pm.get_program_with_teams("program_nonexistent")

    def test_result_includes_all_program_fields(self, pm: ProgramManager) -> None:
        program = pm.compile_program(
            goal="All fields",
            title="Full Program",
            priority="high",
            budget_limits={"tokens": 100_000},
            metadata={"env": "test"},
        )
        result = pm.get_program_with_teams(program.program_id)
        assert result["title"] == "Full Program"
        assert result["priority"] == "high"
        assert result["budget_limits"] == {"tokens": 100_000}
        assert result["metadata"] == {"env": "test"}
        assert result["description"] == ""
        assert "created_at" in result
        assert "updated_at" in result
        assert "milestone_ids" in result


# ---------------------------------------------------------------------------
# list_active_programs
# ---------------------------------------------------------------------------


class TestListActivePrograms:
    def test_empty(self, pm: ProgramManager) -> None:
        result = pm.list_active_programs()
        assert result == []

    def test_includes_draft_active_paused(self, pm: ProgramManager) -> None:
        p1 = pm.compile_program(goal="Draft")
        p2 = pm.compile_program(goal="Active")
        pm.activate_program(p2.program_id)
        p3 = pm.compile_program(goal="Paused")
        pm.activate_program(p3.program_id)
        pm.pause_program(p3.program_id)

        active = pm.list_active_programs()
        ids = {p.program_id for p in active}
        assert p1.program_id in ids
        assert p2.program_id in ids
        assert p3.program_id in ids

    def test_excludes_completed_and_failed(self, pm: ProgramManager) -> None:
        p1 = pm.compile_program(goal="Completed")
        pm.activate_program(p1.program_id)
        pm.complete_program(p1.program_id)

        p2 = pm.compile_program(goal="Failed")
        pm.activate_program(p2.program_id)
        pm.fail_program(p2.program_id)

        p3 = pm.compile_program(goal="Still active")

        active = pm.list_active_programs()
        ids = {p.program_id for p in active}
        assert p1.program_id not in ids
        assert p2.program_id not in ids
        assert p3.program_id in ids

    def test_ordered_by_created_at_desc(self, pm: ProgramManager) -> None:
        p1 = pm.compile_program(goal="First")
        pm.compile_program(goal="Second")
        p3 = pm.compile_program(goal="Third")

        active = pm.list_active_programs()
        # Most recent first
        assert active[0].program_id == p3.program_id
        assert active[-1].program_id == p1.program_id


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


class TestProgramManagerEvents:
    def test_activate_emits_event(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Event test")
        pm.activate_program(program.program_id)
        events = store.list_events(event_type="program.active")
        assert len(events) >= 1
        assert events[0]["entity_id"] == program.program_id

    def test_pause_emits_event(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Event test")
        pm.activate_program(program.program_id)
        pm.pause_program(program.program_id)
        events = store.list_events(event_type="program.paused")
        assert len(events) >= 1

    def test_complete_emits_event(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Event test")
        pm.activate_program(program.program_id)
        pm.complete_program(program.program_id)
        events = store.list_events(event_type="program.completed")
        assert len(events) >= 1

    def test_fail_emits_event(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Event test")
        pm.activate_program(program.program_id)
        pm.fail_program(program.program_id)
        events = store.list_events(event_type="program.failed")
        assert len(events) >= 1

    def test_add_team_emits_event(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Event test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        events = store.list_events(event_type="team.created")
        assert len(events) >= 1
        assert events[0]["entity_id"] == team.team_id

    def test_add_milestone_emits_events(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Event test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms = pm.add_milestone(team_id=team.team_id, title="MS 1")
        # Should emit both milestone.created and program.milestone_added
        ms_events = store.list_events(event_type="milestone.created")
        assert len(ms_events) >= 1
        assert ms_events[0]["entity_id"] == ms.milestone_id
        prog_events = store.list_events(event_type="program.milestone_added")
        assert len(prog_events) >= 1


# ---------------------------------------------------------------------------
# Edge cases and integration
# ---------------------------------------------------------------------------


class TestProgramManagerEdgeCases:
    def test_multiple_programs_independent(self, pm: ProgramManager) -> None:
        p1 = pm.compile_program(goal="Program 1")
        p2 = pm.compile_program(goal="Program 2")
        t1 = pm.add_team(program_id=p1.program_id, title="Team for P1")
        t2 = pm.add_team(program_id=p2.program_id, title="Team for P2")
        pm.add_milestone(team_id=t1.team_id, title="MS P1")
        pm.add_milestone(team_id=t2.team_id, title="MS P2")

        r1 = pm.get_program_with_teams(p1.program_id)
        r2 = pm.get_program_with_teams(p2.program_id)
        assert len(r1["teams"]) == 1
        assert len(r2["teams"]) == 1
        assert r1["teams"][0]["title"] == "Team for P1"
        assert r2["teams"][0]["title"] == "Team for P2"

    def test_program_with_no_milestones(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="No milestones")
        pm.add_team(program_id=program.program_id, title="Empty Team")
        result = pm.get_program_with_teams(program.program_id)
        assert len(result["teams"]) == 1
        assert result["teams"][0]["milestones"] == []

    def test_cross_team_milestone_dependency(self, pm: ProgramManager) -> None:
        """Milestones from different teams can depend on each other."""
        program = pm.compile_program(goal="Cross-team deps")
        team_a = pm.add_team(program_id=program.program_id, title="Team A")
        team_b = pm.add_team(program_id=program.program_id, title="Team B")
        ms_a = pm.add_milestone(team_id=team_a.team_id, title="API Design")
        ms_b = pm.add_milestone(
            team_id=team_b.team_id,
            title="Client Integration",
            dependency_ids=[ms_a.milestone_id],
        )
        assert ms_b.dependency_ids == [ms_a.milestone_id]

    def test_store_roundtrip_consistency(self, pm: ProgramManager, store: KernelStore) -> None:
        """Verify that ProgramManager and raw store produce consistent state."""
        program = pm.compile_program(
            goal="Consistency test",
            title="Consistent",
            priority="high",
            budget_limits={"tokens": 10_000},
        )
        team = pm.add_team(
            program_id=program.program_id,
            title="Team X",
            role_assembly={"lead": "agent_x"},
        )
        ms = pm.add_milestone(
            team_id=team.team_id,
            title="Milestone X",
            acceptance_criteria=["Verified"],
        )
        pm.activate_program(program.program_id)

        # Verify via store directly
        raw_program = store.get_program(program.program_id)
        assert raw_program is not None
        assert raw_program.status == ProgramState.active
        assert raw_program.priority == "high"
        assert ms.milestone_id in raw_program.milestone_ids

        raw_team = store.get_team(team.team_id)
        assert raw_team is not None
        # Legacy string values are wrapped into RoleSlotSpec on round-trip.
        from hermit.kernel.task.models.team import RoleSlotSpec

        assert isinstance(raw_team.role_assembly["lead"], RoleSlotSpec)
        assert raw_team.role_assembly["lead"].config == {"legacy_value": "agent_x"}

        raw_ms = store.get_milestone(ms.milestone_id)
        assert raw_ms is not None
        assert raw_ms.acceptance_criteria == ["Verified"]


# ---------------------------------------------------------------------------
# compile_program_with_structure (prompt leverage)
# ---------------------------------------------------------------------------


class TestCompileProgramWithStructure:
    def test_basic_compilation_with_teams(self, pm: ProgramManager) -> None:
        result = pm.compile_program_with_structure(
            goal="Build auth system",
            team_specs=[
                {"title": "Backend Team"},
                {"title": "Frontend Team"},
            ],
        )
        assert isinstance(result, CompilationResult)
        assert result.program.goal == "Build auth system"
        assert result.program.status == ProgramState.draft
        assert len(result.teams) == 2
        assert result.teams[0].title == "Backend Team"
        assert result.teams[1].title == "Frontend Team"

    def test_compilation_with_milestones(self, pm: ProgramManager) -> None:
        result = pm.compile_program_with_structure(
            goal="Ship v2",
            team_specs=[
                {
                    "title": "Core Team",
                    "milestones": [
                        {
                            "title": "Research",
                            "description": "Gather requirements",
                            "acceptance_criteria": ["Report delivered"],
                        },
                        {
                            "title": "Implement",
                            "dependency_titles": ["Research"],
                            "acceptance_criteria": ["Tests pass"],
                        },
                    ],
                },
            ],
        )
        assert len(result.milestones) == 2
        assert result.milestones[0].title == "Research"
        assert result.milestones[1].title == "Implement"
        # Implement depends on Research
        assert result.milestones[0].milestone_id in result.milestones[1].dependency_ids

    def test_generates_task_contracts_for_ready_milestones(self, pm: ProgramManager) -> None:
        result = pm.compile_program_with_structure(
            goal="Task gen test",
            team_specs=[
                {
                    "title": "Team A",
                    "milestones": [
                        {"title": "Phase 1"},  # no deps — ready
                        {"title": "Phase 2", "dependency_titles": ["Phase 1"]},  # blocked
                    ],
                },
            ],
        )
        # Only Phase 1 should generate a contract (Phase 2 has unmet dep)
        assert len(result.task_contracts) == 1
        assert result.task_contracts[0].goal == "Phase 1"

    def test_no_team_specs_produces_empty_structure(self, pm: ProgramManager) -> None:
        result = pm.compile_program_with_structure(goal="Empty structure")
        assert result.teams == []
        assert result.milestones == []
        assert result.task_contracts == []

    def test_custom_fields_propagated(self, pm: ProgramManager) -> None:
        result = pm.compile_program_with_structure(
            goal="Custom fields",
            title="Custom Program",
            priority="high",
            budget_limits={"tokens": 50_000},
            metadata={"env": "prod"},
        )
        assert result.program.title == "Custom Program"
        assert result.program.priority == "high"
        assert result.program.budget_limits == {"tokens": 50_000}
        assert result.program.metadata == {"env": "prod"}

    def test_workspace_and_context_boundary(self, pm: ProgramManager) -> None:
        result = pm.compile_program_with_structure(
            goal="WS test",
            team_specs=[
                {
                    "title": "Team A",
                    "workspace_id": "ws_custom",
                    "context_boundary": ["src/api/"],
                },
            ],
        )
        assert result.teams[0].workspace_id == "ws_custom"
        assert result.teams[0].context_boundary == ["src/api/"]

    def test_multiple_teams_with_milestones(self, pm: ProgramManager) -> None:
        result = pm.compile_program_with_structure(
            goal="Multi-team",
            team_specs=[
                {
                    "title": "Backend",
                    "milestones": [{"title": "API Design"}],
                },
                {
                    "title": "Frontend",
                    "milestones": [{"title": "UI Mockup"}],
                },
            ],
        )
        assert len(result.teams) == 2
        assert len(result.milestones) == 2
        # Both milestones are ready (no deps) so both get contracts
        assert len(result.task_contracts) == 2


# ---------------------------------------------------------------------------
# generate_tasks (Task Generator)
# ---------------------------------------------------------------------------


class TestGenerateTasks:
    def test_generates_for_ready_milestones(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Task gen")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        pm.add_milestone(
            team_id=team.team_id,
            title="Ready MS",
            acceptance_criteria=["Tests pass"],
        )

        contracts = pm.generate_tasks(program.program_id)
        assert len(contracts) == 1
        assert isinstance(contracts[0], TaskContractPacket)
        assert contracts[0].goal == "Ready MS"
        assert contracts[0].acceptance_criteria == ["Tests pass"]
        assert contracts[0].scope["program_id"] == program.program_id
        assert contracts[0].scope["team_id"] == team.team_id

    def test_skips_milestones_with_unmet_deps(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Dep test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms1 = pm.add_milestone(team_id=team.team_id, title="Phase 1")
        pm.add_milestone(
            team_id=team.team_id,
            title="Phase 2",
            dependency_ids=[ms1.milestone_id],
        )

        contracts = pm.generate_tasks(program.program_id)
        # Only Phase 1 is ready
        assert len(contracts) == 1
        assert contracts[0].goal == "Phase 1"

    def test_unblocks_after_dep_completion(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Unblock test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms1 = pm.add_milestone(team_id=team.team_id, title="Phase 1")
        pm.add_milestone(
            team_id=team.team_id,
            title="Phase 2",
            dependency_ids=[ms1.milestone_id],
        )

        # Complete Phase 1
        store.update_milestone_status(ms1.milestone_id, MilestoneState.COMPLETED)

        contracts = pm.generate_tasks(program.program_id)
        # Phase 2 should now be ready (Phase 1 completed, Phase 2 still pending)
        assert len(contracts) == 1
        assert contracts[0].goal == "Phase 2"

    def test_returns_empty_for_terminal_program(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Terminal test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        pm.add_milestone(team_id=team.team_id, title="Ready MS")
        pm.activate_program(program.program_id)
        pm.complete_program(program.program_id)

        contracts = pm.generate_tasks(program.program_id)
        assert contracts == []

    def test_returns_empty_for_no_milestones(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="No milestones")
        pm.add_team(program_id=program.program_id, title="Team A")

        contracts = pm.generate_tasks(program.program_id)
        assert contracts == []

    def test_skips_active_milestones(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Active ms test")
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms = pm.add_milestone(team_id=team.team_id, title="Already Active")
        store.update_milestone_status(ms.milestone_id, MilestoneState.ACTIVE)

        contracts = pm.generate_tasks(program.program_id)
        assert contracts == []

    def test_contract_scope_includes_context_boundary(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Scope test")
        team = pm.add_team(
            program_id=program.program_id,
            title="Team A",
            context_boundary=["src/kernel/"],
        )
        pm.add_milestone(team_id=team.team_id, title="Scoped Work")

        contracts = pm.generate_tasks(program.program_id)
        assert contracts[0].scope["context_boundary"] == ["src/kernel/"]

    def test_nonexistent_program_raises(self, pm: ProgramManager) -> None:
        with pytest.raises(ProgramManagerError, match="Program not found"):
            pm.generate_tasks("program_nonexistent")

    def test_multi_team_generation(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Multi team gen")
        t1 = pm.add_team(program_id=program.program_id, title="Team A")
        t2 = pm.add_team(program_id=program.program_id, title="Team B")
        pm.add_milestone(team_id=t1.team_id, title="MS A")
        pm.add_milestone(team_id=t2.team_id, title="MS B")

        contracts = pm.generate_tasks(program.program_id)
        assert len(contracts) == 2
        goals = {c.goal for c in contracts}
        assert goals == {"MS A", "MS B"}


# ---------------------------------------------------------------------------
# generate_followups (Follow-up Generator)
# ---------------------------------------------------------------------------


class TestGenerateFollowups:
    def test_rejected_without_issues_produces_retry(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Followup test")
        pm.activate_program(program.program_id)

        verdict = create_verdict(
            task_id="task_123",
            verdict="rejected",
            acceptance_check={"tests_pass": False, "lint_clean": True},
        )
        followups = pm.generate_followups(program_id=program.program_id, verdict=verdict)
        assert len(followups) == 1
        assert followups[0].action == "retry"
        assert isinstance(followups[0], FollowUpTask)
        assert followups[0].contract.acceptance_criteria == ["tests_pass"]
        assert followups[0].source_verdict is verdict

    def test_rejected_with_issues_produces_mitigate(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Issue followup")
        pm.activate_program(program.program_id)

        verdict = create_verdict(
            task_id="task_456",
            verdict="rejected",
            acceptance_check={"coverage": False},
            issues=[
                {"description": "Coverage dropped to 60%"},
                {"description": "Flaky test in auth module"},
            ],
        )
        followups = pm.generate_followups(program_id=program.program_id, verdict=verdict)
        assert len(followups) == 2
        assert all(f.action == "mitigate" for f in followups)
        assert "Coverage dropped" in followups[0].reason
        assert "Flaky test" in followups[1].reason

    def test_blocked_produces_escalate(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Blocked followup")
        pm.activate_program(program.program_id)

        verdict = create_verdict(
            task_id="task_789",
            verdict="blocked",
            recommended_next_action="Need API key from ops team",
        )
        followups = pm.generate_followups(program_id=program.program_id, verdict=verdict)
        assert len(followups) == 1
        assert followups[0].action == "escalate"
        assert followups[0].contract.risk_band == "high"
        assert followups[0].reason == "Need API key from ops team"

    def test_accepted_with_followups_produces_replan(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Replan followup")
        pm.activate_program(program.program_id)

        verdict = create_verdict(
            task_id="task_abc",
            verdict="accepted_with_followups",
            recommended_next_action="Add integration tests for edge cases",
        )
        followups = pm.generate_followups(program_id=program.program_id, verdict=verdict)
        assert len(followups) == 1
        assert followups[0].action == "replan"
        assert followups[0].contract.risk_band == "low"
        assert "integration tests" in followups[0].reason

    def test_accepted_produces_no_followups(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Clean accept")
        pm.activate_program(program.program_id)

        verdict = create_verdict(
            task_id="task_clean",
            verdict="accepted",
            acceptance_check={"tests_pass": True},
        )
        followups = pm.generate_followups(program_id=program.program_id, verdict=verdict)
        assert followups == []

    def test_terminal_program_returns_empty(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Terminal followup")
        pm.activate_program(program.program_id)
        pm.complete_program(program.program_id)

        verdict = create_verdict(task_id="task_x", verdict="rejected")
        followups = pm.generate_followups(program_id=program.program_id, verdict=verdict)
        assert followups == []

    def test_nonexistent_program_raises(self, pm: ProgramManager) -> None:
        verdict = create_verdict(task_id="task_y", verdict="rejected")
        with pytest.raises(ProgramManagerError, match="Program not found"):
            pm.generate_followups(program_id="program_ghost", verdict=verdict)

    def test_accepted_with_followups_no_recommendation(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="No recommendation")
        pm.activate_program(program.program_id)

        verdict = create_verdict(
            task_id="task_no_rec",
            verdict="accepted_with_followups",
            # No recommended_next_action
        )
        followups = pm.generate_followups(program_id=program.program_id, verdict=verdict)
        assert followups == []

    def test_mitigate_contract_includes_scope(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Scope check")
        pm.activate_program(program.program_id)

        verdict = create_verdict(
            task_id="task_scope",
            verdict="rejected",
            issues=[{"description": "Performance regression"}],
        )
        followups = pm.generate_followups(program_id=program.program_id, verdict=verdict)
        assert followups[0].contract.scope["program_id"] == program.program_id
        assert followups[0].contract.scope["source_task_id"] == "task_scope"


# ---------------------------------------------------------------------------
# select_background_work (Background Work Selector)
# ---------------------------------------------------------------------------


class TestSelectBackgroundWork:
    def test_selects_ready_milestones(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Background test", priority="high")
        pm.activate_program(program.program_id)
        team = pm.add_team(program_id=program.program_id, title="Team A")
        pm.add_milestone(
            team_id=team.team_id,
            title="Ready Work",
            acceptance_criteria=["Done"],
        )

        items = pm.select_background_work()
        assert len(items) == 1
        assert isinstance(items[0], BackgroundWorkItem)
        assert items[0].contract.goal == "Ready Work"
        assert 0.0 <= items[0].score <= 1.0
        assert "Ready Work" in items[0].rationale

    def test_respects_max_items(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Max items test")
        pm.activate_program(program.program_id)
        team = pm.add_team(program_id=program.program_id, title="Team A")
        for i in range(10):
            pm.add_milestone(team_id=team.team_id, title=f"MS {i}")

        items = pm.select_background_work(max_items=3)
        assert len(items) == 3

    def test_excludes_blocked_milestones(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Blocked milestone test")
        pm.activate_program(program.program_id)
        team = pm.add_team(program_id=program.program_id, title="Team A")
        ms1 = pm.add_milestone(team_id=team.team_id, title="Phase 1")
        pm.add_milestone(
            team_id=team.team_id,
            title="Phase 2",
            dependency_ids=[ms1.milestone_id],
        )

        items = pm.select_background_work()
        goals = {item.contract.goal for item in items}
        assert "Phase 1" in goals
        assert "Phase 2" not in goals

    def test_empty_when_no_programs(self, pm: ProgramManager) -> None:
        items = pm.select_background_work()
        assert items == []

    def test_empty_when_all_terminal(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Terminal bg test")
        pm.activate_program(program.program_id)
        team = pm.add_team(program_id=program.program_id, title="Team A")
        pm.add_milestone(team_id=team.team_id, title="Ready")
        pm.complete_program(program.program_id)

        items = pm.select_background_work()
        assert items == []

    def test_higher_priority_scores_higher(self, pm: ProgramManager) -> None:
        p_high = pm.compile_program(goal="High prio", priority="high")
        pm.activate_program(p_high.program_id)
        t_high = pm.add_team(program_id=p_high.program_id, title="Team H")
        pm.add_milestone(team_id=t_high.team_id, title="High Work")

        p_low = pm.compile_program(goal="Low prio", priority="low")
        pm.activate_program(p_low.program_id)
        t_low = pm.add_team(program_id=p_low.program_id, title="Team L")
        pm.add_milestone(team_id=t_low.team_id, title="Low Work")

        items = pm.select_background_work()
        assert len(items) == 2
        # First item should be higher priority
        assert items[0].score >= items[1].score

    def test_active_programs_score_higher_than_draft(self, pm: ProgramManager) -> None:
        p_draft = pm.compile_program(goal="Draft")
        t_draft = pm.add_team(program_id=p_draft.program_id, title="Team D")
        pm.add_milestone(team_id=t_draft.team_id, title="Draft Work")

        p_active = pm.compile_program(goal="Active")
        pm.activate_program(p_active.program_id)
        t_active = pm.add_team(program_id=p_active.program_id, title="Team A")
        pm.add_milestone(team_id=t_active.team_id, title="Active Work")

        items = pm.select_background_work()
        assert len(items) == 2
        # Active should score higher due to state_bonus
        active_item = next(i for i in items if i.contract.goal == "Active Work")
        draft_item = next(i for i in items if i.contract.goal == "Draft Work")
        assert active_item.score > draft_item.score

    def test_skips_non_active_teams(self, pm: ProgramManager, store: KernelStore) -> None:
        program = pm.compile_program(goal="Paused team test")
        pm.activate_program(program.program_id)
        team = pm.add_team(program_id=program.program_id, title="Paused Team")
        pm.add_milestone(team_id=team.team_id, title="Paused Work")
        # Pause the team directly via store
        store._get_conn().execute(
            "UPDATE teams SET status = ? WHERE team_id = ?",
            (TeamState.PAUSED, team.team_id),
        )

        items = pm.select_background_work()
        assert items == []

    def test_multi_program_aggregation(self, pm: ProgramManager) -> None:
        for i in range(3):
            p = pm.compile_program(goal=f"Program {i}")
            pm.activate_program(p.program_id)
            t = pm.add_team(program_id=p.program_id, title=f"Team {i}")
            pm.add_milestone(team_id=t.team_id, title=f"Work {i}")

        items = pm.select_background_work(max_items=10)
        assert len(items) == 3

    def test_contract_fields_populated(self, pm: ProgramManager) -> None:
        program = pm.compile_program(goal="Fields test")
        pm.activate_program(program.program_id)
        team = pm.add_team(
            program_id=program.program_id,
            title="Team A",
            workspace_id="ws_test",
        )
        pm.add_milestone(
            team_id=team.team_id,
            title="Detailed Work",
            acceptance_criteria=["Criterion A", "Criterion B"],
        )

        items = pm.select_background_work()
        contract = items[0].contract
        assert contract.scope["program_id"] == program.program_id
        assert contract.scope["workspace_id"] == "ws_test"
        assert contract.acceptance_criteria == ["Criterion A", "Criterion B"]
        assert contract.risk_band == "low"
