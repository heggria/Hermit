"""Comprehensive state machine completeness tests for Task, Step, and StepAttempt lifecycles.

Validates that:
- All valid transitions in the transition matrix are accepted
- All invalid transitions are rejected
- Terminal states have zero outgoing transitions
- Every non-terminal state is reachable from the initial state
- Every non-terminal state has at least one valid outgoing transition
- The transition matrix covers every enum member
"""

from __future__ import annotations

from collections import deque

import pytest

from hermit.kernel.task.state.enums import (
    ACTIVE_TASK_STATES,
    TERMINAL_ATTEMPT_STATES,
    TERMINAL_TASK_STATES,
    StepAttemptState,
    TaskState,
)
from hermit.kernel.task.state.transitions import (
    VALID_ATTEMPT_TRANSITIONS,
    VALID_TASK_TRANSITIONS,
    InvalidTransitionError,
    require_valid_attempt_transition,
    require_valid_task_transition,
    validate_attempt_transition,
    validate_task_transition,
)

# ---------------------------------------------------------------------------
# Helpers for deterministic parametrize (sets are unordered)
# ---------------------------------------------------------------------------


def _sorted_states(states):
    """Sort an iterable of StrEnum members by value for deterministic test order."""
    return sorted(states, key=lambda s: s.value)


def _sorted_task_targets(source: TaskState):
    return _sorted_states(VALID_TASK_TRANSITIONS[source])


def _sorted_attempt_targets(source: StepAttemptState):
    return _sorted_states(VALID_ATTEMPT_TRANSITIONS[source])


# =====================================================================
# Task 1: TaskState transition tests
# =====================================================================


class TestTaskStateValidTransitions:
    """Verify every declared valid transition is accepted."""

    @pytest.mark.parametrize(
        "source, target",
        [
            (TaskState.QUEUED, TaskState.RUNNING),
            (TaskState.QUEUED, TaskState.CANCELLED),
            (TaskState.QUEUED, TaskState.FAILED),
        ],
        ids=["queued->running", "queued->cancelled", "queued->failed"],
    )
    def test_queued_valid_transitions(self, source: TaskState, target: TaskState) -> None:
        assert validate_task_transition(source, target) is True
        require_valid_task_transition(source, target)  # should not raise

    @pytest.mark.parametrize(
        "target",
        _sorted_task_targets(TaskState.RUNNING),
        ids=[f"running->{t.value}" for t in _sorted_task_targets(TaskState.RUNNING)],
    )
    def test_running_valid_transitions(self, target: TaskState) -> None:
        assert validate_task_transition(TaskState.RUNNING, target) is True

    @pytest.mark.parametrize(
        "target",
        _sorted_task_targets(TaskState.BLOCKED),
        ids=[f"blocked->{t.value}" for t in _sorted_task_targets(TaskState.BLOCKED)],
    )
    def test_blocked_valid_transitions(self, target: TaskState) -> None:
        assert validate_task_transition(TaskState.BLOCKED, target) is True

    @pytest.mark.parametrize(
        "target",
        _sorted_task_targets(TaskState.PLANNING_READY),
        ids=[f"planning_ready->{t.value}" for t in _sorted_task_targets(TaskState.PLANNING_READY)],
    )
    def test_planning_ready_valid_transitions(self, target: TaskState) -> None:
        assert validate_task_transition(TaskState.PLANNING_READY, target) is True

    @pytest.mark.parametrize(
        "target",
        _sorted_task_targets(TaskState.PAUSED),
        ids=[f"paused->{t.value}" for t in _sorted_task_targets(TaskState.PAUSED)],
    )
    def test_paused_valid_transitions(self, target: TaskState) -> None:
        assert validate_task_transition(TaskState.PAUSED, target) is True

    def test_budget_exceeded_can_transition_to_cancelled(self) -> None:
        assert validate_task_transition(TaskState.BUDGET_EXCEEDED, TaskState.CANCELLED) is True

    @pytest.mark.parametrize(
        "target",
        _sorted_task_targets(TaskState.NEEDS_ATTENTION),
        ids=[
            f"needs_attention->{t.value}" for t in _sorted_task_targets(TaskState.NEEDS_ATTENTION)
        ],
    )
    def test_needs_attention_valid_transitions(self, target: TaskState) -> None:
        assert validate_task_transition(TaskState.NEEDS_ATTENTION, target) is True


