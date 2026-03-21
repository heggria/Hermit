"""End-to-end tests for WorkerPool 4-layer admission control.

Exercises each admission layer with real WorkerPoolManager + WorkerPoolConfig
to verify claim/release semantics, saturation behaviour, and status reporting.

Layers tested:
1. Per-role slot limits  (Test 21)
2. Workspace conflict    (Test 22)
3. Module conflict       (Test 23)
4. Global active cap     (Test 24)
"""

from __future__ import annotations

from hermit.kernel.execution.workers.models import (
    SlotStatus,
    WorkerPoolConfig,
    WorkerRole,
    WorkerSlotConfig,
)
from hermit.kernel.execution.workers.pool import WorkerPoolManager

# ---------------------------------------------------------------------------
# Test 21: Per-role slot saturation and release
# ---------------------------------------------------------------------------


def test_per_role_slot_saturation_and_release() -> None:
    """Layer 1 — per-role max_active controls admission.

    Config: executor max_active=2.
    - Claim slot 1, 2 → succeed.
    - Claim slot 3 → None (saturated).
    - Release slot 1 → claim slot 3 again → succeed.
    - Pool status shows 2 active executor slots.
    """
    config = WorkerPoolConfig(
        pool_id="e2e-role-pool",
        team_id="team-e2e",
        slots={
            WorkerRole.executor: WorkerSlotConfig(
                role=WorkerRole.executor,
                max_active=2,
            ),
        },
    )
    mgr = WorkerPoolManager(config)

    # Claim slot 1 → succeeds
    slot1 = mgr.claim_slot(WorkerRole.executor)
    assert slot1 is not None
    assert slot1.status == SlotStatus.busy

    # Claim slot 2 → succeeds
    slot2 = mgr.claim_slot(WorkerRole.executor)
    assert slot2 is not None
    assert slot2.status == SlotStatus.busy

    # Claim slot 3 → None (saturated: max_active=2 reached)
    slot3_blocked = mgr.claim_slot(WorkerRole.executor)
    assert slot3_blocked is None

    # Release slot 1
    mgr.release_slot(slot1.slot_id)

    # Claim slot 3 again → succeeds (one slot freed)
    slot3 = mgr.claim_slot(WorkerRole.executor)
    assert slot3 is not None
    assert slot3.status == SlotStatus.busy

    # Verify: pool status shows 2 active executor slots
    status = mgr.get_status()
    assert status.active_slots == 2
    assert status.by_role["executor"] == 2
    assert status.idle_slots == 0


# ---------------------------------------------------------------------------
# Test 22: Workspace conflict — max_same_workspace=1
# ---------------------------------------------------------------------------


def test_workspace_conflict_max_same_workspace_1() -> None:
    """Layer 4a — workspace conflict domain enforcement.

    Config: conflict_limits={"max_same_workspace": 1, "max_same_module": 2},
            executor max_active=4.
    - Claim slot for workspace="ws-A" → succeeds.
    - Claim another slot for workspace="ws-A" → None (conflict).
    - Claim slot for workspace="ws-B" → succeeds.
    - 2 active slots, different workspaces.
    """
    config = WorkerPoolConfig(
        pool_id="e2e-ws-pool",
        team_id="team-e2e",
        slots={
            WorkerRole.executor: WorkerSlotConfig(
                role=WorkerRole.executor,
                max_active=4,
            ),
        },
        conflict_limits={"max_same_workspace": 1, "max_same_module": 2},
    )
    mgr = WorkerPoolManager(config)

    # Claim slot for workspace="ws-A" → succeeds
    slot_a1 = mgr.claim_slot(WorkerRole.executor, workspace="ws-A")
    assert slot_a1 is not None
    assert slot_a1.workspace == "ws-A"

    # Claim another slot for workspace="ws-A" → None (conflict)
    slot_a2 = mgr.claim_slot(WorkerRole.executor, workspace="ws-A")
    assert slot_a2 is None

    # Claim slot for workspace="ws-B" → succeeds
    slot_b = mgr.claim_slot(WorkerRole.executor, workspace="ws-B")
    assert slot_b is not None
    assert slot_b.workspace == "ws-B"

    # Verify: 2 active slots, different workspaces
    status = mgr.get_status()
    assert status.active_slots == 2
    assert status.idle_slots == 2  # 4 total - 2 active


