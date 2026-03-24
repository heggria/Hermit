from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()

_AGE_BONUS_CAP = 10
_BLOCKED_BONUS = 10
_RISK_PENALTIES: dict[str, int] = {
    "default": 5,
    "critical": 20,
    "elevated": 10,
    # "autonomous" tasks operate without human approval gates; treat them the
    # same as "elevated" so they are not unfairly boosted over supervised tasks.
    "autonomous": 10,
}


@dataclass(frozen=True)
class PriorityScore:
    task_id: str
    raw_score: int
    risk_penalty: int
    age_bonus: int
    blocked_bonus: int
    final_score: int
    reason: str


class TaskPrioritizer:
    """Scores tasks and picks the best candidate after a task is parked."""

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def score_task(self, task_id: str) -> PriorityScore | None:
        """Compute a priority score for a single task."""
        task = self._store.get_task(task_id)
        if task is None:
            return None

        # raw_score: use highest queue_priority from active step_attempts
        raw_score = 0
        attempts = self._store.list_step_attempts(task_id=task_id, limit=100)
        for attempt in attempts:
            if attempt.status in {"ready", "running"}:
                raw_score = max(raw_score, attempt.queue_priority)

        # risk_penalty based on policy_profile
        risk_penalty = _RISK_PENALTIES.get(task.policy_profile, 0)

        # age_bonus: hours since creation, capped
        now = time.time()
        age_hours = (now - task.created_at) / 3600
        age_bonus = min(int(age_hours), _AGE_BONUS_CAP)

        # blocked_bonus: if task was previously blocked and is now active
        blocked_bonus = 0
        events = self._store.list_events(task_id=task_id, limit=50)
        was_blocked = any(e.get("event_type") == "task.blocked" for e in events)
        if was_blocked and task.status in {"queued", "running"}:
            blocked_bonus = _BLOCKED_BONUS

        final_score = raw_score - risk_penalty + age_bonus + blocked_bonus
        reason = f"raw={raw_score} risk=-{risk_penalty} age=+{age_bonus} blocked=+{blocked_bonus}"
        return PriorityScore(
            task_id=task_id,
            raw_score=raw_score,
            risk_penalty=risk_penalty,
            age_bonus=age_bonus,
            blocked_bonus=blocked_bonus,
            final_score=final_score,
            reason=reason,
        )

    def best_candidate_after_park(self, parked_task_id: str, conversation_id: str) -> str | None:
        """Find the highest-scoring active task in a conversation, excluding the parked one."""
        tasks = self._store.list_open_tasks_for_conversation(
            conversation_id=conversation_id, limit=100
        )
        candidates = [
            t for t in tasks if t.task_id != parked_task_id and t.status in {"queued", "running"}
        ]
        if not candidates:
            return None

        scored: list[PriorityScore] = []
        for t in candidates:
            s = self.score_task(t.task_id)
            if s is not None:
                scored.append(s)

        if not scored:
            return None

        scored.sort(key=lambda s: s.final_score, reverse=True)
        best = scored[0]
        log.info(
            "prioritizer_best_candidate",
            parked_task_id=parked_task_id,
            best_task_id=best.task_id,
            final_score=best.final_score,
            reason=best.reason,
        )
        return best.task_id

    def recalculate_priorities(self, conversation_id: str | None = None) -> list[PriorityScore]:
        """Score all active tasks, optionally filtered by conversation."""
        if conversation_id:
            tasks = self._store.list_open_tasks_for_conversation(
                conversation_id=conversation_id, limit=100
            )
        else:
            # Deduplicate by task_id: a task may transition between "running" and
            # "queued" between the two store calls, causing it to appear twice.
            seen: set[str] = set()
            tasks = []
            for t in self._store.list_tasks(status="running", limit=200):
                if t.task_id not in seen:
                    seen.add(t.task_id)
                    tasks.append(t)
            for t in self._store.list_tasks(status="queued", limit=200):
                if t.task_id not in seen:
                    seen.add(t.task_id)
                    tasks.append(t)

        scores: list[PriorityScore] = []
        for t in tasks:
            s = self.score_task(t.task_id)
            if s is not None:
                scores.append(s)

        scores.sort(key=lambda s: s.final_score, reverse=True)
        return scores