class TestTaskStateInvalidTransitions:
    """Verify invalid transitions are rejected."""

    _queued_invalid = _sorted_states(
        s for s in TaskState if s not in VALID_TASK_TRANSITIONS[TaskState.QUEUED]
    )

    @pytest.mark.parametrize(
        "target",
        _queued_invalid,
        ids=[f"queued->{s.value}" for s in _queued_invalid],
    )
    def test_queued_rejects_invalid_targets(self, target: TaskState) -> None:
        assert validate_task_transition(TaskState.QUEUED, target) is False

    @pytest.mark.parametrize(
        "source",
        [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED, TaskState.RECALLED],
        ids=["completed", "failed", "cancelled", "recalled"],
    )
    @pytest.mark.parametrize(
        "target",
        _sorted_states(TaskState),
        ids=[t.value for t in _sorted_states(TaskState)],
    )
    def test_terminal_states_reject_all_outgoing(
        self, source: TaskState, target: TaskState
    ) -> None:
        assert validate_task_transition(source, target) is False

    @pytest.mark.parametrize(
        "source",
        [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED],
        ids=["completed", "failed", "cancelled"],
    )
    def test_terminal_states_raise_on_require(self, source: TaskState) -> None:
        with pytest.raises(InvalidTransitionError) as exc_info:
            require_valid_task_transition(source, TaskState.RUNNING)
        assert exc_info.value.entity_type == "task"
        assert exc_info.value.current == source.value
        assert exc_info.value.target == TaskState.RUNNING.value


class TestTaskStateTerminalStates:
    """Verify terminal state definitions are consistent."""

    def test_terminal_states_have_empty_outgoing_sets(self) -> None:
        for state in TERMINAL_TASK_STATES:
            assert VALID_TASK_TRANSITIONS[state] == set(), (
                f"Terminal state {state.value} has non-empty outgoing transitions"
            )

    def test_terminal_states_match_enum_constant(self) -> None:
        assert (
            frozenset(
                {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED, TaskState.RECALLED}
            )
            == TERMINAL_TASK_STATES
        )

    def test_active_states_are_non_terminal(self) -> None:
        for state in ACTIVE_TASK_STATES:
            assert state not in TERMINAL_TASK_STATES, (
                f"State {state.value} is both active and terminal"
            )

    def test_active_states_have_outgoing_transitions(self) -> None:
        for state in ACTIVE_TASK_STATES:
            assert len(VALID_TASK_TRANSITIONS[state]) > 0, (
                f"Active state {state.value} has no outgoing transitions"
            )


class TestTaskStateEdgeCases:
    """Edge cases for the task transition validator."""

    def test_unrecognized_source_state_returns_false(self) -> None:
        assert validate_task_transition("nonexistent", "running") is False

    def test_unrecognized_target_state_returns_false(self) -> None:
        assert validate_task_transition("running", "nonexistent") is False

    def test_require_unrecognized_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            require_valid_task_transition("bad_state", "running")

    def test_self_transition_only_if_declared(self) -> None:
        for state in TaskState:
            expected = state in VALID_TASK_TRANSITIONS.get(state, set())
            assert validate_task_transition(state, state) is expected

    def test_running_to_queued_is_valid_requeue(self) -> None:
        """Running tasks can be re-queued (e.g., after descheduling)."""
        assert validate_task_transition(TaskState.RUNNING, TaskState.QUEUED) is True

    def test_blocked_to_completed_is_valid(self) -> None:
        """Blocked tasks can be marked completed (e.g., external resolution)."""
        assert validate_task_transition(TaskState.BLOCKED, TaskState.COMPLETED) is True


# =====================================================================
# Task 2: StepStatus transition tests (mapped to StepAttemptState since
#          the kernel uses StepAttemptState for both step and attempt
#          statuses; steps share the same state values)
# =====================================================================


