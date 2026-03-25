from __future__ import annotations

import math
import os
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from platform import machine, python_version, system
from typing import Any

__all__ = [
    "BenchmarkEnvironment",
    "BytecodeCounterInstrument",
    "Instrument",
    "LinearRampConfig",
    "LinearRampSampler",
    "SampleResult",
    "SamplingResult",
    "SimpleSampler",
    "WallClockInstrument",
    "calibrate_iterations",
]


# ---------------------------------------------------------------------------
# Abstract Instrument
# ---------------------------------------------------------------------------


class Instrument(ABC):
    """Abstract base for benchmark measurement instruments.

    An instrument encapsulates a single measurement strategy (wall-clock
    timing, instruction counting, etc.) and provides a uniform interface
    for samplers to collect observations.
    """

    @abstractmethod
    def measure(self, fn: Callable[[], Any], iterations: int = 1) -> float:
        """Measure a single sample (*iterations* executions of *fn*).

        Returns:
            Measurement value (seconds for timing instruments, instruction
            count for counting instruments).
        """

    @abstractmethod
    def name(self) -> str:
        """Instrument name for result metadata."""


# ---------------------------------------------------------------------------
# Wall Clock Instrument
# ---------------------------------------------------------------------------


class WallClockInstrument(Instrument):
    """Wall-clock timing using ``time.perf_counter_ns()``.

    Most portable instrument -- works on every platform Python supports.
    Resolution is typically sub-microsecond (nanosecond on modern OSes).

    The measurement returns elapsed *seconds* (float) for consistency with
    the rest of the sampling pipeline, while internally using the highest
    resolution counter available.
    """

    def measure(self, fn: Callable[[], Any], iterations: int = 1) -> float:
        """Return elapsed seconds for *iterations* calls to *fn*."""
        start = time.perf_counter_ns()
        for _ in range(iterations):
            fn()
        elapsed_ns = time.perf_counter_ns() - start
        return elapsed_ns / 1_000_000_000.0

    def name(self) -> str:
        return "wallclock"


# ---------------------------------------------------------------------------
# Bytecode Counter Instrument (Python 3.12+, PEP 669)
# ---------------------------------------------------------------------------


class BytecodeCounterInstrument(Instrument):
    """Deterministic Python bytecode instruction counter using ``sys.monitoring`` (PEP 669).

    100% deterministic and cross-platform (macOS / Linux / Windows).
    Counts only Python bytecode instructions -- C extension calls are
    invisible.  Typical overhead is ~22x compared to normal execution.

    Best suited for algorithmic regression detection where wall-clock noise
    would mask small changes.

    Requires Python 3.12+.

    Reference: PEP 669 -- Low Impact Monitoring for CPython.
    """

    _TOOL_ID: int = 5  # sys.monitoring tool slot (avoid conflict with debuggers)

    def __init__(self) -> None:
        # Verify sys.monitoring is actually available (some stripped builds may
        # lack it even on 3.12+).
        if not hasattr(sys, "monitoring"):
            msg = "sys.monitoring is not available in this Python build"
            raise RuntimeError(msg)

    def measure(self, fn: Callable[[], Any], iterations: int = 1) -> float:
        """Return total bytecode instruction count for *iterations* calls to *fn*.

        The count is returned as a float for interface consistency with timing
        instruments, but the value is always an exact integer.
        """
        mon = sys.monitoring
        tool_id = self._TOOL_ID

        counter = _InstructionCounter()

        # Register our tool and install the callback.
        mon.use_tool_id(tool_id, "hermit_bytecode_counter")
        try:
            mon.set_events(tool_id, mon.events.INSTRUCTION)
            mon.register_callback(
                tool_id,
                mon.events.INSTRUCTION,
                counter.callback,
            )

            for _ in range(iterations):
                fn()

        finally:
            # Always clean up to avoid leaking the monitoring callback.
            mon.set_events(tool_id, 0)
            mon.register_callback(tool_id, mon.events.INSTRUCTION, None)
            mon.free_tool_id(tool_id)

        return float(counter.count)

    def name(self) -> str:
        return "bytecode_instructions"


class _InstructionCounter:
    """Mutable counter for the ``sys.monitoring`` INSTRUCTION callback.

    This is intentionally a mutable class (not a frozen dataclass) because
    the callback must increment the count in-place.  It is scoped to a
    single ``measure()`` invocation and never escapes.
    """

    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count: int = 0

    def callback(self, *_args: object) -> None:
        self.count += 1


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinearRampConfig:
    """Configuration for linear ramp sampling.

    Attributes:
        sample_count: Number of samples (N) to collect.
        measurement_time: Target total measurement time in seconds.
        warmup_time: Warmup phase duration in seconds.
        min_iterations: Minimum iterations per sample.
    """

    sample_count: int = 50
    measurement_time: float = 5.0
    warmup_time: float = 3.0
    min_iterations: int = 1


