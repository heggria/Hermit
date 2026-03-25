"""Tests for workspace lifecycle: extend, expire_stale, queuing, release_all_for_task, cleanup."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermit.kernel.authority.workspaces.models import (
    WorkspaceLeaseQueueEntry,
    WorkspaceLeaseRecord,
)
from hermit.kernel.authority.workspaces.service import (
    WorkspaceLeaseConflict,
    WorkspaceLeaseQueued,
    WorkspaceLeaseService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lease(**overrides: object) -> WorkspaceLeaseRecord:
    defaults: dict[str, object] = {
        "lease_id": "lease-1",
        "task_id": "t-1",
        "step_attempt_id": "sa-1",
        "workspace_id": "ws-1",
        "root_path": "/tmp/ws",
        "holder_principal_id": "p-1",
        "mode": "mutable",
        "resource_scope": ["*"],
        "status": "active",
        "expires_at": time.time() + 600,
    }
    defaults.update(overrides)
    return WorkspaceLeaseRecord(**defaults)  # type: ignore[arg-type]


def _make_service(*, default_ttl: int = 300) -> tuple[WorkspaceLeaseService, MagicMock, MagicMock]:
    store = MagicMock()
    artifact_store = MagicMock()
    artifact_store.store_json.return_value = ("uri://env", "hash123")
    store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")
    store.create_workspace_lease.return_value = _make_lease()
    store.list_workspace_leases.return_value = []
    svc = WorkspaceLeaseService(store, artifact_store, default_ttl_seconds=default_ttl)
    return svc, store, artifact_store


# ---------------------------------------------------------------------------
# WorkspaceLeaseQueueEntry model
# ---------------------------------------------------------------------------


class TestWorkspaceLeaseQueueEntry:
    def test_defaults(self) -> None:
        entry = WorkspaceLeaseQueueEntry(
            queue_entry_id="q-1",
            workspace_id="ws-1",
            task_id="t-1",
            step_attempt_id="sa-1",
            root_path="/tmp",
            holder_principal_id="p-1",
            mode="mutable",
        )
        assert entry.status == "pending"
        assert entry.resource_scope == []
        assert entry.ttl_seconds is None
        assert entry.queued_at is None
        assert entry.metadata == {}

    def test_custom_values(self) -> None:
        entry = WorkspaceLeaseQueueEntry(
            queue_entry_id="q-2",
            workspace_id="ws-2",
            task_id="t-2",
            step_attempt_id="sa-2",
            root_path="/tmp/2",
            holder_principal_id="p-2",
            mode="mutable",
            resource_scope=["file:*"],
            ttl_seconds=600,
            queued_at=1000.0,
            status="served",
        )
        assert entry.resource_scope == ["file:*"]
        assert entry.ttl_seconds == 600
        assert entry.queued_at == 1000.0
        assert entry.status == "served"


# ---------------------------------------------------------------------------
# WorkspaceLeaseQueued exception
# ---------------------------------------------------------------------------


class TestWorkspaceLeaseQueued:
    def test_is_subclass_of_conflict(self) -> None:
        assert issubclass(WorkspaceLeaseQueued, WorkspaceLeaseConflict)

    def test_carries_queue_info(self) -> None:
        exc = WorkspaceLeaseQueued("queued", queue_entry_id="q-1", position=3)
        assert exc.queue_entry_id == "q-1"
        assert exc.position == 3
        assert "queued" in str(exc)


# ---------------------------------------------------------------------------
# WorkspaceLeaseService.extend
# ---------------------------------------------------------------------------


class TestWorkspaceLeaseServiceExtend:
    def test_extend_active_lease(self) -> None:
        svc, store, _ = _make_service()
        lease = _make_lease(status="active", expires_at=2000.0)
        store.get_workspace_lease.return_value = lease
        updated_lease = _make_lease(status="active", expires_at=2600.0)
        # First call returns original, second returns updated
        store.get_workspace_lease.side_effect = [lease, updated_lease]

        with patch("hermit.kernel.authority.workspaces.service.time") as mock_time:
            mock_time.time.return_value = 1500.0
            result = svc.extend("lease-1", 600)

        assert result.expires_at == 2600.0
        store.update_workspace_lease.assert_called_once()
        call_kwargs = store.update_workspace_lease.call_args[1]
        assert call_kwargs["expires_at"] == 2600.0  # max(2000, 1500) + 600
        store.append_event.assert_called_once()
        event_kwargs = store.append_event.call_args[1]
        assert event_kwargs["event_type"] == "workspace.lease_extended"

    def test_extend_nonexistent_lease_raises(self) -> None:
        svc, store, _ = _make_service()
        store.get_workspace_lease.return_value = None
        with pytest.raises(RuntimeError, match="not found"):
            svc.extend("missing-lease", 600)

    def test_extend_released_lease_raises(self) -> None:
        svc, store, _ = _make_service()
        store.get_workspace_lease.return_value = _make_lease(status="released")
        with pytest.raises(RuntimeError, match=r"released.*cannot extend"):
            svc.extend("lease-1", 600)

    def test_extend_expired_lease_raises(self) -> None:
        svc, store, _ = _make_service()
        store.get_workspace_lease.return_value = _make_lease(
            status="active", expires_at=time.time() - 100
        )
        with pytest.raises(RuntimeError, match="already expired"):
            svc.extend("lease-1", 600)

    def test_extend_lease_with_no_expires_at(self) -> None:
        svc, store, _ = _make_service()
        lease = _make_lease(status="active", expires_at=None)
        updated_lease = _make_lease(status="active", expires_at=1600.0)
        store.get_workspace_lease.side_effect = [lease, updated_lease]

        with patch("hermit.kernel.authority.workspaces.service.time") as mock_time:
            mock_time.time.return_value = 1000.0
            result = svc.extend("lease-1", 600)

        assert result.expires_at == 1600.0
        call_kwargs = store.update_workspace_lease.call_args[1]
        assert call_kwargs["expires_at"] == 1600.0  # now + 600

    def test_extend_updates_from_now_when_future_expires(self) -> None:
        svc, store, _ = _make_service()
        lease = _make_lease(status="active", expires_at=3000.0)
        updated_lease = _make_lease(status="active", expires_at=3600.0)
        store.get_workspace_lease.side_effect = [lease, updated_lease]

        with patch("hermit.kernel.authority.workspaces.service.time") as mock_time:
            mock_time.time.return_value = 1000.0
            svc.extend("lease-1", 600)

        call_kwargs = store.update_workspace_lease.call_args[1]
        assert call_kwargs["expires_at"] == 3600.0  # max(3000, 1000) + 600

    def test_extend_after_update_not_found_raises(self) -> None:
        svc, store, _ = _make_service()
        lease = _make_lease(status="active", expires_at=2000.0)
        store.get_workspace_lease.side_effect = [lease, None]

        with patch("hermit.kernel.authority.workspaces.service.time") as mock_time:
            mock_time.time.return_value = 1500.0
            with pytest.raises(RuntimeError, match="not found after update"):
                svc.extend("lease-1", 600)


# ---------------------------------------------------------------------------
# WorkspaceLeaseService.expire_stale
# ---------------------------------------------------------------------------


class TestWorkspaceLeaseServiceExpireStale:
    def test_expire_stale_marks_expired(self) -> None:
        svc, store, _ = _make_service()
        stale = _make_lease(lease_id="stale-1", expires_at=time.time() - 100)
        store.list_workspace_leases.return_value = [stale]

        result = svc.expire_stale()

        assert result == ["stale-1"]
        store.update_workspace_lease.assert_called_once()
        call_args = store.update_workspace_lease.call_args
        assert call_args[0][0] == "stale-1"
        assert call_args[1]["status"] == "expired"

    def test_expire_stale_skips_non_expired(self) -> None:
        svc, store, _ = _make_service()
        valid = _make_lease(lease_id="valid-1", expires_at=time.time() + 600)
        store.list_workspace_leases.return_value = [valid]

        result = svc.expire_stale()

        assert result == []
        store.update_workspace_lease.assert_not_called()

    def test_expire_stale_skips_no_expires_at(self) -> None:
        svc, store, _ = _make_service()
        no_expiry = _make_lease(lease_id="no-exp-1", expires_at=None)
        store.list_workspace_leases.return_value = [no_expiry]

        result = svc.expire_stale()

        assert result == []
        store.update_workspace_lease.assert_not_called()

    def test_expire_stale_returns_expired_ids(self) -> None:
        svc, store, _ = _make_service()
        now = time.time()
        stale1 = _make_lease(lease_id="s1", workspace_id="ws-1", expires_at=now - 100)
        stale2 = _make_lease(lease_id="s2", workspace_id="ws-2", expires_at=now - 50)
        valid = _make_lease(lease_id="v1", workspace_id="ws-3", expires_at=now + 600)
        store.list_workspace_leases.return_value = [stale1, stale2, valid]

        result = svc.expire_stale()

        assert set(result) == {"s1", "s2"}

    def test_expire_stale_empty_list(self) -> None:
        svc, store, _ = _make_service()
        store.list_workspace_leases.return_value = []

        result = svc.expire_stale()

        assert result == []

    def test_expire_stale_emits_events(self) -> None:
        svc, store, _ = _make_service()
        stale = _make_lease(lease_id="stale-1", expires_at=time.time() - 10)
        store.list_workspace_leases.return_value = [stale]

        svc.expire_stale()

        assert store.append_event.called
        event_kwargs = store.append_event.call_args[1]
        assert event_kwargs["event_type"] == "workspace.auto_expired"


# ---------------------------------------------------------------------------
# WorkspaceLeaseService.release_all_for_task
# ---------------------------------------------------------------------------


class TestWorkspaceLeaseServiceReleaseAllForTask:
    def test_release_all_active_leases_for_task(self) -> None:
        svc, store, _ = _make_service()
        lease1 = _make_lease(lease_id="l1", workspace_id="ws-1")
        lease2 = _make_lease(lease_id="l2", workspace_id="ws-2")
        store.list_workspace_leases.return_value = [lease1, lease2]

        result = svc.release_all_for_task("t-1")

        assert result == ["l1", "l2"]
        assert store.update_workspace_lease.call_count == 2

    def test_release_all_returns_released_ids(self) -> None:
        svc, store, _ = _make_service()
        lease1 = _make_lease(lease_id="l1")
        store.list_workspace_leases.return_value = [lease1]

        result = svc.release_all_for_task("t-1")

        assert result == ["l1"]

    def test_release_all_emits_events(self) -> None:
        svc, store, _ = _make_service()
        lease = _make_lease(lease_id="l1")
        store.list_workspace_leases.return_value = [lease]

        svc.release_all_for_task("t-1")

        assert store.append_event.called
        event_kwargs = store.append_event.call_args[1]
        assert event_kwargs["event_type"] == "workspace.auto_released"
        assert event_kwargs["payload"]["reason"] == "task_terminal"

    def test_release_all_empty(self) -> None:
        svc, store, _ = _make_service()
        store.list_workspace_leases.return_value = []

        result = svc.release_all_for_task("t-1")

        assert result == []
        store.update_workspace_lease.assert_not_called()

    def test_release_all_queries_correct_task(self) -> None:
        svc, store, _ = _make_service()
        store.list_workspace_leases.return_value = []

        svc.release_all_for_task("t-99")

        store.list_workspace_leases.assert_called_once_with(
            task_id="t-99", status="active", limit=1000
        )


# ---------------------------------------------------------------------------
# WorkspaceLeaseService.queuing
# ---------------------------------------------------------------------------


class TestWorkspaceLeaseServiceQueuing:
    def test_mutable_conflict_queues_request(self) -> None:
        svc, store, _ = _make_service()
        existing = _make_lease(lease_id="existing", mode="mutable", expires_at=time.time() + 600)
        store.list_workspace_leases.return_value = [existing]

        with pytest.raises(WorkspaceLeaseQueued) as exc_info:
            svc.acquire(
                task_id="t-2",
                step_attempt_id="sa-2",
                workspace_id="ws-1",
                root_path="/tmp",
                holder_principal_id="p-2",
                mode="mutable",
                resource_scope=["*"],
            )

        exc = exc_info.value
        assert exc.queue_entry_id is not None
        assert exc.position == 1
        assert svc.queue_position("ws-1") == 1

    def test_queue_position_empty(self) -> None:
        svc, _, _ = _make_service()
        assert svc.queue_position("ws-nonexistent") == 0

    def test_queue_position_after_multiple_queues(self) -> None:
        svc, store, _ = _make_service()
        existing = _make_lease(lease_id="existing", mode="mutable", expires_at=time.time() + 600)
        store.list_workspace_leases.return_value = [existing]

        for i in range(3):
            with pytest.raises(WorkspaceLeaseQueued):
                svc.acquire(
                    task_id=f"t-{i}",
                    step_attempt_id=f"sa-{i}",
                    workspace_id="ws-1",
                    root_path="/tmp",
                    holder_principal_id=f"p-{i}",
                    mode="mutable",
                    resource_scope=["*"],
                )

        assert svc.queue_position("ws-1") == 3

    def test_queue_processed_after_release(self) -> None:
        svc, store, _ = _make_service()
        existing = _make_lease(
            lease_id="existing", mode="mutable", workspace_id="ws-1", expires_at=time.time() + 600
        )
        # First call: list returns existing lease (for acquire conflict check)
        # Second call: list returns existing lease (for release -> _process_queue check)
        # Third call: no active mutable (after release)
        store.list_workspace_leases.side_effect = [
            [existing],  # acquire conflict check
            [],  # _process_queue after release -> no active mutable
            [],  # _process_queue inner acquire conflict check
        ]
        new_lease = _make_lease(lease_id="new-lease", workspace_id="ws-1")
        store.create_workspace_lease.return_value = new_lease

        with pytest.raises(WorkspaceLeaseQueued):
            svc.acquire(
                task_id="t-queued",
                step_attempt_id="sa-queued",
                workspace_id="ws-1",
                root_path="/tmp",
                holder_principal_id="p-queued",
                mode="mutable",
                resource_scope=["*"],
            )

        assert svc.queue_position("ws-1") == 1

        # Now set up release: get_workspace_lease returns the existing lease
        store.get_workspace_lease.return_value = existing

        svc.release("existing")

        # Queue should be processed; entry marked as served
        assert svc.queue_position("ws-1") == 0

    def test_queue_fifo_order(self) -> None:
        svc, store, _ = _make_service()
        existing = _make_lease(lease_id="existing", mode="mutable", expires_at=time.time() + 600)
        store.list_workspace_leases.return_value = [existing]

        entry_ids: list[str] = []
        for i in range(3):
            with pytest.raises(WorkspaceLeaseQueued) as exc_info:
                svc.acquire(
                    task_id=f"t-{i}",
                    step_attempt_id=f"sa-{i}",
                    workspace_id="ws-1",
                    root_path="/tmp",
                    holder_principal_id=f"p-{i}",
                    mode="mutable",
                    resource_scope=["*"],
                )
            entry_ids.append(exc_info.value.queue_entry_id)

        # Verify FIFO order in internal queue
        queue = svc._queue["ws-1"]
        assert [e.queue_entry_id for e in queue] == entry_ids
        assert queue[0].task_id == "t-0"
        assert queue[1].task_id == "t-1"
        assert queue[2].task_id == "t-2"

    def test_readonly_not_queued(self) -> None:
        svc, store, _ = _make_service()
        # For readonly, list_workspace_leases should not be called
        store.list_workspace_leases.return_value = []

        result = svc.acquire(
            task_id="t-1",
            step_attempt_id="sa-1",
            workspace_id="ws-1",
            root_path="/tmp",
            holder_principal_id="p-1",
            mode="readonly",
            resource_scope=["*"],
        )

        assert result.lease_id == "lease-1"
        store.list_workspace_leases.assert_not_called()
        assert svc.queue_position("ws-1") == 0

    def test_queuing_emits_event(self) -> None:
        svc, store, _ = _make_service()
        existing = _make_lease(lease_id="existing", mode="mutable", expires_at=time.time() + 600)
        store.list_workspace_leases.return_value = [existing]

        with pytest.raises(WorkspaceLeaseQueued):
            svc.acquire(
                task_id="t-2",
                step_attempt_id="sa-2",
                workspace_id="ws-1",
                root_path="/tmp",
                holder_principal_id="p-2",
                mode="mutable",
                resource_scope=["*"],
            )

        store.append_event.assert_called_once()
        event_kwargs = store.append_event.call_args[1]
        assert event_kwargs["event_type"] == "workspace.lease_queued"
        assert event_kwargs["payload"]["blocked_by_lease_id"] == "existing"


# ---------------------------------------------------------------------------
# WorkspaceLeaseService.release with queue processing
# ---------------------------------------------------------------------------


class TestWorkspaceLeaseServiceReleaseWithQueue:
    def test_release_triggers_queue_processing(self) -> None:
        svc, store, _ = _make_service()
        lease = _make_lease(lease_id="l1", workspace_id="ws-1")
        store.get_workspace_lease.return_value = lease
        # After release, no active mutable leases
        store.list_workspace_leases.return_value = []

        svc.release("l1")

        store.update_workspace_lease.assert_called_once_with(
            "l1",
            status="released",
            released_at=store.update_workspace_lease.call_args[1]["released_at"],
        )

    def test_release_without_lease_in_store(self) -> None:
        svc, store, _ = _make_service()
        store.get_workspace_lease.return_value = None

        svc.release("missing-lease")

        store.update_workspace_lease.assert_called_once()

    def test_process_queue_skips_when_active_mutable_still_exists(self) -> None:
        """Queue processing returns None if a non-expired mutable lease remains."""
        svc, store, _ = _make_service()
        # Set up: queue an entry
        existing = _make_lease(lease_id="existing", mode="mutable", expires_at=time.time() + 600)
        store.list_workspace_leases.return_value = [existing]

        with pytest.raises(WorkspaceLeaseQueued):
            svc.acquire(
                task_id="t-queued",
                step_attempt_id="sa-queued",
                workspace_id="ws-1",
                root_path="/tmp",
                holder_principal_id="p-queued",
                mode="mutable",
                resource_scope=["*"],
            )

        assert svc.queue_position("ws-1") == 1

        # Now _process_queue with still-active mutable lease
        store.list_workspace_leases.return_value = [existing]
        result = svc._process_queue("ws-1")
        assert result is None
        # Queue entry should still be pending
        assert svc.queue_position("ws-1") == 1

    def test_process_queue_skips_expired_lease_in_active_check(self) -> None:
        """An expired lease in the active list should be skipped during queue check."""
        svc, store, _ = _make_service()
        # Queue an entry
        existing = _make_lease(lease_id="existing", mode="mutable", expires_at=time.time() + 600)
        store.list_workspace_leases.return_value = [existing]

        with pytest.raises(WorkspaceLeaseQueued):
            svc.acquire(
                task_id="t-queued",
                step_attempt_id="sa-queued",
                workspace_id="ws-1",
                root_path="/tmp",
                holder_principal_id="p-queued",
                mode="mutable",
                resource_scope=["*"],
            )

        # _process_queue with expired mutable lease should proceed
        expired = _make_lease(lease_id="expired", mode="mutable", expires_at=time.time() - 10)
        new_lease = _make_lease(lease_id="dequeued", workspace_id="ws-1")
        store.create_workspace_lease.return_value = new_lease
        # First list call: for _process_queue active check (expired mutable)
        # Second list call: for acquire expire pass (expired gets cleaned)
        # Third list call: for acquire re-read after expiry (empty — no conflicts)
        store.list_workspace_leases.side_effect = [
            [expired],  # _process_queue check
            [expired],  # inner acquire expire pass
            [],  # inner acquire re-read after expiry
        ]
        result = svc._process_queue("ws-1")
        assert result is not None
        assert result.lease_id == "dequeued"

    def test_process_queue_re_queues_on_conflict(self) -> None:
        """If acquire re-raises WorkspaceLeaseQueued, entry goes back to pending."""
        svc, store, _ = _make_service()
        # Queue an entry
        existing = _make_lease(lease_id="existing", mode="mutable", expires_at=time.time() + 600)
        store.list_workspace_leases.return_value = [existing]

        with pytest.raises(WorkspaceLeaseQueued):
            svc.acquire(
                task_id="t-queued",
                step_attempt_id="sa-queued",
                workspace_id="ws-1",
                root_path="/tmp",
                holder_principal_id="p-queued",
                mode="mutable",
                resource_scope=["*"],
            )

        # _process_queue: no active mutable in the check, but acquire finds one
        another_mutable = _make_lease(
            lease_id="sneaky", mode="mutable", expires_at=time.time() + 600
        )
        store.list_workspace_leases.side_effect = [
            [],  # _process_queue active check: looks clear
            [another_mutable],  # inner acquire conflict check: oops, conflict!
        ]
        result = svc._process_queue("ws-1")
        assert result is None
        # Entry should be back in pending (position 1 for original, +1 for re-queue in acquire)
        # The original entry is re-set to pending, plus the acquire creates a NEW queue entry
        entries = svc._queue.get("ws-1", [])
        pending = [e for e in entries if e.status == "pending"]
        assert len(pending) >= 1


# ---------------------------------------------------------------------------
# TaskController cleanup on terminal
# ---------------------------------------------------------------------------


class TestWorkspaceCleanupOnTaskTerminal:
    def _make_controller_with_mock(self) -> tuple[object, MagicMock, MagicMock]:
        from hermit.kernel.task.services.controller import TaskController

        store = MagicMock()
        ws_service = MagicMock()
        ws_service.release_all_for_task.return_value = ["l1", "l2"]
        controller = TaskController(store, workspace_lease_service=ws_service)
        return controller, store, ws_service

    def _make_ctx(self) -> object:
        return SimpleNamespace(
            conversation_id="conv-1",
            task_id="t-1",
            step_id="s-1",
            step_attempt_id="sa-1",
            source_channel="chat",
            policy_profile="default",
            workspace_root="/tmp",
            ingress_metadata={},
            actor_principal_id="user",
        )

    def test_finalize_releases_leases_on_completion(self) -> None:
        controller, store, ws_service = self._make_controller_with_mock()
        ctx = self._make_ctx()
        store.try_finalize_step_attempt.return_value = True
        store.has_non_terminal_steps.return_value = False
        store.activate_waiting_dependents.return_value = []

        controller.finalize_result(ctx, status="completed")

        ws_service.release_all_for_task.assert_called_once_with("t-1")

    def test_finalize_releases_leases_on_failure(self) -> None:
        controller, store, ws_service = self._make_controller_with_mock()
        ctx = self._make_ctx()
        store.try_finalize_step_attempt.return_value = True
        store.has_non_terminal_steps.return_value = False
        step = SimpleNamespace(attempt=1, max_attempts=1)
        store.get_step.return_value = step
        store.activate_waiting_dependents.return_value = []

        controller.finalize_result(ctx, status="failed")

        ws_service.release_all_for_task.assert_called_once_with("t-1")

    def test_finalize_no_release_on_running(self) -> None:
        controller, store, ws_service = self._make_controller_with_mock()
        ctx = self._make_ctx()
        store.try_finalize_step_attempt.return_value = True
        store.has_non_terminal_steps.return_value = True
        store.activate_waiting_dependents.return_value = []

        controller.finalize_result(ctx, status="completed")

        # Task status is "running" (non-terminal), so no cleanup
        ws_service.release_all_for_task.assert_not_called()

    def test_finalize_no_op_without_service(self) -> None:
        from hermit.kernel.task.services.controller import TaskController

        store = MagicMock()
        controller = TaskController(store)
        ctx = self._make_ctx()
        store.try_finalize_step_attempt.return_value = True
        store.has_non_terminal_steps.return_value = False
        store.activate_waiting_dependents.return_value = []

        # Should not raise
        controller.finalize_result(ctx, status="completed")

    def test_finalize_cleanup_error_swallowed(self) -> None:
        controller, store, ws_service = self._make_controller_with_mock()
        ws_service.release_all_for_task.side_effect = RuntimeError("db error")
        ctx = self._make_ctx()
        store.try_finalize_step_attempt.return_value = True
        store.has_non_terminal_steps.return_value = False
        store.activate_waiting_dependents.return_value = []

        # Should not raise despite workspace service error
        controller.finalize_result(ctx, status="completed")

        store.update_task_status.assert_called()

    def test_finalize_cancelled_releases_leases(self) -> None:
        controller, store, ws_service = self._make_controller_with_mock()
        ctx = self._make_ctx()
        store.try_finalize_step_attempt.return_value = True
        store.has_non_terminal_steps.return_value = False
        store.activate_waiting_dependents.return_value = []

        controller.finalize_result(ctx, status="cancelled")

        ws_service.release_all_for_task.assert_called_once_with("t-1")

    def test_finalize_already_finalized_skips(self) -> None:
        controller, store, ws_service = self._make_controller_with_mock()
        ctx = self._make_ctx()
        store.try_finalize_step_attempt.return_value = False

        controller.finalize_result(ctx, status="completed")

        ws_service.release_all_for_task.assert_not_called()

    def test_finalize_emits_cleanup_event(self) -> None:
        controller, store, ws_service = self._make_controller_with_mock()
        ws_service.release_all_for_task.return_value = ["l1"]
        ctx = self._make_ctx()
        store.try_finalize_step_attempt.return_value = True
        store.has_non_terminal_steps.return_value = False
        store.activate_waiting_dependents.return_value = []

        controller.finalize_result(ctx, status="completed")

        # Find the workspace.task_terminal_cleanup event
        cleanup_calls = [
            c
            for c in store.append_event.call_args_list
            if c[1].get("event_type") == "workspace.task_terminal_cleanup"
        ]
        assert len(cleanup_calls) == 1
        payload = cleanup_calls[0][1]["payload"]
        assert payload["released_lease_ids"] == ["l1"]
        assert payload["task_status"] == "completed"
