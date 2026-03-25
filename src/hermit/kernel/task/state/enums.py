"""Formal state enums for Task and StepAttempt lifecycle.

These StrEnums replace the scattered string literals used throughout the
kernel.  Values match the existing strings exactly to maintain backward
compatibility.
"""

from __future__ import annotations

from enum import StrEnum


class TaskState(StrEnum):
    """All valid task states."""

    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    PLANNING_READY = "planning_ready"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"
    NEEDS_ATTENTION = "needs_attention"
    RECONCILING = "reconciling"


class StepAttemptState(StrEnum):
    """All valid step attempt states."""

    READY = "ready"
    WAITING = "waiting"
    RUNNING = "running"
    DISPATCHING = "dispatching"
    CONTRACTING = "contracting"
    PREFLIGHTING = "preflighting"
    OBSERVING = "observing"
    RECONCILING = "reconciling"
    POLICY_PENDING = "policy_pending"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_PLAN_CONFIRMATION = "awaiting_plan_confirmation"
    VERIFICATION_BLOCKED = "verification_blocked"
    RECEIPT_PENDING = "receipt_pending"
    SUCCEEDED = "succeeded"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class WaitingKind(StrEnum):
    """All valid waiting reasons for step attempts."""

    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_PLAN_CONFIRMATION = "awaiting_plan_confirmation"
    DEPENDENCY_FAILED = "dependency_failed"
    INPUT_CHANGED_REENTER_POLICY = "input_changed_reenter_policy"
    REENTRY_RESUMED = "reentry_resumed"
    OBSERVING = "observing"


TERMINAL_TASK_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        # BUDGET_EXCEEDED and NEEDS_ATTENTION are end-states: the task cannot
        # make forward progress without external intervention, so they must be
        # treated as terminal to avoid them being silently ignored by cleanup,
        # scheduling, and progress-check logic.
        TaskState.BUDGET_EXCEEDED,
        TaskState.NEEDS_ATTENTION,
    }
)

ACTIVE_TASK_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.QUEUED,
        TaskState.RUNNING,
        TaskState.BLOCKED,
        TaskState.PLANNING_READY,
    }
)

# States where the task exists but is neither actively running nor terminally
# done.  Callers previously had to infer this by exclusion; naming it
# explicitly avoids subtle off-by-one errors.
SUSPENDED_TASK_STATES: frozenset[TaskState] = frozenset(
    {
        TaskState.PAUSED,
        TaskState.RECONCILING,
    }
)

TERMINAL_ATTEMPT_STATES: frozenset[StepAttemptState] = frozenset(
    {
        StepAttemptState.SUCCEEDED,
        StepAttemptState.COMPLETED,
        StepAttemptState.SKIPPED,
        StepAttemptState.FAILED,
        StepAttemptState.SUPERSEDED,
    }
)
