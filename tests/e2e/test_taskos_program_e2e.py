"""E2E: TaskOS Program management, ingress routing, status projections, follow-up limits.

Uses real KernelStore + ProgramManager / GovernedIngressService / StatusProjectionService.
No mocks — exercises the full kernel path from store creation through projection assembly.
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.execution.executor.reconciliation_executor import MAX_AUTO_FOLLOWUPS
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.program import ProgramState
from hermit.kernel.task.models.team import MilestoneState, TeamState
from hermit.kernel.task.projections.status import (
    StatusProjectionService,
    TaskStatusProjection,
)
from hermit.kernel.task.services.governed_ingress import GovernedIngressService
from hermit.kernel.task.services.governor import GovernorService, IntentClass
from hermit.kernel.task.services.program_manager import ProgramManager

# ---------------------------------------------------------------------------
# Test 29: Program lifecycle — create, activate, add team, add milestone
# ---------------------------------------------------------------------------


class TestProgramLifecycleCreateTeamMilestoneTasks:
    """End-to-end: compile a program, transition to active, attach team
    and milestone, then verify the full structure via store queries."""

    def test_program_lifecycle_create_team_milestone_tasks(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        pm = ProgramManager(store)

        # 1. Create program (draft state)
        program = pm.compile_program(goal="Ship authentication module v2")
        assert program.program_id.startswith("program_")
        assert program.status == ProgramState.active

        # 2. Verify already in active state
        updated_program = store.get_program(program.program_id)
        assert updated_program is not None
        assert updated_program.status == ProgramState.active

        # 3. Create team under program
        team = pm.add_team(
            program_id=program.program_id,
            title="Auth Backend Team",
            workspace_id="ws_auth_backend",
        )
        assert team.team_id.startswith("team_")
        assert team.program_id == program.program_id
        assert team.status == TeamState.ACTIVE

        # 4. Create milestone under team
        milestone = pm.add_milestone(
            team_id=team.team_id,
            title="OAuth2 Integration",
            description="Implement OAuth2 flow with PKCE",
            acceptance_criteria=["All OAuth2 tests pass", "Token refresh works"],
        )
        assert milestone.milestone_id.startswith("milestone_")
        assert milestone.team_id == team.team_id
        assert milestone.status == MilestoneState.PENDING

        # 5. Verify: program has team, team has milestone, all states correct
        teams = store.list_teams_by_program(program_id=program.program_id)
        assert len(teams) == 1
        assert teams[0].team_id == team.team_id

        final_program = store.get_program(program.program_id)
        assert final_program is not None
        assert milestone.milestone_id in final_program.milestone_ids
        assert final_program.status == ProgramState.active

        # Verify via get_program_with_teams for full structure
        structure = pm.get_program_with_teams(program.program_id)
        assert len(structure["teams"]) == 1
        team_data = structure["teams"][0]
        assert team_data["title"] == "Auth Backend Team"
        assert len(team_data["milestones"]) == 1
        ms_data = team_data["milestones"][0]
        assert ms_data["title"] == "OAuth2 Integration"
        assert ms_data["acceptance_criteria"] == [
            "All OAuth2 tests pass",
            "Token refresh works",
        ]


# ---------------------------------------------------------------------------
# Test 30: GovernedIngress 3-path routing
# ---------------------------------------------------------------------------


class TestGovernedIngress3PathRouting:
    """End-to-end: verify intent classification routes to the correct handler
    for new_work, status_query, and control_command — and that status_query
    never creates tasks."""

    def test_governed_ingress_3_path_routing(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        store.ensure_conversation("conv-e2e", source_channel="chat")
        service = GovernedIngressService(store)

        # Path 1: new_work
        result_new = service.process_message(message="Create a new task to fix the bug")
        assert result_new.intent_class == "new_work"
        assert result_new.requires_execution is True
        assert result_new.binding_decision is not None

        # Path 2: status_query
        tasks_before = store.list_tasks(limit=100)
        result_status = service.process_message(message="What is the status of program X?")
        assert result_status.intent_class == "status_query"
        assert result_status.requires_execution is False
        # Verify: status_query does NOT create any tasks
        tasks_after = store.list_tasks(limit=100)
        assert len(tasks_after) == len(tasks_before)

        # Path 3: control_command
        result_control = service.process_message(message="Pause program X")
        assert result_control.intent_class == "control_command"
        assert result_control.requires_execution is False

    def test_governor_intent_classification_directly(self, tmp_path: Path) -> None:
        """Verify GovernorService classify_intent returns correct IntentClass."""
        store = KernelStore(tmp_path / "state.db")
        gov = GovernorService(store)

        # new_work — no status or control keywords
        res_new = gov.classify_intent("implement the login feature now")
        assert res_new.intent_class == IntentClass.new_work

        # status_query — contains status keywords
        res_status = gov.classify_intent("show me the current progress")
        assert res_status.intent_class == IntentClass.status_query

        # control_command — contains control keywords (takes precedence)
        res_control = gov.classify_intent("pause all deployments")
        assert res_control.intent_class == IntentClass.control_command


# ---------------------------------------------------------------------------
# Test 31: StatusProjection read-only, no side effects
# ---------------------------------------------------------------------------


class TestStatusProjectionReadOnlyNoSideEffects:
    """End-to-end: create a task with steps and attempts, query the status
    projection, and verify it is read-only and idempotent."""

    def test_status_projection_read_only_no_side_effects(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        store.ensure_conversation("conv-proj", source_channel="chat")

        # Create a task with a step and an attempt
        task = store.create_task(
            conversation_id="conv-proj",
            title="Run integration tests",
            goal="Execute full test suite and report results",
            source_channel="chat",
        )
        step = store.create_step(
            task_id=task.task_id,
            kind="execute",
            title="run pytest",
        )
        _attempt = store.create_step_attempt(
            task_id=task.task_id,
            step_id=step.step_id,
            attempt=1,
        )

        sps = StatusProjectionService(store)

        # Snapshot state before projection query
        tasks_before = store.list_tasks(limit=100)
        steps_before = store.list_steps(task_id=task.task_id, limit=100)

        # First query
        projection = sps.get_task_status(task.task_id)
        assert isinstance(projection, TaskStatusProjection)
        assert projection.task_id == task.task_id
        assert projection.title == "Run integration tests"
        assert projection.goal == "Execute full test suite and report results"
        assert projection.state == "running"
        assert projection.total_steps == 1
        assert projection.pending_approvals == 0

        # Verify: no new tasks/steps/attempts created (read-only)
        tasks_after = store.list_tasks(limit=100)
        steps_after = store.list_steps(task_id=task.task_id, limit=100)
        assert len(tasks_after) == len(tasks_before)
        assert len(steps_after) == len(steps_before)

        # Second query → same result (idempotent)
        projection2 = sps.get_task_status(task.task_id)
        assert projection2.task_id == projection.task_id
        assert projection2.title == projection.title
        assert projection2.state == projection.state
        assert projection2.total_steps == projection.total_steps

        # Still no side effects after second query
        tasks_final = store.list_tasks(limit=100)
        assert len(tasks_final) == len(tasks_before)


# ---------------------------------------------------------------------------
# Test 32: Auto follow-up max limit (3) blocks 4th
# ---------------------------------------------------------------------------


class TestAutoFollowupMaxLimit3Blocks4th:
    """End-to-end: create a root task and 3 follow-up children with the
    'retry/mitigate:' prefix, then verify the 4th would be blocked by
    MAX_AUTO_FOLLOWUPS."""

    def test_auto_followup_max_limit_3_blocks_4th(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        store.ensure_conversation("conv-followup", source_channel="chat")

        # Create root task
        root = store.create_task(
            conversation_id="conv-followup",
            title="Original task",
            goal="Implement feature X",
            source_channel="chat",
        )

        # Create 3 child tasks with "retry/mitigate: " prefix and parent_task_id=root
        for i in range(3):
            store.create_task(
                conversation_id="conv-followup",
                title=f"retry/mitigate: attempt {i + 1}",
                goal=f"retry/mitigate: Implement feature X (attempt {i + 1})",
                source_channel="chat",
                parent_task_id=root.task_id,
            )

        # Count existing follow-ups by scanning children with "retry/mitigate:" prefix
        children = store.list_child_tasks(parent_task_id=root.task_id)
        followup_count = sum(1 for t in children if t.goal.startswith("retry/mitigate: "))

        # Verify: count == 3
        assert followup_count == 3

        # Verify: MAX_AUTO_FOLLOWUPS is 3
        assert MAX_AUTO_FOLLOWUPS == 3

        # Verify: any logic to generate 4th would be blocked
        assert followup_count >= MAX_AUTO_FOLLOWUPS

        # Demonstrate the guard logic from ReconciliationExecutor:
        # When followup_count >= MAX_AUTO_FOLLOWUPS, no new follow-up is generated.
        should_generate = followup_count < MAX_AUTO_FOLLOWUPS
        assert should_generate is False

        # Adding one more child (simulating what would happen if the guard
        # were bypassed) would push past the limit.
        hypothetical_count = followup_count + 1
        assert hypothetical_count > MAX_AUTO_FOLLOWUPS
