"""Tests for ProgramToolService — MCP-ready bridge for program/team operations."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.program_tools import ProgramToolService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture()
def svc(store: KernelStore) -> ProgramToolService:
    return ProgramToolService(store)


# ---------------------------------------------------------------------------
# create_program
# ---------------------------------------------------------------------------


class TestCreateProgram:
    def test_creates_program_with_defaults(self, svc: ProgramToolService) -> None:
        result = svc.create_program(goal="Ship v1")
        assert "error" not in result
        assert result["program_id"].startswith("program_")
        assert result["goal"] == "Ship v1"
        assert result["status"] == "draft"
        assert result["priority"] == "normal"
        # Title defaults to truncated goal
        assert result["title"] == "Ship v1"

    def test_creates_program_with_explicit_title(self, svc: ProgramToolService) -> None:
        result = svc.create_program(goal="Ship v1", title="Project Alpha")
        assert result["title"] == "Project Alpha"

    def test_creates_program_with_priority(self, svc: ProgramToolService) -> None:
        result = svc.create_program(goal="Ship v1", priority="high")
        assert result["priority"] == "high"

    def test_empty_goal_returns_error(self, svc: ProgramToolService) -> None:
        result = svc.create_program(goal="")
        assert "error" in result

    def test_long_goal_truncated_in_title(self, svc: ProgramToolService) -> None:
        long_goal = "A" * 200
        result = svc.create_program(goal=long_goal)
        assert len(result["title"]) <= 80

    def test_created_at_populated(self, svc: ProgramToolService) -> None:
        before = time.time()
        result = svc.create_program(goal="Test")
        assert result["created_at"] >= before


# ---------------------------------------------------------------------------
# add_team_to_program
# ---------------------------------------------------------------------------


class TestAddTeamToProgram:
    def test_adds_team(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        result = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Frontend Team",
        )
        assert "error" not in result
        assert result["team_id"].startswith("team_")
        assert result["program_id"] == prog["program_id"]
        assert result["title"] == "Frontend Team"
        assert result["status"] == "active"

    def test_auto_workspace_id(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        result = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Backend",
        )
        assert result["workspace_id"].startswith("ws-")

    def test_explicit_workspace_id(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        result = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Backend",
            workspace_id="custom-ws",
        )
        assert result["workspace_id"] == "custom-ws"

    def test_missing_program_returns_error(self, svc: ProgramToolService) -> None:
        result = svc.add_team_to_program(
            program_id="program_nonexistent",
            title="Ghost Team",
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_empty_program_id_returns_error(self, svc: ProgramToolService) -> None:
        result = svc.add_team_to_program(program_id="", title="X")
        assert "error" in result

    def test_empty_title_returns_error(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        result = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="",
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# add_milestone
# ---------------------------------------------------------------------------


class TestAddMilestone:
    def test_adds_milestone(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        team = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Team A",
        )
        result = svc.add_milestone(
            team_id=team["team_id"],
            title="API complete",
        )
        assert "error" not in result
        assert result["milestone_id"].startswith("milestone_")
        assert result["title"] == "API complete"
        assert result["status"] == "pending"

    def test_milestone_with_acceptance_criteria(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        team = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Team A",
        )
        criteria = ["All endpoints tested", "Docs updated"]
        result = svc.add_milestone(
            team_id=team["team_id"],
            title="API complete",
            acceptance_criteria=criteria,
        )
        assert result["acceptance_criteria"] == criteria

    def test_milestone_linked_to_program(self, svc: ProgramToolService, store: KernelStore) -> None:
        prog = svc.create_program(goal="Ship v1")
        team = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Team A",
        )
        ms = svc.add_milestone(team_id=team["team_id"], title="MS1")
        # Verify the milestone is linked to the program record.
        program = store.get_program(prog["program_id"])
        assert program is not None
        assert ms["milestone_id"] in program.milestone_ids

    def test_missing_team_returns_error(self, svc: ProgramToolService) -> None:
        result = svc.add_milestone(team_id="team_ghost", title="X")
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_empty_team_id_returns_error(self, svc: ProgramToolService) -> None:
        result = svc.add_milestone(team_id="", title="X")
        assert "error" in result

    def test_empty_title_returns_error(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        team = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Team A",
        )
        result = svc.add_milestone(team_id=team["team_id"], title="")
        assert "error" in result


# ---------------------------------------------------------------------------
# get_program_status
# ---------------------------------------------------------------------------


class TestGetProgramStatus:
    def test_returns_status_for_existing_program(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1", title="Alpha")
        result = svc.get_program_status(program_id=prog["program_id"])
        assert "error" not in result
        assert result["program_id"] == prog["program_id"]
        assert result["title"] == "Alpha"
        assert result["overall_state"] == "draft"
        assert result["progress_pct"] == 0.0
        assert result["awaiting_human"] is False

    def test_missing_program_returns_error(self, svc: ProgramToolService) -> None:
        result = svc.get_program_status(program_id="program_nonexistent")
        assert "error" in result

    def test_empty_program_id_returns_error(self, svc: ProgramToolService) -> None:
        result = svc.get_program_status(program_id="")
        assert "error" in result

    def test_includes_active_teams_count(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Team A",
        )
        svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Team B",
        )
        result = svc.get_program_status(program_id=prog["program_id"])
        # Falls back to program record path since there's no root task.
        assert result["active_teams"] == 2


# ---------------------------------------------------------------------------
# list_programs
# ---------------------------------------------------------------------------


class TestListPrograms:
    def test_list_empty(self, svc: ProgramToolService) -> None:
        result = svc.list_programs()
        assert result["count"] == 0
        assert result["programs"] == []

    def test_list_multiple(self, svc: ProgramToolService) -> None:
        svc.create_program(goal="Goal A")
        svc.create_program(goal="Goal B")
        result = svc.list_programs()
        assert result["count"] == 2

    def test_filter_by_status(self, svc: ProgramToolService, store: KernelStore) -> None:
        p = svc.create_program(goal="Goal A")
        svc.create_program(goal="Goal B")
        store.update_program_status(p["program_id"], "active")

        active = svc.list_programs(status="active")
        assert active["count"] == 1
        assert active["programs"][0]["status"] == "active"

        draft = svc.list_programs(status="draft")
        assert draft["count"] == 1

    def test_limit_respected(self, svc: ProgramToolService) -> None:
        for i in range(5):
            svc.create_program(goal=f"Goal {i}")
        result = svc.list_programs(limit=3)
        assert result["count"] == 3

    def test_limit_clamped(self, svc: ProgramToolService) -> None:
        # Ensure limit is clamped to [1, 100].
        result = svc.list_programs(limit=0)
        assert "error" not in result

        result = svc.list_programs(limit=999)
        assert "error" not in result

    def test_goal_truncated_in_listing(self, svc: ProgramToolService) -> None:
        long_goal = "X" * 200
        svc.create_program(goal=long_goal)
        result = svc.list_programs()
        assert len(result["programs"][0]["goal"]) <= 120


# ---------------------------------------------------------------------------
# get_team_status
# ---------------------------------------------------------------------------


class TestGetTeamStatus:
    def test_returns_team_status(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        team = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Team A",
        )
        result = svc.get_team_status(team_id=team["team_id"])
        assert "error" not in result
        assert result["team_id"] == team["team_id"]
        assert result["title"] == "Team A"
        assert "milestones" in result

    def test_team_with_milestones(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        team = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Team A",
        )
        svc.add_milestone(team_id=team["team_id"], title="MS1")
        svc.add_milestone(team_id=team["team_id"], title="MS2")
        result = svc.get_team_status(team_id=team["team_id"])
        assert len(result["milestones"]) == 2

    def test_missing_team_returns_error(self, svc: ProgramToolService) -> None:
        result = svc.get_team_status(team_id="team_ghost")
        assert "error" in result

    def test_empty_team_id_returns_error(self, svc: ProgramToolService) -> None:
        result = svc.get_team_status(team_id="")
        assert "error" in result


# ---------------------------------------------------------------------------
# control_program
# ---------------------------------------------------------------------------


class TestControlProgram:
    def test_activate_from_draft(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        result = svc.control_program(
            program_id=prog["program_id"],
            action="activate",
        )
        assert "error" not in result
        assert result["previous_status"] == "draft"
        assert result["new_status"] == "active"

    def test_pause_from_active(self, svc: ProgramToolService, store: KernelStore) -> None:
        prog = svc.create_program(goal="Ship v1")
        store.update_program_status(prog["program_id"], "active")
        result = svc.control_program(
            program_id=prog["program_id"],
            action="pause",
        )
        assert result["new_status"] == "paused"

    def test_resume_from_paused(self, svc: ProgramToolService, store: KernelStore) -> None:
        prog = svc.create_program(goal="Ship v1")
        store.update_program_status(prog["program_id"], "active")
        store.update_program_status(prog["program_id"], "paused")
        result = svc.control_program(
            program_id=prog["program_id"],
            action="resume",
        )
        assert result["new_status"] == "active"

    def test_complete_from_active(self, svc: ProgramToolService, store: KernelStore) -> None:
        prog = svc.create_program(goal="Ship v1")
        store.update_program_status(prog["program_id"], "active")
        result = svc.control_program(
            program_id=prog["program_id"],
            action="complete",
        )
        assert result["new_status"] == "completed"

    def test_complete_from_paused_requires_resume_first(
        self, svc: ProgramToolService, store: KernelStore
    ) -> None:
        prog = svc.create_program(goal="Ship v1")
        store.update_program_status(prog["program_id"], "active")
        store.update_program_status(prog["program_id"], "paused")
        # paused → completed is not valid; must resume first
        result = svc.control_program(
            program_id=prog["program_id"],
            action="complete",
        )
        assert "error" in result

    def test_invalid_transition_returns_error(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        # draft -> pause is not valid
        result = svc.control_program(
            program_id=prog["program_id"],
            action="pause",
        )
        assert "error" in result
        assert "draft" in result["error"]

    def test_terminal_state_returns_error(
        self, svc: ProgramToolService, store: KernelStore
    ) -> None:
        prog = svc.create_program(goal="Ship v1")
        # Walk through valid transitions to reach completed
        store.update_program_status(prog["program_id"], "active")
        store.update_program_status(prog["program_id"], "completed")
        result = svc.control_program(
            program_id=prog["program_id"],
            action="activate",
        )
        assert "error" in result

    def test_unknown_action_returns_error(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        result = svc.control_program(
            program_id=prog["program_id"],
            action="explode",
        )
        assert "error" in result
        assert "Unknown action" in result["error"]

    def test_missing_program_returns_error(self, svc: ProgramToolService) -> None:
        result = svc.control_program(
            program_id="program_nonexistent",
            action="activate",
        )
        assert "error" in result

    def test_empty_program_id_returns_error(self, svc: ProgramToolService) -> None:
        result = svc.control_program(program_id="", action="activate")
        assert "error" in result

    def test_empty_action_returns_error(self, svc: ProgramToolService) -> None:
        prog = svc.create_program(goal="Ship v1")
        result = svc.control_program(
            program_id=prog["program_id"],
            action="",
        )
        assert "error" in result

    def test_full_lifecycle(self, svc: ProgramToolService, store: KernelStore) -> None:
        prog = svc.create_program(goal="Full lifecycle test")
        assert prog["status"] == "draft"

        r1 = svc.control_program(program_id=prog["program_id"], action="activate")
        assert r1["new_status"] == "active"

        r2 = svc.control_program(program_id=prog["program_id"], action="pause")
        assert r2["new_status"] == "paused"

        r3 = svc.control_program(program_id=prog["program_id"], action="resume")
        assert r3["new_status"] == "active"

        r4 = svc.control_program(program_id=prog["program_id"], action="complete")
        assert r4["new_status"] == "completed"

        # Verify store reflects final state.
        program = store.get_program(prog["program_id"])
        assert program is not None
        assert program.status == "completed"


# ---------------------------------------------------------------------------
# get_approval_queue
# ---------------------------------------------------------------------------


class TestGetApprovalQueue:
    def test_empty_queue(self, svc: ProgramToolService) -> None:
        result = svc.get_approval_queue()
        assert "error" not in result
        assert result["total_count"] == 0
        assert result["pending_approvals"] == []


# ---------------------------------------------------------------------------
# Integration: full program setup
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_full_program_setup(self, svc: ProgramToolService, store: KernelStore) -> None:
        """Create a program, add teams, add milestones, check status."""
        prog = svc.create_program(goal="Build v2", title="V2 Release", priority="high")
        assert prog["program_id"]

        team_fe = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Frontend",
        )
        team_be = svc.add_team_to_program(
            program_id=prog["program_id"],
            title="Backend",
            workspace_id="ws-backend",
        )

        ms1 = svc.add_milestone(
            team_id=team_fe["team_id"],
            title="UI mockups",
            acceptance_criteria=["Figma reviewed"],
        )
        ms2 = svc.add_milestone(
            team_id=team_be["team_id"],
            title="API design",
        )

        # Check program status.
        status = svc.get_program_status(program_id=prog["program_id"])
        assert status["title"] == "V2 Release"
        assert status["active_teams"] == 2

        # Check team statuses.
        fe_status = svc.get_team_status(team_id=team_fe["team_id"])
        assert len(fe_status["milestones"]) == 1
        assert fe_status["milestones"][0]["title"] == "UI mockups"

        be_status = svc.get_team_status(team_id=team_be["team_id"])
        assert be_status["workspace"] == "ws-backend"
        assert len(be_status["milestones"]) == 1

        # List programs.
        listing = svc.list_programs()
        assert listing["count"] == 1
        assert listing["programs"][0]["priority"] == "high"

        # Verify program milestones tracked.
        program = store.get_program(prog["program_id"])
        assert program is not None
        assert ms1["milestone_id"] in program.milestone_ids
        assert ms2["milestone_id"] in program.milestone_ids
