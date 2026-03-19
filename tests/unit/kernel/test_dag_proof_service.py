"""Tests for kernel/verification/proofs/dag_proof.py — DAG proof bundle generation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.kernel.verification.proofs.dag_proof import DAGProofBundle, DAGProofService


def _make_step(step_id: str, depends_on: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(step_id=step_id, depends_on=depends_on or [])


def _make_receipt(receipt_id: str, step_id: str) -> SimpleNamespace:
    return SimpleNamespace(receipt_id=receipt_id, step_id=step_id)


class TestDAGProofBundleFrozen:
    def test_is_frozen_dataclass(self) -> None:
        bundle = DAGProofBundle(task_id="t1", dag_definition_ref="ref1")
        try:
            bundle.task_id = "t2"  # type: ignore[misc]
            raise AssertionError("Should have raised FrozenInstanceError")
        except AttributeError:
            pass

    def test_defaults(self) -> None:
        bundle = DAGProofBundle(task_id="t1", dag_definition_ref="ref1")
        assert bundle.step_receipts == {}
        assert bundle.join_events == []
        assert bundle.root_step_ids == []
        assert bundle.leaf_step_ids == []


class TestDAGProofServiceNoSteps:
    def test_empty_steps_returns_empty_bundle(self) -> None:
        store = MagicMock()
        store.list_steps.return_value = []
        proof_service = MagicMock()
        svc = DAGProofService(store, proof_service)

        result = svc.generate("task-1")

        assert result.task_id == "task-1"
        assert result.dag_definition_ref == ""
        assert result.step_receipts == {}
        assert result.join_events == []
        assert result.root_step_ids == []
        assert result.leaf_step_ids == []


class TestDAGProofServiceLinearChain:
    """A -> B -> C linear chain."""

    def test_linear_chain_root_and_leaves(self) -> None:
        steps = [
            _make_step("s1", []),
            _make_step("s2", ["s1"]),
            _make_step("s3", ["s2"]),
        ]
        receipts = [
            _make_receipt("r1", "s1"),
            _make_receipt("r2", "s2"),
        ]
        events = [{"event_id": "evt-1"}]

        store = MagicMock()
        store.list_steps.return_value = steps
        store.list_receipts.return_value = receipts
        store.list_events.return_value = events
        proof_service = MagicMock()

        svc = DAGProofService(store, proof_service)
        result = svc.generate("task-1")

        assert result.task_id == "task-1"
        assert result.dag_definition_ref == "task:task-1:dag"
        assert result.root_step_ids == ["s1"]
        assert result.leaf_step_ids == ["s3"]
        assert result.step_receipts["s1"] == ["r1"]
        assert result.step_receipts["s2"] == ["r2"]
        assert result.step_receipts["s3"] == []
        assert result.join_events == ["evt-1"]


class TestDAGProofServiceDiamondDAG:
    """Diamond: A -> B, A -> C, B -> D, C -> D."""

    def test_diamond_root_and_leaf(self) -> None:
        steps = [
            _make_step("a", []),
            _make_step("b", ["a"]),
            _make_step("c", ["a"]),
            _make_step("d", ["b", "c"]),
        ]
        receipts = [
            _make_receipt("r_a", "a"),
            _make_receipt("r_b", "b"),
            _make_receipt("r_c", "c"),
            _make_receipt("r_d", "d"),
        ]
        events: list[dict] = []

        store = MagicMock()
        store.list_steps.return_value = steps
        store.list_receipts.return_value = receipts
        store.list_events.return_value = events
        proof_service = MagicMock()

        svc = DAGProofService(store, proof_service)
        result = svc.generate("task-2")

        assert result.root_step_ids == ["a"]
        assert result.leaf_step_ids == ["d"]
        assert len(result.step_receipts) == 4

    def test_multiple_receipts_per_step(self) -> None:
        steps = [_make_step("s1", [])]
        receipts = [
            _make_receipt("r1", "s1"),
            _make_receipt("r2", "s1"),
            _make_receipt("r3", "s1"),
        ]
        store = MagicMock()
        store.list_steps.return_value = steps
        store.list_receipts.return_value = receipts
        store.list_events.return_value = []
        proof_service = MagicMock()

        svc = DAGProofService(store, proof_service)
        result = svc.generate("task-3")

        assert result.step_receipts["s1"] == ["r1", "r2", "r3"]


class TestDAGProofServiceMultipleRootsAndLeaves:
    def test_parallel_roots_and_leaves(self) -> None:
        steps = [
            _make_step("r1", []),
            _make_step("r2", []),
            _make_step("mid", ["r1"]),
            _make_step("leaf1", []),  # independent leaf
        ]
        store = MagicMock()
        store.list_steps.return_value = steps
        store.list_receipts.return_value = []
        store.list_events.return_value = []
        proof_service = MagicMock()

        svc = DAGProofService(store, proof_service)
        result = svc.generate("task-4")

        assert "r1" in result.root_step_ids
        assert "r2" in result.root_step_ids
        assert "leaf1" in result.root_step_ids  # no deps = root
        # r2 is not a dependency of anything
        assert "r2" in result.leaf_step_ids
        # mid depends on r1 but nothing depends on mid
        assert "mid" in result.leaf_step_ids
        assert "leaf1" in result.leaf_step_ids


class TestDAGProofServiceStoreInteraction:
    def test_passes_correct_params_to_store(self) -> None:
        store = MagicMock()
        store.list_steps.return_value = [_make_step("s1", [])]
        store.list_receipts.return_value = []
        store.list_events.return_value = []
        proof_service = MagicMock()

        svc = DAGProofService(store, proof_service)
        svc.generate("task-x")

        store.list_steps.assert_called_once_with(task_id="task-x", limit=1000)
        store.list_receipts.assert_called_once_with(task_id="task-x", limit=1000)
        store.list_events.assert_called_once_with(
            task_id="task-x",
            event_type="step.dependency_satisfied",
            limit=1000,
        )
