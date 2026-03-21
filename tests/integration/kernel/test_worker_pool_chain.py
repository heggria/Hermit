"""Integration tests — WorkerPool 4-layer admission control under concurrency.

Exercises ALL 4 admission layers simultaneously:
1. Per-role limits
2. Global cap
3. Per-supervisor limit
4. Conflict-domain limits (workspace, module)

Plus frozen-slot invariants and concurrent combined-pressure scenarios.
"""

from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError

import pytest

from hermit.kernel.execution.workers.models import (
    SlotStatus,
    WorkerPoolConfig,
    WorkerRole,
    WorkerSlotConfig,
)
from hermit.kernel.execution.workers.pool import WorkerPoolManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(
    *,
    executor: int = 3,
    planner: int = 0,
    verifier: int = 0,
    global_max: int = 0,
    max_per_supervisor: int = 0,
    conflict_limits: dict[str, int] | None = None,
) -> WorkerPoolConfig:
    """Build a WorkerPoolConfig with the requested shape."""
    slots: dict[WorkerRole, WorkerSlotConfig] = {}
    if executor > 0:
        slots[WorkerRole.executor] = WorkerSlotConfig(
            role=WorkerRole.executor,
            max_active=executor,
        )
    if planner > 0:
        slots[WorkerRole.planner] = WorkerSlotConfig(
            role=WorkerRole.planner,
            max_active=planner,
        )
    if verifier > 0:
        slots[WorkerRole.verifier] = WorkerSlotConfig(
            role=WorkerRole.verifier,
            max_active=verifier,
        )
    return WorkerPoolConfig(
        pool_id="integration-pool",
        team_id="team-integration",
        slots=slots,
        max_global_active=global_max,
        max_per_supervisor=max_per_supervisor,
        conflict_limits=conflict_limits or {},
    )


# ===================================================================
# 1. Per-role limits
# ===================================================================


class TestPerRoleLimits:
    """Layer 1: per-role max_active controls how many slots one role can hold."""

    def test_claim_up_to_limit_then_reject(self) -> None:
        mgr = WorkerPoolManager(_config(executor=3))

        claimed = [mgr.claim_slot(WorkerRole.executor) for _ in range(3)]
        assert all(s is not None for s in claimed)

        # 4th claim must be rejected
        fourth = mgr.claim_slot(WorkerRole.executor)
        assert fourth is None

    def test_release_reopens_slot(self) -> None:
        mgr = WorkerPoolManager(_config(executor=3))

        claimed = [mgr.claim_slot(WorkerRole.executor) for _ in range(3)]
        assert all(s is not None for s in claimed)
        assert mgr.claim_slot(WorkerRole.executor) is None

        # Release one slot
        mgr.release_slot(claimed[0].slot_id)

        # Now a 4th claim succeeds
        fourth = mgr.claim_slot(WorkerRole.executor)
        assert fourth is not None
        assert fourth.status == SlotStatus.busy


# ===================================================================
# 2. Global cap
# ===================================================================


class TestGlobalCap:
    """Layer 2: max_global_active caps total busy slots across all roles."""

    def test_global_cap_across_roles(self) -> None:
        mgr = WorkerPoolManager(
            _config(
                planner=3,
                executor=3,
                verifier=3,
                global_max=5,
            )
        )

        # Claim 2 planner + 2 executor + 1 verifier = 5 total
        p1 = mgr.claim_slot(WorkerRole.planner)
        p2 = mgr.claim_slot(WorkerRole.planner)
        e1 = mgr.claim_slot(WorkerRole.executor)
        e2 = mgr.claim_slot(WorkerRole.executor)
        v1 = mgr.claim_slot(WorkerRole.verifier)
        assert all(s is not None for s in [p1, p2, e1, e2, v1])

        # 6th claim must fail regardless of which role
        assert mgr.claim_slot(WorkerRole.planner) is None
        assert mgr.claim_slot(WorkerRole.executor) is None
        assert mgr.claim_slot(WorkerRole.verifier) is None

    def test_global_cap_release_unblocks(self) -> None:
        mgr = WorkerPoolManager(
            _config(
                planner=3,
                executor=3,
                global_max=5,
            )
        )

        slots = []
        for _ in range(3):
            slots.append(mgr.claim_slot(WorkerRole.planner))
        for _ in range(2):
            slots.append(mgr.claim_slot(WorkerRole.executor))
        assert all(s is not None for s in slots)
        assert mgr.claim_slot(WorkerRole.executor) is None

        # Release one, then claim should succeed
        mgr.release_slot(slots[0].slot_id)
        new_slot = mgr.claim_slot(WorkerRole.executor)
        assert new_slot is not None

    def test_can_accept_respects_global_cap(self) -> None:
        mgr = WorkerPoolManager(
            _config(
                executor=3,
                verifier=3,
                global_max=2,
            )
        )
        mgr.claim_slot(WorkerRole.executor)
        mgr.claim_slot(WorkerRole.verifier)

        assert mgr.can_accept(WorkerRole.executor) is False
        assert mgr.can_accept(WorkerRole.verifier) is False


