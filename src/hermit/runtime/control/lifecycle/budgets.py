from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Deadline:
    started_at: float
    soft_at: float
    hard_at: float

    @classmethod
    def start(cls, *, soft_seconds: float, hard_seconds: float) -> Deadline:
        now = time.monotonic()
        soft = max(float(soft_seconds), 0.0)
        hard = max(float(hard_seconds), soft)
        return cls(started_at=now, soft_at=now + soft, hard_at=now + hard)

    def soft_remaining(self, *, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        return max(0.0, self.soft_at - current)

    def hard_remaining(self, *, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        return max(0.0, self.hard_at - current)

    def soft_exceeded(self, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        return current >= self.soft_at

    def hard_exceeded(self, *, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        return current >= self.hard_at


@dataclass(frozen=True)
class ExecutionBudget:
    ingress_ack_deadline: float = 5.0
    provider_connect_timeout: float = 5.0
    provider_read_timeout: float = 120.0
    provider_stream_idle_timeout: float = 600.0
    tool_soft_deadline: float = 30.0
    tool_hard_deadline: float = 600.0
    observation_window: float = 600.0
    observation_poll_interval: float = 5.0

    def tool_deadline(self) -> Deadline:
        return Deadline.start(
            soft_seconds=self.tool_soft_deadline,
            hard_seconds=self.tool_hard_deadline,
        )


_DEFAULT_BUDGET = ExecutionBudget()
_budget_lock = threading.Lock()
_runtime_budget = _DEFAULT_BUDGET


def configure_runtime_budget(budget: ExecutionBudget | None) -> None:
    global _runtime_budget
    with _budget_lock:
        _runtime_budget = budget or _DEFAULT_BUDGET


def get_runtime_budget() -> ExecutionBudget:
    with _budget_lock:
        return _runtime_budget
