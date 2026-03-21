"""Tests for kernel/task/projections/status.py — CQRS read-model projections."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.task.models.records import (
    ApprovalRecord,
    StepAttemptRecord,
    StepRecord,
    TaskRecord,
)
from hermit.kernel.task.projections.status import (
    ApprovalQueueProjection,
    AttemptStatusProjection,
    BenchmarkStatusProjection,
    ProgramStatusProjection,
    StatusProjectionService,
    TaskStatusProjection,
    TeamStatusProjection,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "task_1",
    title: str = "Test task",
    status: str = "running",
    goal: str = "test goal",
    priority: str = "normal",
    parent_task_id: str | None = None,
    updated_at: float | None = None,
    created_at: float | None = None,
) -> TaskRecord:
    now = time.time()
    return TaskRecord(
        task_id=task_id,
        conversation_id="conv_1",
        title=title,
        goal=goal,
        status=status,
        priority=priority,
        owner_principal_id="principal_test",
        policy_profile="autonomous",
        source_channel="test",
        parent_task_id=parent_task_id,
        task_contract_ref=None,
        requested_by_principal_id=None,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )


def _make_approval(
    approval_id: str = "appr_1",
    task_id: str = "task_1",
    status: str = "pending",
    approval_type: str = "human",
    action_type: str = "write_file",
    tool_name: str = "write_file",
    requested_at: float | None = None,
) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=approval_id,
        task_id=task_id,
        step_id="step_1",
        step_attempt_id="attempt_1",
        status=status,
        approval_type=approval_type,
        requested_action={"action_type": action_type, "tool_name": tool_name},
        requested_at=requested_at or time.time(),
    )


def _make_step(
    step_id: str = "step_1",
    task_id: str = "task_1",
    status: str = "running",
    title: str | None = None,
    node_key: str | None = None,
) -> StepRecord:
    return StepRecord(
        step_id=step_id,
        task_id=task_id,
        kind="execute",
        status=status,
        attempt=1,
        title=title,
        node_key=node_key,
    )


def _make_step_attempt(
    step_attempt_id: str = "sa_1",
    task_id: str = "task_1",
    step_id: str = "step_1",
    attempt: int = 1,
    status: str = "running",
    waiting_reason: str | None = None,
    approval_id: str | None = None,
    capability_grant_id: str | None = None,
    started_at: float | None = None,
    finished_at: float | None = None,
) -> StepAttemptRecord:
    return StepAttemptRecord(
        step_attempt_id=step_attempt_id,
        task_id=task_id,
        step_id=step_id,
        attempt=attempt,
        status=status,
        waiting_reason=waiting_reason,
        approval_id=approval_id,
        capability_grant_id=capability_grant_id,
        started_at=started_at,
        finished_at=finished_at,
    )


@pytest.fixture()
def mock_store() -> MagicMock:
    store = MagicMock()
    store.get_task.return_value = None
    store.list_child_tasks.return_value = []
    store.list_events.return_value = []
    store.list_approvals.return_value = []
    store.count_steps_by_status.return_value = {}
    store.list_tasks.return_value = []
    store.list_steps.return_value = []
    store.get_step_attempt.return_value = None
    return store


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestProgramStatusProjection:
    def test_defaults(self) -> None:
        proj = ProgramStatusProjection(
            program_id="pgm_1", title="My Program", overall_state="running"
        )
        assert proj.program_id == "pgm_1"
        assert proj.title == "My Program"
        assert proj.overall_state == "running"
        assert proj.progress_pct == 0.0
        assert proj.current_phase == ""
        assert proj.active_teams == 0
        assert proj.queued_tasks == 0
        assert proj.running_attempts == 0
        assert proj.blocked_items == 0
        assert proj.awaiting_human is False
        assert proj.latest_summary == ""
        assert proj.latest_risks == []
        assert proj.latest_benchmark_status == ""
        assert proj.last_updated_at == 0.0

    def test_custom_values(self) -> None:
        proj = ProgramStatusProjection(
            program_id="pgm_2",
            title="Custom",
            overall_state="blocked",
            progress_pct=50.0,
            active_teams=3,
            awaiting_human=True,
            latest_risks=["risk-a", "risk-b"],
        )
        assert proj.progress_pct == 50.0
        assert proj.active_teams == 3
        assert proj.awaiting_human is True
        assert len(proj.latest_risks) == 2


class TestTeamStatusProjection:
    def test_defaults(self) -> None:
        proj = TeamStatusProjection(team_id="t_1", title="Team A", state="running")
        assert proj.workspace == ""
        assert proj.active_workers == 0
        assert proj.milestone_progress == ""
        assert proj.blockers == []

    def test_with_blockers(self) -> None:
        proj = TeamStatusProjection(
            team_id="t_2",
            title="Team B",
            state="blocked",
            blockers=["waiting for approval"],
        )
        assert proj.state == "blocked"
        assert len(proj.blockers) == 1


class TestTaskStatusProjection:
    def test_defaults(self) -> None:
        proj = TaskStatusProjection(task_id="t_1", title="Task A", state="running")
        assert proj.goal == ""
        assert proj.priority == "normal"
        assert proj.parent_task_id is None
        assert proj.total_steps == 0
        assert proj.completed_steps == 0
        assert proj.running_steps == 0
        assert proj.blocked_steps == 0
        assert proj.failed_steps == 0
        assert proj.pending_approvals == 0
        assert proj.latest_event == ""
        assert proj.blockers == []
        assert proj.last_updated_at == 0.0

    def test_with_step_details(self) -> None:
        proj = TaskStatusProjection(
            task_id="t_2",
            title="Task B",
            state="blocked",
            total_steps=5,
            completed_steps=2,
            running_steps=1,
            blocked_steps=1,
            failed_steps=1,
            pending_approvals=2,
            blockers=["step_x is blocked"],
        )
        assert proj.total_steps == 5
        assert proj.pending_approvals == 2
        assert len(proj.blockers) == 1


class TestAttemptStatusProjection:
    def test_defaults(self) -> None:
        proj = AttemptStatusProjection(
            step_attempt_id="sa_1",
            task_id="t_1",
            step_id="s_1",
            attempt_number=1,
            status="running",
        )
        assert proj.waiting_reason == ""
        assert proj.has_approval is False
        assert proj.has_capability_grant is False
        assert proj.started_at == 0.0
        assert proj.finished_at == 0.0
        assert proj.failure_reason == ""

    def test_with_failure(self) -> None:
        proj = AttemptStatusProjection(
            step_attempt_id="sa_2",
            task_id="t_1",
            step_id="s_1",
            attempt_number=2,
            status="failed",
            failure_reason="Test assertion error",
            started_at=1700000000.0,
            finished_at=1700000010.0,
        )
        assert proj.status == "failed"
        assert proj.failure_reason == "Test assertion error"


class TestApprovalQueueProjection:
    def test_defaults(self) -> None:
        proj = ApprovalQueueProjection()
        assert proj.pending_approvals == []
        assert proj.total_count == 0
        assert proj.high_priority_count == 0

    def test_with_items(self) -> None:
        proj = ApprovalQueueProjection(
            pending_approvals=[{"approval_id": "a1"}],
            total_count=1,
            high_priority_count=1,
        )
        assert proj.total_count == 1


class TestBenchmarkStatusProjection:
    def test_defaults(self) -> None:
        proj = BenchmarkStatusProjection()
        assert proj.recent_runs == []
        assert proj.pass_rate == 0.0
        assert proj.regressions == []


# ---------------------------------------------------------------------------
# Service: get_program_status
# ---------------------------------------------------------------------------


class TestGetProgramStatus:
    def test_raises_on_missing_program(self, mock_store: MagicMock) -> None:
        svc = StatusProjectionService(mock_store)
        with pytest.raises(KeyError, match="Program not found"):
            svc.get_program_status("missing")

    def test_minimal_program(self, mock_store: MagicMock) -> None:
        root = _make_task(task_id="pgm_1", title="Root", status="running", goal="Do stuff")
        mock_store.get_task.return_value = root
        mock_store.list_child_tasks.return_value = []
        mock_store.list_approvals.return_value = []
        mock_store.count_steps_by_status.return_value = {}
        mock_store.list_events.return_value = []

        svc = StatusProjectionService(mock_store)
        proj = svc.get_program_status("pgm_1")

        assert proj.program_id == "pgm_1"
        assert proj.title == "Root"
        assert proj.overall_state == "running"
        assert proj.progress_pct == 0.0
        assert proj.awaiting_human is False
        assert proj.latest_benchmark_status == ""

    def test_program_with_children(self, mock_store: MagicMock) -> None:
        root = _make_task(task_id="pgm_1", title="Root", status="running", goal="Goal")
        child_running = _make_task(
            task_id="c1", title="Worker 1", status="running", parent_task_id="pgm_1"
        )
        child_queued = _make_task(
            task_id="c2", title="Worker 2", status="queued", parent_task_id="pgm_1"
        )
        child_done = _make_task(
            task_id="c3", title="Worker 3", status="completed", parent_task_id="pgm_1"
        )
        child_blocked = _make_task(
            task_id="c4", title="Worker 4", status="blocked", parent_task_id="pgm_1"
        )

        mock_store.get_task.return_value = root
        mock_store.list_child_tasks.return_value = [
            child_running,
            child_queued,
            child_done,
            child_blocked,
        ]
        mock_store.list_approvals.return_value = []
        mock_store.count_steps_by_status.return_value = {"running": 2}
        mock_store.list_events.return_value = []

        svc = StatusProjectionService(mock_store)
        proj = svc.get_program_status("pgm_1")

        assert proj.queued_tasks == 1
        assert proj.blocked_items == 1
        assert proj.active_teams == 3  # running + queued + blocked
        assert proj.running_attempts == 2
        assert proj.progress_pct == 25.0  # 1 of 4 terminal

    def test_awaiting_human_from_root_approvals(self, mock_store: MagicMock) -> None:
        root = _make_task(task_id="pgm_1", title="Root", status="running", goal="Goal")
        mock_store.get_task.return_value = root
        mock_store.list_child_tasks.return_value = []
        mock_store.list_approvals.return_value = [_make_approval(task_id="pgm_1")]
        mock_store.count_steps_by_status.return_value = {}
        mock_store.list_events.return_value = []

        svc = StatusProjectionService(mock_store)
        proj = svc.get_program_status("pgm_1")

        assert proj.awaiting_human is True

    def test_risks_extracted_from_events(self, mock_store: MagicMock) -> None:
        root = _make_task(task_id="pgm_1", title="Root", status="running", goal="Goal")
        mock_store.get_task.return_value = root
        mock_store.list_child_tasks.return_value = []
        mock_store.list_approvals.return_value = []
        mock_store.count_steps_by_status.return_value = {}
        mock_store.list_events.return_value = [
            {
                "event_type": "task.note.appended",
                "payload": {"raw_text": "This is a risk: deployment may fail"},
                "occurred_at": time.time(),
                "event_seq": 1,
            },
            {
                "event_type": "task.note.appended",
                "payload": {"raw_text": "Normal note without keywords"},
                "occurred_at": time.time(),
                "event_seq": 2,
            },
        ]

        svc = StatusProjectionService(mock_store)
        proj = svc.get_program_status("pgm_1")

        assert len(proj.latest_risks) == 1
        assert "risk" in proj.latest_risks[0].lower()

    def test_last_updated_tracks_children(self, mock_store: MagicMock) -> None:
        now = time.time()
        root = _make_task(task_id="pgm_1", title="Root", status="running", updated_at=now - 100)
        child = _make_task(
            task_id="c1",
            title="Fresh",
            status="running",
            parent_task_id="pgm_1",
            updated_at=now,
        )
        mock_store.get_task.return_value = root
        mock_store.list_child_tasks.return_value = [child]
        mock_store.list_approvals.return_value = []
        mock_store.count_steps_by_status.return_value = {}
        mock_store.list_events.return_value = []

        svc = StatusProjectionService(mock_store)
        proj = svc.get_program_status("pgm_1")

        assert proj.last_updated_at == now

    def test_benchmark_status_populated_from_events(self, mock_store: MagicMock) -> None:
        root = _make_task(task_id="pgm_1", title="Root", status="running", goal="Goal")
        mock_store.get_task.return_value = root
        mock_store.list_child_tasks.return_value = []
        mock_store.list_approvals.return_value = []
        mock_store.count_steps_by_status.return_value = {}
        mock_store.list_events.return_value = [
            {
                "event_type": "benchmark.completed",
                "occurred_at": time.time(),
                "payload": {"result": "pass"},
                "event_seq": 1,
            },
            {
                "event_type": "benchmark.completed",
                "occurred_at": time.time(),
                "payload": {"result": "fail"},
                "event_seq": 2,
            },
            {
                "event_type": "benchmark.completed",
                "occurred_at": time.time(),
                "payload": {"result": "pass"},
                "event_seq": 3,
            },
        ]

        svc = StatusProjectionService(mock_store)
        proj = svc.get_program_status("pgm_1")

        assert proj.latest_benchmark_status == "2/3 passed"


# ---------------------------------------------------------------------------
# Service: get_team_status
# ---------------------------------------------------------------------------


class TestGetTeamStatus:
    def test_raises_on_missing_team(self, mock_store: MagicMock) -> None:
        svc = StatusProjectionService(mock_store)
        with pytest.raises(KeyError, match="Team not found"):
            svc.get_team_status("missing")

    def test_team_with_workers(self, mock_store: MagicMock) -> None:
        team = _make_task(task_id="team_1", title="Team Alpha", status="running")
        w1 = _make_task(task_id="w1", title="W1", status="running", parent_task_id="team_1")
        w2 = _make_task(task_id="w2", title="W2", status="blocked", parent_task_id="team_1")
        w3 = _make_task(task_id="w3", title="W3", status="completed", parent_task_id="team_1")

        mock_store.get_task.return_value = team
        mock_store.list_child_tasks.return_value = [w1, w2, w3]

        svc = StatusProjectionService(mock_store)
        proj = svc.get_team_status("team_1")

        assert proj.team_id == "team_1"
        assert proj.title == "Team Alpha"
        assert proj.active_workers == 2  # running + blocked
        assert proj.milestone_progress == "1/3"
        assert len(proj.blockers) == 1
        assert "W2" in proj.blockers[0]

    def test_team_no_children(self, mock_store: MagicMock) -> None:
        team = _make_task(task_id="team_1", title="Solo", status="running")
        mock_store.get_task.return_value = team
        mock_store.list_child_tasks.return_value = []

        svc = StatusProjectionService(mock_store)
        proj = svc.get_team_status("team_1")

        assert proj.active_workers == 0
        assert proj.milestone_progress == "0/0"
        assert proj.blockers == []


# ---------------------------------------------------------------------------
# Service: get_task_status
# ---------------------------------------------------------------------------


class TestGetTaskStatus:
    def test_raises_on_missing_task(self, mock_store: MagicMock) -> None:
        svc = StatusProjectionService(mock_store)
        with pytest.raises(KeyError, match="Task not found"):
            svc.get_task_status("missing")

    def test_task_with_steps(self, mock_store: MagicMock) -> None:
        task = _make_task(
            task_id="t_1",
            title="Refactor module",
            status="running",
            goal="Refactor the memory module",
            priority="high",
        )
        mock_store.get_task.return_value = task
        mock_store.count_steps_by_status.return_value = {
            "completed": 3,
            "running": 1,
            "blocked": 1,
            "failed": 0,
            "queued": 2,
        }
        blocked_step = _make_step(
            step_id="s_blocked",
            task_id="t_1",
            status="blocked",
            title="Run benchmark",
        )
        running_step = _make_step(step_id="s_running", task_id="t_1", status="running")
        mock_store.list_steps.return_value = [blocked_step, running_step]
        mock_store.list_approvals.return_value = [_make_approval(task_id="t_1")]
        mock_store.list_events.return_value = [
            {
                "event_type": "step.completed",
                "occurred_at": time.time(),
                "payload": {},
                "event_seq": 1,
            },
        ]

        svc = StatusProjectionService(mock_store)
        proj = svc.get_task_status("t_1")

        assert proj.task_id == "t_1"
        assert proj.title == "Refactor module"
        assert proj.state == "running"
        assert proj.goal == "Refactor the memory module"
        assert proj.priority == "high"
        assert proj.total_steps == 7  # 3 + 1 + 1 + 0 + 2
        assert proj.completed_steps == 3
        assert proj.running_steps == 1
        assert proj.blocked_steps == 1
        assert proj.failed_steps == 0
        assert proj.pending_approvals == 1
        assert proj.latest_event == "step.completed"
        assert len(proj.blockers) == 1
        assert "Run benchmark" in proj.blockers[0]

    def test_task_no_steps_no_events(self, mock_store: MagicMock) -> None:
        task = _make_task(task_id="t_2", title="Simple", status="queued", goal="Simple task")
        mock_store.get_task.return_value = task
        mock_store.count_steps_by_status.return_value = {}
        mock_store.list_steps.return_value = []
        mock_store.list_approvals.return_value = []
        mock_store.list_events.return_value = []

        svc = StatusProjectionService(mock_store)
        proj = svc.get_task_status("t_2")

        assert proj.total_steps == 0
        assert proj.latest_event == ""
        assert proj.blockers == []

    def test_task_preserves_parent_id(self, mock_store: MagicMock) -> None:
        task = _make_task(task_id="t_3", title="Child", status="running", parent_task_id="pgm_1")
        mock_store.get_task.return_value = task
        mock_store.count_steps_by_status.return_value = {}
        mock_store.list_steps.return_value = []
        mock_store.list_approvals.return_value = []
        mock_store.list_events.return_value = []

        svc = StatusProjectionService(mock_store)
        proj = svc.get_task_status("t_3")

        assert proj.parent_task_id == "pgm_1"


# ---------------------------------------------------------------------------
# Service: get_attempt_status
# ---------------------------------------------------------------------------


class TestGetAttemptStatus:
    def test_raises_on_missing_attempt(self, mock_store: MagicMock) -> None:
        svc = StatusProjectionService(mock_store)
        with pytest.raises(KeyError, match="Step attempt not found"):
            svc.get_attempt_status("missing")

    def test_running_attempt(self, mock_store: MagicMock) -> None:
        attempt = _make_step_attempt(
            step_attempt_id="sa_1",
            task_id="t_1",
            step_id="s_1",
            attempt=1,
            status="running",
            approval_id="appr_1",
            capability_grant_id="grant_1",
            started_at=1700000000.0,
        )
        mock_store.get_step_attempt.return_value = attempt

        svc = StatusProjectionService(mock_store)
        proj = svc.get_attempt_status("sa_1")

        assert proj.step_attempt_id == "sa_1"
        assert proj.task_id == "t_1"
        assert proj.step_id == "s_1"
        assert proj.attempt_number == 1
        assert proj.status == "running"
        assert proj.has_approval is True
        assert proj.has_capability_grant is True
        assert proj.started_at == 1700000000.0
        assert proj.finished_at == 0.0
        assert proj.failure_reason == ""

    def test_failed_attempt_with_reason(self, mock_store: MagicMock) -> None:
        attempt = _make_step_attempt(
            step_attempt_id="sa_2",
            task_id="t_1",
            step_id="s_1",
            attempt=2,
            status="failed",
            started_at=1700000000.0,
            finished_at=1700000010.0,
        )
        mock_store.get_step_attempt.return_value = attempt
        mock_store.list_events.return_value = [
            {
                "event_type": "step.attempt.failed",
                "occurred_at": 1700000010.0,
                "payload": {
                    "step_attempt_id": "sa_2",
                    "reason": "Test assertion failed: expected 42 got 0",
                },
                "event_seq": 5,
            },
        ]

        svc = StatusProjectionService(mock_store)
        proj = svc.get_attempt_status("sa_2")

        assert proj.status == "failed"
        assert proj.attempt_number == 2
        assert "Test assertion failed" in proj.failure_reason
        assert proj.finished_at == 1700000010.0

    def test_waiting_attempt(self, mock_store: MagicMock) -> None:
        attempt = _make_step_attempt(
            step_attempt_id="sa_3",
            task_id="t_1",
            step_id="s_1",
            attempt=1,
            status="waiting",
            waiting_reason="awaiting_approval",
        )
        mock_store.get_step_attempt.return_value = attempt

        svc = StatusProjectionService(mock_store)
        proj = svc.get_attempt_status("sa_3")

        assert proj.waiting_reason == "awaiting_approval"
        assert proj.has_approval is False


# ---------------------------------------------------------------------------
# Service: get_approval_queue
# ---------------------------------------------------------------------------


class TestGetApprovalQueue:
    def test_empty_queue(self, mock_store: MagicMock) -> None:
        mock_store.list_approvals.return_value = []

        svc = StatusProjectionService(mock_store)
        proj = svc.get_approval_queue()

        assert proj.total_count == 0
        assert proj.high_priority_count == 0
        assert proj.pending_approvals == []

    def test_queue_with_items(self, mock_store: MagicMock) -> None:
        a1 = _make_approval(approval_id="a1", task_id="t1")
        a2 = _make_approval(approval_id="a2", task_id="t2")
        mock_store.list_approvals.return_value = [a1, a2]

        t1 = _make_task(task_id="t1", priority="high")
        t2 = _make_task(task_id="t2", priority="normal")
        mock_store.get_task.side_effect = lambda tid: {"t1": t1, "t2": t2}.get(tid)

        svc = StatusProjectionService(mock_store)
        proj = svc.get_approval_queue()

        assert proj.total_count == 2
        assert proj.high_priority_count == 1
        assert len(proj.pending_approvals) == 2
        assert proj.pending_approvals[0]["approval_id"] == "a1"

    def test_approval_dict_fields(self, mock_store: MagicMock) -> None:
        a = _make_approval(
            approval_id="a_x",
            task_id="t_x",
            action_type="bash",
            tool_name="bash",
        )
        mock_store.list_approvals.return_value = [a]
        mock_store.get_task.return_value = _make_task(task_id="t_x", priority="low")

        svc = StatusProjectionService(mock_store)
        proj = svc.get_approval_queue()

        item = proj.pending_approvals[0]
        assert item["approval_id"] == "a_x"
        assert item["task_id"] == "t_x"
        assert item["action_type"] == "bash"
        assert item["tool_name"] == "bash"
        assert "requested_at" in item


# ---------------------------------------------------------------------------
# Service: get_benchmark_status
# ---------------------------------------------------------------------------


class TestGetBenchmarkStatus:
    def test_no_events(self, mock_store: MagicMock) -> None:
        mock_store.list_tasks.return_value = []

        svc = StatusProjectionService(mock_store)
        proj = svc.get_benchmark_status()

        assert proj.pass_rate == 0.0
        assert proj.recent_runs == []
        assert proj.regressions == []

    def test_scoped_to_program(self, mock_store: MagicMock) -> None:
        mock_store.list_events.return_value = [
            {
                "event_type": "benchmark.completed",
                "occurred_at": time.time(),
                "payload": {"result": "pass"},
                "event_seq": 1,
            },
            {
                "event_type": "benchmark.completed",
                "occurred_at": time.time(),
                "payload": {"result": "fail"},
                "event_seq": 2,
            },
        ]

        svc = StatusProjectionService(mock_store)
        proj = svc.get_benchmark_status(program_id="pgm_1")

        assert len(proj.recent_runs) == 2
        assert proj.pass_rate == 50.0

    def test_regression_detected(self, mock_store: MagicMock) -> None:
        mock_store.list_events.return_value = [
            {
                "event_type": "task.note.appended",
                "occurred_at": time.time(),
                "payload": {"raw_text": "Regression found in module X"},
                "event_seq": 1,
            },
        ]

        svc = StatusProjectionService(mock_store)
        proj = svc.get_benchmark_status(program_id="pgm_1")

        assert len(proj.regressions) == 1
        assert "regression" in proj.regressions[0].lower()

    def test_global_benchmark_iterates_tasks(self, mock_store: MagicMock) -> None:
        t1 = _make_task(task_id="t1")
        t2 = _make_task(task_id="t2")
        mock_store.list_tasks.return_value = [t1, t2]

        def events_for(*, task_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
            if task_id == "t1":
                return [
                    {
                        "event_type": "benchmark.completed",
                        "occurred_at": time.time(),
                        "payload": {"result": "passed"},
                        "event_seq": 1,
                    }
                ]
            return []

        mock_store.list_events.side_effect = events_for

        svc = StatusProjectionService(mock_store)
        proj = svc.get_benchmark_status()

        assert len(proj.recent_runs) == 1
        assert proj.pass_rate == 100.0


# ---------------------------------------------------------------------------
# Service: format_program_summary (spec format compliance)
# ---------------------------------------------------------------------------


class TestFormatProgramSummary:
    def test_basic_summary_has_spec_sections(self) -> None:
        """Spec requires: 状态, 当前阶段, 进度, 活跃团队, 阻塞项, 最近结果, 是否需要操作."""
        proj = ProgramStatusProjection(
            program_id="pgm_1",
            title="My Program",
            overall_state="running",
            progress_pct=42.5,
            current_phase="building",
            active_teams=2,
            queued_tasks=3,
            running_attempts=1,
            blocked_items=0,
            last_updated_at=1700000000.0,
        )

        text = StatusProjectionService.format_program_summary(proj)

        # Spec: 状态
        assert "Status: running" in text
        # Spec: 当前阶段
        assert "Phase: building" in text
        # Spec: 进度
        assert "Progress: 42.5%" in text
        # Spec: 活跃团队
        assert "Active teams: 2" in text
        # Spec: 阻塞项
        assert "Blocked items: 0" in text
        # Spec: 是否需要操作
        assert "Action required: No" in text
        # Title
        assert "Program: My Program" in text
        # Timestamp
        assert "Last updated:" in text

    def test_action_required_when_awaiting_human(self) -> None:
        proj = ProgramStatusProjection(
            program_id="pgm_1",
            title="Blocked Prog",
            overall_state="blocked",
            awaiting_human=True,
            last_updated_at=1700000000.0,
        )

        text = StatusProjectionService.format_program_summary(proj)
        assert "Action required: Yes" in text
        assert "Pending human approval" in text

    def test_action_required_when_blocked_items(self) -> None:
        proj = ProgramStatusProjection(
            program_id="pgm_1",
            title="Has blockers",
            overall_state="running",
            blocked_items=2,
            last_updated_at=1700000000.0,
        )

        text = StatusProjectionService.format_program_summary(proj)
        assert "Action required: Yes" in text
        assert "2 blocked item(s)" in text

    def test_action_required_both_reasons(self) -> None:
        proj = ProgramStatusProjection(
            program_id="pgm_1",
            title="Both",
            overall_state="blocked",
            awaiting_human=True,
            blocked_items=1,
            last_updated_at=1700000000.0,
        )

        text = StatusProjectionService.format_program_summary(proj)
        assert "Action required: Yes" in text
        assert "Pending human approval" in text
        assert "1 blocked item(s)" in text

    def test_risks_shown(self) -> None:
        proj = ProgramStatusProjection(
            program_id="pgm_1",
            title="Risky Prog",
            overall_state="running",
            latest_risks=["Disk space low", "API rate limit"],
            last_updated_at=1700000000.0,
        )

        text = StatusProjectionService.format_program_summary(proj)
        assert "Risks:" in text
        assert "Disk space low" in text
        assert "API rate limit" in text

    def test_summary_shown(self) -> None:
        proj = ProgramStatusProjection(
            program_id="pgm_1",
            title="Prog",
            overall_state="running",
            latest_summary="Refactoring the memory module",
            last_updated_at=1700000000.0,
        )

        text = StatusProjectionService.format_program_summary(proj)
        assert "Summary: Refactoring the memory module" in text

    def test_benchmark_status_shown(self) -> None:
        proj = ProgramStatusProjection(
            program_id="pgm_1",
            title="Prog",
            overall_state="running",
            latest_benchmark_status="3/5 passed",
            last_updated_at=1700000000.0,
        )

        text = StatusProjectionService.format_program_summary(proj)
        assert "Benchmark: 3/5 passed" in text

    def test_no_risks_no_benchmark_clean(self) -> None:
        proj = ProgramStatusProjection(
            program_id="pgm_1",
            title="Clean",
            overall_state="completed",
            progress_pct=100.0,
            last_updated_at=1700000000.0,
        )

        text = StatusProjectionService.format_program_summary(proj)
        assert "Risks" not in text
        assert "Benchmark" not in text
        assert "Action required: No" in text


# ---------------------------------------------------------------------------
# Service: format_team_summary
# ---------------------------------------------------------------------------


class TestFormatTeamSummary:
    def test_basic_team_summary(self) -> None:
        proj = TeamStatusProjection(
            team_id="t_1",
            title="Execution Team",
            state="running",
            workspace="ws_exec",
            active_workers=3,
            milestone_progress="2/5",
        )

        text = StatusProjectionService.format_team_summary(proj)

        assert "Team: Execution Team" in text
        assert "State: running" in text
        assert "Workspace: ws_exec" in text
        assert "Active workers: 3" in text
        assert "Milestone progress: 2/5" in text

    def test_team_summary_with_blockers(self) -> None:
        proj = TeamStatusProjection(
            team_id="t_1",
            title="Blocked Team",
            state="blocked",
            blockers=["Task X waiting for approval", "Task Y dependency unmet"],
        )

        text = StatusProjectionService.format_team_summary(proj)

        assert "Blockers:" in text
        assert "Task X waiting for approval" in text
        assert "Task Y dependency unmet" in text

    def test_team_default_workspace(self) -> None:
        proj = TeamStatusProjection(team_id="t_1", title="Default WS", state="running")
        text = StatusProjectionService.format_team_summary(proj)
        assert "Workspace: (default)" in text


# ---------------------------------------------------------------------------
# Service: format_task_summary
# ---------------------------------------------------------------------------


class TestFormatTaskSummary:
    def test_basic_task_summary(self) -> None:
        proj = TaskStatusProjection(
            task_id="t_1",
            title="Refactor memory",
            state="running",
            goal="Refactor the memory subsystem",
            priority="high",
            total_steps=10,
            completed_steps=5,
            running_steps=2,
            blocked_steps=1,
            failed_steps=1,
            pending_approvals=1,
            latest_event="step.completed",
            last_updated_at=1700000000.0,
        )

        text = StatusProjectionService.format_task_summary(proj)

        assert "Task: Refactor memory [running]" in text
        assert "Goal: Refactor the memory subsystem" in text
        assert "Priority: high" in text
        assert "5/10 completed" in text
        assert "2 running" in text
        assert "1 blocked" in text
        assert "1 failed" in text
        assert "Pending approvals: 1" in text
        assert "Latest event: step.completed" in text
        assert "Last updated:" in text

    def test_task_summary_with_blockers(self) -> None:
        proj = TaskStatusProjection(
            task_id="t_1",
            title="Stuck task",
            state="blocked",
            blockers=["run_benchmark [s_3]"],
            last_updated_at=1700000000.0,
        )

        text = StatusProjectionService.format_task_summary(proj)
        assert "Blockers:" in text
        assert "run_benchmark [s_3]" in text


# ---------------------------------------------------------------------------
# Service: format_attempt_summary
# ---------------------------------------------------------------------------


class TestFormatAttemptSummary:
    def test_running_attempt_summary(self) -> None:
        proj = AttemptStatusProjection(
            step_attempt_id="sa_1",
            task_id="t_1",
            step_id="s_1",
            attempt_number=1,
            status="running",
            has_approval=True,
            has_capability_grant=True,
            started_at=1700000000.0,
        )

        text = StatusProjectionService.format_attempt_summary(proj)

        assert "Attempt: sa_1" in text
        assert "(#1)" in text
        assert "[running]" in text
        assert "Task: t_1" in text
        assert "Step: s_1" in text
        assert "Has approval: yes" in text
        assert "Has capability grant: yes" in text
        assert "Started:" in text

    def test_failed_attempt_summary(self) -> None:
        proj = AttemptStatusProjection(
            step_attempt_id="sa_2",
            task_id="t_1",
            step_id="s_1",
            attempt_number=2,
            status="failed",
            failure_reason="Module not found",
            started_at=1700000000.0,
            finished_at=1700000010.0,
        )

        text = StatusProjectionService.format_attempt_summary(proj)

        assert "[failed]" in text
        assert "Failure reason: Module not found" in text
        assert "Started:" in text
        assert "Finished:" in text

    def test_waiting_attempt_summary(self) -> None:
        proj = AttemptStatusProjection(
            step_attempt_id="sa_3",
            task_id="t_1",
            step_id="s_1",
            attempt_number=1,
            status="waiting",
            waiting_reason="awaiting_approval",
        )

        text = StatusProjectionService.format_attempt_summary(proj)
        assert "Waiting reason: awaiting_approval" in text
