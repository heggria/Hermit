"""Integration test: StatusProjectionService 4-level query chain.

Exercises all four query granularities (program, team, task, attempt)
with real KernelStore data, plus approval queue, benchmark status,
formatted summaries, and the read-only invariant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.projections.status import (
    ApprovalQueueProjection,
    AttemptStatusProjection,
    BenchmarkStatusProjection,
    ProgramStatusProjection,
    StatusProjectionService,
    TaskStatusProjection,
    TeamStatusProjection,
)


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    """Create a real file-backed KernelStore for each test."""
    s = KernelStore(tmp_path / "kernel" / "state.db")
    return s


@pytest.fixture()
def hierarchy(store: KernelStore) -> dict[str, str]:
    """Build a complete program -> team -> task hierarchy with steps and approvals.

    Hierarchy:
        program_task (root)
          +-- team_task_a (running, 2 children: worker_1 completed, worker_2 blocked)
          +-- team_task_b (queued, 1 child: worker_3 running)

    Steps are created on worker_1 (completed step, blocked step) and worker_2 (blocked step).
    An approval is created on worker_2 to test awaiting_human.
    """
    conv = store.ensure_conversation("conv_test", source_channel="test")

    # --- Program root task ---
    program = store.create_task(
        conversation_id=conv.conversation_id,
        title="Test Program",
        goal="Integration test program for status projections",
        source_channel="test",
        status="running",
        priority="high",
    )

    # --- Team A ---
    team_a = store.create_task(
        conversation_id=conv.conversation_id,
        title="Team Alpha",
        goal="Team A work",
        source_channel="test",
        status="running",
        parent_task_id=program.task_id,
    )

    # Worker 1 under Team A: completed
    worker_1 = store.create_task(
        conversation_id=conv.conversation_id,
        title="Worker 1",
        goal="Worker 1 task",
        source_channel="test",
        status="running",
        parent_task_id=team_a.task_id,
    )
    # Create steps for worker_1
    step_w1_done = store.create_step(
        task_id=worker_1.task_id,
        kind="execute",
        status="completed",
        title="Step W1 Done",
    )
    store.update_step(step_w1_done.step_id, status="completed")

    step_w1_blocked = store.create_step(
        task_id=worker_1.task_id,
        kind="execute",
        status="blocked",
        title="Step W1 Blocked",
    )
    store.update_step(step_w1_blocked.step_id, status="blocked")

    # Mark worker_1 as completed
    store.update_task_status(worker_1.task_id, "completed")

    # Worker 2 under Team A: blocked
    worker_2 = store.create_task(
        conversation_id=conv.conversation_id,
        title="Worker 2",
        goal="Worker 2 task",
        source_channel="test",
        status="running",
        parent_task_id=team_a.task_id,
    )
    step_w2 = store.create_step(
        task_id=worker_2.task_id,
        kind="execute",
        status="running",
        title="Step W2 Running",
    )
    attempt_w2 = store.create_step_attempt(
        task_id=worker_2.task_id,
        step_id=step_w2.step_id,
        attempt=1,
        status="running",
    )

    # Create pending approval on worker_2
    approval_w2 = store.create_approval(
        task_id=worker_2.task_id,
        step_id=step_w2.step_id,
        step_attempt_id=attempt_w2.step_attempt_id,
        approval_type="operator_confirmation",
        requested_action={"action_type": "bash", "tool_name": "bash"},
        request_packet_ref="ref_packet_w2",
    )

    # Update attempt to reference the approval
    store.update_step_attempt(
        attempt_w2.step_attempt_id,
        status="awaiting_approval",
        status_reason="Pending operator confirmation",
        approval_id=approval_w2.approval_id,
    )

    store.update_task_status(worker_2.task_id, "blocked")

    # --- Team B ---
    team_b = store.create_task(
        conversation_id=conv.conversation_id,
        title="Team Beta",
        goal="Team B work",
        source_channel="test",
        status="queued",
        parent_task_id=program.task_id,
    )

    # Worker 3 under Team B: running
    worker_3 = store.create_task(
        conversation_id=conv.conversation_id,
        title="Worker 3",
        goal="Worker 3 task",
        source_channel="test",
        status="running",
        parent_task_id=team_b.task_id,
        priority="high",
    )
    step_w3 = store.create_step(
        task_id=worker_3.task_id,
        kind="execute",
        status="running",
        title="Step W3 Running",
    )
    attempt_w3 = store.create_step_attempt(
        task_id=worker_3.task_id,
        step_id=step_w3.step_id,
        attempt=1,
        status="running",
    )

    # Create another pending approval on worker_3 (high-priority task)
    approval_w3 = store.create_approval(
        task_id=worker_3.task_id,
        step_id=step_w3.step_id,
        step_attempt_id=attempt_w3.step_attempt_id,
        approval_type="operator_confirmation",
        requested_action={"action_type": "write_file", "tool_name": "write_file"},
        request_packet_ref="ref_packet_w3",
    )

    # Create a pending approval on team_a (direct child of program)
    # so that program-level awaiting_human is True.
    step_team_a = store.create_step(
        task_id=team_a.task_id,
        kind="execute",
        status="running",
        title="Step Team A Coordination",
    )
    attempt_team_a = store.create_step_attempt(
        task_id=team_a.task_id,
        step_id=step_team_a.step_id,
        attempt=1,
        status="running",
    )
    approval_team_a = store.create_approval(
        task_id=team_a.task_id,
        step_id=step_team_a.step_id,
        step_attempt_id=attempt_team_a.step_attempt_id,
        approval_type="operator_confirmation",
        requested_action={"action_type": "plan", "tool_name": "plan"},
        request_packet_ref="ref_packet_team_a",
    )

    # Add a risk note to the program
    store.append_event(
        event_type="task.note.appended",
        entity_type="task",
        entity_id=program.task_id,
        task_id=program.task_id,
        actor="kernel",
        payload={"raw_text": "Risk: potential memory pressure on large DAGs"},
    )

    return {
        "program_id": program.task_id,
        "team_a_id": team_a.task_id,
        "team_b_id": team_b.task_id,
        "worker_1_id": worker_1.task_id,
        "worker_2_id": worker_2.task_id,
        "worker_3_id": worker_3.task_id,
        "step_w1_done_id": step_w1_done.step_id,
        "step_w1_blocked_id": step_w1_blocked.step_id,
        "step_w2_id": step_w2.step_id,
        "step_w3_id": step_w3.step_id,
        "attempt_w2_id": attempt_w2.step_attempt_id,
        "attempt_w3_id": attempt_w3.step_attempt_id,
        "approval_w2_id": approval_w2.approval_id,
        "approval_w3_id": approval_w3.approval_id,
        "approval_team_a_id": approval_team_a.approval_id,
        "step_team_a_id": step_team_a.step_id,
        "attempt_team_a_id": attempt_team_a.step_attempt_id,
    }


# ---------------------------------------------------------------------------
# 1. Program-level query
# ---------------------------------------------------------------------------


class TestProgramLevel:
    def test_get_program_status(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        proj = svc.get_program_status(hierarchy["program_id"])

        assert isinstance(proj, ProgramStatusProjection)
        assert proj.program_id == hierarchy["program_id"]
        assert proj.title == "Test Program"
        assert proj.overall_state == "running"

        # progress: 0/2 teams terminal => 0%
        # (team_a is running, team_b is queued)
        assert proj.progress_pct == 0.0

        # active_teams: team_a (running) and team_b (queued) are both in ACTIVE_STATUSES
        assert proj.active_teams == 2

        # blocked_items: worker_2 under team_a is blocked, but
        # the direct children of program are team_a & team_b, so 0 blocked
        assert proj.blocked_items == 0

        # awaiting_human: child tasks have pending approvals
        assert proj.awaiting_human is True

        # Risks should contain the risk note we appended
        assert len(proj.latest_risks) >= 1
        assert any("memory pressure" in r for r in proj.latest_risks)

        # last_updated_at should be positive
        assert proj.last_updated_at > 0

    def test_program_not_found_raises(self, store: KernelStore) -> None:
        svc = StatusProjectionService(store)
        with pytest.raises(KeyError, match="Program not found"):
            svc.get_program_status("nonexistent_program")


# ---------------------------------------------------------------------------
# 2. Team-level query
# ---------------------------------------------------------------------------


class TestTeamLevel:
    def test_get_team_status_a(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        proj = svc.get_team_status(hierarchy["team_a_id"])

        assert isinstance(proj, TeamStatusProjection)
        assert proj.team_id == hierarchy["team_a_id"]
        assert proj.title == "Team Alpha"
        assert proj.state == "running"

        # active_workers: worker_2 is blocked (in _ACTIVE_STATUSES)
        # worker_1 is completed (not active)
        assert proj.active_workers == 1

        # milestone_progress: 1/2 (worker_1 completed, worker_2 blocked)
        assert proj.milestone_progress == "1/2"

        # blockers: worker_2 is blocked
        assert len(proj.blockers) == 1
        assert hierarchy["worker_2_id"] in proj.blockers[0]

    def test_get_team_status_b(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        proj = svc.get_team_status(hierarchy["team_b_id"])

        assert isinstance(proj, TeamStatusProjection)
        assert proj.title == "Team Beta"
        assert proj.state == "queued"

        # worker_3 is running => 1 active worker
        assert proj.active_workers == 1

        # milestone_progress: 0/1
        assert proj.milestone_progress == "0/1"

        # No blockers
        assert proj.blockers == []


# ---------------------------------------------------------------------------
# 3. Task-level query
# ---------------------------------------------------------------------------


class TestTaskLevel:
    def test_get_task_status_worker1(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        proj = svc.get_task_status(hierarchy["worker_1_id"])

        assert isinstance(proj, TaskStatusProjection)
        assert proj.task_id == hierarchy["worker_1_id"]
        assert proj.title == "Worker 1"
        assert proj.state == "completed"
        assert proj.goal == "Worker 1 task"
        assert proj.parent_task_id == hierarchy["team_a_id"]

        # worker_1 has 2 steps: 1 completed, 1 blocked
        assert proj.total_steps == 2
        assert proj.completed_steps == 1
        assert proj.blocked_steps == 1

        # No pending approvals on worker_1
        assert proj.pending_approvals == 0

        # blocked step should produce a blocker
        assert len(proj.blockers) == 1
        assert "Step W1 Blocked" in proj.blockers[0]

    def test_get_task_status_worker2(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        proj = svc.get_task_status(hierarchy["worker_2_id"])

        assert proj.state == "blocked"
        assert proj.total_steps == 1
        assert proj.running_steps == 1  # step_w2 is still "running" in the steps table

        # 1 pending approval
        assert proj.pending_approvals == 1

        # latest_event should not be empty
        assert proj.latest_event != ""

    def test_task_not_found_raises(self, store: KernelStore) -> None:
        svc = StatusProjectionService(store)
        with pytest.raises(KeyError, match="Task not found"):
            svc.get_task_status("nonexistent_task")


# ---------------------------------------------------------------------------
# 4. Attempt-level query
# ---------------------------------------------------------------------------


class TestAttemptLevel:
    def test_get_attempt_status_w2(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        proj = svc.get_attempt_status(hierarchy["attempt_w2_id"])

        assert isinstance(proj, AttemptStatusProjection)
        assert proj.step_attempt_id == hierarchy["attempt_w2_id"]
        assert proj.task_id == hierarchy["worker_2_id"]
        assert proj.step_id == hierarchy["step_w2_id"]
        assert proj.attempt_number == 1
        assert proj.status == "awaiting_approval"
        assert proj.status_reason == "Pending operator confirmation"
        assert proj.has_approval is True
        assert proj.started_at > 0

    def test_get_attempt_status_w3(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        proj = svc.get_attempt_status(hierarchy["attempt_w3_id"])

        assert proj.status == "running"
        assert proj.has_approval is False  # not linked yet
        assert proj.status_reason == ""

    def test_attempt_not_found_raises(self, store: KernelStore) -> None:
        svc = StatusProjectionService(store)
        with pytest.raises(KeyError, match="Step attempt not found"):
            svc.get_attempt_status("nonexistent_attempt")


# ---------------------------------------------------------------------------
# 5. Formatted summaries
# ---------------------------------------------------------------------------


class TestFormattedSummaries:
    def test_format_program_summary(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        proj = svc.get_program_status(hierarchy["program_id"])
        summary = StatusProjectionService.format_program_summary(proj)

        assert "Program: Test Program" in summary
        assert "Status: running" in summary
        assert "Progress:" in summary
        assert "Active teams:" in summary
        assert "Blocked items:" in summary
        assert "Action required:" in summary
        assert "Last updated:" in summary

    def test_format_team_summary(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        proj = svc.get_team_status(hierarchy["team_a_id"])
        summary = StatusProjectionService.format_team_summary(proj)

        assert "Team: Team Alpha" in summary
        assert "State: running" in summary
        assert "Active workers:" in summary
        assert "Milestone progress:" in summary
        assert "Blockers:" in summary

    def test_format_task_summary(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        proj = svc.get_task_status(hierarchy["worker_1_id"])
        summary = StatusProjectionService.format_task_summary(proj)

        assert "Task: Worker 1 [completed]" in summary
        assert "Goal: Worker 1 task" in summary
        assert "Steps:" in summary
        assert "Pending approvals:" in summary
        assert "Last updated:" in summary

    def test_format_attempt_summary(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        proj = svc.get_attempt_status(hierarchy["attempt_w2_id"])
        summary = StatusProjectionService.format_attempt_summary(proj)

        assert hierarchy["attempt_w2_id"] in summary
        assert "[awaiting_approval]" in summary
        assert "Status reason: Pending operator confirmation" in summary
        assert "Has approval: yes" in summary
        assert "Started:" in summary


# ---------------------------------------------------------------------------
# 6. Approval queue
# ---------------------------------------------------------------------------


class TestApprovalQueue:
    def test_get_approval_queue(self, store: KernelStore, hierarchy: dict[str, str]) -> None:
        svc = StatusProjectionService(store)
        queue = svc.get_approval_queue()

        assert isinstance(queue, ApprovalQueueProjection)
        # We created 3 pending approvals (team_a, worker_2, and worker_3)
        assert queue.total_count == 3
        assert len(queue.pending_approvals) == 3

        # worker_3 is in a high-priority task
        assert queue.high_priority_count == 1

        # Verify approval dict structure
        for item in queue.pending_approvals:
            assert "approval_id" in item
            assert "task_id" in item
            assert "step_id" in item
            assert "approval_type" in item
            assert "action_type" in item
            assert "tool_name" in item
            assert "requested_at" in item


# ---------------------------------------------------------------------------
# 7. Benchmark status
# ---------------------------------------------------------------------------


class TestBenchmarkStatus:
    def test_get_benchmark_status_empty(
        self, store: KernelStore, hierarchy: dict[str, str]
    ) -> None:
        svc = StatusProjectionService(store)
        bench = svc.get_benchmark_status(program_id=hierarchy["program_id"])

        assert isinstance(bench, BenchmarkStatusProjection)
        # No benchmark events were inserted
        assert bench.recent_runs == []
        assert bench.pass_rate == 0.0
        assert bench.regressions == []

    def test_get_benchmark_status_global(self, store: KernelStore) -> None:
        svc = StatusProjectionService(store)
        bench = svc.get_benchmark_status()

        assert isinstance(bench, BenchmarkStatusProjection)
        assert isinstance(bench.recent_runs, list)
        assert isinstance(bench.pass_rate, float)
        assert isinstance(bench.regressions, list)

    def test_get_benchmark_status_with_events(
        self, store: KernelStore, hierarchy: dict[str, str]
    ) -> None:
        """Insert benchmark events and verify they are picked up."""
        store.append_event(
            event_type="benchmark.completed",
            entity_type="task",
            entity_id=hierarchy["program_id"],
            task_id=hierarchy["program_id"],
            actor="kernel",
            payload={"result": "pass", "name": "test_1"},
        )
        store.append_event(
            event_type="benchmark.completed",
            entity_type="task",
            entity_id=hierarchy["program_id"],
            task_id=hierarchy["program_id"],
            actor="kernel",
            payload={"result": "fail", "name": "test_2"},
        )

        svc = StatusProjectionService(store)
        bench = svc.get_benchmark_status(program_id=hierarchy["program_id"])

        assert len(bench.recent_runs) == 2
        assert bench.pass_rate == 50.0


# ---------------------------------------------------------------------------
# 8. Read-only invariant
# ---------------------------------------------------------------------------


class TestReadOnlyInvariant:
    def test_queries_do_not_create_tasks(
        self, store: KernelStore, hierarchy: dict[str, str]
    ) -> None:
        """Verify that all query methods are side-effect free."""
        # Snapshot the current state
        tasks_before = store.list_tasks(limit=1000)
        events_before = store.list_events(limit=10000)
        task_ids_before = {t.task_id for t in tasks_before}
        event_count_before = len(events_before)

        svc = StatusProjectionService(store)

        # Execute all query methods
        svc.get_program_status(hierarchy["program_id"])
        svc.get_team_status(hierarchy["team_a_id"])
        svc.get_team_status(hierarchy["team_b_id"])
        svc.get_task_status(hierarchy["worker_1_id"])
        svc.get_task_status(hierarchy["worker_2_id"])
        svc.get_task_status(hierarchy["worker_3_id"])
        svc.get_attempt_status(hierarchy["attempt_w2_id"])
        svc.get_attempt_status(hierarchy["attempt_w3_id"])
        svc.get_approval_queue()
        svc.get_benchmark_status(program_id=hierarchy["program_id"])
        svc.get_benchmark_status()

        # Verify no new tasks were created
        tasks_after = store.list_tasks(limit=1000)
        task_ids_after = {t.task_id for t in tasks_after}
        assert task_ids_before == task_ids_after, "Queries must not create new tasks"

        # Verify no new events were emitted
        events_after = store.list_events(limit=10000)
        assert len(events_after) == event_count_before, (
            f"Queries must not emit events: before={event_count_before}, after={len(events_after)}"
        )
