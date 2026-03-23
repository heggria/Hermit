"""E2E tests for dispatch recovery of in-flight step attempts after serve restart."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.kernel.execution.coordination.dispatch import KernelDispatchService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController


def _make_dispatch_service(store: KernelStore, controller: TaskController) -> KernelDispatchService:
    """Build a KernelDispatchService with a minimal mock runner."""
    runner = SimpleNamespace(
        task_controller=SimpleNamespace(store=store),
        process_claimed_attempt=lambda step_attempt_id: None,
    )
    return KernelDispatchService(runner, worker_count=1)


def _create_task_with_attempt(
    controller: TaskController,
    store: KernelStore,
    *,
    conversation_id: str,
    attempt_status: str,
    has_capability_grant: bool = False,
    dispatch_mode: str = "async",
) -> tuple[str, str, str]:
    """Create a task with one step attempt in the given status.

    Returns (task_id, step_id, step_attempt_id).
    """
    ctx = controller.start_task(
        conversation_id=conversation_id,
        goal=f"test recovery from {attempt_status}",
        source_channel="chat",
        kind="respond",
    )
    context: dict[str, Any] = {
        "ingress_metadata": {"dispatch_mode": dispatch_mode},
    }
    update_kwargs: dict[str, Any] = {
        "status": attempt_status,
        "context": context,
    }
    if has_capability_grant:
        update_kwargs["capability_grant_id"] = f"grant-{conversation_id}"

    store.update_step_attempt(ctx.step_attempt_id, **update_kwargs)
    store.update_step(ctx.step_id, status=attempt_status)
    store.update_task_status(ctx.task_id, "running")

    return ctx.task_id, ctx.step_id, ctx.step_attempt_id


class TestDispatchRecoveryInflightStatuses:
    """Phase 1: all intermediate statuses are recovered on restart."""

    @pytest.mark.parametrize(
        "inflight_status",
        ["running", "dispatching", "reconciling", "observing", "contracting", "preflighting"],
    )
    def test_inflight_without_grant_recovers_to_ready(
        self, tmp_path: Path, inflight_status: str
    ) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        task_id, step_id, attempt_id = _create_task_with_attempt(
            controller,
            store,
            conversation_id=f"recover-{inflight_status}-no-grant",
            attempt_status=inflight_status,
            has_capability_grant=False,
        )

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        attempt = store.get_step_attempt(attempt_id)
        step = store.get_step(step_id)
        task = store.get_task(task_id)

        assert attempt is not None
        assert attempt.status == "ready"
        assert attempt.context["recovered_after_interrupt"] is True
        assert attempt.context["original_status_at_interrupt"] == inflight_status
        assert attempt.context["reentry_boundary"] == "policy_reentry"
        assert attempt.context["reentry_reason"] == "worker_interrupted"
        assert attempt.status_reason == "worker_interrupted_requeued"

        assert step is not None
        assert step.status == "ready"

        assert task is not None
        assert task.status == "queued"

    @pytest.mark.parametrize(
        "inflight_status",
        ["running", "dispatching", "reconciling", "observing"],
    )
    def test_inflight_with_grant_recovers_to_blocked(
        self, tmp_path: Path, inflight_status: str
    ) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        task_id, step_id, attempt_id = _create_task_with_attempt(
            controller,
            store,
            conversation_id=f"recover-{inflight_status}-with-grant",
            attempt_status=inflight_status,
            has_capability_grant=True,
        )

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        attempt = store.get_step_attempt(attempt_id)
        step = store.get_step(step_id)
        task = store.get_task(task_id)

        assert attempt is not None
        assert attempt.status == "blocked"
        assert attempt.context["recovered_after_interrupt"] is True
        assert attempt.context["original_status_at_interrupt"] == inflight_status
        assert attempt.context["recovery_required"] is True
        assert attempt.context["reentry_boundary"] == "observation_resolution"
        assert attempt.status_reason == "worker_interrupted_recovery_required"

        assert step is not None
        assert step.status == "blocked"

        assert task is not None
        assert task.status == "blocked"

    def test_sync_attempts_are_cancelled_as_orphaned(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        task_id, step_id, attempt_id = _create_task_with_attempt(
            controller,
            store,
            conversation_id="sync-running",
            attempt_status="running",
            dispatch_mode="sync",
        )

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        attempt = store.get_step_attempt(attempt_id)
        step = store.get_step(step_id)
        task = store.get_task(task_id)

        assert attempt is not None
        assert attempt.status == "cancelled"
        assert attempt.context["recovered_after_interrupt"] is True
        assert attempt.context["original_status_at_interrupt"] == "running"
        assert attempt.context["recovery_action"] == "cancelled_orphaned_sync"
        assert attempt.status_reason == "worker_interrupted_sync_orphaned"

        assert step is not None
        assert step.status == "cancelled"

        assert task is not None
        assert task.status == "cancelled"

    def test_no_dispatch_mode_attempts_are_cancelled_as_orphaned(self, tmp_path: Path) -> None:
        """Attempts without any dispatch_mode (e.g. feishu adapter sync path)."""
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        _task_id, _step_id, attempt_id = _create_task_with_attempt(
            controller,
            store,
            conversation_id="no-dispatch-mode",
            attempt_status="reconciling",
            has_capability_grant=True,
            dispatch_mode="",
        )
        # Remove dispatch_mode entirely to simulate feishu adapter path
        attempt = store.get_step_attempt(attempt_id)
        context = dict(attempt.context or {})
        ingress = dict(context.get("ingress_metadata", {}) or {})
        ingress.pop("dispatch_mode", None)
        context["ingress_metadata"] = ingress
        store.update_step_attempt(attempt_id, context=context)

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        attempt = store.get_step_attempt(attempt_id)
        assert attempt is not None
        assert attempt.status == "cancelled"
        assert attempt.context["original_status_at_interrupt"] == "reconciling"
        assert attempt.context["recovery_action"] == "cancelled_orphaned_sync"

    def test_terminal_statuses_are_not_recovered(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        for terminal in ("succeeded", "completed", "skipped", "failed"):
            _task_id, _step_id, _attempt_id = _create_task_with_attempt(
                controller,
                store,
                conversation_id=f"terminal-{terminal}",
                attempt_status=terminal,
            )

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        for terminal in ("succeeded", "completed", "skipped", "failed"):
            attempts = store.list_step_attempts(status=terminal, limit=100)
            for a in attempts:
                assert "recovered_after_interrupt" not in (a.context or {})


class TestDispatchRecoveryReadyRepair:
    """Phase 2: ready attempts with stale parent task status get repaired."""

    def test_ready_attempt_with_blocked_task_gets_repaired(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        ctx = controller.start_task(
            conversation_id="ready-blocked-task",
            goal="test ready repair",
            source_channel="chat",
            kind="respond",
        )
        store.update_step_attempt(
            ctx.step_attempt_id,
            status="ready",
            context={"ingress_metadata": {"dispatch_mode": "async"}},
        )
        store.update_step(ctx.step_id, status="ready")
        store.update_task_status(ctx.task_id, "blocked")

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "queued"

    def test_ready_attempt_with_queued_task_not_modified(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        ctx = controller.start_task(
            conversation_id="ready-queued-task",
            goal="test no-op repair",
            source_channel="chat",
            kind="respond",
        )
        store.update_step_attempt(
            ctx.step_attempt_id,
            status="ready",
            context={"ingress_metadata": {"dispatch_mode": "async"}},
        )
        store.update_step(ctx.step_id, status="ready")
        store.update_task_status(ctx.task_id, "queued")

        task_before = store.get_task(ctx.task_id)
        _ = task_before.updated_at if task_before else 0

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "queued"

    def test_ready_sync_attempt_not_repaired(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        ctx = controller.start_task(
            conversation_id="ready-sync",
            goal="sync should not be repaired",
            source_channel="chat",
            kind="respond",
        )
        store.update_step_attempt(
            ctx.step_attempt_id,
            status="ready",
            context={"ingress_metadata": {"dispatch_mode": "sync"}},
        )
        store.update_step(ctx.step_id, status="ready")
        store.update_task_status(ctx.task_id, "blocked")

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "blocked"


class TestDispatchRecoveryMultipleAttempts:
    """Recovery handles multiple concurrent in-flight attempts correctly."""

    def test_mixed_statuses_all_recovered(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)

        scenarios: list[dict[str, Any]] = [
            {"status": "running", "grant": False, "expect_status": "ready"},
            {"status": "dispatching", "grant": True, "expect_status": "blocked"},
            {"status": "reconciling", "grant": True, "expect_status": "blocked"},
            {"status": "contracting", "grant": False, "expect_status": "ready"},
            {"status": "preflighting", "grant": False, "expect_status": "ready"},
            {"status": "observing", "grant": True, "expect_status": "blocked"},
        ]

        attempt_ids: list[tuple[str, dict[str, Any]]] = []
        for i, scenario in enumerate(scenarios):
            _task_id, _step_id, attempt_id = _create_task_with_attempt(
                controller,
                store,
                conversation_id=f"mixed-{i}-{scenario['status']}",
                attempt_status=scenario["status"],
                has_capability_grant=scenario["grant"],
            )
            attempt_ids.append((attempt_id, scenario))

        service = _make_dispatch_service(store, controller)
        service.recover_interrupted_attempts()

        for attempt_id, scenario in attempt_ids:
            attempt = store.get_step_attempt(attempt_id)
            assert attempt is not None, f"attempt {attempt_id} not found"
            assert attempt.status == scenario["expect_status"], (
                f"attempt from {scenario['status']} (grant={scenario['grant']}): "
                f"expected {scenario['expect_status']}, got {attempt.status}"
            )
            assert attempt.context["original_status_at_interrupt"] == scenario["status"]
            assert attempt.context["recovered_after_interrupt"] is True
