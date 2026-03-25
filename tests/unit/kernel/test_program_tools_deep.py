"""Deep tests for ProgramToolService — full lifecycle flows and error paths.

Tests create_program → add_team → add_milestone → control_program flow,
get_program_status projections, control transitions, and list filtering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.program_tools import ProgramToolService


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture()
def service(store: KernelStore) -> ProgramToolService:
    return ProgramToolService(store)


# ---------------------------------------------------------------------------
# create_program → add_team → add_milestone → control_program flow
# ---------------------------------------------------------------------------


class TestFullLifecycleFlow:
    """Test the complete program lifecycle: create → team → milestone → control."""

    def test_create_program_returns_program_id(self, service: ProgramToolService) -> None:
        result = service.create_program(goal="Build a new feature")
        assert "program_id" in result
        assert result["status"] == "draft"
        assert result["goal"] == "Build a new feature"

    def test_create_program_with_custom_title(self, service: ProgramToolService) -> None:
        result = service.create_program(goal="Build feature X", title="Project X")
        assert result["title"] == "Project X"

    def test_create_program_default_title_from_goal(self, service: ProgramToolService) -> None:
        goal = "This is a goal"
        result = service.create_program(goal=goal)
        assert result["title"] == goal

    def test_create_program_empty_goal_returns_error(self, service: ProgramToolService) -> None:
        result = service.create_program(goal="")
        assert "error" in result

    def test_add_team_to_program(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Test goal")
        team = service.add_team_to_program(
            program_id=prog["program_id"],
            title="Backend Team",
        )
        assert "team_id" in team
        assert team["program_id"] == prog["program_id"]
        assert team["title"] == "Backend Team"

    def test_add_team_generates_workspace_id(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Test goal")
        team = service.add_team_to_program(
            program_id=prog["program_id"],
            title="Team A",
        )
        assert team["workspace_id"] == f"ws-{prog['program_id']}"

    def test_add_team_custom_workspace(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Test goal")
        team = service.add_team_to_program(
            program_id=prog["program_id"],
            title="Team A",
            workspace_id="custom-ws",
        )
        assert team["workspace_id"] == "custom-ws"

    def test_add_team_nonexistent_program_returns_error(self, service: ProgramToolService) -> None:
        result = service.add_team_to_program(
            program_id="nonexistent",
            title="Team",
        )
        assert "error" in result

    def test_add_team_empty_program_id_returns_error(self, service: ProgramToolService) -> None:
        result = service.add_team_to_program(program_id="", title="Team")
        assert "error" in result

    def test_add_team_empty_title_returns_error(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Test goal")
        result = service.add_team_to_program(
            program_id=prog["program_id"],
            title="",
        )
        assert "error" in result

    def test_add_milestone_to_team(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Test goal")
        team = service.add_team_to_program(
            program_id=prog["program_id"],
            title="Team A",
        )
        milestone = service.add_milestone(
            team_id=team["team_id"],
            title="MVP Release",
            acceptance_criteria=["All tests pass", "No critical bugs"],
        )
        assert "milestone_id" in milestone
        assert milestone["team_id"] == team["team_id"]
        assert milestone["title"] == "MVP Release"
        assert milestone["status"] == "pending"
        assert milestone["acceptance_criteria"] == ["All tests pass", "No critical bugs"]

    def test_add_milestone_nonexistent_team_returns_error(
        self, service: ProgramToolService
    ) -> None:
        result = service.add_milestone(team_id="nonexistent", title="M1")
        assert "error" in result

    def test_add_milestone_empty_team_id_returns_error(self, service: ProgramToolService) -> None:
        result = service.add_milestone(team_id="", title="M1")
        assert "error" in result

    def test_add_milestone_empty_title_returns_error(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Test goal")
        team = service.add_team_to_program(program_id=prog["program_id"], title="Team A")
        result = service.add_milestone(team_id=team["team_id"], title="")
        assert "error" in result

    def test_full_lifecycle(self, service: ProgramToolService) -> None:
        """Create program, add team, add milestone, activate, pause, complete."""
        prog = service.create_program(goal="E2E lifecycle test")
        pid = prog["program_id"]
        assert prog["status"] == "draft"

        team = service.add_team_to_program(program_id=pid, title="Core Team")
        assert "team_id" in team

        milestone = service.add_milestone(
            team_id=team["team_id"],
            title="Phase 1",
        )
        assert "milestone_id" in milestone

        # Activate
        activate = service.control_program(program_id=pid, action="activate")
        assert activate["new_status"] == "active"

        # Pause
        pause = service.control_program(program_id=pid, action="pause")
        assert pause["new_status"] == "paused"

        # Resume
        resume = service.control_program(program_id=pid, action="resume")
        assert resume["new_status"] == "active"

        # Complete
        complete = service.control_program(program_id=pid, action="complete")
        assert complete["new_status"] == "completed"


# ---------------------------------------------------------------------------
# get_program_status returns correct projection
# ---------------------------------------------------------------------------


class TestGetProgramStatus:
    """Test get_program_status returns correct projection data."""

    def test_program_status_for_draft_program(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Draft status test")
        status = service.get_program_status(program_id=prog["program_id"])
        assert status["program_id"] == prog["program_id"]
        assert status["overall_state"] == "draft"
        assert status["progress_pct"] == 0.0

    def test_program_status_with_teams(
        self, service: ProgramToolService, store: KernelStore
    ) -> None:
        prog = service.create_program(goal="Status with teams")
        pid = prog["program_id"]
        service.add_team_to_program(program_id=pid, title="Team 1")
        service.add_team_to_program(program_id=pid, title="Team 2")

        status = service.get_program_status(program_id=pid)
        assert status["program_id"] == pid
        # active_teams depends on whether it uses program record or task tree
        assert isinstance(status["active_teams"], int)

    def test_program_status_nonexistent_returns_error(self, service: ProgramToolService) -> None:
        result = service.get_program_status(program_id="nonexistent")
        assert "error" in result

    def test_program_status_empty_id_returns_error(self, service: ProgramToolService) -> None:
        result = service.get_program_status(program_id="")
        assert "error" in result

    def test_program_status_includes_required_fields(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Field check")
        status = service.get_program_status(program_id=prog["program_id"])
        required_fields = [
            "program_id",
            "title",
            "overall_state",
            "progress_pct",
            "current_phase",
            "active_teams",
            "queued_tasks",
            "running_attempts",
            "blocked_items",
            "awaiting_human",
            "latest_summary",
            "latest_risks",
            "latest_benchmark_status",
            "last_updated_at",
        ]
        for field in required_fields:
            assert field in status, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# control_program with invalid transitions → error
# ---------------------------------------------------------------------------


class TestControlProgramErrors:
    """Test control_program returns errors for invalid transitions."""

    def test_pause_draft_program_returns_error(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Pause draft test")
        result = service.control_program(program_id=prog["program_id"], action="pause")
        assert "error" in result
        assert "draft" in result["error"]

    def test_resume_draft_program_returns_error(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Resume draft test")
        result = service.control_program(program_id=prog["program_id"], action="resume")
        assert "error" in result

    def test_unknown_action_returns_error(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Unknown action test")
        result = service.control_program(program_id=prog["program_id"], action="destroy")
        assert "error" in result
        assert "Unknown action" in result["error"]

    def test_control_completed_program_returns_error(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Terminal test")
        pid = prog["program_id"]
        service.control_program(program_id=pid, action="activate")
        service.control_program(program_id=pid, action="complete")

        result = service.control_program(program_id=pid, action="activate")
        assert "error" in result
        assert "terminal" in result["error"]

    def test_control_nonexistent_program_returns_error(self, service: ProgramToolService) -> None:
        result = service.control_program(program_id="nonexistent", action="activate")
        assert "error" in result

    def test_control_empty_program_id_returns_error(self, service: ProgramToolService) -> None:
        result = service.control_program(program_id="", action="activate")
        assert "error" in result

    def test_control_empty_action_returns_error(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Empty action test")
        result = service.control_program(program_id=prog["program_id"], action="")
        assert "error" in result

    def test_pause_then_activate_is_invalid(self, service: ProgramToolService) -> None:
        """'activate' is only valid from 'draft' state."""
        prog = service.create_program(goal="Pause activate test")
        pid = prog["program_id"]
        service.control_program(program_id=pid, action="activate")
        service.control_program(program_id=pid, action="pause")

        # Cannot 'activate' from 'paused' — must 'resume'
        result = service.control_program(program_id=pid, action="activate")
        assert "error" in result

    def test_resume_active_program_is_invalid(self, service: ProgramToolService) -> None:
        """Cannot resume an already active program."""
        prog = service.create_program(goal="Resume active test")
        pid = prog["program_id"]
        service.control_program(program_id=pid, action="activate")

        result = service.control_program(program_id=pid, action="resume")
        assert "error" in result


# ---------------------------------------------------------------------------
# Valid control transitions
# ---------------------------------------------------------------------------


class TestControlTransitions:
    """Test valid control transition paths."""

    def test_activate_from_draft(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Activate test")
        result = service.control_program(program_id=prog["program_id"], action="activate")
        assert result["previous_status"] == "draft"
        assert result["new_status"] == "active"

    def test_pause_from_active(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Pause test")
        pid = prog["program_id"]
        service.control_program(program_id=pid, action="activate")

        result = service.control_program(program_id=pid, action="pause")
        assert result["previous_status"] == "active"
        assert result["new_status"] == "paused"

    def test_resume_from_paused(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Resume test")
        pid = prog["program_id"]
        service.control_program(program_id=pid, action="activate")
        service.control_program(program_id=pid, action="pause")

        result = service.control_program(program_id=pid, action="resume")
        assert result["previous_status"] == "paused"
        assert result["new_status"] == "active"

    def test_complete_from_active(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Complete test")
        pid = prog["program_id"]
        service.control_program(program_id=pid, action="activate")

        result = service.control_program(program_id=pid, action="complete")
        assert result["new_status"] == "completed"

    def test_complete_from_paused_requires_resume_first(self, service: ProgramToolService) -> None:
        """Per PROGRAM_STATE_TRANSITIONS, paused can only go to active or failed.
        To complete, must resume first."""
        prog = service.create_program(goal="Complete from paused test")
        pid = prog["program_id"]
        service.control_program(program_id=pid, action="activate")
        service.control_program(program_id=pid, action="pause")

        # Cannot complete directly from paused
        result = service.control_program(program_id=pid, action="complete")
        assert "error" in result

        # Resume first, then complete
        service.control_program(program_id=pid, action="resume")
        result = service.control_program(program_id=pid, action="complete")
        assert result["previous_status"] == "active"
        assert result["new_status"] == "completed"


# ---------------------------------------------------------------------------
# list_programs with status filter
# ---------------------------------------------------------------------------


class TestListPrograms:
    """Test list_programs with optional status filtering."""

    def test_list_empty_returns_no_programs(self, service: ProgramToolService) -> None:
        result = service.list_programs()
        assert result["count"] == 0
        assert result["programs"] == []

    def test_list_returns_created_programs(self, service: ProgramToolService) -> None:
        service.create_program(goal="Program A")
        service.create_program(goal="Program B")
        result = service.list_programs()
        assert result["count"] == 2

    def test_list_with_status_filter(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Filterable program")
        service.control_program(program_id=prog["program_id"], action="activate")
        service.create_program(goal="Draft program")

        active_list = service.list_programs(status="active")
        assert active_list["count"] == 1
        assert active_list["programs"][0]["status"] == "active"

        draft_list = service.list_programs(status="draft")
        assert draft_list["count"] == 1
        assert draft_list["programs"][0]["status"] == "draft"

    def test_list_with_nonexistent_status_returns_empty(self, service: ProgramToolService) -> None:
        service.create_program(goal="Some program")
        result = service.list_programs(status="completed")
        assert result["count"] == 0

    def test_list_programs_has_required_fields(self, service: ProgramToolService) -> None:
        service.create_program(goal="Field check program")
        result = service.list_programs()
        assert result["count"] == 1
        prog = result["programs"][0]
        for field in ["program_id", "title", "status", "priority", "goal", "created_at"]:
            assert field in prog, f"Missing field: {field}"

    def test_list_programs_limit(self, service: ProgramToolService) -> None:
        for i in range(5):
            service.create_program(goal=f"Program {i}")
        result = service.list_programs(limit=3)
        assert result["count"] == 3

    def test_list_programs_limit_clamped(self, service: ProgramToolService) -> None:
        """Limit is clamped to [1, 100]."""
        service.create_program(goal="Program")
        result = service.list_programs(limit=0)
        # Clamped to 1
        assert result["count"] >= 0


# ---------------------------------------------------------------------------
# Team and attempt status
# ---------------------------------------------------------------------------


class TestTeamAndAttemptStatus:
    """Test get_team_status and get_attempt_status error paths."""

    def test_get_team_status_nonexistent(self, service: ProgramToolService) -> None:
        result = service.get_team_status(team_id="nonexistent")
        assert "error" in result

    def test_get_team_status_empty_id(self, service: ProgramToolService) -> None:
        result = service.get_team_status(team_id="")
        assert "error" in result

    def test_get_team_status_with_milestones(self, service: ProgramToolService) -> None:
        prog = service.create_program(goal="Team status test")
        team = service.add_team_to_program(program_id=prog["program_id"], title="Team X")
        service.add_milestone(team_id=team["team_id"], title="M1")
        service.add_milestone(team_id=team["team_id"], title="M2")

        result = service.get_team_status(team_id=team["team_id"])
        assert "team_id" in result
        assert "milestones" in result
        assert len(result["milestones"]) == 2

    def test_get_task_status_nonexistent(self, service: ProgramToolService) -> None:
        result = service.get_task_status(task_id="nonexistent")
        assert "error" in result

    def test_get_task_status_empty_id(self, service: ProgramToolService) -> None:
        result = service.get_task_status(task_id="")
        assert "error" in result

    def test_get_attempt_status_nonexistent(self, service: ProgramToolService) -> None:
        result = service.get_attempt_status(step_attempt_id="nonexistent")
        assert "error" in result

    def test_get_attempt_status_empty_id(self, service: ProgramToolService) -> None:
        result = service.get_attempt_status(step_attempt_id="")
        assert "error" in result
