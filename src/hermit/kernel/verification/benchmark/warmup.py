from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "WarmupClassification",
    "WarmupResult",
    "calibrate_iterations",
    "classify_warmup",
    "cusum_changepoint",
    "cusum_with_threshold",
    "detect_warmup_split_half",
    "detect_warmup_trend",
    "split_half_stable",
]

# ---------------------------------------------------------------------------
# Local helpers (duplicated from stats.py to avoid circular imports)
# ---------------------------------------------------------------------------


def _percentile(sorted_data: list[float], p: float) -> float:
    """Linear interpolation quantile (Method 7, Hyndman & Fan 1996).

    Expects *sorted_data* to be pre-sorted in ascending order.
    *p* must be in [0, 1].
    """
    n = len(sorted_data)
    if n == 1:
        return sorted_data[0]
    idx = p * (n - 1)
    lo = math.floor(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_data[lo] * (1.0 - frac) + sorted_data[hi] * frac


def _mad(data: list[float]) -> float:
    """Median Absolute Deviation: MAD = median(|x_i - median(x)|).

    Returns 0.0 for fewer than 2 data points.
    """
    if len(data) < 2:
        return 0.0
    med = statistics.median(data)
    return statistics.median([abs(x - med) for x in data])


# ---------------------------------------------------------------------------
# 1. Split-Half Stability Test (pyperf algorithm)
# ---------------------------------------------------------------------------


def split_half_stable(
    sample1: list[float],
    sample2: list[float],
    mean_threshold: float = 0.10,
    mad_threshold: float = 0.10,
    quartile_threshold: float = 0.05,
) -> bool:
    """Test if two halves are from the same distribution (pyperf method).

    Compares location and spread of *sample1* against *sample2* using four
    relative-difference checks:

    - ``|mean(s1) - mean(s2)| / mean(s2)`` must be in ``[-0.5, mean_threshold]``
    - ``|mad(s1) - mad(s2)| / mad(s2)`` must be ``<= mad_threshold``
    - ``|Q1(s1) - Q1(s2)| / Q1(s2)`` must be ``<= quartile_threshold``
    - ``|Q3(s1) - Q3(s2)| / Q3(s2)`` must be ``<= quartile_threshold``

    Reference: pyperf (Victor Stinner), ``_bench_suite.py`` stability check.

    Args:
        sample1: First half of the observations.
        sample2: Second half (reference).
        mean_threshold: Maximum relative difference in means.
        mad_threshold: Maximum relative difference in MAD.
        quartile_threshold: Maximum relative difference in Q1 and Q3.

    Returns:
        ``True`` if ALL checks pass (halves are statistically compatible).
    """
    if not sample1 or not sample2:
        return False

    mean1 = statistics.mean(sample1)
    mean2 = statistics.mean(sample2)

    # Mean check — reference (mean2) must be positive for relative comparison.
    if mean2 == 0.0:
        # When reference mean is zero, only pass if both means are zero.
        if mean1 != 0.0:
            return False
    else:
        rel_mean = abs(mean1 - mean2) / abs(mean2)
        if rel_mean > mean_threshold:
            return False

    # MAD check.
    mad1 = _mad(sample1)
    mad2 = _mad(sample2)
    if mad2 == 0.0:
        # Zero MAD means all values identical; require the same of sample1.
        if mad1 != 0.0:
            return False
    else:
        if abs(mad1 - mad2) / mad2 > mad_threshold:
            return False

    # Quartile checks — Q1 and Q3.
    sorted1 = sorted(sample1)
    sorted2 = sorted(sample2)

    q1_s1 = _percentile(sorted1, 0.25)
    q1_s2 = _percentile(sorted2, 0.25)
    q3_s1 = _percentile(sorted1, 0.75)
    q3_s2 = _percentile(sorted2, 0.75)

    for ref, val in ((q1_s2, q1_s1), (q3_s2, q3_s1)):
        if ref == 0.0:
            if val != 0.0:
                return False
        else:
            if abs(val - ref) / abs(ref) > quartile_threshold:
                return False

    return True


# ---------------------------------------------------------------------------
# 2. Warmup Detection via Split-Half (pyperf)
# ---------------------------------------------------------------------------


def detect_warmup_split_half(
    values: list[float],
    sample_size: int = 20,
    max_warmup: int = 300,
) -> int:
    """Detect number of warmup values to discard using pyperf's split-half method.

    Algorithm:
      1. Start with ``nwarmup = 1``.
      2. Split post-warmup values into two adjacent halves of size
         *sample_size*.
      3. Test stability via :func:`split_half_stable`.
      4. If unstable, increment ``nwarmup`` and retry.
      5. Fail (return ``nwarmup``) if ``nwarmup > max_warmup`` or not enough
         values remain for two halves.

    Reference: pyperf (Victor Stinner), ``_warmup.py``.

    Args:
        values: Raw benchmark observations.  Not mutated.
        sample_size: Number of observations per half.
        max_warmup: Upper bound on warmup iterations.

    Returns:
        Number of warmup values to discard.  Returns 0 when fewer than
        ``2 * sample_size`` values are available.
    """
    n = len(values)
    min_required = 2 * sample_size

    if n < min_required:
        return 0

    for nwarmup in range(n - min_required + 1):
        if nwarmup > max_warmup:
            break

        rest = values[nwarmup:]
        half = len(rest) // 2
        if half < sample_size:
            break

        s1 = rest[:sample_size]
        s2 = rest[half : half + sample_size]

        if split_half_stable(s1, s2):
            return nwarmup

    # Could not find a stable split; return the last attempted warmup count.
    return min(nwarmup, n - min_required) if n > min_required else 0


# ---------------------------------------------------------------------------
# 3. CUSUM Changepoint Detection (Page 1954)
# ---------------------------------------------------------------------------


def cusum_changepoint(values: list[float]) -> int:
    """Detect warmup/steady-state changepoint using CUSUM (Page 1954).

    Algorithm:
      1. Use the latter half's mean as the "in-control" target.
      2. Accumulate deviations: ``S_i = S_{i-1} + (x_i - target)``.
      3. The changepoint is at ``argmax(|S_i|)``.
      4. If the changepoint falls after 75 % of the data, return 0
         (no clear warmup phase detected).

    Reference: Page, E.S. (1954). Continuous inspection schemes.
    *Biometrika*, 41(1/2), 100-115.

    Args:
        values: Raw observations.  Not mutated.

    Returns:
        Index of the first steady-state value (number of values to discard).
        Returns 0 when the data is too short (fewer than 4 values) or when
        no warmup is detected.
    """
    n = len(values)
    if n < 4:
        return 0

    # Target is the mean of the latter half.
    half = n // 2
    target = statistics.mean(values[half:])

    # Accumulate deviations.
    cumsum = 0.0
    max_abs = 0.0
    changepoint = 0

    for i, x in enumerate(values):
        cumsum += x - target
        abs_cumsum = abs(cumsum)
        if abs_cumsum > max_abs:
            max_abs = abs_cumsum
            changepoint = i

    # No deviation at all — all values equal to target.
    if max_abs == 0.0:
        return 0

    # Validate: changepoint too late means no meaningful warmup.
    if changepoint > int(0.75 * n):
        return 0

    # Steady state starts after the changepoint.
    return changepoint + 1


# ---------------------------------------------------------------------------
# 4. CUSUM with Threshold Parameters (Tabular CUSUM, Page 1954)
# ---------------------------------------------------------------------------


def cusum_with_threshold(
    values: list[float],
    drift: float = 0.5,
    threshold: float = 5.0,
) -> int:
    """Classical tabular CUSUM with drift and threshold (Page 1954).

    Algorithm:
      1. Estimate steady-state mean (``mu_0``) and standard deviation
         (``sigma``) from the latter 50 % of values.
      2. Normalize each observation: ``z_i = (x_i - mu_0) / sigma``.
      3. Upper CUSUM: ``S_h(i) = max(0, S_h(i-1) + z_i - drift)``.
      4. Lower CUSUM: ``S_l(i) = max(0, S_l(i-1) - z_i - drift)``.
      5. ``last_alarm`` = last index where ``S_h`` or ``S_l`` exceeds
         *threshold*.

    Reference: Page, E.S. (1954). Continuous inspection schemes.
    *Biometrika*, 41(1/2), 100-115.

    Args:
        values: Raw observations.  Not mutated.
        drift: Allowance parameter (``k`` in the literature).
        threshold: Decision interval (``h`` in the literature).

    Returns:
        Number of warmup values to discard.  Returns 0 when the data is
        too short or no alarm is raised.
    """
    n = len(values)
    if n < 4:
        return 0

    half = n // 2
    tail = values[half:]
    mu_0 = statistics.mean(tail)
    sigma = statistics.stdev(tail) if len(tail) >= 2 else 0.0

    if sigma == 0.0:
        # Constant signal; check whether the first half differs from mu_0.
        # If all values are identical, no warmup to discard.
        if all(x == mu_0 for x in values):
            return 0
        # Non-zero but constant tail; use a small epsilon for normalisation.
        sigma = abs(mu_0) * 1e-9 if mu_0 != 0.0 else 1.0

    s_high = 0.0
    s_low = 0.0
    last_alarm = -1

    for i, x in enumerate(values):
        z = (x - mu_0) / sigma
        s_high = max(0.0, s_high + z - drift)
        s_low = max(0.0, s_low - z - drift)
        if s_high > threshold or s_low > threshold:
            last_alarm = i

    if last_alarm < 0:
        return 0

    # Discard everything up to and including the last alarm.
    return min(last_alarm + 1, n - 1)


# ---------------------------------------------------------------------------
# 5. Linear Regression Warmup Detection
# ---------------------------------------------------------------------------


def _ols_slope(xs: list[float], ys: list[float]) -> float:
    """Ordinary least-squares slope for (xs, ys).

    Uses the textbook formula:
        beta = sum((x_i - x_bar)(y_i - y_bar)) / sum((x_i - x_bar)^2)

    Returns 0.0 when the denominator is zero (constant x or single point).
    """
    n = len(xs)
    if n < 2:
        return 0.0

    x_bar = sum(xs) / n
    y_bar = sum(ys) / n

    num = sum((xi - x_bar) * (yi - y_bar) for xi, yi in zip(xs, ys, strict=True))
    den = sum((xi - x_bar) ** 2 for xi in xs)

    if den == 0.0:
        return 0.0
    return num / den


def detect_warmup_trend(
    values: list[float],
    window: int = 10,
    slope_threshold: float = 0.01,
) -> int:
    """Detect warmup by finding where the linear-regression slope stabilises.

    Slides a window of size *window* through the data and computes the OLS
    slope at each position.  Warmup ends at the start of the first window
    where ``|slope| < slope_threshold`` for at least two consecutive windows.

    Args:
        values: Raw observations.  Not mutated.
        window: Number of observations per sliding window.
        slope_threshold: Maximum absolute slope for a window to be
            considered "stable".

    Returns:
        Number of warmup values to discard.  Returns 0 when the data is
        too short or stable from the start.
    """
    n = len(values)
    if n < window:
        return 0

    xs = list(range(window))  # reusable x-coordinates (0..window-1)
    consecutive_stable = 0
    required_consecutive = 2

    for start in range(n - window + 1):
        ys = values[start : start + window]
        slope = _ols_slope(xs, ys)

        if abs(slope) < slope_threshold:
            consecutive_stable += 1
            if consecutive_stable >= required_consecutive:
                # Steady state began at the start of the first stable window.
                first_stable_start = start - (consecutive_stable - 1)
                return max(0, first_stable_start)
        else:
            consecutive_stable = 0

    # Never stabilised; discard nothing (caller decides what to do).
    return 0


# ---------------------------------------------------------------------------
# 6. Calibration Loop (pyperf)
# ---------------------------------------------------------------------------


def calibrate_iterations(
    benchmark_fn: Callable[[int], float],
    min_time: float = 0.1,
    max_loops: int = 2**32,
) -> int:
    """Determine loop count so each measurement takes >= *min_time* seconds.

    Starts at 1 iteration and doubles until the elapsed time reported by
    *benchmark_fn(loops)* meets *min_time*.

    Reference: pyperf (Victor Stinner), calibration loop.

    Args:
        benchmark_fn: Callable that takes a loop count and returns elapsed
            seconds.  Must be deterministic enough that doubling loops
            roughly doubles elapsed time.
        min_time: Minimum target duration per sample in seconds.
        max_loops: Upper bound to prevent runaway calibration.

    Returns:
        Number of iterations per sample.

    Raises:
        RuntimeError: If *max_loops* is reached without meeting *min_time*.
    """
    loops = 1
    while loops <= max_loops:
        elapsed = benchmark_fn(loops)
        if elapsed >= min_time:
            return loops
        # Double the loop count.
        loops *= 2

    msg = f"calibration did not converge: {max_loops} loops still under {min_time}s target"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# 7. Classification (Krun / Tratt methodology)
# ---------------------------------------------------------------------------


class WarmupClassification(StrEnum):
    """Classification of a benchmark run's warmup behaviour.

    Reference: Barrett, E., Bolz-Tereick, C.F., Killick, R., Mount, S.,
    & Tratt, L. (2017). Virtual machine warmup blows hot and cold.
    *Proc. ACM Program. Lang.*, 1(OOPSLA), 52.
    """

    FLAT = "flat"
    """No changepoints, consistent performance throughout."""

    WARMUP = "warmup"
    """Starts slow, speeds up — expected JIT behaviour."""

    SLOWDOWN = "slowdown"
    """Starts fast, gets slower over time."""

    NO_STEADY_STATE = "no_steady_state"
    """Never stabilises into a consistent performance region."""


@dataclass(frozen=True)
class WarmupResult:
    """Immutable result of warmup classification.

    Attributes:
        warmup_count: Number of initial values to discard.
        classification: The detected warmup behaviour category.
        steady_state_start: Index where steady state begins (same as
            *warmup_count* unless no steady state is found).
        method: Name of the detection method used.
    """

    warmup_count: int
    classification: WarmupClassification
    steady_state_start: int
    method: str


def classify_warmup(
    values: list[float],
    steady_window: int = 50,
) -> WarmupResult:
    """Classify a benchmark run using Krun/Tratt methodology.

    Uses CUSUM to find the changepoint, then classifies the run based on
    whether the segment after the changepoint is faster or slower than the
    segment before it.  If the changepoint falls very late or the post-
    changepoint coefficient of variation is too high, the run is classified
    as :attr:`WarmupClassification.NO_STEADY_STATE`.

    Reference: Barrett et al. (2017). Virtual machine warmup blows hot
    and cold.

    Args:
        values: Raw observations.  Not mutated.
        steady_window: Minimum number of observations required in the
            steady-state segment.

    Returns:
        A frozen :class:`WarmupResult`.
    """
    n = len(values)
    method = "cusum+krun"

    # Edge case: too few values to analyse.
    if n < 4:
        return WarmupResult(
            warmup_count=0,
            classification=WarmupClassification.FLAT,
            steady_state_start=0,
            method=method,
        )

    # All identical values — trivially flat.
    if all(x == values[0] for x in values):
        return WarmupResult(
            warmup_count=0,
            classification=WarmupClassification.FLAT,
            steady_state_start=0,
            method=method,
        )

    # Detect changepoint.
    cp = cusum_changepoint(values)

    # No changepoint detected — check if the run is truly flat or never
    # settles.
    if cp == 0:
        cv = _coefficient_of_variation(values)
        if cv < 0.10:
            return WarmupResult(
                warmup_count=0,
                classification=WarmupClassification.FLAT,
                steady_state_start=0,
                method=method,
            )
        # High CV but no changepoint — no steady state.
        return WarmupResult(
            warmup_count=0,
            classification=WarmupClassification.NO_STEADY_STATE,
            steady_state_start=0,
            method=method,
        )

    # We have a changepoint at index *cp*.  Classify the direction.
    before = values[:cp]
    after = values[cp:]

    # Not enough steady-state data — classify as no_steady_state.
    if len(after) < steady_window:
        return WarmupResult(
            warmup_count=cp,
            classification=WarmupClassification.NO_STEADY_STATE,
            steady_state_start=cp,
            method=method,
        )

    # Check that the post-changepoint segment is reasonably stable.
    cv_after = _coefficient_of_variation(after)
    if cv_after > 0.20:
        return WarmupResult(
            warmup_count=cp,
            classification=WarmupClassification.NO_STEADY_STATE,
            steady_state_start=cp,
            method=method,
        )

    mean_before = statistics.mean(before)
    mean_after = statistics.mean(after)

    if mean_before > mean_after:
        # Values decreased after changepoint — classic warmup (JIT).
        classification = WarmupClassification.WARMUP
    elif mean_before < mean_after:
        # Values increased — slowdown.
        classification = WarmupClassification.SLOWDOWN
    else:
        classification = WarmupClassification.FLAT

    return WarmupResult(
        warmup_count=cp,
        classification=classification,
        steady_state_start=cp,
        method=method,
    )


def _coefficient_of_variation(data: list[float]) -> float:
    """Coefficient of variation: stdev / |mean|.

    Returns 0.0 when the mean is zero or fewer than 2 data points.
    """
    if len(data) < 2:
        return 0.0
    mu = statistics.mean(data)
    if mu == 0.0:
        return 0.0
    return statistics.stdev(data) / abs(mu)
