"""Tests for metaloop v2 pipeline models — phases, transitions, and iteration state."""

from __future__ import annotations

import pytest

from hermit.plugins.builtin.hooks.metaloop.models import (
    ALLOWED_TRANSITIONS,
    MAX_REVISION_CYCLES,
    TERMINAL_PHASES,
    IterationState,
    PipelinePhase,
)


class TestPhaseTransitions:
    def test_pending_can_go_to_planning_or_failed(self) -> None:
        allowed = ALLOWED_TRANSITIONS[PipelinePhase.PENDING]
        assert PipelinePhase.PLANNING in allowed
        assert PipelinePhase.FAILED in allowed

    def test_planning_can_go_to_implementing_rejected_failed(self) -> None:
        allowed = ALLOWED_TRANSITIONS[PipelinePhase.PLANNING]
        assert PipelinePhase.IMPLEMENTING in allowed
        assert PipelinePhase.REJECTED in allowed
        assert PipelinePhase.FAILED in allowed

    def test_implementing_can_go_to_reviewing_or_failed(self) -> None:
        allowed = ALLOWED_TRANSITIONS[PipelinePhase.IMPLEMENTING]
        assert PipelinePhase.REVIEWING in allowed
        assert PipelinePhase.FAILED in allowed

    def test_implementing_self_transition_allowed(self) -> None:
        """IMPLEMENTING -> IMPLEMENTING is valid (for dag_task_id write)."""
        allowed = ALLOWED_TRANSITIONS[PipelinePhase.IMPLEMENTING]
        assert PipelinePhase.IMPLEMENTING in allowed

    def test_reviewing_can_go_to_accepted_rejected_failed(self) -> None:
        allowed = ALLOWED_TRANSITIONS[PipelinePhase.REVIEWING]
        assert PipelinePhase.ACCEPTED in allowed
        assert PipelinePhase.REJECTED in allowed
        assert PipelinePhase.FAILED in allowed

    def test_terminal_phases_transitions(self) -> None:
        """ACCEPTED/REJECTED can transition to FAILED; FAILED has no outbound."""
        assert PipelinePhase.FAILED in ALLOWED_TRANSITIONS.get(PipelinePhase.ACCEPTED, frozenset())
        assert PipelinePhase.FAILED in ALLOWED_TRANSITIONS.get(PipelinePhase.REJECTED, frozenset())
        assert ALLOWED_TRANSITIONS.get(PipelinePhase.FAILED, frozenset()) == frozenset()

    def test_all_non_terminal_phases_have_transitions(self) -> None:
        """Every non-terminal phase must have an entry in ALLOWED_TRANSITIONS."""
        non_terminal = set(PipelinePhase) - TERMINAL_PHASES
        for phase in non_terminal:
            assert phase in ALLOWED_TRANSITIONS, f"{phase} missing from ALLOWED_TRANSITIONS"


class TestRevisionLoop:
    def test_reviewing_to_implementing_is_valid(self) -> None:
        """REVIEWING -> IMPLEMENTING is the revision loop."""
        allowed = ALLOWED_TRANSITIONS[PipelinePhase.REVIEWING]
        assert PipelinePhase.IMPLEMENTING in allowed

    def test_implementing_to_reviewing_is_valid(self) -> None:
        """IMPLEMENTING -> REVIEWING completes the loop."""
        allowed = ALLOWED_TRANSITIONS[PipelinePhase.IMPLEMENTING]
        assert PipelinePhase.REVIEWING in allowed

    def test_full_revision_cycle_is_reachable(self) -> None:
        """Simulate a complete revision loop through the transition map."""
        phase = PipelinePhase.REVIEWING
        # Step 1: REVIEWING -> IMPLEMENTING
        assert PipelinePhase.IMPLEMENTING in ALLOWED_TRANSITIONS[phase]
        phase = PipelinePhase.IMPLEMENTING
        # Step 2: IMPLEMENTING -> REVIEWING
        assert PipelinePhase.REVIEWING in ALLOWED_TRANSITIONS[phase]
        phase = PipelinePhase.REVIEWING
        # Step 3: REVIEWING -> ACCEPTED
        assert PipelinePhase.ACCEPTED in ALLOWED_TRANSITIONS[phase]


class TestTerminalPhases:
    def test_terminal_phases_are_accepted_rejected_failed(self) -> None:
        assert (
            frozenset({PipelinePhase.ACCEPTED, PipelinePhase.REJECTED, PipelinePhase.FAILED})
            == TERMINAL_PHASES
        )

    def test_pending_is_not_terminal(self) -> None:
        assert PipelinePhase.PENDING not in TERMINAL_PHASES

    def test_implementing_is_not_terminal(self) -> None:
        assert PipelinePhase.IMPLEMENTING not in TERMINAL_PHASES

    def test_reviewing_is_not_terminal(self) -> None:
        assert PipelinePhase.REVIEWING not in TERMINAL_PHASES


class TestIterationState:
    def test_is_terminal_for_accepted(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.ACCEPTED)
        assert state.is_terminal is True

    def test_is_terminal_for_rejected(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.REJECTED)
        assert state.is_terminal is True

    def test_is_terminal_for_failed(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.FAILED)
        assert state.is_terminal is True

    def test_is_not_terminal_for_implementing(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.IMPLEMENTING)
        assert state.is_terminal is False

    def test_can_revise_when_cycle_below_max(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.REVIEWING, revision_cycle=0)
        assert state.can_revise is True

    def test_can_revise_at_one_below_max(self) -> None:
        state = IterationState(
            spec_id="s1",
            phase=PipelinePhase.REVIEWING,
            revision_cycle=MAX_REVISION_CYCLES - 1,
        )
        assert state.can_revise is True

    def test_cannot_revise_at_max(self) -> None:
        state = IterationState(
            spec_id="s1",
            phase=PipelinePhase.REVIEWING,
            revision_cycle=MAX_REVISION_CYCLES,
        )
        assert state.can_revise is False

    def test_cannot_revise_above_max(self) -> None:
        state = IterationState(
            spec_id="s1",
            phase=PipelinePhase.REVIEWING,
            revision_cycle=MAX_REVISION_CYCLES + 1,
        )
        assert state.can_revise is False

    def test_frozen_dataclass(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.PENDING)
        with pytest.raises(AttributeError):
            state.phase = PipelinePhase.PLANNING  # type: ignore[misc]

    def test_defaults(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.PENDING)
        assert state.attempt == 1
        assert state.revision_cycle == 0
        assert state.dag_task_id is None
        assert state.plan_artifact_ref is None
        assert state.error is None
