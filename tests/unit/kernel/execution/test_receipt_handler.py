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


def _make_handler(tmp_path: Path) -> tuple[ReceiptHandler, KernelStore, ArtifactStore]:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifact_store = ArtifactStore(tmp_path / "kernel" / "artifacts")
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


def _start_task(store: KernelStore, tmp_path: Path, goal: str = "test") -> TaskExecutionContext:
    controller = TaskController(store)
    return controller.start_task(
        conversation_id="chat-receipt-test",
        goal=goal,
        source_channel="chat",
        kind="respond",
        workspace_root=str(tmp_path),
    )


class TestReceiptIssuance:
    """Test basic receipt issuance through ReceiptHandler."""

    def test_issue_receipt_returns_receipt_id(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/test.txt", "content": "hello"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
            policy_ref="pol-1",
            decision_ref="dec-1",
            capability_grant_ref="grant-1",
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key="idem-1",
        )

        assert receipt_id is not None
        assert isinstance(receipt_id, str)

    def test_issue_receipt_creates_input_and_output_artifacts(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/a.txt"},
            raw_result={"status": "written"},
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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

    def test_issue_receipt_with_custom_result_summary(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/b.txt"},
            raw_result="done",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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

    def test_issue_receipt_default_summary_uses_tool_name(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool(name="bash")
        policy = _make_policy()

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="bash",
            tool_input={"command": "ls"},
            raw_result="file.txt",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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
    """Test environment_ref resolution paths."""

    def test_explicit_environment_ref_used_directly(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/c.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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

    def test_no_environment_ref_creates_snapshot(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/d.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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
        # Should have created an environment.snapshot artifact
        artifacts = store.list_artifacts(task_id=ctx.task_id)
        env_artifacts = [a for a in artifacts if a.kind == "environment.snapshot"]
        assert len(env_artifacts) == 1

    def test_workspace_lease_environment_ref_used(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

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
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/e.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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
    """Test contract_ref and authorization_plan_ref fallback to step attempt."""

    def test_contract_ref_from_step_attempt(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

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
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/f.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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

    def test_explicit_contract_ref_overrides_step_attempt(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        store.update_step_attempt(
            ctx.step_attempt_id,
            execution_contract_ref="contract-from-attempt",
        )

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/g.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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

    def test_authorization_plan_ref_from_step_attempt(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        store.update_step_attempt(
            ctx.step_attempt_id,
            authorization_plan_ref="auth-plan-from-attempt",
        )

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/h.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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
    """Test verifiability field based on policy requires_receipt."""

    def test_verifiability_baseline_when_receipt_required(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy(require_receipt=True)

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/i.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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

    def test_verifiability_overridden_by_proof_service(self, tmp_path: Path) -> None:
        """Even when policy does not require receipt, ensure_receipt_bundle
        upgrades verifiability to baseline_verifiable after bundling."""
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy(require_receipt=False)

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/j.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
            policy_ref=None,
            decision_ref=None,
            capability_grant_ref=None,
            workspace_lease_ref=None,
            witness_ref=None,
            result_code="succeeded",
            idempotency_key=None,
        )

        receipt = store.get_receipt(receipt_id)
        # ProofService.ensure_receipt_bundle overrides to baseline_verifiable
        assert receipt.verifiability == "baseline_verifiable"


class TestRollbackFields:
    """Test rollback-related fields are passed through to receipt."""

    def test_rollback_supported_and_strategy(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/k.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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
    """Test custom output_kind for output artifact."""

    def test_custom_output_kind(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/l.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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
    """Test that issue_receipt updates the step attempt with environment_ref."""

    def test_step_attempt_updated_with_environment_ref(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/m.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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
    """Test policy_result_ref falls back to policy_ref when not provided."""

    def test_policy_result_ref_defaults_to_policy_ref(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/n.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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

    def test_explicit_policy_result_ref_overrides_fallback(self, tmp_path: Path) -> None:
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/o.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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
    """Verify that issue_receipt closes the contract when reconciliation is not required."""

    def test_contract_closed_when_reconciliation_not_required(self, tmp_path: Path) -> None:
        """Contract must transition to 'closed' after receipt issuance with no reconciliation."""
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

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
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/closed.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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

    def test_contract_not_closed_when_reconciliation_required(self, tmp_path: Path) -> None:
        """Contract must NOT be closed when reconciliation is still pending."""
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

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
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/still_open.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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
        # Status should still be "executing"; reconciliation will close it later.
        assert updated.status == "executing"

    def test_no_error_when_no_contract_ref(self, tmp_path: Path) -> None:
        """issue_receipt must succeed gracefully when there is no associated contract."""
        handler, store, _ = _make_handler(tmp_path)
        ctx = _start_task(store, tmp_path)
        tool = _make_tool()
        policy = _make_policy()

        # No contract attached to the attempt
        receipt_id = handler.issue_receipt(
            tool=tool,
            tool_name="write_file",
            tool_input={"path": "/tmp/no_contract.txt"},
            raw_result="ok",
            attempt_ctx=ctx,
            approval_ref=None,
            policy=policy,
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
