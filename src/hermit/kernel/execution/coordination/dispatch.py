from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any

import structlog

from hermit.kernel.task.state.outcomes import TERMINAL_TASK_STATUSES
from hermit.runtime.capability.contracts.base import HookEvent

log = structlog.get_logger()

POLL_INTERVAL_SECONDS = 0.1

_INFLIGHT_STATUSES = frozenset(
    {
        "running",
        "dispatching",
        "executing",
        "reconciling",
        "observing",
        "contracting",
        "preflighting",
    }
)


class KernelDispatchService:
    """Small in-process worker pool for async kernel ingress."""

    def __init__(self, runner: Any, *, worker_count: int = 4) -> None:
        self.runner = runner
        self.worker_count = max(1, int(worker_count or 1))
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.worker_count,
            thread_name_prefix="kernel-dispatch",
        )
        self.futures: dict[concurrent.futures.Future[Any], str] = {}
        self.lock = threading.Lock()
        self.kind_handlers: dict[str, Any] = {}
        self.reaper_thread: threading.Thread | None = None
        self.emitted_complete: set[str] = set()
        self._paused = threading.Event()  # When set, dispatch loop skips claiming

    def register_kind_handler(self, kind: str, handler: Any) -> None:
        """Register a custom handler for a step kind.

        When the dispatch loop claims a step attempt whose step has the given
        ``kind``, it will call ``handler(step_attempt_id)`` instead of the
        default ``process_claimed_attempt`` agent loop.  This allows non-LLM
        tasks (e.g. memory promotion) to reuse the same thread pool, heartbeat,
        and recovery infrastructure.
        """
        self.kind_handlers[kind] = handler

    def start(self) -> None:
        self.recover_interrupted_attempts()
        self.thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="kernel-dispatch-loop",
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.reaper_thread is not None:
            self.reaper_thread.join(timeout=5)
        self.executor.shutdown(wait=False, cancel_futures=True)

    def wake(self) -> None:
        self.wake_event.set()

    def pause_dispatch(self) -> None:
        """Pause the dispatch loop — it will skip claiming until resumed."""
        self._paused.set()

    def resume_dispatch(self) -> None:
        """Resume the dispatch loop and wake it immediately."""
        self._paused.clear()
        self.wake_event.set()

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    def report_heartbeat(self, step_attempt_id: str) -> None:
        """Record a heartbeat for a running step attempt.

        Called by step executors to signal liveness.  Uses the lightweight
        ``touch_heartbeat()`` store method which only updates the dedicated
        ``last_heartbeat_at`` column — no full-row rewrite, no event append —
        so heartbeats do not contend for the WAL write lock at high concurrency.
        """
        try:
            store = self.runner.task_controller.store
            store.touch_heartbeat(step_attempt_id)
        except Exception:
            log.exception(
                "kernel_dispatch_heartbeat_failed",
                step_attempt_id=step_attempt_id,
            )

    def check_heartbeat_timeouts(self) -> None:
        """Scan running attempts with heartbeat intervals and fail timed-out ones.

        Heartbeat is opt-in: only attempts whose context contains
        ``heartbeat_interval_seconds`` are checked.  If the last heartbeat
        (or the ``claimed_at`` timestamp when no heartbeat has been reported
        yet) is older than the configured interval, the attempt is marked
        failed with reason ``heartbeat_timeout`` and a retry attempt is
        created if allowed by ``max_attempts``.

        Uses a single combined query across all in-flight statuses to reduce
        database round-trips (previously 3 separate queries).
        """
        store = self.runner.task_controller.store
        now = time.time()
        # Combine all heartbeat-relevant statuses into a single query pass.
        _heartbeat_statuses = ("running", "dispatching", "executing")
        candidates: list[Any] = []
        for status in _heartbeat_statuses:
            candidates.extend(store.list_step_attempts(status=status, limit=500))
        for attempt in candidates:
            ctx = attempt.context or {}
            interval = ctx.get("heartbeat_interval_seconds")
            if interval is None:
                continue
            interval = float(interval)
            last_beat = attempt.last_heartbeat_at or attempt.claimed_at or attempt.started_at
            if last_beat is None:
                continue
            if now - float(last_beat) <= interval:
                continue
            # Heartbeat timed out — fail this attempt.
            log.warning(
                "heartbeat_timeout",
                step_attempt_id=attempt.step_attempt_id,
                last_heartbeat_at=last_beat,
                interval=interval,
            )
            store.update_step_attempt(
                attempt.step_attempt_id,
                status="failed",
                waiting_reason="heartbeat_timeout",
                status_reason="heartbeat_timeout",
                finished_at=now,
            )
            store.update_step(attempt.step_id, status="failed", finished_at=now)
            # Trigger retry via the store if max_attempts allows.
            step = store.get_step(attempt.step_id)
            if step is not None and step.attempt < step.max_attempts:
                store.retry_step(attempt.task_id, attempt.step_id)
            else:
                store.propagate_step_failure(attempt.task_id, attempt.step_id)
                if not store.has_non_terminal_steps(attempt.task_id):
                    store.update_task_status(
                        attempt.task_id,
                        "failed",
                        payload={
                            "result_preview": "heartbeat_timeout",
                            "result_text": "heartbeat_timeout",
                        },
                    )
                    self._emit_subtask_complete_for_task(
                        attempt.task_id,
                        success=False,
                        error="heartbeat_timeout",
                    )

    def resolve_handler(self, step_attempt_id: str) -> Any:
        """Return the handler for the given step attempt based on step kind."""
        if not self.kind_handlers:
            return self.runner.process_claimed_attempt
        try:
            store = self.runner.task_controller.store
            attempt = store.get_step_attempt(step_attempt_id)
            if attempt is not None:
                step = store.get_step(attempt.step_id)
                if step is not None and step.kind in self.kind_handlers:
                    return self.kind_handlers[step.kind]
        except Exception:
            log.warning("kind_handler_resolve_failed", step_attempt_id=step_attempt_id)
        return self.runner.process_claimed_attempt

    @staticmethod
    def _dispatch_with_cleanup(
        handler: Any,
        attempt_id: str,
        store: Any,
    ) -> Any:
        """Run *handler* and close the thread-local SQLite connection afterwards.

        Worker threads in the ThreadPoolExecutor create thread-local SQLite
        connections via ``KernelStore._conn``.  Without explicit cleanup the
        connection (and its file descriptors) leaks until the thread is
        reaped by the pool — which may never happen for long-lived pools.
        """
        try:
            return handler(attempt_id)
        finally:
            try:
                store.close_thread_conn()
            except Exception:
                pass

    def _loop(self) -> None:
        _heartbeat_counter = 0
        while not self.stop_event.is_set():
            self._reap_futures()
            _heartbeat_counter += 1
            if _heartbeat_counter >= 10:
                self.check_heartbeat_timeouts()
                _heartbeat_counter = 0
            claimed = False
            if not self._paused.is_set():
                while self._capacity_available():
                    attempt = self.runner.task_controller.store.claim_next_ready_step_attempt()
                    if attempt is None:
                        break
                    handler = self.resolve_handler(attempt.step_attempt_id)
                    future = self.executor.submit(
                        handler,
                        attempt.step_attempt_id,
                    )
                    with self.lock:
                        self.futures[future] = attempt.step_attempt_id
                    claimed = True
            if claimed:
                continue
            self.wake_event.wait(POLL_INTERVAL_SECONDS)
            self.wake_event.clear()

    def _capacity_available(self) -> bool:
        with self.lock:
            return len(self.futures) < self.worker_count

    def _reap_futures(self) -> None:
        done: list[concurrent.futures.Future[Any]] = []
        with self.lock:
            for future in list(self.futures):
                if future.done():
                    done.append(future)
        for future in done:
            attempt_id = ""
            with self.lock:
                attempt_id = self.futures.pop(future, "")
            try:
                future.result()
                self.on_attempt_completed(attempt_id)
            except Exception:
                log.exception("kernel_dispatch_attempt_failed", step_attempt_id=attempt_id)
                # Ensure the step is marked failed and DAG dependents are
                # unblocked even when process_claimed_attempt crashes.
                self.force_fail_attempt(attempt_id)

    def force_fail_attempt(self, step_attempt_id: str) -> None:
        """Mark a crashed attempt as failed and propagate DAG failure.

        Called when ``process_claimed_attempt`` itself raises an unhandled
        exception.  Without this, the step remains in an intermediate status
        and downstream DAG steps hang indefinitely.

        Also emits ``SUBTASK_COMPLETE`` when the failure causes the parent
        task to reach a terminal state.
        """
        if not step_attempt_id:
            return
        try:
            store = self.runner.task_controller.store
            attempt = store.get_step_attempt(step_attempt_id)
            if attempt is None:
                return
            now = time.time()
            if attempt.status not in ("failed", "succeeded", "completed", "skipped"):
                store.update_step_attempt(
                    step_attempt_id,
                    status="failed",
                    waiting_reason="worker_exception",
                    finished_at=now,
                )
                store.update_step(attempt.step_id, status="failed", finished_at=now)
            store.propagate_step_failure(attempt.task_id, attempt.step_id)
            task_terminal = not store.has_non_terminal_steps(attempt.task_id)
            if task_terminal:
                store.update_task_status(
                    attempt.task_id,
                    "failed",
                    payload={
                        "result_preview": "worker_exception",
                        "result_text": "worker_exception",
                    },
                )
            self.wake_event.set()
            if task_terminal:
                self._emit_subtask_complete_for_task(
                    attempt.task_id,
                    success=False,
                    error="worker_exception",
                )
        except Exception:
            log.exception(
                "kernel_dispatch_force_fail_failed",
                step_attempt_id=step_attempt_id,
            )

    def on_attempt_completed(self, step_attempt_id: str) -> None:
        """Wake the dispatch loop after a step completes and emit
        ``SUBTASK_COMPLETE`` when the parent task reaches a terminal state.

        DAG activation (activate_waiting_dependents / propagate_step_failure) is
        handled by ``controller.finalize_result()`` which is called inside
        ``process_claimed_attempt``.  This method wakes the dispatch loop so
        that newly-ready steps get claimed promptly and fires the hook so that
        metaloop, benchmark, and other consumers are notified.
        """
        if not step_attempt_id:
            return
        self.wake_event.set()
        self._maybe_emit_subtask_complete(step_attempt_id)

    def _maybe_emit_subtask_complete(self, step_attempt_id: str) -> None:
        """Check whether the parent task has reached a terminal state and
        fire ``SUBTASK_COMPLETE`` if so.

        This is the central emission point for the hook.  Both the normal
        completion path (``_on_attempt_completed``) and the failure path
        (``_force_fail_attempt``) funnel through here.
        """
        try:
            store = self.runner.task_controller.store
            attempt = store.get_step_attempt(step_attempt_id)
            if attempt is None:
                return
            task = store.get_task(attempt.task_id)
            if task is None:
                return
            if task.status not in TERMINAL_TASK_STATUSES:
                return
            success = task.status in ("completed", "succeeded")
            error = None if success else (task.status or "failed")
            self._emit_subtask_complete_for_task(attempt.task_id, success=success, error=error)
        except Exception:
            log.warning(
                "subtask_complete_emit_failed",
                step_attempt_id=step_attempt_id,
                exc_info=True,
            )

    def _emit_subtask_complete_for_task(
        self,
        task_id: str,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Fire the ``SUBTASK_COMPLETE`` hook for a task that has reached
        a terminal state.

        Passes ``task_id``, ``success``, ``error``, ``store``, ``status``,
        and ``settings`` so that all registered consumers (metaloop,
        benchmark, etc.) receive the parameters they expect.
        """
        with self.lock:
            if task_id in self.emitted_complete:
                return
            self.emitted_complete.add(task_id)
        try:
            pm = getattr(self.runner, "pm", None)
            if pm is None:
                return
            store = self.runner.task_controller.store
            status = "succeeded" if success else (error or "failed")
            settings = getattr(pm, "settings", None)
            pm.hooks.fire(
                HookEvent.SUBTASK_COMPLETE,
                task_id=task_id,
                success=success,
                error=error,
                store=store,
                status=status,
                settings=settings,
            )
            log.debug(
                "subtask_complete_emitted",
                task_id=task_id,
                success=success,
            )
        except Exception:
            log.warning(
                "subtask_complete_fire_failed",
                task_id=task_id,
                exc_info=True,
            )

    def check_deliberation_needed(self, step_attempt_id: str) -> bool:
        """Check if a step attempt requires deliberation before dispatch.

        Reads risk_band from the attempt context and step_kind from the step,
        then delegates to ``DeliberationService.check_deliberation_needed``.
        When deliberation is required the attempt and step are moved to
        ``deliberation_pending`` status and a ledger event is recorded.

        Returns True when deliberation is required, False otherwise.
        """
        from hermit.kernel.execution.competition.deliberation_service import (
            DeliberationService,
        )

        store = self.runner.task_controller.store
        attempt = store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return False

        ctx = attempt.context or {}
        risk_band = ctx.get("risk_band", "low")

        # Skip deliberation for autonomous policy profile tasks.
        policy_profile = ctx.get("ingress_metadata", {}).get("policy_profile", "")
        if policy_profile == "autonomous":
            return False

        step = store.get_step(attempt.step_id)
        step_kind = step.kind if step else "execute"

        if not DeliberationService.check_deliberation_needed(
            risk_level=risk_band, action_class=step_kind
        ):
            return False

        # Move attempt to deliberation_pending.
        now = time.time()
        updated_ctx = dict(ctx)
        updated_ctx["deliberation_risk_band"] = risk_band
        updated_ctx["deliberation_step_kind"] = step_kind
        updated_ctx["deliberation_pending_at"] = now
        store.update_step_attempt(
            step_attempt_id,
            status="deliberation_pending",
            context=updated_ctx,
            waiting_reason="deliberation_required",
        )
        store.update_step(attempt.step_id, status="deliberation_pending")

        # Record ledger event.
        store.append_event(
            event_type="dispatch.deliberation_required",
            entity_type="step_attempt",
            entity_id=step_attempt_id,
            task_id=attempt.task_id,
            step_id=attempt.step_id,
            payload={
                "risk_band": risk_band,
                "step_kind": step_kind,
            },
        )
        log.info(
            "dispatch.deliberation_required",
            step_attempt_id=step_attempt_id,
            risk_band=risk_band,
            step_kind=step_kind,
        )
        return True

    def reaper_loop(self) -> None:
        """Background loop that periodically checks for expired leases.

        Runs as a daemon thread alongside the main dispatch loop.  Checks
        heartbeat timeouts on a slower cadence than the main dispatch poll.
        """
        while not self.stop_event.is_set():
            try:
                self.check_heartbeat_timeouts()
            except Exception:
                log.exception("lease_reaper_error")
            self.stop_event.wait(2.0)

    def recover_interrupted_attempts(self) -> None:
        store = self.runner.task_controller.store
        now = time.time()

        # Phase 1: recover all in-flight intermediate-status attempts.
        # Track which steps already have a recovered attempt to avoid
        # re-readying multiple duplicate attempts for the same step.
        recovered_steps: set[str] = set()
        for inflight_status in _INFLIGHT_STATUSES:
            for attempt in store.list_step_attempts(status=inflight_status, limit=1000):
                if attempt.step_id in recovered_steps:
                    # Duplicate attempt for a step already recovered — supersede it.
                    context = dict(attempt.context or {})
                    context["recovered_after_interrupt"] = True
                    context["recovery_action"] = "superseded_duplicate"
                    store.update_step_attempt(
                        attempt.step_attempt_id,
                        status="superseded",
                        context=context,
                        waiting_reason="duplicate_recovered_superseded",
                        finished_at=now,
                    )
                    continue
                self._recover_single_attempt(store, attempt, now)
                recovered_steps.add(attempt.step_id)

        # Phase 2: deduplicate ready attempts — if multiple ready attempts exist
        # for the same step, keep only the latest and supersede the rest.
        ready_by_step: dict[str, list[Any]] = {}
        for attempt in store.list_step_attempts(status="ready", limit=1000):
            ready_by_step.setdefault(attempt.step_id, []).append(attempt)
        for _step_id, attempts in ready_by_step.items():
            if len(attempts) > 1:
                # Keep the one with the highest attempt number; supersede the rest.
                attempts.sort(key=lambda a: a.attempt, reverse=True)
                for dup in attempts[1:]:
                    store.update_step_attempt(
                        dup.step_attempt_id,
                        status="superseded",
                        waiting_reason="duplicate_ready_superseded",
                        finished_at=now,
                    )

        # Phase 3: repair ready attempts whose parent task has a stale status.
        for attempt in store.list_step_attempts(status="ready", limit=1000):
            ingress = dict(attempt.context.get("ingress_metadata", {}) or {})
            if ingress.get("dispatch_mode") != "async":
                continue
            task = store.get_task(attempt.task_id)
            if task and task.status not in ("queued", "running"):
                store.update_task_status(
                    attempt.task_id,
                    "queued",
                    payload={
                        "result_preview": "task_status_repaired_for_ready_attempt",
                        "result_text": "task_status_repaired_for_ready_attempt",
                    },
                )

    def _fail_orphaned_sync_attempt(self, store: Any, attempt: Any, now: float) -> None:
        """Mark a non-async in-flight attempt as cancelled.

        Sync-path attempts are not managed by the dispatch service, so they
        cannot be re-queued.  Leaving them in an intermediate status forever
        would block their parent task, so we cancel them with a clear reason.

        Uses ``cancelled`` rather than ``failed`` because the attempt was
        abandoned due to a process interrupt, not because the work itself
        failed.  This prevents confusing ``task.failed`` events from
        appearing in ``hermit task list`` after a CLI session was
        interrupted (e.g. Ctrl-C during ``hermit run``).
        """
        context = dict(attempt.context or {})
        context["recovered_after_interrupt"] = True
        context["interrupt_recovered_at"] = now
        context["original_status_at_interrupt"] = attempt.status
        context["recovery_action"] = "cancelled_orphaned_sync"
        store.update_step_attempt(
            attempt.step_attempt_id,
            status="cancelled",
            context=context,
            waiting_reason="worker_interrupted_sync_orphaned",
            finished_at=now,
        )
        store.update_step(
            attempt.step_id,
            status="cancelled",
            finished_at=now,
        )
        store.update_task_status(
            attempt.task_id,
            "cancelled",
            payload={
                "result_preview": "worker_interrupted_sync_orphaned",
                "result_text": "worker_interrupted_sync_orphaned",
            },
        )

    def _recover_single_attempt(self, store: Any, attempt: Any, now: float) -> None:
        ingress = dict(attempt.context.get("ingress_metadata", {}) or {})
        if ingress.get("dispatch_mode") != "async":
            self._fail_orphaned_sync_attempt(store, attempt, now)
            return
        context = dict(attempt.context or {})
        context["recovered_after_interrupt"] = True
        context["interrupt_recovered_at"] = now
        context["original_status_at_interrupt"] = attempt.status

        capability_grant_id = getattr(attempt, "capability_grant_id", None)
        if capability_grant_id:
            # capability grant exists → action may have executed → block for manual review
            context["recovery_required"] = True
            context["reentry_required"] = True
            context["reentry_reason"] = "worker_interrupted"
            context["reentry_boundary"] = "observation_resolution"
            context["reentry_requested_at"] = now
            store.update_step_attempt(
                attempt.step_attempt_id,
                status="blocked",
                context=context,
                waiting_reason="worker_interrupted_recovery_required",
                finished_at=None,
            )
            store.update_step(
                attempt.step_id,
                status="blocked",
                finished_at=None,
            )
            store.update_task_status(
                attempt.task_id,
                "blocked",
                payload={
                    "result_preview": "worker_interrupted_recovery_required",
                    "result_text": "worker_interrupted_recovery_required",
                },
            )
            return

        # no capability grant → action never authorized/executed → safe to re-enter
        context["reentry_required"] = True
        context["reentry_reason"] = "worker_interrupted"
        context["reentry_boundary"] = "policy_reentry"
        context["reentry_requested_at"] = now
        store.update_step_attempt(
            attempt.step_attempt_id,
            status="ready",
            context=context,
            waiting_reason="worker_interrupted_requeued",
            finished_at=None,
        )
        store.update_step(
            attempt.step_id,
            status="ready",
            finished_at=None,
        )
        store.update_task_status(
            attempt.task_id,
            "queued",
            payload={
                "result_preview": "worker_interrupted_requeued",
                "result_text": "worker_interrupted_requeued",
            },
        )
