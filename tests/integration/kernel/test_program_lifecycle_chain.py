"""End-to-end validation: Program → Team → Milestone → State Transitions"""

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.program import ProgramState
from hermit.kernel.task.models.team import MilestoneState
from hermit.kernel.task.services.program_manager import ProgramManager


class TestProgramLifecycleChain:
    @pytest.fixture
    def store(self, tmp_path):
        return KernelStore(tmp_path / "test.db")

    @pytest.fixture
    def pm(self, store):
        return ProgramManager(store)

    def test_full_lifecycle(self, pm, store):
        # 1. Compile program
        prog = pm.compile_program(goal="Build v1", title="V1 Release")
        assert prog.status == ProgramState.draft

        # 2. Add 2 teams with milestones
        team_a = pm.add_team(program_id=prog.program_id, title="Backend")
        team_b = pm.add_team(program_id=prog.program_id, title="Frontend")

        ms1 = pm.add_milestone(
            team_id=team_a.team_id,
            title="API Design",
            acceptance_criteria=["OpenAPI spec reviewed"],
        )
        ms2 = pm.add_milestone(
            team_id=team_a.team_id,
            title="Implementation",
            dependency_ids=[ms1.milestone_id],
        )
        ms3 = pm.add_milestone(team_id=team_b.team_id, title="UI Components")

        # 3. Activate program
        pm.activate_program(prog.program_id)
        refreshed = store.get_program(prog.program_id)
        assert refreshed.status == ProgramState.active

        # 4. Complete milestones in order
        store.update_milestone_status(ms1.milestone_id, MilestoneState.ACTIVE)
        store.update_milestone_status(ms1.milestone_id, MilestoneState.COMPLETED)
        store.update_milestone_status(ms3.milestone_id, MilestoneState.COMPLETED)

        # 5. Verify milestone dependency — ms2 depends on ms1
        # ms2 should now be unblocked since ms1 is completed
        ms1_refreshed = store.get_milestone(ms1.milestone_id)
        assert ms1_refreshed.status == MilestoneState.COMPLETED
        assert ms1_refreshed.completed_at is not None

        ms2_refreshed = store.get_milestone(ms2.milestone_id)
        assert ms2_refreshed.status == MilestoneState.PENDING  # not auto-activated

        # 6. Generate tasks — should produce contracts for ready milestones
        tasks = pm.generate_tasks(prog.program_id)
        # ms2 should be ready (ms1 completed, ms2 still pending), ms3 already completed
        # So we expect exactly 1 task contract for ms2
        assert len(tasks) == 1
        assert tasks[0].scope["milestone_id"] == ms2.milestone_id

        # 7. Pause and resume
        pm.pause_program(prog.program_id)
        paused = store.get_program(prog.program_id)
        assert paused.status == ProgramState.paused

        pm.resume_program(prog.program_id)
        resumed = store.get_program(prog.program_id)
        assert resumed.status == ProgramState.active

        # 8. Complete program
        pm.complete_program(prog.program_id)
        final = store.get_program(prog.program_id)
        assert final.status == ProgramState.completed

        # 9. Verify full structure
        structure = pm.get_program_with_teams(prog.program_id)
        assert len(structure["teams"]) == 2
        team_titles = {t["title"] for t in structure["teams"]}
        assert team_titles == {"Backend", "Frontend"}

        # Verify milestones are nested correctly
        backend_team = next(t for t in structure["teams"] if t["title"] == "Backend")
        assert len(backend_team["milestones"]) == 2
        frontend_team = next(t for t in structure["teams"] if t["title"] == "Frontend")
        assert len(frontend_team["milestones"]) == 1

        # 10. Verify events were emitted for all transitions
        # list_events with no task_id returns all events (including task_id=None)
        # ordered by event_seq DESC when no filters; we get up to 100.
        events = store.list_events(limit=100)
        event_types = [e["event_type"] for e in events]
        # The store emits "program.created" (not "program.compiled")
        assert "program.created" in event_types
        assert "program.active" in event_types
        assert "program.paused" in event_types
        assert "program.completed" in event_types
        assert "team.created" in event_types
        assert "milestone.created" in event_types
        assert "milestone.active" in event_types
        assert "milestone.completed" in event_types

    def test_program_cannot_add_team_after_completion(self, pm):
        """Terminal programs reject team additions."""
        prog = pm.compile_program(goal="Short-lived", title="Done Quick")
        pm.activate_program(prog.program_id)
        pm.complete_program(prog.program_id)

        from hermit.kernel.task.services.program_manager import ProgramManagerError

        with pytest.raises(ProgramManagerError, match="terminal state"):
            pm.add_team(program_id=prog.program_id, title="Too Late")

    def test_invalid_state_transitions_rejected(self, pm):
        """Invalid state transitions raise ProgramManagerError."""
        prog = pm.compile_program(goal="Invalid transitions", title="Bad Flow")

        from hermit.kernel.task.services.program_manager import ProgramManagerError

        # Cannot pause a draft program (draft -> paused not allowed)
        with pytest.raises(ProgramManagerError, match="Invalid program transition"):
            pm.pause_program(prog.program_id)

        # Cannot resume a non-paused program
        with pytest.raises(ProgramManagerError, match="Cannot resume"):
            pm.resume_program(prog.program_id)

    def test_milestone_dependency_validation(self, pm):
        """Adding a milestone with non-existent dependency raises error."""
        prog = pm.compile_program(goal="Dep check", title="Dep Validation")
        team = pm.add_team(program_id=prog.program_id, title="Team A")

        from hermit.kernel.task.services.program_manager import ProgramManagerError

        with pytest.raises(ProgramManagerError, match="dependency not found"):
            pm.add_milestone(
                team_id=team.team_id,
                title="Orphan",
                dependency_ids=["nonexistent_milestone_id"],
            )

    def test_generate_tasks_skips_terminal_program(self, pm):
        """generate_tasks returns empty list for completed/failed programs."""
        prog = pm.compile_program(goal="Terminal test", title="Terminal")
        team = pm.add_team(program_id=prog.program_id, title="Team")
        pm.add_milestone(team_id=team.team_id, title="MS1")
        pm.activate_program(prog.program_id)
        pm.complete_program(prog.program_id)

        tasks = pm.generate_tasks(prog.program_id)
        assert tasks == []

    def test_compile_program_with_structure(self, pm, store):
        """Full compilation chain via compile_program_with_structure."""
        result = pm.compile_program_with_structure(
            goal="Build v2",
            title="V2 Release",
            priority="high",
            team_specs=[
                {
                    "title": "API Team",
                    "milestones": [
                        {
                            "title": "Design",
                            "acceptance_criteria": ["Schema approved"],
                        },
                        {
                            "title": "Build",
                            "dependency_titles": ["Design"],
                            "acceptance_criteria": ["Tests pass"],
                        },
                    ],
                },
            ],
        )
        assert result.program.status == ProgramState.draft
        assert len(result.teams) == 1
        assert len(result.milestones) == 2
        # Design has no deps so it should generate a task contract
        assert len(result.task_contracts) == 1
        assert result.task_contracts[0].goal == "Design"
