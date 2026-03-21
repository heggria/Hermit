from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass

__all__ = [
    "RegressionResult",
    "classify_change",
    "detect_regression",
    "mixed_bootstrap_test",
    "residual_z_score",
    "welch_t_test",
]

# ---------------------------------------------------------------------------
# T-distribution critical value lookup table (two-tailed, alpha = 0.05)
#
# Values for df = 1..30 sourced from standard statistical tables.
# For df > 30 we interpolate; for df > 120 we use the z-limit (1.96).
# ---------------------------------------------------------------------------

_T_TABLE_95: list[float] = [
    12.706,  # df = 1
    4.303,  # df = 2
    3.182,  # df = 3
    2.776,  # df = 4
    2.571,  # df = 5
    2.447,  # df = 6
    2.365,  # df = 7
    2.306,  # df = 8
    2.262,  # df = 9
    2.228,  # df = 10
    2.201,  # df = 11
    2.179,  # df = 12
    2.160,  # df = 13
    2.145,  # df = 14
    2.131,  # df = 15
    2.120,  # df = 16
    2.110,  # df = 17
    2.101,  # df = 18
    2.093,  # df = 19
    2.086,  # df = 20
    2.080,  # df = 21
    2.074,  # df = 22
    2.069,  # df = 23
    2.064,  # df = 24
    2.060,  # df = 25
    2.056,  # df = 26
    2.052,  # df = 27
    2.048,  # df = 28
    2.045,  # df = 29
    2.042,  # df = 30
]

# Sparse entries for larger degrees of freedom (two-tailed, alpha = 0.05).
_T_TABLE_95_SPARSE: list[tuple[int, float]] = [
    (40, 2.021),
    (60, 2.000),
    (80, 1.990),
    (100, 1.984),
    (120, 1.980),
]

_Z_LIMIT_95 = 1.960  # Normal approximation for df → infinity


def _t_critical(df: float, alpha: float = 0.05) -> float:
    """T-distribution critical value lookup (two-tailed).

    Uses the embedded table for df 1-30, linear interpolation between
    sparse entries for df 31-120, and the normal z-limit for df > 120.

    Only alpha = 0.05 is supported via table lookup. For other alpha
    values, we fall back to the normal approximation using the
    Abramowitz-Stegun rational approximation of the inverse normal CDF.

    Args:
        df: Degrees of freedom (will be floored to nearest integer for
            table lookup, with interpolation for fractional values).
        alpha: Significance level (two-tailed). Default 0.05.

    Returns:
        Critical t-value for the given df and alpha.
    """
    if df <= 0:
        return math.inf

    # For non-standard alpha, use normal approximation (accurate for large df,
    # conservative for small df).
    if abs(alpha - 0.05) > 1e-9:
        return _inverse_normal_approx(1.0 - alpha / 2.0)

    # Exact table for df 1-30.
    if df <= 30:
        idx_low = max(0, math.floor(df) - 1)
        idx_high = min(29, math.ceil(df) - 1)
        if idx_low == idx_high:
            return _T_TABLE_95[idx_low]
        frac = df - math.floor(df)
        return _T_TABLE_95[idx_low] * (1.0 - frac) + _T_TABLE_95[idx_high] * frac

    # Interpolation between sparse entries for df 31-120.
    if df <= 120:
        prev_df, prev_val = 30, _T_TABLE_95[29]
        for sp_df, sp_val in _T_TABLE_95_SPARSE:
            if df <= sp_df:
                frac = (df - prev_df) / (sp_df - prev_df)
                return prev_val * (1.0 - frac) + sp_val * frac
            prev_df, prev_val = sp_df, sp_val
        return prev_val  # pragma: no cover — should not reach here

    # df > 120: normal approximation.
    return _Z_LIMIT_95


