"""Self-modification worktree lifecycle management.

Creates isolated git worktrees for self-modification, ensuring the live
Hermit instance is never affected by in-progress changes.  Follows the
same pattern as CompetitionWorkspaceManager but scoped to self-iterate.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

from hermit.infra.locking.lock import FileGuard
from hermit.kernel.execution.self_modify.models import MergeConflictError
from hermit.kernel.execution.suspension.git_worktree import GitWorktreeInspector

logger = structlog.get_logger()

_WORKTREE_BASE = ".hermit/self-modify"


class SelfModifyWorkspace:
    """Manages git worktrees for self-modification iterations."""

    def __init__(
        self,
        repo_root: Path,
        inspector: GitWorktreeInspector | None = None,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._inspector = inspector or GitWorktreeInspector()
        self._base_dir = self._repo_root / _WORKTREE_BASE

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def create(self, iteration_id: str) -> Path:
        """Create an isolated worktree for a self-modification iteration.

        Uses FileGuard to prevent concurrent branch creation races.
        Cleans up any residual branch from a previous failed attempt.
        """
        worktree_path = self._base_dir / iteration_id
        branch_name = f"self-modify/{iteration_id}"
        lock_path = self._base_dir / "create.lock"

        with FileGuard.acquire(lock_path, cross_process=True):
            # Clean up residual branch if exists (from previous crash)
            existing = subprocess.run(
                ["git", "branch", "--list", branch_name],
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            if existing.stdout.strip():
                subprocess.run(
                    ["git", "branch", "-D", branch_name],
                    cwd=self._repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                logger.info(
                    "self_modify.branch.cleaned",
                    iteration_id=iteration_id,
                    branch=branch_name,
                )

            # Clean up residual worktree directory
            if worktree_path.exists():
                self._inspector.remove_worktree(
                    repo_root=self._repo_root,
                    path=worktree_path,
                )

            self._base_dir.mkdir(parents=True, exist_ok=True)
            self._inspector.create_worktree(
                repo_root=self._repo_root,
                path=worktree_path,
                branch=branch_name,
            )

        logger.info(
            "self_modify.workspace.created",
            iteration_id=iteration_id,
            worktree_path=str(worktree_path),
            branch=branch_name,
        )
        return worktree_path

    def remove(self, iteration_id: str) -> None:
        """Remove a worktree and its branch. Safe to call if already removed."""
        worktree_path = self._base_dir / iteration_id
        branch_name = f"self-modify/{iteration_id}"

        if worktree_path.exists():
            try:
                self._inspector.remove_worktree(
                    repo_root=self._repo_root,
                    path=worktree_path,
                )
            except Exception:
                logger.warning(
                    "self_modify.workspace.remove_failed",
                    iteration_id=iteration_id,
                    exc_info=True,
                )

        # Clean up the branch
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=self._repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        logger.info("self_modify.workspace.removed", iteration_id=iteration_id)

    def merge_to_main(self, iteration_id: str) -> str:
        """Merge the self-modify branch back into the current branch.

        Uses FileGuard to serialize concurrent merge operations.
        Handles merge conflicts by aborting and raising MergeConflictError.

        Returns the merge commit SHA.
        """
        branch_name = f"self-modify/{iteration_id}"
        merge_lock = self._base_dir / "merge.lock"

        with FileGuard.acquire(merge_lock, cross_process=True):
            try:
                subprocess.run(
                    [
                        "git",
                        "merge",
                        branch_name,
                        "--no-ff",
                        "-m",
                        f"self-modify: merge iteration {iteration_id}",
                    ],
                    cwd=self._repo_root,
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                # Abort the failed merge to restore clean state
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=self._repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                logger.error(
                    "self_modify.merge.conflict",
                    iteration_id=iteration_id,
                    stderr=e.stderr,
                )
                raise MergeConflictError(
                    f"Merge conflict for iteration {iteration_id}: {e.stderr}"
                ) from e

            # Get the merge commit SHA
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
            commit_sha = result.stdout.strip()

        logger.info(
            "self_modify.workspace.merged",
            iteration_id=iteration_id,
            commit_sha=commit_sha,
        )
        return commit_sha

    def list_active(self) -> list[str]:
        """List iteration IDs with active worktrees."""
        if not self._base_dir.exists():
            return []
        return sorted(
            child.name
            for child in self._base_dir.iterdir()
            if child.is_dir() and not child.name.endswith(".lock")
        )

    def cleanup_orphans(self, active_ids: set[str] | None = None) -> list[str]:
        """Remove worktrees not in the active set. Returns cleaned IDs."""
        active = active_ids or set()
        cleaned: list[str] = []
        for iteration_id in self.list_active():
            if iteration_id not in active:
                self.remove(iteration_id)
                cleaned.append(iteration_id)
                logger.info(
                    "self_modify.orphan.cleaned",
                    iteration_id=iteration_id,
                )
        return cleaned
