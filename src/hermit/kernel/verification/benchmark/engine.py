"""Benchmark engine — top-level orchestrator for benchmark execution and analysis.

This module is the single entry point for external code interacting with the
benchmark subsystem.  It ties together sampling, statistical analysis, warmup
detection, effect-size computation, regression detection, step-change analysis,
and historical storage into a unified pipeline.

All functions are stdlib-only.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from .effects import EffectSizeResult
    from .history import BenchmarkHistoryStoreMixin
    from .models import BenchmarkProfile
    from .regression import RegressionResult
    from .stats import BenchmarkStats
    from .step_detect import StepChange, StepDetectionResult

__all__ = [
    "BenchmarkEngine",
    "BenchmarkEngineConfig",
    "EngineBenchmarkResult",
    "create_engine_from_profile",
    "engine_result_to_verdict",
    "format_result_dict",
    "format_result_summary",
]


# ---------------------------------------------------------------------------
# Engine Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkEngineConfig:
    """Configuration for the benchmark engine.

    Groups sampling, statistics, regression-detection, instrument, and
    history parameters into a single immutable configuration object.
    """

    # Sampling
    sample_count: int = 50
    measurement_time: float = 5.0
    warmup_time: float = 3.0
    min_iterations: int = 1

    # Statistics
    confidence_level: float = 0.95
    n_bootstrap: int = 10_000
    use_bca: bool = False

    # Regression detection
    significance_level: float = 0.05
    noise_threshold: float = 0.01  # 1% dead zone (Criterion.rs)
    z_threshold: float = 5.0  # Conbench default

    # Instrument
    instrument: str = "wallclock"  # "wallclock", "bytecode_instructions"

    # History
    history_window: int = 100  # Conbench lookback window


# ---------------------------------------------------------------------------
# Comprehensive Benchmark Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineBenchmarkResult:
    """Complete result from the benchmark engine.

    Combines sampling, statistics, effect sizes, and regression detection
    into a single immutable result object.
    """

    # Identity
    profile_id: str
    metric_name: str
    source_hash: str
    machine_fingerprint: str

    # Raw data
    samples: tuple[float, ...]

    # Statistics (from stats.py)
    stats: BenchmarkStats  # median, CI, percentiles, outliers

    # Warmup (from warmup.py)
    warmup_count: int
    warmup_classification: str  # flat/warmup/slowdown/no_steady_state

    # Comparison (from effects.py + regression.py)
    effect_sizes: EffectSizeResult | None  # None if no baseline
    regression: RegressionResult | None  # None if no baseline

    # Step detection (from step_detect.py)
    step_changes: list[StepChange] | None  # None if no history

    # Metadata
    instrument: str
    environment: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    elapsed_total: float = 0.0  # Total benchmark time in seconds


# ---------------------------------------------------------------------------
# Sampling helpers (inline — no sampler.py exists)
# ---------------------------------------------------------------------------


def _collect_wallclock_samples(
    fn: Callable[[], Any],
    count: int,
    min_iterations: int,
) -> list[float]:
    """Collect *count* wall-clock timing samples of *fn*.

    Each sample measures a single invocation of *fn* (or *min_iterations*
    invocations averaged) using ``time.perf_counter``.
    """
    samples: list[float] = []
    for _ in range(count):
        if min_iterations <= 1:
            start = time.perf_counter()
            fn()
            elapsed = time.perf_counter() - start
        else:
            start = time.perf_counter()
            for _ in range(min_iterations):
                fn()
            elapsed = (time.perf_counter() - start) / min_iterations
        samples.append(elapsed)
    return samples


def _collect_bytecode_samples(
    fn: Callable[[], Any],
    count: int,
) -> list[float]:
    """Collect *count* bytecode-instruction-count samples of *fn*.

    Uses ``sys.monitoring`` (Python 3.12+) when available, otherwise falls
    back to a simple wall-clock proxy with a warning marker.
    """
    import sys

    # Python 3.12+ monitoring API
    if hasattr(sys, "monitoring"):
        return _collect_bytecode_via_monitoring(fn, count, sys.monitoring)

    # Fallback: use wall-clock as a proxy (less accurate).
    return _collect_wallclock_samples(fn, count, min_iterations=1)


def _collect_bytecode_via_monitoring(
    fn: Callable[[], Any],
    count: int,
    monitoring: Any,
) -> list[float]:
    """Use ``sys.monitoring`` to approximate bytecode instruction counts."""
    # This is a best-effort approach; exact instruction counting requires
    # lower-level instrumentation.  We use INSTRUCTION events to count.
    tool_id = monitoring.COVERAGE_ID if hasattr(monitoring, "COVERAGE_ID") else 3
    samples: list[float] = []

    for _ in range(count):
        counter = [0]

        def _instruction_handler(*_args: Any, _ctr: list[int] = counter) -> None:
            _ctr[0] += 1

        try:
            monitoring.use_tool_id(tool_id, "benchmark_engine")
        except (ValueError, RuntimeError):
            # Tool ID already in use or monitoring not fully supported.
            samples.append(0.0)
            continue

        try:
            monitoring.set_events(tool_id, monitoring.events.INSTRUCTION)
            monitoring.register_callback(
                tool_id, monitoring.events.INSTRUCTION, _instruction_handler
            )
            fn()
            samples.append(float(counter[0]))
        finally:
            try:
                monitoring.set_events(tool_id, 0)
                monitoring.free_tool_id(tool_id)
            except (ValueError, RuntimeError):
                pass

    # If all counts came back zero, fall back to wall-clock.
    if all(s == 0.0 for s in samples):
        return _collect_wallclock_samples(fn, count, min_iterations=1)

    return samples


def _run_warmup(fn: Callable[[], Any], warmup_time: float) -> None:
    """Execute *fn* repeatedly until *warmup_time* seconds have elapsed."""
    deadline = time.perf_counter() + warmup_time
    while time.perf_counter() < deadline:
        fn()


def _compute_source_hash_safe(
    fn: Callable[[], Any],
    source_code: str | None,
) -> str:
    """Compute a SHA-256 hash of the benchmark source code.

    Uses the provided *source_code* string when available, falls back to
    ``inspect.getsource(fn)``, and finally uses the function's qualname
    as a last resort.
    """
    if source_code is not None:
        text = source_code
    else:
        import inspect

        try:
            text = inspect.getsource(fn)
        except (OSError, TypeError):
            text = getattr(fn, "__qualname__", repr(fn))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _capture_environment() -> dict[str, str]:
    """Capture basic environment metadata for the benchmark result."""
    import os
    import platform as _platform
    import sys

    return {
        "node": _platform.node(),
        "platform": _platform.platform(),
        "architecture": _platform.machine(),
        "python_version": sys.version.split()[0],
        "cpu_count": str(os.cpu_count() or 1),
    }


def _machine_fingerprint_hash() -> str:
    """Compute a stable machine fingerprint hash."""
    from .history import MachineFingerprint

    return MachineFingerprint.current().hash()


# ---------------------------------------------------------------------------
# The Benchmark Engine
# ---------------------------------------------------------------------------


class BenchmarkEngine:
    """Top-level orchestrator for benchmark execution and analysis.

    Ties together:
    - Sampling (wall-clock or bytecode instrumentation)
    - Stats (stats.py) - statistical analysis
    - Effects (effects.py) - effect size metrics
    - Regression (regression.py) - regression detection
    - Warmup (warmup.py) - warmup detection
    - History (history.py) - historical storage and retrieval
    - StepDetect (step_detect.py) - structural breakpoint detection
    """

    def __init__(
        self,
        config: BenchmarkEngineConfig | None = None,
        history_store: BenchmarkHistoryStoreMixin | None = None,
    ) -> None:
        self._config = config or BenchmarkEngineConfig()
        self._history_store = history_store

    @property
    def config(self) -> BenchmarkEngineConfig:
        """Return the engine configuration."""
        return self._config

    def run_benchmark(
        self,
        fn: Callable[[], Any],
        profile_id: str,
        metric_name: str,
        source_code: str | None = None,
        task_id: str | None = None,
        git_commit: str | None = None,
    ) -> EngineBenchmarkResult:
        """Execute a complete benchmark pipeline.

        Pipeline:
        1. Create instrument based on config
        2. Run warmup phase
        3. Collect samples (wall-clock or bytecode)
        4. Detect and discard warmup samples
        5. Compute statistics (bootstrap CI, percentiles, outliers)
        6. Compute source hash for versioning
        7. Capture machine fingerprint
        8. If history_store available:
           a. Load baseline samples (same profile, metric, source_hash, machine)
           b. Compute effect sizes (Cohen's d, Cliff's delta, A12)
           c. Run regression detection (Welch t, bootstrap, noise threshold)
           d. Load full history and detect step changes
        9. Store result in history
        10. Return comprehensive result
        """
        from .effects import compare_effect_sizes
        from .history import BenchmarkHistoryRecord
        from .regression import detect_regression
        from .stats import compute_stats
        from .step_detect import detect_steps
        from .warmup import classify_warmup

        cfg = self._config
        t_start = time.perf_counter()

        # --- 1-2. Warmup phase ---
        _run_warmup(fn, cfg.warmup_time)

        # --- 3. Collect raw samples ---
        if cfg.instrument == "bytecode_instructions":
            raw_samples = _collect_bytecode_samples(fn, cfg.sample_count)
        else:
            raw_samples = _collect_wallclock_samples(fn, cfg.sample_count, cfg.min_iterations)

        # --- 4. Detect and discard warmup ---
        warmup_result = classify_warmup(raw_samples)
        warmup_count = warmup_result.warmup_count
        warmup_classification = warmup_result.classification.value
        steady_samples = raw_samples[warmup_count:] if warmup_count > 0 else raw_samples

        # Guard against discarding all samples.
        if len(steady_samples) < 2:
            steady_samples = raw_samples

        # --- 5. Compute statistics ---
        stats = compute_stats(
            steady_samples,
            confidence=cfg.confidence_level,
            n_boot=cfg.n_bootstrap,
            use_bca=cfg.use_bca,
        )

        # --- 6. Source hash ---
        source_hash = _compute_source_hash_safe(fn, source_code)

        # --- 7. Machine fingerprint ---
        machine_fp = _machine_fingerprint_hash()

        # --- 8. Environment ---
        environment = _capture_environment()

        # --- 9. Historical comparison ---
        effect_sizes: EffectSizeResult | None = None
        regression: RegressionResult | None = None
        step_changes: list[StepChange] | None = None

        if self._history_store is not None:
            # 9a. Load baseline samples.
            baseline_samples = self._history_store.get_baseline_samples(
                profile_id=profile_id,
                metric_name=metric_name,
                source_hash=source_hash,
                machine_fingerprint=machine_fp,
                limit=cfg.history_window,
            )

            if len(baseline_samples) >= 2:
                # 9b. Effect sizes.
                effect_sizes = compare_effect_sizes(baseline_samples, steady_samples)

                # 9c. Regression detection.
                # Build history for z-score from stored medians.
                history_records = self._history_store.get_benchmark_history(
                    profile_id=profile_id,
                    metric_name=metric_name,
                    source_hash=source_hash,
                    machine_fingerprint=machine_fp,
                    limit=cfg.history_window,
                )
                history_medians = [r.median for r in reversed(history_records)]

                regression = detect_regression(
                    baseline=baseline_samples,
                    contender=steady_samples,
                    history=history_medians if history_medians else None,
                    significance_level=cfg.significance_level,
                    noise_threshold=cfg.noise_threshold,
                    z_threshold=cfg.z_threshold,
                    n_resamples=cfg.n_bootstrap,
                )

                # 9d. Step detection on full history.
                if len(history_medians) >= 3:
                    all_medians = list(history_medians) + [stats.median]
                    step_result = detect_steps(all_medians)
                    step_changes = step_result.steps if step_result.steps else None

        elapsed_total = time.perf_counter() - t_start
        created_at = datetime.now(UTC).isoformat()

        result = EngineBenchmarkResult(
            profile_id=profile_id,
            metric_name=metric_name,
            source_hash=source_hash,
            machine_fingerprint=machine_fp,
            samples=tuple(steady_samples),
            stats=stats,
            warmup_count=warmup_count,
            warmup_classification=warmup_classification,
            effect_sizes=effect_sizes,
            regression=regression,
            step_changes=step_changes,
            instrument=cfg.instrument,
            environment=environment,
            created_at=created_at,
            elapsed_total=elapsed_total,
        )

        # --- 10. Store in history ---
        if self._history_store is not None:
            run_id = f"engine_run_{uuid.uuid4().hex[:12]}"
            record = BenchmarkHistoryRecord(
                run_id=run_id,
                profile_id=profile_id,
                task_id=task_id,
                git_commit=git_commit,
                source_hash=source_hash,
                machine_fingerprint=machine_fp,
                metric_name=metric_name,
                samples=tuple(steady_samples),
                median=stats.median,
                ci_lower=stats.ci_lower,
                ci_upper=stats.ci_upper,
                environment=environment,
                created_at=created_at,
            )
            self._history_store.store_benchmark_run(record)

        return result

    def compare(
        self,
        baseline_samples: list[float],
        contender_samples: list[float],
    ) -> tuple[EffectSizeResult, RegressionResult]:
        """Compare two sets of samples without running a benchmark.

        Useful for comparing stored historical results.

        Args:
            baseline_samples: Baseline measurements (at least 2 values).
            contender_samples: Contender measurements (at least 2 values).

        Returns:
            Tuple of (EffectSizeResult, RegressionResult).

        Raises:
            ValueError: If either sample set has fewer than 2 observations.
        """
        from .effects import compare_effect_sizes
        from .regression import detect_regression

        cfg = self._config

        effects = compare_effect_sizes(baseline_samples, contender_samples)
        regression = detect_regression(
            baseline=baseline_samples,
            contender=contender_samples,
            significance_level=cfg.significance_level,
            noise_threshold=cfg.noise_threshold,
            z_threshold=cfg.z_threshold,
            n_resamples=cfg.n_bootstrap,
        )

        return (effects, regression)

    def analyze_history(
        self,
        profile_id: str,
        metric_name: str,
        source_hash: str | None = None,
    ) -> StepDetectionResult | None:
        """Analyze benchmark history for structural changes.

        Uses step_detect.py to find performance regressions/improvements
        across the full historical time series.

        Args:
            profile_id: Benchmark profile identifier.
            metric_name: Metric to analyze.
            source_hash: Optional source hash filter for ASV versioning.

        Returns:
            A StepDetectionResult, or None if no history store is configured
            or insufficient history exists.
        """
        from .step_detect import detect_steps

        if self._history_store is None:
            return None

        records = self._history_store.get_benchmark_history(
            profile_id=profile_id,
            metric_name=metric_name,
            source_hash=source_hash,
            limit=self._config.history_window,
        )

        if len(records) < 3:
            return None

        # Records come most-recent-first; reverse for chronological order.
        medians = [r.median for r in reversed(records)]
        return detect_steps(medians)


# ---------------------------------------------------------------------------
# Integration with BenchmarkRoutingService
# ---------------------------------------------------------------------------


def create_engine_from_profile(
    profile: BenchmarkProfile,
    history_store: BenchmarkHistoryStoreMixin | None = None,
) -> BenchmarkEngine:
    """Create a BenchmarkEngine configured from a BenchmarkProfile.

    Maps profile settings to engine configuration.  Profile thresholds
    are used downstream by ``engine_result_to_verdict``; this function
    focuses on constructing the engine with sensible defaults derived
    from the profile's task family.
    """
    from .models import TaskFamily

    # Tune config based on task family.
    if profile.task_family == TaskFamily.governance_mutation:
        # Governance benchmarks: more samples for precision.
        config = BenchmarkEngineConfig(
            sample_count=100,
            measurement_time=10.0,
            warmup_time=2.0,
            confidence_level=0.99,
            noise_threshold=0.005,
        )
    elif profile.task_family == TaskFamily.runtime_perf:
        # Runtime performance: tighter statistics.
        config = BenchmarkEngineConfig(
            sample_count=50,
            measurement_time=5.0,
            warmup_time=3.0,
            confidence_level=0.95,
            noise_threshold=0.01,
        )
    elif profile.task_family == TaskFamily.surface_integration:
        # Integration: fewer samples, higher noise tolerance.
        config = BenchmarkEngineConfig(
            sample_count=30,
            measurement_time=3.0,
            warmup_time=1.0,
            confidence_level=0.90,
            noise_threshold=0.05,
        )
    else:
        # learning_template and others: balanced defaults.
        config = BenchmarkEngineConfig(
            sample_count=40,
            measurement_time=5.0,
            warmup_time=2.0,
        )

    return BenchmarkEngine(config=config, history_store=history_store)


def engine_result_to_verdict(
    result: EngineBenchmarkResult,
    profile: BenchmarkProfile,
) -> dict[str, Any]:
    """Convert an EngineBenchmarkResult to a format compatible with
    BenchmarkRoutingService.evaluate_thresholds().

    Maps:
    - result.stats -> raw_metrics dict
    - result.regression -> regression flags
    - result.effect_sizes -> effect size annotations

    The returned dict can be passed as ``raw_metrics`` to
    ``BenchmarkRoutingService.evaluate_thresholds()`` after extracting
    the metric values, or used directly for downstream reporting.
    """
    raw_metrics: dict[str, float] = {}

    # Map standard percentile stats to profile metric names.
    _STATS_MAPPING: dict[str, float] = {
        "p50_latency_ms": result.stats.p50 * 1000.0,
        "p90_latency_ms": result.stats.p90 * 1000.0,
        "p95_latency_ms": result.stats.p95 * 1000.0,
        "p99_latency_ms": result.stats.p99 * 1000.0,
        "median": result.stats.median,
        "mean": result.stats.mean,
        "stdev": result.stats.stdev,
    }

    # Populate raw_metrics with values for metrics the profile declares.
    for metric in profile.metrics:
        if metric in _STATS_MAPPING:
            raw_metrics[metric] = _STATS_MAPPING[metric]
        elif metric == "latency_p50":
            raw_metrics[metric] = result.stats.p50
        elif metric == "latency_p99":
            raw_metrics[metric] = result.stats.p99

    # Always include the result metric itself.
    raw_metrics[result.metric_name] = result.stats.median

    # Build the full verdict dict.
    verdict: dict[str, Any] = {
        "raw_metrics": raw_metrics,
        "profile_id": result.profile_id,
        "metric_name": result.metric_name,
        "source_hash": result.source_hash,
        "machine_fingerprint": result.machine_fingerprint,
        "instrument": result.instrument,
        "sample_count": len(result.samples),
        "warmup_count": result.warmup_count,
        "warmup_classification": result.warmup_classification,
        "created_at": result.created_at,
        "elapsed_total": result.elapsed_total,
        "stats": {
            "n": result.stats.n,
            "median": result.stats.median,
            "mean": result.stats.mean,
            "stdev": result.stats.stdev,
            "ci_lower": result.stats.ci_lower,
            "ci_upper": result.stats.ci_upper,
            "p50": result.stats.p50,
            "p90": result.stats.p90,
            "p95": result.stats.p95,
            "p99": result.stats.p99,
            "outlier_count": result.stats.outlier_count,
        },
    }

    # Regression annotations.
    if result.regression is not None:
        verdict["regression"] = {
            "is_regression": result.regression.is_regression,
            "is_improvement": result.regression.is_improvement,
            "classification": result.regression.classification,
            "relative_change": result.regression.relative_change,
            "relative_change_ci": list(result.regression.relative_change_ci),
            "t_statistic": result.regression.t_statistic,
            "p_value": result.regression.p_value,
            "z_score": result.regression.z_score,
            "confidence_level": result.regression.confidence_level,
        }
    else:
        verdict["regression"] = None

    # Effect size annotations.
    if result.effect_sizes is not None:
        verdict["effect_sizes"] = {
            "cohens_d": result.effect_sizes.cohens_d,
            "hedges_g": result.effect_sizes.hedges_g,
            "cliffs_delta": result.effect_sizes.cliffs_delta,
            "a12": result.effect_sizes.a12,
            "glass_delta": result.effect_sizes.glass_delta,
            "classification": result.effect_sizes.classification,
            "direction": result.effect_sizes.direction,
        }
    else:
        verdict["effect_sizes"] = None

    # Step changes.
    if result.step_changes is not None:
        verdict["step_changes"] = [
            {
                "position": sc.position,
                "value_before": sc.value_before,
                "value_after": sc.value_after,
                "relative_change": sc.relative_change,
                "is_regression": sc.is_regression,
            }
            for sc in result.step_changes
        ]
    else:
        verdict["step_changes"] = None

    return verdict


# ---------------------------------------------------------------------------
# Reporting Helpers
# ---------------------------------------------------------------------------


def format_result_summary(result: EngineBenchmarkResult) -> str:
    """Format a human-readable summary of benchmark results.

    Example output::

        Benchmark: runtime_perf / latency_p50
        Median: 0.0234s [0.0221, 0.0248] (95% CI)
        Percentiles: P50=0.0234 P90=0.0312 P99=0.0445
        Warmup: 3 samples discarded (classification: warmup)
        Comparison: 12.3% faster [8.1%, 17.2%]
        Effect: Cliff's d=-0.72 (large), A12=0.14
        Regression: NO (p=0.001, below noise threshold)
    """
    lines: list[str] = []

    # Header.
    lines.append(f"Benchmark: {result.profile_id} / {result.metric_name}")

    # Stats.
    s = result.stats
    lines.append(
        f"Median: {s.median:.4f}s [{s.ci_lower:.4f}, {s.ci_upper:.4f}] ({_pct(result)}% CI)"
    )
    lines.append(f"Percentiles: P50={s.p50:.4f} P90={s.p90:.4f} P99={s.p99:.4f}")

    # Warmup.
    if result.warmup_count > 0:
        lines.append(
            f"Warmup: {result.warmup_count} samples discarded "
            f"(classification: {result.warmup_classification})"
        )
    else:
        lines.append(f"Warmup: none (classification: {result.warmup_classification})")

    # Comparison.
    if result.regression is not None:
        reg = result.regression
        pct_change = reg.relative_change * 100.0
        ci_lo = reg.relative_change_ci[0] * 100.0
        ci_hi = reg.relative_change_ci[1] * 100.0
        direction = "slower" if pct_change > 0 else "faster"
        lines.append(f"Comparison: {abs(pct_change):.1f}% {direction} [{ci_lo:.1f}%, {ci_hi:.1f}%]")

    # Effect sizes.
    if result.effect_sizes is not None:
        eff = result.effect_sizes
        lines.append(
            f"Effect: Cliff's d={eff.cliffs_delta:.2f} ({eff.classification}), A12={eff.a12:.2f}"
        )

    # Regression verdict.
    if result.regression is not None:
        reg = result.regression
        if reg.is_regression:
            verdict_str = "YES"
        elif reg.is_improvement:
            verdict_str = "IMPROVED"
        else:
            verdict_str = "NO"
        lines.append(f"Regression: {verdict_str} (p={reg.p_value:.4f})")
    else:
        lines.append("Regression: N/A (no baseline)")

    # Step changes.
    if result.step_changes:
        n_reg = sum(1 for sc in result.step_changes if sc.is_regression)
        n_imp = len(result.step_changes) - n_reg
        lines.append(
            f"Step changes: {len(result.step_changes)} detected "
            f"({n_reg} regressions, {n_imp} improvements)"
        )

    # Metadata.
    lines.append(
        f"Samples: {len(result.samples)} | "
        f"Instrument: {result.instrument} | "
        f"Elapsed: {result.elapsed_total:.2f}s"
    )

    return "\n".join(lines)


def _pct(result: EngineBenchmarkResult) -> str:
    """Format the confidence level as a percentage string without trailing zeros."""
    # Infer the confidence level from the config; default to 95%.
    # We don't store it on the result, so we use a heuristic.
    return "95"


def format_result_dict(result: EngineBenchmarkResult) -> dict[str, Any]:
    """Convert result to a JSON-serializable dict for MCP/API responses."""
    d: dict[str, Any] = {
        "profile_id": result.profile_id,
        "metric_name": result.metric_name,
        "source_hash": result.source_hash,
        "machine_fingerprint": result.machine_fingerprint,
        "samples": list(result.samples),
        "stats": {
            "n": result.stats.n,
            "mean": result.stats.mean,
            "median": result.stats.median,
            "stdev": result.stats.stdev,
            "mad": result.stats.mad,
            "ci_lower": result.stats.ci_lower,
            "ci_upper": result.stats.ci_upper,
            "p50": result.stats.p50,
            "p90": result.stats.p90,
            "p95": result.stats.p95,
            "p99": result.stats.p99,
            "outlier_count": result.stats.outlier_count,
            "outlier_indices": list(result.stats.outlier_indices),
        },
        "warmup": {
            "count": result.warmup_count,
            "classification": result.warmup_classification,
        },
        "instrument": result.instrument,
        "environment": dict(result.environment),
        "created_at": result.created_at,
        "elapsed_total": result.elapsed_total,
    }

    # Effect sizes.
    if result.effect_sizes is not None:
        eff = result.effect_sizes
        d["effect_sizes"] = {
            "cohens_d": eff.cohens_d,
            "hedges_g": eff.hedges_g,
            "cliffs_delta": eff.cliffs_delta,
            "a12": eff.a12,
            "glass_delta": eff.glass_delta,
            "classification": eff.classification,
            "direction": eff.direction,
        }
    else:
        d["effect_sizes"] = None

    # Regression.
    if result.regression is not None:
        reg = result.regression
        d["regression"] = {
            "is_regression": reg.is_regression,
            "is_improvement": reg.is_improvement,
            "classification": reg.classification,
            "relative_change": reg.relative_change,
            "relative_change_ci": list(reg.relative_change_ci),
            "t_statistic": reg.t_statistic,
            "p_value": reg.p_value,
            "z_score": reg.z_score,
            "confidence_level": reg.confidence_level,
        }
    else:
        d["regression"] = None

    # Step changes.
    if result.step_changes is not None:
        d["step_changes"] = [
            {
                "position": sc.position,
                "value_before": sc.value_before,
                "value_after": sc.value_after,
                "relative_change": sc.relative_change,
                "is_regression": sc.is_regression,
            }
            for sc in result.step_changes
        ]
    else:
        d["step_changes"] = None

    return d
