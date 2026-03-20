"""Tests for SelfModifyWorkspace."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermit.kernel.execution.self_modify.models import MergeConflictError
from hermit.kernel.execution.self_modify.workspace import SelfModifyWorkspace


@pytest.fixture()
def mock_inspector() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    """Create a minimal git repo for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=repo,
        capture_output=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(tmp_path),
        },
    )
    return repo


@pytest.fixture()
def workspace(repo_root: Path, mock_inspector: MagicMock) -> SelfModifyWorkspace:
    return SelfModifyWorkspace(repo_root, inspector=mock_inspector)


class TestCreate:
    def test_creates_worktree_with_correct_branch(
        self, workspace: SelfModifyWorkspace, mock_inspector: MagicMock, repo_root: Path
    ) -> None:
        result = workspace.create("iter-001")
        expected_path = repo_root / ".hermit" / "self-modify" / "iter-001"
        assert result == expected_path
        mock_inspector.create_worktree.assert_called_once_with(
            repo_root=repo_root,
            path=expected_path,
            branch="self-modify/iter-001",
        )

    def test_cleans_residual_branch_before_create(
        self, workspace: SelfModifyWorkspace, mock_inspector: MagicMock, repo_root: Path
    ) -> None:
        # Create a residual branch
        subprocess.run(
            ["git", "branch", "self-modify/iter-002"],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
        workspace.create("iter-002")
        # Should still succeed (cleaned up residual)
        mock_inspector.create_worktree.assert_called_once()

    def test_cleans_residual_worktree_dir(
        self, workspace: SelfModifyWorkspace, mock_inspector: MagicMock, repo_root: Path
    ) -> None:
        # Create residual directory
        residual = repo_root / ".hermit" / "self-modify" / "iter-003"
        residual.mkdir(parents=True)
        workspace.create("iter-003")
        mock_inspector.remove_worktree.assert_called_once()
        mock_inspector.create_worktree.assert_called_once()


class TestRemove:
    def test_removes_worktree_and_branch(
        self, workspace: SelfModifyWorkspace, mock_inspector: MagicMock, repo_root: Path
    ) -> None:
        worktree_dir = repo_root / ".hermit" / "self-modify" / "iter-004"
        worktree_dir.mkdir(parents=True)
        workspace.remove("iter-004")
        mock_inspector.remove_worktree.assert_called_once()

    def test_remove_nonexistent_is_safe(
        self, workspace: SelfModifyWorkspace, mock_inspector: MagicMock
    ) -> None:
        workspace.remove("nonexistent")
        mock_inspector.remove_worktree.assert_not_called()

    def test_remove_handles_worktree_remove_failure(
        self, workspace: SelfModifyWorkspace, mock_inspector: MagicMock, repo_root: Path
    ) -> None:
        worktree_dir = repo_root / ".hermit" / "self-modify" / "iter-005"
        worktree_dir.mkdir(parents=True)
        mock_inspector.remove_worktree.side_effect = subprocess.CalledProcessError(1, "git")
        # Should not raise
        workspace.remove("iter-005")


class TestMergeToMain:
    def test_merge_success(self, repo_root: Path, mock_inspector: MagicMock) -> None:
        ws = SelfModifyWorkspace(repo_root, inspector=mock_inspector)
        branch = "self-modify/iter-006"
        subprocess.run(
            ["git", "branch", branch],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
        sha = ws.merge_to_main("iter-006")
        assert len(sha) == 40  # full SHA

    def test_merge_conflict_aborts_and_raises(
        self, repo_root: Path, mock_inspector: MagicMock
    ) -> None:
        ws = SelfModifyWorkspace(repo_root, inspector=mock_inspector)
        # Create divergent branches that will conflict
        subprocess.run(
            ["git", "checkout", "-b", "self-modify/iter-007"],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
        conflict_file = repo_root / "conflict.txt"
        conflict_file.write_text("branch content")
        subprocess.run(["git", "add", "."], cwd=repo_root, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "branch change"],
            cwd=repo_root,
            capture_output=True,
            check=True,
            env={
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "t@t",
                "HOME": str(repo_root.parent),
            },
        )
        subprocess.run(["git", "checkout", "-"], cwd=repo_root, capture_output=True, check=True)
        conflict_file.write_text("main content")
        subprocess.run(["git", "add", "."], cwd=repo_root, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "main change"],
            cwd=repo_root,
            capture_output=True,
            check=True,
            env={
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "t@t",
                "HOME": str(repo_root.parent),
            },
        )
        with pytest.raises(MergeConflictError, match="iter-007"):
            ws.merge_to_main("iter-007")
        # Verify no merge conflicts remain (untracked .hermit/ dir is expected)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        tracked_changes = [
            line for line in status.stdout.strip().splitlines() if not line.startswith("??")
        ]
        assert tracked_changes == []


class TestListActive:
    def test_empty_when_no_worktrees(self, workspace: SelfModifyWorkspace) -> None:
        assert workspace.list_active() == []

    def test_lists_worktree_dirs(self, workspace: SelfModifyWorkspace, repo_root: Path) -> None:
        base = repo_root / ".hermit" / "self-modify"
        (base / "iter-a").mkdir(parents=True)
        (base / "iter-b").mkdir(parents=True)
        assert workspace.list_active() == ["iter-a", "iter-b"]

    def test_excludes_lock_files(self, workspace: SelfModifyWorkspace, repo_root: Path) -> None:
        base = repo_root / ".hermit" / "self-modify"
        base.mkdir(parents=True)
        (base / "create.lock").touch()
        (base / "iter-x").mkdir()
        assert workspace.list_active() == ["iter-x"]


class TestCleanupOrphans:
    def test_removes_orphan_worktrees(
        self, workspace: SelfModifyWorkspace, mock_inspector: MagicMock, repo_root: Path
    ) -> None:
        base = repo_root / ".hermit" / "self-modify"
        (base / "active-1").mkdir(parents=True)
        (base / "orphan-1").mkdir(parents=True)
        cleaned = workspace.cleanup_orphans(active_ids={"active-1"})
        assert cleaned == ["orphan-1"]

    def test_keeps_active_worktrees(
        self, workspace: SelfModifyWorkspace, mock_inspector: MagicMock, repo_root: Path
    ) -> None:
        base = repo_root / ".hermit" / "self-modify"
        (base / "active-1").mkdir(parents=True)
        cleaned = workspace.cleanup_orphans(active_ids={"active-1"})
        assert cleaned == []
