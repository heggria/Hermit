from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "BenchmarkStats",
    "bootstrap_bca",
    "bootstrap_ci",
    "compute_stats",
    "detect_outliers_mad",
    "mad",
    "normal_cdf",
    "normal_ppf",
    "percentiles",
    "remove_outliers_iqr",
]

# ---------------------------------------------------------------------------
# Normal distribution helpers
# ---------------------------------------------------------------------------


def normal_cdf(z: float) -> float:
    """Standard normal cumulative distribution function.

    Uses the identity: Phi(z) = 0.5 * (1 + erf(z / sqrt(2))).

    Reference: Abramowitz & Stegun, Handbook of Mathematical Functions, 7.1.21.
    """
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def normal_ppf(p: float) -> float:
    """Inverse of the standard normal CDF (percent-point function).

    Implements the rational approximation by Peter J. Acklam (2003),
    accurate to approximately 1.15e-9 across the full (0, 1) range.

    Reference: https://web.archive.org/web/20151030215612/
               http://home.online.no/~pjacklam/notes/invnorm/

    Raises:
        ValueError: If *p* is not in the open interval (0, 1).
    """
    if p <= 0.0 or p >= 1.0:
        msg = f"p must be in (0, 1), got {p}"
        raise ValueError(msg)

    # Coefficients for the rational approximation.
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )

    p_low = 0.02425
    p_high = 1.0 - p_low

    if p < p_low:
        # Lower tail: rational approximation for p in (0, p_low].
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )

    if p <= p_high:
        # Central region: rational approximation for p in [p_low, p_high].
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        )

    # Upper tail: rational approximation for p in (p_high, 1).
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )


# ---------------------------------------------------------------------------
# Bootstrap confidence intervals
# ---------------------------------------------------------------------------


