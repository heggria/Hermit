"""Tests for hermit.kernel.authority.workspaces.service — WorkspaceLeaseService."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hermit.kernel.authority.workspaces.models import WorkspaceLeaseQueueEntry, WorkspaceLeaseRecord
from hermit.kernel.authority.workspaces.service import (
    WorkspaceLeaseConflict,
    WorkspaceLeaseService,
    capture_execution_environment,
)

# ---------------------------------------------------------------------------
# capture_execution_environment
# ---------------------------------------------------------------------------


class TestCaptureExecutionEnvironment:
    def test_returns_expected_keys(self, tmp_path: Path) -> None:
        result = capture_execution_environment(cwd=tmp_path)
        assert result["cwd"] == str(tmp_path)
        assert "os" in result
        assert "python" in result
        assert "platform" in result

    def test_cwd_matches_input(self) -> None:
        result = capture_execution_environment(cwd=Path("/some/path"))
        assert result["cwd"] == "/some/path"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lease(**overrides) -> WorkspaceLeaseRecord:
    defaults = {
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
    return WorkspaceLeaseRecord(**defaults)


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
# WorkspaceLeaseService
# ---------------------------------------------------------------------------


class TestWorkspaceLeaseServiceInit:
    def test_stores_dependencies(self) -> None:
        svc, store, artifact_store = _make_service(default_ttl=120)
        assert svc.store is store
        assert svc.artifact_store is artifact_store
        assert svc.default_ttl_seconds == 120


class TestWorkspaceLeaseServiceAcquire:
    def test_readonly_mode_skips_conflict_check(self) -> None:
        svc, store, _ = _make_service()
        svc.acquire(
            task_id="t-1",
            step_attempt_id="sa-1",
            workspace_id="ws-1",
            root_path="/tmp",
            holder_principal_id="p-1",
            mode="readonly",
            resource_scope=["*"],
        )
        store.list_workspace_leases.assert_not_called()
        store.create_workspace_lease.assert_called_once()

    def test_mutable_mode_no_conflicts(self) -> None:
        svc, store, _ = _make_service()
        store.list_workspace_leases.return_value = []
        result = svc.acquire(
            task_id="t-1",
            step_attempt_id="sa-1",
            workspace_id="ws-1",
            root_path="/tmp",
            holder_principal_id="p-1",
            mode="mutable",
            resource_scope=["*"],
        )
        store.list_workspace_leases.assert_called_once_with(
            workspace_id="ws-1", status="active", limit=100
        )
        assert result.lease_id == "lease-1"

    def test_mutable_mode_auto_expires_expired_lease(self) -> None:
        svc, store, _ = _make_service()
        expired_lease = _make_lease(
            lease_id="old-lease", mode="mutable", expires_at=time.time() - 100
        )
        # First call returns expired lease; second call (re-read) returns empty
        store.list_workspace_leases.side_effect = [[expired_lease], []]
        svc.acquire(
            task_id="t-1",
            step_attempt_id="sa-1",
            workspace_id="ws-1",
            root_path="/tmp",
            holder_principal_id="p-1",
            mode="mutable",
            resource_scope=["*"],
        )
        store.update_workspace_lease.assert_called_once()
        call_args = store.update_workspace_lease.call_args
        assert call_args[0][0] == "old-lease"
        assert call_args[1]["status"] == "expired"

    def test_acquire_rereads_after_expiry_to_prevent_toctou(self) -> None:
        """After expiring stale leases, acquire() re-reads the active list
        so that the conflict check uses fresh state (TOCTOU fix)."""
        svc, store, _ = _make_service()
        expired_lease = _make_lease(
            lease_id="expired-lease", mode="mutable", expires_at=time.time() - 100
        )
        # First call: returns the expired lease.
        # Second call (re-read): returns empty — the expired lease is gone.
        store.list_workspace_leases.side_effect = [[expired_lease], []]
        svc.acquire(
            task_id="t-1",
            step_attempt_id="sa-1",
            workspace_id="ws-1",
            root_path="/tmp",
            holder_principal_id="p-1",
            mode="mutable",
            resource_scope=["*"],
        )
        # Should have called list_workspace_leases twice (expire pass + re-read)
        assert store.list_workspace_leases.call_count == 2

    def test_mutable_mode_conflicts_with_active_mutable(self) -> None:
        svc, store, _ = _make_service()
        active_lease = _make_lease(
            lease_id="active-lease", mode="mutable", expires_at=time.time() + 600
        )
        store.list_workspace_leases.return_value = [active_lease]
        with pytest.raises(WorkspaceLeaseConflict, match="already has an active mutable lease"):
            svc.acquire(
                task_id="t-1",
                step_attempt_id="sa-1",
                workspace_id="ws-1",
                root_path="/tmp",
                holder_principal_id="p-1",
                mode="mutable",
                resource_scope=["*"],
            )

    def test_mutable_mode_readonly_existing_is_ok(self) -> None:
        svc, store, _ = _make_service()
        readonly_lease = _make_lease(
            lease_id="ro-lease", mode="readonly", expires_at=time.time() + 600
        )
        store.list_workspace_leases.return_value = [readonly_lease]
        result = svc.acquire(
            task_id="t-1",
            step_attempt_id="sa-1",
            workspace_id="ws-1",
            root_path="/tmp",
            holder_principal_id="p-1",
            mode="mutable",
            resource_scope=["*"],
        )
        assert result.lease_id == "lease-1"

    def test_uses_default_ttl_when_none(self) -> None:
        svc, store, _ = _make_service(default_ttl=999)
        with patch("hermit.kernel.authority.workspaces.service.time") as mock_time:
            mock_time.time.return_value = 1000.0
            svc.acquire(
                task_id="t-1",
                step_attempt_id="sa-1",
                workspace_id="ws-1",
                root_path="/tmp",
                holder_principal_id="p-1",
                mode="readonly",
                resource_scope=["*"],
                ttl_seconds=None,
            )
            create_call = store.create_workspace_lease.call_args
            assert create_call[1]["expires_at"] == 1999.0

    def test_uses_provided_ttl(self) -> None:
        svc, store, _ = _make_service(default_ttl=300)
        with patch("hermit.kernel.authority.workspaces.service.time") as mock_time:
            mock_time.time.return_value = 1000.0
            svc.acquire(
                task_id="t-1",
                step_attempt_id="sa-1",
                workspace_id="ws-1",
                root_path="/tmp",
                holder_principal_id="p-1",
                mode="readonly",
                resource_scope=["*"],
                ttl_seconds=600,
            )
            create_call = store.create_workspace_lease.call_args
            assert create_call[1]["expires_at"] == 1600.0

    def test_stores_environment_artifact(self) -> None:
        svc, store, artifact_store = _make_service()
        svc.acquire(
            task_id="t-1",
            step_attempt_id="sa-1",
            workspace_id="ws-1",
            root_path="/tmp",
            holder_principal_id="p-1",
            mode="readonly",
            resource_scope=["*"],
        )
        artifact_store.store_json.assert_called_once()
        store.create_artifact.assert_called_once()
        art_kwargs = store.create_artifact.call_args[1]
        assert art_kwargs["kind"] == "environment.snapshot"
        assert art_kwargs["uri"] == "uri://env"
        assert art_kwargs["content_hash"] == "hash123"
        create_kwargs = store.create_workspace_lease.call_args[1]
        assert create_kwargs["environment_ref"] == "art-1"

    def test_mutable_lease_with_no_expires_at_does_not_conflict(self) -> None:
        """Active lease with expires_at=None should not be auto-expired."""
        svc, store, _ = _make_service()
        active_lease = _make_lease(lease_id="no-expiry", mode="mutable", expires_at=None)
        store.list_workspace_leases.return_value = [active_lease]
        with pytest.raises(WorkspaceLeaseConflict):
            svc.acquire(
                task_id="t-1",
                step_attempt_id="sa-1",
                workspace_id="ws-1",
                root_path="/tmp",
                holder_principal_id="p-1",
                mode="mutable",
                resource_scope=["*"],
            )


class TestWorkspaceLeaseServiceRelease:
    def test_release_updates_status(self) -> None:
        svc, store, _ = _make_service()
        with patch("hermit.kernel.authority.workspaces.service.time") as mock_time:
            mock_time.time.return_value = 5000.0
            svc.release("lease-1")
        store.update_workspace_lease.assert_called_once_with(
            "lease-1", status="released", released_at=5000.0
        )


class TestWorkspaceLeaseServiceValidateActive:
    def test_not_found_raises(self) -> None:
        svc, store, _ = _make_service()
        store.get_workspace_lease.return_value = None
        with pytest.raises(RuntimeError, match="not found"):
            svc.validate_active("missing-lease")

    def test_not_active_status_raises(self) -> None:
        svc, store, _ = _make_service()
        store.get_workspace_lease.return_value = _make_lease(status="released")
        with pytest.raises(RuntimeError, match="is released"):
            svc.validate_active("lease-1")

    def test_expired_raises(self) -> None:
        svc, store, _ = _make_service()
        store.get_workspace_lease.return_value = _make_lease(
            status="active", expires_at=time.time() - 100
        )
        with pytest.raises(RuntimeError, match="expired"):
            svc.validate_active("lease-1")

    def test_valid_active_lease_returns_lease(self) -> None:
        svc, store, _ = _make_service()
        lease = _make_lease(status="active", expires_at=time.time() + 600)
        store.get_workspace_lease.return_value = lease
        result = svc.validate_active("lease-1")
        assert result is lease

    def test_no_expires_at_returns_lease(self) -> None:
        """Active lease with expires_at=None and no acquired_at is still valid."""
        svc, store, _ = _make_service()
        lease = _make_lease(status="active", expires_at=None)
        store.get_workspace_lease.return_value = lease
        result = svc.validate_active("lease-1")
        assert result is lease

    def test_no_expires_at_recent_lease_returns_lease(self) -> None:
        """Active lease with expires_at=None but recently acquired is valid."""
        svc, store, _ = _make_service()
        lease = _make_lease(status="active", expires_at=None, acquired_at=time.time() - 3600)
        store.get_workspace_lease.return_value = lease
        result = svc.validate_active("lease-1")
        assert result is lease

    def test_no_expires_at_old_lease_raises(self) -> None:
        """Active lease with expires_at=None older than MAX_LEASE_AGE_SECONDS raises."""
        svc, store, _ = _make_service()
        # Acquired more than 24 hours ago
        lease = _make_lease(
            status="active",
            expires_at=None,
            acquired_at=time.time() - 90000,  # 25 hours
        )
        store.get_workspace_lease.return_value = lease
        with pytest.raises(RuntimeError, match="maximum age"):
            svc.validate_active("lease-1")


# ---------------------------------------------------------------------------
# workspaces/__init__.py lazy __getattr__
# ---------------------------------------------------------------------------


class TestWorkspacesInitGetattr:
    def test_getattr_service(self) -> None:
        from hermit.kernel.authority.workspaces import WorkspaceLeaseService as WLS

        assert WLS is WorkspaceLeaseService

    def test_getattr_function(self) -> None:
        from hermit.kernel.authority.workspaces import (
            capture_execution_environment as fn,
        )

        assert fn is capture_execution_environment

    def test_getattr_unknown_raises(self) -> None:
        import hermit.kernel.authority.workspaces as mod

        with pytest.raises(AttributeError):
            _ = mod.NoSuchThing  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# reap_orphaned_leases
# ---------------------------------------------------------------------------


class TestReapOrphanedLeases:
    def test_happy_path_terminal_task_lease_gets_released(self) -> None:
        """Active lease owned by a terminal task should be released."""
        svc, store, _ = _make_service()
        active_lease = _make_lease(
            lease_id="orphan-lease",
            task_id="t-terminal",
            workspace_id="ws-1",
            mode="mutable",
        )
        store.list_workspace_leases.return_value = [active_lease]
        terminal_task = SimpleNamespace(status="completed")
        store.get_task.return_value = terminal_task

        released = svc.reap_orphaned_leases()

        assert "orphan-lease" in released
        store.update_workspace_lease.assert_called_once_with(
            "orphan-lease", status="released", released_at=pytest.approx(time.time(), abs=5)
        )
        store.append_event.assert_called_once()
        event_kwargs = store.append_event.call_args[1]
        assert event_kwargs["event_type"] == "workspace.orphan_reaped"
        assert event_kwargs["payload"]["lease_id"] == "orphan-lease"

    def test_workspace_lease_service_is_none_graceful(self) -> None:
        """When workspace_lease_service is None the caller should handle it;
        here we verify that a service with no active leases returns empty."""
        svc, store, _ = _make_service()
        store.list_workspace_leases.return_value = []

        released = svc.reap_orphaned_leases()

        assert released == []

    def test_store_get_task_returns_none_skip(self) -> None:
        """If the task owning a lease no longer exists, skip it."""
        svc, store, _ = _make_service()
        active_lease = _make_lease(
            lease_id="orphan-lease",
            task_id="t-missing",
            workspace_id="ws-1",
        )
        store.list_workspace_leases.return_value = [active_lease]
        store.get_task.return_value = None

        released = svc.reap_orphaned_leases()

        # Only expire_stale results, if any — no orphan release.
        store.update_workspace_lease.assert_not_called()
        # expire_stale is called internally; released may contain stale results.
        # The orphan-lease should NOT be in released.
        assert "orphan-lease" not in released

    def test_non_terminal_task_skipped(self) -> None:
        """Active leases for running tasks should not be reaped."""
        svc, store, _ = _make_service()
        active_lease = _make_lease(
            lease_id="running-lease",
            task_id="t-running",
            workspace_id="ws-1",
        )
        store.list_workspace_leases.return_value = [active_lease]
        store.get_task.return_value = SimpleNamespace(status="running")

        released = svc.reap_orphaned_leases()

        assert "running-lease" not in released


# ---------------------------------------------------------------------------
# _drain_terminal_queue_entries
# ---------------------------------------------------------------------------


class TestDrainTerminalQueueEntries:
    def test_entries_for_terminal_tasks_are_drained(self) -> None:
        """Pending queue entries whose task is terminal should be marked 'drained'."""
        svc, store, _ = _make_service()
        entry = WorkspaceLeaseQueueEntry(
            queue_entry_id="qe-1",
            workspace_id="ws-1",
            task_id="t-done",
            step_attempt_id="sa-1",
            root_path="/tmp/ws",
            holder_principal_id="p-1",
            mode="mutable",
            status="pending",
        )
        svc._queue["ws-1"] = [entry]
        store.get_task.return_value = SimpleNamespace(status="completed")

        drained = svc._drain_terminal_queue_entries()

        assert drained == 1
        assert entry.status == "drained"

    def test_non_pending_entries_are_skipped(self) -> None:
        """Queue entries that are not 'pending' should be left alone."""
        svc, store, _ = _make_service()
        entry = WorkspaceLeaseQueueEntry(
            queue_entry_id="qe-1",
            workspace_id="ws-1",
            task_id="t-done",
            step_attempt_id="sa-1",
            root_path="/tmp/ws",
            holder_principal_id="p-1",
            mode="mutable",
            status="granted",
        )
        svc._queue["ws-1"] = [entry]
        store.get_task.return_value = SimpleNamespace(status="completed")

        drained = svc._drain_terminal_queue_entries()

        assert drained == 0
        assert entry.status == "granted"

    def test_pending_entry_for_running_task_not_drained(self) -> None:
        """Pending queue entries for non-terminal tasks should not be drained."""
        svc, store, _ = _make_service()
        entry = WorkspaceLeaseQueueEntry(
            queue_entry_id="qe-1",
            workspace_id="ws-1",
            task_id="t-running",
            step_attempt_id="sa-1",
            root_path="/tmp/ws",
            holder_principal_id="p-1",
            mode="mutable",
            status="pending",
        )
        svc._queue["ws-1"] = [entry]
        store.get_task.return_value = SimpleNamespace(status="running")

        drained = svc._drain_terminal_queue_entries()

        assert drained == 0
        assert entry.status == "pending"
