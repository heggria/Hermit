"""Unit tests for GitWorktreeSnapshot and GitWorktreeInspector."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.kernel.execution.suspension.git_worktree import (
    GitWorktreeInspector,
    GitWorktreeSnapshot,
)

# ---------------------------------------------------------------------------
# TestGitWorktreeSnapshot
# ---------------------------------------------------------------------------


class TestGitWorktreeSnapshotToState:
    def test_present_no_error(self) -> None:
        snap = GitWorktreeSnapshot(repo_path="/repo", present=True, head="abc123", dirty=False)
        assert snap.to_state() == {"head": "abc123", "dirty": False}

    def test_present_dirty(self) -> None:
        snap = GitWorktreeSnapshot(repo_path="/repo", present=True, head="abc123", dirty=True)
        assert snap.to_state() == {"head": "abc123", "dirty": True}

    def test_not_present(self) -> None:
        snap = GitWorktreeSnapshot(repo_path="/repo", present=False)
        assert snap.to_state() is None

    def test_with_error(self) -> None:
        snap = GitWorktreeSnapshot(repo_path="/repo", present=True, head="abc", error="fail")
        assert snap.to_state() is None


class TestGitWorktreeSnapshotToWitness:
    def test_present_no_error(self) -> None:
        snap = GitWorktreeSnapshot(repo_path="/repo", present=True, head="abc123", dirty=False)
        result = snap.to_witness()
        assert result == {"present": True, "head": "abc123", "dirty": False}

    def test_present_with_error(self) -> None:
        snap = GitWorktreeSnapshot(
            repo_path="/repo", present=True, head="abc", dirty=False, error="err"
        )
        result = snap.to_witness()
        assert result["present"] is True
        assert result["error"] == "err"
        assert result["head"] == "abc"
        assert result["dirty"] is False

    def test_not_present(self) -> None:
        snap = GitWorktreeSnapshot(repo_path="/repo", present=False)
        result = snap.to_witness()
        assert result == {"present": False}

    def test_not_present_no_head_or_dirty(self) -> None:
        snap = GitWorktreeSnapshot(repo_path="/repo", present=False)
        result = snap.to_witness()
        assert "head" not in result
        assert "dirty" not in result


class TestGitWorktreeSnapshotToPrestate:
    def test_present_no_error(self) -> None:
        snap = GitWorktreeSnapshot(repo_path="/repo", present=True, head="abc123", dirty=True)
        assert snap.to_prestate() == {
            "repo_path": "/repo",
            "head": "abc123",
            "dirty": True,
        }

    def test_not_present(self) -> None:
        snap = GitWorktreeSnapshot(repo_path="/repo", present=False)
        assert snap.to_prestate() is None

    def test_with_error(self) -> None:
        snap = GitWorktreeSnapshot(repo_path="/repo", present=True, head="abc", error="fail")
        assert snap.to_prestate() is None


# ---------------------------------------------------------------------------
# TestGitWorktreeInspector — snapshot
# ---------------------------------------------------------------------------


class TestGitWorktreeInspectorSnapshot:
    def test_no_git_dir(self, tmp_path: Path) -> None:
        inspector = GitWorktreeInspector()
        result = inspector.snapshot(tmp_path)
        assert result.present is False
        assert result.repo_path == str(tmp_path.resolve())

    def test_successful_snapshot_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".git").mkdir()
        inspector = GitWorktreeInspector()

        calls: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
            calls.append(cmd)
            if cmd[1] == "rev-parse":
                return SimpleNamespace(returncode=0, stdout="abc123def\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result = inspector.snapshot(tmp_path)
        assert result.present is True
        assert result.head == "abc123def"
        assert result.dirty is False
        assert result.error is None

    def test_dirty_snapshot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".git").mkdir()
        inspector = GitWorktreeInspector()

        def mock_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
            if cmd[1] == "rev-parse":
                return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
            return SimpleNamespace(returncode=0, stdout=" M file.py\n", stderr="")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result = inspector.snapshot(tmp_path)
        assert result.dirty is True

    def test_head_command_fails_with_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".git").mkdir()
        inspector = GitWorktreeInspector()

        def mock_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(returncode=128, stdout="", stderr="fatal: bad ref")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result = inspector.snapshot(tmp_path)
        assert result.present is True
        assert result.error == "fatal: bad ref"

    def test_head_command_fails_without_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".git").mkdir()
        inspector = GitWorktreeInspector()

        def mock_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(returncode=1, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result = inspector.snapshot(tmp_path)
        assert result.error == "git rev-parse failed"

    def test_status_command_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".git").mkdir()
        inspector = GitWorktreeInspector()

        def mock_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
            if cmd[1] == "rev-parse":
                return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="status error")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result = inspector.snapshot(tmp_path)
        assert result.present is True
        assert result.head == "abc123"
        assert result.error == "status error"

    def test_status_fails_without_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".git").mkdir()
        inspector = GitWorktreeInspector()

        def mock_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
            if cmd[1] == "rev-parse":
                return SimpleNamespace(returncode=0, stdout="abc\n", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result = inspector.snapshot(tmp_path)
        assert result.error == "git status failed"

    def test_os_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".git").mkdir()
        inspector = GitWorktreeInspector()

        def mock_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
            raise OSError("command not found")

        monkeypatch.setattr(subprocess, "run", mock_run)
        result = inspector.snapshot(tmp_path)
        assert result.present is True
        assert result.error == "git unavailable"


# ---------------------------------------------------------------------------
# TestGitWorktreeInspector — hard_reset
# ---------------------------------------------------------------------------


class TestGitWorktreeInspectorHardReset:
    def test_calls_git_reset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        inspector = GitWorktreeInspector()
        captured: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
            captured.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", mock_run)
        inspector.hard_reset(tmp_path, "abc123")
        assert captured[0] == ["git", "reset", "--hard", "abc123"]

    def test_propagates_called_process_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inspector = GitWorktreeInspector()

        def mock_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(subprocess, "run", mock_run)
        with pytest.raises(subprocess.CalledProcessError):
            inspector.hard_reset(tmp_path, "abc123")


# ---------------------------------------------------------------------------
# TestGitWorktreeInspector — create_worktree
# ---------------------------------------------------------------------------


class TestGitWorktreeInspectorCreateWorktree:
    def test_calls_git_worktree_add(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        inspector = GitWorktreeInspector()
        captured: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
            captured.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", mock_run)
        wt_path = tmp_path / "worktree"
        inspector.create_worktree(repo_root=tmp_path, path=wt_path, branch="feature-1")
        assert captured[0] == [
            "git",
            "worktree",
            "add",
            "-b",
            "feature-1",
            str(wt_path),
        ]


# ---------------------------------------------------------------------------
# TestGitWorktreeInspector — remove_worktree
# ---------------------------------------------------------------------------


class TestGitWorktreeInspectorRemoveWorktree:
    def test_calls_git_worktree_remove(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inspector = GitWorktreeInspector()
        captured: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
            captured.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", mock_run)
        wt_path = tmp_path / "worktree"
        inspector.remove_worktree(repo_root=tmp_path, path=wt_path)
        assert captured[0] == [
            "git",
            "worktree",
            "remove",
            "--force",
            str(wt_path),
        ]


# ---------------------------------------------------------------------------
# TestCommandError
# ---------------------------------------------------------------------------


class TestCommandError:
    def test_success_returns_none(self) -> None:
        inspector = GitWorktreeInspector()
        result = SimpleNamespace(returncode=0, stderr="")
        assert inspector._command_error(result, default="fail") is None

    def test_failure_with_stderr(self) -> None:
        inspector = GitWorktreeInspector()
        result = SimpleNamespace(returncode=1, stderr="error message")
        assert inspector._command_error(result, default="fail") == "error message"

    def test_failure_without_stderr(self) -> None:
        inspector = GitWorktreeInspector()
        result = SimpleNamespace(returncode=1, stderr="")
        assert inspector._command_error(result, default="default error") == "default error"

    def test_failure_with_whitespace_stderr(self) -> None:
        inspector = GitWorktreeInspector()
        result = SimpleNamespace(returncode=1, stderr="   ")
        assert inspector._command_error(result, default="default error") == "default error"

    def test_none_returncode_treated_as_zero(self) -> None:
        inspector = GitWorktreeInspector()
        result = SimpleNamespace(returncode=None, stderr="")
        assert inspector._command_error(result, default="fail") is None

    def test_missing_returncode_attr(self) -> None:
        inspector = GitWorktreeInspector()
        result = SimpleNamespace()
        assert inspector._command_error(result, default="fail") is None

    def test_missing_stderr_attr(self) -> None:
        inspector = GitWorktreeInspector()
        result = SimpleNamespace(returncode=1)
        assert inspector._command_error(result, default="fallback") == "fallback"
