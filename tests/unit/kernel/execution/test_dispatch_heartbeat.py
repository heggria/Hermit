"""Tests for KernelDispatchService heartbeat reporting and timeout checking."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from hermit.kernel.execution.coordination.dispatch import KernelDispatchService


def _make_runner(store: Any | None = None) -> SimpleNamespace:
    if store is None:
        store = MagicMock()
    return SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        process_claimed_attempt=MagicMock(),
    )


def _make_attempt(
    *,
    step_attempt_id: str = "sa-1",
    step_id: str = "step-1",
    task_id: str = "task-1",
    status: str = "running",
    context: dict[str, Any] | None = None,
    last_heartbeat_at: float | None = None,
    claimed_at: float | None = None,
    started_at: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        step_attempt_id=step_attempt_id,
        step_id=step_id,
        task_id=task_id,
        status=status,
        context=context or {},
        last_heartbeat_at=last_heartbeat_at,
        claimed_at=claimed_at,
        started_at=started_at,
    )


class TestReportHeartbeat:
    def test_updates_step_attempt_heartbeat(self) -> None:
        store = MagicMock()
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        with patch("time.time", return_value=1000.0):
            svc.report_heartbeat("sa-1")

        store.update_step_attempt.assert_called_once_with("sa-1", last_heartbeat_at=1000.0)

    def test_heartbeat_exception_logged_not_raised(self) -> None:
        store = MagicMock()
        store.update_step_attempt.side_effect = RuntimeError("db error")
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        # Should not raise
        svc.report_heartbeat("sa-1")


def _heartbeat_store(attempt: SimpleNamespace | None = None) -> MagicMock:
    """Build a store mock that returns the attempt only for the 'running' status call."""
    store = MagicMock()

    def _side_effect(*, status: str, limit: int = 500) -> list:
        if status == "running" and attempt is not None:
            return [attempt]
        return []

    store.list_step_attempts.side_effect = _side_effect
    return store


class TestCheckHeartbeatTimeouts:
    def test_no_heartbeat_interval_skipped(self) -> None:
        attempt = _make_attempt(context={})
        store = _heartbeat_store(attempt)
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        svc.check_heartbeat_timeouts()

        store.update_step_attempt.assert_not_called()

    def test_heartbeat_within_interval_not_timed_out(self) -> None:
        attempt = _make_attempt(
            context={"heartbeat_interval_seconds": 30},
            last_heartbeat_at=995.0,
        )
        store = _heartbeat_store(attempt)
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        with patch("time.time", return_value=1000.0):
            svc.check_heartbeat_timeouts()

        store.update_step_attempt.assert_not_called()

    def test_heartbeat_expired_marks_failed(self) -> None:
        attempt = _make_attempt(
            context={"heartbeat_interval_seconds": 10},
            last_heartbeat_at=980.0,
        )
        step = SimpleNamespace(attempt=1, max_attempts=1)
        store = _heartbeat_store(attempt)
        store.get_step.return_value = step
        store.has_non_terminal_steps.return_value = False
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        with patch("time.time", return_value=1000.0):
            svc.check_heartbeat_timeouts()

        store.update_step_attempt.assert_called_once()
        call_kwargs = store.update_step_attempt.call_args[1]
        assert call_kwargs["status"] == "failed"
        assert call_kwargs["status_reason"] == "heartbeat_timeout"

    def test_heartbeat_timeout_retries_if_allowed(self) -> None:
        attempt = _make_attempt(
            context={"heartbeat_interval_seconds": 10},
            last_heartbeat_at=980.0,
        )
        step = SimpleNamespace(attempt=1, max_attempts=3)
        store = _heartbeat_store(attempt)
        store.get_step.return_value = step
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        with patch("time.time", return_value=1000.0):
            svc.check_heartbeat_timeouts()

        store.retry_step.assert_called_once_with("task-1", "step-1")
        store.propagate_step_failure.assert_not_called()

    def test_heartbeat_timeout_propagates_when_max_attempts_reached(self) -> None:
        attempt = _make_attempt(
            context={"heartbeat_interval_seconds": 10},
            last_heartbeat_at=980.0,
        )
        step = SimpleNamespace(attempt=3, max_attempts=3)
        store = _heartbeat_store(attempt)
        store.get_step.return_value = step
        store.has_non_terminal_steps.return_value = False
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        with patch("time.time", return_value=1000.0):
            svc.check_heartbeat_timeouts()

        store.propagate_step_failure.assert_called_once()
        store.update_task_status.assert_called_once()
        assert store.update_task_status.call_args[0][1] == "failed"

    def test_heartbeat_timeout_task_not_failed_if_other_steps_remain(self) -> None:
        attempt = _make_attempt(
            context={"heartbeat_interval_seconds": 10},
            last_heartbeat_at=980.0,
        )
        step = SimpleNamespace(attempt=1, max_attempts=1)
        store = _heartbeat_store(attempt)
        store.get_step.return_value = step
        store.has_non_terminal_steps.return_value = True
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        with patch("time.time", return_value=1000.0):
            svc.check_heartbeat_timeouts()

        store.propagate_step_failure.assert_called_once()
        store.update_task_status.assert_not_called()

    def test_uses_claimed_at_when_no_heartbeat(self) -> None:
        attempt = _make_attempt(
            context={"heartbeat_interval_seconds": 10},
            last_heartbeat_at=None,
            claimed_at=980.0,
            started_at=975.0,
        )
        step = SimpleNamespace(attempt=1, max_attempts=1)
        store = _heartbeat_store(attempt)
        store.get_step.return_value = step
        store.has_non_terminal_steps.return_value = False
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        with patch("time.time", return_value=1000.0):
            svc.check_heartbeat_timeouts()

        # claimed_at=980.0, interval=10, now=1000 -> expired
        store.update_step_attempt.assert_called_once()

    def test_no_timestamps_at_all_skipped(self) -> None:
        attempt = _make_attempt(
            context={"heartbeat_interval_seconds": 10},
            last_heartbeat_at=None,
            claimed_at=None,
            started_at=None,
        )
        store = _heartbeat_store(attempt)
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        with patch("time.time", return_value=1000.0):
            svc.check_heartbeat_timeouts()

        store.update_step_attempt.assert_not_called()

    def test_checks_all_relevant_statuses(self) -> None:
        store = MagicMock()
        store.list_step_attempts.return_value = []
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        svc.check_heartbeat_timeouts()

        status_args = [c[1]["status"] for c in store.list_step_attempts.call_args_list]
        assert "running" in status_args
        assert "dispatching" in status_args
        assert "executing" in status_args
