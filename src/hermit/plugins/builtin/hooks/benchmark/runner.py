"""BenchmarkRunner — executes quality checks and detects regressions."""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any

import structlog

from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkResult

if TYPE_CHECKING:
    from hermit.kernel.analytics.engine import AnalyticsEngine
    from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()

_PYTEST_SUMMARY_RE = re.compile(r"(\d+)\s+passed(?:.*?(\d+)\s+failed)?", re.IGNORECASE)
_COVERAGE_RE = re.compile(r"TOTAL\s+\d+\s+\d+\s+(\d+(?:\.\d+)?)%")
_RUFF_VIOLATION_RE = re.compile(r"Found\s+(\d+)\s+error", re.IGNORECASE)


class BenchmarkRunner:
    """Run quality checks and produce a BenchmarkResult."""

    def __init__(
        self,
        store: KernelStore,
        analytics: AnalyticsEngine | None = None,
        timeout: int = 600,
    ) -> None:
        self._store = store
        self._analytics = analytics
        self._timeout = timeout

    async def run(
        self,
        iteration_id: str,
        spec_id: str,
        worktree_path: str | None = None,
    ) -> BenchmarkResult:
        """Execute make check and parse results."""
        start = time.monotonic()
        cwd = worktree_path or "."
        log.info("benchmark_start", iteration_id=iteration_id, spec_id=spec_id)

        stdout, returncode = await self._exec("make check", cwd)
        duration = time.monotonic() - start

        test_total, test_passed = _parse_pytest(stdout)
        coverage = _parse_coverage(stdout)
        lint_violations = _parse_ruff(stdout)
        check_passed = returncode == 0

        baseline = self._fetch_baseline(spec_id, iteration_id)
        regression, compared = _detect_regression(
            test_total,
            coverage,
            lint_violations,
            baseline,
        )

        result = BenchmarkResult(
            iteration_id=iteration_id,
            spec_id=spec_id,
            check_passed=check_passed,
            test_total=test_total,
            test_passed=test_passed,
            coverage=coverage,
            lint_violations=lint_violations,
            duration_seconds=round(duration, 2),
            regression_detected=regression,
            compared_to_baseline=compared,
        )
        log.info("benchmark_done", passed=check_passed, regression=regression)
        return result

    async def _exec(self, cmd: str, cwd: str) -> tuple[str, int]:
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            raw, _ = await asyncio.wait_for(proc.communicate(), self._timeout)
            return raw.decode(errors="replace"), proc.returncode or 0
        except TimeoutError:
            log.warning("benchmark_timeout", cmd=cmd, timeout=self._timeout)
            return "", 1
        except OSError as exc:
            log.warning("benchmark_exec_error", error=str(exc))
            return "", 1

    def _fetch_baseline(
        self,
        spec_id: str,
        current_id: str,
    ) -> dict[str, Any] | None:
        if not hasattr(self._store, "list_lessons"):
            return None
        # Convention: store benchmark metadata on the iteration record
        try:
            tasks = self._store.list_tasks(limit=50)
            for t in tasks:
                meta = getattr(t, "metadata", None) or {}
                if (
                    meta.get("spec_id") == spec_id
                    and meta.get("benchmark") is not None
                    and meta.get("iteration_id") != current_id
                ):
                    return meta["benchmark"]  # type: ignore[no-any-return]
        except Exception:
            log.debug("baseline_fetch_skipped")
        return None


def _parse_pytest(output: str) -> tuple[int, int]:
    m = _PYTEST_SUMMARY_RE.search(output)
    if not m:
        return 0, 0
    passed = int(m.group(1))
    failed = int(m.group(2)) if m.group(2) else 0
    return passed + failed, passed


def _parse_coverage(output: str) -> float:
    m = _COVERAGE_RE.search(output)
    return float(m.group(1)) if m else 0.0


def _parse_ruff(output: str) -> int:
    m = _RUFF_VIOLATION_RE.search(output)
    return int(m.group(1)) if m else 0


def _detect_regression(
    test_total: int,
    coverage: float,
    lint_violations: int,
    baseline: dict[str, Any] | None,
) -> tuple[bool, dict[str, Any]]:
    if baseline is None:
        return False, {}
    compared: dict[str, Any] = {
        "test_total_delta": test_total - baseline.get("test_total", 0),
        "coverage_delta": round(coverage - baseline.get("coverage", 0.0), 2),
        "lint_delta": lint_violations - baseline.get("lint_violations", 0),
    }
    regression = (
        test_total < baseline.get("test_total", 0)
        or coverage < baseline.get("coverage", 0.0) - 0.5
        or lint_violations > baseline.get("lint_violations", 0)
    )
    return regression, compared