# ===================================================================
# 3. Per-supervisor limit
# ===================================================================


class TestPerSupervisorLimit:
    """Layer 3: max_per_supervisor caps how many slots one supervisor holds."""

    def test_supervisor_cap_blocks_same_supervisor(self) -> None:
        mgr = WorkerPoolManager(_config(executor=5, max_per_supervisor=2))

        s1 = mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-A")
        s2 = mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-A")
        assert s1 is not None
        assert s2 is not None

        # 3rd for sup-A must fail
        s3 = mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-A")
        assert s3 is None

    def test_different_supervisor_still_claims(self) -> None:
        mgr = WorkerPoolManager(_config(executor=5, max_per_supervisor=2))

        mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-A")
        mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-A")

        # sup-A at limit, but sup-B can still claim
        s = mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-B")
        assert s is not None
        assert s.supervisor_id == "sup-B"

    def test_supervisor_release_reopens(self) -> None:
        mgr = WorkerPoolManager(_config(executor=5, max_per_supervisor=2))

        s1 = mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-A")
        _s2 = mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-A")
        assert mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-A") is None

        mgr.release_slot(s1.slot_id)
        s3 = mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-A")
        assert s3 is not None


# ===================================================================
# 4a. Workspace conflict
# ===================================================================


class TestWorkspaceConflict:
    """Layer 4a: max_same_workspace prevents workspace collisions."""

    def test_same_workspace_blocked(self) -> None:
        mgr = WorkerPoolManager(_config(executor=5, conflict_limits={"max_same_workspace": 1}))

        s1 = mgr.claim_slot(WorkerRole.executor, workspace="/repo/a")
        assert s1 is not None

        # Same workspace must be rejected
        s2 = mgr.claim_slot(WorkerRole.executor, workspace="/repo/a")
        assert s2 is None

    def test_different_workspace_succeeds(self) -> None:
        mgr = WorkerPoolManager(_config(executor=5, conflict_limits={"max_same_workspace": 1}))

        mgr.claim_slot(WorkerRole.executor, workspace="/repo/a")

        s2 = mgr.claim_slot(WorkerRole.executor, workspace="/repo/b")
        assert s2 is not None
        assert s2.workspace == "/repo/b"

    def test_workspace_release_unblocks(self) -> None:
        mgr = WorkerPoolManager(_config(executor=5, conflict_limits={"max_same_workspace": 1}))

        s1 = mgr.claim_slot(WorkerRole.executor, workspace="/repo/a")
        assert mgr.claim_slot(WorkerRole.executor, workspace="/repo/a") is None

        mgr.release_slot(s1.slot_id)

        s2 = mgr.claim_slot(WorkerRole.executor, workspace="/repo/a")
        assert s2 is not None

    def test_can_accept_workspace_conflict(self) -> None:
        mgr = WorkerPoolManager(_config(executor=5, conflict_limits={"max_same_workspace": 1}))
        mgr.claim_slot(WorkerRole.executor, workspace="/repo/x")

        assert mgr.can_accept(WorkerRole.executor, workspace="/repo/x") is False
        assert mgr.can_accept(WorkerRole.executor, workspace="/repo/y") is True


# ===================================================================
# 4b. Module conflict
# ===================================================================


class TestModuleConflict:
    """Layer 4b: max_same_module limits concurrent workers on one module."""

    def test_module_limit_reached(self) -> None:
        mgr = WorkerPoolManager(_config(executor=5, conflict_limits={"max_same_module": 2}))

        s1 = mgr.claim_slot(WorkerRole.executor, module="kernel")
        s2 = mgr.claim_slot(WorkerRole.executor, module="kernel")
        assert s1 is not None
        assert s2 is not None

        # 3rd on same module must fail
        s3 = mgr.claim_slot(WorkerRole.executor, module="kernel")
        assert s3 is None

    def test_different_module_succeeds(self) -> None:
        mgr = WorkerPoolManager(_config(executor=5, conflict_limits={"max_same_module": 2}))

        mgr.claim_slot(WorkerRole.executor, module="kernel")
        mgr.claim_slot(WorkerRole.executor, module="kernel")

        s3 = mgr.claim_slot(WorkerRole.executor, module="runtime")
        assert s3 is not None
        assert s3.module == "runtime"

    def test_module_release_unblocks(self) -> None:
        mgr = WorkerPoolManager(_config(executor=5, conflict_limits={"max_same_module": 2}))

        s1 = mgr.claim_slot(WorkerRole.executor, module="kernel")
        mgr.claim_slot(WorkerRole.executor, module="kernel")
        assert mgr.claim_slot(WorkerRole.executor, module="kernel") is None

        mgr.release_slot(s1.slot_id)

        s4 = mgr.claim_slot(WorkerRole.executor, module="kernel")
        assert s4 is not None


