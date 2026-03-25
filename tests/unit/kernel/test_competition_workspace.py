from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermit.kernel.execution.competition.workspace import CompetitionWorkspaceManager


class FakeInspector:
    """Lightweight mock for GitWorktreeInspector."""

    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.removed: list[dict[str, object]] = []
        self.remove_error: bool = False

    def create_worktree(self, *, repo_root: Path, path: Path, branch: str) -> None:
        self.created.append({"repo_root": repo_root, "path": path, "branch": branch})

    def remove_worktree(self, *, repo_root: Path, path: Path) -> None:
        self.removed.append({"repo_root": repo_root, "path": path})
        if self.remove_error:
            raise RuntimeError("remove failed")


# -- __init__ ----------------------------------------------------------------


def test_init_resolves_repo_root(tmp_path: Path) -> None:
    """Lines 17-18: repo_root is resolved and inspector defaults to GitWorktreeInspector."""
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(sub, inspector=inspector)
    assert mgr._repo_root == sub.resolve()


def test_init_default_inspector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Line 18: when no inspector is given, a GitWorktreeInspector is created."""
    from hermit.kernel.execution.suspension import git_worktree

    calls: list[object] = []

    class StubInspector:
        def __init__(self) -> None:
            calls.append(self)

    monkeypatch.setattr(git_worktree, "GitWorktreeInspector", StubInspector)
    # Re-import to pick up the patched class via the module-level reference
    import hermit.kernel.execution.competition.workspace as ws_mod

    monkeypatch.setattr(ws_mod, "GitWorktreeInspector", StubInspector)
    CompetitionWorkspaceManager(tmp_path)
    assert len(calls) == 1


# -- create_workspace --------------------------------------------------------


def test_create_workspace_returns_path(tmp_path: Path) -> None:
    """Lines 25-29, 34, 40: creates directory structure, delegates to inspector, returns path."""
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    result = mgr.create_workspace("comp-1", "alpha")

    expected_dir = tmp_path / ".hermit" / "competition" / "comp-1"
    expected_path = expected_dir / "alpha"
    assert result == str(expected_path)
    assert expected_dir.exists()
    assert len(inspector.created) == 1
    assert inspector.created[0]["path"] == expected_path
    assert inspector.created[0]["branch"] == "competition/comp-1/alpha"
    assert inspector.created[0]["repo_root"] == tmp_path.resolve()


def test_create_workspace_multiple_candidates(tmp_path: Path) -> None:
    """Calling create_workspace twice produces two distinct worktrees."""
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    p1 = mgr.create_workspace("comp-2", "alpha")
    p2 = mgr.create_workspace("comp-2", "beta")

    assert p1 != p2
    assert len(inspector.created) == 2


# -- merge_winner ------------------------------------------------------------


def test_merge_winner_calls_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lines 44, 46, 53-54, 68: rev-parse to get branch, then merge."""
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(cmd)
        if "rev-parse" in cmd:
            return SimpleNamespace(stdout="competition/comp-1/alpha\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    workspace_path = str(tmp_path / ".hermit" / "competition" / "comp-1" / "alpha")
    mgr.merge_winner("comp-1", workspace_path)

    assert len(calls) == 2
    # First call: rev-parse
    assert calls[0] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]
    # Second call: merge with --no-ff
    assert calls[1][0:3] == ["git", "merge", "competition/comp-1/alpha"]
    assert "--no-ff" in calls[1]
    assert "Merge competition winner: comp-1" in calls[1]


