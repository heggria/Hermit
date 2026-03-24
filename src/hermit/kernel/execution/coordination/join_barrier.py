from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hermit.kernel.ledger.journal.store import KernelStore


class JoinStrategy(StrEnum):
    ALL_REQUIRED = "all_required"
    ANY_SUFFICIENT = "any_sufficient"
    MAJORITY = "majority"
    BEST_EFFORT = "best_effort"


_SUCCEEDED_STATUSES = frozenset({"succeeded", "completed", "skipped"})
_FAILED_STATUSES = frozenset({"failed", "needs_attention"})
_TERMINAL_STATUSES = _SUCCEEDED_STATUSES | _FAILED_STATUSES


@dataclass(frozen=True)
class JoinBarrierResult:
    satisfied: bool
    strategy: JoinStrategy
    total: int
    succeeded: int
    failed: int
    pending: int
    missing: int  # deps whose step records could not be found in the store


class JoinBarrierService:
    """Evaluate join barriers for DAG step dependencies."""

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def evaluate(self, task_id: str, step_id: str) -> JoinBarrierResult:
        """Check whether the join barrier for a step is satisfied.

        A dependency step that cannot be found in the store is counted as
        *missing* and treated as still-pending (not succeeded, not failed).
        This prevents ``BEST_EFFORT`` barriers from resolving prematurely
        when store reads fail transiently, and makes the gap observable via
        ``JoinBarrierResult.missing``.
        """
        step = self._store.get_step(step_id)
        if step is None:
            return JoinBarrierResult(
                satisfied=False,
                strategy=JoinStrategy.ALL_REQUIRED,
                total=0,
                succeeded=0,
                failed=0,
                pending=0,
                missing=0,
            )

        deps = step.depends_on
        if not deps:
            return JoinBarrierResult(
                satisfied=True,
                strategy=JoinStrategy(step.join_strategy),
                total=0,
                succeeded=0,
                failed=0,
                pending=0,
                missing=0,
            )

        statuses: dict[str, str] = {}
        missing = 0
        for dep_id in deps:
            dep_step = self._store.get_step(dep_id)
            if dep_step is not None:
                statuses[dep_id] = dep_step.status
            else:
                missing += 1

        total = len(deps)
        succeeded = sum(1 for s in statuses.values() if s in _SUCCEEDED_STATUSES)
        failed = sum(1 for s in statuses.values() if s in _FAILED_STATUSES)
        # missing deps are treated as pending so barriers cannot resolve
        # while some dependency records are unreadable.
        pending = total - succeeded - failed

        strategy = JoinStrategy(step.join_strategy)
        satisfied = _evaluate_strategy(strategy, total, succeeded, failed, missing)

        return JoinBarrierResult(
            satisfied=satisfied,
            strategy=strategy,
            total=total,
            succeeded=succeeded,
            failed=failed,
            pending=pending,
            missing=missing,
        )

    def check_failure_cascade(self, task_id: str, failed_step_id: str) -> list[str]:
        """Return step_ids that should be cascade-failed due to a step failure."""
        return self._store.propagate_step_failure(task_id, failed_step_id)


def _evaluate_strategy(
    strategy: JoinStrategy,
    total: int,
    succeeded: int,
    failed: int,
    missing: int = 0,
) -> bool:
    """Return True when *strategy* is satisfied given the current dep counts.

    ``missing`` deps (store look-up failures) are treated as pending so that
    no strategy resolves while dependency records are unavailable.
    """
    terminal = succeeded + failed
    if strategy == JoinStrategy.ALL_REQUIRED:
        return succeeded == total and missing == 0
    elif strategy == JoinStrategy.ANY_SUFFICIENT:
        return succeeded >= 1
    elif strategy == JoinStrategy.MAJORITY:
        return succeeded > total / 2
    elif strategy == JoinStrategy.BEST_EFFORT:
        # All deps must reach a terminal state; missing records are not terminal.
        return terminal == total and missing == 0
    # Unreachable — all JoinStrategy members are handled above.
    raise ValueError(f"Unhandled join strategy: {strategy!r}")
