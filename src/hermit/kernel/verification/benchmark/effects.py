from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

__all__ = [
    "EffectSizeResult",
    "cliffs_delta",
    "cohens_d",
    "compare_effect_sizes",
    "geometric_mean_ratio",
    "glass_delta",
    "hedges_g",
    "vargha_delaney_a12",
]


# ---------------------------------------------------------------------------
# 1. Cohen's d and Hedges' g
# ---------------------------------------------------------------------------


def cohens_d(a: list[float], b: list[float]) -> float:
    """Cohen's d with pooled standard deviation.

    Measures the standardised difference between two group means using the
    pooled within-group standard deviation as the denominator.

    Thresholds (Cohen, 1988):
        |d| < 0.2  — negligible
        0.2 - 0.5  — small
        0.5 - 0.8  — medium
        >= 0.8     — large

    References:
        Cohen, J. (1988). *Statistical Power Analysis for the Behavioral
        Sciences* (2nd ed.). Lawrence Erlbaum Associates.

    Returns 0.0 when either sample is empty or pooled SD is zero.
    """
    na, nb = len(a), len(b)
    if na < 1 or nb < 1:
        return 0.0

    mean_a = statistics.mean(a)
    mean_b = statistics.mean(b)

    if na < 2 or nb < 2:
        # Cannot compute variance with fewer than 2 observations per group.
        return 0.0

    var_a = statistics.variance(a)
    var_b = statistics.variance(b)

    pooled_var = ((na - 1) * var_a + (nb - 1) * var_b) / (na + nb - 2)
    pooled_sd = math.sqrt(pooled_var)

    if pooled_sd == 0.0:
        return 0.0

    return (mean_a - mean_b) / pooled_sd


def hedges_g(a: list[float], b: list[float]) -> float:
    """Bias-corrected Cohen's d (Hedges' g).

    Applies the exact correction factor J to remove small-sample bias from
    Cohen's d.  Preferred when either group has n < 20.

    Correction factor:
        J = 1 - 3 / (4·(n₁ + n₂) - 9)

    References:
        Hedges, L. V. (1981). Distribution theory for Glass's estimator of
        effect size and related estimators. *Journal of Educational
        Statistics*, 6(2), 107-128.

    Returns 0.0 when either sample is empty or pooled SD is zero.
    """
    na, nb = len(a), len(b)
    d = cohens_d(a, b)

    df = na + nb - 2
    if df <= 0:
        return 0.0

    # Exact correction factor (Hedges, 1981).
    denom = 4 * df - 1
    if denom <= 0:
        return d
    correction = 1.0 - 3.0 / (4.0 * df - 1.0)
    return d * correction


# ---------------------------------------------------------------------------
# 2. Glass's Delta
# ---------------------------------------------------------------------------


def glass_delta(treatment: list[float], control: list[float]) -> float:
    """Glass's delta — uses the control group SD as the denominator.

    Appropriate when one group is a well-established baseline whose variability
    is considered the reference scale (e.g. comparing a new optimisation
    against a stable production baseline).

    References:
        Glass, G. V. (1976). Primary, secondary, and meta-analysis of
        research. *Educational Researcher*, 5(10), 3-8.

    Returns 0.0 when either sample is too small or control SD is zero.
    """
    if len(treatment) < 1 or len(control) < 2:
        return 0.0

    mean_treatment = statistics.mean(treatment)
    mean_control = statistics.mean(control)
    sd_control = statistics.stdev(control)

    if sd_control == 0.0:
        return 0.0

    return (mean_treatment - mean_control) / sd_control


# ---------------------------------------------------------------------------
# 3. Cliff's Delta (non-parametric)
# ---------------------------------------------------------------------------


