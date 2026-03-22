"""BenchmarkRunner — executes quality checks and detects regressions."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import TYPE_CHECKING, Any

import structlog

from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkErrorDetail, BenchmarkResult

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

# Patterns for structured error detail extraction
_PYTEST_FAILED_RE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)
_PYRIGHT_ERROR_RE = re.compile(r"^(.+?):(\d+):\d+:?\s*-?\s*error:", re.MULTILINE)
_MYPY_ERROR_RE = re.compile(r"^(.+?):(\d+):\s*error:", re.MULTILINE)
_TYPECHECK_SUMMARY_RE = re.compile(r"(\d+)\s+error(?:s)?\s+(?:in|found|on)", re.IGNORECASE)
_RUFF_FILE_RE = re.compile(r"^(.+?):\d+:\d+:\s+\w+\d+", re.MULTILINE)
_RAW_OUTPUT_LIMIT = 2000


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
        verification_plan: tuple[dict[str, str], ...] | None = None,
        baseline_metrics: dict[str, Any] | None = None,
        changed_files: list[str] | None = None,
        strategy: str = "tiered",
        verification_baseline: list[dict[str, Any]] | None = None,
    ) -> BenchmarkResult:
        """Execute quality checks and parse results.

        When *changed_files* is provided and *strategy* is ``"tiered"``
        (the default), runs a 3-tier benchmark:

        - **Tier 1 (5-10s):** lint + test only the changed files.
        - **Tier 2 (30-60s):** all unit tests.
        - **Tier 3 (3-10min):** full ``make test`` (skips typecheck).

        Each tier runs only if the previous tier passed.  When
        *changed_files* is empty/None or *strategy* is ``"full"``,
        falls back to ``make check``.

        When *baseline_metrics* is provided (a dict with ``typecheck_errors``
        and ``lint_violations`` captured **before** the iteration started),
        the pass/fail decision uses **delta comparison** instead of requiring
        a zero exit code.  Pre-existing errors that the iteration did not
        introduce are tolerated; only *new* errors cause a failure.
        Test failures are always treated as real failures.

        When *verification_baseline* is provided (a list of dicts with actual
        ``before_value`` measurements captured before implementation), the
        verification plan comparison uses real before/after deltas instead of
        relying on the LLM-guessed ``before_expected`` field.
        """
        start = time.monotonic()
        cwd = worktree_path or "."
        log.info(
            "benchmark_start",
            iteration_id=iteration_id,
            spec_id=spec_id,
            strategy=strategy,
            changed_file_count=len(changed_files) if changed_files else 0,
        )

        tier = "full"
        strategy_used = strategy
        if strategy == "tiered" and changed_files:
            stdout, returncode, tier = await self._run_tiered(changed_files, cwd)
        elif strategy == "quick" and changed_files:
            stdout, returncode, tier = await self._run_quick(changed_files, cwd)
        else:
            strategy_used = "full"
            stdout, returncode = await self._exec("make check", cwd)
        duration = time.monotonic() - start

        test_total, test_passed = _parse_pytest(stdout)
        coverage = _parse_coverage(stdout)
        lint_violations = _parse_ruff(stdout)
        typecheck_errors = _count_typecheck_errors(stdout)

        # --- Delta comparison vs absolute pass/fail ---
        delta_info: dict[str, Any] = {}
        if baseline_metrics:
            baseline_tc = int(baseline_metrics.get("typecheck_errors", 0))
            baseline_lint = int(baseline_metrics.get("lint_violations", 0))

            new_typecheck = max(0, typecheck_errors - baseline_tc)
            new_lint = max(0, lint_violations - baseline_lint)

            # Tests must still pass (test failures are always real)
            test_ok = test_passed == test_total or test_total == 0
            check_passed = (new_typecheck == 0) and (new_lint == 0) and test_ok

            delta_info = {
                "baseline_typecheck": baseline_tc,
                "current_typecheck": typecheck_errors,
                "new_typecheck": new_typecheck,
                "baseline_lint": baseline_lint,
                "current_lint": lint_violations,
                "new_lint": new_lint,
                "test_passed": test_passed,
                "test_total": test_total,
            }
            log.info(
                "benchmark_delta_comparison",
                iteration_id=iteration_id,
                new_typecheck=new_typecheck,
                new_lint=new_lint,
                test_ok=test_ok,
                check_passed=check_passed,
            )
        else:
            check_passed = returncode == 0

        # Build structured error details when the check failed.
        error_details: tuple[BenchmarkErrorDetail, ...] = ()
        if not check_passed:
            error_details = _collect_error_details(stdout, lint_violations)

        raw_output = stdout[:_RAW_OUTPUT_LIMIT] if not check_passed else ""

        baseline = self._fetch_baseline(spec_id, iteration_id)
        regression, compared = _detect_regression(
            test_total,
            coverage,
            lint_violations,
            baseline,
        )

        # Run verification plan if provided
        verification_results: tuple[dict[str, Any], ...] = ()
        if verification_plan:
            verification_results = await self._run_verification_plan(
                verification_plan, cwd, verification_baseline
            )

        result = BenchmarkResult(
            iteration_id=iteration_id,
            spec_id=spec_id,
            check_passed=check_passed,
            test_total=test_total,
            test_passed=test_passed,
            coverage=coverage,
            lint_violations=lint_violations,
            typecheck_errors=typecheck_errors,
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
            error_details=error_details,
            raw_output=raw_output,
            verification_results=verification_results,
            delta_info=delta_info,
            tier_reached=tier,
            strategy_used=strategy_used,
        )
        log.info(
            "benchmark_done",
            passed=check_passed,
            regression=regression,
            tier=tier,
            strategy=strategy_used,
        )
        return result

    # ------------------------------------------------------------------
    # Tiered benchmark strategies
    # ------------------------------------------------------------------

    async def _run_tiered(
        self,
        changed_files: list[str],
        cwd: str,
    ) -> tuple[str, int, str]:
        """Run a 3-tier benchmark.  Returns (stdout, returncode, tier_reached).

        Tier 1 — Quick (5-10s): lint + test changed files only.
        Tier 2 — Medium (30-60s): all unit tests.
        Tier 3 — Full (3-10min): ``make test`` (skips typecheck).
        """
        all_output_parts: list[str] = []
        py_files = [f for f in changed_files if f.endswith(".py")]
        test_files = [f for f in py_files if "test" in f]
        src_files = [f for f in py_files if "test" not in f]

        # --- Tier 1: lint + test changed files only ---
        if src_files:
            lint_cmd = f"uv run ruff check {' '.join(src_files)}"
            lint_out, lint_rc = await self._exec(lint_cmd, cwd)
            all_output_parts.append(lint_out)
            if lint_rc != 0:
                log.info(
                    "benchmark_tier1_lint_failed",
                    file_count=len(src_files),
                    returncode=lint_rc,
                )
                return "\n".join(all_output_parts), lint_rc, "tier1_lint"

        if test_files:
            test_cmd = f"uv run pytest {' '.join(test_files)} -x -q --no-header"
            test_out, test_rc = await self._exec(test_cmd, cwd)
            all_output_parts.append(test_out)
            if test_rc != 0:
                log.info(
                    "benchmark_tier1_test_failed",
                    test_count=len(test_files),
                    returncode=test_rc,
                )
                return "\n".join(all_output_parts), test_rc, "tier1_test"

        log.info("benchmark_tier1_passed")

        # --- Tier 2: all unit tests ---
        unit_out, unit_rc = await self._exec("uv run pytest tests/unit/ -x -q --no-header", cwd)
        all_output_parts.append(unit_out)
        if unit_rc != 0:
            log.info("benchmark_tier2_unit_failed", returncode=unit_rc)
            return "\n".join(all_output_parts), unit_rc, "tier2_unit"

        log.info("benchmark_tier2_passed")

        # --- Tier 3: full test suite (skip typecheck) ---
        full_out, full_rc = await self._exec("make test", cwd)
        all_output_parts.append(full_out)
        tier = "tier3_full"
        if full_rc != 0:
            log.info("benchmark_tier3_full_failed", returncode=full_rc)
        else:
            log.info("benchmark_tier3_passed")
        return "\n".join(all_output_parts), full_rc, tier

    async def _run_quick(
        self,
        changed_files: list[str],
        cwd: str,
    ) -> tuple[str, int, str]:
        """Run only Tier 1 (changed files).  Returns (stdout, returncode, tier)."""
        all_output_parts: list[str] = []
        py_files = [f for f in changed_files if f.endswith(".py")]
        test_files = [f for f in py_files if "test" in f]
        src_files = [f for f in py_files if "test" not in f]

        if src_files:
            lint_cmd = f"uv run ruff check {' '.join(src_files)}"
            lint_out, lint_rc = await self._exec(lint_cmd, cwd)
            all_output_parts.append(lint_out)
            if lint_rc != 0:
                return "\n".join(all_output_parts), lint_rc, "tier1_lint"

        if test_files:
            test_cmd = f"uv run pytest {' '.join(test_files)} -x -q --no-header"
            test_out, test_rc = await self._exec(test_cmd, cwd)
            all_output_parts.append(test_out)
            if test_rc != 0:
                return "\n".join(all_output_parts), test_rc, "tier1_test"

        combined = "\n".join(all_output_parts)
        return combined, 0, "tier1_quick"

    # ------------------------------------------------------------------
    # Subprocess execution
    # ------------------------------------------------------------------

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

    async def _run_verification_plan(
        self,
        plan: tuple[dict[str, str], ...],
        cwd: str,
        baseline_measurements: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Execute each verification plan entry and compare against baseline.

        When *baseline_measurements* is provided (actual before-implementation
        measurements), uses real before/after delta instead of the LLM-guessed
        ``before_expected`` / ``after_expected`` fields.

        For each entry:
        1. Run ``measurement_command`` to get the current (after) value.
        2. Look up the matching baseline by metric name to get the real before value.
        3. Compare: did the metric improve, stay the same, or regress?
        4. Apply ``threshold`` to decide pass/fail.

        Returns a tuple of result dicts:
            {metric, command, before_value, after_value, delta, threshold, passed, reason}
        """
        # Build a lookup from metric name -> baseline measurement
        baseline_by_metric: dict[str, dict[str, Any]] = {}
        if baseline_measurements:
            for bm in baseline_measurements:
                metric_name = bm.get("metric", "")
                if metric_name:
                    baseline_by_metric[metric_name] = bm

        results: list[dict[str, Any]] = []
        for entry in plan:
            metric = entry.get("metric", "")
            command = entry.get("measurement_command", "")
            after_expected = entry.get("after_expected", "")
            threshold = entry.get("threshold", "")

            if not command:
                results.append(
                    {
                        "metric": metric,
                        "command": command,
                        "before_value": "",
                        "after_value": "",
                        "output": "",
                        "expected": after_expected,
                        "passed": False,
                        "reason": "no measurement_command provided",
                    }
                )
                continue

            output, returncode = await self._exec(command, cwd)
            after_value = output.strip()[:500]

            # Look up the actual baseline measurement
            baseline_entry = baseline_by_metric.get(metric)
            before_value = baseline_entry["before_value"] if baseline_entry else ""

            # Determine pass/fail using real before/after delta when available
            passed = False
            reason = ""
            delta = ""

            if baseline_entry and before_value and before_value != "(measurement failed)":
                # Real before/after comparison
                delta = _compute_metric_delta(before_value, after_value)
                passed, reason = _evaluate_metric_change(
                    metric, before_value, after_value, delta, threshold, returncode
                )
            elif after_expected and after_expected.lower() in after_value.lower():
                # Fallback: LLM-guessed after_expected
                passed = True
                reason = "after_expected matched in output"
            elif returncode == 0 and not after_expected:
                passed = True
                reason = "command exited successfully"
            elif returncode == 0 and after_expected:
                if threshold and "exit" in threshold.lower() and "0" in threshold:
                    passed = True
                    reason = "command exited 0 (threshold satisfied)"
                else:
                    reason = "after_expected not found in output"
            else:
                reason = f"command exited with code {returncode}"

            result_entry: dict[str, Any] = {
                "metric": metric,
                "command": command,
                "before_value": before_value,
                "after_value": after_value,
                "output": after_value,
                "expected": after_expected,
                "threshold": threshold,
                "passed": passed,
                "reason": reason,
            }
            if delta:
                result_entry["delta"] = delta
            results.append(result_entry)

            log.info(
                "verification_metric_delta",
                metric=metric,
                before=before_value[:80] if before_value else "(no baseline)",
                after=after_value[:80],
                delta=delta,
                passed=passed,
            )

        log.info(
            "verification_plan_complete",
            total=len(results),
            passed=sum(1 for r in results if r["passed"]),
            failed=sum(1 for r in results if not r["passed"]),
        )
        return tuple(results)

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


