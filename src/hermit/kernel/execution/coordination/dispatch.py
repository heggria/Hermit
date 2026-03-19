from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any

import structlog

log = structlog.get_logger()

_POLL_INTERVAL_SECONDS = 0.5


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

    def _on_attempt_completed(self, step_attempt_id: str) -> None:
        """After a step completes, activate waiting dependents and wake the loop."""
        if not step_attempt_id:
            return
        try:
            attempt = self._runner.task_controller.store.get_step_attempt(step_attempt_id)
            if attempt is None:
                return
            if attempt.status in ("succeeded", "completed", "skipped"):
                activated = self._runner.task_controller.store.activate_waiting_dependents(
                    attempt.task_id, attempt.step_id
                )
                if activated:
                    self._wake.set()
        except Exception:
            log.exception(
                "kernel_dispatch_dag_activation_failed",
                step_attempt_id=step_attempt_id,
            )

    def _recover_interrupted_attempts(self) -> None:
        store = self._runner.task_controller.store
        now = time.time()
        for attempt in store.list_step_attempts(status="running", limit=1000):
            ingress = dict(attempt.context.get("ingress_metadata", {}) or {})
            if ingress.get("dispatch_mode") != "async":
                continue
            context = dict(attempt.context or {})
            context["recovered_after_interrupt"] = True
            context["interrupt_recovered_at"] = now
            capability_grant_id = getattr(attempt, "capability_grant_id", None)
            if capability_grant_id:
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
                continue
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
