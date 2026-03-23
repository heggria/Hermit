"""Tests for the Worker Pool / Slot abstraction."""

from __future__ import annotations

import threading
from dataclasses import replace

from hermit.kernel.execution.workers.models import (
    DEFAULT_CONFLICT_LIMITS,
    SlotStatus,
    WorkerPoolConfig,
    WorkerPoolStatus,
    WorkerRole,
    WorkerSlotConfig,
)
from hermit.kernel.execution.workers.pool import WorkerPoolManager


def _make_config(
    *,
    pool_id: str = "pool-1",
    team_id: str = "team-1",
    executor_max: int = 2,
    verifier_max: int = 1,
    max_global_active: int = 0,
    max_per_supervisor: int = 0,
    conflict_limits: dict[str, int] | None = None,
) -> WorkerPoolConfig:
    return WorkerPoolConfig(
        pool_id=pool_id,
        team_id=team_id,
        slots={
            WorkerRole.executor: WorkerSlotConfig(
                role=WorkerRole.executor,
                max_active=executor_max,
                accepted_step_kinds=["execute", "tool_call"],
                output_artifact_kinds=["diff"],
            ),
            WorkerRole.verifier: WorkerSlotConfig(
                role=WorkerRole.verifier,
                max_active=verifier_max,
                accepted_step_kinds=["verify"],
                required_capabilities=["read_file"],
                output_artifact_kinds=["verdict"],
            ),
        },
        max_global_active=max_global_active,
        max_per_supervisor=max_per_supervisor,
        conflict_limits=conflict_limits or {},
    )


# -- slot claiming -----------------------------------------------------------


def test_claim_slot_returns_busy_slot() -> None:
    mgr = WorkerPoolManager(_make_config())
    slot = mgr.claim_slot(WorkerRole.executor)

    assert slot is not None
    assert slot.status == SlotStatus.busy
    assert slot.started_at is not None
    assert slot.role == WorkerRole.executor


def test_claim_slot_returns_none_when_exhausted() -> None:
    mgr = WorkerPoolManager(_make_config(verifier_max=1))

    first = mgr.claim_slot(WorkerRole.verifier)
    second = mgr.claim_slot(WorkerRole.verifier)

    assert first is not None
    assert second is None


def test_claim_slot_returns_none_for_unconfigured_role() -> None:
    mgr = WorkerPoolManager(_make_config())
    slot = mgr.claim_slot(WorkerRole.planner)

    assert slot is None


def test_claim_multiple_slots_independent() -> None:
    mgr = WorkerPoolManager(_make_config(executor_max=3))

    slots = [mgr.claim_slot(WorkerRole.executor) for _ in range(3)]
    assert all(s is not None for s in slots)
    # All slot ids are unique
    ids = [s.slot_id for s in slots if s is not None]
    assert len(set(ids)) == 3

    # Fourth claim should fail
    assert mgr.claim_slot(WorkerRole.executor) is None


# -- worker_id property ------------------------------------------------------


def test_worker_id_property_aliases_slot_id() -> None:
    """Spec: worker_id is the slot's unique identity."""
    mgr = WorkerPoolManager(_make_config())
    slot = mgr.claim_slot(WorkerRole.executor)

    assert slot is not None
    assert slot.worker_id == slot.slot_id


# -- output_artifact_kinds ---------------------------------------------------


def test_slot_config_output_artifact_kinds() -> None:
    """Spec: workers should declare output_artifact_kinds."""
    cfg = _make_config()
    exec_cfg = cfg.slots[WorkerRole.executor]
    assert exec_cfg.output_artifact_kinds == ["diff"]

    ver_cfg = cfg.slots[WorkerRole.verifier]
    assert ver_cfg.output_artifact_kinds == ["verdict"]


# -- capacity limits ---------------------------------------------------------


def test_can_accept_true_when_idle() -> None:
    mgr = WorkerPoolManager(_make_config(executor_max=2))
    assert mgr.can_accept(WorkerRole.executor) is True


def test_can_accept_false_when_full() -> None:
    mgr = WorkerPoolManager(_make_config(executor_max=1))
    mgr.claim_slot(WorkerRole.executor)
    assert mgr.can_accept(WorkerRole.executor) is False


def test_can_accept_false_for_unconfigured_role() -> None:
    mgr = WorkerPoolManager(_make_config())
    assert mgr.can_accept(WorkerRole.benchmarker) is False


# -- global active cap -------------------------------------------------------


def test_global_active_cap_blocks_claim() -> None:
    """When max_global_active is reached, no more slots can be claimed."""
    mgr = WorkerPoolManager(_make_config(executor_max=3, verifier_max=2, max_global_active=2))

    s1 = mgr.claim_slot(WorkerRole.executor)
    s2 = mgr.claim_slot(WorkerRole.verifier)
    assert s1 is not None
    assert s2 is not None

    # Third claim blocked by global cap despite per-role availability
    assert mgr.claim_slot(WorkerRole.executor) is None
    assert mgr.can_accept(WorkerRole.executor) is False