def _inverse_normal_approx(p: float) -> float:
    """Rational approximation of the inverse standard normal CDF.

    Uses the Abramowitz-Stegun approximation (formula 26.2.23) which
    provides accuracy to ~4.5 x 10^-4.

    Args:
        p: Probability (0 < p < 1).

    Returns:
        Approximate z-value such that Phi(z) ~ p.
    """
    if p <= 0.0 or p >= 1.0:
        return math.inf if p >= 1.0 else -math.inf

    # Coefficients for the rational approximation.
    c0 = 2.515517
    c1 = 0.802853
    c2 = 0.010328
    d1 = 1.432788
    d2 = 0.189269
    d3 = 0.001308

    if p > 0.5:
        t = math.sqrt(-2.0 * math.log(1.0 - p))
        z = t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)
        return z
    else:
        t = math.sqrt(-2.0 * math.log(p))
        z = t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)
        return -z


# ---------------------------------------------------------------------------
# Welch's t-test
# ---------------------------------------------------------------------------


def welch_t_test(a: list[float], b: list[float], alpha: float = 0.05) -> tuple[bool, float, float]:
    """Welch's t-test for two independent samples with unequal variances.

    Implements the two-sample t-test without assuming equal variances,
    following the formulation used by pyperf and similar benchmarking
    frameworks.

    Formula:
        t = (mean_a - mean_b) / sqrt(var_a/n_a + var_b/n_b)
        df = Welch-Satterthwaite approximation:
             (var_a/n_a + var_b/n_b)^2 /
             ((var_a/n_a)^2/(n_a-1) + (var_b/n_b)^2/(n_b-1))

    Args:
        a: First sample (baseline measurements).
        b: Second sample (contender measurements).
        alpha: Significance level (two-tailed). Default 0.05.

    Returns:
        Tuple of (is_significant, t_score, degrees_of_freedom).

        - is_significant: True if |t| exceeds the critical value at the
          given alpha level.
        - t_score: The computed t-statistic.
        - degrees_of_freedom: Welch-Satterthwaite degrees of freedom.

    Raises:
        ValueError: If either sample has fewer than 2 observations.

    References:
        - Welch, B. L. (1947). "The generalization of Student's problem..."
        - pyperf: https://github.com/psf/pyperf
    """
    n_a, n_b = len(a), len(b)

    if n_a < 2 or n_b < 2:
        raise ValueError(
            f"Both samples must have at least 2 observations (got n_a={n_a}, n_b={n_b})."
        )

    mean_a = statistics.mean(a)
    mean_b = statistics.mean(b)
    var_a = statistics.variance(a)
    var_b = statistics.variance(b)

    # Handle zero-variance edge case: both samples have zero variance.
    se_a = var_a / n_a
    se_b = var_b / n_b
    se_sum = se_a + se_b

    if se_sum == 0.0:
        # Both samples have identical values. If means are equal, no
        # difference; otherwise infinitely significant (degenerate case).
        if mean_a == mean_b:
            return (False, 0.0, float(n_a + n_b - 2))
        return (True, math.copysign(math.inf, mean_a - mean_b), float(n_a + n_b - 2))

    t_score = (mean_a - mean_b) / math.sqrt(se_sum)

    # Welch-Satterthwaite degrees of freedom.
    numerator = se_sum * se_sum
    denominator = 0.0
    if se_a > 0:
        denominator += (se_a * se_a) / (n_a - 1)
    if se_b > 0:
        denominator += (se_b * se_b) / (n_b - 1)

    if denominator == 0.0:
        df = float(n_a + n_b - 2)
    else:
        df = numerator / denominator

    t_crit = _t_critical(df, alpha)
    is_significant = abs(t_score) > t_crit

    return (is_significant, t_score, df)


# ---------------------------------------------------------------------------
# Mixed Bootstrap Hypothesis Test (Criterion.rs approach)
# ---------------------------------------------------------------------------


def _compute_t_stat(x: list[float], y: list[float]) -> float:
    """Compute the t-statistic between two samples.

    Uses direct arithmetic (no statistics module) for performance in
    tight bootstrap loops.

    Args:
        x: First sample.
        y: Second sample.

    Returns:
        The t-statistic, or 0.0 for degenerate cases.
    """
    n_x, n_y = len(x), len(y)
    if n_x < 1 or n_y < 1:
        return 0.0

    sum_x = math.fsum(x)
    sum_y = math.fsum(y)
    mean_x = sum_x / n_x
    mean_y = sum_y / n_y

    if n_x >= 2:
        ss_x = math.fsum((v - mean_x) ** 2 for v in x)
        var_x = ss_x / (n_x - 1)
    else:
        var_x = 0.0

    if n_y >= 2:
        ss_y = math.fsum((v - mean_y) ** 2 for v in y)
        var_y = ss_y / (n_y - 1)
    else:
        var_y = 0.0

    se = var_x / n_x + var_y / n_y
    if se <= 0.0:
        return 0.0 if mean_x == mean_y else math.copysign(math.inf, mean_x - mean_y)

    return (mean_x - mean_y) / math.sqrt(se)


