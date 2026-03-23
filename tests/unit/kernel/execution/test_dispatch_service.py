"""Tests for KernelDispatchService (dispatch.py) — coordination/dispatch coverage."""

from __future__ import annotations

import concurrent.futures
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.execution.coordination.dispatch import (
    _INFLIGHT_STATUSES,
    KernelDispatchService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    capability_grant_id: str | None = None,
    attempt: int = 1,
    queue_priority: int = 0,
) -> SimpleNamespace:
    if context is None:
        context = {}
    return SimpleNamespace(
        step_attempt_id=step_attempt_id,
        step_id=step_id,
        task_id=task_id,
        status=status,
        context=context,
        capability_grant_id=capability_grant_id,
        attempt=attempt,
        queue_priority=queue_priority,
    )


# ---------------------------------------------------------------------------
# TestInit
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_worker_count(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner)
        assert svc.worker_count == 4

    def test_worker_count_clamped_to_1_from_zero(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner, worker_count=0)
        assert svc.worker_count == 1

    def test_worker_count_clamped_to_1_from_negative(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner, worker_count=-5)
        assert svc.worker_count == 1

    def test_worker_count_clamped_to_1_from_none(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner, worker_count=None)  # type: ignore[arg-type]
        assert svc.worker_count == 1

    def test_custom_worker_count(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner, worker_count=8)
        assert svc.worker_count == 8

    def test_internal_state_initialized(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner)
        assert isinstance(svc.stop_event, threading.Event)
        assert isinstance(svc.wake_event, threading.Event)
        assert svc.thread is None
        assert isinstance(svc.futures, dict)
        assert len(svc.futures) == 0


# ---------------------------------------------------------------------------
# TestStartStop
# ---------------------------------------------------------------------------


class TestStartStop:
    def test_start_creates_thread(self) -> None:
        store = MagicMock()
        store.list_step_attempts.return_value = []
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.start()
        try:
            assert svc.thread is not None
            assert svc.thread.is_alive()
        finally:
            svc.stop()

    def test_stop_sets_stop_event(self) -> None:
        store = MagicMock()
        store.list_step_attempts.return_value = []
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.start()
        svc.stop()
        assert svc.stop_event.is_set()

    def test_stop_without_start(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner)
        svc.stop()  # should not raise
        assert svc.stop_event.is_set()


class TestWake:
    def test_wake_sets_wake_event(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner)
        assert not svc.wake_event.is_set()
        svc.wake()
        assert svc.wake_event.is_set()


# ---------------------------------------------------------------------------
# TestCapacityAvailable
# ---------------------------------------------------------------------------


class TestCapacityAvailable:
    def test_capacity_available_when_no_futures(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner, worker_count=2)
        assert svc._capacity_available() is True

    def test_capacity_available_when_under_limit(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner, worker_count=2)
        # Add one fake future
        fake_future = MagicMock(spec=concurrent.futures.Future)
        svc.futures[fake_future] = "sa-1"
        assert svc._capacity_available() is True

    def test_capacity_not_available_at_limit(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner, worker_count=2)
        for i in range(2):
            fake_future = MagicMock(spec=concurrent.futures.Future)
            svc.futures[fake_future] = f"sa-{i}"
        assert svc._capacity_available() is False


# ---------------------------------------------------------------------------
# TestOnAttemptCompleted
# ---------------------------------------------------------------------------


class TestOnAttemptCompleted:
    def test_truthy_id_sets_wake(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner)
        svc.wake_event.clear()
        svc.on_attempt_completed("sa-1")
        assert svc.wake_event.is_set()

    def test_empty_id_does_not_set_wake(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner)
        svc.wake_event.clear()
        svc.on_attempt_completed("")
        assert not svc.wake_event.is_set()


# ---------------------------------------------------------------------------
# TestReapFutures
# ---------------------------------------------------------------------------