# ---------------------------------------------------------------------------
# Test 23: Module conflict — max_same_module=2
# ---------------------------------------------------------------------------


def test_module_conflict_max_same_module_2() -> None:
    """Layer 4b — module conflict domain enforcement.

    Config: conflict_limits={"max_same_module": 2}, executor max_active=5.
    - Claim slot 1 for module="kernel.policy" → succeeds.
    - Claim slot 2 for module="kernel.policy" → succeeds.
    - Claim slot 3 for module="kernel.policy" → None (max 2 reached).
    - Claim slot 3 for module="kernel.execution" → succeeds.
    - 3 active: 2 on kernel.policy, 1 on kernel.execution.
    """
    config = WorkerPoolConfig(
        pool_id="e2e-mod-pool",
        team_id="team-e2e",
        slots={
            WorkerRole.executor: WorkerSlotConfig(
                role=WorkerRole.executor,
                max_active=5,
            ),
        },
        conflict_limits={"max_same_module": 2},
    )
    mgr = WorkerPoolManager(config)

    # Claim slot 1 for module="kernel.policy" → succeeds
    slot1 = mgr.claim_slot(WorkerRole.executor, module="kernel.policy")
    assert slot1 is not None
    assert slot1.module == "kernel.policy"

    # Claim slot 2 for module="kernel.policy" → succeeds
    slot2 = mgr.claim_slot(WorkerRole.executor, module="kernel.policy")
    assert slot2 is not None
    assert slot2.module == "kernel.policy"

    # Claim slot 3 for module="kernel.policy" → None (max 2 reached)
    slot3_blocked = mgr.claim_slot(WorkerRole.executor, module="kernel.policy")
    assert slot3_blocked is None

    # Claim slot 3 for module="kernel.execution" → succeeds (different module)
    slot3 = mgr.claim_slot(WorkerRole.executor, module="kernel.execution")
    assert slot3 is not None
    assert slot3.module == "kernel.execution"

    # Verify: 3 active — 2 on kernel.policy, 1 on kernel.execution
    status = mgr.get_status()
    assert status.active_slots == 3
    assert status.idle_slots == 2  # 5 total - 3 active
    assert status.by_role["executor"] == 3


# ---------------------------------------------------------------------------
# Test 24: Global active cap enforcement
# ---------------------------------------------------------------------------


def test_global_active_cap_enforcement() -> None:
    """Layer 2 — max_global_active caps total busy slots across all roles.

    Config: max_global_active=3, executor max_active=5, planner max_active=5.
    - Claim 3 slots (mixed roles) → all succeed.
    - Claim 4th slot → None (global cap).
    - Release 1 slot → claim succeeds.
    - Global cap enforced regardless of per-role availability.
    """
    config = WorkerPoolConfig(
        pool_id="e2e-global-pool",
        team_id="team-e2e",
        slots={
            WorkerRole.executor: WorkerSlotConfig(
                role=WorkerRole.executor,
                max_active=5,
            ),
            WorkerRole.planner: WorkerSlotConfig(
                role=WorkerRole.planner,
                max_active=5,
            ),
        },
        max_global_active=3,
    )
    mgr = WorkerPoolManager(config)

    # Claim 3 slots (mixed roles) → all succeed
    slot1 = mgr.claim_slot(WorkerRole.executor)
    slot2 = mgr.claim_slot(WorkerRole.planner)
    slot3 = mgr.claim_slot(WorkerRole.executor)
    assert slot1 is not None
    assert slot2 is not None
    assert slot3 is not None

    # Claim 4th slot → None (global cap of 3 reached)
    slot4_blocked = mgr.claim_slot(WorkerRole.executor)
    assert slot4_blocked is None

    # Also blocked for the other role — global cap is role-agnostic
    slot4_planner_blocked = mgr.claim_slot(WorkerRole.planner)
    assert slot4_planner_blocked is None

    # can_accept should also reflect global cap
    assert mgr.can_accept(WorkerRole.executor) is False
    assert mgr.can_accept(WorkerRole.planner) is False

    # Release 1 slot → claim succeeds
    mgr.release_slot(slot2.slot_id)

    slot4 = mgr.claim_slot(WorkerRole.planner)
    assert slot4 is not None

    # Verify: global cap enforced — 3 active again
    status = mgr.get_status()
    assert status.active_slots == 3
    # 10 total slots (5 executor + 5 planner), 3 busy, 7 idle
    assert status.idle_slots == 7
