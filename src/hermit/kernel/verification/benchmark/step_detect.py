from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "L1Dist",
    "StepChange",
    "StepDetectionResult",
    "detect_steps",
    "golden_search",
    "solve_potts",
    "solve_potts_approx",
    "solve_potts_autogamma",
    "weighted_median",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_EVICTION_LIMIT = 500_000
_GOLDEN_RATIO = (math.sqrt(5.0) - 1.0) / 2.0  # ~0.618


# ---------------------------------------------------------------------------
# Weighted median
# ---------------------------------------------------------------------------


def weighted_median(values: list[float], weights: list[float]) -> float:
    """Compute weighted median.

    Sorts by value, then finds the point where cumulative weight crosses 50%
    of the total weight.  For even splits the value at the crossing point is
    returned (no interpolation).

    Args:
        values: Observed values.
        weights: Non-negative weights (same length as *values*).

    Returns:
        The weighted median value.

    Raises:
        ValueError: If *values* is empty or lengths mismatch.
    """
    if not values:
        msg = "values must be non-empty"
        raise ValueError(msg)
    if len(values) != len(weights):
        msg = f"values and weights must have the same length ({len(values)} != {len(weights)})"
        raise ValueError(msg)

    if len(values) == 1:
        return values[0]

    # Sort (value, weight) pairs by value without mutating input lists.
    pairs = sorted(zip(values, weights, strict=True), key=lambda p: p[0])

    total_weight = sum(w for _, w in pairs)
    if total_weight <= 0.0:
        # All weights zero — fall back to unweighted median (middle element).
        return pairs[len(pairs) // 2][0]

    half = total_weight / 2.0
    cumulative = 0.0
    for val, w in pairs:
        cumulative += w
        if cumulative >= half:
            return val

    # Unreachable for well-formed input, but satisfy the type checker.
    return pairs[-1][0]


# ---------------------------------------------------------------------------
# L1 (Laplace) distance calculator with memoization
# ---------------------------------------------------------------------------


class L1Dist:
    """Memoized L1 (Laplace) distance calculator for intervals.

    For interval ``[l, r]`` the distance is defined as::

        dist(l, r) = sum(w_i * |y_i - median(y[l:r+1])|)  for i in [l, r]

    where the median is the *weighted* median using the corresponding weights.

    Caches both ``mu(l, r)`` and ``dist(l, r)`` results.  When the total
    number of cached entries exceeds 500,000 the caches are cleared to bound
    memory usage.

    Reference: Friedrich, F. et al. (2008). Complexity penalized M-estimation:
    fast computation. *Journal of Computational and Graphical Statistics*,
    17(1), 201-224.
    """

    def __init__(self, y: list[float], w: list[float]) -> None:
        # Store copies to guarantee immutability of internal state.
        self._y: list[float] = list(y)
        self._w: list[float] = list(w)
        self._n: int = len(y)
        self._mu_cache: dict[tuple[int, int], float] = {}
        self._dist_cache: dict[tuple[int, int], float] = {}

    def _maybe_evict(self) -> None:
        """Clear caches if total entry count exceeds the eviction limit."""
        if len(self._mu_cache) + len(self._dist_cache) > _CACHE_EVICTION_LIMIT:
            self._mu_cache.clear()
            self._dist_cache.clear()

    def mu(self, l: int, r: int) -> float:  # noqa: E741  (ambiguous name 'l')
        """Weighted median of ``y[l:r+1]``."""
        key = (l, r)
        cached = self._mu_cache.get(key)
        if cached is not None:
            return cached

        self._maybe_evict()

        if l == r:
            result = self._y[l]
        else:
            result = weighted_median(self._y[l : r + 1], self._w[l : r + 1])

        self._mu_cache[key] = result
        return result

    def dist(self, l: int, r: int) -> float:  # noqa: E741
        """L1 cost of interval ``[l, r]``."""
        key = (l, r)
        cached = self._dist_cache.get(key)
        if cached is not None:
            return cached

        self._maybe_evict()

        median_val = self.mu(l, r)
        total = 0.0
        for i in range(l, r + 1):
            total += self._w[i] * abs(self._y[i] - median_val)

        self._dist_cache[key] = total
        return total


# ---------------------------------------------------------------------------
# Exact windowed Potts DP solver
# ---------------------------------------------------------------------------


def solve_potts(
    y: list[float],
    w: list[float],
    gamma: float,
    min_size: int = 2,
    max_size: int = 20,
) -> tuple[list[int], list[float], float]:
    """Solve the Potts problem via windowed Bellman dynamic programming.

    Minimizes the objective::

        gamma * k + sum(w_i * |y_i - mu_r|)

    where *k* is the number of segments and *mu_r* is the weighted median of
    the segment containing observation *i*.

    Uses the Bellman recurrence from Friedrich et al. (2008)::

        B[0] = -gamma
        B[r+1] = min over l in window of: B[l] + gamma + dist(l, r)

    The window is ``[max(0, r - max_size + 1), r - min_size + 1]``, giving
    O(n * max_size) complexity.

    Args:
        y: Observations.
        w: Weights (same length as *y*).
        gamma: Penalty per additional segment.
        min_size: Minimum segment length.
        max_size: Maximum segment length.

    Returns:
        Tuple of ``(rights, values, total_cost)`` where *rights* lists the
        right endpoint (inclusive) of each segment, *values* lists the
        weighted median of each segment, and *total_cost* is the Potts cost.
    """
    n = len(y)
    if n == 0:
        return ([], [], 0.0)

    # Ensure min_size is at least 1 and max_size >= min_size.
    min_size = max(1, min_size)
    max_size = max(min_size, max_size)

    dist_calc = L1Dist(y, w)

    # B[j] = optimal cost for the first j observations.
    # B[0] = -gamma (adding the first segment's gamma cancels this).
    b = [0.0] * (n + 1)
    b[0] = -gamma

    # back[j] stores the optimal left endpoint for the segment ending at j-1.
    back: list[int] = [0] * (n + 1)

    for r in range(n):
        # Window of valid left endpoints for a segment ending at index r.
        l_min = max(0, r - max_size + 1)
        l_max = max(l_min, r - min_size + 1)

        best_cost = math.inf
        best_l = l_min

        for l in range(l_min, l_max + 1):  # noqa: E741
            cost = b[l] + gamma + dist_calc.dist(l, r)
            if cost < best_cost:
                best_cost = cost
                best_l = l

        b[r + 1] = best_cost
        back[r + 1] = best_l

    # Trace back to recover segments.
    rights: list[int] = []
    values: list[float] = []
    pos = n
    while pos > 0:
        l = back[pos]  # noqa: E741
        r = pos - 1
        rights.append(r)
        values.append(dist_calc.mu(l, r))
        pos = l

    rights.reverse()
    values.reverse()

    return (rights, values, b[n])


# ---------------------------------------------------------------------------
# Three-phase approximate Potts solver
# ---------------------------------------------------------------------------


def solve_potts_approx(
    y: list[float],
    w: list[float],
    gamma: float,
    min_size: int = 2,
    max_size: int = 20,
) -> tuple[list[int], list[float], float]:
    """Three-phase approximate Potts solver (based on ASV).

    Phase 1: Windowed exact DP with ``max_size`` window -- O(n * W).
    Phase 2: Merge adjacent segments when removal reduces total cost -- O(k^2).
    Phase 3: Perturb each boundary by +/- ``max_size`` to refine -- O(k * W).

    Overall complexity: O(n) for typical data where k << n.

    Args:
        y: Observations.
        w: Weights (same length as *y*).
        gamma: Penalty per additional segment.
        min_size: Minimum segment length.
        max_size: Maximum segment length.

    Returns:
        Tuple of ``(rights, values, total_cost)`` -- see :func:`solve_potts`.
    """
    n = len(y)
    if n == 0:
        return ([], [], 0.0)

    dist_calc = L1Dist(y, w)

    # --- Phase 1: windowed exact DP ----------------------------------------
    rights, values, cost = solve_potts(y, w, gamma, min_size, max_size)

    if len(rights) <= 1:
        return (rights, values, cost)

    # Reconstruct left endpoints from rights.
    lefts = _rights_to_lefts(rights)

    # --- Phase 2: merge adjacent segments ----------------------------------
    lefts, rights = _merge_segments(lefts, rights, gamma, dist_calc)

    # --- Phase 3: boundary perturbation ------------------------------------
    lefts, rights = _perturb_boundaries(lefts, rights, max_size, dist_calc)

    # Recompute values and total cost for the final segmentation.
    final_values = [dist_calc.mu(lefts[i], rights[i]) for i in range(len(rights))]
    final_cost = gamma * len(rights)
    for i in range(len(rights)):
        final_cost += dist_calc.dist(lefts[i], rights[i])

    return (rights, final_values, final_cost)


def _rights_to_lefts(rights: list[int]) -> list[int]:
    """Derive left endpoints from right endpoints."""
    lefts = [0]
    for i in range(len(rights) - 1):
        lefts.append(rights[i] + 1)
    return lefts


def _merge_segments(
    lefts: list[int],
    rights: list[int],
    gamma: float,
    dist_calc: L1Dist,
) -> tuple[list[int], list[int]]:
    """Phase 2: greedily merge adjacent segments when it reduces total cost."""
    improved = True
    while improved and len(rights) > 1:
        improved = False
        best_gain = 0.0
        best_idx = -1

        for i in range(len(rights) - 1):
            # Cost of keeping segments i and i+1 separate.
            cost_separate = (
                gamma
                + dist_calc.dist(lefts[i], rights[i])
                + gamma
                + dist_calc.dist(lefts[i + 1], rights[i + 1])
            )
            # Cost of merging into one segment.
            cost_merged = gamma + dist_calc.dist(lefts[i], rights[i + 1])
            gain = cost_separate - cost_merged
            if gain > best_gain:
                best_gain = gain
                best_idx = i

        if best_idx >= 0 and best_gain > 0:
            # Merge segment best_idx with best_idx+1.
            new_lefts: list[int] = []
            new_rights: list[int] = []
            for i in range(len(rights)):
                if i == best_idx:
                    new_lefts.append(lefts[i])
                    new_rights.append(rights[i + 1])
                elif i == best_idx + 1:
                    continue
                else:
                    new_lefts.append(lefts[i])
                    new_rights.append(rights[i])
            lefts = new_lefts
            rights = new_rights
            improved = True

    return lefts, rights


def _perturb_boundaries(
    lefts: list[int],
    rights: list[int],
    max_size: int,
    dist_calc: L1Dist,
) -> tuple[list[int], list[int]]:
    """Phase 3: shift each internal boundary by up to +/- max_size."""
    if len(rights) <= 1:
        return lefts, rights

    for i in range(len(rights) - 1):
        boundary = rights[i]  # current boundary between segment i and i+1
        lo = max(lefts[i], boundary - max_size)
        hi = min(rights[i + 1] - 1, boundary + max_size)

        best_cost = math.inf
        best_boundary = boundary

        for b in range(lo, hi + 1):
            cost_left = dist_calc.dist(lefts[i], b)
            cost_right = dist_calc.dist(b + 1, rights[i + 1])
            total = cost_left + cost_right
            if total < best_cost:
                best_cost = total
                best_boundary = b

        # Apply the best boundary — build new lefts/rights immutably below,
        # but since we process left-to-right and only touch adjacent pairs,
        # in-place update is safe and equivalent here.
        rights[i] = best_boundary
        lefts[i + 1] = best_boundary + 1

    return lefts, rights


# ---------------------------------------------------------------------------
# Golden section search
# ---------------------------------------------------------------------------


def golden_search(
    f: Callable[[float], float],
    a: float,
    b: float,
    tol: float = 1e-3,
) -> float:
    """Golden section search for the minimum of a unimodal function.

    Finds the value *x* in ``[a, b]`` that minimizes ``f(x)``, to within
    tolerance *tol*.

    Reference: Kiefer, J. (1953). Sequential minimax search for a maximum.
    *Proceedings of the AMS*, 4(3), 502-506.

    Args:
        f: Unimodal function to minimize.
        a: Left bound of the search interval.
        b: Right bound of the search interval.
        tol: Convergence tolerance on the interval width.

    Returns:
        The *x* value that approximately minimizes *f*.
    """
    c = b - _GOLDEN_RATIO * (b - a)
    d = a + _GOLDEN_RATIO * (b - a)
    fc = f(c)
    fd = f(d)

    while abs(b - a) > tol:
        if fc < fd:
            b = d
            d = c
            fd = fc
            c = b - _GOLDEN_RATIO * (b - a)
            fc = f(c)
        else:
            a = c
            c = d
            fc = fd
            d = a + _GOLDEN_RATIO * (b - a)
            fd = f(d)

    return (a + b) / 2.0


# ---------------------------------------------------------------------------
# Auto-gamma selection via BIC
# ---------------------------------------------------------------------------


def solve_potts_autogamma(
    y: list[float],
    w: list[float] | None = None,
    min_size: int = 2,
    max_size: int = 20,
    beta_factor: float = 4.0,
) -> tuple[list[int], list[float]]:
    """Auto-select gamma using Schwarz BIC criterion (based on ASV).

    Algorithm:
      1. ``gamma_0`` = total L1 cost of a single-segment fit.
      2. ``beta = beta_factor * ln(n) / n``.
      3. Golden search over ``x in [ln(0.1 / n), 0]``:
         - ``gamma = gamma_0 * exp(x)``
         - Solve approximate Potts.
         - ``BIC = beta * k + ln(sigma_0 + sigma_star)``
      4. Return the best segmentation.

    ``sigma_0`` is an anti-overfitting noise floor defined as
    ``0.1 * min(|diff(values)|)`` over adjacent segment values (clamped to
    a small positive number when all segments have equal value).

    Reference: Schwarz, G. (1978). Estimating the dimension of a model.
    *Annals of Statistics*, 6(2), 461-464. Adapted from ASV's
    ``step_detect.py``.

    Args:
        y: Observations.  Must have at least 3 elements for meaningful
           segmentation (returns a single segment otherwise).
        w: Weights.  Defaults to uniform weights of 1.0.
        min_size: Minimum segment length.
        max_size: Maximum segment length.
        beta_factor: Multiplier for the BIC penalty term.

    Returns:
        Tuple ``(rights, values)`` of the optimal segmentation.
    """
    n = len(y)
    if n == 0:
        return ([], [])

    if w is None:
        w = [1.0] * n

    # Trivial cases.
    if n < 3:
        med = weighted_median(y, w)
        return ([n - 1], [med])

    # Step 1: gamma_0 = L1 cost of fitting the entire series as one segment.
    dist_calc = L1Dist(y, w)
    gamma_0 = dist_calc.dist(0, n - 1)

    # If the series is perfectly flat, no steps to detect.
    if gamma_0 == 0.0:
        return ([n - 1], [dist_calc.mu(0, n - 1)])

    # Step 2: BIC penalty coefficient.
    beta = beta_factor * math.log(n) / n

    # Step 3: golden search over log-gamma space.
    log_lower = math.log(0.1 / n)
    log_upper = 0.0

    def _bic(log_x: float) -> float:
        gamma = gamma_0 * math.exp(log_x)
        rights, values, _ = solve_potts_approx(y, w, gamma, min_size, max_size)
        k = len(rights)

        # sigma_star: average segment cost (residual noise).
        if k > 0 and n > 0:
            total_dist = sum(
                dist_calc.dist(
                    0 if i == 0 else rights[i - 1] + 1,
                    rights[i],
                )
                for i in range(k)
            )
            sigma_star = total_dist / n
        else:
            sigma_star = 0.0

        # sigma_0: noise floor to prevent overfitting.
        sigma_0 = _noise_floor(values)

        return beta * k + math.log(sigma_0 + sigma_star)

    best_log_x = golden_search(_bic, log_lower, log_upper)
    best_gamma = gamma_0 * math.exp(best_log_x)

    rights, values, _ = solve_potts_approx(y, w, best_gamma, min_size, max_size)
    return (rights, values)


def _noise_floor(values: list[float]) -> float:
    """Anti-overfitting noise floor: 0.1 * min(|diff(adjacent values)|).

    Returns a small positive number when all values are identical or when
    there is only one segment.
    """
    if len(values) < 2:
        return 1e-12

    diffs = [abs(values[i + 1] - values[i]) for i in range(len(values) - 1)]
    nonzero_diffs = [d for d in diffs if d > 0.0]

    if not nonzero_diffs:
        return 1e-12

    return 0.1 * min(nonzero_diffs)


# ---------------------------------------------------------------------------
# Step change dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepChange:
    """A detected step change in benchmark history.

    Attributes:
        position: Index where the change occurs (first index of the new
            segment).
        value_before: Weighted median of the segment before the change.
        value_after: Weighted median of the segment after the change.
        relative_change: ``(after - before) / before``.  Positive means
            the value increased.
        is_regression: ``True`` if performance got worse (higher execution
            time).
    """

    position: int
    value_before: float
    value_after: float
    relative_change: float
    is_regression: bool


@dataclass(frozen=True)
class StepDetectionResult:
    """Result of step detection on a benchmark time series.

    Attributes:
        segments: List of ``(start, end, value)`` tuples describing each
            detected constant-value segment.
        steps: List of :class:`StepChange` objects at segment boundaries.
        latest_value: Weighted median of the final segment.
        best_value: Lowest segment value observed (best performance).
    """

    segments: list[tuple[int, int, float]]
    steps: list[StepChange]
    latest_value: float
    best_value: float


# ---------------------------------------------------------------------------
# High-level detection entry point
# ---------------------------------------------------------------------------


def detect_steps(
    values: list[float],
    weights: list[float] | None = None,
    min_size: int = 2,
) -> StepDetectionResult:
    """Detect structural breakpoints in a benchmark time series.

    Uses the simplified Potts model with automatic gamma selection
    (:func:`solve_potts_autogamma`) to partition *values* into piecewise-
    constant segments, then identifies step changes at segment boundaries.

    Args:
        values: Benchmark observations ordered by time.  Not mutated.
        weights: Per-observation weights.  Defaults to uniform 1.0.
        min_size: Minimum segment length (passed through to the solver).

    Returns:
        A :class:`StepDetectionResult` with segments and detected steps.

    Raises:
        ValueError: If *values* is empty.
    """
    if not values:
        msg = "values must be non-empty"
        raise ValueError(msg)

    n = len(values)
    if weights is None:
        weights = [1.0] * n

    # Trivial cases: too few observations for meaningful segmentation.
    if n < 3:
        med = weighted_median(values, weights)
        segments = [(0, n - 1, med)]
        return StepDetectionResult(
            segments=segments,
            steps=[],
            latest_value=med,
            best_value=med,
        )

    # Run auto-gamma Potts segmentation.
    rights, seg_values = solve_potts_autogamma(
        values,
        weights,
        min_size=min_size,
    )

    # Build segment tuples.
    segments: list[tuple[int, int, float]] = []
    for i, (r, v) in enumerate(zip(rights, seg_values, strict=True)):
        l = 0 if i == 0 else rights[i - 1] + 1  # noqa: E741
        segments.append((l, r, v))

    # Identify step changes at segment boundaries.
    steps: list[StepChange] = []
    for i in range(len(segments) - 1):
        _, _, val_before = segments[i]
        start_after, _, val_after = segments[i + 1]

        if val_before == 0.0:
            # Avoid division by zero; use absolute change as a fallback.
            relative = float("inf") if val_after != 0.0 else 0.0
        else:
            relative = (val_after - val_before) / abs(val_before)

        steps.append(
            StepChange(
                position=start_after,
                value_before=val_before,
                value_after=val_after,
                relative_change=relative,
                is_regression=val_after > val_before,
            )
        )

    latest_value = seg_values[-1] if seg_values else values[-1]
    best_value = min(seg_values) if seg_values else min(values)

    return StepDetectionResult(
        segments=segments,
        steps=steps,
        latest_value=latest_value,
        best_value=best_value,
    )
