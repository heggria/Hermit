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

        A dependency whose step record cannot be found in the store is counted
        as *missing* and treated as failed for strategy evaluation purposes.
        This prevents missing records from silently inflating the pending count
        and masking data-integrity problems.
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
        missing_ids: list[str] = []
        for dep_id in deps:
            dep_step = self._store.get_step(dep_id)
            if dep_step is not None:
                statuses[dep_id] = dep_step.status
            else:
                missing_ids.append(dep_id)

        total = len(deps)
        succeeded = sum(1 for s in statuses.values() if s in _SUCCEEDED_STATUSES)
        # Missing deps are treated as failed for strategy evaluation.
        failed = sum(1 for s in statuses.values() if s in _FAILED_STATUSES) + len(missing_ids)
        pending = total - succeeded - failed

        strategy = JoinStrategy(step.join_strategy)
        satisfied = _evaluate_strategy(strategy, total, succeeded, failed)

        return JoinBarrierResult(
            satisfied=satisfied,
            strategy=strategy,
            total=total,
            succeeded=succeeded,
            failed=failed,
            pending=pending,
            missing=len(missing_ids),
        )

    def check_failure_cascade(self, task_id: str, failed_step_id: str) -> list[str]:
        """Return step_ids that should be cascade-failed due to a step failure."""
        return self._store.propagate_step_failure(task_id, failed_step_id)


def _evaluate_strategy(strategy: JoinStrategy, total: int, succeeded: int, failed: int) -> bool:
    terminal = succeeded + failed
    if strategy == JoinStrategy.ALL_REQUIRED:
        return succeeded == total
    elif strategy == JoinStrategy.ANY_SUFFICIENT:
        return succeeded >= 1
    elif strategy == JoinStrategy.MAJORITY:
        return succeeded > total / 2
    elif strategy == JoinStrategy.BEST_EFFORT:
        return terminal == total
    return succeeded == total