class TestStepStatusTransitions:
    """Test step-level status transitions using the attempt state machine.

    In Hermit, StepRecord.status uses the same string values as
    StepAttemptState. The step status is derived from its current
    attempt's status.
    """

    def test_deliberation_pending_is_used_as_intermediate_status(self) -> None:
        """The dispatch layer sets 'deliberation_pending' on steps as an
        intermediate status (via raw string). Verify this value is NOT in the
        formal enum -- it is a runtime extension outside the transition matrix."""
        assert "deliberation_pending" not in [s.value for s in StepAttemptState]

    def test_awaiting_approval_resume_path(self) -> None:
        """awaiting_approval can resume to ready or running."""
        assert validate_attempt_transition(
            StepAttemptState.AWAITING_APPROVAL, StepAttemptState.READY
        )
        assert validate_attempt_transition(
            StepAttemptState.AWAITING_APPROVAL, StepAttemptState.RUNNING
        )

    def test_awaiting_approval_can_fail(self) -> None:
        assert validate_attempt_transition(
            StepAttemptState.AWAITING_APPROVAL, StepAttemptState.FAILED
        )

    def test_awaiting_approval_can_be_superseded(self) -> None:
        assert validate_attempt_transition(
            StepAttemptState.AWAITING_APPROVAL, StepAttemptState.SUPERSEDED
        )

    def test_awaiting_plan_confirmation_resume_path(self) -> None:
        """awaiting_plan_confirmation can resume to ready or running."""
        assert validate_attempt_transition(
            StepAttemptState.AWAITING_PLAN_CONFIRMATION, StepAttemptState.READY
        )
        assert validate_attempt_transition(
            StepAttemptState.AWAITING_PLAN_CONFIRMATION, StepAttemptState.RUNNING
        )

    def test_policy_pending_to_awaiting_approval(self) -> None:
        """Policy evaluation can escalate to human approval."""
        assert validate_attempt_transition(
            StepAttemptState.POLICY_PENDING, StepAttemptState.AWAITING_APPROVAL
        )

    def test_policy_pending_to_running(self) -> None:
        """Policy auto-approval resumes execution."""
        assert validate_attempt_transition(
            StepAttemptState.POLICY_PENDING, StepAttemptState.RUNNING
        )

    def test_observing_can_proceed_to_reconciling(self) -> None:
        """Observation phase feeds into reconciliation."""
        assert validate_attempt_transition(StepAttemptState.OBSERVING, StepAttemptState.RECONCILING)

    def test_observing_can_succeed_directly(self) -> None:
        """Observation can complete with success."""
        assert validate_attempt_transition(StepAttemptState.OBSERVING, StepAttemptState.SUCCEEDED)

    def test_verification_blocked_resume_paths(self) -> None:
        """Verification blocks can resume to ready or running, or fail."""
        for target in (StepAttemptState.READY, StepAttemptState.RUNNING, StepAttemptState.FAILED):
            assert validate_attempt_transition(StepAttemptState.VERIFICATION_BLOCKED, target)


# =====================================================================
# Task 3: StepAttemptState transition tests
# =====================================================================


class TestStepAttemptFullLifecycle:
    """Test the full attempt lifecycle: ready -> running -> succeeded/failed."""

    def test_happy_path_ready_running_succeeded(self) -> None:
        assert validate_attempt_transition(StepAttemptState.READY, StepAttemptState.RUNNING)
        assert validate_attempt_transition(StepAttemptState.RUNNING, StepAttemptState.SUCCEEDED)

    def test_happy_path_ready_running_completed(self) -> None:
        assert validate_attempt_transition(StepAttemptState.READY, StepAttemptState.RUNNING)
        assert validate_attempt_transition(StepAttemptState.RUNNING, StepAttemptState.COMPLETED)

    def test_failure_path_ready_running_failed(self) -> None:
        assert validate_attempt_transition(StepAttemptState.READY, StepAttemptState.RUNNING)
        assert validate_attempt_transition(StepAttemptState.RUNNING, StepAttemptState.FAILED)

    def test_ready_can_fail_directly(self) -> None:
        """An attempt can fail before it starts running (e.g., preflight failure)."""
        assert validate_attempt_transition(StepAttemptState.READY, StepAttemptState.FAILED)

    def test_ready_can_be_superseded(self) -> None:
        """An attempt can be superseded before running."""
        assert validate_attempt_transition(StepAttemptState.READY, StepAttemptState.SUPERSEDED)

    def test_running_can_be_skipped(self) -> None:
        assert validate_attempt_transition(StepAttemptState.RUNNING, StepAttemptState.SKIPPED)


