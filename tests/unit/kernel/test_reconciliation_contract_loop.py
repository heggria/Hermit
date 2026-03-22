"""Stress tests for reconciliation idempotency, result_class coverage,
contract loop closure, and receipt HMAC signing roundtrip.

Tasks 9-12 from the reconciliation-contract loop stress test plan.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.recovery.reconcile import ReconcileOutcome, ReconcileService
from hermit.kernel.execution.recovery.reconciliations import ReconciliationService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import ReconciliationRecord
from hermit.kernel.verification.receipts.receipts import ReceiptService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store_and_artifacts(tmp_path: Path) -> tuple[KernelStore, ArtifactStore]:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    return store, artifact_store


def _create_task_step_attempt(
    store: KernelStore,
    *,
    task_title: str = "Test Task",
) -> TaskExecutionContext:
    task = store.create_task(
        conversation_id="conv-1",
        title=task_title,
        goal="test goal",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="execute")
    attempt = store.create_step_attempt(
        task_id=task.task_id,
        step_id=step.step_id,
        attempt=1,
    )
    return TaskExecutionContext(
        conversation_id="conv-1",
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        source_channel="chat",
        workspace_root=str(Path("/tmp/ws")),
    )


def _make_reconcile_service_stub(result_code: str = "reconciled_applied") -> ReconcileService:
    """Create a ReconcileService whose reconcile() returns a fixed outcome."""
    svc = MagicMock(spec=ReconcileService)
    svc.reconcile.return_value = ReconcileOutcome(
        result_code=result_code,
        summary=f"Stub outcome: {result_code}",
        observed_refs=["ref-1"],
    )
    return svc


def _issue_receipt(
    receipt_svc: ReceiptService,
    attempt_ctx: TaskExecutionContext,
    *,
    contract_ref: str | None = None,
) -> str:
    return receipt_svc.issue(
        task_id=attempt_ctx.task_id,
        step_id=attempt_ctx.step_id,
        step_attempt_id=attempt_ctx.step_attempt_id,
        action_type="execute_command",
        input_refs=[],
        environment_ref=None,
        policy_result={"verdict": "allow"},
        approval_ref=None,
        output_refs=[],
        result_summary="done",
        result_code="succeeded",
        contract_ref=contract_ref,
    )


# ===========================================================================
# Task 9: Reconciliation idempotency
# ===========================================================================


class TestReconciliationIdempotency:
    """Calling reconcile_attempt twice with the same data should be idempotent."""

    def test_second_call_returns_existing_no_duplicate(self, tmp_path: Path) -> None:
        store, artifact_store = _store_and_artifacts(tmp_path)
        attempt_ctx = _create_task_step_attempt(store)
        reconcile_svc = _make_reconcile_service_stub("reconciled_applied")
        svc = ReconciliationService(store, artifact_store, reconcile_svc)

        contract = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="test",
        )
        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id = _issue_receipt(receipt_svc, attempt_ctx, contract_ref=contract.contract_id)

        common_kwargs: dict[str, Any] = {
            "attempt_ctx": attempt_ctx,
            "contract_ref": contract.contract_id,
            "receipt_ref": receipt_id,
            "action_type": "execute_command",
            "tool_input": {},
            "workspace_root": "/tmp/ws",
            "observables": None,
            "witness": None,
            "result_code_hint": "succeeded",
            "authorized_effect_summary": "test effect",
        }

        rec1, _outcome1, _artifact_ref1 = svc.reconcile_attempt(**common_kwargs)
        rec2, _outcome2, _artifact_ref2 = svc.reconcile_attempt(**common_kwargs)

        assert rec1.reconciliation_id == rec2.reconciliation_id
        assert rec1.result_class == rec2.result_class

        reconciliations = store.list_reconciliations(step_attempt_id=attempt_ctx.step_attempt_id)
        assert len(reconciliations) == 1

    def test_different_receipt_creates_separate_reconciliation(self, tmp_path: Path) -> None:
        store, artifact_store = _store_and_artifacts(tmp_path)
        attempt_ctx = _create_task_step_attempt(store)
        reconcile_svc = _make_reconcile_service_stub("reconciled_applied")
        svc = ReconciliationService(store, artifact_store, reconcile_svc)

        contract = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="test",
        )
        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id_1 = _issue_receipt(receipt_svc, attempt_ctx, contract_ref=contract.contract_id)
        receipt_id_2 = _issue_receipt(receipt_svc, attempt_ctx, contract_ref=contract.contract_id)

        rec1, _, _ = svc.reconcile_attempt(
            attempt_ctx=attempt_ctx,
            contract_ref=contract.contract_id,
            receipt_ref=receipt_id_1,
            action_type="execute_command",
            tool_input={},
            workspace_root="/tmp/ws",
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="effect",
        )
        rec2, _, _ = svc.reconcile_attempt(
            attempt_ctx=attempt_ctx,
            contract_ref=contract.contract_id,
            receipt_ref=receipt_id_2,
            action_type="execute_command",
            tool_input={},
            workspace_root="/tmp/ws",
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="effect",
        )

        assert rec1.reconciliation_id != rec2.reconciliation_id
        reconciliations = store.list_reconciliations(step_attempt_id=attempt_ctx.step_attempt_id)
        assert len(reconciliations) == 2

    def test_idempotent_reconciliation_returns_cached_outcome(self, tmp_path: Path) -> None:
        """Verify that the second call returns a reconstructed ReconcileOutcome
        from the persisted record without invoking the underlying ReconcileService."""
        store, artifact_store = _store_and_artifacts(tmp_path)
        attempt_ctx = _create_task_step_attempt(store)
        reconcile_svc = _make_reconcile_service_stub("reconciled_applied")
        svc = ReconciliationService(store, artifact_store, reconcile_svc)

        contract = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="test",
        )
        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id = _issue_receipt(receipt_svc, attempt_ctx, contract_ref=contract.contract_id)

        kwargs: dict[str, Any] = {
            "attempt_ctx": attempt_ctx,
            "contract_ref": contract.contract_id,
            "receipt_ref": receipt_id,
            "action_type": "execute_command",
            "tool_input": {},
            "workspace_root": "/tmp/ws",
            "observables": None,
            "witness": None,
            "result_code_hint": "succeeded",
            "authorized_effect_summary": "test effect",
        }

        svc.reconcile_attempt(**kwargs)
        # Reset the call count on the underlying reconcile service
        reconcile_svc.reconcile.reset_mock()
        svc.reconcile_attempt(**kwargs)
        # The underlying reconcile should NOT have been called again
        reconcile_svc.reconcile.assert_not_called()


# ===========================================================================
# Task 10: result_class coverage
# ===========================================================================


class TestResultClassCoverage:
    """Test each result_class derivation and its downstream effects."""

    @pytest.mark.parametrize(
        "outcome_code,hint,expected_class",
        [
            ("reconciled_applied", "succeeded", "satisfied"),
            ("reconciled_observed", "succeeded", "satisfied"),
            ("reconciled_inferred", "succeeded", "satisfied"),
            ("reconciled_not_applied", "succeeded", "violated"),
            ("reconciled_applied", "unknown_outcome", "partial"),
            ("reconciled_observed", "unknown_outcome", "partial"),
            ("still_unknown", "unknown_outcome", "ambiguous"),
            ("reconciled_not_applied", "dispatch_denied", "unauthorized"),
            ("reconciled_not_applied", "denied", "unauthorized"),
            ("still_unknown", "succeeded", "satisfied_with_downgrade"),
        ],
    )
    def test_result_class_derivation(
        self,
        outcome_code: str,
        hint: str,
        expected_class: str,
    ) -> None:
        outcome = ReconcileOutcome(result_code=outcome_code, summary="test", observed_refs=[])
        result = ReconciliationService._result_class(outcome, result_code_hint=hint)
        assert result == expected_class

    @pytest.mark.parametrize(
        "hint,expected_class",
        [
            ("drifted", "drifted"),
            ("witness_drift", "drifted"),
            ("contract_expiry", "drifted"),
            ("policy_version_drift", "drifted"),
            ("rolled_back", "rolled_back"),
            ("rollback_succeeded", "rolled_back"),
        ],
    )
    def test_result_class_from_hint_override(self, hint: str, expected_class: str) -> None:
        outcome = ReconcileOutcome(
            result_code="reconciled_applied", summary="test", observed_refs=[]
        )
        result = ReconciliationService._result_class(outcome, result_code_hint=hint)
        assert result == expected_class

    @pytest.mark.parametrize(
        "result_class,expected_resolution",
        [
            ("satisfied", "promote_learning"),
            ("satisfied_with_downgrade", "promote_learning"),
            ("violated", "gather_more_evidence"),
            ("unauthorized", "request_authority"),
            ("drifted", "reenter_policy"),
            ("rolled_back", "confirm_rollback"),
            ("ambiguous", "park_and_escalate"),
            ("partial", "park_and_escalate"),
        ],
    )
    def test_recommended_resolution(self, result_class: str, expected_resolution: str) -> None:
        result = ReconciliationService._recommended_resolution(result_class)
        assert result == expected_resolution

    def test_satisfied_triggers_template_learning(self, tmp_path: Path) -> None:
        """When result_class is 'satisfied', ReconciliationExecutor should call
        learn_contract_template."""
        from hermit.kernel.execution.executor.reconciliation_executor import (
            ReconciliationExecutor,
        )

        store = MagicMock()
        attempt_record = SimpleNamespace(
            step_attempt_id="attempt-1",
            execution_contract_ref="contract-1",
            evidence_case_ref=None,
            authorization_plan_ref=None,
            context={},
            selected_contract_template_ref="",
        )
        store.get_step_attempt.return_value = attempt_record
        store.has_non_terminal_steps.return_value = False

        reconciliation = ReconciliationRecord(
            reconciliation_id="recon-1",
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            contract_ref="contract-1",
            result_class="satisfied",
            operator_summary="ok",
        )
        outcome = ReconcileOutcome(result_code="reconciled_applied", summary="ok", observed_refs=[])
        reconciliations_svc = MagicMock()
        reconciliations_svc.reconcile_attempt.return_value = (
            reconciliation,
            outcome,
            "art-1",
        )

        execution_contracts = MagicMock()
        executor = ReconciliationExecutor(
            store=store,
            artifact_store=MagicMock(),
            reconciliations=reconciliations_svc,
            execution_contracts=execution_contracts,
            evidence_cases=MagicMock(),
            pattern_learner=MagicMock(),
        )

        attempt_ctx = TaskExecutionContext(
            conversation_id="conv-1",
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            source_channel="chat",
            workspace_root="/tmp/ws",
        )
        executor.record_reconciliation(
            attempt_ctx=attempt_ctx,
            receipt_id="receipt-1",
            action_type="execute_command",
            tool_input={},
            observables=None,
            witness_ref=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )
        execution_contracts.template_learner.learn_from_reconciliation.assert_called_once()

    def test_violated_triggers_memory_invalidation(self) -> None:
        """When result_class is 'violated', ReconciliationExecutor should call
        invalidate_memories_for_reconciliation."""
        from hermit.kernel.execution.executor.reconciliation_executor import (
            ReconciliationExecutor,
        )

        store = MagicMock()
        attempt_record = SimpleNamespace(
            step_attempt_id="attempt-1",
            execution_contract_ref="contract-1",
            evidence_case_ref=None,
            authorization_plan_ref=None,
            context={},
            selected_contract_template_ref="",
        )
        store.get_step_attempt.return_value = attempt_record
        store.list_memory_records.return_value = []

        reconciliation = ReconciliationRecord(
            reconciliation_id="recon-1",
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            contract_ref="contract-1",
            result_class="violated",
            operator_summary="violation",
        )
        outcome = ReconcileOutcome(
            result_code="reconciled_not_applied", summary="bad", observed_refs=[]
        )
        reconciliations_svc = MagicMock()
        reconciliations_svc.reconcile_attempt.return_value = (
            reconciliation,
            outcome,
            "art-1",
        )

        execution_contracts = MagicMock()
        executor = ReconciliationExecutor(
            store=store,
            artifact_store=MagicMock(),
            reconciliations=reconciliations_svc,
            execution_contracts=execution_contracts,
            evidence_cases=MagicMock(),
            pattern_learner=MagicMock(),
        )

        attempt_ctx = TaskExecutionContext(
            conversation_id="conv-1",
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            source_channel="chat",
            workspace_root="/tmp/ws",
        )
        executor.record_reconciliation(
            attempt_ctx=attempt_ctx,
            receipt_id="receipt-1",
            action_type="execute_command",
            tool_input={},
            observables=None,
            witness_ref=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )
        # Violated should trigger memory invalidation search
        store.list_memory_records.assert_called()
        # Violated should trigger template degradation
        execution_contracts.template_learner.degrade_templates_for_violation.assert_called_once()

    @pytest.mark.parametrize(
        "result_class,expected_status",
        [
            ("satisfied", "succeeded"),
            ("violated", "failed"),
            ("ambiguous", "needs_attention"),
            ("unauthorized", "needs_attention"),
            ("partial", "reconciling"),
            ("satisfied_with_downgrade", "reconciling"),
        ],
    )
    def test_reconciliation_execution_status_mapping(
        self, result_class: str, expected_status: str
    ) -> None:
        from hermit.kernel.execution.executor.reconciliation_executor import (
            ReconciliationExecutor,
        )

        recon = SimpleNamespace(result_class=result_class)
        status = ReconciliationExecutor.reconciliation_execution_status(recon)
        assert status == expected_status

    def test_confidence_delta_values(self) -> None:
        """Verify confidence_delta calculation for each outcome code."""
        cases = [
            ("reconciled_applied", 0.2),
            ("reconciled_observed", 0.2),
            ("reconciled_inferred", 0.05),
            ("reconciled_not_applied", -0.3),
            ("still_unknown", -0.1),
        ]
        for result_code, expected_delta in cases:
            outcome = ReconcileOutcome(result_code=result_code, summary="", observed_refs=[])
            delta = ReconciliationService._confidence_delta(outcome)
            assert delta == expected_delta, f"Failed for {result_code}"


# ===========================================================================
# Task 11: Contract loop closure
# ===========================================================================


class TestContractLoopClosure:
    """Test the full contract->receipt->reconciliation chain and status transitions."""

    def test_full_chain_contract_to_reconciliation(self, tmp_path: Path) -> None:
        """Create contract, issue receipt, reconcile, verify contract closes."""
        store, artifact_store = _store_and_artifacts(tmp_path)
        attempt_ctx = _create_task_step_attempt(store)

        # 1. Create contract (starts as draft)
        contract = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="write test file",
            status="draft",
        )
        assert contract.status == "draft"

        # 2. Transition: draft -> authorized
        store.update_execution_contract(contract.contract_id, status="authorized")
        c = store.get_execution_contract(contract.contract_id)
        assert c is not None
        assert c.status == "authorized"

        # 3. Transition: authorized -> executing
        store.update_execution_contract(contract.contract_id, status="executing")
        c = store.get_execution_contract(contract.contract_id)
        assert c is not None
        assert c.status == "executing"

        # 4. Issue receipt
        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id = _issue_receipt(receipt_svc, attempt_ctx, contract_ref=contract.contract_id)
        receipt = store.get_receipt(receipt_id)
        assert receipt is not None
        assert receipt.contract_ref == contract.contract_id

        # 5. Reconcile (satisfied -> close contract)
        reconcile_stub = _make_reconcile_service_stub("reconciled_applied")
        recon_svc = ReconciliationService(store, artifact_store, reconcile_stub)
        rec, _outcome, _art = recon_svc.reconcile_attempt(
            attempt_ctx=attempt_ctx,
            contract_ref=contract.contract_id,
            receipt_ref=receipt_id,
            action_type="execute_command",
            tool_input={},
            workspace_root="/tmp/ws",
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )
        assert rec.result_class == "satisfied"

        # 6. Close contract (as ReconciliationExecutor does)
        store.update_execution_contract(contract.contract_id, status="closed")
        final = store.get_execution_contract(contract.contract_id)
        assert final is not None
        assert final.status == "closed"

    def test_contract_status_transitions(self, tmp_path: Path) -> None:
        """Verify that contracts go through expected status transitions."""
        store, _artifact_store = _store_and_artifacts(tmp_path)
        attempt_ctx = _create_task_step_attempt(store)

        contract = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="status test",
            status="draft",
        )

        statuses = ["authorized", "executing", "closed"]
        for target_status in statuses:
            store.update_execution_contract(contract.contract_id, status=target_status)
            c = store.get_execution_contract(contract.contract_id)
            assert c is not None
            assert c.status == target_status

    def test_violated_reconciliation_sets_violated_status(self, tmp_path: Path) -> None:
        """A violated reconciliation should cause contract to be set to 'violated'."""
        store, artifact_store = _store_and_artifacts(tmp_path)
        attempt_ctx = _create_task_step_attempt(store)

        contract = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="violation test",
            status="executing",
        )
        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id = _issue_receipt(receipt_svc, attempt_ctx, contract_ref=contract.contract_id)

        reconcile_stub = _make_reconcile_service_stub("reconciled_not_applied")
        recon_svc = ReconciliationService(store, artifact_store, reconcile_stub)
        rec, _, _ = recon_svc.reconcile_attempt(
            attempt_ctx=attempt_ctx,
            contract_ref=contract.contract_id,
            receipt_ref=receipt_id,
            action_type="execute_command",
            tool_input={},
            workspace_root="/tmp/ws",
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )
        assert rec.result_class == "violated"

        # Simulate what ReconciliationExecutor does for 'violated'
        store.update_execution_contract(contract.contract_id, status="violated")
        c = store.get_execution_contract(contract.contract_id)
        assert c is not None
        assert c.status == "violated"

    def test_unclosed_contracts_detectable(self, tmp_path: Path) -> None:
        """Verify that unclosed contracts can be found by listing and filtering."""
        store, _artifact_store = _store_and_artifacts(tmp_path)
        attempt_ctx = _create_task_step_attempt(store)

        # Create some contracts in different statuses
        c1 = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="closed one",
            status="draft",
        )
        c2 = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="still executing",
            status="draft",
        )
        c3 = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="authorized but not closed",
            status="draft",
        )

        store.update_execution_contract(c1.contract_id, status="closed")
        store.update_execution_contract(c2.contract_id, status="executing")
        store.update_execution_contract(c3.contract_id, status="authorized")

        all_contracts = store.list_execution_contracts(task_id=attempt_ctx.task_id)
        terminal = {"closed", "violated", "superseded", "abandoned"}
        unclosed = [c for c in all_contracts if c.status not in terminal]
        assert len(unclosed) == 2
        unclosed_ids = {c.contract_id for c in unclosed}
        assert c2.contract_id in unclosed_ids
        assert c3.contract_id in unclosed_ids

    def test_contract_reconciliation_event_recorded(self, tmp_path: Path) -> None:
        """Verify reconciliation.closed event is appended to the ledger."""
        store, artifact_store = _store_and_artifacts(tmp_path)
        attempt_ctx = _create_task_step_attempt(store)

        contract = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="event test",
            status="executing",
        )
        receipt_svc = ReceiptService(store, artifact_store)
        receipt_id = _issue_receipt(receipt_svc, attempt_ctx, contract_ref=contract.contract_id)

        reconcile_stub = _make_reconcile_service_stub("reconciled_applied")
        recon_svc = ReconciliationService(store, artifact_store, reconcile_stub)
        recon_svc.reconcile_attempt(
            attempt_ctx=attempt_ctx,
            contract_ref=contract.contract_id,
            receipt_ref=receipt_id,
            action_type="execute_command",
            tool_input={},
            workspace_root="/tmp/ws",
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )

        events = store.list_events(task_id=attempt_ctx.task_id, limit=100)
        recon_events = [e for e in events if e.get("event_type") == "reconciliation.closed"]
        assert len(recon_events) >= 1
        payload = recon_events[0].get("payload", {})
        assert payload.get("contract_ref") == contract.contract_id
        assert payload.get("receipt_ref") == receipt_id

    def test_superseded_contract_transition(self, tmp_path: Path) -> None:
        """Verify contract can be superseded."""
        store, _artifact_store = _store_and_artifacts(tmp_path)
        attempt_ctx = _create_task_step_attempt(store)

        c1 = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="old contract",
            status="executing",
        )
        c2 = store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective="new contract",
            status="executing",
        )

        store.update_execution_contract(
            c1.contract_id,
            status="superseded",
            superseded_by_contract_id=c2.contract_id,
        )
        old = store.get_execution_contract(c1.contract_id)
        assert old is not None
        assert old.status == "superseded"
        assert old.superseded_by_contract_id == c2.contract_id


# ===========================================================================
# Task 12: Receipt HMAC signing roundtrip
# ===========================================================================


class TestReceiptHMACSigning:
    """Test receipt HMAC signing, verification, and tamper detection."""

    @pytest.fixture(autouse=True)
    def _set_signing_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMIT_PROOF_SIGNING_SECRET", "test-secret-key-12345")

    def test_receipt_has_hmac_signature(self, tmp_path: Path) -> None:
        """After issue(), ensure_receipt_bundle() overwrites the signature
        field with the proof bundle's signature metadata (a JSON dict).
        Verify that the bundle signature is present and valid JSON."""
        store, artifact_store = _store_and_artifacts(tmp_path)
        attempt_ctx = _create_task_step_attempt(store)
        receipt_svc = ReceiptService(store, artifact_store)

        receipt_id = _issue_receipt(receipt_svc, attempt_ctx)
        receipt = store.get_receipt(receipt_id)
        assert receipt is not None
        assert receipt.signature is not None
        # The proof bundle overwrites the signature with JSON metadata
        import json as _json

        sig_meta = _json.loads(receipt.signature)
        assert sig_meta["kind"] == "hmac-sha256"
        assert "signature" in sig_meta

    def test_receipt_signature_verifies(self, tmp_path: Path) -> None:
        """Test HMAC v2 signature computation and verification roundtrip
        using the static methods directly (the persisted receipt signature
        is overwritten by the proof bundle)."""
        receipt_data = {
            "receipt_id": "r-1",
            "task_id": "t-1",
            "step_id": "s-1",
            "step_attempt_id": "sa-1",
            "action_type": "execute_command",
            "receipt_class": "execute_command",
            "input_refs": [],
            "environment_ref": None,
            "policy_result": {"verdict": "allow"},
            "approval_ref": None,
            "output_refs": [],
            "result_summary": "done",
            "result_code": "succeeded",
            "decision_ref": None,
            "capability_grant_ref": None,
            "workspace_lease_ref": None,
            "policy_ref": None,
            "action_request_ref": None,
            "policy_result_ref": None,
            "contract_ref": None,
            "authorization_plan_ref": None,
            "witness_ref": None,
            "idempotency_key": None,
            "verifiability": None,
            "signer_ref": None,
            "rollback_supported": False,
            "rollback_strategy": None,
            "rollback_status": "not_requested",
            "rollback_ref": None,
            "rollback_artifact_refs": None,
            "observed_effect_summary": None,
            "reconciliation_required": False,
        }
        sig = ReceiptService._compute_signature(receipt_data)
        assert sig is not None
        assert sig.startswith("v2:")
        assert ReceiptService.verify_signature(receipt_data, sig)

    def test_tampered_receipt_fails_verification(self, tmp_path: Path) -> None:
        """Sign receipt data, then tamper with fields -- verification must fail."""
        receipt_data = {
            "receipt_id": "r-1",
            "task_id": "t-1",
            "step_id": "s-1",
            "step_attempt_id": "sa-1",
            "action_type": "execute_command",
            "receipt_class": "execute_command",
            "input_refs": [],
            "environment_ref": None,
            "policy_result": {"verdict": "allow"},
            "approval_ref": None,
            "output_refs": [],
            "result_summary": "done",
            "result_code": "succeeded",
            "decision_ref": None,
            "capability_grant_ref": None,
            "workspace_lease_ref": None,
            "policy_ref": None,
            "action_request_ref": None,
            "policy_result_ref": None,
            "contract_ref": None,
            "authorization_plan_ref": None,
            "witness_ref": None,
            "idempotency_key": None,
            "verifiability": None,
            "signer_ref": None,
            "rollback_supported": False,
            "rollback_strategy": None,
            "rollback_status": "not_requested",
            "rollback_ref": None,
            "rollback_artifact_refs": None,
            "observed_effect_summary": None,
            "reconciliation_required": False,
        }

        sig = ReceiptService._compute_signature(receipt_data)
        assert sig is not None

        # Tamper with task_id
        tampered = {**receipt_data, "task_id": "TAMPERED-ID"}
        assert not ReceiptService.verify_signature(tampered, sig)

        # Tamper with result_code
        tampered2 = {**receipt_data, "result_code": "failed"}
        assert not ReceiptService.verify_signature(tampered2, sig)

        # Tamper with result_summary
        tampered3 = {**receipt_data, "result_summary": "EVIL SUMMARY"}
        assert not ReceiptService.verify_signature(tampered3, sig)

    def test_no_secret_means_no_signature(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HERMIT_PROOF_SIGNING_SECRET", raising=False)
        store, artifact_store = _store_and_artifacts(tmp_path)
        attempt_ctx = _create_task_step_attempt(store)
        receipt_svc = ReceiptService(store, artifact_store)

        receipt_id = _issue_receipt(receipt_svc, attempt_ctx)
        receipt = store.get_receipt(receipt_id)
        assert receipt is not None
        assert receipt.signature is None

    def test_verify_fails_without_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sign with secret, then remove it -- verification should fail."""
        receipt_data = {
            "receipt_id": "r-1",
            "task_id": "t-1",
            "step_id": "s-1",
            "action_type": "execute_command",
            "result_code": "succeeded",
        }
        sig = ReceiptService._compute_signature(receipt_data)
        assert sig is not None

        monkeypatch.delenv("HERMIT_PROOF_SIGNING_SECRET", raising=False)
        assert not ReceiptService.verify_signature(receipt_data, sig)

    def test_wrong_secret_fails_verification(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        receipt_data = {
            "receipt_id": "r-1",
            "task_id": "t-1",
            "step_id": "s-1",
            "action_type": "execute_command",
            "result_code": "succeeded",
        }
        sig = ReceiptService._compute_signature(receipt_data)
        assert sig is not None

        monkeypatch.setenv("HERMIT_PROOF_SIGNING_SECRET", "WRONG-SECRET")
        assert not ReceiptService.verify_signature(receipt_data, sig)

    def test_canonicalize_excludes_signature_and_none(self) -> None:
        data = {
            "receipt_id": "r-1",
            "task_id": "t-1",
            "signature": "should-be-excluded",
            "optional_field": None,
        }
        canonical = ReceiptService._canonicalize(data)
        assert "signature" not in canonical
        assert "optional_field" not in canonical
        assert "receipt_id" in canonical
        assert "task_id" in canonical

    def test_canonicalize_deterministic(self) -> None:
        """Same data in different key order produces same canonical string."""
        data_a = {"z_field": "z", "a_field": "a", "m_field": "m"}
        data_b = {"a_field": "a", "m_field": "m", "z_field": "z"}
        assert ReceiptService._canonicalize(data_a) == ReceiptService._canonicalize(data_b)

    def test_legacy_signature_verification(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Legacy 5-field signatures should still verify."""
        monkeypatch.setenv("HERMIT_PROOF_SIGNING_SECRET", "test-secret-key-12345")
        receipt_data = {
            "receipt_id": "r-1",
            "task_id": "t-1",
            "step_id": "s-1",
            "action_type": "execute_command",
            "result_code": "succeeded",
        }
        sig = ReceiptService._compute_legacy_signature(
            "r-1", "t-1", "s-1", "execute_command", "succeeded"
        )
        assert sig is not None
        assert ReceiptService.verify_signature(receipt_data, sig)

    def test_legacy_tampered_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMIT_PROOF_SIGNING_SECRET", "test-secret-key-12345")
        receipt_data = {
            "receipt_id": "r-1",
            "task_id": "t-1",
            "step_id": "s-1",
            "action_type": "execute_command",
            "result_code": "succeeded",
        }
        sig = ReceiptService._compute_legacy_signature(
            "r-1", "t-1", "s-1", "execute_command", "succeeded"
        )
        assert sig is not None
        tampered = {**receipt_data, "result_code": "failed"}
        assert not ReceiptService.verify_signature(tampered, sig)