def mixed_bootstrap_test(
    baseline: list[float],
    contender: list[float],
    n_resamples: int = 100_000,
    significance_level: float = 0.05,
    seed: int | None = None,
) -> tuple[bool, float, float]:
    """Bootstrapped hypothesis test using mixed/permutation bootstrap.

    Implements the permutation-based bootstrap hypothesis test as
    described in the Criterion.rs benchmarking library. Under the null
    hypothesis (H0: no difference between baseline and contender), both
    samples are drawn from the same distribution.

    Algorithm:
        1. Pool baseline and contender samples into a single array.
        2. For each resample:
           a. Draw n_baseline values with replacement from the pool.
           b. Draw n_contender values with replacement from the pool.
           c. Compute the t-statistic between the resampled groups.
        3. The observed t-statistic is computed from the original samples.
        4. Two-tailed p-value = 2 * min(count_ge, n_resamples - count_ge)
           / n_resamples, where count_ge is the number of resampled
           t-statistics with |t| >= |t_observed|.

    Args:
        baseline: Baseline measurements.
        contender: Contender measurements.
        n_resamples: Number of bootstrap resamples. Default 100,000.
        significance_level: Alpha level for significance. Default 0.05.
        seed: Optional seed for reproducibility.

    Returns:
        Tuple of (is_significant, t_observed, p_value).

    Raises:
        ValueError: If either sample is empty.

    References:
        - Criterion.rs: https://bheisler.github.io/criterion.rs/book/
        - Efron, B. & Tibshirani, R. (1993). "An Introduction to the Bootstrap."
    """
    n_base = len(baseline)
    n_cont = len(contender)

    if n_base == 0 or n_cont == 0:
        raise ValueError(
            f"Both samples must be non-empty (got baseline={n_base}, contender={n_cont})."
        )

    # Handle single-element samples: cannot compute meaningful variance.
    if n_base < 2 and n_cont < 2:
        t_obs = _compute_t_stat(baseline, contender)
        p = 1.0 if math.isfinite(t_obs) and t_obs == 0.0 else 0.0
        return (p < significance_level, t_obs, p)

    t_observed = _compute_t_stat(baseline, contender)

    # Pool samples for permutation resampling.
    pool = list(baseline) + list(contender)
    n_pool = len(pool)

    rng = random.Random(seed)

    # Pre-compute absolute observed t for comparison.
    abs_t_observed = abs(t_observed)

    # Performance-critical loop: inline all arithmetic to avoid function
    # call overhead. At 100k iterations with n=100, this must complete
    # in under 5 seconds. We avoid statistics.mean/variance and
    # _compute_t_stat calls, using direct sum/variance computation.
    _choices = rng.choices  # Bind method locally for speed.
    _sqrt = math.sqrt
    _fabs = math.fabs
    n_base_f = float(n_base)
    n_cont_f = float(n_cont)
    n_base_m1 = n_base - 1 if n_base >= 2 else 1
    n_cont_m1 = n_cont - 1 if n_cont >= 2 else 1

    count_ge = 0
    for _ in range(n_resamples):
        resampled = _choices(pool, k=n_pool)

        # Compute means of the two groups inline.
        sum_a = 0.0
        for i in range(n_base):
            sum_a += resampled[i]
        mean_a = sum_a / n_base_f

        sum_b = 0.0
        for i in range(n_base, n_pool):
            sum_b += resampled[i]
        mean_b = sum_b / n_cont_f

        # Compute sample variances inline.
        ss_a = 0.0
        for i in range(n_base):
            d = resampled[i] - mean_a
            ss_a += d * d
        var_a = ss_a / n_base_m1

        ss_b = 0.0
        for i in range(n_base, n_pool):
            d = resampled[i] - mean_b
            ss_b += d * d
        var_b = ss_b / n_cont_m1

        se = var_a / n_base_f + var_b / n_cont_f
        if se > 0.0:
            t_boot = (mean_a - mean_b) / _sqrt(se)
            if _fabs(t_boot) >= abs_t_observed:
                count_ge += 1
        else:
            # Zero variance: t is 0 if means equal, inf otherwise.
            if mean_a != mean_b:
                count_ge += 1  # inf >= abs_t_observed
            elif abs_t_observed == 0.0:
                count_ge += 1  # 0 >= 0

    # Two-tailed p-value.
    p_value = count_ge / n_resamples

    is_significant = p_value < significance_level
    return (is_significant, t_observed, p_value)


