"""Tests for hermit.kernel.execution.executor.phase_tracker."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.phase_tracker import (
    _WITNESS_REQUIRED_ACTIONS,
    PhaseTracker,
    _execution_status_from_result_code,
    _needs_witness,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attempt_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults: dict[str, Any] = {
        "conversation_id": "conv-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "source_channel": "cli",
    }
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


# ---------------------------------------------------------------------------
# _needs_witness
# ---------------------------------------------------------------------------


class TestNeedsWitness:
    def test_all_required_actions(self) -> None:
        for action in _WITNESS_REQUIRED_ACTIONS:
            assert _needs_witness(action) is True

    def test_non_required_actions(self) -> None:
        assert _needs_witness("read_local") is False
        assert _needs_witness("network_read") is False
        assert _needs_witness("ephemeral_ui_mutation") is False
        assert _needs_witness("unknown_action") is False

    def test_empty_string(self) -> None:
        assert _needs_witness("") is False


# ---------------------------------------------------------------------------
# _execution_status_from_result_code
# ---------------------------------------------------------------------------


class TestExecutionStatusFromResultCode:
    def test_approval_required(self) -> None:
        assert _execution_status_from_result_code("approval_required") == "awaiting_approval"

    def test_contract_blocked(self) -> None:
        assert _execution_status_from_result_code("contract_blocked") == "blocked"

    def test_observation_submitted(self) -> None:
        assert _execution_status_from_result_code("observation_submitted") == "observing"

    def test_denied(self) -> None:
        assert _execution_status_from_result_code("denied") == "failed"

    def test_failed(self) -> None:
        assert _execution_status_from_result_code("failed") == "failed"

    def test_timeout(self) -> None:
        assert _execution_status_from_result_code("timeout") == "failed"

    def test_cancelled(self) -> None:
        assert _execution_status_from_result_code("cancelled") == "failed"

    def test_reconciled_applied(self) -> None:
        assert _execution_status_from_result_code("reconciled_applied") == "reconciling"

    def test_reconciled_not_applied(self) -> None:
        assert _execution_status_from_result_code("reconciled_not_applied") == "reconciling"

    def test_reconciled_observed(self) -> None:
        assert _execution_status_from_result_code("reconciled_observed") == "reconciling"

    def test_unknown_outcome(self) -> None:
        assert _execution_status_from_result_code("unknown_outcome") == "needs_attention"

    def test_succeeded(self) -> None:
        assert _execution_status_from_result_code("succeeded") == "succeeded"

    def test_any_other_code_succeeds(self) -> None:
        assert _execution_status_from_result_code("some_random_code") == "succeeded"

    def test_empty_string(self) -> None:
        assert _execution_status_from_result_code("") == "succeeded"


# ---------------------------------------------------------------------------
# PhaseTracker
# ---------------------------------------------------------------------------


class TestPhaseTracker:
    def test_set_attempt_phase_delegates_to_helper(self) -> None:
        store = MagicMock()
        attempt = SimpleNamespace(context={"phase": ""})
        store.get_step_attempt.return_value = attempt
        tracker = PhaseTracker(store=store)
        ctx = _make_attempt_ctx()
        tracker.set_attempt_phase(ctx, "executing", reason="test")
        store.update_step_attempt.assert_called_once()
        store.append_event.assert_called_once()

    def test_set_attempt_phase_noop_same_phase(self) -> None:
        store = MagicMock()
        attempt = SimpleNamespace(context={"phase": "executing"})
        store.get_step_attempt.return_value = attempt
        tracker = PhaseTracker(store=store)
        ctx = _make_attempt_ctx()
        tracker.set_attempt_phase(ctx, "executing", reason="test")
        store.update_step_attempt.assert_not_called()
        store.append_event.assert_not_called()

    def test_set_attempt_phase_missing_attempt(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = None
        tracker = PhaseTracker(store=store)
        ctx = _make_attempt_ctx()
        # Should not raise
        tracker.set_attempt_phase(ctx, "executing", reason="test")
        store.update_step_attempt.assert_not_called()
        store.append_event.assert_not_called()