# ---------------------------------------------------------------------------
# Structured error detail parsers
# ---------------------------------------------------------------------------


def _parse_typecheck_errors(output: str) -> BenchmarkErrorDetail | None:
    """Detect pyright/mypy errors, extract count and affected file paths."""
    pyright_matches = _PYRIGHT_ERROR_RE.findall(output)
    mypy_matches = _MYPY_ERROR_RE.findall(output)

    all_matches = pyright_matches or mypy_matches
    if not all_matches:
        # Check for a summary line without per-file details
        summary_m = _TYPECHECK_SUMMARY_RE.search(output)
        if not summary_m:
            return None
        count = int(summary_m.group(1))
        # Extract context around the summary
        start = max(0, summary_m.start() - 200)
        end = min(len(output), summary_m.end() + 200)
        summary = output[start:end].strip()[:500]
        return BenchmarkErrorDetail(
            category="typecheck",
            count=count,
            summary=summary,
        )

    file_paths = tuple(dict.fromkeys(m[0] for m in all_matches))
    count = len(all_matches)

    # Build a summary from the first few error lines
    error_lines: list[str] = []
    for line in output.splitlines():
        if _PYRIGHT_ERROR_RE.match(line) or _MYPY_ERROR_RE.match(line):
            error_lines.append(line)
            if len(error_lines) >= 10:
                break
    summary = "\n".join(error_lines)[:500]

    return BenchmarkErrorDetail(
        category="typecheck",
        count=count,
        summary=summary,
        file_paths=file_paths,
    )