class TestStepAttemptSupersededStatus:
    """Test superseded status transitions."""

    def test_superseded_is_terminal(self) -> None:
        assert StepAttemptState.SUPERSEDED in TERMINAL_ATTEMPT_STATES

    def test_superseded_has_no_outgoing_transitions(self) -> None:
        assert VALID_ATTEMPT_TRANSITIONS[StepAttemptState.SUPERSEDED] == set()

    @pytest.mark.parametrize(
        "source",
        [
            StepAttemptState.READY,
            StepAttemptState.RUNNING,
            StepAttemptState.DISPATCHING,
            StepAttemptState.CONTRACTING,
            StepAttemptState.PREFLIGHTING,
            StepAttemptState.POLICY_PENDING,
            StepAttemptState.AWAITING_APPROVAL,
            StepAttemptState.AWAITING_PLAN_CONFIRMATION,
        ],
        ids=[
            "ready",
            "running",
            "dispatching",
            "contracting",
            "preflighting",
            "policy_pending",
            "awaiting_approval",
            "awaiting_plan_confirmation",
        ],
    )
    def test_superseded_reachable_from_non_terminal_states(self, source: StepAttemptState) -> None:
        """Superseded should be reachable from most non-terminal states."""
        assert validate_attempt_transition(source, StepAttemptState.SUPERSEDED)


class TestStepAttemptReconcilingTransitions:
    """Test the reconciling status transitions."""

    def test_reconciling_to_succeeded(self) -> None:
        assert validate_attempt_transition(StepAttemptState.RECONCILING, StepAttemptState.SUCCEEDED)

    def test_reconciling_to_completed(self) -> None:
        assert validate_attempt_transition(StepAttemptState.RECONCILING, StepAttemptState.COMPLETED)

    def test_reconciling_to_failed(self) -> None:
        assert validate_attempt_transition(StepAttemptState.RECONCILING, StepAttemptState.FAILED)

    def test_reconciling_to_running(self) -> None:
        """Reconciliation can return control to running for another cycle."""
        assert validate_attempt_transition(StepAttemptState.RECONCILING, StepAttemptState.RUNNING)

    def test_running_to_reconciling(self) -> None:
        """Running can enter reconciliation."""
        assert validate_attempt_transition(StepAttemptState.RUNNING, StepAttemptState.RECONCILING)


class TestStepAttemptReceiptPending:
    """Test receipt_pending transitions."""

    def test_receipt_pending_to_succeeded(self) -> None:
        assert validate_attempt_transition(
            StepAttemptState.RECEIPT_PENDING, StepAttemptState.SUCCEEDED
        )

    def test_receipt_pending_to_failed(self) -> None:
        assert validate_attempt_transition(
            StepAttemptState.RECEIPT_PENDING, StepAttemptState.FAILED
        )

    def test_receipt_pending_has_no_other_outgoing(self) -> None:
        valid_targets = VALID_ATTEMPT_TRANSITIONS[StepAttemptState.RECEIPT_PENDING]
        assert valid_targets == {StepAttemptState.SUCCEEDED, StepAttemptState.FAILED}


