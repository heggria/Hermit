"""Worktree merger with PR-based review and safe reload.

After a self-modification passes all verification gates, this module
creates a PR branch (instead of merging directly to main), stores PR
metadata, and — only after explicit approval — performs the merge and
triggers a graceful SIGHUP-based reload.

IMPORTANT: Direct merges to main and os.execv() restarts have been
removed. All iteration changes now go through a PR review workflow.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import structlog

from hermit.kernel.execution.self_modify.models import (
    MergeConflictError,
    SelfModPhase,
    SelfModSession,
)
from hermit.kernel.execution.self_modify.workspace import SelfModifyWorkspace

logger = structlog.get_logger()


@dataclass(frozen=True)
class IterationPRInfo:
    """Metadata for a PR created from a self-iteration."""

    iteration_id: str
    branch_name: str
    title: str
    body: str
    pr_url: str | None = None  # set if remote push + gh pr create succeed
    commit_sha: str = ""
    pushed: bool = False
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "iteration_id": self.iteration_id,
            "branch_name": self.branch_name,
            "title": self.title,
            "body": self.body,
            "pr_url": self.pr_url,
            "commit_sha": self.commit_sha,
            "pushed": self.pushed,
            "metadata": self.metadata,
        }


class WorktreeMerger:
    """Creates PR branches from verified worktrees and performs governed merges.

    The old dangerous behavior (direct merge + os.execv restart) has been
    replaced with a two-phase flow:
    1. create_pr() — push an iteration branch; optionally create a GitHub PR
    2. merge_approved() — only after approval, merge the branch and reload
    """

    def __init__(
        self,
        workspace: SelfModifyWorkspace,
    ) -> None:
        self._workspace = workspace

    # ------------------------------------------------------------------
    # Phase 1: Create PR branch (replaces old merge_and_reload)
    # ------------------------------------------------------------------

    async def create_pr(
        self,
        session: SelfModSession,
        *,
        iteration_summary: str = "",
        benchmark_results: dict | None = None,
        lessons: list[str] | None = None,
    ) -> tuple[SelfModSession, IterationPRInfo]:
        """Create a PR branch from the worktree instead of merging directly.

        Pushes the branch to a remote (if configured) and attempts to create
        a GitHub PR via ``gh``. Does NOT merge — the iteration waits for
        explicit approval.

        Returns the updated session and PR info.
        """
        iteration_id = session.iteration_id
        source_branch = f"self-modify/{iteration_id}"
        pr_branch = f"iteration/{iteration_id}"

        repo_root = self._workspace._repo_root

        # Create the iteration branch from the worktree branch
        try:
            await asyncio.to_thread(
                partial(
                    subprocess.run,
                    ["git", "branch", pr_branch, source_branch],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    check=True,
                )
            )
        except subprocess.CalledProcessError as e:
            logger.error(
                "self_modify.pr.branch_failed",
                iteration_id=iteration_id,
                stderr=e.stderr,
            )
            return (
                session.with_phase(
                    SelfModPhase.FAILED, error=f"Branch creation failed: {e.stderr}"
                ),
                IterationPRInfo(
                    iteration_id=iteration_id,
                    branch_name=pr_branch,
                    title="",
                    body="",
                ),
            )

        # Get the tip commit SHA
        result = await asyncio.to_thread(
            partial(
                subprocess.run,
                ["git", "rev-parse", pr_branch],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
        )
        commit_sha = result.stdout.strip() if result.returncode == 0 else ""

        # Build PR title and body
        title = f"iteration: {iteration_id}"
        body_parts = [f"## Self-Iteration: {iteration_id}"]
        if iteration_summary:
            body_parts.append(f"\n### Summary\n{iteration_summary}")
        if benchmark_results:
            body_parts.append(f"\n### Benchmark Results\n```json\n{benchmark_results}\n```")
        if lessons:
            lesson_text = "\n".join(f"- {ls}" for ls in lessons)
            body_parts.append(f"\n### Lessons Learned\n{lesson_text}")
        body_parts.append(
            "\n---\n*This PR was created automatically by Hermit's self-iteration pipeline. "
            "It requires human review before merging.*"
        )
        body = "\n".join(body_parts)

        # Attempt to push to remote
        pushed = False
        pr_url: str | None = None
        if _has_remote(repo_root):
            push_result = await asyncio.to_thread(
                partial(
                    subprocess.run,
                    ["git", "push", "-u", "origin", pr_branch],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            )
            pushed = push_result.returncode == 0
            if not pushed:
                logger.warning(
                    "self_modify.pr.push_failed",
                    iteration_id=iteration_id,
                    stderr=push_result.stderr,
                )

            # Attempt to create PR via gh CLI
            if pushed:
                pr_url = _create_github_pr(repo_root, pr_branch, title, body)

        pr_info = IterationPRInfo(
            iteration_id=iteration_id,
            branch_name=pr_branch,
            title=title,
            body=body,
            pr_url=pr_url,
            commit_sha=commit_sha,
            pushed=pushed,
            metadata={
                "iteration_summary": iteration_summary,
                "benchmark_results": benchmark_results or {},
                "lessons": lessons or [],
            },
        )

        logger.info(
            "self_modify.pr.created",
            iteration_id=iteration_id,
            branch=pr_branch,
            pushed=pushed,
            pr_url=pr_url,
            commit_sha=commit_sha,
        )

        updated_session = session.with_phase(
            SelfModPhase.COMPLETED,
            commit_sha=commit_sha,
            metadata={**session.metadata, "pr_info": pr_info.to_dict()},
        )

        return updated_session, pr_info

    # ------------------------------------------------------------------
    # Phase 2: Approved merge (called only after explicit approval)
    # ------------------------------------------------------------------

    async def merge_approved(
        self,
        iteration_id: str,
        *,
        pr_branch: str | None = None,
    ) -> str:
        """Merge an approved iteration branch into the current branch.

        Only call this after the iteration has been explicitly approved
        (state = pr_created -> merge_approved). Performs the merge and
        triggers a graceful reload via SIGHUP.

        Returns the merge commit SHA.
        Raises MergeConflictError if the merge fails.
        """
        branch = pr_branch or f"iteration/{iteration_id}"
        repo_root = self._workspace._repo_root

        commit_sha = _do_merge(repo_root, branch, iteration_id)

        # Clean up the iteration branch (merged)
        await asyncio.to_thread(
            partial(
                subprocess.run,
                ["git", "branch", "-d", branch],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
        )

        # Clean up worktree and self-modify branch
        self._workspace.remove(iteration_id)

        logger.info(
            "self_modify.merge_approved.completed",
            iteration_id=iteration_id,
            commit_sha=commit_sha,
        )

        # Graceful reload via SIGHUP — NOT os.execv
        _trigger_sighup_reload()

        return commit_sha


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _has_remote(repo_root: Path) -> bool:
    """Return True if the repo has at least one remote configured."""
    result = subprocess.run(
        ["git", "remote"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


def _create_github_pr(
    repo_root: Path,
    branch: str,
    title: str,
    body: str,
) -> str | None:
    """Attempt to create a GitHub PR via gh CLI. Returns PR URL or None."""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--head",
                branch,
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        pr_url = result.stdout.strip()
        logger.info("self_modify.pr.github_created", pr_url=pr_url)
        return pr_url
    except FileNotFoundError:
        logger.debug("self_modify.pr.gh_not_found")
        return None
    except subprocess.CalledProcessError as e:
        logger.warning(
            "self_modify.pr.gh_failed",
            stderr=e.stderr,
        )
        return None


def _do_merge(repo_root: Path, branch: str, iteration_id: str) -> str:
    """Merge a branch into the current branch. Returns commit SHA.

    Raises MergeConflictError on conflict (after aborting the merge).
    """
    try:
        subprocess.run(
            [
                "git",
                "merge",
                branch,
                "--no-ff",
                "-m",
                f"self-modify: merge approved iteration {iteration_id}",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        raise MergeConflictError(
            f"Merge conflict for approved iteration {iteration_id}: {e.stderr}"
        ) from e

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _trigger_sighup_reload() -> None:
    """Send SIGHUP to the current process to trigger a graceful reload.

    This is the safe reload mechanism used by ``hermit reload``. It
    causes the serve loop to re-read config, rediscover plugins, rebuild
    tools, and restart the adapter — all without replacing the process.
    """
    import signal
    import sys

    if sys.platform == "win32":
        logger.info("self_modify.reload.skipped_windows")
        return

    logger.info("self_modify.reload.sighup")
    os.kill(os.getpid(), signal.SIGHUP)