def _parse_test_failures(output: str) -> BenchmarkErrorDetail | None:
    """Detect pytest FAILED lines, extract test names as file paths."""
    matches = _PYTEST_FAILED_RE.findall(output)
    if not matches:
        return None

    # Each match is a test node ID like "tests/test_foo.py::test_bar"
    file_paths = tuple(dict.fromkeys(matches))
    count = len(matches)

    # Build summary from the FAILED lines
    failed_lines: list[str] = []
    for line in output.splitlines():
        if line.strip().startswith("FAILED"):
            failed_lines.append(line.strip())
            if len(failed_lines) >= 10:
                break
    summary = "\n".join(failed_lines)[:500]

    return BenchmarkErrorDetail(
        category="test_failure",
        count=count,
        summary=summary,
        file_paths=file_paths,
    )


def _parse_lint_errors(output: str) -> BenchmarkErrorDetail | None:
    """Detect ruff violations, extract count and affected file paths."""
    count_match = _RUFF_VIOLATION_RE.search(output)
    if not count_match:
        return None

    count = int(count_match.group(1))
    if count == 0:
        return None

    file_matches = _RUFF_FILE_RE.findall(output)
    file_paths = tuple(dict.fromkeys(file_matches))

    # Build summary from lint violation lines
    lint_lines: list[str] = []
    for line in output.splitlines():
        if _RUFF_FILE_RE.match(line):
            lint_lines.append(line.strip())
            if len(lint_lines) >= 10:
                break
    summary = "\n".join(lint_lines)[:500]

    return BenchmarkErrorDetail(
        category="lint",
        count=count,
        summary=summary,
        file_paths=file_paths,
    )


