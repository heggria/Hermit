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
            except Exception:
                log.exception("kernel_dispatch_attempt_failed", step_attempt_id=attempt_id)

    def _recover_interrupted_attempts(self) -> None:
        store = self._runner.task_controller.store
        now = time.time()
        for attempt in store.list_step_attempts(status="running", limit=1000):
            ingress = dict(attempt.context.get("ingress_metadata", {}) or {})
            if ingress.get("dispatch_mode") != "async":
                continue
            store.update_step_attempt(
                attempt.step_attempt_id,
                status="failed",
                waiting_reason="worker_interrupted",
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
                    "result_preview": "worker_interrupted",
                    "result_text": "worker_interrupted",
                },
            )
