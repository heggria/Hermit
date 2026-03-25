from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

from hermit.kernel.execution.suspension.git_worktree import GitWorktreeInspector

logger = structlog.get_logger()


class CompetitionWorkspaceManager:
    """Manages git worktrees for competition candidates."""

    def __init__(self, repo_root: Path, inspector: GitWorktreeInspector | None = None) -> None:
        self._repo_root = repo_root.resolve()
        self._inspector = inspector or GitWorktreeInspector()

    def create_workspace(self, competition_id: str, candidate_label: str) -> str:
        """Create a git worktree for a competition candidate.

        Returns the worktree path as a string.
        """
        worktree_dir = self._repo_root / ".hermit" / "competition" / competition_id
        worktree_dir.mkdir(parents=True, exist_ok=True)
        worktree_path = worktree_dir / candidate_label
        branch_name = f"competition/{competition_id}/{candidate_label}"
        self._inspector.create_worktree(
            repo_root=self._repo_root,
            path=worktree_path,
            branch=branch_name,
        )
        logger.info(
            "competition.workspace.created",
            competition_id=competition_id,
            candidate_label=candidate_label,
            worktree_path=str(worktree_path),
        )
        return str(worktree_path)

    def merge_winner(self, competition_id: str, workspace_ref: str) -> None:
        """Merge the winner branch back into the current branch."""
        worktree_path = Path(workspace_ref)
        # Determine branch name from worktree
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=True,
        )
        winner_branch = result.stdout.strip()
        subprocess.run(
            [
                "git",
                "merge",
                winner_branch,
                "--no-ff",
                "-m",
                f"Merge competition winner: {competition_id}",
            ],
            cwd=self._repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info(
            "competition.workspace.merged",
            competition_id=competition_id,
            branch=winner_branch,
        )

    def cleanup_all(self, competition_id: str) -> None:
        """Remove all worktrees associated with a competition.

        Raises RuntimeError listing every worktree path that could not be
        removed, so callers are never silently left with leaked worktrees.
        """
        competition_dir = self._repo_root / ".hermit" / "competition" / competition_id
        if not competition_dir.exists():
            return

        failed_paths: list[str] = []
        for child in sorted(competition_dir.iterdir()):
            if child.is_dir():
                try:
                    self._inspector.remove_worktree(repo_root=self._repo_root, path=child)
                except Exception:
                    logger.warning(
                        "competition.workspace.cleanup_failed",
                        path=str(child),
                        exc_info=True,
                    )
                    failed_paths.append(str(child))

        # Remove competition directory itself only when it is empty.
        try:
            competition_dir.rmdir()
        except OSError:
            logger.warning(
                "competition.workspace.dir_not_removed",
                competition_dir=str(competition_dir),
                reason="directory not empty or permission error",
            )

        logger.info("competition.workspace.cleanup_done", competition_id=competition_id)

        if failed_paths:
            raise RuntimeError(
                f"cleanup_all: {len(failed_paths)} worktree(s) could not be removed "
                f"for competition '{competition_id}': {failed_paths}"
            )

    def list_orphans(self) -> list[str]:
        """List worktree directories under .hermit/competition/ that have no matching record."""
        base = self._repo_root / ".hermit" / "competition"
        if not base.exists():
            return []
        orphans: list[str] = []
        for child in sorted(base.iterdir()):
            if child.is_dir():
                orphans.append(str(child))
        return orphans
