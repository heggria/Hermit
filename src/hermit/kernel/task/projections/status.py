"""CQRS-style read models for program, team, task, attempt, approval, and benchmark status.

Each projection dataclass is a denormalized, read-optimized view assembled
from the kernel store.  ``StatusProjectionService`` builds projections on
demand — no background materializer is required.

The spec requires support for four query granularity levels:
  - Program level (ProgramStatusProjection)
  - Team level (TeamStatusProjection)
  - Task level (TaskStatusProjection)
  - Attempt level (AttemptStatusProjection)

All read-path queries are side-effect free — no worker dispatch, no mutations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import ApprovalRecord, StepRecord, TaskRecord

# ---------------------------------------------------------------------------
# Projection dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProgramStatusProjection:
    """Top-level read model for a program (root task tree)."""

    program_id: str
    title: str
    overall_state: str
    progress_pct: float = 0.0
    current_phase: str = ""
    active_teams: int = 0
    queued_tasks: int = 0
    running_attempts: int = 0
    blocked_items: int = 0
    awaiting_human: bool = False
    latest_summary: str = ""
    latest_risks: list[str] = field(default_factory=list)
    latest_benchmark_status: str = ""
    last_updated_at: float = 0.0


@dataclass
class TeamStatusProjection:
    """Read model for a single team (child task group)."""

    team_id: str
    title: str
    state: str
    workspace: str = ""
    active_workers: int = 0
    milestone_progress: str = ""
    blockers: list[str] = field(default_factory=list)


@dataclass
class TaskStatusProjection:
    """Read model for a single task — answers 'why is Task X stuck?'."""

    task_id: str
    title: str
    state: str
    goal: str = ""
    priority: str = "normal"
    parent_task_id: str | None = None
    total_steps: int = 0
    completed_steps: int = 0
    running_steps: int = 0
    blocked_steps: int = 0
    failed_steps: int = 0
    pending_approvals: int = 0
    latest_event: str = ""
    blockers: list[str] = field(default_factory=list)
    last_updated_at: float = 0.0


@dataclass
class AttemptStatusProjection:
    """Read model for a single step attempt — answers 'why did attempt Y fail?'."""

    step_attempt_id: str
    task_id: str
    step_id: str
    attempt_number: int
    status: str
    waiting_reason: str = ""
    has_approval: bool = False
    has_capability_grant: bool = False
    started_at: float = 0.0
    finished_at: float = 0.0
    failure_reason: str = ""


@dataclass
class ApprovalQueueProjection:
    """Aggregated view of pending approval items."""

    pending_approvals: list[dict[str, Any]] = field(default_factory=list)
    total_count: int = 0
    high_priority_count: int = 0


@dataclass
class BenchmarkStatusProjection:
    """Summary of recent benchmark activity."""

    recent_runs: list[dict[str, Any]] = field(default_factory=list)
    pass_rate: float = 0.0
    regressions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ACTIVE_STATUSES = frozenset({"queued", "running", "blocked", "planning_ready"})
_BLOCKED_STATUSES = frozenset({"blocked"})


def _progress_from_children(children: list[TaskRecord]) -> float:
    """Derive a 0–100 progress percentage from child task statuses."""
    if not children:
        return 0.0
    terminal = sum(1 for c in children if c.status in ("completed", "failed", "cancelled"))
    return round(terminal / len(children) * 100, 1)


def _current_phase(task: TaskRecord, children: list[TaskRecord]) -> str:
    """Infer the current phase label from the root task and its children."""
    if task.status in ("completed", "failed", "cancelled"):
        return task.status
    running = [c for c in children if c.status == "running"]
    if running:
        return running[0].title or "running"
    blocked = [c for c in children if c.status == "blocked"]
    if blocked:
        return f"blocked ({len(blocked)})"
    queued = [c for c in children if c.status == "queued"]
    if queued:
        return "queued"
    return task.status or "unknown"


def _extract_risks(events: list[dict[str, Any]]) -> list[str]:
    """Pull risk-related notes from the event stream."""
    risks: list[str] = []
    for event in reversed(events):
        if event["event_type"] == "task.note.appended":
            text = str(event["payload"].get("raw_text") or "")
            lower = text.lower()
            if "risk" in lower or "blocker" in lower or "warning" in lower:
                risks.append(text[:200].strip())
        if len(risks) >= 5:
            break
    return risks


def _approval_to_dict(approval: ApprovalRecord) -> dict[str, Any]:
    """Convert an ApprovalRecord to a lightweight dict for the projection."""
    action_type = str(approval.requested_action.get("action_type", ""))
    tool_name = str(approval.requested_action.get("tool_name", ""))
    return {
        "approval_id": approval.approval_id,
        "task_id": approval.task_id,
        "step_id": approval.step_id,
        "approval_type": approval.approval_type,
        "action_type": action_type,
        "tool_name": tool_name,
        "requested_at": approval.requested_at or 0.0,
    }


def _derive_benchmark_status(events: list[dict[str, Any]]) -> str:
    """Derive a short benchmark status label from events."""
    total = 0
    passed = 0
    for event in events:
        if "benchmark" in str(event.get("event_type", "")).lower():
            total += 1
            result = str((event.get("payload") or {}).get("result", "")).lower()
            if result in ("pass", "passed", "ok"):
                passed += 1
    if total == 0:
        return ""
    return f"{passed}/{total} passed"


def _step_blockers(steps: list[StepRecord]) -> list[str]:
    """Extract blocker descriptions from blocked steps."""
    blockers: list[str] = []
    for step in steps:
        if step.status == "blocked":
            label = step.title or step.node_key or step.step_id
            blockers.append(f"{label} [{step.step_id}]")
    return blockers


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class StatusProjectionService:
    """Assembles CQRS read-model projections from the kernel store."""

    def __init__(self, store: KernelStore) -> None:
        self.store = store

    # -- Program status -----------------------------------------------------

    def get_program_status(self, program_id: str) -> ProgramStatusProjection:
        """Build a ``ProgramStatusProjection`` for the given root task."""
        task = self.store.get_task(program_id)
        if task is None:
            raise KeyError(f"Program not found: {program_id}")

        children = self.store.list_child_tasks(parent_task_id=program_id, limit=200)
        events = self.store.list_events(task_id=program_id, limit=500)

        queued = sum(1 for c in children if c.status == "queued")
        blocked = sum(1 for c in children if c.status in _BLOCKED_STATUSES)
        active_teams = sum(1 for c in children if c.status in _ACTIVE_STATUSES)

        pending_approvals = self.store.list_approvals(task_id=program_id, status="pending")
        child_pending: list[ApprovalRecord] = []
        for child in children:
            child_pending.extend(self.store.list_approvals(task_id=child.task_id, status="pending"))
        awaiting_human = len(pending_approvals) > 0 or len(child_pending) > 0

        step_counts = self.store.count_steps_by_status(task_id=program_id)
        running_attempts = int(step_counts.get("running", 0))

        risks = _extract_risks(events)

        last_updated = task.updated_at
        for child in children:
            if child.updated_at > last_updated:
                last_updated = child.updated_at

        # Derive benchmark status from events for this program.
        benchmark_status = _derive_benchmark_status(events)

        return ProgramStatusProjection(
            program_id=program_id,
            title=task.title,
            overall_state=task.status,
            progress_pct=_progress_from_children(children),
            current_phase=_current_phase(task, children),
            active_teams=active_teams,
            queued_tasks=queued,
            running_attempts=running_attempts,
            blocked_items=blocked,
            awaiting_human=awaiting_human,
            latest_summary=task.goal[:300] if task.goal else "",
            latest_risks=risks,
            latest_benchmark_status=benchmark_status,
            last_updated_at=last_updated,
        )

    # -- Team status --------------------------------------------------------

    def get_team_status(self, team_id: str) -> TeamStatusProjection:
        """Build a ``TeamStatusProjection`` for a child task acting as a team."""
        task = self.store.get_task(team_id)
        if task is None:
            raise KeyError(f"Team not found: {team_id}")

        children = self.store.list_child_tasks(parent_task_id=team_id, limit=200)
        active_workers = sum(1 for c in children if c.status in _ACTIVE_STATUSES)

        blockers: list[str] = []
        for child in children:
            if child.status == "blocked":
                blockers.append(f"{child.title} [{child.task_id}]")

        total = len(children)
        done = sum(1 for c in children if c.status in ("completed", "failed", "cancelled"))
        milestone = f"{done}/{total}" if total > 0 else "0/0"

        return TeamStatusProjection(
            team_id=team_id,
            title=task.title,
            state=task.status,
            workspace="",
            active_workers=active_workers,
            milestone_progress=milestone,
            blockers=blockers,
        )

    # -- Task status --------------------------------------------------------

    def get_task_status(self, task_id: str) -> TaskStatusProjection:
        """Build a ``TaskStatusProjection`` for a single task.

        Answers questions like 'why is Task X stuck?' by summarising its
        step DAG, pending approvals, and recent events.
        """
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")

        step_counts = self.store.count_steps_by_status(task_id=task_id)
        total_steps = sum(step_counts.values())
        completed_steps = int(step_counts.get("completed", 0))
        running_steps = int(step_counts.get("running", 0))
        blocked_steps = int(step_counts.get("blocked", 0))
        failed_steps = int(step_counts.get("failed", 0))

        steps = self.store.list_steps(task_id=task_id, limit=200)
        blockers = _step_blockers(steps)

        pending = self.store.list_approvals(task_id=task_id, status="pending")

        events = self.store.list_events(task_id=task_id, limit=5)
        latest_event = ""
        if events:
            last = events[-1]
            latest_event = str(last.get("event_type", ""))

        return TaskStatusProjection(
            task_id=task_id,
            title=task.title,
            state=task.status,
            goal=task.goal[:300] if task.goal else "",
            priority=task.priority,
            parent_task_id=task.parent_task_id,
            total_steps=total_steps,
            completed_steps=completed_steps,
            running_steps=running_steps,
            blocked_steps=blocked_steps,
            failed_steps=failed_steps,
            pending_approvals=len(pending),
            latest_event=latest_event,
            blockers=blockers,
            last_updated_at=task.updated_at,
        )

    # -- Attempt status -----------------------------------------------------

    def get_attempt_status(self, step_attempt_id: str) -> AttemptStatusProjection:
        """Build an ``AttemptStatusProjection`` for a single step attempt.

        Answers questions like 'why did this benchmark attempt fail?'.
        """
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            raise KeyError(f"Step attempt not found: {step_attempt_id}")

        # Derive failure reason from events if the attempt is terminal.
        failure_reason = ""
        if attempt.status in ("failed", "cancelled"):
            events = self.store.list_events(task_id=attempt.task_id, limit=100)
            for event in reversed(events):
                payload = event.get("payload") or {}
                if str(payload.get("step_attempt_id", "")) == step_attempt_id:
                    reason = str(payload.get("reason") or payload.get("error") or "")
                    if reason:
                        failure_reason = reason[:300]
                        break

        return AttemptStatusProjection(
            step_attempt_id=step_attempt_id,
            task_id=attempt.task_id,
            step_id=attempt.step_id,
            attempt_number=attempt.attempt,
            status=attempt.status,
            waiting_reason=attempt.waiting_reason or "",
            has_approval=attempt.approval_id is not None,
            has_capability_grant=attempt.capability_grant_id is not None,
            started_at=attempt.started_at or 0.0,
            finished_at=attempt.finished_at or 0.0,
            failure_reason=failure_reason,
        )

    # -- Approval queue -----------------------------------------------------

    def get_approval_queue(self) -> ApprovalQueueProjection:
        """Build an ``ApprovalQueueProjection`` across all tasks."""
        pending = self.store.list_approvals(status="pending", limit=200)
        items = [_approval_to_dict(a) for a in pending]

        high_count = 0
        for approval in pending:
            task = self.store.get_task(approval.task_id)
            if task is not None and task.priority == "high":
                high_count += 1

        return ApprovalQueueProjection(
            pending_approvals=items,
            total_count=len(items),
            high_priority_count=high_count,
        )

    # -- Benchmark status ---------------------------------------------------

    def get_benchmark_status(self, program_id: str | None = None) -> BenchmarkStatusProjection:
        """Build a ``BenchmarkStatusProjection``.

        When *program_id* is given, scopes events to that task tree;
        otherwise returns a global summary.
        """
        events: list[dict[str, Any]] = []
        if program_id is not None:
            events = self.store.list_events(task_id=program_id, limit=500)
        else:
            for task in self.store.list_tasks(limit=100):
                events.extend(self.store.list_events(task_id=task.task_id, limit=100))

        runs: list[dict[str, Any]] = []
        regressions: list[str] = []
        for event in events:
            payload = dict(event.get("payload") or {})
            if "benchmark" in str(event.get("event_type", "")).lower():
                runs.append(
                    {
                        "event_type": event["event_type"],
                        "occurred_at": float(event["occurred_at"]),
                        "payload": payload,
                    }
                )
            raw_text = str(payload.get("raw_text") or "")
            if "regression" in raw_text.lower():
                regressions.append(raw_text[:200].strip())

        total = len(runs)
        passed = sum(
            1
            for r in runs
            if str(r.get("payload", {}).get("result", "")).lower() in ("pass", "passed", "ok")
        )
        pass_rate = round(passed / total * 100, 1) if total > 0 else 0.0

        return BenchmarkStatusProjection(
            recent_runs=runs[-20:],
            pass_rate=pass_rate,
            regressions=regressions[:10],
        )

    # -- Formatting ---------------------------------------------------------

    @staticmethod
    def format_program_summary(projection: ProgramStatusProjection) -> str:
        """Return a human-readable multi-line summary of a program projection.

        Matches the spec format: 状态, 当前阶段, 进度, 活跃团队, 阻塞项,
        最近结果, 是否需要操作.
        """
        lines: list[str] = [
            f"Program: {projection.title}",
            "",
            f"Status: {projection.overall_state}",
            f"Phase: {projection.current_phase}",
            f"Progress: {projection.progress_pct}%",
            f"Active teams: {projection.active_teams}",
            f"Running attempts: {projection.running_attempts}",
            f"Queued tasks: {projection.queued_tasks}",
            f"Blocked items: {projection.blocked_items}",
        ]
        if projection.latest_summary:
            lines.append(f"Summary: {projection.latest_summary}")
        if projection.latest_risks:
            lines.append("Risks:")
            for risk in projection.latest_risks:
                lines.append(f"  - {risk}")
        if projection.latest_benchmark_status:
            lines.append(f"Benchmark: {projection.latest_benchmark_status}")
        # Action required section — spec: 是否需要操作
        if projection.awaiting_human or projection.blocked_items > 0:
            lines.append("Action required: Yes")
            reasons: list[str] = []
            if projection.awaiting_human:
                reasons.append("Pending human approval")
            if projection.blocked_items > 0:
                reasons.append(f"{projection.blocked_items} blocked item(s)")
            for reason in reasons:
                lines.append(f"  - {reason}")
        else:
            lines.append("Action required: No")
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(projection.last_updated_at))
        lines.append(f"Last updated: {ts}")
        return "\n".join(lines)

    @staticmethod
    def format_team_summary(projection: TeamStatusProjection) -> str:
        """Return a human-readable multi-line summary of a team projection."""
        lines: list[str] = [
            f"Team: {projection.title}",
            "",
            f"State: {projection.state}",
            f"Workspace: {projection.workspace or '(default)'}",
            f"Active workers: {projection.active_workers}",
            f"Milestone progress: {projection.milestone_progress}",
        ]
        if projection.blockers:
            lines.append("Blockers:")
            for blocker in projection.blockers:
                lines.append(f"  - {blocker}")
        return "\n".join(lines)

    @staticmethod
    def format_task_summary(projection: TaskStatusProjection) -> str:
        """Return a human-readable multi-line summary of a task projection."""
        lines: list[str] = [
            f"Task: {projection.title} [{projection.state}]",
            "",
            f"Goal: {projection.goal}",
            f"Priority: {projection.priority}",
            f"Steps: {projection.completed_steps}/{projection.total_steps} completed, "
            f"{projection.running_steps} running, "
            f"{projection.blocked_steps} blocked, "
            f"{projection.failed_steps} failed",
            f"Pending approvals: {projection.pending_approvals}",
        ]
        if projection.latest_event:
            lines.append(f"Latest event: {projection.latest_event}")
        if projection.blockers:
            lines.append("Blockers:")
            for blocker in projection.blockers:
                lines.append(f"  - {blocker}")
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(projection.last_updated_at))
        lines.append(f"Last updated: {ts}")
        return "\n".join(lines)

    @staticmethod
    def format_attempt_summary(projection: AttemptStatusProjection) -> str:
        """Return a human-readable multi-line summary of an attempt projection."""
        lines: list[str] = [
            f"Attempt: {projection.step_attempt_id} "
            f"(#{projection.attempt_number}) [{projection.status}]",
            "",
            f"Task: {projection.task_id}",
            f"Step: {projection.step_id}",
        ]
        if projection.waiting_reason:
            lines.append(f"Waiting reason: {projection.waiting_reason}")
        if projection.has_approval:
            lines.append("Has approval: yes")
        if projection.has_capability_grant:
            lines.append("Has capability grant: yes")
        if projection.failure_reason:
            lines.append(f"Failure reason: {projection.failure_reason}")
        if projection.started_at:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(projection.started_at))
            lines.append(f"Started: {ts}")
        if projection.finished_at:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(projection.finished_at))
            lines.append(f"Finished: {ts}")
        return "\n".join(lines)


__all__ = [
    "ApprovalQueueProjection",
    "AttemptStatusProjection",
    "BenchmarkStatusProjection",
    "ProgramStatusProjection",
    "StatusProjectionService",
    "TaskStatusProjection",
    "TeamStatusProjection",
]