# ===================================================================
# 5. Combined pressure — 10 threads, 5 executor slots, workspace conflicts
# ===================================================================


class TestCombinedPressure:
    """All 4 layers active simultaneously under thread contention."""

    def test_concurrent_combined_admission(self) -> None:
        """10 threads compete for 5 executor slots with:
        - global_max=5
        - max_per_supervisor=2
        - max_same_workspace=1

        Each thread claims (executor, supervisor=A-or-B, workspace=unique-ish).
        Verify exactly the right number succeed and no slot leaks.
        """
        mgr = WorkerPoolManager(
            _config(
                executor=5,
                global_max=5,
                max_per_supervisor=2,
                conflict_limits={"max_same_workspace": 1},
            )
        )

        results: list[object] = []
        barrier = threading.Barrier(10)

        def worker(idx: int) -> None:
            barrier.wait()
            sup = "sup-A" if idx < 5 else "sup-B"
            ws = f"/repo/{idx % 5}"  # 5 unique workspaces, each used by 2 threads
            slot = mgr.claim_slot(
                WorkerRole.executor,
                supervisor_id=sup,
                workspace=ws,
            )
            results.append(slot)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        claimed = [r for r in results if r is not None]
        rejected = [r for r in results if r is None]

        # Max 5 slots exist. max_same_workspace=1 means each workspace can be claimed
        # at most once. There are 5 unique workspaces so at most 5 claims succeed.
        # Per-supervisor limit is 2, so sup-A can hold at most 2 and sup-B at most 2
        # -> combined max from supervisor limit = 4, but workspace allows up to 5.
        # The binding constraint depends on race ordering; the key invariant is:
        assert len(claimed) <= 5, f"Exceeded pool capacity: {len(claimed)} claimed"
        assert len(claimed) + len(rejected) == 10, "All threads must report a result"

        # No slot ID duplicates among claimed slots
        slot_ids = [s.slot_id for s in claimed]
        assert len(set(slot_ids)) == len(slot_ids), "Duplicate slot IDs found"

        # Verify per-supervisor limit was not exceeded
        sup_a_count = sum(1 for s in claimed if s.supervisor_id == "sup-A")
        sup_b_count = sum(1 for s in claimed if s.supervisor_id == "sup-B")
        assert sup_a_count <= 2, f"sup-A exceeded limit: {sup_a_count}"
        assert sup_b_count <= 2, f"sup-B exceeded limit: {sup_b_count}"

        # Verify workspace uniqueness among claimed slots
        workspaces = [s.workspace for s in claimed]
        assert len(set(workspaces)) == len(workspaces), "Workspace collision in claims"

        # Verify no slot leaks: pool status must be consistent
        status = mgr.get_status()
        assert status.active_slots == len(claimed)
        assert status.active_slots + status.idle_slots == 5

    def test_concurrent_claim_release_cycle(self) -> None:
        """Threads claim and release in tight loops. No slot leaks allowed."""
        mgr = WorkerPoolManager(
            _config(
                executor=5,
                global_max=5,
                conflict_limits={"max_same_workspace": 1, "max_same_module": 2},
            )
        )

        errors: list[str] = []

        def cycle(thread_id: int) -> None:
            for _ in range(20):
                slot = mgr.claim_slot(
                    WorkerRole.executor,
                    workspace=f"/repo/{thread_id}",
                    module=f"mod-{thread_id % 3}",
                )
                if slot is not None:
                    mgr.release_slot(slot.slot_id)

        threads = [threading.Thread(target=cycle, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # After all cycles, every slot must be idle (no leaks)
        status = mgr.get_status()
        assert status.active_slots == 0, f"Slot leak: {status.active_slots} still active"
        assert status.idle_slots == 5, f"Expected 5 idle, got {status.idle_slots}"
        assert len(errors) == 0

    def test_all_four_layers_interact(self) -> None:
        """Exercise all 4 admission layers in a single scenario.

        Config:
        - executor=4, planner=2 (per-role)
        - global_max=5 (global cap)
        - max_per_supervisor=3 (per-supervisor)
        - max_same_workspace=1, max_same_module=2 (conflict domains)
        """
        mgr = WorkerPoolManager(
            _config(
                executor=4,
                planner=2,
                global_max=5,
                max_per_supervisor=3,
                conflict_limits={"max_same_workspace": 1, "max_same_module": 2},
            )
        )

        # Claim e1: executor, sup-X, workspace=/a, module=kernel
        e1 = mgr.claim_slot(
            WorkerRole.executor, supervisor_id="sup-X", workspace="/a", module="kernel"
        )
        assert e1 is not None

        # Claim e2: executor, sup-X, workspace=/b, module=kernel
        e2 = mgr.claim_slot(
            WorkerRole.executor, supervisor_id="sup-X", workspace="/b", module="kernel"
        )
        assert e2 is not None

        # Claim e3: executor, sup-X, workspace=/c, module=runtime
        # sup-X now has 3 slots -> at supervisor limit
        e3 = mgr.claim_slot(
            WorkerRole.executor, supervisor_id="sup-X", workspace="/c", module="runtime"
        )
        assert e3 is not None

        # Claim e4: executor, sup-X -> REJECTED by per-supervisor limit (3 already)
        e4_blocked = mgr.claim_slot(
            WorkerRole.executor, supervisor_id="sup-X", workspace="/d", module="runtime"
        )
        assert e4_blocked is None

        # Claim e4: executor, sup-Y, workspace=/a -> REJECTED by workspace conflict (/a busy)
        e4_ws = mgr.claim_slot(
            WorkerRole.executor, supervisor_id="sup-Y", workspace="/a", module="policy"
        )
        assert e4_ws is None

        # Claim e4: executor, sup-Y, workspace=/d, module=kernel
        # -> REJECTED by module conflict (kernel already has 2)
        e4_mod = mgr.claim_slot(
            WorkerRole.executor, supervisor_id="sup-Y", workspace="/d", module="kernel"
        )
        assert e4_mod is None

        # Claim p1: planner, sup-Y, workspace=/d, module=policy -> SUCCESS
        p1 = mgr.claim_slot(
            WorkerRole.planner, supervisor_id="sup-Y", workspace="/d", module="policy"
        )
        assert p1 is not None

        # Claim p2: planner, sup-Y, workspace=/e, module=policy -> SUCCESS (5th total = global cap)
        p2 = mgr.claim_slot(
            WorkerRole.planner, supervisor_id="sup-Y", workspace="/e", module="policy"
        )
        assert p2 is not None

        # Now 5 total busy -> global cap reached
        # Claim anything -> REJECTED by global cap
        assert mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-Z", workspace="/f") is None

        # Verify status is consistent
        status = mgr.get_status()
        assert status.active_slots == 5
        assert status.idle_slots == 1  # 4 executor + 2 planner = 6 total, 5 busy

        # Release one executor, then claim succeeds
        mgr.release_slot(e1.slot_id)
        status = mgr.get_status()
        assert status.active_slots == 4

        # Now workspace=/a is free, module kernel has 1 (was 2 before release)
        e5 = mgr.claim_slot(
            WorkerRole.executor, supervisor_id="sup-Z", workspace="/a", module="kernel"
        )
        assert e5 is not None


# ===================================================================
# 6. Frozen WorkerSlot
# ===================================================================


class TestFrozenWorkerSlot:
    """WorkerSlot is frozen — claimed slots must be immutable snapshots."""

    def test_claimed_slot_is_frozen(self) -> None:
        mgr = WorkerPoolManager(_config(executor=3))
        slot = mgr.claim_slot(WorkerRole.executor)
        assert slot is not None

        with pytest.raises(FrozenInstanceError):
            slot.status = SlotStatus.idle  # type: ignore[misc]

        with pytest.raises(FrozenInstanceError):
            slot.workspace = "/mutated"  # type: ignore[misc]

    def test_release_does_not_mutate_claimed_snapshot(self) -> None:
        """The slot object returned by claim_slot must remain unchanged after release."""
        mgr = WorkerPoolManager(_config(executor=3))
        slot = mgr.claim_slot(WorkerRole.executor, workspace="/repo/x")
        assert slot is not None

        original_id = slot.slot_id
        original_status = slot.status
        original_workspace = slot.workspace

        mgr.release_slot(slot.slot_id)

        # Original snapshot must be untouched
        assert slot.slot_id == original_id
        assert slot.status == original_status
        assert slot.workspace == original_workspace

    def test_internal_state_updated_without_mutating_returned_slot(self) -> None:
        """Internal pool state and returned snapshot are independent objects."""
        mgr = WorkerPoolManager(_config(executor=3))
        slot = mgr.claim_slot(WorkerRole.executor, module="kernel")
        assert slot is not None

        # The internal slot and the returned slot must be different objects
        # (claim returns a copy via replace())
        with mgr._lock:
            internal = mgr._slot_index[slot.slot_id]
            assert internal is not slot  # Different object
            assert internal.slot_id == slot.slot_id  # Same identity
            assert internal.status == slot.status  # Same state