def _collect_error_details(output: str, lint_violations: int) -> tuple[BenchmarkErrorDetail, ...]:
    """Collect all structured error details from benchmark output."""
    details: list[BenchmarkErrorDetail] = []

    typecheck = _parse_typecheck_errors(output)
    if typecheck is not None:
        details.append(typecheck)

    test_fail = _parse_test_failures(output)
    if test_fail is not None:
        details.append(test_fail)

    lint = _parse_lint_errors(output)
    if lint is not None:
        details.append(lint)

    return tuple(details)


# ---------------------------------------------------------------------------
# Verification plan delta helpers
# ---------------------------------------------------------------------------


def _try_parse_number(value: str) -> float | None:
    """Try to extract a leading number from a string value."""
    value = value.strip()
    m = re.match(r"^-?[\d.]+", value)
    if m:
        try:
            return float(m.group())
        except ValueError:
            return None
    return None


def _compute_metric_delta(before: str, after: str) -> str:
    """Compute a human-readable delta between two metric values.

    If both values are numeric, returns the numeric difference.
    Otherwise returns a textual comparison indicator.
    """
    before_num = _try_parse_number(before)
    after_num = _try_parse_number(after)
    if before_num is not None and after_num is not None:
        diff = after_num - before_num
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.4g}"
    if before == after:
        return "unchanged"
    return "changed"


