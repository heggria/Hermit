"""Worker pool manager — claims and releases role-bound slots.

Enforces three layers of admission control:

1. **Per-role limits** — each :class:`WorkerRole` has a fixed number of slots.
2. **Global active cap** — optional total busy-slot ceiling across all roles.
3. **Per-supervisor limit** — optional cap on how many slots one supervisor may
   hold concurrently.
4. **Conflict-domain limits** — e.g. ``max_same_workspace`` prevents multiple
   workers from operating on the same workspace simultaneously.

All counting is O(1) via maintained counters.  Slot objects are created
on demand (lazy allocation) and removed on release, keeping memory
proportional to active work rather than total capacity.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict
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
    """Manages role-bound worker slots with O(1) admission control.

    Thread-safe: all slot mutations are protected by a single lock so that
    the dispatch loop and reaper can operate concurrently.  O(1) counter
    operations keep the critical section short even at high slot counts.
    """

    def __init__(self, config: WorkerPoolConfig) -> None:
        self._config = config
        self._lock = threading.Lock()

        # Slot index: slot_id -> live WorkerSlot (only busy/interrupted slots)
        self._slot_index: dict[str, WorkerSlot] = {}

        # Per-role lists of live slot objects (only busy/interrupted)
        self._slots: dict[WorkerRole, list[WorkerSlot]] = {role: [] for role in config.slots}

        # --- O(1) counters ---
        self._busy_count: int = 0
        self._supervisor_counts: dict[str, int] = defaultdict(int)
        self._workspace_counts: dict[str, int] = defaultdict(int)
        self._module_counts: dict[str, int] = defaultdict(int)
        self._role_busy_counts: dict[WorkerRole, int] = defaultdict(int)

        # Effective global cap: explicit config or sum of per-role max_active.
        total_role_slots = sum(sc.max_active for sc in config.slots.values())
        self._max_global_active = (
            config.max_global_active if config.max_global_active > 0 else total_role_slots
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
        """Claim a slot for *role*.

        Returns a :class:`WorkerSlot` snapshot with ``status=busy`` and a
        fresh ``started_at`` timestamp, or ``None`` when the claim is
        rejected due to:

        * per-role capacity reached,
        * global active cap reached,
        * per-supervisor limit reached, or
        * conflict-domain limit reached (workspace or module).

        Slots are created on demand — no pre-allocation.
        """
        with self._lock:
            # --- role config check ---
            slot_cfg = self._config.slots.get(role)
            if slot_cfg is None:
                return None

            # --- per-role cap ---
            if self._role_busy_counts[role] >= slot_cfg.max_active:
                log.debug(
                    "worker_slot_role_cap",
                    pool_id=self._config.pool_id,
                    role=role,
                )
                return None

            # --- global cap ---
            if self._busy_count >= self._max_global_active:
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
                and self._supervisor_counts[supervisor_id] >= self._config.max_per_supervisor
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
            if workspace and max_same_ws > 0 and self._workspace_counts[workspace] >= max_same_ws:
                log.debug(
                    "worker_slot_workspace_conflict",
                    pool_id=self._config.pool_id,
                    workspace=workspace,
                    role=role,
                )
                return None

            # --- conflict-domain: module ---
            max_same_mod = self._config.conflict_limits.get("max_same_module", 0)
            if module and max_same_mod > 0 and self._module_counts[module] >= max_same_mod:
                log.debug(
                    "worker_slot_module_conflict",
                    pool_id=self._config.pool_id,
                    module=module,
                    role=role,
                )
                return None

            # --- create slot on demand ---
            new_slot = WorkerSlot(
                slot_id=f"{self._config.pool_id}-{role}-{uuid.uuid4().hex[:12]}",
                role=role,
                status=SlotStatus.busy,
                started_at=time.time(),
                supervisor_id=supervisor_id,
                workspace=workspace,
                module=module,
            )

            # Register in indices
            if role not in self._slots:
                self._slots[role] = []
            self._slots[role].append(new_slot)
            self._slot_index[new_slot.slot_id] = new_slot

            # Increment counters
            self._busy_count += 1
            self._role_busy_counts[role] += 1
            if supervisor_id:
                self._supervisor_counts[supervisor_id] += 1
            if workspace:
                self._workspace_counts[workspace] += 1
            if module:
                self._module_counts[module] += 1

            log.debug(
                "worker_slot_claimed",
                pool_id=self._config.pool_id,
                slot_id=new_slot.slot_id,
                role=role,
            )
            return replace(new_slot)

    def release_slot(self, slot_id: str) -> None:
        """Release a previously claimed slot, removing it from the pool."""
        with self._lock:
            slot = self._slot_index.get(slot_id)
            if slot is None:
                log.warning(
                    "worker_slot_release_unknown",
                    pool_id=self._config.pool_id,
                    slot_id=slot_id,
                )
                return

            # Decrement counters (only for busy slots)
            if slot.status == SlotStatus.busy:
                self._busy_count -= 1
                self._role_busy_counts[slot.role] -= 1
                if slot.supervisor_id:
                    self._supervisor_counts[slot.supervisor_id] -= 1
                    if self._supervisor_counts[slot.supervisor_id] <= 0:
                        del self._supervisor_counts[slot.supervisor_id]
                if slot.workspace:
                    self._workspace_counts[slot.workspace] -= 1
                    if self._workspace_counts[slot.workspace] <= 0:
                        del self._workspace_counts[slot.workspace]
                if slot.module:
                    self._module_counts[slot.module] -= 1
                    if self._module_counts[slot.module] <= 0:
                        del self._module_counts[slot.module]

            # Remove from role list
            role_slots = self._slots.get(slot.role, [])
            self._slots[slot.role] = [s for s in role_slots if s.slot_id != slot_id]

            # Remove from index
            del self._slot_index[slot_id]

            log.debug(
                "worker_slot_released",
                pool_id=self._config.pool_id,
                slot_id=slot_id,
                role=slot.role,
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
        conflict-domain limits (workspace and module).  All checks are O(1).
        """
        with self._lock:
            # Role config
            slot_cfg = self._config.slots.get(role)
            if slot_cfg is None:
                return False
            # Per-role cap
            if self._role_busy_counts[role] >= slot_cfg.max_active:
                return False
            # Global cap
            if self._busy_count >= self._max_global_active:
                return False
            # Per-supervisor
            if (
                supervisor_id
                and self._config.max_per_supervisor > 0
                and self._supervisor_counts[supervisor_id] >= self._config.max_per_supervisor
            ):
                return False
            # Conflict: workspace
            max_same_ws = self._config.conflict_limits.get("max_same_workspace", 0)
            if workspace and max_same_ws > 0 and self._workspace_counts[workspace] >= max_same_ws:
                return False
            # Conflict: module
            max_same_mod = self._config.conflict_limits.get("max_same_module", 0)
            return not (module and max_same_mod > 0 and self._module_counts[module] >= max_same_mod)

    def get_status(self) -> WorkerPoolStatus:
        """Return a point-in-time snapshot of pool utilisation.

        ``idle_slots`` represents available capacity (total configured
        max_active minus active and interrupted slots).
        """
        with self._lock:
            active = self._busy_count
            interrupted = 0
            by_role: dict[str, int] = {}

            # Count interrupted slots and build per-role active counts
            for role in self._config.slots:
                role_active = self._role_busy_counts[role]
                role_interrupted = sum(
                    1 for s in self._slots.get(role, []) if s.status == SlotStatus.interrupted
                )
                interrupted += role_interrupted
                by_role[str(role)] = role_active

            total_capacity = sum(sc.max_active for sc in self._config.slots.values())
            idle = total_capacity - active - interrupted

            return WorkerPoolStatus(
                pool_id=self._config.pool_id,
                active_slots=active,
                idle_slots=idle,
                interrupted_slots=interrupted,
                by_role=by_role,
            )