@dataclass(frozen=True)
class SampleResult:
    """Result of a single sample measurement.

    Attributes:
        iterations: Number of iterations executed in this sample.
        elapsed: Total elapsed time (seconds) or instruction count.
        per_iteration: ``elapsed / iterations``.
    """

    iterations: int
    elapsed: float
    per_iteration: float


@dataclass(frozen=True)
class SamplingResult:
    """Complete sampling result.

    Attributes:
        samples: Tuple of individual sample results.
        per_iteration_times: Tuple of per-iteration measurements across all
            samples.
        slope: OLS slope estimate giving per-iteration time.
        slope_ci: Optional 95% confidence interval on the slope.
        instrument: Name of the instrument used.
        warmup_iterations: Total iterations executed during warmup.
        total_iterations: Total iterations executed during measurement.
        total_elapsed: Total elapsed time during measurement.
    """

    samples: tuple[SampleResult, ...]
    per_iteration_times: tuple[float, ...]
    slope: float
    slope_ci: tuple[float, float] | None
    instrument: str
    warmup_iterations: int
    total_iterations: int
    total_elapsed: float


# ---------------------------------------------------------------------------
# Linear Ramp Sampler (Criterion.rs methodology)
# ---------------------------------------------------------------------------


class LinearRampSampler:
    """Linear ramp sampling strategy inspired by Criterion.rs.

    Collects *N* samples with linearly increasing iteration counts:
    ``[d, 2d, 3d, ..., Nd]``.  The OLS slope of ``elapsed`` vs.
    ``iterations`` gives the per-iteration time, which is superior to
    fixed-iteration sampling because:

    1. All *N* samples contribute simultaneously via regression.
    2. The linear model absorbs constant measurement overhead (intercept).
    3. Bootstrap CI on the slope quantifies measurement uncertainty.

    Reference: Brook Heisler, "Criterion.rs -- Statistics-driven Micro-
    benchmarking in Rust", https://bheisler.github.io/criterion.rs/book/
    """

    def __init__(
        self,
        instrument: Instrument | None = None,
        config: LinearRampConfig | None = None,
    ) -> None:
        self._instrument = instrument if instrument is not None else WallClockInstrument()
        self._config = config if config is not None else LinearRampConfig()

    @property
    def instrument(self) -> Instrument:
        return self._instrument

    @property
    def config(self) -> LinearRampConfig:
        return self._config

    # -- warmup --------------------------------------------------------------

    def warmup(self, fn: Callable[[], Any]) -> tuple[float, int]:
        """Run warmup phase, return ``(mean_execution_time, total_iterations)``.

        Doubles the iteration count each round until *warmup_time* has
        elapsed (wall-clock), regardless of which instrument is in use.
        The mean execution time is derived from the *instrument* so it
        is in the correct unit for subsequent calculations.
        """
        cfg = self._config
        total_warmup_iters = 0
        elapsed_wall = 0.0
        loops = 1
        last_per_iter = 0.0

        while elapsed_wall < cfg.warmup_time:
            wall_start = time.perf_counter()
            measured = self._instrument.measure(fn, iterations=loops)
            wall_end = time.perf_counter()

            elapsed_wall += wall_end - wall_start
            total_warmup_iters += loops
            last_per_iter = measured / loops
            loops *= 2

        # Guard against zero (e.g. bytecode counter on a trivially fast fn).
        mean_exec = max(last_per_iter, 1e-15)
        return mean_exec, total_warmup_iters

    # -- iteration counts ----------------------------------------------------

    def compute_iteration_counts(self, met: float) -> list[int]:
        """Compute linear ramp iteration counts ``[d, 2d, ..., Nd]``.

        The step size *d* is chosen so the total measurement time is close to
        ``config.measurement_time``::

            total_iterations = d * N*(N+1)/2
            total_time ~ met * total_iterations = measurement_time
            => d = ceil(measurement_time / (met * N*(N+1)/2))

        If *met* indicates the function is too slow for a full ramp (i.e.
        ``d`` would be 0), falls back to flat mode with
        ``d = max(1, min_iterations)``.
        """
        cfg = self._config
        n = cfg.sample_count
        triangle = n * (n + 1) / 2.0
        denominator = met * triangle

        if denominator <= 0.0:
            d = max(1, cfg.min_iterations)
        else:
            d = max(1, math.ceil(cfg.measurement_time / denominator))

        d = max(d, cfg.min_iterations)
        return [d * k for k in range(1, n + 1)]

    # -- OLS through origin -------------------------------------------------

    @staticmethod
    def fit_slope(xs: list[float], ys: list[float]) -> float:
        """OLS regression through the origin: ``slope = sum(xi*yi) / sum(xi^2)``.

        For the linear ramp model, the intercept represents constant overhead
        per sample (not per iteration), so regression through the origin on
        *iterations* vs *elapsed* gives the per-iteration cost.

        Returns 0.0 if the denominator is zero.
        """
        sum_xy = sum(xi * yi for xi, yi in zip(xs, ys, strict=True))
        sum_xx = sum(xi * xi for xi in xs)
        if sum_xx == 0.0:
            return 0.0
        return sum_xy / sum_xx

    # -- slope CI via bootstrap (optional) -----------------------------------

    @staticmethod
    def _bootstrap_slope_ci(
        xs: list[float],
        ys: list[float],
        n_boot: int = 5_000,
        confidence: float = 0.95,
        seed: int | None = None,
    ) -> tuple[float, float]:
        """Percentile bootstrap CI on the through-origin OLS slope.

        Resamples ``(x_i, y_i)`` pairs with replacement and re-fits the
        slope for each resample.

        Returns ``(lower, upper)`` bounds.
        """
        import random as _random

        n = len(xs)
        if n < 2:
            slope = LinearRampSampler.fit_slope(xs, ys)
            return (slope, slope)

        rng = _random.Random(seed)
        slopes: list[float] = []
        for _ in range(n_boot):
            indices = [rng.randrange(n) for _ in range(n)]
            bx = [xs[i] for i in indices]
            by = [ys[i] for i in indices]
            slopes.append(LinearRampSampler.fit_slope(bx, by))

        slopes.sort()
        alpha = 1.0 - confidence
        lo = max(0, math.floor((alpha / 2.0) * n_boot))
        hi = min(n_boot - 1, math.floor((1.0 - alpha / 2.0) * n_boot))
        return (slopes[lo], slopes[hi])

    # -- full pipeline -------------------------------------------------------

    def run(self, fn: Callable[[], Any]) -> SamplingResult:
        """Execute the full linear ramp sampling pipeline.

        Steps:
          1. Warmup phase.
          2. Compute linear ramp iteration counts from mean execution time.
          3. Collect *N* samples at linearly increasing iteration counts.
          4. Derive per-iteration times for each sample.
          5. Fit slope via OLS through origin.
          6. Compute bootstrap CI on the slope.
          7. Return comprehensive :class:`SamplingResult`.
        """
        # 1. Warmup
        met, warmup_iters = self.warmup(fn)

        # 2. Iteration counts
        iter_counts = self.compute_iteration_counts(met)

        # 3. Collect samples
        samples: list[SampleResult] = []
        total_iters = 0
        total_elapsed = 0.0

        for iters in iter_counts:
            elapsed = self._instrument.measure(fn, iterations=iters)
            per_iter = elapsed / iters if iters > 0 else elapsed
            samples.append(
                SampleResult(
                    iterations=iters,
                    elapsed=elapsed,
                    per_iteration=per_iter,
                )
            )
            total_iters += iters
            total_elapsed += elapsed

        # 4. Per-iteration times
        per_iter_times = tuple(s.per_iteration for s in samples)

        # 5. OLS slope
        xs = [float(s.iterations) for s in samples]
        ys = [s.elapsed for s in samples]
        slope = self.fit_slope(xs, ys)

        # 6. Bootstrap CI on slope
        slope_ci: tuple[float, float] | None = None
        if len(samples) >= 2:
            slope_ci = self._bootstrap_slope_ci(xs, ys, seed=42)

        return SamplingResult(
            samples=tuple(samples),
            per_iteration_times=per_iter_times,
            slope=slope,
            slope_ci=slope_ci,
            instrument=self._instrument.name(),
            warmup_iterations=warmup_iters,
            total_iterations=total_iters,
            total_elapsed=total_elapsed,
        )