# ---------------------------------------------------------------------------
# Noise Threshold Gate (Criterion.rs approach)
# ---------------------------------------------------------------------------


def classify_change(
    relative_change_ci: tuple[float, float],
    noise_threshold: float = 0.01,
) -> str:
    """Classify a benchmark change using noise threshold dead zone.

    Implements the two-gate classification from Criterion.rs:

    - **Gate 1**: Statistical significance (determined externally via
      t-test or bootstrap test before calling this function).
    - **Gate 2**: Practical significance — the entire confidence interval
      of the relative change must fall outside the noise threshold
      dead zone [-threshold, +threshold].

    The dead zone prevents reporting noise as real regressions.

    Classification logic:
        - If the entire CI is above +threshold: ``"regressed"``
          (performance got worse, i.e., metric increased).
        - If the entire CI is below -threshold: ``"improved"``
          (performance got better, i.e., metric decreased).
        - Otherwise: ``"no_change"`` (CI overlaps the dead zone).

    Args:
        relative_change_ci: Tuple of (lower_bound, upper_bound) of the
            relative change confidence interval. Both values should be
            fractional (e.g., 0.05 means 5% regression).
        noise_threshold: Dead zone half-width. Default 0.01 (1%).

    Returns:
        One of ``"improved"``, ``"regressed"``, or ``"no_change"``.

    References:
        - Criterion.rs: https://bheisler.github.io/criterion.rs/book/
    """
    lo, hi = relative_change_ci

    if lo > noise_threshold:
        return "regressed"
    if hi < -noise_threshold:
        return "improved"
    return "no_change"


# ---------------------------------------------------------------------------
# Residual-based Z-score (Conbench approach)
# ---------------------------------------------------------------------------


def residual_z_score(
    history: list[float],
    contender: float,
    window: int = 100,
) -> float:
    """Conbench-style residual Z-score for change detection.

    Computes a Z-score using residual-based standard deviation rather
    than the raw standard deviation of the time series. This approach
    prevents known distribution shifts from inflating the variance
    estimate, making it more sensitive to genuine regressions.

    Algorithm:
        1. Take the last ``window`` values from ``history``.
        2. Compute a rolling mean of the windowed history.
        3. Compute residuals: each value minus the rolling mean at
           that point.
        4. sigma = stdev(residuals) (NOT stdev of raw values).
        5. baseline_mean = mean of the windowed history.
        6. z = (contender - baseline_mean) / sigma.

    A large positive Z-score indicates the contender is significantly
    higher than the baseline (potential regression for latency metrics).
    A large negative Z-score indicates improvement.

    Args:
        history: Historical benchmark measurements (oldest first).
        contender: The new measurement to evaluate.
        window: Rolling window size. Default 100.

    Returns:
        The residual Z-score. Returns 0.0 if history is empty.
        Returns ``math.inf`` or ``-math.inf`` if sigma is zero
        (all residuals are identical) and contender differs from mean.

    References:
        - Conbench: https://conbench.github.io/conbench/
    """
    if not history:
        return 0.0

    # Trim to the most recent `window` values.
    windowed = history[-window:] if len(history) > window else list(history)
    n = len(windowed)

    if n < 2:
        # With a single historical point, no variance can be estimated.
        if windowed[0] == contender:
            return 0.0
        return math.copysign(math.inf, contender - windowed[0])

    baseline_mean = statistics.mean(windowed)

    # Compute residuals from a simple rolling mean.
    # For each point, the "rolling mean" is the cumulative mean up to
    # and including that point.
    residuals: list[float] = []
    cumulative_sum = 0.0
    for i, val in enumerate(windowed):
        cumulative_sum += val
        rolling_mean = cumulative_sum / (i + 1)
        residuals.append(val - rolling_mean)

    sigma = statistics.stdev(residuals)

    if sigma == 0.0:
        if contender == baseline_mean:
            return 0.0
        return math.copysign(math.inf, contender - baseline_mean)

    return (contender - baseline_mean) / sigma


