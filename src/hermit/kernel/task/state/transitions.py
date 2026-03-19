"""Transition tables and validators for the formal state machine.

Each state machine defines a mapping of ``current_state -> set[valid_targets]``.
The ``validate_*`` helpers check legality without side effects; the
``require_*`` variants raise ``InvalidTransitionError`` on illegal moves.
"""

from __future__ import annotations

from hermit.kernel.task.state.enums import StepAttemptState, TaskState


class InvalidTransitionError(ValueError):
    """Raised when a state transition is not allowed."""

    def __init__(self, entity_type: str, current: str, target: str) -> None:
        self.entity_type = entity_type
        self.current = current
        self.target = target
        super().__init__(f"Invalid {entity_type} transition: {current!r} -> {target!r}")


# ── Task state machine ────────────────────────────────────────────────

VALID_TASK_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.QUEUED: {TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED},
    TaskState.RUNNING: {
        TaskState.BLOCKED,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.PAUSED,
        TaskState.QUEUED,
        TaskState.BUDGET_EXCEEDED,
        TaskState.NEEDS_ATTENTION,
    },
    TaskState.BLOCKED: {
        TaskState.RUNNING,
        TaskState.QUEUED,
        TaskState.CANCELLED,
        TaskState.FAILED,
        TaskState.COMPLETED,
        TaskState.NEEDS_ATTENTION,
    },
    TaskState.PLANNING_READY: {
        TaskState.RUNNING,
        TaskState.QUEUED,
        TaskState.CANCELLED,
        TaskState.FAILED,
    },
    TaskState.PAUSED: {
        TaskState.RUNNING,
        TaskState.QUEUED,
        TaskState.CANCELLED,
    },
    TaskState.COMPLETED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
    TaskState.BUDGET_EXCEEDED: {TaskState.CANCELLED},
    TaskState.NEEDS_ATTENTION: {
        TaskState.RUNNING,
        TaskState.CANCELLED,
        TaskState.FAILED,
    },
}


# ── Step-attempt state machine ────────────────────────────────────────

VALID_ATTEMPT_TRANSITIONS: dict[StepAttemptState, set[StepAttemptState]] = {
    StepAttemptState.READY: {
        StepAttemptState.RUNNING,
        StepAttemptState.FAILED,
        StepAttemptState.SUPERSEDED,
    },
    StepAttemptState.WAITING: {
        StepAttemptState.READY,
        StepAttemptState.FAILED,
    },
    StepAttemptState.RUNNING: {
        StepAttemptState.SUCCEEDED,
        StepAttemptState.COMPLETED,
        StepAttemptState.FAILED,
        StepAttemptState.SKIPPED,
        StepAttemptState.SUPERSEDED,
        StepAttemptState.AWAITING_APPROVAL,
        StepAttemptState.AWAITING_PLAN_CONFIRMATION,
        StepAttemptState.OBSERVING,
        StepAttemptState.POLICY_PENDING,
        StepAttemptState.DISPATCHING,
        StepAttemptState.CONTRACTING,
        StepAttemptState.PREFLIGHTING,
        StepAttemptState.RECONCILING,
        StepAttemptState.VERIFICATION_BLOCKED,
        StepAttemptState.RECEIPT_PENDING,
        StepAttemptState.READY,
    },
    StepAttemptState.DISPATCHING: {
        StepAttemptState.RUNNING,
        StepAttemptState.FAILED,
        StepAttemptState.SUPERSEDED,
        StepAttemptState.CONTRACTING,
    },
    StepAttemptState.CONTRACTING: {
        StepAttemptState.PREFLIGHTING,
        StepAttemptState.RUNNING,
        StepAttemptState.FAILED,
        StepAttemptState.SUPERSEDED,
    },
    StepAttemptState.PREFLIGHTING: {
        StepAttemptState.RUNNING,
        StepAttemptState.AWAITING_APPROVAL,
        StepAttemptState.FAILED,
        StepAttemptState.SUPERSEDED,
    },
    StepAttemptState.OBSERVING: {
        StepAttemptState.RUNNING,
        StepAttemptState.SUCCEEDED,
        StepAttemptState.COMPLETED,
        StepAttemptState.FAILED,
        StepAttemptState.RECONCILING,
    },
    StepAttemptState.RECONCILING: {
        StepAttemptState.SUCCEEDED,
        StepAttemptState.COMPLETED,
        StepAttemptState.FAILED,
        StepAttemptState.RUNNING,
    },
    StepAttemptState.POLICY_PENDING: {
        StepAttemptState.RUNNING,
        StepAttemptState.AWAITING_APPROVAL,
        StepAttemptState.FAILED,
        StepAttemptState.SUPERSEDED,
    },
    StepAttemptState.AWAITING_APPROVAL: {
        StepAttemptState.READY,
        StepAttemptState.RUNNING,
        StepAttemptState.FAILED,
        StepAttemptState.SUPERSEDED,
    },
    StepAttemptState.AWAITING_PLAN_CONFIRMATION: {
        StepAttemptState.READY,
        StepAttemptState.RUNNING,
        StepAttemptState.FAILED,
        StepAttemptState.SUPERSEDED,
    },
    StepAttemptState.VERIFICATION_BLOCKED: {
        StepAttemptState.READY,
        StepAttemptState.RUNNING,
        StepAttemptState.FAILED,
    },
    StepAttemptState.RECEIPT_PENDING: {
        StepAttemptState.SUCCEEDED,
        StepAttemptState.FAILED,
    },
    StepAttemptState.SUCCEEDED: set(),
    StepAttemptState.COMPLETED: set(),
    StepAttemptState.SKIPPED: set(),
    StepAttemptState.FAILED: set(),
    StepAttemptState.SUPERSEDED: set(),
}


def validate_task_transition(current: str, target: str) -> bool:
    """Check whether a task state transition is valid.

    Returns True if the transition is allowed, False otherwise.
    Unrecognized states are treated as invalid (returns False).
    """
    try:
        current_state = TaskState(current)
        target_state = TaskState(target)
    except ValueError:
        return False
    return target_state in VALID_TASK_TRANSITIONS.get(current_state, set())


def validate_attempt_transition(current: str, target: str) -> bool:
    """Check whether a step attempt state transition is valid.

    Returns True if the transition is allowed, False otherwise.
    Unrecognized states are treated as invalid (returns False).
    """
    try:
        current_state = StepAttemptState(current)
        target_state = StepAttemptState(target)
    except ValueError:
        return False
    return target_state in VALID_ATTEMPT_TRANSITIONS.get(current_state, set())


def require_valid_task_transition(current: str, target: str) -> None:
    """Raise InvalidTransitionError if the task transition is not allowed."""
    if not validate_task_transition(current, target):
        raise InvalidTransitionError("task", current, target)


def require_valid_attempt_transition(current: str, target: str) -> None:
    """Raise InvalidTransitionError if the attempt transition is not allowed."""
    if not validate_attempt_transition(current, target):
        raise InvalidTransitionError("step_attempt", current, target)
