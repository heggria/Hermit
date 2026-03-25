"""Unit tests for ReceiptHandler receipt issuance logic."""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.receipt_handler import ReceiptHandler
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.models.models import PolicyDecision, PolicyObligations
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.receipts.receipts import ReceiptService
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec


def _make_tool(
    name: str = "write_file",
    action_class: str = "write_local",
    risk_hint: str = "medium",
    requires_receipt: bool = True,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="test tool",
        input_schema={"type": "object"},
        handler=lambda _: "ok",
        action_class=action_class,
        risk_hint=risk_hint,
        requires_receipt=requires_receipt,
    )


def _make_policy(
    verdict: str = "allow",
    action_class: str = "write_local",
    require_receipt: bool = True,
) -> PolicyDecision:
    return PolicyDecision(
        verdict=verdict,
        action_class=action_class,
        obligations=PolicyObligations(require_receipt=require_receipt),
    )


def _make_handler(
    store: KernelStore, tmp_path: Path
) -> tuple[ReceiptHandler, KernelStore, ArtifactStore]:
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    receipt_service = ReceiptService(store, artifact_store)
    registry = ToolRegistry()
    from hermit.kernel.policy import PolicyEngine

    policy_engine = PolicyEngine()
    from hermit.kernel.authority.workspaces import WorkspaceLeaseService

    workspace_lease_service = WorkspaceLeaseService(store, artifact_store)

    handler = ReceiptHandler(
        store=store,
        artifact_store=artifact_store,
        receipt_service=receipt_service,
        registry=registry,
        policy_engine=policy_engine,
        workspace_lease_service=workspace_lease_service,
    )
    return handler, store, artifact_store


def _start_task(
    store: KernelStore, conv_id: str, tmp_path: Path, goal: str = "test"
) -> TaskExecutionContext:
    controller = TaskController(store)
    return controller.start_task(
        conversation_id=conv_id,
        goal=goal,
        source_channel="chat",
        kind="respond",
        workspace_root=str(tmp_path),
    )