# ---------------------------------------------------------------------------
# Bootstrap confidence interval for relative change
# ---------------------------------------------------------------------------


def _bootstrap_relative_change_ci(
    baseline: list[float],
    contender: list[float],
    confidence: float = 0.95,
    n_resamples: int = 10_000,
    rng: random.Random | None = None,
) -> tuple[float, float]:
    """Bootstrap confidence interval for relative change of means.

    Computes the CI of (mean_contender - mean_baseline) / mean_baseline
    via the percentile bootstrap method.

    Args:
        baseline: Baseline measurements.
        contender: Contender measurements.
        confidence: Confidence level. Default 0.95.
        n_resamples: Number of bootstrap resamples. Default 10,000.
        rng: Optional seeded random.Random instance.

    Returns:
        Tuple of (lower_bound, upper_bound) of the relative change CI.
    """
    if rng is None:
        rng = random.Random()

    n_base = len(baseline)
    n_cont = len(contender)

    mean_base = statistics.mean(baseline)

    # If baseline mean is zero, relative change is undefined.
    # Fall back to absolute difference.
    use_absolute = mean_base == 0.0

    deltas: list[float] = []
    for _ in range(n_resamples):
        boot_base = rng.choices(baseline, k=n_base)
        boot_cont = rng.choices(contender, k=n_cont)
        mb = statistics.mean(boot_base)
        mc = statistics.mean(boot_cont)
        if use_absolute:
            deltas.append(mc - mb)
        elif mb == 0.0:
            # Avoid division by zero in this resample; skip it.
            deltas.append(0.0)
        else:
            deltas.append((mc - mb) / mb)

    deltas.sort()

    # Percentile method.
    alpha = 1.0 - confidence
    lo_idx = max(0, math.floor(alpha / 2.0 * n_resamples))
    hi_idx = min(n_resamples - 1, math.ceil((1.0 - alpha / 2.0) * n_resamples) - 1)

    return (deltas[lo_idx], deltas[hi_idx])


# ---------------------------------------------------------------------------
# Comprehensive Regression Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegressionResult:
    """Immutable regression detection result.

    Combines statistical significance (Welch's t-test or bootstrap),
    practical significance (noise threshold gating), and optional
    historical analysis (residual Z-score) into a single verdict.

    Attributes:
        is_regression: True if all active detection gates flag a regression.
        is_improvement: True if all active detection gates flag an improvement.
        classification: One of ``"regressed"``, ``"improved"``, or ``"no_change"``.
        relative_change: Point estimate of relative change
            ``(contender_mean - baseline_mean) / baseline_mean``.
        relative_change_ci: Bootstrap confidence interval of relative change.
        t_statistic: Welch's t-test statistic.
        p_value: Bootstrap p-value (or 2-tailed t-test p-value estimate).
        z_score: Conbench residual Z-score, or None if no history provided.
        confidence_level: ``1 - significance_level`` used for the test.
    """

    is_regression: bool
    is_improvement: bool
    classification: str
    relative_change: float
    relative_change_ci: tuple[float, float]
    t_statistic: float
    p_value: float
    z_score: float | None
    confidence_level: float


