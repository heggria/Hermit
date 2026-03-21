"""Data models for the Worker Pool / Slot abstraction.

Workers are role-bound executors that consume StepAttempts.  Each worker
occupies a *slot* inside a *pool*; the pool enforces per-role capacity
limits so the kernel can reason about concurrency without exposing raw
thread counts.

Spec alignment:
- worker types are open (``WorkerRole`` is extensible)
- worker instances are admission-controlled via ``WorkerSlotConfig.max_active``
- conflict domain limits (``max_same_workspace``, ``max_same_module``) live on
  ``WorkerPoolConfig.conflict_limits``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "DEFAULT_CONFLICT_LIMITS",
    "SlotStatus",
    "WorkerPoolConfig",
    "WorkerPoolStatus",
    "WorkerRole",
    "WorkerSlot",
    "WorkerSlotConfig",
]


class WorkerRole(StrEnum):
    """Role-bound executor identity.

    Spec: "worker 类型无限制，worker 实例强限流" — role *types* may grow freely;
    active *instances* per role are capacity-controlled by the pool.
    """

    planner = "planner"
    executor = "executor"
    verifier = "verifier"
    benchmarker = "benchmarker"
    researcher = "researcher"
    reconciler = "reconciler"
    tester = "tester"
    spec = "spec"


class SlotStatus(StrEnum):
    idle = "idle"
    busy = "busy"
    interrupted = "interrupted"


@dataclass
class WorkerSlotConfig:
    """Per-role slot configuration inside a pool."""

    role: WorkerRole
    max_active: int
    accepted_step_kinds: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    output_artifact_kinds: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkerSlot:
    """Runtime state of a single worker slot.

    Spec class-diagram fields: ``worker_id``, ``worker_role``,
    ``accepted_step_kinds``, ``max_concurrency``.  ``worker_id`` is exposed
    as :pyattr:`worker_id` (alias for ``slot_id``).
    """

    slot_id: str
    role: WorkerRole
    status: SlotStatus = SlotStatus.idle
    current_attempt_id: str | None = None
    started_at: float | None = None
    supervisor_id: str | None = None
    workspace: str | None = None
    module: str | None = None

    @property
    def worker_id(self) -> str:
        """Spec-aligned alias — ``worker_id`` is the slot's unique identity."""
        return self.slot_id


# -- default conflict domain limits -----------------------------------------

DEFAULT_CONFLICT_LIMITS: dict[str, int] = {
    "max_same_workspace": 1,
    "max_same_module": 2,
}


@dataclass
class WorkerPoolConfig:
    """Declarative configuration for a worker pool.

    ``max_global_active`` caps the total number of busy slots across all
    roles.  When set to 0 (default), the global cap equals the sum of
    per-role ``max_active`` values — i.e. no additional restriction.

    ``max_per_supervisor`` limits how many slots a single supervisor can
    hold concurrently.  0 means unlimited.

    ``conflict_limits`` keys follow the spec naming convention:
    ``max_same_workspace``, ``max_same_module``.
    """

    pool_id: str
    team_id: str
    slots: dict[WorkerRole, WorkerSlotConfig] = field(default_factory=dict)
    conflict_limits: dict[str, int] = field(default_factory=dict)
    max_global_active: int = 0
    max_per_supervisor: int = 0


@dataclass
class WorkerPoolStatus:
    """Snapshot of pool utilisation."""

    pool_id: str
    active_slots: int
    idle_slots: int
    interrupted_slots: int = 0
    by_role: dict[str, int] = field(default_factory=dict)