class TestStepAttemptValidTransitionsExhaustive:
    """Verify every declared valid attempt transition is accepted, and
    every undeclared transition is rejected."""

    _valid_pairs = sorted(
        [
            (source, target)
            for source, targets in VALID_ATTEMPT_TRANSITIONS.items()
            for target in targets
        ],
        key=lambda pair: (pair[0].value, pair[1].value),
    )

    _invalid_pairs = sorted(
        [
            (source, target)
            for source, targets in VALID_ATTEMPT_TRANSITIONS.items()
            for target in StepAttemptState
            if target not in targets
        ],
        key=lambda pair: (pair[0].value, pair[1].value),
    )

    @pytest.mark.parametrize(
        "source_target",
        _valid_pairs,
        ids=[f"{s.value}->{t.value}" for s, t in _valid_pairs],
    )
    def test_all_valid_transitions_accepted(
        self, source_target: tuple[StepAttemptState, StepAttemptState]
    ) -> None:
        source, target = source_target
        assert validate_attempt_transition(source, target) is True

    @pytest.mark.parametrize(
        "source_target",
        _invalid_pairs,
        ids=[f"{s.value}-X->{t.value}" for s, t in _invalid_pairs],
    )
    def test_all_invalid_transitions_rejected(
        self, source_target: tuple[StepAttemptState, StepAttemptState]
    ) -> None:
        source, target = source_target
        assert validate_attempt_transition(source, target) is False


class TestStepAttemptTerminalStates:
    """Verify terminal attempt state definitions."""

    def test_terminal_attempt_states_have_empty_outgoing(self) -> None:
        for state in TERMINAL_ATTEMPT_STATES:
            assert VALID_ATTEMPT_TRANSITIONS[state] == set(), (
                f"Terminal attempt state {state.value} has non-empty outgoing transitions"
            )

    def test_terminal_attempt_states_match_constant(self) -> None:
        assert (
            frozenset(
                {
                    StepAttemptState.SUCCEEDED,
                    StepAttemptState.COMPLETED,
                    StepAttemptState.SKIPPED,
                    StepAttemptState.FAILED,
                    StepAttemptState.SUPERSEDED,
                }
            )
            == TERMINAL_ATTEMPT_STATES
        )

    _sorted_terminal = _sorted_states(TERMINAL_ATTEMPT_STATES)

    @pytest.mark.parametrize(
        "state",
        _sorted_terminal,
        ids=[s.value for s in _sorted_terminal],
    )
    def test_terminal_attempt_require_raises(self, state: StepAttemptState) -> None:
        with pytest.raises(InvalidTransitionError):
            require_valid_attempt_transition(state, StepAttemptState.RUNNING)


class TestStepAttemptGovernedPipeline:
    """Test the governed execution pipeline path:
    ready -> dispatching -> contracting -> preflighting -> running -> ...
    """

    def test_governed_pipeline_happy_path(self) -> None:
        """Full governed path: ready -> running -> dispatching -> contracting ->
        preflighting -> running -> receipt_pending -> succeeded."""
        pipeline = [
            (StepAttemptState.READY, StepAttemptState.RUNNING),
            (StepAttemptState.RUNNING, StepAttemptState.DISPATCHING),
            (StepAttemptState.DISPATCHING, StepAttemptState.CONTRACTING),
            (StepAttemptState.CONTRACTING, StepAttemptState.PREFLIGHTING),
            (StepAttemptState.PREFLIGHTING, StepAttemptState.RUNNING),
            (StepAttemptState.RUNNING, StepAttemptState.RECEIPT_PENDING),
            (StepAttemptState.RECEIPT_PENDING, StepAttemptState.SUCCEEDED),
        ]
        for source, target in pipeline:
            assert validate_attempt_transition(source, target) is True, (
                f"Pipeline transition {source.value} -> {target.value} should be valid"
            )

    def test_governed_pipeline_with_approval(self) -> None:
        """Path through preflighting -> awaiting_approval -> running."""
        pipeline = [
            (StepAttemptState.PREFLIGHTING, StepAttemptState.AWAITING_APPROVAL),
            (StepAttemptState.AWAITING_APPROVAL, StepAttemptState.RUNNING),
        ]
        for source, target in pipeline:
            assert validate_attempt_transition(source, target) is True

    def test_governed_pipeline_with_policy(self) -> None:
        """Path through running -> policy_pending -> awaiting_approval -> ready."""
        pipeline = [
            (StepAttemptState.RUNNING, StepAttemptState.POLICY_PENDING),
            (StepAttemptState.POLICY_PENDING, StepAttemptState.AWAITING_APPROVAL),
            (StepAttemptState.AWAITING_APPROVAL, StepAttemptState.READY),
        ]
        for source, target in pipeline:
            assert validate_attempt_transition(source, target) is True

    def test_observation_reconciliation_path(self) -> None:
        """Path: running -> observing -> reconciling -> succeeded."""
        pipeline = [
            (StepAttemptState.RUNNING, StepAttemptState.OBSERVING),
            (StepAttemptState.OBSERVING, StepAttemptState.RECONCILING),
            (StepAttemptState.RECONCILING, StepAttemptState.SUCCEEDED),
        ]
        for source, target in pipeline:
            assert validate_attempt_transition(source, target) is True


