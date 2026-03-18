from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GitWorktreeSnapshot:
    repo_path: str
    present: bool
    head: str = ""
    dirty: bool = False
    error: str | None = None

    def to_state(self) -> dict[str, Any] | None:
        if not self.present or self.error:
            return None
        return {"head": self.head, "dirty": self.dirty}

    def to_witness(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"present": self.present}
        if not self.present:
            return payload
        payload["head"] = self.head
        payload["dirty"] = self.dirty
        if self.error:
            payload["error"] = self.error
        return payload

    def to_prestate(self) -> dict[str, Any] | None:
        if not self.present or self.error:
            return None
        return {"repo_path": self.repo_path, "head": self.head, "dirty": self.dirty}


class GitWorktreeInspector:
    def snapshot(self, workspace_root: Path) -> GitWorktreeSnapshot:
        root = workspace_root.resolve()
        if not (root / ".git").exists():
            return GitWorktreeSnapshot(repo_path=str(root), present=False)
        try:
            head_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            head_error = self._command_error(head_result, default="git rev-parse failed")
            if head_error is not None:
                return GitWorktreeSnapshot(repo_path=str(root), present=True, error=head_error)
            head = str(getattr(head_result, "stdout", "") or "").strip()
            status_result = subprocess.run(
                ["git", "status", "--short"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            status_error = self._command_error(status_result, default="git status failed")
            if status_error is not None:
                return GitWorktreeSnapshot(
                    repo_path=str(root),
                    present=True,
                    head=head,
                    error=status_error,
                )
        except OSError:
            return GitWorktreeSnapshot(repo_path=str(root), present=True, error="git unavailable")
        return GitWorktreeSnapshot(
            repo_path=str(root),
            present=True,
            head=head,
            dirty=bool(str(getattr(status_result, "stdout", "") or "").strip()),
        )

    def hard_reset(self, workspace_root: Path, head: str) -> None:
        subprocess.run(
            ["git", "reset", "--hard", head],
            cwd=workspace_root.resolve(),
            check=True,
            capture_output=True,
            text=True,
        )

    def create_worktree(self, *, repo_root: Path, path: Path, branch: str) -> None:
        """Create a new git worktree with a new branch."""
        subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(path)],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )

    def remove_worktree(self, *, repo_root: Path, path: Path) -> None:
        """Remove a git worktree."""
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )

    def _command_error(self, result: Any, *, default: str) -> str | None:
        returncode = int(getattr(result, "returncode", 0) or 0)
        if returncode == 0:
            return None
        stderr = str(getattr(result, "stderr", "") or "").strip()
        return stderr or default
