"""Unit tests for ReconcileService (reconcile.py)."""

from __future__ import annotations

import hashlib
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.kernel.execution.recovery.reconcile import ReconcileOutcome, ReconcileService
from hermit.kernel.execution.suspension.git_worktree import GitWorktreeSnapshot

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_git() -> MagicMock:
    git = MagicMock()
    git.snapshot.return_value = GitWorktreeSnapshot(
        repo_path="/repo", present=True, head="abc123", dirty=False
    )
    return git


@pytest.fixture
def mock_store() -> MagicMock:
    return MagicMock()


@pytest.fixture
def svc(mock_git: MagicMock, mock_store: MagicMock) -> ReconcileService:
    return ReconcileService(git_worktree=mock_git, store=mock_store)


# ---------------------------------------------------------------------------
# TestReconcileReadonly
# ---------------------------------------------------------------------------


class TestReconcileReadonly:
    def test_readonly_command(self, svc: ReconcileService) -> None:
        result = svc.reconcile(
            action_type="execute_command_readonly",
            tool_input={},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "reconciled_applied"
        assert "Read-only" in result.summary


# ---------------------------------------------------------------------------
# TestReconcileLocalWrite
# ---------------------------------------------------------------------------


class TestReconcileLocalWrite:
    def test_file_matches(self, svc: ReconcileService, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("hello world", encoding="utf-8")
        result = svc.reconcile(
            action_type="write_local",
            tool_input={"path": "test.txt", "content": "hello world"},
            workspace_root=str(tmp_path),
        )
        assert result.result_code == "reconciled_applied"

    def test_file_missing(self, svc: ReconcileService, tmp_path: Path) -> None:
        result = svc.reconcile(
            action_type="write_local",
            tool_input={"path": "missing.txt", "content": "hello"},
            workspace_root=str(tmp_path),
        )
        assert result.result_code == "reconciled_not_applied"

    def test_file_content_mismatch(self, svc: ReconcileService, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("different content", encoding="utf-8")
        result = svc.reconcile(
            action_type="write_local",
            tool_input={"path": "test.txt", "content": "expected content"},
            workspace_root=str(tmp_path),
        )
        assert result.result_code == "reconciled_not_applied"

    def test_empty_path_falls_through(self, svc: ReconcileService) -> None:
        result = svc.reconcile(
            action_type="write_local",
            tool_input={"path": "", "content": "hello"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "still_unknown"

    def test_empty_workspace_root_falls_through(self, svc: ReconcileService) -> None:
        result = svc.reconcile(
            action_type="write_local",
            tool_input={"path": "test.txt", "content": "hello"},
            workspace_root="",
        )
        assert result.result_code == "still_unknown"

    def test_patch_file_action_type(self, svc: ReconcileService, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("patched", encoding="utf-8")
        result = svc.reconcile(
            action_type="patch_file",
            tool_input={"path": "test.txt", "content": "patched"},
            workspace_root=str(tmp_path),
        )
        assert result.result_code == "reconciled_applied"

    def test_os_error_reading_file(
        self, svc: ReconcileService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "test.txt"
        target.write_text("content", encoding="utf-8")

        original_read_text = Path.read_text

        def mock_read_text(self_path: Path, *args: Any, **kwargs: Any) -> str:
            if self_path.name == "test.txt":
                raise OSError("Permission denied")
            return original_read_text(self_path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", mock_read_text)
        result = svc.reconcile(
            action_type="write_local",
            tool_input={"path": "test.txt", "content": "content"},
            workspace_root=str(tmp_path),
        )
        assert result.result_code == "reconciled_not_applied"


# ---------------------------------------------------------------------------
# TestReconcileCommandOrVcs
# ---------------------------------------------------------------------------


class TestReconcileCommandOrVcs:
    def test_changed_paths_detected(self, svc: ReconcileService, tmp_path: Path) -> None:
        target = tmp_path / "changed.txt"
        target.write_text("new content", encoding="utf-8")
        result = svc.reconcile(
            action_type="execute_command",
            tool_input={},
            workspace_root=str(tmp_path),
            observables={"target_paths": [str(target)]},
            witness={"files": [{"path": str(target), "exists": False}]},
        )
        assert result.result_code == "reconciled_applied"

    def test_git_changed_detected(self, svc: ReconcileService, mock_git: MagicMock) -> None:
        mock_git.snapshot.return_value = GitWorktreeSnapshot(
            repo_path="/repo", present=True, head="new_head", dirty=False
        )
        result = svc.reconcile(
            action_type="execute_command",
            tool_input={},
            workspace_root="/tmp/ws",
            observables={},
            witness={"git": {"head": "old_head", "dirty": False}},
        )
        assert result.result_code == "reconciled_applied"

    def test_git_not_changed_vcs_mutation(self, svc: ReconcileService, mock_git: MagicMock) -> None:
        mock_git.snapshot.return_value = GitWorktreeSnapshot(
            repo_path="/repo", present=True, head="same_head", dirty=False
        )
        result = svc.reconcile(
            action_type="vcs_mutation",
            tool_input={},
            workspace_root="/tmp/ws",
            observables={},
            witness={"git": {"head": "same_head", "dirty": False}},
        )
        assert result.result_code == "reconciled_not_applied"

    def test_git_not_changed_with_vcs_operation_observable(
        self, svc: ReconcileService, mock_git: MagicMock
    ) -> None:
        mock_git.snapshot.return_value = GitWorktreeSnapshot(
            repo_path="/repo", present=True, head="same", dirty=False
        )
        result = svc.reconcile(
            action_type="execute_command",
            tool_input={},
            workspace_root="/tmp/ws",
            observables={"vcs_operation": "commit"},
            witness={"git": {"head": "same", "dirty": False}},
        )
        assert result.result_code == "reconciled_not_applied"

    def test_command_preview_with_target_paths_unchanged(
        self, svc: ReconcileService, tmp_path: Path
    ) -> None:
        target = tmp_path / "unchanged.txt"
        target.write_text("content", encoding="utf-8")
        stat = target.stat()

        result = svc.reconcile(
            action_type="execute_command",
            tool_input={},
            workspace_root=str(tmp_path),
            observables={
                "command_preview": "some cmd",
                "target_paths": [str(target)],
            },
            witness={
                "files": [
                    {
                        "path": str(target),
                        "exists": True,
                        "mtime_ns": stat.st_mtime_ns,
                        "size": stat.st_size,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    }
                ]
            },
        )
        assert result.result_code == "reconciled_not_applied"

    def test_no_changes_returns_still_unknown(
        self, svc: ReconcileService, mock_git: MagicMock
    ) -> None:
        mock_git.snapshot.return_value = GitWorktreeSnapshot(repo_path="/repo", present=False)
        result = svc.reconcile(
            action_type="execute_command",
            tool_input={},
            workspace_root="/tmp/ws",
            observables={},
            witness={},
        )
        # When there are no observable targets (no target_paths, no git
        # witness, no vcs_operation), the reconciler now infers success
        # rather than returning still_unknown.
        assert result.result_code == "reconciled_inferred"
        assert "No observable side-effect targets" in result.summary

    def test_no_observables_with_git_witness_returns_none_fallthrough(
        self, svc: ReconcileService, mock_git: MagicMock
    ) -> None:
        """When a git witness is present but head/dirty unchanged and no
        target_paths, the method returns None (falls through to still_unknown)
        because there *was* something to check and nothing changed."""
        mock_git.snapshot.return_value = GitWorktreeSnapshot(
            repo_path="/repo", present=True, head="same", dirty=False
        )
        result = svc.reconcile(
            action_type="execute_command",
            tool_input={},
            workspace_root="/tmp/ws",
            observables={},
            witness={"git": {"head": "same", "dirty": False}},
        )
        # git witness was provided and showed no change — falls through
        assert result.result_code == "still_unknown"


# ---------------------------------------------------------------------------
# TestReconcileRemoteWrite
# ---------------------------------------------------------------------------


class TestReconcileRemoteWrite:
    def test_no_url_falls_through(self, svc: ReconcileService) -> None:
        result = svc.reconcile(
            action_type="network_write",
            tool_input={},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "still_unknown"

    @patch("hermit.kernel.execution.recovery.reconcile.urllib.request.urlopen")
    @patch("hermit.kernel.execution.recovery.reconcile.get_runtime_budget")
    def test_successful_head_request(
        self, mock_budget: MagicMock, mock_urlopen: MagicMock, svc: ReconcileService
    ) -> None:
        mock_budget.return_value = SimpleNamespace(reconciliation_probe_timeout=10)
        response = MagicMock()
        response.status = 200
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = response
        result = svc.reconcile(
            action_type="network_write",
            tool_input={"url": "https://example.com/api"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "reconciled_observed"

    @patch("hermit.kernel.execution.recovery.reconcile.urllib.request.urlopen")
    @patch("hermit.kernel.execution.recovery.reconcile.get_runtime_budget")
    def test_404_response(
        self, mock_budget: MagicMock, mock_urlopen: MagicMock, svc: ReconcileService
    ) -> None:
        mock_budget.return_value = SimpleNamespace(reconciliation_probe_timeout=10)
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com", 404, "Not Found", {}, None
        )
        result = svc.reconcile(
            action_type="network_write",
            tool_input={"url": "https://example.com/api"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "reconciled_not_applied"

    @patch("hermit.kernel.execution.recovery.reconcile.urllib.request.urlopen")
    @patch("hermit.kernel.execution.recovery.reconcile.get_runtime_budget")
    def test_401_response(
        self, mock_budget: MagicMock, mock_urlopen: MagicMock, svc: ReconcileService
    ) -> None:
        mock_budget.return_value = SimpleNamespace(reconciliation_probe_timeout=10)
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com", 401, "Unauthorized", {}, None
        )
        result = svc.reconcile(
            action_type="network_write",
            tool_input={"url": "https://example.com/api"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "reconciled_observed"

    @patch("hermit.kernel.execution.recovery.reconcile.urllib.request.urlopen")
    @patch("hermit.kernel.execution.recovery.reconcile.get_runtime_budget")
    def test_403_response(
        self, mock_budget: MagicMock, mock_urlopen: MagicMock, svc: ReconcileService
    ) -> None:
        mock_budget.return_value = SimpleNamespace(reconciliation_probe_timeout=10)
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com", 403, "Forbidden", {}, None
        )
        result = svc.reconcile(
            action_type="network_write",
            tool_input={"url": "https://example.com/api"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "reconciled_observed"

    @patch("hermit.kernel.execution.recovery.reconcile.urllib.request.urlopen")
    @patch("hermit.kernel.execution.recovery.reconcile.get_runtime_budget")
    def test_405_response(
        self, mock_budget: MagicMock, mock_urlopen: MagicMock, svc: ReconcileService
    ) -> None:
        mock_budget.return_value = SimpleNamespace(reconciliation_probe_timeout=10)
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com", 405, "Method Not Allowed", {}, None
        )
        result = svc.reconcile(
            action_type="network_write",
            tool_input={"url": "https://example.com/api"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "reconciled_observed"

    @patch("hermit.kernel.execution.recovery.reconcile.urllib.request.urlopen")
    @patch("hermit.kernel.execution.recovery.reconcile.get_runtime_budget")
    def test_500_response_returns_none(
        self, mock_budget: MagicMock, mock_urlopen: MagicMock, svc: ReconcileService
    ) -> None:
        mock_budget.return_value = SimpleNamespace(reconciliation_probe_timeout=10)
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com", 500, "Server Error", {}, None
        )
        result = svc.reconcile(
            action_type="network_write",
            tool_input={"url": "https://example.com/api"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "still_unknown"

    @patch("hermit.kernel.execution.recovery.reconcile.urllib.request.urlopen")
    @patch("hermit.kernel.execution.recovery.reconcile.get_runtime_budget")
    def test_os_error_returns_none(
        self, mock_budget: MagicMock, mock_urlopen: MagicMock, svc: ReconcileService
    ) -> None:
        mock_budget.return_value = SimpleNamespace(reconciliation_probe_timeout=10)
        mock_urlopen.side_effect = OSError("connection refused")
        result = svc.reconcile(
            action_type="network_write",
            tool_input={"url": "https://example.com/api"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "still_unknown"

    def test_url_from_resource_url(self, svc: ReconcileService) -> None:
        with (
            patch(
                "hermit.kernel.execution.recovery.reconcile.urllib.request.urlopen"
            ) as mock_urlopen,
            patch("hermit.kernel.execution.recovery.reconcile.get_runtime_budget") as mock_budget,
        ):
            mock_budget.return_value = SimpleNamespace(reconciliation_probe_timeout=10)
            response = MagicMock()
            response.status = 200
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = response
            result = svc.reconcile(
                action_type="network_write",
                tool_input={"resource_url": "https://example.com/resource"},
                workspace_root="/tmp/ws",
            )
            assert result.result_code == "reconciled_observed"

    def test_url_from_webhook_url(self, svc: ReconcileService) -> None:
        with (
            patch(
                "hermit.kernel.execution.recovery.reconcile.urllib.request.urlopen"
            ) as mock_urlopen,
            patch("hermit.kernel.execution.recovery.reconcile.get_runtime_budget") as mock_budget,
        ):
            mock_budget.return_value = SimpleNamespace(reconciliation_probe_timeout=10)
            response = MagicMock()
            response.status = 200
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = response
            result = svc.reconcile(
                action_type="network_write",
                tool_input={"webhook_url": "https://example.com/webhook"},
                workspace_root="/tmp/ws",
            )
            assert result.result_code == "reconciled_observed"

    def test_publication_action_type(self, svc: ReconcileService) -> None:
        with (
            patch(
                "hermit.kernel.execution.recovery.reconcile.urllib.request.urlopen"
            ) as mock_urlopen,
            patch("hermit.kernel.execution.recovery.reconcile.get_runtime_budget") as mock_budget,
        ):
            mock_budget.return_value = SimpleNamespace(reconciliation_probe_timeout=10)
            response = MagicMock()
            response.status = 200
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = response
            result = svc.reconcile(
                action_type="publication",
                tool_input={"url": "https://example.com/publish"},
                workspace_root="/tmp/ws",
            )
            assert result.result_code == "reconciled_observed"

    def test_external_mutation_action_type(self, svc: ReconcileService) -> None:
        with (
            patch(
                "hermit.kernel.execution.recovery.reconcile.urllib.request.urlopen"
            ) as mock_urlopen,
            patch("hermit.kernel.execution.recovery.reconcile.get_runtime_budget") as mock_budget,
        ):
            mock_budget.return_value = SimpleNamespace(reconciliation_probe_timeout=10)
            response = MagicMock()
            response.status = 200
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = response
            result = svc.reconcile(
                action_type="external_mutation",
                tool_input={"url": "https://example.com/mutate"},
                workspace_root="/tmp/ws",
            )
            assert result.result_code == "reconciled_observed"


# ---------------------------------------------------------------------------
# TestReconcileStoreObservation
# ---------------------------------------------------------------------------


class TestReconcileStoreObservation:
    def test_store_none_falls_through(self) -> None:
        svc = ReconcileService(store=None)
        result = svc.reconcile(
            action_type="scheduler_mutation",
            tool_input={"schedule_id": "sched-1"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "still_unknown"

    def test_record_found(self, svc: ReconcileService, mock_store: MagicMock) -> None:
        mock_store.get_schedule.return_value = SimpleNamespace(schedule_id="sched-1")
        result = svc.reconcile(
            action_type="scheduler_mutation",
            tool_input={"schedule_id": "sched-1"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "reconciled_applied"

    def test_record_not_found(self, svc: ReconcileService, mock_store: MagicMock) -> None:
        mock_store.get_schedule.return_value = None
        result = svc.reconcile(
            action_type="scheduler_mutation",
            tool_input={"schedule_id": "sched-1"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "reconciled_not_applied"

    def test_memory_write_uses_memory_id(
        self, svc: ReconcileService, mock_store: MagicMock
    ) -> None:
        mock_store.get_memory_record.return_value = SimpleNamespace(memory_id="mem-1")
        result = svc.reconcile(
            action_type="memory_write",
            tool_input={"memory_id": "mem-1"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "reconciled_applied"
        mock_store.get_memory_record.assert_called_once_with("mem-1")

    def test_rollback_uses_receipt_id(self, svc: ReconcileService, mock_store: MagicMock) -> None:
        mock_store.get_receipt.return_value = SimpleNamespace(receipt_id="rcpt-1")
        result = svc.reconcile(
            action_type="rollback",
            tool_input={"receipt_id": "rcpt-1"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "reconciled_applied"

    def test_approval_resolution_uses_approval_id(
        self, svc: ReconcileService, mock_store: MagicMock
    ) -> None:
        mock_store.get_approval.return_value = SimpleNamespace(approval_id="appr-1")
        result = svc.reconcile(
            action_type="approval_resolution",
            tool_input={"approval_id": "appr-1"},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "reconciled_applied"

    def test_no_record_id_falls_through(self, svc: ReconcileService, mock_store: MagicMock) -> None:
        result = svc.reconcile(
            action_type="scheduler_mutation",
            tool_input={},
            workspace_root="/tmp/ws",
        )
        assert result.result_code == "still_unknown"

    def test_store_observation_action_type(
        self, svc: ReconcileService, mock_store: MagicMock
    ) -> None:
        svc.reconcile(
            action_type="store_observation",
            tool_input={"record_id": "rec-1"},
            workspace_root="/tmp/ws",
            observables={"entity_type": "task"},
        )
        mock_store.get_task.assert_called_once_with("rec-1")

    def test_entity_type_fallback(self, svc: ReconcileService, mock_store: MagicMock) -> None:
        mock_store.get_step.return_value = SimpleNamespace(step_id="s-1")
        result = svc.reconcile(
            action_type="store_observation",
            tool_input={"record_id": "s-1"},
            workspace_root="/tmp/ws",
            observables={"entity_type": "step"},
        )
        assert result.result_code == "reconciled_applied"


# ---------------------------------------------------------------------------
# TestChangedPaths
# ---------------------------------------------------------------------------


class TestChangedPaths:
    def test_path_changed(self, svc: ReconcileService, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("new", encoding="utf-8")
        result = svc._changed_paths(
            target_paths=[str(target)],
            witness_files=[{"path": str(target), "exists": False}],
        )
        assert str(target) in result

    def test_path_unchanged(self, svc: ReconcileService, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("content", encoding="utf-8")
        stat = target.stat()
        result = svc._changed_paths(
            target_paths=[str(target)],
            witness_files=[
                {
                    "path": str(target),
                    "exists": True,
                    "mtime_ns": stat.st_mtime_ns,
                    "size": stat.st_size,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            ],
        )
        assert result == []

    def test_empty_paths(self, svc: ReconcileService) -> None:
        assert svc._changed_paths(target_paths=[], witness_files=[]) == []


# ---------------------------------------------------------------------------
# TestPathState
# ---------------------------------------------------------------------------


class TestPathState:
    def test_existing_file(self, svc: ReconcileService, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("hello", encoding="utf-8")
        result = svc._path_state(target)
        assert result["exists"] is True
        assert "mtime_ns" in result
        assert "size" in result
        assert "sha256" in result

    def test_nonexistent_path(self, svc: ReconcileService, tmp_path: Path) -> None:
        target = tmp_path / "missing.txt"
        result = svc._path_state(target)
        assert result["exists"] is False

    def test_directory(self, svc: ReconcileService, tmp_path: Path) -> None:
        target = tmp_path / "subdir"
        target.mkdir()
        result = svc._path_state(target)
        assert result["exists"] is True
        assert result["kind"] == "directory"

    def test_os_error_on_exists(
        self, svc: ReconcileService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = Path("/nonexistent/deep/path")
        # Path.exists() may raise OSError for certain paths
        original_exists = Path.exists

        def mock_exists(self_path: Path) -> bool:
            if str(self_path) == str(target):
                raise OSError("Permission denied")
            return original_exists(self_path)

        monkeypatch.setattr(Path, "exists", mock_exists)
        result = svc._path_state(target)
        assert result["exists"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# TestGitChanged
# ---------------------------------------------------------------------------


class TestGitChanged:
    def test_no_workspace_root(self, svc: ReconcileService) -> None:
        assert svc._git_changed(workspace_root="", witness={"head": "abc"}) is None

    def test_witness_not_dict(self, svc: ReconcileService) -> None:
        assert svc._git_changed(workspace_root="/tmp/ws", witness="not_dict") is None

    def test_witness_none(self, svc: ReconcileService) -> None:
        assert svc._git_changed(workspace_root="/tmp/ws", witness=None) is None

    def test_head_changed(self, svc: ReconcileService, mock_git: MagicMock) -> None:
        mock_git.snapshot.return_value = GitWorktreeSnapshot(
            repo_path="/repo", present=True, head="new_head", dirty=False
        )
        assert (
            svc._git_changed(
                workspace_root="/tmp/ws",
                witness={"head": "old_head", "dirty": False},
            )
            is True
        )

    def test_dirty_changed(self, svc: ReconcileService, mock_git: MagicMock) -> None:
        mock_git.snapshot.return_value = GitWorktreeSnapshot(
            repo_path="/repo", present=True, head="same", dirty=True
        )
        assert (
            svc._git_changed(
                workspace_root="/tmp/ws",
                witness={"head": "same", "dirty": False},
            )
            is True
        )

    def test_no_change(self, svc: ReconcileService, mock_git: MagicMock) -> None:
        mock_git.snapshot.return_value = GitWorktreeSnapshot(
            repo_path="/repo", present=True, head="same", dirty=False
        )
        assert (
            svc._git_changed(
                workspace_root="/tmp/ws",
                witness={"head": "same", "dirty": False},
            )
            is False
        )

    def test_git_state_none(self, svc: ReconcileService, mock_git: MagicMock) -> None:
        mock_git.snapshot.return_value = GitWorktreeSnapshot(repo_path="/repo", present=False)
        assert (
            svc._git_changed(
                workspace_root="/tmp/ws",
                witness={"head": "abc", "dirty": False},
            )
            is None
        )


# ---------------------------------------------------------------------------
# TestExtractStoreRecordId
# ---------------------------------------------------------------------------


class TestExtractStoreRecordId:
    def test_scheduler_mutation(self, svc: ReconcileService) -> None:
        result = svc._extract_store_record_id("scheduler_mutation", {"schedule_id": "sched-1"}, {})
        assert result == "sched-1"

    def test_memory_write(self, svc: ReconcileService) -> None:
        result = svc._extract_store_record_id("memory_write", {"memory_id": "mem-1"}, {})
        assert result == "mem-1"

    def test_rollback(self, svc: ReconcileService) -> None:
        result = svc._extract_store_record_id("rollback", {"receipt_id": "rcpt-1"}, {})
        assert result == "rcpt-1"

    def test_approval_resolution(self, svc: ReconcileService) -> None:
        result = svc._extract_store_record_id("approval_resolution", {"approval_id": "appr-1"}, {})
        assert result == "appr-1"

    def test_fallback_record_id(self, svc: ReconcileService) -> None:
        result = svc._extract_store_record_id("unknown_action", {"record_id": "rec-1"}, {})
        assert result == "rec-1"

    def test_no_id_returns_empty(self, svc: ReconcileService) -> None:
        result = svc._extract_store_record_id("unknown_action", {}, {})
        assert result == ""


# ---------------------------------------------------------------------------
# TestLookupStoreRecord
# ---------------------------------------------------------------------------


class TestLookupStoreRecord:
    def test_known_action_type_found(self, svc: ReconcileService, mock_store: MagicMock) -> None:
        mock_store.get_schedule.return_value = SimpleNamespace(schedule_id="sched-1")
        result = svc._lookup_store_record("scheduler_mutation", "sched-1", {})
        assert result is not None
        assert result["record_id"] == "sched-1"

    def test_known_action_type_not_found(
        self, svc: ReconcileService, mock_store: MagicMock
    ) -> None:
        mock_store.get_schedule.return_value = None
        result = svc._lookup_store_record("scheduler_mutation", "sched-1", {})
        assert result is None

    def test_entity_type_fallback(self, svc: ReconcileService, mock_store: MagicMock) -> None:
        mock_store.get_step.return_value = SimpleNamespace(step_id="s-1")
        result = svc._lookup_store_record("unknown_action", "s-1", {"entity_type": "step"})
        assert result is not None
        assert result["entity_type"] == "step"

    def test_entity_type_not_found(self, svc: ReconcileService, mock_store: MagicMock) -> None:
        mock_store.get_step.return_value = None
        result = svc._lookup_store_record("unknown_action", "s-1", {"entity_type": "step"})
        assert result is None

    def test_no_store(self) -> None:
        svc = ReconcileService(store=None)
        result = svc._lookup_store_record("scheduler_mutation", "sched-1", {})
        assert result is None

    def test_no_entity_type_no_mapping(self, svc: ReconcileService, mock_store: MagicMock) -> None:
        result = svc._lookup_store_record("unknown_action", "rec-1", {})
        assert result is None


# ---------------------------------------------------------------------------
# TestReconcileOutcome
# ---------------------------------------------------------------------------


class TestReconcileOutcome:
    def test_dataclass_fields(self) -> None:
        outcome = ReconcileOutcome(
            result_code="reconciled_applied",
            summary="test summary",
            observed_refs=["ref-1", "ref-2"],
        )
        assert outcome.result_code == "reconciled_applied"
        assert outcome.summary == "test summary"
        assert outcome.observed_refs == ["ref-1", "ref-2"]


# ---------------------------------------------------------------------------
# TestDefaultConstructor
# ---------------------------------------------------------------------------


class TestDefaultConstructor:
    def test_default_git_worktree(self) -> None:
        svc = ReconcileService()
        assert svc.git_worktree is not None
        assert svc.store is None

    def test_with_store(self, mock_store: MagicMock) -> None:
        svc = ReconcileService(store=mock_store)
        assert svc.store is mock_store
