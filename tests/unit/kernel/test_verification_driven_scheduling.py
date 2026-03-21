"""Tests for verification-driven scheduling (Spec 03).

Covers:
- Verification gates (opt-in blocking on reconciliation issues)
- Verifier-driven reopen (invalidation creates new attempts)
- First-class edge types (verifies, supersedes)
- Receipt HMAC signing
- Backward compatibility (non-verified DAGs unchanged)
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.dag_builder import StepDAGBuilder, StepNode
from hermit.kernel.task.services.dag_execution import DAGExecutionService
from hermit.kernel.verification.receipts.receipts import ReceiptService


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture
def builder(store: KernelStore) -> StepDAGBuilder:
    return StepDAGBuilder(store)


@pytest.fixture
def dag_exec(store: KernelStore) -> DAGExecutionService:
    return DAGExecutionService(store)


def _make_task(store: KernelStore) -> str:
    store.ensure_conversation("conv_1", source_channel="test")
    task = store.create_task(
        conversation_id="conv_1",
        title="test",
        goal="test",
        source_channel="test",
    )
    return task.task_id


# ──────────────────────────────────────────────────────────────────────
# Edge type validation
# ──────────────────────────────────────────────────────────────────────


class TestEdgeTypes:
    def test_verifies_edge_validation(self, builder: StepDAGBuilder) -> None:
        """verifies edges must reference existing step keys."""
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="v", kind="verify", title="V", verifies=["a"]),
        ]
        dag = builder.validate(nodes)
        assert "a" in dag.topological_order
        assert "v" in dag.topological_order
        # v must come after a in topo order
        assert dag.topological_order.index("a") < dag.topological_order.index("v")

    def test_supersedes_edge_validation(self, builder: StepDAGBuilder) -> None:
        """supersedes edges must reference existing step keys."""
        nodes = [
            StepNode(key="old", kind="execute", title="Old"),
            StepNode(key="new", kind="execute", title="New", supersedes=["old"]),
        ]
        dag = builder.validate(nodes)
        assert dag.topological_order.index("old") < dag.topological_order.index("new")

    def test_verifies_unknown_ref_rejected(self, builder: StepDAGBuilder) -> None:
        """verifies referencing a non-existent key raises ValueError."""
        nodes = [
            StepNode(key="v", kind="verify", title="V", verifies=["nonexistent"]),
        ]
        with pytest.raises(ValueError, match="verifies unknown step"):
            builder.validate(nodes)

    def test_supersedes_unknown_ref_rejected(self, builder: StepDAGBuilder) -> None:
        """supersedes referencing a non-existent key raises ValueError."""
        nodes = [
            StepNode(key="s", kind="execute", title="S", supersedes=["nonexistent"]),
        ]
        with pytest.raises(ValueError, match="supersedes unknown step"):
            builder.validate(nodes)

    def test_cycle_detection_with_verifies(self, builder: StepDAGBuilder) -> None:
        """Cycle via verifies edge should be detected."""
        nodes = [
            StepNode(key="a", kind="execute", title="A", verifies=["b"]),
            StepNode(key="b", kind="execute", title="B", verifies=["a"]),
        ]
        with pytest.raises(ValueError, match="Cycle detected"):
            builder.validate(nodes)

    def test_verifies_and_depends_on_combined(self, builder: StepDAGBuilder) -> None:
        """A step can both depend on and verify the same step."""
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="v", kind="verify", title="V", depends_on=["a"], verifies=["a"]),
        ]
        dag = builder.validate(nodes)
        assert dag.topological_order.index("a") < dag.topological_order.index("v")

    def test_materialize_persists_edge_types(
        self, store: KernelStore, builder: StepDAGBuilder
    ) -> None:
        """verifies and supersedes fields should be persisted to the store."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="v", kind="verify", title="V", depends_on=["a"], verifies=["a"]),
            StepNode(key="s", kind="execute", title="S", depends_on=["a"], supersedes=["a"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        v_step = store.get_step(key_map["v"])
        assert v_step is not None
        assert key_map["a"] in v_step.verifies

        s_step = store.get_step(key_map["s"])
        assert s_step is not None
        assert key_map["a"] in s_step.supersedes

    def test_verification_required_persisted(
        self, store: KernelStore, builder: StepDAGBuilder
    ) -> None:
        """verification_required field should be persisted."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(
                key="b",
                kind="execute",
                title="B",
                depends_on=["a"],
                verification_required=True,
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        b_step = store.get_step(key_map["b"])
        assert b_step is not None
        assert b_step.verification_required is True


# ──────────────────────────────────────────────────────────────────────
# Verification gates
# ──────────────────────────────────────────────────────────────────────


class TestVerificationGate:
    def test_gate_blocks_on_reconciliation_required(
        self,
        store: KernelStore,
        builder: StepDAGBuilder,
        dag_exec: DAGExecutionService,
    ) -> None:
        """When upstream receipt has reconciliation_required=True, gate blocks downstream."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(
                key="b",
                kind="execute",
                title="B",
                depends_on=["a"],
                verification_required=True,
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        # Complete step A
        attempt_a = store.list_step_attempts(step_id=key_map["a"], limit=1)[0]
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        store.update_step_attempt(attempt_a.step_attempt_id, status="succeeded")

        # Create a receipt with reconciliation_required=True
        store.create_receipt(
            task_id=task_id,
            step_id=key_map["a"],
            step_attempt_id=attempt_a.step_attempt_id,
            action_type="execute",
            input_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            output_refs=[],
            result_summary="done",
            reconciliation_required=True,
        )

        # Advance the DAG
        dag_exec.advance(
            task_id=task_id,
            step_id=key_map["a"],
            step_attempt_id=attempt_a.step_attempt_id,
            status="succeeded",
        )

        # Step B should be blocked
        b_step = store.get_step(key_map["b"])
        assert b_step is not None
        assert b_step.status == "verification_blocked"

        # Event should have been emitted
        events = store.list_events(task_id=task_id, event_type="verification.gate_blocked")
        assert len(events) >= 1
        assert events[0]["payload"]["blocked_step_id"] == key_map["b"]

    def test_gate_passes_when_no_issues(
        self,
        store: KernelStore,
        builder: StepDAGBuilder,
        dag_exec: DAGExecutionService,
    ) -> None:
        """When no receipt has reconciliation issues, gate passes normally."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(
                key="b",
                kind="execute",
                title="B",
                depends_on=["a"],
                verification_required=True,
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        attempt_a = store.list_step_attempts(step_id=key_map["a"], limit=1)[0]
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        store.update_step_attempt(attempt_a.step_attempt_id, status="succeeded")

        # Create a clean receipt (no reconciliation issues)
        store.create_receipt(
            task_id=task_id,
            step_id=key_map["a"],
            step_attempt_id=attempt_a.step_attempt_id,
            action_type="execute",
            input_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            output_refs=[],
            result_summary="done",
            reconciliation_required=False,
        )

        dag_exec.advance(
            task_id=task_id,
            step_id=key_map["a"],
            step_attempt_id=attempt_a.step_attempt_id,
            status="succeeded",
        )

        # Step B should be ready (not blocked)
        b_step = store.get_step(key_map["b"])
        assert b_step is not None
        assert b_step.status == "ready"

    def test_non_verified_steps_unaffected(
        self,
        store: KernelStore,
        builder: StepDAGBuilder,
        dag_exec: DAGExecutionService,
    ) -> None:
        """Steps without verification_required should not be affected by reconciliation."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        attempt_a = store.list_step_attempts(step_id=key_map["a"], limit=1)[0]
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        store.update_step_attempt(attempt_a.step_attempt_id, status="succeeded")

        # Create a receipt with reconciliation issues
        store.create_receipt(
            task_id=task_id,
            step_id=key_map["a"],
            step_attempt_id=attempt_a.step_attempt_id,
            action_type="execute",
            input_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            output_refs=[],
            result_summary="done",
            reconciliation_required=True,
        )

        dag_exec.advance(
            task_id=task_id,
            step_id=key_map["a"],
            step_attempt_id=attempt_a.step_attempt_id,
            status="succeeded",
        )

        # Step B should be activated (not blocked) because it doesn't require verification
        b_step = store.get_step(key_map["b"])
        assert b_step is not None
        assert b_step.status == "ready"


# ──────────────────────────────────────────────────────────────────────
# Verifier-driven reopen
# ──────────────────────────────────────────────────────────────────────


class TestVerifierReopen:
    def test_reopen_creates_new_attempt(
        self,
        store: KernelStore,
        builder: StepDAGBuilder,
        dag_exec: DAGExecutionService,
    ) -> None:
        """When a verifier step fails, the verified step gets a new attempt."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A", max_attempts=3),
            StepNode(
                key="v",
                kind="verify",
                title="V",
                depends_on=["a"],
                verifies=["a"],
                max_attempts=1,
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        # Complete step A
        attempt_a = store.list_step_attempts(step_id=key_map["a"], limit=1)[0]
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        store.update_step_attempt(attempt_a.step_attempt_id, status="succeeded")
        store.activate_waiting_dependents(task_id, key_map["a"])

        # Complete step V (verifier) — succeed first to activate it, then run it
        attempt_v = store.list_step_attempts(step_id=key_map["v"], status="ready", limit=1)[0]
        store.update_step(key_map["v"], status="failed", finished_at=time.time())
        store.update_step_attempt(attempt_v.step_attempt_id, status="failed")

        # Advance with verifier failure
        dag_exec.advance(
            task_id=task_id,
            step_id=key_map["v"],
            step_attempt_id=attempt_v.step_attempt_id,
            status="failed",
        )

        # Step A should have a new attempt
        step_a = store.get_step(key_map["a"])
        assert step_a is not None
        assert step_a.attempt == 2
        assert step_a.status == "ready"

    def test_reopen_emits_invalidation_event(
        self,
        store: KernelStore,
        builder: StepDAGBuilder,
        dag_exec: DAGExecutionService,
    ) -> None:
        """Reopen should emit verification.step_invalidated event."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A", max_attempts=3),
            StepNode(
                key="v",
                kind="verify",
                title="V",
                depends_on=["a"],
                verifies=["a"],
                max_attempts=1,
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        attempt_a = store.list_step_attempts(step_id=key_map["a"], limit=1)[0]
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        store.update_step_attempt(attempt_a.step_attempt_id, status="succeeded")
        store.activate_waiting_dependents(task_id, key_map["a"])

        attempt_v = store.list_step_attempts(step_id=key_map["v"], status="ready", limit=1)[0]
        store.update_step(key_map["v"], status="failed", finished_at=time.time())
        store.update_step_attempt(attempt_v.step_attempt_id, status="failed")

        dag_exec.advance(
            task_id=task_id,
            step_id=key_map["v"],
            step_attempt_id=attempt_v.step_attempt_id,
            status="failed",
        )

        events = store.list_events(task_id=task_id, event_type="verification.step_invalidated")
        assert len(events) >= 1
        payload = events[0]["payload"]
        assert payload["invalidated_step_id"] == key_map["a"]
        assert payload["verifier_step_id"] == key_map["v"]

    def test_original_attempt_unchanged(
        self,
        store: KernelStore,
        builder: StepDAGBuilder,
        dag_exec: DAGExecutionService,
    ) -> None:
        """The original attempt record should remain unchanged after reopen."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A", max_attempts=3),
            StepNode(
                key="v",
                kind="verify",
                title="V",
                depends_on=["a"],
                verifies=["a"],
                max_attempts=1,
            ),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        attempt_a = store.list_step_attempts(step_id=key_map["a"], limit=1)[0]
        original_attempt_id = attempt_a.step_attempt_id
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        store.update_step_attempt(attempt_a.step_attempt_id, status="succeeded")
        store.activate_waiting_dependents(task_id, key_map["a"])

        attempt_v = store.list_step_attempts(step_id=key_map["v"], status="ready", limit=1)[0]
        store.update_step(key_map["v"], status="failed", finished_at=time.time())
        store.update_step_attempt(attempt_v.step_attempt_id, status="failed")

        dag_exec.advance(
            task_id=task_id,
            step_id=key_map["v"],
            step_attempt_id=attempt_v.step_attempt_id,
            status="failed",
        )

        # Original attempt should still be succeeded
        original = store.get_step_attempt(original_attempt_id)
        assert original is not None
        assert original.status == "succeeded"

        # There should be a new attempt
        all_attempts = store.list_step_attempts(step_id=key_map["a"])
        assert len(all_attempts) >= 2


# ──────────────────────────────────────────────────────────────────────
# Receipt HMAC signing
# ──────────────────────────────────────────────────────────────────────


class TestReceiptSigning:
    def test_signature_populated_when_secret_set(
        self, store: KernelStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HMAC signature should be populated when HERMIT_PROOF_SIGNING_SECRET is set."""
        monkeypatch.setenv("HERMIT_PROOF_SIGNING_SECRET", "test-secret-key")
        sig = ReceiptService._compute_signature(
            {
                "receipt_id": "receipt_001",
                "task_id": "task_001",
                "step_id": "step_001",
                "action_type": "execute",
                "result_code": "succeeded",
            }
        )
        assert sig is not None
        # v2 format: "v2:" prefix + 64 hex chars
        assert sig.startswith("v2:")
        assert len(sig) == 3 + 64

    def test_no_signature_without_secret(
        self, store: KernelStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No signature should be returned when no signing secret is configured."""
        monkeypatch.delenv("HERMIT_PROOF_SIGNING_SECRET", raising=False)
        sig = ReceiptService._compute_signature(
            {
                "receipt_id": "receipt_001",
                "task_id": "task_001",
                "step_id": "step_001",
                "action_type": "execute",
                "result_code": "succeeded",
            }
        )
        assert sig is None

    def test_signature_deterministic(
        self, store: KernelStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same inputs should produce the same signature."""
        monkeypatch.setenv("HERMIT_PROOF_SIGNING_SECRET", "test-secret-key")
        data = {
            "receipt_id": "receipt_001",
            "task_id": "task_001",
            "step_id": "step_001",
            "action_type": "execute",
            "result_code": "succeeded",
        }
        sig1 = ReceiptService._compute_signature(data)
        sig2 = ReceiptService._compute_signature(data)
        assert sig1 == sig2

    def test_different_inputs_different_signature(
        self, store: KernelStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Different inputs should produce different signatures."""
        monkeypatch.setenv("HERMIT_PROOF_SIGNING_SECRET", "test-secret-key")
        sig1 = ReceiptService._compute_signature(
            {
                "receipt_id": "receipt_001",
                "task_id": "task_001",
                "step_id": "step_001",
                "action_type": "execute",
                "result_code": "succeeded",
            }
        )
        sig2 = ReceiptService._compute_signature(
            {
                "receipt_id": "receipt_002",
                "task_id": "task_001",
                "step_id": "step_001",
                "action_type": "execute",
                "result_code": "succeeded",
            }
        )
        assert sig1 != sig2

    def test_update_receipt_signature(self, store: KernelStore) -> None:
        """update_receipt_signature should persist the signature."""
        task_id = _make_task(store)
        step = store.create_step(task_id=task_id, kind="execute", title="test")
        attempt = store.create_step_attempt(task_id=task_id, step_id=step.step_id, status="running")
        receipt = store.create_receipt(
            task_id=task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            action_type="execute",
            input_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            output_refs=[],
            result_summary="done",
        )
        assert receipt.signature is None

        store.update_receipt_signature(receipt.receipt_id, "test-sig-value")
        updated = store.get_receipt(receipt.receipt_id)
        assert updated is not None
        assert updated.signature == "test-sig-value"


# ──────────────────────────────────────────────────────────────────────
# Backward compatibility
# ──────────────────────────────────────────────────────────────────────


class TestBackwardCompatibility:
    def test_non_verified_dag_unchanged(
        self,
        store: KernelStore,
        builder: StepDAGBuilder,
        dag_exec: DAGExecutionService,
    ) -> None:
        """A DAG without verification features should work exactly as before."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
            StepNode(key="d", kind="execute", title="D", depends_on=["b", "c"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        # Complete step A
        attempt_a = store.list_step_attempts(step_id=key_map["a"], limit=1)[0]
        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        store.update_step_attempt(attempt_a.step_attempt_id, status="succeeded")

        dag_exec.advance(
            task_id=task_id,
            step_id=key_map["a"],
            step_attempt_id=attempt_a.step_attempt_id,
            status="succeeded",
        )

        # B and C should be activated
        assert store.get_step(key_map["b"]).status == "ready"
        assert store.get_step(key_map["c"]).status == "ready"
        # D still waiting
        assert store.get_step(key_map["d"]).status == "waiting"

    def test_step_record_defaults(self, store: KernelStore) -> None:
        """New StepRecord fields should have safe defaults."""
        task_id = _make_task(store)
        step = store.create_step(task_id=task_id, kind="execute", title="test")
        assert step.verification_required is False
        assert step.verifies == []
        assert step.supersedes == []

    def test_failure_propagation_unchanged_without_verifies(
        self,
        store: KernelStore,
        builder: StepDAGBuilder,
        dag_exec: DAGExecutionService,
    ) -> None:
        """Normal failure handling should work for steps without verifies edges."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        attempt_a = store.list_step_attempts(step_id=key_map["a"], limit=1)[0]
        store.update_step(key_map["a"], status="failed", finished_at=time.time())
        store.update_step_attempt(attempt_a.step_attempt_id, status="failed")

        dag_exec.advance(
            task_id=task_id,
            step_id=key_map["a"],
            step_attempt_id=attempt_a.step_attempt_id,
            status="failed",
        )

        # B should be failed (cascaded)
        b_step = store.get_step(key_map["b"])
        assert b_step is not None
        assert b_step.status == "failed"


# ──────────────────────────────────────────────────────────────────────
# list_receipts_for_step
# ──────────────────────────────────────────────────────────────────────


class TestListReceiptsForStep:
    def test_returns_receipts_for_specific_step(self, store: KernelStore) -> None:
        """list_receipts_for_step should only return receipts for the given step."""
        task_id = _make_task(store)
        step1 = store.create_step(task_id=task_id, kind="execute", title="s1")
        step2 = store.create_step(task_id=task_id, kind="execute", title="s2")
        attempt1 = store.create_step_attempt(
            task_id=task_id, step_id=step1.step_id, status="running"
        )
        attempt2 = store.create_step_attempt(
            task_id=task_id, step_id=step2.step_id, status="running"
        )

        store.create_receipt(
            task_id=task_id,
            step_id=step1.step_id,
            step_attempt_id=attempt1.step_attempt_id,
            action_type="execute",
            input_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            output_refs=[],
            result_summary="done1",
        )
        store.create_receipt(
            task_id=task_id,
            step_id=step2.step_id,
            step_attempt_id=attempt2.step_attempt_id,
            action_type="execute",
            input_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            output_refs=[],
            result_summary="done2",
        )

        receipts = store.list_receipts_for_step(step_id=step1.step_id)
        assert len(receipts) == 1
        assert receipts[0].result_summary == "done1"
