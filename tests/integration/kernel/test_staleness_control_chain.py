"""Integration tests: StalenessGuard sweep + ProgramToolService control chain.

Exercises staleness detection against a real KernelStore (SQLite) and validates
the full ProgramToolService CRUD + control lifecycle.
"""

from __future__ import annotations

import time

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.program import ProgramState
from hermit.kernel.task.services.program_tools import ProgramToolService
from hermit.kernel.task.services.staleness_guard import StalenessGuard
from hermit.kernel.task.state.enums import TaskState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_task_in_state(
    store: KernelStore,
    *,
    status: str,
    conversation_id: str = "conv_test",
    goal: str = "test task",
) -> str:
    """Create a task via the store API and transition it to the desired state.

    The store creates tasks in 'running' by default, so we need intermediate
    transitions to reach certain states like 'blocked', 'paused', or
    'planning_ready'.  We use update_task_status which soft-validates (warns
    but does not block) so we can reach any target state.
    """
    store.ensure_conversation(conversation_id, source_channel="test")
    task = store.create_task(
        conversation_id=conversation_id,
        title=goal,
        goal=goal,
        source_channel="test",
        status="running",
    )
    if status != "running":
        store.update_task_status(task.task_id, status)
    return task.task_id


def _set_updated_at(store: KernelStore, task_id: str, updated_at: float) -> None:
    """Directly set updated_at via raw SQL for staleness testing."""
    store.execute_raw(
        "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
        (updated_at, task_id),
        write=True,
    )


# ---------------------------------------------------------------------------
# 1. StalenessGuard sweep — stale tasks transition to failed/cancelled
# ---------------------------------------------------------------------------