def bootstrap_ci(
    data: list[float],
    stat_fn: Callable[[list[float]], float] = statistics.median,
    n_boot: int = 10_000,
    confidence: float = 0.95,
    seed: int | None = None,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval.

    Draws *n_boot* resamples with replacement from *data*, computes *stat_fn*
    on each resample, and returns the (alpha/2, 1 - alpha/2) quantiles of the
    bootstrap distribution as the confidence interval bounds.

    Reference: Efron, B. (1979). Bootstrap methods: another look at the
    jackknife. *Annals of Statistics*, 7(1), 1-26.

    Args:
        data: Sample observations.  Must contain at least one element.
        stat_fn: Summary statistic to bootstrap (default: median).
        n_boot: Number of bootstrap resamples.
        confidence: Confidence level in (0, 1).
        seed: Optional RNG seed for reproducibility.

    Returns:
        (lower, upper) confidence interval bounds.

    Raises:
        ValueError: If *data* is empty or *confidence* is not in (0, 1).
    """
    if not data:
        msg = "data must be non-empty"
        raise ValueError(msg)
    if confidence <= 0.0 or confidence >= 1.0:
        msg = f"confidence must be in (0, 1), got {confidence}"
        raise ValueError(msg)

    if len(data) == 1:
        return (data[0], data[0])

    rng = random.Random(seed)
    n = len(data)
    boot_stats: list[float] = []

    for _ in range(n_boot):
        resample = [data[rng.randrange(n)] for _ in range(n)]
        boot_stats.append(stat_fn(resample))

    boot_stats.sort()
    alpha = 1.0 - confidence
    lower_idx = max(0, math.floor((alpha / 2.0) * n_boot))
    upper_idx = min(n_boot - 1, math.floor((1.0 - alpha / 2.0) * n_boot))
    return (boot_stats[lower_idx], boot_stats[upper_idx])


def bootstrap_bca(
    data: list[float],
    stat_fn: Callable[[list[float]], float],
    n_boot: int = 10_000,
    confidence: float = 0.95,
    seed: int | None = None,
) -> tuple[float, float]:
    """BCa (Bias-Corrected and accelerated) bootstrap confidence interval.

    Adjusts the simple percentile interval for both median-bias and skewness
    in the bootstrap distribution using bias-correction factor *z0* and
    acceleration constant *a_hat* derived from the jackknife.

    Algorithm (following Efron 1987 / haskell-statistics bootstrapBCA):
      1. Compute point estimate theta_hat = stat_fn(data).
      2. Generate *n_boot* bootstrap replicates.
      3. Bias correction: z0 = Phi_inv(proportion of replicates < theta_hat).
      4. Jackknife acceleration: a_hat = sum((mean_jk - jk_i)^3) /
         (6 * (sum((mean_jk - jk_i)^2))^1.5).
      5. Adjusted percentiles:
         alpha1 = Phi(z0 + (z0 + z_alpha) / (1 - a_hat * (z0 + z_alpha)))
         alpha2 = Phi(z0 + (z0 + z_{1-alpha}) / (1 - a_hat * (z0 + z_{1-alpha})))
      6. Return quantiles at alpha1 and alpha2 from the bootstrap distribution.

    Reference: Efron, B. (1987). Better bootstrap confidence intervals.
    *Journal of the American Statistical Association*, 82(397), 171-185.

    Args:
        data: Sample observations.  Must contain at least two elements for the
              jackknife to be meaningful.
        stat_fn: Summary statistic to bootstrap.
        n_boot: Number of bootstrap resamples.
        confidence: Confidence level in (0, 1).
        seed: Optional RNG seed for reproducibility.

    Returns:
        (lower, upper) confidence interval bounds.

    Raises:
        ValueError: If *data* has fewer than 2 elements or *confidence* is
                    not in (0, 1).
    """
    if len(data) < 2:
        if not data:
            msg = "data must be non-empty"
            raise ValueError(msg)
        return (data[0], data[0])
    if confidence <= 0.0 or confidence >= 1.0:
        msg = f"confidence must be in (0, 1), got {confidence}"
        raise ValueError(msg)

    n = len(data)
    theta_hat = stat_fn(data)

    # --- bootstrap replicates ---
    rng = random.Random(seed)
    boot_stats: list[float] = []
    for _ in range(n_boot):
        resample = [data[rng.randrange(n)] for _ in range(n)]
        boot_stats.append(stat_fn(resample))
    boot_stats.sort()

    # --- bias correction z0 ---
    count_below = sum(1 for s in boot_stats if s < theta_hat)
    proportion = count_below / n_boot

    # Clamp to avoid Phi_inv(0) or Phi_inv(1) which are undefined.
    proportion = max(1.0 / (n_boot + 1), min(proportion, n_boot / (n_boot + 1)))
    z0 = normal_ppf(proportion)

    # --- jackknife acceleration a_hat ---
    jackknife_stats: list[float] = []
    for i in range(n):
        jk_sample = data[:i] + data[i + 1 :]
        jackknife_stats.append(stat_fn(jk_sample))

    jk_mean = sum(jackknife_stats) / n
    diffs = [jk_mean - jk_i for jk_i in jackknife_stats]
    sum_sq = sum(d * d for d in diffs)
    sum_cubes = sum(d * d * d for d in diffs)

    if sum_sq == 0.0:
        # All jackknife estimates are identical; no acceleration possible.
        a_hat = 0.0
    else:
        a_hat = sum_cubes / (6.0 * sum_sq**1.5)

    # --- adjusted percentiles ---
    alpha = 1.0 - confidence
    z_alpha_lower = normal_ppf(alpha / 2.0)
    z_alpha_upper = normal_ppf(1.0 - alpha / 2.0)

    def _adjusted_alpha(z_alpha: float) -> float:
        numerator = z0 + z_alpha
        denominator = 1.0 - a_hat * numerator
        if denominator == 0.0:
            # Degenerate case: fall back to unadjusted percentile.
            return normal_cdf(z_alpha)
        return normal_cdf(z0 + numerator / denominator)

    alpha1 = _adjusted_alpha(z_alpha_lower)
    alpha2 = _adjusted_alpha(z_alpha_upper)

    lower_idx = max(0, min(n_boot - 1, math.floor(alpha1 * n_boot)))
    upper_idx = max(0, min(n_boot - 1, math.floor(alpha2 * n_boot)))

    return (boot_stats[lower_idx], boot_stats[upper_idx])


# ---------------------------------------------------------------------------
# MAD outlier detection and IQR filtering
# ---------------------------------------------------------------------------


def mad(data: list[float]) -> float:
    """Median Absolute Deviation.

    MAD = median(|x_i - median(x)|)

    MAD is a robust measure of spread that is resistant to outliers,
    unlike standard deviation.

    Reference: Hampel, F.R. (1974). The influence curve and its role in
    robust estimation. *JASA*, 69(346), 383-393.

    Returns:
        The MAD value, or 0.0 for empty or single-element input.
    """
    if len(data) < 2:
        return 0.0

    med = statistics.median(data)
    abs_deviations = [abs(x - med) for x in data]
    return statistics.median(abs_deviations)


def detect_outliers_mad(data: list[float], threshold: float = 3.0) -> list[int]:
    """Return indices of outliers using the modified Z-score (MAD-based).

    The modified Z-score is defined as:
        M_i = 0.6745 * (x_i - median) / MAD

    The constant 0.6745 is the 0.75th quantile of the standard normal
    distribution, making the modified Z-score consistent with the standard
    deviation for normally distributed data.

    Points with |M_i| > *threshold* are classified as outliers.

    Reference: Iglewicz, B. & Hoaglin, D.C. (1993). *Volume 16: How to
    Detect and Handle Outliers*. ASQ Quality Press.

    Args:
        data: Sample observations.
        threshold: Modified Z-score cutoff (default: 3.0).

    Returns:
        Sorted list of indices of outlier values.
    """
    if len(data) < 2:
        return []

    med = statistics.median(data)
    mad_value = mad(data)

    if mad_value == 0.0:
        # All values equal to the median are inliers; anything else is an
        # outlier.  When MAD = 0 the distribution is degenerate at the median.
        return sorted(i for i, x in enumerate(data) if x != med)

    # 0.6745 ~ normal_ppf(0.75) for consistency with the standard deviation.
    _CONSISTENCY_CONSTANT = 0.6745
    return sorted(
        i
        for i, x in enumerate(data)
        if abs(_CONSISTENCY_CONSTANT * (x - med) / mad_value) > threshold
    )


def remove_outliers_iqr(data: list[float], factor: float = 1.5) -> list[float]:
    """Remove outliers using Tukey IQR fences.

    Values outside [Q1 - factor*IQR, Q3 + factor*IQR] are removed.

    Reference: Tukey, J.W. (1977). *Exploratory Data Analysis*.
    Addison-Wesley.

    Args:
        data: Sample observations.  Not mutated.
        factor: IQR multiplier for fence width (default: 1.5).

    Returns:
        New list with outliers removed, preserving original order.
    """
    if len(data) < 4:
        return list(data)

    sorted_data = sorted(data)
    n = len(sorted_data)
    q1 = sorted_data[n // 4]
    q3 = sorted_data[(3 * n) // 4]
    iqr = q3 - q1

    lower_fence = q1 - factor * iqr
    upper_fence = q3 + factor * iqr

    return [x for x in data if lower_fence <= x <= upper_fence]


# ---------------------------------------------------------------------------
# Percentile computation
# ---------------------------------------------------------------------------


def percentiles(data: list[float]) -> dict[str, float]:
    """Compute P50, P90, P95, P99 percentiles using linear interpolation.

    Uses the *exclusive* method (Method 6 of Hyndman & Fan 1996) which is
    appropriate for continuous distributions and matches the behaviour of
    ``statistics.quantiles(method='exclusive')`` in Python 3.10+.

    Reference: Hyndman, R.J. & Fan, Y. (1996). Sample quantiles in
    statistical packages. *The American Statistician*, 50(4), 361-365.

    Args:
        data: Sample observations.  Must be non-empty.

    Returns:
        Dict with keys ``"p50"``, ``"p90"``, ``"p95"``, ``"p99"``.

    Raises:
        ValueError: If *data* is empty.
    """
    if not data:
        msg = "data must be non-empty"
        raise ValueError(msg)

    if len(data) == 1:
        v = data[0]
        return {"p50": v, "p90": v, "p95": v, "p99": v}

    sorted_data = sorted(data)
    n = len(sorted_data)

    def _quantile(p: float) -> float:
        """Linear interpolation quantile (Method 7 of Hyndman & Fan)."""
        idx = p * (n - 1)
        lo = math.floor(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return sorted_data[lo] * (1.0 - frac) + sorted_data[hi] * frac

    return {
        "p50": _quantile(0.50),
        "p90": _quantile(0.90),
        "p95": _quantile(0.95),
        "p99": _quantile(0.99),
    }


# ---------------------------------------------------------------------------
# Summary statistics dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkStats:
    """Immutable statistical summary of benchmark samples.

    All fields are computed by :func:`compute_stats`.  The CI bounds come from
    either the simple percentile bootstrap or the BCa bootstrap depending on
    the ``use_bca`` parameter.
    """

    n: int
    mean: float
    median: float
    stdev: float
    mad: float
    ci_lower: float
    ci_upper: float
    p50: float
    p90: float
    p95: float
    p99: float
    outlier_count: int
    outlier_indices: tuple[int, ...]


def compute_stats(
    samples: list[float],
    confidence: float = 0.95,
    n_boot: int = 10_000,
    use_bca: bool = False,
    seed: int | None = None,
) -> BenchmarkStats:
    """Compute comprehensive statistics for a benchmark run.

    Produces an immutable :class:`BenchmarkStats` summary including location
    (mean, median), spread (stdev, MAD), confidence interval for the median
    via bootstrap, percentiles (P50/P90/P95/P99), and MAD-based outlier
    detection.

    Args:
        samples: Raw benchmark observations.  Must be non-empty.
        confidence: Confidence level for the bootstrap CI (default: 0.95).
        n_boot: Number of bootstrap resamples (default: 10_000).
        use_bca: If ``True``, use BCa bootstrap instead of percentile
                 bootstrap for the CI.  BCa requires at least 2 data points.
        seed: Optional RNG seed for reproducible bootstrap.

    Returns:
        A frozen :class:`BenchmarkStats` dataclass.

    Raises:
        ValueError: If *samples* is empty.
    """
    if not samples:
        msg = "samples must be non-empty"
        raise ValueError(msg)

    n = len(samples)
    sample_mean = statistics.mean(samples)
    sample_median = statistics.median(samples)
    sample_stdev = statistics.stdev(samples) if n >= 2 else 0.0
    sample_mad = mad(samples)

    # Bootstrap CI.
    if use_bca and n >= 2:
        ci_lower, ci_upper = bootstrap_bca(
            samples,
            stat_fn=statistics.median,
            n_boot=n_boot,
            confidence=confidence,
            seed=seed,
        )
    else:
        ci_lower, ci_upper = bootstrap_ci(
            samples,
            stat_fn=statistics.median,
            n_boot=n_boot,
            confidence=confidence,
            seed=seed,
        )

    pcts = percentiles(samples)
    outlier_idx = detect_outliers_mad(samples)

    return BenchmarkStats(
        n=n,
        mean=sample_mean,
        median=sample_median,
        stdev=sample_stdev,
        mad=sample_mad,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        p50=pcts["p50"],
        p90=pcts["p90"],
        p95=pcts["p95"],
        p99=pcts["p99"],
        outlier_count=len(outlier_idx),
        outlier_indices=tuple(outlier_idx),
    )
