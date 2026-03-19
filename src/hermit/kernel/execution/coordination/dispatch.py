from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any

import structlog

log = structlog.get_logger()

_POLL_INTERVAL_SECONDS = 0.5

_INFLIGHT_STATUSES = frozenset(
    {
        "running",
        "dispatching",
        "reconciling",
        "observing",
        "contracting",
        "preflighting",
    }
)


class KernelDispatchService:
    """Small in-process worker pool for async kernel ingress."""

    def __init__(self, runner: Any, *, worker_count: int = 4) -> None:
        self._runner = runner
        self._worker_count = max(1, int(worker_count or 1))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self._worker_count,
            thread_name_prefix="kernel-dispatch",
        )
        self._futures: dict[concurrent.futures.Future[Any], str] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        self._recover_interrupted_attempts()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="kernel-dispatch-loop",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._reap_futures()
            claimed = False
            while self._capacity_available():
                attempt = self._runner.task_controller.store.claim_next_ready_step_attempt()
                if attempt is None:
                    break
                future = self._executor.submit(
                    self._runner.process_claimed_attempt,
                    attempt.step_attempt_id,
                )
                with self._lock:
                    self._futures[future] = attempt.step_attempt_id
                claimed = True
            if claimed:
                continue
            self._wake.wait(_POLL_INTERVAL_SECONDS)
            self._wake.clear()

    def _capacity_available(self) -> bool:
        with self._lock:
            return len(self._futures) < self._worker_count

    def _reap_futures(self) -> None:
        done: list[concurrent.futures.Future[Any]] = []
        with self._lock:
            for future in list(self._futures):
                if future.done():
                    done.append(future)
        for future in done:
            attempt_id = ""
            with self._lock:
                attempt_id = self._futures.pop(future, "")
            try:
                future.result()
                self._on_attempt_completed(attempt_id)
            except Exception:
                log.exception("kernel_dispatch_attempt_failed", step_attempt_id=attempt_id)
                # Ensure the step is marked failed and DAG dependents are
                # unblocked even when process_claimed_attempt crashes.
                self._force_fail_attempt(attempt_id)

    def _force_fail_attempt(self, step_attempt_id: str) -> None:
        """Mark a crashed attempt as failed and propagate DAG failure.

        Called when ``process_claimed_attempt`` itself raises an unhandled
        exception.  Without this, the step remains in an intermediate status
        and downstream DAG steps hang indefinitely.
        """
        if not step_attempt_id:
            return
        try:
            store = self._runner.task_controller.store
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
            if not store.has_non_terminal_steps(attempt.task_id):
                store.update_task_status(
                    attempt.task_id,
                    "failed",
                    payload={
                        "result_preview": "worker_exception",
                        "result_text": "worker_exception",
                    },
                )
            self._wake.set()
        except Exception:
            log.exception(
                "kernel_dispatch_force_fail_failed",
                step_attempt_id=step_attempt_id,
            )

    def _on_attempt_completed(self, step_attempt_id: str) -> None:
        """Wake the dispatch loop after a step completes.

        DAG activation (activate_waiting_dependents / propagate_step_failure) is
        handled by ``controller.finalize_result()`` which is called inside
        ``process_claimed_attempt``.  This method only needs to wake the dispatch
        loop so that newly-ready steps get claimed promptly.
        """
        if step_attempt_id:
            self._wake.set()

    def _recover_interrupted_attempts(self) -> None:
        store = self._runner.task_controller.store
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
        """Mark a non-async in-flight attempt as failed.

        Sync-path attempts are not managed by the dispatch service, so they
        cannot be re-queued.  Leaving them in an intermediate status forever
        would block their parent task, so we fail them with a clear reason.
        """
        context = dict(attempt.context or {})
        context["recovered_after_interrupt"] = True
        context["interrupt_recovered_at"] = now
        context["original_status_at_interrupt"] = attempt.status
        context["recovery_action"] = "failed_orphaned_sync"
        store.update_step_attempt(
            attempt.step_attempt_id,
            status="failed",
            context=context,
            waiting_reason="worker_interrupted_sync_orphaned",
            finished_at=now,
        )
        store.update_step(
            attempt.step_id,
            status="failed",
            finished_at=now,
        )
        store.update_task_status(
            attempt.task_id,
            "failed",
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