class TestStalenessGuardSweepIntegration:
    def test_sweep_stale_planning_ready(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        task_id = _create_task_in_state(store, status=TaskState.PLANNING_READY)
        _set_updated_at(store, task_id, time.time() - 8 * 86400)

        guard = StalenessGuard(store)
        affected = guard.sweep()

        assert task_id in affected
        task = store.get_task(task_id)
        assert task is not None
        assert task.status == "failed"

    def test_sweep_stale_paused(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        task_id = _create_task_in_state(store, status=TaskState.PAUSED)
        _set_updated_at(store, task_id, time.time() - 8 * 86400)

        guard = StalenessGuard(store)
        affected = guard.sweep()

        assert task_id in affected
        task = store.get_task(task_id)
        assert task is not None
        assert task.status == "cancelled"  # paused -> cancelled (not failed)

    def test_sweep_stale_blocked(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        task_id = _create_task_in_state(store, status=TaskState.BLOCKED)
        _set_updated_at(store, task_id, time.time() - 8 * 86400)

        guard = StalenessGuard(store)
        affected = guard.sweep()

        assert task_id in affected
        task = store.get_task(task_id)
        assert task is not None
        assert task.status == "failed"

    def test_sweep_multiple_stale_states(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        old_ts = time.time() - 8 * 86400

        t_planning = _create_task_in_state(store, status=TaskState.PLANNING_READY)
        _set_updated_at(store, t_planning, old_ts)

        t_paused = _create_task_in_state(store, status=TaskState.PAUSED)
        _set_updated_at(store, t_paused, old_ts)

        t_blocked = _create_task_in_state(store, status=TaskState.BLOCKED)
        _set_updated_at(store, t_blocked, old_ts)

        guard = StalenessGuard(store)
        affected = guard.sweep()

        assert set(affected) == {t_planning, t_paused, t_blocked}

        assert store.get_task(t_planning).status == "failed"
        assert store.get_task(t_paused).status == "cancelled"
        assert store.get_task(t_blocked).status == "failed"


# ---------------------------------------------------------------------------
# 2. Non-stale tasks preserved
# ---------------------------------------------------------------------------


class TestStalenessGuardPreservesRecent:
    def test_recent_tasks_not_swept(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")

        t_blocked = _create_task_in_state(store, status=TaskState.BLOCKED)
        t_paused = _create_task_in_state(store, status=TaskState.PAUSED)
        t_planning = _create_task_in_state(store, status=TaskState.PLANNING_READY)

        # All tasks have recent updated_at (just created)
        guard = StalenessGuard(store)
        affected = guard.sweep()

        assert affected == []
        assert store.get_task(t_blocked).status == TaskState.BLOCKED
        assert store.get_task(t_paused).status == TaskState.PAUSED
        assert store.get_task(t_planning).status == TaskState.PLANNING_READY

    def test_mix_of_stale_and_fresh(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        old_ts = time.time() - 8 * 86400

        t_stale = _create_task_in_state(store, status=TaskState.BLOCKED)
        _set_updated_at(store, t_stale, old_ts)

        t_fresh = _create_task_in_state(store, status=TaskState.BLOCKED)
        # t_fresh keeps its recent updated_at

        guard = StalenessGuard(store)
        affected = guard.sweep()

        assert affected == [t_stale]
        assert store.get_task(t_stale).status == "failed"
        assert store.get_task(t_fresh).status == TaskState.BLOCKED


# ---------------------------------------------------------------------------
# 3. Custom TTL
# ---------------------------------------------------------------------------


class TestStalenessGuardCustomTTL:
    def test_custom_ttl_60_seconds(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        task_id = _create_task_in_state(store, status=TaskState.BLOCKED)
        _set_updated_at(store, task_id, time.time() - 120)  # 120 seconds old

        guard = StalenessGuard(store, ttl_seconds=60)
        affected = guard.sweep()

        assert task_id in affected
        task = store.get_task(task_id)
        assert task is not None
        assert task.status == "failed"

    def test_custom_ttl_not_yet_expired(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        task_id = _create_task_in_state(store, status=TaskState.BLOCKED)
        _set_updated_at(store, task_id, time.time() - 30)  # 30 seconds old

        guard = StalenessGuard(store, ttl_seconds=60)
        affected = guard.sweep()

        assert affected == []
        assert store.get_task(task_id).status == TaskState.BLOCKED


# ---------------------------------------------------------------------------
# 4. ProgramToolService control chain
# ---------------------------------------------------------------------------


class TestProgramToolServiceControlChain:
    def test_full_lifecycle(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        svc = ProgramToolService(store)

        # Step 1: create_program
        result = svc.create_program(goal="Build a test suite", title="Test Suite Program")
        assert "error" not in result
        assert "program_id" in result
        assert result["title"] == "Test Suite Program"
        assert result["goal"] == "Build a test suite"
        assert result["status"] == ProgramState.active
        program_id = result["program_id"]

        # Step 2: add_team_to_program
        team_result = svc.add_team_to_program(
            program_id=program_id,
            title="Core Team",
        )
        assert "error" not in team_result
        assert "team_id" in team_result
        assert team_result["program_id"] == program_id
        assert team_result["title"] == "Core Team"
        team_id = team_result["team_id"]

        # Step 3: add_milestone
        milestone_result = svc.add_milestone(
            team_id=team_id,
            title="Phase 1 Complete",
            acceptance_criteria=["All tests pass", "Coverage > 80%"],
        )
        assert "error" not in milestone_result
        assert "milestone_id" in milestone_result
        assert milestone_result["title"] == "Phase 1 Complete"
        assert milestone_result["acceptance_criteria"] == ["All tests pass", "Coverage > 80%"]

        # Step 4: control_program archive (active -> archived)
        archive_result = svc.control_program(program_id=program_id, action="archive")
        assert "error" not in archive_result
        assert archive_result["new_status"] == ProgramState.archived
        assert archive_result["previous_status"] == ProgramState.active

        # Step 5: control_program activate (archived -> active)
        activate_result = svc.control_program(program_id=program_id, action="activate")
        assert "error" not in activate_result
        assert activate_result["new_status"] == ProgramState.active
        assert activate_result["previous_status"] == ProgramState.archived

        # Step 7: get_program_status — verify projection
        status_result = svc.get_program_status(program_id=program_id)
        assert "error" not in status_result
        assert status_result["program_id"] == program_id
        assert status_result["title"] == "Test Suite Program"
        assert status_result["overall_state"] == ProgramState.active
        assert status_result["active_teams"] >= 1

        # Step 8: list_programs — verify in list
        list_result = svc.list_programs()
        assert "error" not in list_result
        assert list_result["count"] >= 1
        program_ids_in_list = [p["program_id"] for p in list_result["programs"]]
        assert program_id in program_ids_in_list


# ---------------------------------------------------------------------------
# 5. Invalid transitions
# ---------------------------------------------------------------------------


class TestProgramToolServiceInvalidTransitions:
    def test_archive_from_archived_fails(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        svc = ProgramToolService(store)

        result = svc.create_program(goal="Test invalid transition")
        program_id = result["program_id"]

        # Archive first (active -> archived)
        svc.control_program(program_id=program_id, action="archive")

        # Attempt 'archive' again — already archived
        archive_result = svc.control_program(program_id=program_id, action="archive")
        assert "error" in archive_result
        assert "archived" in archive_result["error"]

    def test_activate_from_active_fails(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        svc = ProgramToolService(store)

        result = svc.create_program(goal="Test double activate")
        program_id = result["program_id"]

        # Program starts as active, so activate again should fail
        activate_result = svc.control_program(program_id=program_id, action="activate")
        assert "error" in activate_result
        assert "active" in activate_result["error"]

    def test_unknown_action_fails(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        svc = ProgramToolService(store)

        result = svc.create_program(goal="Test unknown action")
        program_id = result["program_id"]

        bad_result = svc.control_program(program_id=program_id, action="explode")
        assert "error" in bad_result
        assert "Unknown action" in bad_result["error"]

    def test_control_archived_program_fails(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        svc = ProgramToolService(store)

        result = svc.create_program(goal="Test terminal block")
        program_id = result["program_id"]
        svc.control_program(program_id=program_id, action="archive")

        # Attempt archive again on archived program — already in terminal state
        archive_result = svc.control_program(program_id=program_id, action="archive")
        assert "error" in archive_result
        assert "archived" in archive_result["error"]


# ---------------------------------------------------------------------------
# 6. Program with status projection
# ---------------------------------------------------------------------------


class TestProgramStatusProjection:
    def test_meaningful_projection(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        svc = ProgramToolService(store)

        # Create program + team + milestone
        program = svc.create_program(
            goal="Deliver kernel v2",
            title="Kernel v2",
            priority="high",
        )
        program_id = program["program_id"]

        team1 = svc.add_team_to_program(program_id=program_id, title="Backend Team")
        team2 = svc.add_team_to_program(program_id=program_id, title="Frontend Team")

        svc.add_milestone(
            team_id=team1["team_id"],
            title="API Design",
            acceptance_criteria=["Schema validated", "Endpoints documented"],
        )
        svc.add_milestone(
            team_id=team2["team_id"],
            title="UI Mockups",
            acceptance_criteria=["Approved by stakeholders"],
        )

        # Program starts as active — no activation needed

        # Get status projection
        projection = svc.get_program_status(program_id=program_id)

        assert projection["program_id"] == program_id
        assert projection["title"] == "Kernel v2"
        assert projection["overall_state"] == ProgramState.active
        assert projection["active_teams"] >= 2
        assert projection["latest_summary"] != ""
        assert isinstance(projection["progress_pct"], float)
        assert isinstance(projection["blocked_items"], int)
        assert isinstance(projection["awaiting_human"], bool)
        assert "last_updated_at" in projection

    def test_get_program_status_nonexistent(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        svc = ProgramToolService(store)

        result = svc.get_program_status(program_id="nonexistent_id")
        assert "error" in result

    def test_list_programs_with_status_filter(self, tmp_path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        svc = ProgramToolService(store)

        # Create two programs (both start as active), archive one
        p1 = svc.create_program(goal="Active program")
        p2 = svc.create_program(goal="Archived program")
        svc.control_program(program_id=p2["program_id"], action="archive")

        # List only active programs
        active_list = svc.list_programs(status="active")
        assert active_list["count"] >= 1
        active_ids = [p["program_id"] for p in active_list["programs"]]
        assert p1["program_id"] in active_ids
        assert p2["program_id"] not in active_ids

        # List only archived programs
        archived_list = svc.list_programs(status="archived")
        assert archived_list["count"] >= 1
        archived_ids = [p["program_id"] for p in archived_list["programs"]]
        assert p2["program_id"] in archived_ids
        assert p1["program_id"] not in archived_ids