class TestStepAttemptEdgeCases:
    """Edge cases for the attempt transition validator."""

    def test_unrecognized_source_returns_false(self) -> None:
        assert validate_attempt_transition("bogus", "running") is False

    def test_unrecognized_target_returns_false(self) -> None:
        assert validate_attempt_transition("running", "bogus") is False

    def test_require_unrecognized_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            require_valid_attempt_transition("bad", "running")

    def test_waiting_to_ready_is_valid(self) -> None:
        """Waiting attempts can become ready again."""
        assert validate_attempt_transition(StepAttemptState.WAITING, StepAttemptState.READY)

    def test_waiting_to_failed_is_valid(self) -> None:
        """Waiting attempts can fail (e.g., dependency failure)."""
        assert validate_attempt_transition(StepAttemptState.WAITING, StepAttemptState.FAILED)

    def test_running_to_ready_is_valid(self) -> None:
        """Running can be reset to ready (e.g., requeue after soft failure)."""
        assert validate_attempt_transition(StepAttemptState.RUNNING, StepAttemptState.READY)


# =====================================================================
# Task 4: Meta-tests for structural completeness
# =====================================================================


class TestTaskTransitionMatrixCompleteness:
    """Programmatic verification of the task transition matrix structure."""

    def test_every_task_state_has_a_transition_entry(self) -> None:
        """Every member of TaskState must appear as a key in VALID_TASK_TRANSITIONS."""
        for state in TaskState:
            assert state in VALID_TASK_TRANSITIONS, (
                f"TaskState.{state.value} is missing from VALID_TASK_TRANSITIONS"
            )

    def test_all_transition_targets_are_valid_task_states(self) -> None:
        """Every target in the transition sets must be a valid TaskState member."""
        for source, targets in VALID_TASK_TRANSITIONS.items():
            for target in targets:
                assert isinstance(target, TaskState), (
                    f"Target {target!r} from {source.value} is not a TaskState"
                )

    def test_non_terminal_task_states_have_outgoing_transitions(self) -> None:
        """Every non-terminal state must have at least one valid outgoing transition."""
        for state in TaskState:
            if state in TERMINAL_TASK_STATES:
                continue
            assert len(VALID_TASK_TRANSITIONS[state]) > 0, (
                f"Non-terminal TaskState.{state.value} has zero outgoing transitions"
            )

    def test_terminal_task_states_have_zero_outgoing(self) -> None:
        """Terminal states must have exactly zero outgoing transitions."""
        for state in TERMINAL_TASK_STATES:
            assert len(VALID_TASK_TRANSITIONS[state]) == 0, (
                f"Terminal TaskState.{state.value} has "
                f"{len(VALID_TASK_TRANSITIONS[state])} outgoing transitions"
            )

    def test_initial_state_is_queued(self) -> None:
        """QUEUED is the initial task state and must exist."""
        assert TaskState.QUEUED in VALID_TASK_TRANSITIONS

    def test_all_non_terminal_states_reachable_from_initial_states(self) -> None:
        """Every TaskState must be reachable from at least one initial state.

        QUEUED is the primary initial state. PLANNING_READY is an alternative
        initial state set directly when a task is created with a plan (not
        reached via the transition validator from QUEUED).
        """
        # States that can be set as initial task status at creation time.
        initial_states = {TaskState.QUEUED, TaskState.PLANNING_READY}
        # Combine reachability from all initial states.
        reachable: set = set()
        for init in initial_states:
            reachable |= _bfs_reachable(init, VALID_TASK_TRANSITIONS)
            reachable.add(init)
        for state in TaskState:
            assert state in reachable, (
                f"TaskState.{state.value} is unreachable from any initial state"
            )

    def test_planning_ready_is_alternative_initial_state(self) -> None:
        """PLANNING_READY is not reachable from QUEUED via transitions -- it is
        set directly as an initial state. Verify it has valid outgoing
        transitions leading to terminal states."""
        reachable_from_queued = _bfs_reachable(TaskState.QUEUED, VALID_TASK_TRANSITIONS)
        assert TaskState.PLANNING_READY not in reachable_from_queued, (
            "PLANNING_READY should not be reachable from QUEUED via transitions"
        )
        reachable_from_pr = _bfs_reachable(TaskState.PLANNING_READY, VALID_TASK_TRANSITIONS)
        terminal_reachable = reachable_from_pr & TERMINAL_TASK_STATES
        assert len(terminal_reachable) > 0, "PLANNING_READY cannot reach any terminal state"

    def test_no_orphan_task_states(self) -> None:
        """No TaskState should exist that has no entry in VALID_TASK_TRANSITIONS."""
        enum_members = set(TaskState)
        matrix_keys = set(VALID_TASK_TRANSITIONS.keys())
        orphans = enum_members - matrix_keys
        assert orphans == set(), f"Orphan TaskState members: {orphans}"


