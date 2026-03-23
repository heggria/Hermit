"""Tests for dispatch recovery deduplication.

Verifies that _recover_interrupted_attempts correctly deduplicates
multiple in-flight attempts for the same step, keeping only one and
superseding the rest.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from hermit.kernel.execution.coordination.dispatch import KernelDispatchService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController


def _make_dispatch_service(store: KernelStore, controller: TaskController) -> KernelDispatchService:
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        process_claimed_attempt=lambda step_attempt_id: None,
    )
    return KernelDispatchService(runner, worker_count=1)


_ASYNC_CONTEXT = json.dumps({"ingress_metadata": {"dispatch_mode": "async"}})


def _create_async_task(
    controller: TaskController,
    store: KernelStore,
    conversation_id: str,
) -> tuple[str, str, str]:
    """Create a task with one async step attempt. Returns (task_id, step_id, attempt_id)."""
    ctx = controller.start_task(
        conversation_id=conversation_id,
        goal="test recovery dedup",
        source_channel="chat",
        kind="respond",
    )
    store.update_step_attempt(
        ctx.step_attempt_id,
        context={"ingress_metadata": {"dispatch_mode": "async"}},
    )
    return ctx.task_id, ctx.step_id, ctx.step_attempt_id


def _insert_raw_attempt(
    store: KernelStore,
    *,
    task_id: str,
    step_id: str,
    attempt_num: int,
    status: str,
) -> str:
    """Insert a raw step_attempt row. Returns the attempt ID."""
    attempt_id = store._id("attempt")
    store._get_conn().execute(
        """
        INSERT INTO step_attempts (
            step_attempt_id, task_id, step_id, attempt, status,
            context_json, queue_priority, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (attempt_id, task_id, step_id, attempt_num, status, _ASYNC_CONTEXT, 0, time.time()),
    )
    store._get_conn().commit()
    return attempt_id


class TestRecoveryDedupSameStep:
    """When multiple inflight attempts exist for the same step, only one is recovered."""

    def test_duplicate_reconciling_attempts_superseded(self, tmp_path: Path) -> None:
        """Multiple reconciling attempts for the same step: first recovered, rest superseded."""
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        task_id, step_id, first_attempt_id = _create_async_task(
            controller, store, "dedup-reconciling"
        )

        # Create additional duplicate attempts for the same step
        dup_ids: list[str] = []
        for _i in range(3):
            dup_ids.append(
                _insert_raw_attempt(
                    store,
                    task_id=task_id,
                    step_id=step_id,
                    attempt_num=2,
                    status="reconciling",
                )
            )

        # Set the original attempt to reconciling too
        store.update_step_attempt(first_attempt_id, status="reconciling")
        store.update_step(step_id, status="reconciling")
        store.update_task_status(task_id, "running")

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        # Exactly one attempt should be recovered (ready or blocked), rest superseded
        all_attempts = store.list_step_attempts(step_id=step_id, limit=100)
        recovered = [a for a in all_attempts if a.status in ("ready", "blocked")]
        superseded = [a for a in all_attempts if a.status == "superseded"]

        assert len(recovered) == 1, (
            f"Expected exactly 1 recovered attempt, got {len(recovered)}: "
            f"{[(a.step_attempt_id, a.status) for a in all_attempts]}"
        )
        assert len(superseded) == 3, f"Expected 3 superseded duplicates, got {len(superseded)}"

    def test_mixed_inflight_statuses_same_step_deduped(self, tmp_path: Path) -> None:
        """Multiple attempts with different inflight statuses for the same step."""
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        task_id, step_id, first_attempt_id = _create_async_task(controller, store, "dedup-mixed")

        # Set first attempt to running
        store.update_step_attempt(first_attempt_id, status="running")
        store.update_step(step_id, status="running")
        store.update_task_status(task_id, "running")

        # Add a dispatching duplicate
        _insert_raw_attempt(
            store,
            task_id=task_id,
            step_id=step_id,
            attempt_num=2,
            status="dispatching",
        )

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        all_attempts = store.list_step_attempts(step_id=step_id, limit=100)
        recovered = [a for a in all_attempts if a.status in ("ready", "blocked")]
        superseded = [a for a in all_attempts if a.status == "superseded"]

        assert len(recovered) == 1
        assert len(superseded) == 1


class TestRecoveryReadyDedup:
    """Phase 2: multiple ready attempts for the same step are deduplicated."""

    def test_multiple_ready_attempts_deduplicated(self, tmp_path: Path) -> None:
        """Only the highest-attempt-number ready attempt survives."""
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        task_id, step_id, _first_attempt_id = _create_async_task(controller, store, "dedup-ready")

        # Create additional ready attempts for the same step
        for i in range(2):
            _insert_raw_attempt(
                store,
                task_id=task_id,
                step_id=step_id,
                attempt_num=i + 2,
                status="ready",
            )

        store.update_task_status(task_id, "queued")

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        all_attempts = store.list_step_attempts(step_id=step_id, limit=100)
        ready = [a for a in all_attempts if a.status == "ready"]
        superseded = [a for a in all_attempts if a.status == "superseded"]

        assert len(ready) == 1, f"Expected 1 ready, got {len(ready)}"
        assert len(superseded) == 2, f"Expected 2 superseded, got {len(superseded)}"
        # The surviving one should have the highest attempt number
        assert ready[0].attempt == 3

    def test_single_ready_attempt_not_touched(self, tmp_path: Path) -> None:
        """A step with only one ready attempt is not modified."""
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        task_id, _step_id, attempt_id = _create_async_task(controller, store, "single-ready")
        store.update_task_status(task_id, "queued")

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        attempt = store.get_step_attempt(attempt_id)
        assert attempt is not None
        assert attempt.status == "ready"