# ---------------------------------------------------------------------------
# Simple Sampler
# ---------------------------------------------------------------------------


class SimpleSampler:
    """Simple fixed-iteration sampler for quick benchmarks.

    Runs ``fn()`` for *rounds* rounds, each with *iterations* iterations.
    Preceded by *warmup_rounds* discarded rounds.  Returns per-iteration
    measurements.

    Use this when the linear ramp is overkill (e.g. quick smoke tests,
    integration benchmarks, or when the function under test is expensive
    enough that a single call provides a stable measurement).
    """

    def __init__(
        self,
        instrument: Instrument | None = None,
        rounds: int = 20,
        iterations: int = 1,
        warmup_rounds: int = 5,
    ) -> None:
        self._instrument = instrument if instrument is not None else WallClockInstrument()
        self._rounds = max(1, rounds)
        self._iterations = max(1, iterations)
        self._warmup_rounds = max(0, warmup_rounds)

    @property
    def instrument(self) -> Instrument:
        return self._instrument

    def run(self, fn: Callable[[], Any]) -> SamplingResult:
        """Execute simple sampling.

        1. Run *warmup_rounds* discarded rounds.
        2. Run *rounds* measured rounds.
        3. Compute per-iteration times.
        4. Fit a flat slope (mean of per-iteration times).
        5. Return :class:`SamplingResult`.
        """
        iters = self._iterations

        # 1. Warmup
        warmup_iters = 0
        for _ in range(self._warmup_rounds):
            self._instrument.measure(fn, iterations=iters)
            warmup_iters += iters

        # 2. Measurement rounds
        samples: list[SampleResult] = []
        total_elapsed = 0.0
        total_iters = 0

        for _ in range(self._rounds):
            elapsed = self._instrument.measure(fn, iterations=iters)
            per_iter = elapsed / iters if iters > 0 else elapsed
            samples.append(
                SampleResult(
                    iterations=iters,
                    elapsed=elapsed,
                    per_iteration=per_iter,
                )
            )
            total_elapsed += elapsed
            total_iters += iters

        # 3. Per-iteration times
        per_iter_times = tuple(s.per_iteration for s in samples)

        # 4. Slope = mean per-iteration time (flat, not ramped).
        slope = sum(per_iter_times) / len(per_iter_times) if per_iter_times else 0.0

        return SamplingResult(
            samples=tuple(samples),
            per_iteration_times=per_iter_times,
            slope=slope,
            slope_ci=None,
            instrument=self._instrument.name(),
            warmup_iterations=warmup_iters,
            total_iterations=total_iters,
            total_elapsed=total_elapsed,
        )


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def calibrate_iterations(
    fn: Callable[[], Any],
    instrument: Instrument | None = None,
    min_time: float = 0.1,
    max_loops: int = 2**24,
) -> int:
    """Determine loop count so each measurement takes >= *min_time*.

    Algorithm: start at 1 iteration, double until ``instrument.measure(fn, loops)``
    yields a value >= *min_time*.  For timing instruments this means the
    measurement takes at least *min_time* seconds.

    Args:
        fn: The function to benchmark.
        instrument: Measurement instrument (defaults to
            :class:`WallClockInstrument`).
        min_time: Minimum target measurement value per sample.
        max_loops: Upper bound to prevent runaway calibration.

    Returns:
        Number of iterations per sample.

    Raises:
        RuntimeError: If *max_loops* is reached without meeting *min_time*.
    """
    inst = instrument if instrument is not None else WallClockInstrument()
    loops = 1

    while loops <= max_loops:
        measured = inst.measure(fn, iterations=loops)
        if measured >= min_time:
            return loops
        loops *= 2

    msg = (
        f"calibration did not converge: {max_loops} loops still under "
        f"{min_time} target ({inst.name()})"
    )
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Environment Info
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkEnvironment:
    """Captured benchmark execution environment.

    Attributes:
        python_version: Python version string (e.g. ``"3.13.1"``).
        platform_system: OS name (e.g. ``"Darwin"``, ``"Linux"``).
        platform_machine: CPU architecture (e.g. ``"arm64"``, ``"x86_64"``).
        cpu_count: Number of logical CPUs, or ``None`` if unavailable.
        instrument: Name of the measurement instrument.
        timestamp: ISO 8601 UTC timestamp of capture.
    """

    python_version: str
    platform_system: str
    platform_machine: str
    cpu_count: int | None
    instrument: str
    timestamp: str

    @staticmethod
    def capture(instrument_name: str) -> BenchmarkEnvironment:
        """Capture the current execution environment.

        Args:
            instrument_name: Name of the instrument in use (e.g.
                ``"wallclock"``).

        Returns:
            A frozen :class:`BenchmarkEnvironment` snapshot.
        """
        return BenchmarkEnvironment(
            python_version=python_version(),
            platform_system=system(),
            platform_machine=machine(),
            cpu_count=os.cpu_count(),
            instrument=instrument_name,
            timestamp=datetime.now(UTC).isoformat(),
        )