def test_global_active_cap_release_unblocks() -> None:
    mgr = WorkerPoolManager(_make_config(executor_max=2, verifier_max=1, max_global_active=2))

    s1 = mgr.claim_slot(WorkerRole.executor)
    s2 = mgr.claim_slot(WorkerRole.verifier)
    assert s1 is not None
    assert s2 is not None

    mgr.release_slot(s1.slot_id)
    assert mgr.can_accept(WorkerRole.executor) is True
    s3 = mgr.claim_slot(WorkerRole.executor)
    assert s3 is not None


# -- per-supervisor limit ----------------------------------------------------


def test_per_supervisor_cap_blocks_claim() -> None:
    mgr = WorkerPoolManager(_make_config(executor_max=3, max_per_supervisor=2))
    mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-1")
    mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-1")

    # Third claim by same supervisor blocked
    assert mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-1") is None

    # Different supervisor can still claim
    s = mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-2")
    assert s is not None


def test_per_supervisor_can_accept() -> None:
    mgr = WorkerPoolManager(_make_config(executor_max=3, max_per_supervisor=1))
    mgr.claim_slot(WorkerRole.executor, supervisor_id="sup-A")

    assert mgr.can_accept(WorkerRole.executor, supervisor_id="sup-A") is False
    assert mgr.can_accept(WorkerRole.executor, supervisor_id="sup-B") is True


# -- conflict domain: workspace ---------------------------------------------


def test_workspace_conflict_limit() -> None:
    """Spec: max_same_workspace: 1 — only one worker per workspace."""
    mgr = WorkerPoolManager(_make_config(executor_max=3, conflict_limits={"max_same_workspace": 1}))
    s1 = mgr.claim_slot(WorkerRole.executor, workspace="ws-alpha")
    assert s1 is not None

    # Same workspace blocked
    assert mgr.claim_slot(WorkerRole.executor, workspace="ws-alpha") is None
    assert mgr.can_accept(WorkerRole.executor, workspace="ws-alpha") is False

    # Different workspace OK
    s2 = mgr.claim_slot(WorkerRole.executor, workspace="ws-beta")
    assert s2 is not None


def test_workspace_conflict_no_limit_when_zero() -> None:
    """When max_same_workspace=0 or absent, no workspace restriction."""
    mgr = WorkerPoolManager(_make_config(executor_max=3))
    s1 = mgr.claim_slot(WorkerRole.executor, workspace="ws-1")
    s2 = mgr.claim_slot(WorkerRole.executor, workspace="ws-1")
    assert s1 is not None
    assert s2 is not None


# -- conflict domain: module -------------------------------------------------


def test_module_conflict_limit() -> None:
    """Spec: max_same_module: 2 — at most 2 workers on the same module."""
    mgr = WorkerPoolManager(_make_config(executor_max=4, conflict_limits={"max_same_module": 2}))
    s1 = mgr.claim_slot(WorkerRole.executor, module="kernel.task")
    s2 = mgr.claim_slot(WorkerRole.executor, module="kernel.task")
    assert s1 is not None
    assert s2 is not None

    # Third on same module blocked
    assert mgr.claim_slot(WorkerRole.executor, module="kernel.task") is None
    assert mgr.can_accept(WorkerRole.executor, module="kernel.task") is False

    # Different module OK
    s3 = mgr.claim_slot(WorkerRole.executor, module="kernel.policy")
    assert s3 is not None


def test_module_field_on_claimed_slot() -> None:
    mgr = WorkerPoolManager(_make_config(executor_max=2, conflict_limits={"max_same_module": 2}))
    slot = mgr.claim_slot(WorkerRole.executor, module="runtime.runner")
    assert slot is not None
    assert slot.module == "runtime.runner"


def test_module_cleared_on_release() -> None:
    mgr = WorkerPoolManager(_make_config(executor_max=2, conflict_limits={"max_same_module": 1}))
    slot = mgr.claim_slot(WorkerRole.executor, module="runtime.runner")
    assert slot is not None

    # Same module blocked
    assert mgr.claim_slot(WorkerRole.executor, module="runtime.runner") is None

    # Release and reclaim
    mgr.release_slot(slot.slot_id)
    s2 = mgr.claim_slot(WorkerRole.executor, module="runtime.runner")
    assert s2 is not None


# -- release -----------------------------------------------------------------


def test_release_slot_makes_slot_available_again() -> None:
    mgr = WorkerPoolManager(_make_config(verifier_max=1))

    slot = mgr.claim_slot(WorkerRole.verifier)
    assert slot is not None
    assert mgr.can_accept(WorkerRole.verifier) is False

    mgr.release_slot(slot.slot_id)
    assert mgr.can_accept(WorkerRole.verifier) is True

    # Can claim again after release
    reclaimed = mgr.claim_slot(WorkerRole.verifier)
    assert reclaimed is not None
    assert reclaimed.slot_id == slot.slot_id