class TestReapFutures:
    def test_completed_future_reaped(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner)

        future = concurrent.futures.Future()
        future.set_result(None)
        svc.futures[future] = "sa-1"

        svc._reap_futures()
        assert len(svc.futures) == 0
        assert svc.wake_event.is_set()

    def test_failed_future_triggers_force_fail(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(status="running")
        store.get_step_attempt.return_value = attempt
        store.has_non_terminal_steps.return_value = False
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        future = concurrent.futures.Future()
        future.set_exception(RuntimeError("boom"))
        svc.futures[future] = "sa-1"

        svc._reap_futures()
        assert len(svc.futures) == 0
        store.update_step_attempt.assert_called()
        store.update_step.assert_called()

    def test_not_done_future_not_reaped(self) -> None:
        runner = _make_runner()
        svc = KernelDispatchService(runner)

        future = MagicMock(spec=concurrent.futures.Future)
        future.done.return_value = False
        svc.futures[future] = "sa-1"

        svc._reap_futures()
        assert len(svc.futures) == 1


# ---------------------------------------------------------------------------
# TestForceFailAttempt
# ---------------------------------------------------------------------------


class TestForceFailAttempt:
    def test_empty_id_returns_early(self) -> None:
        store = MagicMock()
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.force_fail_attempt("")
        store.get_step_attempt.assert_not_called()

    def test_none_attempt_returns_early(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = None
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.force_fail_attempt("sa-1")
        store.update_step_attempt.assert_not_called()

    @pytest.mark.parametrize("terminal_status", ["failed", "succeeded", "completed", "skipped"])
    def test_terminal_status_not_updated(self, terminal_status: str) -> None:
        store = MagicMock()
        attempt = _make_attempt(status=terminal_status)
        store.get_step_attempt.return_value = attempt
        store.has_non_terminal_steps.return_value = False
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        svc.force_fail_attempt("sa-1")
        # update_step_attempt should NOT be called for status update
        store.update_step_attempt.assert_not_called()
        # but propagate_step_failure IS still called
        store.propagate_step_failure.assert_called_once()

    def test_non_terminal_status_marked_failed(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(status="running")
        store.get_step_attempt.return_value = attempt
        store.has_non_terminal_steps.return_value = True
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        svc.force_fail_attempt("sa-1")
        store.update_step_attempt.assert_called_once()
        call_kwargs = store.update_step_attempt.call_args
        assert call_kwargs[1]["status"] == "failed" or call_kwargs[0][1] == "failed"
        store.update_step.assert_called_once()
        store.propagate_step_failure.assert_called_once()

    def test_task_failed_when_no_non_terminal_steps(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(status="running")
        store.get_step_attempt.return_value = attempt
        store.has_non_terminal_steps.return_value = False
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        svc.force_fail_attempt("sa-1")
        store.update_task_status.assert_called_once_with(
            "task-1",
            "failed",
            payload={
                "result_preview": "worker_exception",
                "result_text": "worker_exception",
            },
        )

    def test_task_not_failed_when_non_terminal_steps_remain(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(status="running")
        store.get_step_attempt.return_value = attempt
        store.has_non_terminal_steps.return_value = True
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        svc.force_fail_attempt("sa-1")
        store.update_task_status.assert_not_called()

    def test_wake_set_after_force_fail(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(status="running")
        store.get_step_attempt.return_value = attempt
        store.has_non_terminal_steps.return_value = True
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.wake_event.clear()

        svc.force_fail_attempt("sa-1")
        assert svc.wake_event.is_set()

    def test_exception_in_force_fail_logged_not_raised(self) -> None:
        store = MagicMock()
        store.get_step_attempt.side_effect = RuntimeError("db error")
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        # Should not raise
        svc.force_fail_attempt("sa-1")


# ---------------------------------------------------------------------------
# TestFailOrphanedSyncAttempt
# ---------------------------------------------------------------------------


class TestFailOrphanedSyncAttempt:
    def test_marks_attempt_failed_with_context(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(status="running", context={"existing": "data"})
        now = 1000.0
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        svc._fail_orphaned_sync_attempt(store, attempt, now)

        store.update_step_attempt.assert_called_once()
        call_kwargs = store.update_step_attempt.call_args
        ctx = call_kwargs[1].get("context") or call_kwargs[0][2]
        assert ctx["recovered_after_interrupt"] is True
        assert ctx["recovery_action"] == "cancelled_orphaned_sync"

    def test_updates_step_and_task_to_cancelled(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(status="dispatching")
        now = 2000.0
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        svc._fail_orphaned_sync_attempt(store, attempt, now)

        store.update_step.assert_called_once_with(
            attempt.step_id,
            status="cancelled",
            finished_at=now,
        )
        store.update_task_status.assert_called_once()
        task_call = store.update_task_status.call_args
        assert task_call[0][1] == "cancelled"


# ---------------------------------------------------------------------------
# TestRecoverSingleAttempt
# ---------------------------------------------------------------------------


class TestRecoverSingleAttempt:
    def test_non_async_delegates_to_cancel_orphaned(self) -> None:
        store = MagicMock()
        # Non-async attempt (dispatch_mode absent)
        attempt = _make_attempt(
            status="running",
            context={"ingress_metadata": {"dispatch_mode": "sync"}},
        )
        now = 3000.0
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        svc._recover_single_attempt(store, attempt, now)

        # Should have called update to cancel the attempt
        store.update_step_attempt.assert_called_once()
        call_kwargs = store.update_step_attempt.call_args
        assert call_kwargs[1]["status"] == "cancelled"

    def test_async_with_grant_sets_blocked(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(
            status="running",
            context={"ingress_metadata": {"dispatch_mode": "async"}},
            capability_grant_id="grant-1",
        )
        now = 4000.0
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        svc._recover_single_attempt(store, attempt, now)

        store.update_step_attempt.assert_called_once()
        call_kwargs = store.update_step_attempt.call_args
        assert call_kwargs[1]["status"] == "blocked"
        ctx = call_kwargs[1]["context"]
        assert ctx["reentry_required"] is True
        assert ctx["reentry_boundary"] == "observation_resolution"

        store.update_step.assert_called_once()
        step_kwargs = store.update_step.call_args
        assert step_kwargs[1]["status"] == "blocked"

        store.update_task_status.assert_called_once()
        task_args = store.update_task_status.call_args
        assert task_args[0][1] == "blocked"

    def test_async_without_grant_sets_ready(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(
            status="dispatching",
            context={"ingress_metadata": {"dispatch_mode": "async"}},
            capability_grant_id=None,
        )
        now = 5000.0
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        svc._recover_single_attempt(store, attempt, now)

        store.update_step_attempt.assert_called_once()
        call_kwargs = store.update_step_attempt.call_args
        assert call_kwargs[1]["status"] == "ready"
        ctx = call_kwargs[1]["context"]
        assert ctx["reentry_required"] is True
        assert ctx["reentry_boundary"] == "policy_reentry"

        store.update_step.assert_called_once()
        step_kwargs = store.update_step.call_args
        assert step_kwargs[1]["status"] == "ready"

        store.update_task_status.assert_called_once()
        task_args = store.update_task_status.call_args
        assert task_args[0][1] == "queued"


# ---------------------------------------------------------------------------
# TestRecoverInterruptedAttempts
# ---------------------------------------------------------------------------


class TestRecoverInterruptedAttempts:
    """Tests for the 3-phase recovery in _recover_interrupted_attempts."""

    def test_phase1_recovers_async_attempt_without_grant_as_ready(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(
            step_attempt_id="sa-1",
            step_id="step-1",
            status="running",
            context={"ingress_metadata": {"dispatch_mode": "async"}},
            capability_grant_id=None,
        )
        # Phase 1: return attempt for first inflight status, empty for rest
        call_count = 0

        def side_effect(**kwargs: Any) -> list[Any]:
            nonlocal call_count
            status = kwargs.get("status", "")
            if status == "running":
                return [attempt]
            if status == "ready":
                return []  # Phase 2
            return []

        store.list_step_attempts.side_effect = side_effect
        store.get_task.return_value = None

        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.recover_interrupted_attempts()

        # Attempt should be set to ready
        update_calls = store.update_step_attempt.call_args_list
        assert len(update_calls) >= 1
        first_call = update_calls[0]
        assert first_call[1]["status"] == "ready"

    def test_phase1_recovers_async_attempt_with_grant_as_blocked(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(
            step_attempt_id="sa-1",
            step_id="step-1",
            status="observing",
            context={"ingress_metadata": {"dispatch_mode": "async"}},
            capability_grant_id="grant-1",
        )

        def side_effect(**kwargs: Any) -> list[Any]:
            status = kwargs.get("status", "")
            if status == "observing":
                return [attempt]
            if status == "ready":
                return []
            return []

        store.list_step_attempts.side_effect = side_effect
        store.get_task.return_value = None

        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.recover_interrupted_attempts()

        update_calls = store.update_step_attempt.call_args_list
        assert len(update_calls) >= 1
        first_call = update_calls[0]
        assert first_call[1]["status"] == "blocked"

    def test_phase1_cancels_sync_orphaned_attempt(self) -> None:
        store = MagicMock()
        attempt = _make_attempt(
            step_attempt_id="sa-1",
            step_id="step-1",
            status="contracting",
            context={"ingress_metadata": {"dispatch_mode": "sync"}},
        )

        def side_effect(**kwargs: Any) -> list[Any]:
            status = kwargs.get("status", "")
            if status == "contracting":
                return [attempt]
            if status == "ready":
                return []
            return []

        store.list_step_attempts.side_effect = side_effect
        store.get_task.return_value = None

        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.recover_interrupted_attempts()

        update_calls = store.update_step_attempt.call_args_list
        assert len(update_calls) >= 1
        first_call = update_calls[0]
        assert first_call[1]["status"] == "cancelled"

    def test_phase1_supersedes_duplicate_for_same_step(self) -> None:
        store = MagicMock()
        attempt1 = _make_attempt(
            step_attempt_id="sa-1",
            step_id="step-1",
            status="running",
            context={"ingress_metadata": {"dispatch_mode": "async"}},
            capability_grant_id=None,
        )
        attempt2 = _make_attempt(
            step_attempt_id="sa-2",
            step_id="step-1",  # same step_id
            status="running",
            context={"ingress_metadata": {"dispatch_mode": "async"}},
            capability_grant_id=None,
        )

        def side_effect(**kwargs: Any) -> list[Any]:
            status = kwargs.get("status", "")
            if status == "running":
                return [attempt1, attempt2]
            if status == "ready":
                return []
            return []

        store.list_step_attempts.side_effect = side_effect
        store.get_task.return_value = None

        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.recover_interrupted_attempts()

        # First attempt recovered normally, second superseded
        update_calls = store.update_step_attempt.call_args_list
        assert len(update_calls) >= 2
        statuses = [c[1]["status"] for c in update_calls]
        assert "superseded" in statuses
        assert "ready" in statuses

    def test_phase2_deduplicates_ready_attempts_keeps_latest(self) -> None:
        store = MagicMock()
        ready1 = _make_attempt(
            step_attempt_id="sa-1",
            step_id="step-1",
            status="ready",
            attempt=1,
        )
        ready2 = _make_attempt(
            step_attempt_id="sa-2",
            step_id="step-1",
            status="ready",
            attempt=2,
        )

        def side_effect(**kwargs: Any) -> list[Any]:
            status = kwargs.get("status", "")
            if status == "ready":
                return [ready1, ready2]
            return []

        store.list_step_attempts.side_effect = side_effect
        store.get_task.return_value = None

        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.recover_interrupted_attempts()

        # sa-1 (attempt=1) should be superseded, sa-2 (attempt=2) kept
        update_calls = store.update_step_attempt.call_args_list
        superseded_ids = [c[0][0] for c in update_calls if c[1].get("status") == "superseded"]
        assert "sa-1" in superseded_ids

    def test_phase2_single_ready_not_superseded(self) -> None:
        store = MagicMock()
        ready1 = _make_attempt(
            step_attempt_id="sa-1",
            step_id="step-1",
            status="ready",
            attempt=1,
            context={"ingress_metadata": {"dispatch_mode": "sync"}},
        )

        def side_effect(**kwargs: Any) -> list[Any]:
            status = kwargs.get("status", "")
            if status == "ready":
                return [ready1]
            return []

        store.list_step_attempts.side_effect = side_effect
        store.get_task.return_value = None

        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.recover_interrupted_attempts()

        # No supersession should happen
        superseded_calls = [
            c
            for c in store.update_step_attempt.call_args_list
            if c[1].get("status") == "superseded"
        ]
        assert len(superseded_calls) == 0

    def test_phase3_repairs_stale_task_status(self) -> None:
        store = MagicMock()
        ready_attempt = _make_attempt(
            step_attempt_id="sa-1",
            step_id="step-1",
            task_id="task-1",
            status="ready",
            context={"ingress_metadata": {"dispatch_mode": "async"}},
        )
        stale_task = SimpleNamespace(task_id="task-1", status="completed")

        call_count = {"ready": 0}

        def side_effect(**kwargs: Any) -> list[Any]:
            status = kwargs.get("status", "")
            if status == "ready":
                call_count["ready"] += 1
                if call_count["ready"] <= 1:
                    # Phase 2 — dedup
                    return [ready_attempt]
                else:
                    # Phase 3 — repair
                    return [ready_attempt]
            return []

        store.list_step_attempts.side_effect = side_effect
        store.get_task.return_value = stale_task

        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.recover_interrupted_attempts()

        store.update_task_status.assert_called()
        task_call = store.update_task_status.call_args
        assert task_call[0][1] == "queued"

    def test_phase3_skips_non_async_dispatch_mode(self) -> None:
        store = MagicMock()
        ready_attempt = _make_attempt(
            step_attempt_id="sa-1",
            step_id="step-1",
            task_id="task-1",
            status="ready",
            context={"ingress_metadata": {"dispatch_mode": "sync"}},
        )

        call_count = {"ready": 0}

        def side_effect(**kwargs: Any) -> list[Any]:
            status = kwargs.get("status", "")
            if status == "ready":
                call_count["ready"] += 1
                return [ready_attempt]
            return []

        store.list_step_attempts.side_effect = side_effect

        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.recover_interrupted_attempts()

        store.update_task_status.assert_not_called()

    def test_phase3_skips_task_already_queued(self) -> None:
        store = MagicMock()
        ready_attempt = _make_attempt(
            step_attempt_id="sa-1",
            step_id="step-1",
            task_id="task-1",
            status="ready",
            context={"ingress_metadata": {"dispatch_mode": "async"}},
        )
        active_task = SimpleNamespace(task_id="task-1", status="queued")

        call_count = {"ready": 0}

        def side_effect(**kwargs: Any) -> list[Any]:
            status = kwargs.get("status", "")
            if status == "ready":
                call_count["ready"] += 1
                return [ready_attempt]
            return []

        store.list_step_attempts.side_effect = side_effect
        store.get_task.return_value = active_task

        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.recover_interrupted_attempts()

        store.update_task_status.assert_not_called()

    def test_iterates_all_inflight_statuses(self) -> None:
        store = MagicMock()
        store.list_step_attempts.return_value = []
        store.get_task.return_value = None

        runner = _make_runner(store)
        svc = KernelDispatchService(runner)
        svc.recover_interrupted_attempts()

        # Should have called list_step_attempts for each inflight status + "ready" twice
        status_args = [
            c[1]["status"] for c in store.list_step_attempts.call_args_list if "status" in c[1]
        ]
        for inflight_status in _INFLIGHT_STATUSES:
            assert inflight_status in status_args


# ---------------------------------------------------------------------------
# TestLoop
# ---------------------------------------------------------------------------


class TestLoop:
    def test_loop_stops_on_stop_event(self) -> None:
        store = MagicMock()
        store.claim_next_ready_step_attempt.return_value = None
        store.list_step_attempts.return_value = []
        runner = _make_runner(store)
        svc = KernelDispatchService(runner)

        # Set stop before starting the loop
        svc.stop_event.set()
        svc._loop()  # should return immediately

    def test_loop_claims_and_submits_attempt(self) -> None:
        store = MagicMock()
        store.list_step_attempts.return_value = []
        attempt = _make_attempt(step_attempt_id="sa-claim")
        claim_calls = [0]

        def claim_side_effect() -> SimpleNamespace | None:
            claim_calls[0] += 1
            if claim_calls[0] == 1:
                return attempt
            return None

        store.claim_next_ready_step_attempt.side_effect = claim_side_effect

        runner = _make_runner(store)
        svc = KernelDispatchService(runner, worker_count=2)

        # Run loop for brief period then stop
        iteration_count = [0]
        original_reap = svc._reap_futures

        def counting_reap() -> None:
            original_reap()
            iteration_count[0] += 1
            if iteration_count[0] >= 2:
                svc.stop_event.set()

        svc._reap_futures = counting_reap  # type: ignore[assignment]
        svc._loop()

        assert claim_calls[0] >= 1