def cliffs_delta(a: list[float], b: list[float]) -> float:
    """Cliff's delta — non-parametric effect size measure.

    Computes the probability that a randomly chosen observation from *a*
    is larger than one from *b*, minus the reverse probability.

    Range: [-1, +1].  Positive values indicate *a* tends to be larger.

    Thresholds (Romano et al., 2006):
        |delta| < 0.147   — negligible
        0.147 - 0.33  — small
        0.33  - 0.474 — medium
        >= 0.474      — large

    Implementation uses O(n log n) rank-sum rather than O(n^2) pairwise
    comparison.

    References:
        Cliff, N. (1993). Dominance statistics: Ordinal analyses to answer
        ordinal questions. *Psychological Bulletin*, 114(3), 494-509.

        Romano, J., Kromrey, J. D., Coraggio, J., & Skowronek, J. (2006).
        Appropriate statistics for ordinal level data: Should we really be
        using t-test and Cohen's d for evaluating group differences on the
        NSSE and similar surveys? *Annual Meeting of the Florida Association
        of Institutional Research*.

    Returns 0.0 when either sample is empty.
    """
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return 0.0

    # Compute via rank-sum (O(n log n)).
    # Cliff's delta = (2 * R_a - n_a * (n_a + n_b + 1)) / (n_a * n_b)
    # where R_a is the rank sum of group a in the combined sample.
    rank_sum_a = _rank_sum(a, b)
    return (2.0 * rank_sum_a - na * (na + nb + 1)) / (na * nb)


# ---------------------------------------------------------------------------
# 4. Vargha-Delaney A₁₂
# ---------------------------------------------------------------------------


def vargha_delaney_a12(a: list[float], b: list[float]) -> float:
    """A₁₂ statistic (Vargha & Delaney, 2000).

    Estimates P(X > Y) + 0.5 * P(X = Y) for observations drawn from *a* and
    *b* respectively.

    Interpretation:
        0.50 — no difference
        0.56 — small
        0.64 — medium
        0.71 — large

    Relationship to Cliff's delta:
        A₁₂ = (delta + 1) / 2

    Uses rank-sum method for O(n log n) efficiency:
        A₁₂ = (R₁ - n₁(n₁+1)/2) / (n₁ * n₂)

    References:
        Vargha, A. & Delaney, H. D. (2000). A critique and improvement of
        the CL common language effect size statistics of McGraw and Wong.
        *Journal of Educational and Behavioral Statistics*, 25(2), 101-132.

        Arcuri, A. & Briand, L. (2011). A practical guide for using
        statistical tests to assess randomized algorithms in software
        engineering. *Proc. ICSE 2011*, 1-10.

    Returns 0.5 when either sample is empty (no effect).
    """
    na, nb = len(a), len(b)
    if na == 0 or nb == 0:
        return 0.5

    rank_sum_a = _rank_sum(a, b)
    return (rank_sum_a - na * (na + 1) / 2.0) / (na * nb)


# ---------------------------------------------------------------------------
# Shared rank-sum helper
# ---------------------------------------------------------------------------


def _rank_sum(a: list[float], b: list[float]) -> float:
    """Return the sum of ranks for group *a* in the combined sample.

    Ties are handled via mid-rank averaging.  Runs in O(n log n) where
    n = len(a) + len(b).
    """
    # Tag each observation with its group: 0 = a, 1 = b.
    combined: list[tuple[float, int, int]] = []
    for i, val in enumerate(a):
        combined.append((val, 0, i))
    for i, val in enumerate(b):
        combined.append((val, 1, i))

    combined.sort(key=lambda t: t[0])

    n = len(combined)
    ranks = [0.0] * n

    # Assign mid-ranks for tied groups.
    i = 0
    while i < n:
        j = i
        while j < n and combined[j][0] == combined[i][0]:
            j += 1
        # Positions i..j-1 share the same value; assign mid-rank.
        mid_rank = (i + 1 + j) / 2.0  # 1-based rank average
        for k in range(i, j):
            ranks[k] = mid_rank
        i = j

    # Sum ranks belonging to group a (tag == 0).
    return sum(ranks[k] for k in range(n) if combined[k][1] == 0)


# ---------------------------------------------------------------------------
# 5. Effect Size Classification
# ---------------------------------------------------------------------------

_CLIFF_NEGLIGIBLE = 0.147
_CLIFF_SMALL = 0.33
_CLIFF_MEDIUM = 0.474