class TestAttemptTransitionMatrixCompleteness:
    """Programmatic verification of the attempt transition matrix structure."""

    def test_every_attempt_state_has_a_transition_entry(self) -> None:
        """Every member of StepAttemptState must appear as a key."""
        for state in StepAttemptState:
            assert state in VALID_ATTEMPT_TRANSITIONS, (
                f"StepAttemptState.{state.value} is missing from VALID_ATTEMPT_TRANSITIONS"
            )

    def test_all_transition_targets_are_valid_attempt_states(self) -> None:
        """Every target in the transition sets must be a valid StepAttemptState."""
        for source, targets in VALID_ATTEMPT_TRANSITIONS.items():
            for target in targets:
                assert isinstance(target, StepAttemptState), (
                    f"Target {target!r} from {source.value} is not a StepAttemptState"
                )

    def test_non_terminal_attempt_states_have_outgoing_transitions(self) -> None:
        """Every non-terminal state must have at least one outgoing transition."""
        for state in StepAttemptState:
            if state in TERMINAL_ATTEMPT_STATES:
                continue
            assert len(VALID_ATTEMPT_TRANSITIONS[state]) > 0, (
                f"Non-terminal StepAttemptState.{state.value} has zero outgoing transitions"
            )

    def test_terminal_attempt_states_have_zero_outgoing(self) -> None:
        """Terminal states must have exactly zero outgoing transitions."""
        for state in TERMINAL_ATTEMPT_STATES:
            assert len(VALID_ATTEMPT_TRANSITIONS[state]) == 0, (
                f"Terminal StepAttemptState.{state.value} has "
                f"{len(VALID_ATTEMPT_TRANSITIONS[state])} outgoing transitions"
            )

    def test_initial_state_is_ready(self) -> None:
        """READY is the initial attempt state."""
        assert StepAttemptState.READY in VALID_ATTEMPT_TRANSITIONS

    def test_all_states_reachable_from_initial_states(self) -> None:
        """Every StepAttemptState must be reachable from at least one initial state.

        READY is the primary initial state. WAITING is an alternative initial
        state for attempts that start with unmet dependencies (e.g., DAG steps
        waiting on upstream completion).
        """
        initial_states = {StepAttemptState.READY, StepAttemptState.WAITING}
        reachable: set = set()
        for init in initial_states:
            reachable |= _bfs_reachable(init, VALID_ATTEMPT_TRANSITIONS)
            reachable.add(init)
        for state in StepAttemptState:
            assert state in reachable, (
                f"StepAttemptState.{state.value} is unreachable from any initial state"
            )

    def test_waiting_is_alternative_initial_state(self) -> None:
        """WAITING is not reachable from READY via transitions -- it is set
        directly as an initial state for dependency-blocked attempts."""
        reachable_from_ready = _bfs_reachable(StepAttemptState.READY, VALID_ATTEMPT_TRANSITIONS)
        assert StepAttemptState.WAITING not in reachable_from_ready, (
            "WAITING should not be reachable from READY via transitions"
        )
        reachable_from_waiting = _bfs_reachable(StepAttemptState.WAITING, VALID_ATTEMPT_TRANSITIONS)
        terminal_reachable = reachable_from_waiting & TERMINAL_ATTEMPT_STATES
        assert len(terminal_reachable) > 0, "WAITING cannot reach any terminal state"

    def test_no_orphan_attempt_states(self) -> None:
        """No StepAttemptState should exist without a transition matrix entry."""
        enum_members = set(StepAttemptState)
        matrix_keys = set(VALID_ATTEMPT_TRANSITIONS.keys())
        orphans = enum_members - matrix_keys
        assert orphans == set(), f"Orphan StepAttemptState members: {orphans}"

    def test_transition_matrix_is_not_reflexive_for_terminals(self) -> None:
        """Terminal states must not allow self-transitions."""
        for state in TERMINAL_ATTEMPT_STATES:
            assert state not in VALID_ATTEMPT_TRANSITIONS[state], (
                f"Terminal {state.value} allows self-transition"
            )


