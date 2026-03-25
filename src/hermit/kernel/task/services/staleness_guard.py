"""Watchdog that fails tasks stuck in non-terminal states beyond TTL.

Scans for tasks with updated_at older than the configured TTL and
transitions them to FAILED with reason 'state_timeout_exceeded'.
"""

from __future__ import annotations

import time

import structlog

from hermit.kernel.task.state.enums import TaskState

__all__ = [
    "StalenessGuard",
]

log = structlog.get_logger()

# States where PAUSED does not allow a direct transition to FAILED.
# For PAUSED tasks, we use CANCELLED instead (valid per transition table).
_PAUSED_TERMINAL = "cancelled"
_DEFAULT_TERMINAL = "failed"

# Reason attached to events emitted by the staleness guard.
_TIMEOUT_REASON = "state_timeout_exceeded"


class StalenessGuard:
    """Watchdog that fails tasks stuck in non-terminal states beyond TTL.

    Scans for tasks with updated_at older than the configured TTL and
    transitions them to FAILED with reason 'state_timeout_exceeded'.

    For PAUSED tasks, the guard transitions to CANCELLED because the
    formal state machine does not allow PAUSED -> FAILED directly.
    """

    DEFAULT_TTL_SECONDS: int = 7 * 24 * 3600  # 7 days

    WATCHABLE_STATES: frozenset[str] = frozenset(
        {
            TaskState.PLANNING_READY,
            TaskState.PAUSED,
            TaskState.NEEDS_ATTENTION,
            TaskState.BLOCKED,
            TaskState.RECONCILING,
        }
    )

    def __init__(self, store: object, *, ttl_seconds: int | None = None) -> None:
        self.store = store
        self.ttl = ttl_seconds or self.DEFAULT_TTL_SECONDS

    def sweep(self) -> list[str]:
        """Scan and fail stale tasks. Returns list of affected task_ids."""
        cutoff = time.time() - self.ttl
        affected: list[str] = []

        for state in sorted(self.WATCHABLE_STATES):
            tasks = self.store.list_tasks(status=state, limit=500)
            for task in tasks:
                if task.updated_at < cutoff:
                    target_status = (
                        _PAUSED_TERMINAL if task.status == TaskState.PAUSED else _DEFAULT_TERMINAL
                    )
                    # Guard the status transition so that a single store failure
                    # (e.g. DB error, concurrent deletion, state-machine violation)
                    # does not abort the entire sweep and leave remaining stale
                    # tasks unprocessed.
                    try:
                        self.store.update_task_status(
                            task.task_id,
                            target_status,
                            payload={
                                "reason": _TIMEOUT_REASON,
                                "original_status": task.status,
                                "stale_seconds": int(time.time() - task.updated_at),
                            },
                        )
                    except Exception:
                        log.warning(
                            "staleness_guard.status_update_failed",
                            task_id=task.task_id,
                            original_status=task.status,
                            target_status=target_status,
                            exc_info=True,
                        )
                        continue

                    # C11: Resolve any active observation tickets for the stale
                    # task so they do not remain orphaned after timeout.
                    try:
                        self.store.resolve_observations_for_task(task.task_id, status="cancelled")
                    except Exception:
                        log.warning(
                            "staleness_guard.observation_cleanup_failed",
                            task_id=task.task_id,
                            exc_info=True,
                        )
                    log.info(
                        "staleness_guard.task_timed_out",
                        task_id=task.task_id,
                        original_status=task.status,
                        target_status=target_status,
                        stale_seconds=int(time.time() - task.updated_at),
                    )
                    affected.append(task.task_id)

        if affected:
            log.info(
                "staleness_guard.sweep_complete",
                affected_count=len(affected),
            )
        return affected

    def check_task(self, task_id: str) -> bool:
        """Check if a specific task is stale. Returns True if stale."""
        task = self.store.get_task(task_id)
        if task is None:
            return False
        if task.status not in self.WATCHABLE_STATES:
            return False
        cutoff = time.time() - self.ttl
        return task.updated_at < cutoff
