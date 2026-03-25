"""Staged verification for self-modification worktrees.

Three gates, each more thorough than the last.  Fails fast — if Gate 1
fails, Gate 2 and Gate 3 are skipped.

Gate 1 (test-quick):   ~10s — core smoke tests
Gate 2 (test-changed): ~1-3min — tests affected by changed files
Gate 3 (check):        ~5-10min — full lint + typecheck + test suite
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import structlog

from hermit.kernel.execution.self_modify.models import (
    StagedVerificationResult,
    VerificationGate,
    VerificationResult,
)

logger = structlog.get_logger()

# Default timeouts per gate (seconds)
_DEFAULT_TIMEOUTS: dict[VerificationGate, int] = {
    VerificationGate.QUICK: 30,
    VerificationGate.CHANGED: 180,
    VerificationGate.FULL: 600,
}


class StagedVerifier:
    """Runs 3-level verification gates on a worktree, failing fast."""

    def __init__(
        self,
        *,
        repo_root: Path,
        timeouts: dict[VerificationGate, int] | None = None,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._timeouts = timeouts or dict(_DEFAULT_TIMEOUTS)

    async def verify(self, worktree_path: Path) -> StagedVerificationResult:
        """Run all 3 gates sequentially. Stops at first failure."""
        results: list[VerificationResult] = []

        for gate in (VerificationGate.QUICK, VerificationGate.CHANGED, VerificationGate.FULL):
            result = await self._run_gate(gate, worktree_path)
            results.append(result)
            if result.failed:
                logger.warning(
                    "self_modify.verify.gate_failed",
                    gate=gate.value,
                    duration=result.duration_seconds,
                    error=result.error or result.stderr[:200],
                )
                return StagedVerificationResult(
                    results=tuple(results),
                    passed=False,
                )
            logger.info(
                "self_modify.verify.gate_passed",
                gate=gate.value,
                duration=result.duration_seconds,
            )

        return StagedVerificationResult(
            results=tuple(results),
            passed=True,
        )

    async def _run_gate(
        self,
        gate: VerificationGate,
        worktree_path: Path,
    ) -> VerificationResult:
        """Run a single verification gate."""
        timeout = self._timeouts.get(gate, 300)
        cmd = self._gate_command(gate, worktree_path)

        start = time.monotonic()
        try:
            result = await asyncio.to_thread(
                self._run_subprocess,
                cmd,
                worktree_path,
                timeout,
            )
            elapsed = time.monotonic() - start
            return VerificationResult(
                gate=gate,
                passed=result.returncode == 0,
                duration_seconds=round(elapsed, 2),
                stdout=result.stdout[-2000:] if result.stdout else "",
                stderr=result.stderr[-2000:] if result.stderr else "",
                return_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            return VerificationResult(
                gate=gate,
                passed=False,
                duration_seconds=round(elapsed, 2),
                error=f"Timeout after {timeout}s",
                return_code=-1,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            return VerificationResult(
                gate=gate,
                passed=False,
                duration_seconds=round(elapsed, 2),
                error=str(exc),
                return_code=-1,
            )

    def _gate_command(self, gate: VerificationGate, worktree_path: Path) -> list[str]:
        """Build the shell command for a gate."""
        if gate == VerificationGate.QUICK:
            return ["make", "test-quick"]
        elif gate == VerificationGate.CHANGED:
            return self._changed_tests_command(worktree_path)
        else:
            return ["make", "check"]

    def _changed_tests_command(self, worktree_path: Path) -> list[str]:
        """Build command to run only tests affected by changed files.

        Maps changed source files to their test counterparts by convention:
          src/hermit/foo/bar.py → tests/unit/foo/test_bar.py
        Falls back to test-quick if no mapping found.
        """
        try:
            diff_result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD...main"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if diff_result.returncode != 0:
                # Fallback: diff against parent
                diff_result = subprocess.run(
                    ["git", "diff", "--name-only", "HEAD~1"],
                    cwd=worktree_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )
        except Exception:
            return ["make", "test-quick"]

        changed_files = [
            f
            for f in diff_result.stdout.strip().splitlines()
            if f.endswith(".py") and f.startswith("src/hermit/")
        ]
        if not changed_files:
            return ["make", "test-quick"]

        test_files: list[str] = []
        for src_file in changed_files:
            # src/hermit/foo/bar.py → tests/unit/foo/test_bar.py
            rel = src_file.removeprefix("src/hermit/")
            parts = rel.rsplit("/", 1)
            if len(parts) == 2:
                directory, filename = parts
                test_path = f"tests/unit/{directory}/test_{filename}"
            else:
                test_path = f"tests/unit/test_{parts[0]}"
            full_path = worktree_path / test_path
            if full_path.exists():
                test_files.append(test_path)

        if not test_files:
            return ["make", "test-quick"]

        return ["uv", "run", "pytest", *test_files, "-x", "-q"]

    @staticmethod
    def _run_subprocess(
        cmd: list[str],
        cwd: Path,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