class TestCrossValidation:
    """Cross-validate enums, constants, and transition tables."""

    def test_task_transition_count(self) -> None:
        """Sanity: the transition table should have exactly as many keys as TaskState members."""
        assert len(VALID_TASK_TRANSITIONS) == len(TaskState)

    def test_attempt_transition_count(self) -> None:
        """Sanity: the transition table should have exactly as many keys as StepAttemptState."""
        assert len(VALID_ATTEMPT_TRANSITIONS) == len(StepAttemptState)

    def test_task_terminal_plus_active_plus_others_cover_all_states(self) -> None:
        """Terminal + Active + other states should cover the full TaskState enum."""
        classified = TERMINAL_TASK_STATES | ACTIVE_TASK_STATES
        uncovered = set(TaskState) - classified
        # These are the "other" states -- paused, budget_exceeded, needs_attention, reconciling.
        # They exist but are not in ACTIVE or TERMINAL.
        expected_others = {
            TaskState.PAUSED,
            TaskState.BUDGET_EXCEEDED,
            TaskState.NEEDS_ATTENTION,
            TaskState.RECONCILING,
        }
        assert uncovered == expected_others, (
            f"Unclassified TaskState members beyond expected: {uncovered - expected_others}"
        )

    def test_every_non_terminal_task_state_can_reach_a_terminal(self) -> None:
        """From every non-terminal TaskState, at least one terminal state must be reachable."""
        for state in TaskState:
            if state in TERMINAL_TASK_STATES:
                continue
            reachable = _bfs_reachable(state, VALID_TASK_TRANSITIONS)
            terminal_reachable = reachable & TERMINAL_TASK_STATES
            assert len(terminal_reachable) > 0, (
                f"TaskState.{state.value} cannot reach any terminal state"
            )

    def test_every_non_terminal_attempt_can_reach_a_terminal(self) -> None:
        """From every non-terminal attempt state, at least one terminal must be reachable."""
        for state in StepAttemptState:
            if state in TERMINAL_ATTEMPT_STATES:
                continue
            reachable = _bfs_reachable(state, VALID_ATTEMPT_TRANSITIONS)
            terminal_reachable = reachable & TERMINAL_ATTEMPT_STATES
            assert len(terminal_reachable) > 0, (
                f"StepAttemptState.{state.value} cannot reach any terminal state"
            )


# =====================================================================
# BFS helper
# =====================================================================


def _bfs_reachable(start, graph: dict) -> set:
    """Return the set of all states reachable from *start* in the transition graph."""
    visited: set = set()
    queue: deque = deque()
    queue.append(start)
    while queue:
        current = queue.popleft()
        for neighbor in graph.get(current, set()):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return visited