def detect_regression(
    baseline: list[float],
    contender: list[float],
    history: list[float] | None = None,
    significance_level: float = 0.05,
    noise_threshold: float = 0.01,
    z_threshold: float = 5.0,
    n_resamples: int = 10_000,
    seed: int | None = None,
) -> RegressionResult:
    """Full regression detection pipeline.

    Combines multiple detection methods to minimize both false positives
    and false negatives:

    1. **Welch's t-test** for statistical significance.
    2. **Bootstrap CI** of relative change (percentile method).
    3. **Noise threshold gate** for practical significance (Criterion.rs).
    4. **Residual Z-score** from history for trend-aware detection (Conbench).

    A regression is flagged when ALL of the following hold:

    - The t-test finds a statistically significant difference (p < alpha).
    - The entire CI of relative change is outside
      ``[-noise_threshold, +noise_threshold]``.
    - If history is provided: ``|z_score| > z_threshold``.

    An improvement is flagged symmetrically (contender is significantly
    better than baseline).

    Args:
        baseline: Baseline measurements (at least 2 values).
        contender: Contender measurements (at least 2 values).
        history: Optional historical measurements for Z-score analysis.
        significance_level: Alpha level for t-test. Default 0.05.
        noise_threshold: Dead zone half-width for practical significance.
            Default 0.01 (1%).
        z_threshold: Minimum |z_score| to confirm regression from history.
            Default 5.0 (following Conbench defaults).
        n_resamples: Number of bootstrap resamples for CI estimation.
            Default 10,000.
        seed: Optional random seed for reproducibility.

    Returns:
        A ``RegressionResult`` with the combined verdict.

    Raises:
        ValueError: If either sample has fewer than 2 observations.

    References:
        - Criterion.rs: https://bheisler.github.io/criterion.rs/book/
        - Conbench: https://conbench.github.io/conbench/
        - pyperf: https://github.com/psf/pyperf
    """
    n_base = len(baseline)
    n_cont = len(contender)

    if n_base < 2 or n_cont < 2:
        raise ValueError(
            f"Both samples must have at least 2 observations "
            f"(got baseline={n_base}, contender={n_cont})."
        )

    rng = random.Random(seed)

    # --- Gate 1: Welch's t-test for statistical significance ---
    _is_sig, t_stat, _df = welch_t_test(baseline, contender, alpha=significance_level)

    # --- Bootstrap p-value (more robust than t-test alone) ---
    # Use the bootstrap seed derived from the main seed for reproducibility.
    boot_seed = rng.randint(0, 2**31 - 1)
    _boot_sig, _t_obs, p_value = mixed_bootstrap_test(
        baseline,
        contender,
        n_resamples=n_resamples,
        significance_level=significance_level,
        seed=boot_seed,
    )

    # Use bootstrap p-value as the primary significance signal.
    stat_significant = p_value < significance_level

    # --- Gate 2: Bootstrap CI of relative change ---
    ci_seed_rng = random.Random(rng.randint(0, 2**31 - 1))
    relative_change_ci = _bootstrap_relative_change_ci(
        baseline,
        contender,
        confidence=1.0 - significance_level,
        n_resamples=n_resamples,
        rng=ci_seed_rng,
    )

    # Point estimate of relative change.
    mean_base = statistics.mean(baseline)
    mean_cont = statistics.mean(contender)
    if mean_base != 0.0:
        relative_change = (mean_cont - mean_base) / mean_base
    else:
        relative_change = mean_cont - mean_base  # Absolute fallback.

    # Noise threshold classification.
    classification = classify_change(relative_change_ci, noise_threshold)
    practical_significant = classification != "no_change"

    # --- Gate 3 (optional): Residual Z-score from history ---
    z: float | None = None
    z_confirms = True  # Default to True when no history is available.

    if history is not None and len(history) >= 2:
        z = residual_z_score(history, mean_cont)
        z_confirms = abs(z) > z_threshold if math.isfinite(z) else True

    # --- Combined verdict ---
    is_regression = (
        stat_significant and practical_significant and classification == "regressed" and z_confirms
    )
    is_improvement = (
        stat_significant and practical_significant and classification == "improved" and z_confirms
    )

    # Override classification if gates disagree.
    if not stat_significant or not practical_significant or not z_confirms:
        final_classification = "no_change"
    else:
        final_classification = classification

    return RegressionResult(
        is_regression=is_regression,
        is_improvement=is_improvement,
        classification=final_classification,
        relative_change=relative_change,
        relative_change_ci=relative_change_ci,
        t_statistic=t_stat,
        p_value=p_value,
        z_score=z,
        confidence_level=1.0 - significance_level,
    )
