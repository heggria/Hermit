"""Worker pool manager — claims and releases role-bound slots.

Enforces three layers of admission control:

1. **Per-role limits** — each :class:`WorkerRole` has a fixed number of slots.
2. **Global active cap** — optional total busy-slot ceiling across all roles.
3. **Per-supervisor limit** — optional cap on how many slots one supervisor may
   hold concurrently.
4. **Conflict-domain limits** — e.g. ``max_same_workspace`` prevents multiple
   workers from operating on the same workspace simultaneously.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import replace

import structlog

from hermit.kernel.execution.workers.models import (
    SlotStatus,
    WorkerPoolConfig,
    WorkerPoolStatus,
    WorkerRole,
    WorkerSlot,
)

log = structlog.get_logger()

__all__ = ["WorkerPoolManager"]


class WorkerPoolManager:
    """Manages a fixed set of worker slots partitioned by :class:`WorkerRole`.

    Thread-safe: all slot mutations are protected by a single lock so that
    the dispatch loop and reaper can operate concurrently.
    """

    def __init__(self, config: WorkerPoolConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        # role -> list of live WorkerSlot objects
        self._slots: dict[WorkerRole, list[WorkerSlot]] = {}
        self._slot_index: dict[str, WorkerSlot] = {}

        # Effective global cap: explicit config or sum of per-role max_active.
        total_role_slots = sum(sc.max_active for sc in config.slots.values())
        self._max_global_active = (
            config.max_global_active if config.max_global_active > 0 else total_role_slots
        )

        self._init_slots()

    # -- initialisation -------------------------------------------------------

    def _init_slots(self) -> None:
        for role, slot_cfg in self._config.slots.items():
            role_slots: list[WorkerSlot] = []
            for _ in range(slot_cfg.max_active):
                slot = WorkerSlot(
                    slot_id=f"{self._config.pool_id}-{role}-{uuid.uuid4().hex[:12]}",
                    role=role,
                    status=SlotStatus.idle,
                )
                role_slots.append(slot)
                self._slot_index[slot.slot_id] = slot
            self._slots[role] = role_slots

    # -- internal helpers (caller must hold self._lock) -----------------------

    def _count_busy(self) -> int:
        """Count total busy slots across all roles.  Caller must hold lock."""
        return sum(
            1 for slots in self._slots.values() for s in slots if s.status == SlotStatus.busy
        )

    def _count_supervisor(self, supervisor_id: str) -> int:
        """Count busy slots held by *supervisor_id*.  Caller must hold lock."""
        return sum(
            1
            for slots in self._slots.values()
            for s in slots
            if s.status == SlotStatus.busy and s.supervisor_id == supervisor_id
        )

    def _count_workspace(self, workspace: str) -> int:
        """Count busy slots in *workspace*.  Caller must hold lock."""
        return sum(
            1
            for slots in self._slots.values()
            for s in slots
            if s.status == SlotStatus.busy and s.workspace == workspace
        )

    def _count_module(self, module: str) -> int:
        """Count busy slots in *module*.  Caller must hold lock."""
        return sum(
            1
            for slots in self._slots.values()
            for s in slots
            if s.status == SlotStatus.busy and s.module == module
        )

    # -- public API -----------------------------------------------------------

    def claim_slot(
        self,
        role: WorkerRole,
        *,
        supervisor_id: str | None = None,
        workspace: str | None = None,
        module: str | None = None,
    ) -> WorkerSlot | None:
        """Claim an idle slot for *role*.

        Returns a :class:`WorkerSlot` snapshot with ``status=busy`` and a
        fresh ``started_at`` timestamp, or ``None`` when the claim is
        rejected due to:

        * no idle slot for the requested role,
        * global active cap reached,
        * per-supervisor limit reached, or
        * conflict-domain limit reached (workspace or module).
        """
        with self._lock:
            # --- global cap ---
            if self._count_busy() >= self._max_global_active:
                log.debug(
                    "worker_slot_global_cap",
                    pool_id=self._config.pool_id,
                    role=role,
                )
                return None

            # --- per-supervisor cap ---
            if (
                supervisor_id
                and self._config.max_per_supervisor > 0
                and self._count_supervisor(supervisor_id) >= self._config.max_per_supervisor
            ):
                log.debug(
                    "worker_slot_supervisor_cap",
                    pool_id=self._config.pool_id,
                    supervisor_id=supervisor_id,
                    role=role,
                )
                return None

            # --- conflict-domain: workspace ---
            max_same_ws = self._config.conflict_limits.get("max_same_workspace", 0)
            if workspace and max_same_ws > 0 and self._count_workspace(workspace) >= max_same_ws:
                log.debug(
                    "worker_slot_workspace_conflict",
                    pool_id=self._config.pool_id,
                    workspace=workspace,
                    role=role,
                )
                return None

            # --- conflict-domain: module ---
            max_same_mod = self._config.conflict_limits.get("max_same_module", 0)
            if module and max_same_mod > 0 and self._count_module(module) >= max_same_mod:
                log.debug(
                    "worker_slot_module_conflict",
                    pool_id=self._config.pool_id,
                    module=module,
                    role=role,
                )
                return None

            # --- per-role slot ---
            role_slots = self._slots.get(role, [])
            for idx, slot in enumerate(role_slots):
                if slot.status == SlotStatus.idle:
                    new_slot = replace(
                        slot,
                        status=SlotStatus.busy,
                        started_at=time.time(),
                        supervisor_id=supervisor_id,
                        workspace=workspace,
                        module=module,
                    )
                    role_slots[idx] = new_slot
                    self._slot_index[new_slot.slot_id] = new_slot
                    log.debug(
                        "worker_slot_claimed",
                        pool_id=self._config.pool_id,
                        slot_id=new_slot.slot_id,
                        role=role,
                    )
                    return replace(new_slot)
        return None

    def release_slot(self, slot_id: str) -> None:
        """Release a previously claimed slot back to idle."""
        with self._lock:
            slot = self._slot_index.get(slot_id)
            if slot is None:
                log.warning(
                    "worker_slot_release_unknown",
                    pool_id=self._config.pool_id,
                    slot_id=slot_id,
                )
                return
            new_slot = replace(
                slot,
                status=SlotStatus.idle,
                current_attempt_id=None,
                started_at=None,
                supervisor_id=None,
                workspace=None,
                module=None,
            )
            # Replace in role list
            role_slots = self._slots.get(slot.role, [])
            for idx, s in enumerate(role_slots):
                if s.slot_id == slot_id:
                    role_slots[idx] = new_slot
                    break
            self._slot_index[slot_id] = new_slot
            log.debug(
                "worker_slot_released",
                pool_id=self._config.pool_id,
                slot_id=slot_id,
                role=new_slot.role,
            )

    def can_accept(
        self,
        role: WorkerRole,
        *,
        supervisor_id: str | None = None,
        workspace: str | None = None,
        module: str | None = None,
    ) -> bool:
        """Return ``True`` if a slot can be claimed for *role*.

        Checks role availability, global cap, per-supervisor limit, and
        conflict-domain limits (workspace and module).
        """
        with self._lock:
            # Global cap
            if self._count_busy() >= self._max_global_active:
                return False
            # Per-supervisor
            if (
                supervisor_id
                and self._config.max_per_supervisor > 0
                and self._count_supervisor(supervisor_id) >= self._config.max_per_supervisor
            ):
                return False
            # Conflict: workspace
            max_same_ws = self._config.conflict_limits.get("max_same_workspace", 0)
            if workspace and max_same_ws > 0 and self._count_workspace(workspace) >= max_same_ws:
                return False
            # Conflict: module
            max_same_mod = self._config.conflict_limits.get("max_same_module", 0)
            if module and max_same_mod > 0 and self._count_module(module) >= max_same_mod:
                return False
            # Role slot
            return any(s.status == SlotStatus.idle for s in self._slots.get(role, []))

    def get_status(self) -> WorkerPoolStatus:
        """Return a point-in-time snapshot of pool utilisation."""
        with self._lock:
            active = 0
            idle = 0
            interrupted = 0
            by_role: dict[str, int] = {}
            for role, role_slots in self._slots.items():
                busy_count = sum(1 for s in role_slots if s.status == SlotStatus.busy)
                int_count = sum(1 for s in role_slots if s.status == SlotStatus.interrupted)
                active += busy_count
                interrupted += int_count
                idle += len(role_slots) - busy_count - int_count
                by_role[str(role)] = busy_count
            return WorkerPoolStatus(
                pool_id=self._config.pool_id,
                active_slots=active,
                idle_slots=idle,
                interrupted_slots=interrupted,
                by_role=by_role,
            )
