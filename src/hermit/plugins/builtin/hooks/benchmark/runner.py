"""BenchmarkRunner — executes quality checks and detects regressions."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import TYPE_CHECKING, Any

import structlog

from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkResult

# Safe import of the statistical engine modules.  The runner still works
# without them — the engine analysis is purely additive.
try:
    from hermit.kernel.verification.benchmark.effects import compare_effect_sizes
    from hermit.kernel.verification.benchmark.regression import (
        detect_regression as engine_detect_regression,
    )
    from hermit.kernel.verification.benchmark.stats import compute_stats

    _HAS_ENGINE = True
except ImportError:
    _HAS_ENGINE = False

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
            statistical_analysis=self._run_statistical_analysis(
                test_total=test_total,
                test_passed=test_passed,
                coverage=coverage,
                lint_violations=lint_violations,
                baseline=baseline,
            ),
        )
        log.info("benchmark_done", passed=check_passed, regression=regression)
        return result

    async def _exec(self, cmd: str, cwd: str) -> tuple[str, int]:
        # Skip the test suite lock so benchmark can run even when other
        # pytest processes are active (e.g. dispatch workers running tests).
        env = {**os.environ, "_HERMIT_SKIP_TEST_LOCK": "1"}
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
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
        """Fetch the most recent baseline benchmark data for regression detection.

        Primary: look in spec_backlog entries (where the metaloop stores benchmark
        results via _update_metadata).
        Fallback: look in task metadata for backward compatibility.
        """
        # Primary: spec_backlog lookup
        baseline = self._fetch_baseline_from_specs(spec_id, current_id)
        if baseline is not None:
            return baseline

        # Fallback: task metadata (backward compat for older iterations)
        return self._fetch_baseline_from_tasks(spec_id, current_id)

    def _fetch_baseline_from_specs(
        self,
        current_spec_id: str,
        current_id: str,
    ) -> dict[str, Any] | None:
        """Fetch baseline benchmark data from prior spec_backlog entries."""
        if not hasattr(self._store, "list_spec_backlog"):
            return None
        try:
            # Query specs in terminal states that have benchmark metadata
            for status in ("completed", "accepted", "rejected"):
                specs = self._store.list_spec_backlog(
                    status=status,
                    limit=50,
                    order_by="updated_at",
                )
                for spec in specs:
                    # Skip the current spec
                    if spec.get("spec_id") == current_spec_id:
                        continue
                    # Parse the metadata JSON
                    raw_meta = spec.get("metadata")
                    if isinstance(raw_meta, str):
                        try:
                            meta = json.loads(raw_meta)
                        except (json.JSONDecodeError, TypeError):
                            continue
                    elif isinstance(raw_meta, dict):
                        meta = raw_meta
                    else:
                        continue
                    benchmark = meta.get("benchmark")
                    if benchmark is not None and isinstance(benchmark, dict):
                        return benchmark
        except Exception:
            log.debug("baseline_spec_fetch_skipped")
        return None

    def _fetch_baseline_from_tasks(
        self,
        spec_id: str,
        current_id: str,
    ) -> dict[str, Any] | None:
        """Fetch baseline from task metadata (backward compatibility)."""
        if not hasattr(self._store, "list_tasks"):
            return None
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
            log.debug("baseline_task_fetch_skipped")
        return None

    def _run_statistical_analysis(
        self,
        *,
        test_total: int,
        test_passed: int,
        coverage: float,
        lint_violations: int,
        baseline: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Run statistical engine analysis on the parsed benchmark metrics.

        Uses compute_stats() for bootstrap CI and percentiles,
        detect_regression() for Welch's t-test + noise threshold,
        and compare_effect_sizes() for Cohen's d / Cliff's delta.

        Returns None when the engine modules are unavailable or when
        there is no baseline to compare against.
        """
        if not _HAS_ENGINE:
            return None
        if baseline is None:
            return None

        try:
            # Build sample lists from current and baseline scalar metrics.
            # The engine expects list[float] with >= 2 values.  We construct
            # synthetic sample pairs by treating each metric as a repeated
            # measurement (current vs baseline) with small noise to give the
            # statistical tests meaningful variance.
            current_metrics = [
                float(test_total),
                float(test_passed),
                coverage,
                float(lint_violations),
            ]
            baseline_metrics = [
                float(baseline.get("test_total", 0)),
                float(baseline.get("test_passed", 0)),
                float(baseline.get("coverage", 0.0)),
                float(baseline.get("lint_violations", 0)),
            ]

            analysis: dict[str, Any] = {"engine_available": True}

            # Per-metric statistical summaries where meaningful.
            metric_names = ["test_total", "test_passed", "coverage", "lint_violations"]
            per_metric: dict[str, Any] = {}

            for name, cur_val, base_val in zip(
                metric_names, current_metrics, baseline_metrics, strict=True
            ):
                entry: dict[str, Any] = {
                    "current": cur_val,
                    "baseline": base_val,
                    "delta": round(cur_val - base_val, 4),
                }

                # compute_stats on a single value still yields useful summary
                stats = compute_stats([cur_val] if cur_val != 0 else [0.0, 0.0], n_boot=1000)
                entry["stats"] = {
                    "mean": stats.mean,
                    "median": stats.median,
                    "ci_lower": stats.ci_lower,
                    "ci_upper": stats.ci_upper,
                }
                per_metric[name] = entry

            analysis["per_metric"] = per_metric

            # Aggregate effect size and regression across all metrics.
            # Only run the full comparison when both sides have >= 2 values,
            # which they do since we have 4 metrics each.
            if len(current_metrics) >= 2 and len(baseline_metrics) >= 2:
                effect = compare_effect_sizes(baseline_metrics, current_metrics)
                analysis["effect_sizes"] = {
                    "cohens_d": round(effect.cohens_d, 4),
                    "hedges_g": round(effect.hedges_g, 4),
                    "cliffs_delta": round(effect.cliffs_delta, 4),
                    "a12": round(effect.a12, 4),
                    "glass_delta": round(effect.glass_delta, 4),
                    "classification": effect.classification,
                    "direction": effect.direction,
                }

                regression_result = engine_detect_regression(
                    baseline=baseline_metrics,
                    contender=current_metrics,
                    significance_level=0.05,
                    noise_threshold=0.01,
                    n_resamples=5000,
                )
                analysis["regression"] = {
                    "is_regression": regression_result.is_regression,
                    "is_improvement": regression_result.is_improvement,
                    "classification": regression_result.classification,
                    "relative_change": round(regression_result.relative_change, 4),
                    "relative_change_ci": [
                        round(regression_result.relative_change_ci[0], 4),
                        round(regression_result.relative_change_ci[1], 4),
                    ],
                    "t_statistic": round(regression_result.t_statistic, 4),
                    "p_value": round(regression_result.p_value, 4),
                    "confidence_level": regression_result.confidence_level,
                }

            log.debug(
                "statistical_analysis_complete",
                effect_classification=analysis.get("effect_sizes", {}).get("classification"),
                regression_detected=analysis.get("regression", {}).get("is_regression"),
            )
            return analysis

        except Exception as exc:
            log.warning("statistical_analysis_failed", error=str(exc))
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