@dataclass(frozen=True)
class EffectSizeResult:
    """Immutable effect size comparison result.

    Bundles all common effect size metrics with a human-readable
    classification and direction indicator.
    """

    cohens_d: float
    hedges_g: float
    cliffs_delta: float
    a12: float
    glass_delta: float
    classification: str  # "negligible" | "small" | "medium" | "large"
    direction: str  # "faster" | "slower" | "equivalent"


def compare_effect_sizes(
    baseline: list[float],
    contender: list[float],
) -> EffectSizeResult:
    """Compute all effect size metrics and classify the overall effect.

    Classification logic:
        - Primary: Cliff's delta (non-parametric, most robust for benchmarks
          with non-normal distributions and outliers).
        - Secondary: Cohen's d (for academic reporting compatibility).
        - Direction: based on the sign of Cliff's delta.  A *negative* value
          means the contender tends to produce *smaller* values — interpreted
          as "faster" for time-based metrics.

    Thresholds follow Romano et al. (2006) for Cliff's delta:
        |delta| < 0.147  — negligible
        0.147 - 0.33     — small
        0.33  - 0.474    — medium
        >= 0.474         — large

    References:
        Romano, J. et al. (2006). See :func:`cliffs_delta`.
        Cohen, J. (1988). See :func:`cohens_d`.
        Arcuri, A. & Briand, L. (2011). See :func:`vargha_delaney_a12`.
    """
    d = cohens_d(contender, baseline)
    g = hedges_g(contender, baseline)
    delta = cliffs_delta(contender, baseline)
    a12 = vargha_delaney_a12(contender, baseline)
    gd = glass_delta(contender, baseline)

    abs_delta = abs(delta)

    if abs_delta < _CLIFF_NEGLIGIBLE:
        classification = "negligible"
    elif abs_delta < _CLIFF_SMALL:
        classification = "small"
    elif abs_delta < _CLIFF_MEDIUM:
        classification = "medium"
    else:
        classification = "large"

    if classification == "negligible":
        direction = "equivalent"
    elif delta < 0:
        # Contender produces smaller values (faster for time metrics).
        direction = "faster"
    else:
        # Contender produces larger values (slower for time metrics).
        direction = "slower"

    return EffectSizeResult(
        cohens_d=d,
        hedges_g=g,
        cliffs_delta=delta,
        a12=a12,
        glass_delta=gd,
        classification=classification,
        direction=direction,
    )


# ---------------------------------------------------------------------------
# 6. Geometric Mean Ratio
# ---------------------------------------------------------------------------


def geometric_mean_ratio(baseline: list[float], contender: list[float]) -> float:
    """Geometric mean of ratios — standard for aggregate multi-benchmark comparison.

    Used by SPEC CPU and similar benchmark suites to aggregate across
    heterogeneous workloads where arithmetic means can be misleading.

    Behaviour:
        - **Paired** (equal-length inputs): computes the geometric mean of
          element-wise ratios ``contender[i] / baseline[i]``.  Pairs where the
          baseline value is non-positive are skipped.
        - **Unpaired** (different-length inputs): computes
          ``mean(contender) / mean(baseline)`` and returns that single ratio
          (which is trivially its own geometric mean).

    A return value < 1.0 means the contender is faster / smaller on average;
    > 1.0 means slower / larger.

    Returns 1.0 (no effect) when inputs are empty, all-zero, or otherwise
    degenerate.

    References:
        Fleming, P. J. & Wallace, J. J. (1986). How not to lie with
        statistics: the correct way to summarize benchmark results.
        *Communications of the ACM*, 29(3), 218-221.
    """
    nb, nc = len(baseline), len(contender)

    if nb == 0 or nc == 0:
        return 1.0

    if nb == nc:
        # Paired: geometric mean of element-wise ratios.
        log_sum = 0.0
        valid = 0
        for i in range(nb):
            if baseline[i] > 0.0 and contender[i] > 0.0:
                log_sum += math.log(contender[i] / baseline[i])
                valid += 1
        if valid == 0:
            return 1.0
        return math.exp(log_sum / valid)

    # Unpaired: ratio of means.
    mean_b = statistics.mean(baseline)
    mean_c = statistics.mean(contender)

    if mean_b <= 0.0 or mean_c <= 0.0:
        return 1.0

    return mean_c / mean_b
