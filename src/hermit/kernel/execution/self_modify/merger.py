"""Worktree merger with conflict handling and process restart.

After a self-modification passes all verification gates, this module
merges the worktree branch back into the main branch, cleans up, and
triggers a process restart so the new code takes effect.
"""

from __future__ import annotations

import os
import sys

import structlog

from hermit.kernel.execution.self_modify.models import (
    MergeConflictError,
    SelfModPhase,
    SelfModSession,
)
from hermit.kernel.execution.self_modify.workspace import SelfModifyWorkspace

logger = structlog.get_logger()


class WorktreeMerger:
    """Merges verified worktrees and triggers process reload."""

    def __init__(
        self,
        workspace: SelfModifyWorkspace,
        *,
        restart_mode: str = "hard",  # "hard" | "sighup" | "none"
    ) -> None:
        self._workspace = workspace
        self._restart_mode = restart_mode

    async def merge_and_reload(self, session: SelfModSession) -> SelfModSession:
        """Merge the worktree branch and trigger reload.

        Returns an updated session with COMPLETED or FAILED phase.
        """
        iteration_id = session.iteration_id
        try:
            commit_sha = self._workspace.merge_to_main(iteration_id)
        except MergeConflictError as e:
            self._workspace.remove(iteration_id)
            return session.with_phase(
                SelfModPhase.FAILED,
                error=str(e),
            )

        # Clean up the worktree (branch already merged)
        self._workspace.remove(iteration_id)

        updated = session.with_phase(
            SelfModPhase.COMPLETED,
            commit_sha=commit_sha,
        )

        logger.info(
            "self_modify.merge.completed",
            iteration_id=iteration_id,
            commit_sha=commit_sha,
            restart_mode=self._restart_mode,
        )

        # Trigger process restart if configured
        self._trigger_restart()

        return updated

    def _trigger_restart(self) -> None:
        """Trigger process restart based on configured mode.

        - "hard": os.execv() to restart the current process (replaces it)
        - "sighup": send SIGHUP to self (only reloads adapter, not plugins)
        - "none": skip restart (for testing or manual restart)
        """
        if self._restart_mode == "none":
            logger.info("self_modify.restart.skipped")
            return

        if self._restart_mode == "sighup":
            import signal

            logger.info("self_modify.restart.sighup")
            os.kill(os.getpid(), signal.SIGHUP)
            return

        # Default: hard restart via os.execv
        logger.info("self_modify.restart.hard", argv=sys.argv[:3])
        try:
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except Exception:
            logger.error("self_modify.restart.failed", exc_info=True)
            # Process continues running with old code — safe but stale
