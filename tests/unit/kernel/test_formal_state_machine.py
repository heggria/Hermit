"""Tests for the formal state machine: enums, transition tables, and validators."""

from __future__ import annotations

import pytest

from hermit.kernel.task.state.enums import (
    ACTIVE_TASK_STATES,
    TERMINAL_ATTEMPT_STATES,
    TERMINAL_TASK_STATES,
    StepAttemptState,
    TaskState,
    WaitingKind,
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

# ── Enum completeness ─────────────────────────────────────────────────


class TestTaskState:
    def test_all_values(self) -> None:
        expected = {
            "queued",
            "running",
            "blocked",
            "planning_ready",
            "paused",
            "completed",
            "failed",
            "cancelled",
            "budget_exceeded",
            "needs_attention",
            "reconciling",
        }
        assert {s.value for s in TaskState} == expected

    def test_str_compatibility(self) -> None:
        """TaskState values must be usable as plain strings."""
        assert TaskState.RUNNING == "running"
        assert str(TaskState.RUNNING) == "running"
        assert f"{TaskState.RUNNING}" == "running"

    def test_construct_from_string(self) -> None:
        assert TaskState("queued") is TaskState.QUEUED
        assert TaskState("completed") is TaskState.COMPLETED

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            TaskState("nonexistent")

    def test_terminal_states(self) -> None:
        assert (
            frozenset({TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED})
            == TERMINAL_TASK_STATES
        )

    def test_active_states(self) -> None:
        assert (
            frozenset(
                {TaskState.QUEUED, TaskState.RUNNING, TaskState.BLOCKED, TaskState.PLANNING_READY}
            )
            == ACTIVE_TASK_STATES
        )

    def test_terminal_and_active_disjoint(self) -> None:
        assert frozenset() == TERMINAL_TASK_STATES & ACTIVE_TASK_STATES

    def test_paused_is_neither_terminal_nor_active(self) -> None:
        assert TaskState.PAUSED not in TERMINAL_TASK_STATES
        assert TaskState.PAUSED not in ACTIVE_TASK_STATES


class TestStepAttemptState:
    def test_all_values(self) -> None:
        expected = {
            "ready",
            "waiting",
            "running",
            "dispatching",
            "contracting",
            "preflighting",
            "observing",
            "reconciling",
            "verification_blocked",
            "receipt_pending",
            "policy_pending",
            "awaiting_approval",
            "awaiting_plan_confirmation",
            "succeeded",
            "completed",
            "skipped",
            "failed",
            "superseded",
        }
        assert {s.value for s in StepAttemptState} == expected

    def test_str_compatibility(self) -> None:
        assert StepAttemptState.RUNNING == "running"
        assert str(StepAttemptState.AWAITING_APPROVAL) == "awaiting_approval"

    def test_construct_from_string(self) -> None:
        assert StepAttemptState("ready") is StepAttemptState.READY
        assert StepAttemptState("superseded") is StepAttemptState.SUPERSEDED

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            StepAttemptState("bogus")

    def test_terminal_attempt_states(self) -> None:
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


class TestWaitingKind:
    def test_all_values(self) -> None:
        expected = {
            "awaiting_approval",
            "awaiting_plan_confirmation",
            "dependency_failed",
            "input_changed_reenter_policy",
            "reentry_resumed",
            "observing",
        }
        assert {w.value for w in WaitingKind} == expected

    def test_str_compatibility(self) -> None:
        assert WaitingKind.AWAITING_APPROVAL == "awaiting_approval"


# ── Transition table coverage ─────────────────────────────────────────


class TestTransitionTableCompleteness:
    def test_all_task_states_in_table(self) -> None:
        """Every TaskState must have an entry in the transition table."""
        for state in TaskState:
            assert state in VALID_TASK_TRANSITIONS, f"{state} missing from VALID_TASK_TRANSITIONS"

    def test_all_attempt_states_in_table(self) -> None:
        """Every StepAttemptState must have an entry in the transition table."""
        for state in StepAttemptState:
            assert state in VALID_ATTEMPT_TRANSITIONS, (
                f"{state} missing from VALID_ATTEMPT_TRANSITIONS"
            )

    def test_terminal_task_states_have_no_transitions(self) -> None:
        for state in TERMINAL_TASK_STATES:
            assert VALID_TASK_TRANSITIONS[state] == set(), (
                f"Terminal state {state} should have no outgoing transitions"
            )

    def test_terminal_attempt_states_have_no_transitions(self) -> None:
        for state in TERMINAL_ATTEMPT_STATES:
            assert VALID_ATTEMPT_TRANSITIONS[state] == set(), (
                f"Terminal state {state} should have no outgoing transitions"
            )

    def test_transition_targets_are_valid_states(self) -> None:
        """All target states in the tables must be valid enum members."""
        for targets in VALID_TASK_TRANSITIONS.values():
            for target in targets:
                assert isinstance(target, TaskState)
        for targets in VALID_ATTEMPT_TRANSITIONS.values():
            for target in targets:
                assert isinstance(target, StepAttemptState)


# ── validate_task_transition ──────────────────────────────────────────


class TestValidateTaskTransition:
    @pytest.mark.parametrize(
        "current, target",
        [
            ("queued", "running"),
            ("queued", "cancelled"),
            ("queued", "failed"),
            ("running", "blocked"),
            ("running", "completed"),
            ("running", "failed"),
            ("running", "cancelled"),
            ("running", "paused"),
            ("running", "queued"),
            ("blocked", "running"),
            ("blocked", "queued"),
            ("blocked", "cancelled"),
            ("blocked", "failed"),
            ("blocked", "completed"),
            ("planning_ready", "running"),
            ("planning_ready", "queued"),
            ("planning_ready", "cancelled"),
            ("planning_ready", "failed"),
            ("paused", "running"),
            ("paused", "queued"),
            ("paused", "cancelled"),
        ],
    )
    def test_valid_transitions(self, current: str, target: str) -> None:
        assert validate_task_transition(current, target) is True

    @pytest.mark.parametrize(
        "current, target",
        [
            ("completed", "running"),
            ("completed", "queued"),
            ("failed", "running"),
            ("failed", "completed"),
            ("cancelled", "running"),
            ("cancelled", "queued"),
            ("queued", "completed"),
            ("queued", "blocked"),
            ("paused", "completed"),
            ("paused", "failed"),
        ],
    )
    def test_invalid_transitions(self, current: str, target: str) -> None:
        assert validate_task_transition(current, target) is False

    def test_unrecognized_current_state(self) -> None:
        assert validate_task_transition("bogus", "running") is False

    def test_unrecognized_target_state(self) -> None:
        assert validate_task_transition("running", "bogus") is False

    def test_self_transition_is_invalid(self) -> None:
        """A state transitioning to itself is not in the valid set, except reconciling."""
        for state in TaskState:
            if state == TaskState.RECONCILING:
                # reconciling -> reconciling is valid (partial reconciliation)
                assert validate_task_transition(state.value, state.value) is True
            else:
                assert validate_task_transition(state.value, state.value) is False


# ── validate_attempt_transition ───────────────────────────────────────


class TestValidateAttemptTransition:
    @pytest.mark.parametrize(
        "current, target",
        [
            ("ready", "running"),
            ("ready", "failed"),
            ("ready", "superseded"),
            ("waiting", "ready"),
            ("waiting", "failed"),
            ("running", "succeeded"),
            ("running", "completed"),
            ("running", "failed"),
            ("running", "skipped"),
            ("running", "superseded"),
            ("running", "awaiting_approval"),
            ("running", "awaiting_plan_confirmation"),
            ("running", "observing"),
            ("running", "dispatching"),
            ("running", "reconciling"),
            ("running", "ready"),
            ("awaiting_approval", "ready"),
            ("awaiting_approval", "running"),
            ("awaiting_approval", "failed"),
            ("awaiting_approval", "superseded"),
            ("awaiting_plan_confirmation", "ready"),
            ("awaiting_plan_confirmation", "running"),
            ("observing", "running"),
            ("observing", "succeeded"),
            ("observing", "reconciling"),
            ("dispatching", "running"),
            ("dispatching", "failed"),
        ],
    )
    def test_valid_transitions(self, current: str, target: str) -> None:
        assert validate_attempt_transition(current, target) is True

    @pytest.mark.parametrize(
        "current, target",
        [
            ("succeeded", "running"),
            ("completed", "running"),
            ("skipped", "running"),
            ("failed", "running"),
            ("superseded", "running"),
            ("ready", "completed"),
            ("ready", "succeeded"),
            ("waiting", "succeeded"),
            ("waiting", "completed"),
        ],
    )
    def test_invalid_transitions(self, current: str, target: str) -> None:
        assert validate_attempt_transition(current, target) is False

    def test_unrecognized_states(self) -> None:
        assert validate_attempt_transition("bogus", "running") is False
        assert validate_attempt_transition("running", "bogus") is False


# ── require_valid_*_transition ────────────────────────────────────────


class TestRequireValidTransition:
    def test_require_task_valid(self) -> None:
        require_valid_task_transition("queued", "running")  # should not raise

    def test_require_task_invalid_raises(self) -> None:
        with pytest.raises(InvalidTransitionError) as exc_info:
            require_valid_task_transition("completed", "running")
        assert exc_info.value.entity_type == "task"
        assert exc_info.value.current == "completed"
        assert exc_info.value.target == "running"
        assert "completed" in str(exc_info.value)
        assert "running" in str(exc_info.value)

    def test_require_attempt_valid(self) -> None:
        require_valid_attempt_transition("ready", "running")  # should not raise

    def test_require_attempt_invalid_raises(self) -> None:
        with pytest.raises(InvalidTransitionError) as exc_info:
            require_valid_attempt_transition("succeeded", "running")
        assert exc_info.value.entity_type == "step_attempt"

    def test_require_task_unrecognized_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            require_valid_task_transition("bogus", "running")

    def test_require_attempt_unrecognized_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            require_valid_attempt_transition("running", "bogus")


# ── InvalidTransitionError ────────────────────────────────────────────


class TestInvalidTransitionError:
    def test_is_value_error(self) -> None:
        err = InvalidTransitionError("task", "a", "b")
        assert isinstance(err, ValueError)

    def test_attributes(self) -> None:
        err = InvalidTransitionError("step_attempt", "running", "succeeded")
        assert err.entity_type == "step_attempt"
        assert err.current == "running"
        assert err.target == "succeeded"

    def test_message(self) -> None:
        err = InvalidTransitionError("task", "queued", "completed")
        assert "Invalid task transition" in str(err)
        assert "queued" in str(err)
        assert "completed" in str(err)


# ── Backward compatibility ────────────────────────────────────────────


class TestBackwardCompatibility:
    """Ensure enum string values match existing string literals in the codebase."""

    def test_task_states_match_store_constants(self) -> None:
        """Match the _ACTIVE_TASK_STATUSES and _TERMINAL_TASK_STATUSES in store_tasks.py."""
        active_from_store = {"queued", "running", "blocked", "planning_ready"}
        terminal_from_store = {"completed", "failed", "cancelled"}
        assert {s.value for s in ACTIVE_TASK_STATES} == active_from_store
        assert {s.value for s in TERMINAL_TASK_STATES} == terminal_from_store

    def test_terminal_statuses_match_outcomes(self) -> None:
        """Match TERMINAL_TASK_STATUSES from outcomes.py."""
        from hermit.kernel.task.state.outcomes import TERMINAL_TASK_STATUSES as LEGACY

        assert {s.value for s in TERMINAL_TASK_STATES} == LEGACY

    def test_enum_values_usable_in_set_membership(self) -> None:
        """StrEnum values work with plain string comparisons."""
        status = "running"
        assert status in {s.value for s in TaskState}
        assert TaskState.RUNNING in {"running", "blocked"}

    def test_enum_in_dict_key(self) -> None:
        """StrEnum values can be used as dict keys interchangeably with strings."""
        d: dict[str, int] = {"running": 1}
        assert d[TaskState.RUNNING] == 1
        assert d.get(TaskState.RUNNING) == 1

    def test_all_task_transitions_from_table(self) -> None:
        """Verify all transition entries are present and non-empty for active states."""
        for state in ACTIVE_TASK_STATES:
            assert len(VALID_TASK_TRANSITIONS[state]) > 0, (
                f"Active state {state} must have at least one transition"
            )

    def test_all_non_terminal_attempt_states_have_transitions(self) -> None:
        """Non-terminal attempt states must have at least one outgoing edge."""
        for state in StepAttemptState:
            if state not in TERMINAL_ATTEMPT_STATES:
                assert len(VALID_ATTEMPT_TRANSITIONS[state]) > 0, (
                    f"Non-terminal attempt state {state} must have at least one transition"
                )


# ── Re-export through __init__ ────────────────────────────────────────


class TestReExport:
    """Verify that the state/__init__.py re-exports work correctly."""

    def test_import_from_state_package(self) -> None:
        from hermit.kernel.task.state import (  # noqa: F401
            ACTIVE_TASK_STATES,
            TERMINAL_ATTEMPT_STATES,
            TERMINAL_TASK_STATES,
            VALID_ATTEMPT_TRANSITIONS,
            VALID_TASK_TRANSITIONS,
            InvalidTransitionError,
            StepAttemptState,
            TaskState,
            WaitingKind,
            require_valid_attempt_transition,
            require_valid_task_transition,
            validate_attempt_transition,
            validate_task_transition,
        )

        # Verify they are the same objects
        assert TaskState.RUNNING == "running"
        assert validate_task_transition("queued", "running") is True