def test_merge_winner_uses_correct_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lines 44, 46: rev-parse uses worktree cwd, merge uses repo_root."""
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    cwds: list[object] = []

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        cwds.append(kwargs.get("cwd"))
        if "rev-parse" in cmd:
            return SimpleNamespace(stdout="feat-branch\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    ws = str(tmp_path / "ws")
    mgr.merge_winner("comp-x", ws)

    assert cwds[0] == Path(ws)  # rev-parse in worktree
    assert cwds[1] == tmp_path.resolve()  # merge in repo root


# -- cleanup_all -------------------------------------------------------------


def test_cleanup_all_removes_worktrees(tmp_path: Path) -> None:
    """Lines 76-84, 90-94: removes each child dir, then removes competition dir."""
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    comp_dir = tmp_path / ".hermit" / "competition" / "comp-1"
    (comp_dir / "alpha").mkdir(parents=True)
    (comp_dir / "beta").mkdir(parents=True)

    mgr.cleanup_all("comp-1")

    assert len(inspector.removed) == 2
    removed_paths = {r["path"] for r in inspector.removed}
    assert comp_dir / "alpha" in removed_paths
    assert comp_dir / "beta" in removed_paths
    # Competition dir itself should be removed (it's now empty after worktree removal)
    # Note: since FakeInspector doesn't actually delete dirs, rmdir may fail silently


def test_cleanup_all_nonexistent_dir(tmp_path: Path) -> None:
    """Lines 76-78: early return when competition directory does not exist."""
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    # Should not raise
    mgr.cleanup_all("nonexistent")
    assert len(inspector.removed) == 0


def test_cleanup_all_handles_remove_error(tmp_path: Path) -> None:
    """Lines 81-84: logs warning but continues when remove_worktree fails."""
    inspector = FakeInspector()
    inspector.remove_error = True
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    comp_dir = tmp_path / ".hermit" / "competition" / "comp-1"
    (comp_dir / "alpha").mkdir(parents=True)
    (comp_dir / "beta").mkdir(parents=True)

    # Should not raise despite remove errors
    mgr.cleanup_all("comp-1")

    # Both were attempted
    assert len(inspector.removed) == 2


def test_cleanup_all_skips_files(tmp_path: Path) -> None:
    """Line 80: only directories are passed to remove_worktree, files are skipped."""
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    comp_dir = tmp_path / ".hermit" / "competition" / "comp-1"
    comp_dir.mkdir(parents=True)
    (comp_dir / "alpha").mkdir()
    (comp_dir / "note.txt").write_text("hi")

    mgr.cleanup_all("comp-1")

    assert len(inspector.removed) == 1
    assert inspector.removed[0]["path"] == comp_dir / "alpha"


def test_cleanup_all_rmdir_silent_on_nonempty(tmp_path: Path) -> None:
    """Lines 90-93: OSError from rmdir is caught silently; leftover file is not deleted."""
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    comp_dir = tmp_path / ".hermit" / "competition" / "comp-1"
    comp_dir.mkdir(parents=True)
    leftover = comp_dir / "leftover.txt"
    leftover.write_text("data")

    # cleanup_all must not raise even though rmdir will fail on a non-empty dir
    mgr.cleanup_all("comp-1")

    # The directory (and the leftover file) should still exist — rmdir failed silently
    assert leftover.exists(), "leftover file must survive the silent OSError from rmdir"


# -- list_orphans ------------------------------------------------------------


def test_list_orphans_returns_dirs(tmp_path: Path) -> None:
    """Lines 98-105: returns sorted list of directories under .hermit/competition/."""
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    base = tmp_path / ".hermit" / "competition"
    (base / "comp-b").mkdir(parents=True)
    (base / "comp-a").mkdir(parents=True)
    # File should be excluded
    (base / "readme.txt").write_text("info")

    result = mgr.list_orphans()

    assert result == [str(base / "comp-a"), str(base / "comp-b")]


def test_list_orphans_empty_when_no_base(tmp_path: Path) -> None:
    """Lines 98-100: returns empty list when .hermit/competition/ does not exist."""
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    result = mgr.list_orphans()
    assert result == []


def test_list_orphans_empty_when_no_children(tmp_path: Path) -> None:
    """Lines 101-105: returns empty list when base exists but has no subdirectories."""
    inspector = FakeInspector()
    mgr = CompetitionWorkspaceManager(tmp_path, inspector=inspector)

    base = tmp_path / ".hermit" / "competition"
    base.mkdir(parents=True)
    (base / "file.txt").write_text("not a dir")

    result = mgr.list_orphans()
    assert result == []