class TestReceiptIssuance:
    def test_issue_receipt_returns_receipt_id(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/test.txt", "content": "hello"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref="pol-1",
            decision_ref="dec-1",
            capability_grant_ref="grant-1",
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key="idem-1",
        )
        assert receipt_id is not None and isinstance(receipt_id, str)

    def test_issue_receipt_creates_input_and_output_artifacts(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/a.txt"},
            raw_result={"status": "written"},
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )
        artifacts = store.list_artifacts(task_id=ctx.task_id)
        kinds = [a.kind for a in artifacts]
        assert "tool_input" in kinds
        assert "tool_output" in kinds

    def test_issue_receipt_with_custom_result_summary(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/b.txt"},
            raw_result="done",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
            result_summary="custom summary",
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.result_summary == "custom summary"

    def test_issue_receipt_default_summary_uses_tool_name(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(name="bash"),
            tool_name="bash",
            tool_input={"command": "ls"},
            raw_result="file.txt",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )
        receipt = store.get_receipt(receipt_id)
        assert "bash" in receipt.result_summary


class TestEnvironmentRefResolution:
    def test_explicit_environment_ref_used_directly(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/c.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
            environment_ref="env-explicit-123",
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.environment_ref == "env-explicit-123"

    def test_no_environment_ref_creates_snapshot(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/d.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.environment_ref is not None
        artifacts = store.list_artifacts(task_id=ctx.task_id)
        env_artifacts = [a for a in artifacts if a.kind == "environment.snapshot"]
        assert len(env_artifacts) == 1

    def test_workspace_lease_environment_ref_used(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        lease = store.create_workspace_lease(
            task_id=ctx.task_id,
            step_attempt_id=ctx.step_attempt_id,
            workspace_id="ws-1",
            root_path=str(tmp_path),
            holder_principal_id="principal_user",
            mode="none",
            resource_scope=["*"],
            environment_ref="env-from-lease",
            expires_at=None,
        )
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/e.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=lease.lease_id,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.environment_ref == "env-from-lease"


class TestContractAndAuthPlanResolution:
    def test_contract_ref_from_step_attempt(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="write file",
            status="executing",
        )
        store.update_step_attempt(
            ctx.step_attempt_id,
            execution_contract_ref=contract.contract_id,
        )
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/f.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.contract_ref == contract.contract_id

    def test_explicit_contract_ref_overrides_step_attempt(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        store.update_step_attempt(
            ctx.step_attempt_id,
            execution_contract_ref="contract-from-attempt",
        )
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/g.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
            contract_ref="explicit-contract",
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.contract_ref == "explicit-contract"

    def test_authorization_plan_ref_from_step_attempt(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        store.update_step_attempt(
            ctx.step_attempt_id,
            authorization_plan_ref="auth-plan-from-attempt",
        )
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/h.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.authorization_plan_ref == "auth-plan-from-attempt"


class TestVerifiabilityFlag:
    def test_verifiability_baseline_when_receipt_required(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/i.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(require_receipt=True),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.verifiability == "baseline_verifiable"

    def test_verifiability_overridden_by_proof_service(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/j.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(require_receipt=False),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.verifiability == "baseline_verifiable"


class TestRollbackFields:
    def test_rollback_supported_and_strategy(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/k.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
            rollback_supported=True,
            rollback_strategy="restore_backup",
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.rollback_supported is True
        assert receipt.rollback_strategy == "restore_backup"


class TestOutputKind:
    def test_custom_output_kind(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/l.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
            output_kind="custom_output",
        )
        artifacts = store.list_artifacts(task_id=ctx.task_id)
        output_artifacts = [a for a in artifacts if a.kind == "custom_output"]
        assert len(output_artifacts) == 1


class TestStepAttemptEnvironmentUpdate:
    def test_step_attempt_updated_with_environment_ref(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/m.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )
        attempt = store.get_step_attempt(ctx.step_attempt_id)
        assert attempt.environment_ref is not None


class TestPolicyRefFallback:
    def test_policy_result_ref_defaults_to_policy_ref(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/n.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref="pol-ref-123",
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.policy_result_ref == "pol-ref-123"

    def test_explicit_policy_result_ref_overrides_fallback(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/o.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref="pol-ref-456",
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
            policy_result_ref="explicit-result-ref",
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt.policy_result_ref == "explicit-result-ref"


class TestContractTerminalState:
    def test_contract_closed_when_reconciliation_not_required(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="write file",
            status="executing",
        )
        store.update_step_attempt(
            ctx.step_attempt_id,
            execution_contract_ref=contract.contract_id,
        )
        handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/closed.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
            reconciliation_required=False,
        )
        updated = store.get_execution_contract(contract.contract_id)
        assert updated is not None
        assert updated.status == "closed"

    def test_contract_not_closed_when_reconciliation_required(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="write file",
            status="executing",
        )
        store.update_step_attempt(
            ctx.step_attempt_id,
            execution_contract_ref=contract.contract_id,
        )
        handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/still_open.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
            reconciliation_required=True,
        )
        updated = store.get_execution_contract(contract.contract_id)
        assert updated is not None
        assert updated.status == "executing"

    def test_no_error_when_no_contract_ref(
        self, shared_store: KernelStore, conv_id: str, tmp_path: Path
    ) -> None:
        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/no_contract.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
            reconciliation_required=False,
        )
        assert receipt_id is not None


class TestReceiptSignatureAtomicity:
    def test_signature_present_after_full_issue(
        self,
        shared_store: KernelStore,
        conv_id: str,
        tmp_path: Path,
        monkeypatch: object,
    ) -> None:
        import os

        monkeypatch.setattr(
            os,
            "environ",
            {**os.environ, "HERMIT_PROOF_SIGNING_SECRET": "test-secret-key"},
        )  # type: ignore[attr-defined]

        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/sig_test.txt", "content": "hello"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref="pol-sig",
            decision_ref="dec-sig",
            capability_grant_ref="grant-sig",
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key="idem-sig",
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt is not None
        assert receipt.signature is not None

    def test_no_signature_when_no_secret(
        self,
        shared_store: KernelStore,
        conv_id: str,
        tmp_path: Path,
        monkeypatch: object,
    ) -> None:
        import os

        env_copy = {k: v for k, v in os.environ.items() if k != "HERMIT_PROOF_SIGNING_SECRET"}
        monkeypatch.setattr(os, "environ", env_copy)  # type: ignore[attr-defined]

        handler, store, _ = _make_handler(shared_store, tmp_path)
        ctx = _start_task(store, conv_id, tmp_path)
        receipt_id = handler.issue_receipt(
            tool=_make_tool(),
            tool_name="write_file",
            tool_input={"path": "/tmp/nosig_test.txt", "content": "hello"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=_make_policy(),
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )
        receipt = store.get_receipt(receipt_id)
        assert receipt is not None
        assert receipt.signature is None

    def test_atomic_receipt_has_hmac_before_proof_bundle(self, tmp_path: Path) -> None:
        """The receipt must have the HMAC from the first transaction."""
        import os

        os.environ["HERMIT_PROOF_SIGNING_SECRET"] = "atomic-test-secret"
        try:
            store = KernelStore(tmp_path / "atomic" / "state.db")
            ArtifactStore(tmp_path / "atomic" / "artifacts")
            store.ensure_conversation("chat-atomic", source_channel="chat")
            controller = TaskController(store)
            ctx = controller.start_task(
                conversation_id="chat-atomic",
                goal="test atomicity",
                source_channel="chat",
                kind="respond",
                workspace_root=str(tmp_path),
            )
            receipt_id = store.generate_id("receipt")
            receipt_data = {
                "receipt_id": receipt_id,
                "task_id": ctx.task_id,
                "step_id": ctx.step_id,
                "step_attempt_id": ctx.step_attempt_id,
                "action_type": "write_local",
                "input_refs": [],
                "policy_result": {"verdict": "allow"},
                "output_refs": [],
                "result_summary": "atomic test",
                "result_code": "succeeded",
                "rollback_supported": False,
                "rollback_status": "not_requested",
                "reconciliation_required": False,
            }
            hmac_sig = ReceiptService._compute_signature(receipt_data)
            assert hmac_sig is not None
            store.create_receipt(
                receipt_id=receipt_id,
                task_id=ctx.task_id,
                step_id=ctx.step_id,
                step_attempt_id=ctx.step_attempt_id,
                action_type="write_local",
                input_refs=[],
                environment_ref=None,
                policy_result={"verdict": "allow"},
                approval_ref=None,
                output_refs=[],
                result_summary="atomic test",
                result_code="succeeded",
                signature=hmac_sig,
            )
            receipt = store.get_receipt(receipt_id)
            assert receipt is not None
            assert receipt.signature == hmac_sig
            # v2 signatures are prefixed with "v2:" followed by 64 hex chars
            assert receipt.signature.startswith("v2:")
            assert len(receipt.signature) == 3 + 64
        finally:
            del os.environ["HERMIT_PROOF_SIGNING_SECRET"]

    def test_signature_matches_receipt_id(self, tmp_path: Path) -> None:
        """The pre-computed v2 HMAC signature must correspond to the stored receipt."""
        import os

        secret = "verify-me-secret"
        os.environ["HERMIT_PROOF_SIGNING_SECRET"] = secret
        try:
            store = KernelStore(tmp_path / "verify" / "state.db")
            ArtifactStore(tmp_path / "verify" / "artifacts")
            store.ensure_conversation("chat-verify", source_channel="chat")
            controller = TaskController(store)
            ctx = controller.start_task(
                conversation_id="chat-verify",
                goal="verify sig",
                source_channel="chat",
                kind="respond",
                workspace_root=str(tmp_path),
            )
            receipt_id = store.generate_id("receipt")
            receipt_data = {
                "receipt_id": receipt_id,
                "task_id": ctx.task_id,
                "step_id": ctx.step_id,
                "step_attempt_id": ctx.step_attempt_id,
                "action_type": "write_local",
                "input_refs": [],
                "policy_result": {"verdict": "allow"},
                "output_refs": [],
                "result_summary": "verify test",
                "result_code": "succeeded",
                "rollback_supported": False,
                "rollback_status": "not_requested",
                "reconciliation_required": False,
            }
            computed_sig = ReceiptService._compute_signature(receipt_data)
            store.create_receipt(
                receipt_id=receipt_id,
                task_id=ctx.task_id,
                step_id=ctx.step_id,
                step_attempt_id=ctx.step_attempt_id,
                action_type="write_local",
                input_refs=[],
                environment_ref=None,
                policy_result={"verdict": "allow"},
                approval_ref=None,
                output_refs=[],
                result_summary="verify test",
                result_code="succeeded",
                signature=computed_sig,
            )
            receipt = store.get_receipt(receipt_id)
            # Verify using the new verify_signature API
            assert ReceiptService.verify_signature(receipt_data, receipt.signature)
        finally:
            del os.environ["HERMIT_PROOF_SIGNING_SECRET"]

    def test_legacy_signature_backward_compatible(self, tmp_path: Path) -> None:
        """Legacy 5-field signatures must still verify via verify_signature."""
        import os

        secret = "legacy-compat-secret"
        os.environ["HERMIT_PROOF_SIGNING_SECRET"] = secret
        try:
            legacy_sig = ReceiptService._compute_legacy_signature(
                "r-1", "t-1", "s-1", "write_local", "succeeded"
            )
            assert legacy_sig is not None
            receipt_data = {
                "receipt_id": "r-1",
                "task_id": "t-1",
                "step_id": "s-1",
                "action_type": "write_local",
                "result_code": "succeeded",
                "approval_ref": "approval-1",
            }
            assert ReceiptService.verify_signature(receipt_data, legacy_sig)
        finally:
            del os.environ["HERMIT_PROOF_SIGNING_SECRET"]