def _evaluate_metric_change(
    metric: str,
    before: str,
    after: str,
    delta: str,
    threshold: str,
    returncode: int,
) -> tuple[bool, str]:
    """Evaluate whether a metric change represents a pass or fail.

    Returns (passed, reason).
    """
    before_num = _try_parse_number(before)
    after_num = _try_parse_number(after)

    # If threshold specifies a numeric bound, use it
    if threshold:
        threshold_num = _try_parse_number(threshold)
        if threshold_num is not None and after_num is not None:
            if after_num <= threshold_num:
                return True, f"after ({after_num}) <= threshold ({threshold_num})"
            return False, f"after ({after_num}) > threshold ({threshold_num})"
        if "exit" in threshold.lower() and "0" in threshold:
            passed = returncode == 0
            return passed, f"exit code {returncode} vs threshold exit 0"

    # Numeric comparison: no regression means after >= before (for counts, fewer is better)
    if before_num is not None and after_num is not None:
        # For metrics where "more is better" (test count, coverage) vs
        # "less is better" (errors, violations), we cannot reliably infer
        # direction from the metric name alone. Default: treat no-regression
        # as the value not getting worse (same or command succeeded).
        if returncode == 0:
            return True, f"command succeeded; before={before_num}, after={after_num}"
        return False, f"command failed (exit {returncode}); before={before_num}, after={after_num}"

    # Text comparison
    if before == after:
        return True, "metric unchanged"
    if returncode == 0:
        return True, f"command succeeded; value changed from {before[:40]!r} to {after[:40]!r}"
    return False, f"command failed (exit {returncode}); value changed"


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


# ---------------------------------------------------------------------------
# Delta comparison helpers — count errors from make check output
# ---------------------------------------------------------------------------


def _count_typecheck_errors(stdout: str) -> int:
    """Count typecheck errors (pyright/mypy) from combined make check output.

    Looks for the summary line first (e.g. ``1337 errors found``), falling
    back to counting individual ``error:`` lines.
    """
    summary_m = _TYPECHECK_SUMMARY_RE.search(stdout)
    if summary_m:
        return int(summary_m.group(1))
    # Fallback: count individual error lines
    pyright_count = len(_PYRIGHT_ERROR_RE.findall(stdout))
    mypy_count = len(_MYPY_ERROR_RE.findall(stdout))
    return pyright_count or mypy_count


def _count_lint_violations(stdout: str) -> int:
    """Count lint violations (ruff) from combined make check output.

    Reuses the same regex as ``_parse_ruff``.
    """
    return _parse_ruff(stdout)
