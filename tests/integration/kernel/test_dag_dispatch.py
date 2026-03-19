"""Integration tests for DAG dispatch loop and wake mechanism."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.dag_builder import StepDAGBuilder, StepNode


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture
def builder(store: KernelStore) -> StepDAGBuilder:
    return StepDAGBuilder(store)


def _make_task(store: KernelStore) -> str:
    store.ensure_conversation("conv_1", source_channel="test")
    task = store.create_task(
        conversation_id="conv_1", title="test", goal="test", source_channel="test"
    )
    store.update_task_status(task.task_id, "queued")
    return task.task_id


def test_claim_only_returns_ready_attempts(store: KernelStore, builder: StepDAGBuilder) -> None:
    """claim_next_ready_step_attempt skips steps with unsatisfied dependencies."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    # Only A should be claimable
    claimed = store.claim_next_ready_step_attempt()
    assert claimed is not None
    assert claimed.step_id == key_map["a"]

    # Nothing else claimable
    assert store.claim_next_ready_step_attempt() is None


def test_claim_after_activation(store: KernelStore, builder: StepDAGBuilder) -> None:
    """After dependency satisfied, newly ready steps become claimable."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    # Claim and complete A
    claimed = store.claim_next_ready_step_attempt()
    assert claimed is not None
    store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
    store.update_step_attempt(claimed.step_attempt_id, status="succeeded", finished_at=time.time())
    store.activate_waiting_dependents(task_id, key_map["a"])

    # Now B should be claimable
    claimed_b = store.claim_next_ready_step_attempt()
    assert claimed_b is not None
    assert claimed_b.step_id == key_map["b"]


def test_parallel_claims_for_independent_steps(store: KernelStore, builder: StepDAGBuilder) -> None:
    """Independent steps at the same level can be claimed in sequence."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="root", kind="execute", title="Root"),
        StepNode(key="b", kind="execute", title="B", depends_on=["root"]),
        StepNode(key="c", kind="execute", title="C", depends_on=["root"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    # Complete root
    claimed = store.claim_next_ready_step_attempt()
    assert claimed is not None
    store.update_step(key_map["root"], status="succeeded", finished_at=time.time())
    store.update_step_attempt(claimed.step_attempt_id, status="succeeded", finished_at=time.time())
    store.activate_waiting_dependents(task_id, key_map["root"])

    # Both B and C should be claimable
    claimed_1 = store.claim_next_ready_step_attempt()
    assert claimed_1 is not None
    claimed_2 = store.claim_next_ready_step_attempt()
    assert claimed_2 is not None
    claimed_ids = {claimed_1.step_id, claimed_2.step_id}
    assert claimed_ids == {key_map["b"], key_map["c"]}


def test_failed_step_prevents_downstream_claim(store: KernelStore, builder: StepDAGBuilder) -> None:
    """After a step fails and cascades, downstream steps are not claimable."""
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    claimed = store.claim_next_ready_step_attempt()
    assert claimed is not None
    store.update_step(key_map["a"], status="failed", finished_at=time.time())
    store.update_step_attempt(claimed.step_attempt_id, status="failed", finished_at=time.time())
    store.propagate_step_failure(task_id, key_map["a"])

    # B is now failed, not claimable
    assert store.claim_next_ready_step_attempt() is None
    assert store.get_step(key_map["b"]).status == "failed"


def test_double_activation_is_idempotent(store: KernelStore, builder: StepDAGBuilder) -> None:
    """Calling activate_waiting_dependents twice for the same completed step is idempotent.

    Both controller.finalize_result() and dispatch._on_attempt_completed() may call
    activate_waiting_dependents for the same step.  The downstream step must get exactly
    one ready attempt, not two.
    """
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    # Claim and complete A
    claimed = store.claim_next_ready_step_attempt()
    assert claimed is not None
    store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
    store.update_step_attempt(claimed.step_attempt_id, status="succeeded", finished_at=time.time())

    # First activation — should transition B from waiting to ready
    activated_1 = store.activate_waiting_dependents(task_id, key_map["a"])
    assert key_map["b"] in activated_1

    # Second activation — B is already ready, should be a no-op
    activated_2 = store.activate_waiting_dependents(task_id, key_map["a"])
    assert activated_2 == []

    # B's step status must be "ready", not double-transitioned
    step_b = store.get_step(key_map["b"])
    assert step_b.status == "ready"

    # B must have exactly one attempt, and it must be "ready"
    attempts_b = store.list_step_attempts(step_id=key_map["b"])
    assert len(attempts_b) == 1
    assert attempts_b[0].status == "ready"

    # B should be claimable exactly once
    claimed_b = store.claim_next_ready_step_attempt()
    assert claimed_b is not None
    assert claimed_b.step_id == key_map["b"]
    assert store.claim_next_ready_step_attempt() is None


def test_force_fail_attempt_diamond_dag_propagation(
    store: KernelStore, builder: StepDAGBuilder
) -> None:
    """Simulate _force_fail_attempt on a diamond DAG (A -> B, C -> D).

    Scenario: A succeeds, B and C become ready. B's worker crashes (unhandled
    exception), triggering the _force_fail_attempt path. This test exercises the
    same store operations that KernelDispatchService._force_fail_attempt performs:

    1. The crashed attempt and step B are marked failed.
    2. propagate_step_failure cascades to D (all_required join on B and C).
    3. Step C remains unaffected (still ready).
    4. The task transitions to failed when no non-terminal steps remain.
    """
    task_id = _make_task(store)
    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
        StepNode(key="d", kind="execute", title="D", depends_on=["b", "c"]),
    ]
    _dag, key_map = builder.build_and_materialize(task_id, nodes)

    # ── Step A: claim, run, succeed ──
    claimed_a = store.claim_next_ready_step_attempt()
    assert claimed_a is not None
    assert claimed_a.step_id == key_map["a"]
    now = time.time()
    store.update_step_attempt(claimed_a.step_attempt_id, status="succeeded", finished_at=now)
    store.update_step(key_map["a"], status="succeeded", finished_at=now)
    store.activate_waiting_dependents(task_id, key_map["a"])

    # B and C should now be ready
    assert store.get_step(key_map["b"]).status == "ready"
    assert store.get_step(key_map["c"]).status == "ready"

    # ── Step B: claim, then simulate worker crash via _force_fail_attempt logic ──
    claimed_b = store.claim_next_ready_step_attempt()
    assert claimed_b is not None
    assert claimed_b.step_id == key_map["b"]

    # Reproduce the exact sequence from KernelDispatchService._force_fail_attempt:
    # 1. Mark the attempt as failed with worker_exception reason
    crash_time = time.time()
    store.update_step_attempt(
        claimed_b.step_attempt_id,
        status="failed",
        waiting_reason="worker_exception",
        finished_at=crash_time,
    )
    # 2. Mark the step as failed
    store.update_step(key_map["b"], status="failed", finished_at=crash_time)

    # Verify: attempt and step B are failed
    failed_attempt = store.get_step_attempt(claimed_b.step_attempt_id)
    assert failed_attempt is not None
    assert failed_attempt.status == "failed"
    assert failed_attempt.waiting_reason == "worker_exception"
    assert store.get_step(key_map["b"]).status == "failed"

    # 3. Propagate failure through the DAG
    cascaded = store.propagate_step_failure(task_id, key_map["b"])

    # D depends on B with all_required join — it must be cascade-failed
    assert key_map["d"] in cascaded
    assert store.get_step(key_map["d"]).status == "failed"

    # C is on an independent branch — it must remain ready
    assert store.get_step(key_map["c"]).status == "ready"

    # ── Complete C to make all steps terminal ──
    claimed_c = store.claim_next_ready_step_attempt()
    assert claimed_c is not None
    assert claimed_c.step_id == key_map["c"]
    done_time = time.time()
    store.update_step_attempt(claimed_c.step_attempt_id, status="succeeded", finished_at=done_time)
    store.update_step(key_map["c"], status="succeeded", finished_at=done_time)

    # 4. Check task termination condition: no non-terminal steps remain
    assert not store.has_non_terminal_steps(task_id)

    # Transition task to failed (same as _force_fail_attempt does)
    store.update_task_status(
        task_id,
        "failed",
        payload={"result_preview": "worker_exception", "result_text": "worker_exception"},
    )
    task = store.get_task(task_id)
    assert task is not None
    assert task.status == "failed"