def test_release_unknown_slot_does_not_raise() -> None:
    mgr = WorkerPoolManager(_make_config())
    # Should log a warning but not raise
    mgr.release_slot("nonexistent-slot-id")


# -- get_status --------------------------------------------------------------


def test_status_all_idle() -> None:
    mgr = WorkerPoolManager(_make_config(executor_max=2, verifier_max=1))
    status = mgr.get_status()

    assert status.pool_id == "pool-1"
    assert status.active_slots == 0
    assert status.idle_slots == 3
    assert status.interrupted_slots == 0
    assert status.by_role["executor"] == 0
    assert status.by_role["verifier"] == 0


def test_status_reflects_claims() -> None:
    mgr = WorkerPoolManager(_make_config(executor_max=2, verifier_max=1))

    mgr.claim_slot(WorkerRole.executor)
    mgr.claim_slot(WorkerRole.verifier)

    status = mgr.get_status()
    assert status.active_slots == 2
    assert status.idle_slots == 1
    assert status.interrupted_slots == 0
    assert status.by_role["executor"] == 1
    assert status.by_role["verifier"] == 1


def test_status_reflects_releases() -> None:
    mgr = WorkerPoolManager(_make_config(executor_max=2, verifier_max=1))

    slot = mgr.claim_slot(WorkerRole.executor)
    assert slot is not None
    mgr.release_slot(slot.slot_id)

    status = mgr.get_status()
    assert status.active_slots == 0
    assert status.idle_slots == 3


def test_status_tracks_interrupted_slots() -> None:
    """interrupted_slots should be reported separately from active/idle."""
    mgr = WorkerPoolManager(_make_config(executor_max=2, verifier_max=1))

    slot = mgr.claim_slot(WorkerRole.executor)
    assert slot is not None

    # Manually set a slot to interrupted (simulating worker failure)
    with mgr._lock:
        internal_slot = mgr._slot_index[slot.slot_id]
        interrupted_slot = replace(internal_slot, status=SlotStatus.interrupted)
        mgr._slot_index[slot.slot_id] = interrupted_slot
        role_slots = mgr._slots[WorkerRole.executor]
        for idx, s in enumerate(role_slots):
            if s.slot_id == slot.slot_id:
                role_slots[idx] = interrupted_slot
                break

    status = mgr.get_status()
    assert status.active_slots == 0
    assert status.interrupted_slots == 1
    assert status.idle_slots == 2  # 3 total minus 1 interrupted


# -- thread safety -----------------------------------------------------------


def test_concurrent_claim_respects_capacity() -> None:
    """Slots must never be double-claimed under concurrent access."""
    mgr = WorkerPoolManager(_make_config(executor_max=5))

    results: list[object] = []
    barrier = threading.Barrier(10)

    def claim() -> None:
        barrier.wait()
        slot = mgr.claim_slot(WorkerRole.executor)
        results.append(slot)

    threads = [threading.Thread(target=claim) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    claimed = [r for r in results if r is not None]
    assert len(claimed) == 5
    # All claimed slot ids must be unique
    ids = [s.slot_id for s in claimed]
    assert len(set(ids)) == 5


def test_concurrent_claim_respects_global_cap() -> None:
    """Global cap must hold under concurrent access."""
    mgr = WorkerPoolManager(_make_config(executor_max=5, max_global_active=3))

    results: list[object] = []
    barrier = threading.Barrier(8)

    def claim() -> None:
        barrier.wait()
        slot = mgr.claim_slot(WorkerRole.executor)
        results.append(slot)

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    claimed = [r for r in results if r is not None]
    assert len(claimed) == 3


# -- model enums -------------------------------------------------------------


def test_worker_role_values() -> None:
    """All spec-defined roles must be present."""
    assert set(WorkerRole) == {
        WorkerRole.planner,
        WorkerRole.executor,
        WorkerRole.verifier,
        WorkerRole.benchmarker,
        WorkerRole.researcher,
        WorkerRole.reconciler,
        WorkerRole.tester,
        WorkerRole.spec,
        WorkerRole.reviewer,
    }


def test_slot_status_values() -> None:
    assert set(SlotStatus) == {
        SlotStatus.idle,
        SlotStatus.busy,
        SlotStatus.interrupted,
    }


# -- DEFAULT_CONFLICT_LIMITS -------------------------------------------------


def test_default_conflict_limits() -> None:
    """Spec: max_same_workspace: 1, max_same_module: 2."""
    assert DEFAULT_CONFLICT_LIMITS == {
        "max_same_workspace": 1,
        "max_same_module": 2,
    }


# -- WorkerPoolStatus.interrupted_slots default ------------------------------


def test_pool_status_interrupted_default() -> None:
    status = WorkerPoolStatus(pool_id="p", active_slots=0, idle_slots=0)
    assert status.interrupted_slots == 0
