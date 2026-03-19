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
