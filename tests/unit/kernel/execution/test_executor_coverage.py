"""Additional coverage tests for hermit.kernel.execution.executor.executor.

Focuses on ToolExecutionResult, ToolExecutor delegation methods, and
the execute() orchestration logic that is not covered by other test files.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.executor import ToolExecutionResult, ToolExecutor
from hermit.kernel.policy.models.models import (
    ActionRequest,
    PolicyDecision,
    PolicyObligations,
)
from hermit.runtime.capability.registry.tools import ToolSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attempt_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults: dict[str, Any] = {
        "conversation_id": "conv-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "source_channel": "cli",
    }
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


def _make_tool(
    *,
    name: str = "test_tool",
    readonly: bool = False,
    action_class: str = "write_local",
    handler: Any = None,
) -> ToolSpec:
    kwargs: dict[str, Any] = {
        "name": name,
        "description": "test",
        "input_schema": {},
        "handler": handler or (lambda _: "result"),
        "readonly": readonly,
        "action_class": action_class,
    }
    if readonly:
        kwargs["requires_receipt"] = False
    else:
        kwargs["risk_hint"] = "low"
        kwargs["requires_receipt"] = True
    return ToolSpec(**kwargs)


def _make_policy(
    *,
    verdict: str = "allow",
    action_class: str = "write_local",
    require_receipt: bool = False,
    require_approval: bool = False,
    require_preview: bool = False,
) -> PolicyDecision:
    return PolicyDecision(
        verdict=verdict,
        action_class=action_class,
        obligations=PolicyObligations(
            require_receipt=require_receipt,
            require_approval=require_approval,
            require_preview=require_preview,
        ),
    )


def _make_action_request(**overrides: Any) -> ActionRequest:
    defaults: dict[str, Any] = {
        "request_id": "req-1",
        "tool_name": "test_tool",
        "action_class": "write_local",
        "derived": {},
    }
    defaults.update(overrides)
    return ActionRequest(**defaults)


def _make_executor(**overrides: Any) -> ToolExecutor:
    """Create a ToolExecutor with heavily mocked dependencies."""
    registry = MagicMock()
    store = MagicMock()
    artifact_store = MagicMock()
    policy_engine = MagicMock()
    approval_service = MagicMock()
    receipt_service = MagicMock()

    # Common store returns
    store.get_step_attempt.return_value = SimpleNamespace(
        approval_id=None,
        context={},
        execution_contract_ref=None,
        evidence_case_ref=None,
        authorization_plan_ref=None,
        workspace_lease_id=None,
        policy_version=None,
    )
    store.get_task.return_value = SimpleNamespace(
        task_id="task-1",
        budget_tokens_limit=None,
        budget_tokens_used=0,
        conversation_id="conv-1",
        source_channel="cli",
        policy_profile="default",
        title="Test",
        goal="",
    )

    # Artifact store needs to return tuples for store_json
    artifact_store.store_json.return_value = ("uri://test", "hash123")
    store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")
    store.create_execution_contract.return_value = SimpleNamespace(contract_id="contract-1")
    store.create_evidence_case.return_value = SimpleNamespace(evidence_case_id="ev-1")
    store.create_authorization_plan.return_value = SimpleNamespace(
        authorization_plan_id="ap-1", revalidation_rules=None
    )

    defaults: dict[str, Any] = {
        "registry": registry,
        "store": store,
        "artifact_store": artifact_store,
        "policy_engine": policy_engine,
        "approval_service": approval_service,
        "receipt_service": receipt_service,
    }
    defaults.update(overrides)
    return ToolExecutor(**defaults)


# ---------------------------------------------------------------------------
# ToolExecutionResult
# ---------------------------------------------------------------------------


class TestToolExecutionResult:
    def test_defaults(self) -> None:
        r = ToolExecutionResult(model_content="ok")
        assert r.model_content == "ok"
        assert r.blocked is False
        assert r.suspended is False
        assert r.denied is False
        assert r.result_code == "succeeded"
        assert r.execution_status == "succeeded"
        assert r.state_applied is False
        assert r.approval_id is None
        assert r.observation is None

    def test_all_fields(self) -> None:
        r = ToolExecutionResult(
            model_content="blocked",
            raw_result={"key": "val"},
            blocked=True,
            suspended=True,
            waiting_kind="awaiting_approval",
            denied=True,
            approval_id="apr-1",
            approval_message="Please approve",
            receipt_id="rcp-1",
            decision_id="dec-1",
            capability_grant_id="cap-1",
            workspace_lease_id="lease-1",
            policy_ref="pol-1",
            witness_ref="wit-1",
            result_code="approval_required",
            execution_status="awaiting_approval",
            state_applied=True,
        )
        assert r.blocked is True
        assert r.approval_id == "apr-1"
        assert r.receipt_id == "rcp-1"


# ---------------------------------------------------------------------------
# ToolExecutor delegation tests
# ---------------------------------------------------------------------------


class TestToolExecutorDelegation:
    def test_set_attempt_phase_delegates(self) -> None:
        executor = _make_executor()
        executor._phase = MagicMock()
        ctx = _make_attempt_ctx()
        executor._set_attempt_phase(ctx, "executing", reason="test")
        executor._phase.set_attempt_phase.assert_called_once_with(ctx, "executing", reason="test")

    def test_contract_refs_delegates(self) -> None:
        executor = _make_executor()
        executor._contract = MagicMock()
        executor._contract.contract_refs.return_value = ("c1", "e1", "a1")
        ctx = _make_attempt_ctx()
        result = executor._contract_refs(ctx)
        assert result == ("c1", "e1", "a1")

    def test_contract_expired_static(self) -> None:
        result = ToolExecutor._contract_expired(None)
        assert isinstance(result, bool)

    def test_policy_version_drifted_static(self) -> None:
        attempt = SimpleNamespace(policy_version=None)
        result = ToolExecutor._policy_version_drifted(attempt)
        assert isinstance(result, bool)

    def test_spawn_subtasks_delegates(self) -> None:
        executor = _make_executor()
        executor._subtask = MagicMock()
        mock_result = ToolExecutionResult(model_content="spawned", blocked=True)
        executor._subtask.handle_spawn.return_value = mock_result
        ctx = _make_attempt_ctx()
        result = executor.spawn_subtasks(
            attempt_ctx=ctx,
            descriptors=[
                {"tool_name": "t", "tool_input": {}, "join_strategy": "all_required", "title": "T"}
            ],
        )
        assert result.blocked is True
        executor._subtask.handle_spawn.assert_called_once()


# ---------------------------------------------------------------------------
# ToolExecutor.execute — policy deny path
# ---------------------------------------------------------------------------


class TestToolExecutorExecuteDeny:
    def test_denied_by_policy(self) -> None:
        executor = _make_executor()
        tool = _make_tool(readonly=False)
        policy = _make_policy(verdict="deny", action_class="write_local")
        executor.registry.get.return_value = tool
        executor.policy_engine.build_action_request.return_value = _make_action_request()
        executor.policy_engine.evaluate.return_value = policy
        executor.policy_engine.infer_action_class.return_value = "write_local"

        # Mock all delegate handlers to avoid deep initialization
        executor._request = MagicMock()
        executor._request.record_action_request.return_value = "ar-1"
        executor._request.record_policy_evaluation.return_value = "pr-1"
        executor._request.build_preview_artifact.return_value = None

        executor._approval = MagicMock()
        executor._approval.matching_approval.return_value = (None, None, None)

        executor._evidence_enricher = MagicMock()
        executor._evidence_enricher.enrich.side_effect = lambda x: x

        executor._contract = MagicMock()
        executor._contract.load_contract_bundle.return_value = (None, None, None)
        # synthesize returns mock objects that have required attributes
        mock_contract = SimpleNamespace(contract_id="c-1", expiry_at=None)
        mock_evidence = SimpleNamespace(evidence_case_id="e-1", status="sufficient")
        mock_auth_plan = SimpleNamespace(
            authorization_plan_id="ap-1", revalidation_rules=None, status="authorized"
        )
        executor._contract.synthesize_contract_loop.return_value = (
            mock_contract,
            mock_evidence,
            mock_auth_plan,
        )
        executor._contract.contract_expired.return_value = False
        executor._contract.policy_version_drifted.return_value = False
        executor._contract.admissibility_resolution.return_value = None

        executor.decision_service = MagicMock()
        executor.decision_service.record.return_value = "dec-1"

        ctx = _make_attempt_ctx()
        result = executor.execute(ctx, "test_tool", {"path": "/tmp/test"})
        assert result.denied is True
        assert result.result_code == "denied"
        assert result.execution_status == "failed"


# ---------------------------------------------------------------------------
# ToolExecutor.execute — read-only fast path
# ---------------------------------------------------------------------------


class TestToolExecutorReadOnlyPath:
    def test_readonly_tool_skips_governance(self) -> None:
        executor = _make_executor()
        tool = _make_tool(readonly=True, action_class="read_local", handler=lambda _: "read result")
        policy = _make_policy(verdict="allow", action_class="read_local")
        executor.registry.get.return_value = tool
        executor.policy_engine.build_action_request.return_value = _make_action_request(
            action_class="read_local"
        )
        executor.policy_engine.evaluate.return_value = policy
        executor.policy_engine.infer_action_class.return_value = "read_local"

        executor._request = MagicMock()
        executor._request.record_action_request.return_value = "ar-1"
        executor._request.record_policy_evaluation.return_value = "pr-1"
        executor._request.build_preview_artifact.return_value = None

        executor._approval = MagicMock()
        executor._approval.matching_approval.return_value = (None, None, None)

        executor._evidence_enricher = MagicMock()
        executor._evidence_enricher.enrich.side_effect = lambda x: x

        executor._authorization = MagicMock()
        executor._authorization.prepare_rollback_plan.return_value = {
            "supported": False,
            "strategy": None,
            "artifact_refs": [],
        }

        executor._reconciliation = MagicMock()
        executor._reconciliation.authorized_effect_summary.return_value = ""

        ctx = _make_attempt_ctx()
        result = executor.execute(ctx, "test_tool", {})
        assert result.result_code == "succeeded"
        assert result.blocked is False


# ---------------------------------------------------------------------------
# ToolExecutor.execute — budget tracking
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ToolExecutor.execute — approval required path
# ---------------------------------------------------------------------------


class TestToolExecutorApprovalRequired:
    def test_approval_required_blocks(self) -> None:
        executor = _make_executor()
        tool = _make_tool(readonly=False, action_class="write_local")
        policy = _make_policy(
            verdict="allow",
            action_class="write_local",
            require_approval=True,
        )
        executor.registry.get.return_value = tool
        executor.policy_engine.build_action_request.return_value = _make_action_request()
        executor.policy_engine.evaluate.return_value = policy
        executor.policy_engine.infer_action_class.return_value = "write_local"

        executor._request = MagicMock()
        executor._request.record_action_request.return_value = "ar-1"
        executor._request.record_policy_evaluation.return_value = "pr-1"
        executor._request.build_preview_artifact.return_value = None
        executor._request.requested_action_payload.return_value = {
            "tool_name": "test_tool",
            "action_class": "write_local",
        }

        executor._approval = MagicMock()
        executor._approval.matching_approval.return_value = (None, None, None)

        executor._evidence_enricher = MagicMock()
        executor._evidence_enricher.enrich.side_effect = lambda x: x

        executor._contract = MagicMock()
        executor._contract.load_contract_bundle.return_value = (None, None, None)
        mock_contract = SimpleNamespace(contract_id="c-1", expiry_at=None)
        mock_evidence = SimpleNamespace(evidence_case_id="e-1", status="sufficient")
        mock_auth_plan = SimpleNamespace(
            authorization_plan_id="ap-1", revalidation_rules=None, status="authorized"
        )
        executor._contract.synthesize_contract_loop.return_value = (
            mock_contract,
            mock_evidence,
            mock_auth_plan,
        )
        executor._contract.contract_expired.return_value = False
        executor._contract.policy_version_drifted.return_value = False
        executor._contract.admissibility_resolution.return_value = None

        executor.decision_service = MagicMock()
        executor.decision_service.record.return_value = "dec-1"
        executor.approval_service = MagicMock()
        executor.approval_service.request.return_value = "apr-1"
        executor.approval_copy = MagicMock()
        executor.approval_copy.build_canonical_copy.return_value = {}
        executor.approval_copy.model_prompt.return_value = "Please approve"
        executor.approval_copy.blocked_message.return_value = "Blocked msg"

        ctx = _make_attempt_ctx()
        result = executor.execute(ctx, "test_tool", {"path": "/tmp/test"})
        assert result.blocked is True
        assert result.suspended is True
        assert result.waiting_kind == "awaiting_approval"
        assert result.result_code == "approval_required"
        assert result.approval_id == "apr-1"


# ---------------------------------------------------------------------------
# ToolExecutor.execute — successful governed execution with receipt
# ---------------------------------------------------------------------------


class TestToolExecutorGovernedExecution:
    def test_governed_success_with_receipt(self) -> None:
        executor = _make_executor()
        tool = _make_tool(
            readonly=False,
            action_class="write_local",
            handler=lambda _: {"written": True},
        )
        policy = _make_policy(
            verdict="allow",
            action_class="write_local",
            require_receipt=True,
        )
        executor.registry.get.return_value = tool
        executor.policy_engine.build_action_request.return_value = _make_action_request()
        executor.policy_engine.evaluate.return_value = policy
        executor.policy_engine.infer_action_class.return_value = "write_local"

        executor._request = MagicMock()
        executor._request.record_action_request.return_value = "ar-1"
        executor._request.record_policy_evaluation.return_value = "pr-1"
        executor._request.build_preview_artifact.return_value = None

        executor._approval = MagicMock()
        executor._approval.matching_approval.return_value = (None, None, None)

        executor._evidence_enricher = MagicMock()
        executor._evidence_enricher.enrich.side_effect = lambda x: x

        executor._contract = MagicMock()
        executor._contract.load_contract_bundle.return_value = (None, None, None)
        mock_contract = SimpleNamespace(contract_id="c-1", expiry_at=None)
        mock_evidence = SimpleNamespace(evidence_case_id="e-1", status="sufficient")
        mock_auth_plan = SimpleNamespace(
            authorization_plan_id="ap-1", revalidation_rules=None, status="authorized"
        )
        executor._contract.synthesize_contract_loop.return_value = (
            mock_contract,
            mock_evidence,
            mock_auth_plan,
        )
        executor._contract.contract_expired.return_value = False
        executor._contract.policy_version_drifted.return_value = False
        executor._contract.admissibility_resolution.return_value = None
        executor._contract.contract_refs.return_value = ("c-1", "e-1", "ap-1")

        executor.decision_service = MagicMock()
        executor.decision_service.record.return_value = "dec-1"

        executor._authorization = MagicMock()
        executor._authorization.authorization_reason.return_value = "Allowed"
        executor._authorization.successful_result_summary.return_value = "Success"
        executor._authorization.prepare_rollback_plan.return_value = {
            "supported": False,
            "strategy": None,
            "artifact_refs": [],
        }
        executor._authorization.ensure_workspace_lease.return_value = None
        executor._authorization.capability_constraints.return_value = {}

        executor.capability_service = MagicMock()
        executor.capability_service.issue.return_value = "cap-1"

        executor._witness_handler = MagicMock()

        executor._receipt = MagicMock()
        executor._receipt.issue_receipt.return_value = "receipt-1"

        executor._reconciliation = MagicMock()
        executor._reconciliation.record_reconciliation.return_value = (None, None)
        executor._reconciliation.reconciliation_execution_status.return_value = "succeeded"
        executor._reconciliation.authorized_effect_summary.return_value = "Effect"

        ctx = _make_attempt_ctx()
        result = executor.execute(ctx, "test_tool", {"path": "/tmp/test"})
        assert result.result_code == "succeeded"
        assert result.receipt_id == "receipt-1"
        assert result.capability_grant_id == "cap-1"


# ---------------------------------------------------------------------------
# ToolExecutor persistence delegation tests
# ---------------------------------------------------------------------------


class TestToolExecutorPersistenceDelegation:
    def test_persist_suspended_state(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        ctx = _make_attempt_ctx()
        executor.persist_suspended_state(
            ctx,
            suspend_kind="observing",
            pending_tool_blocks=[],
            tool_result_blocks=[],
            messages=[],
            next_turn=1,
            disable_tools=False,
            readonly_only=False,
        )
        executor._persistence.persist_suspended_state.assert_called_once()

    def test_persist_blocked_state(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        ctx = _make_attempt_ctx()
        executor.persist_blocked_state(
            ctx,
            pending_tool_blocks=[],
            tool_result_blocks=[],
            messages=[],
            next_turn=1,
            disable_tools=False,
            readonly_only=False,
        )
        executor._persistence.persist_blocked_state.assert_called_once()

    def test_load_suspended_state(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        executor._persistence.load_suspended_state.return_value = {"suspend_kind": "test"}
        result = executor.load_suspended_state("att-1")
        assert result["suspend_kind"] == "test"

    def test_load_blocked_state(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        executor._persistence.load_blocked_state.return_value = {}
        executor.load_blocked_state("att-1")
        executor._persistence.load_blocked_state.assert_called_once()

    def test_clear_suspended_state(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        executor.clear_suspended_state("att-1")
        executor._persistence.clear_suspended_state.assert_called_once()

    def test_clear_blocked_state(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        executor.clear_blocked_state("att-1")
        executor._persistence.clear_blocked_state.assert_called_once()

    def test_current_note_cursor(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        executor._persistence.current_note_cursor.return_value = 42
        assert executor.current_note_cursor("att-1") == 42

    def test_consume_appended_notes(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        executor._persistence.consume_appended_notes.return_value = ([], 0)
        result = executor.consume_appended_notes(_make_attempt_ctx())
        assert result == ([], 0)

    def test_runtime_snapshot_envelope(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        executor._persistence._runtime_snapshot_envelope.return_value = {"schema": 2}
        result = executor._runtime_snapshot_envelope({"test": True})
        assert result["schema"] == 2

    def test_store_pending_execution(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        ctx = _make_attempt_ctx()
        executor._store_pending_execution(ctx, {"payload": True})
        executor._persistence._store_pending_execution.assert_called_once()

    def test_load_pending_execution(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        executor._persistence._load_pending_execution.return_value = {}
        executor._load_pending_execution("att-1")
        executor._persistence._load_pending_execution.assert_called_once()

    def test_clear_pending_execution(self) -> None:
        executor = _make_executor()
        executor._persistence = MagicMock()
        executor._clear_pending_execution("att-1")
        executor._persistence._clear_pending_execution.assert_called_once()


# ---------------------------------------------------------------------------
# ToolExecutor observation delegation tests
# ---------------------------------------------------------------------------


class TestToolExecutorObservationDelegation:
    def test_poll_observation(self) -> None:
        executor = _make_executor()
        executor._observation = MagicMock()
        executor._observation.poll_observation.return_value = None
        result = executor.poll_observation("att-1", now=100.0)
        assert result is None
        executor._observation.poll_observation.assert_called_once()

    def test_finalize_observation(self) -> None:
        executor = _make_executor()
        executor._observation = MagicMock()
        executor._observation.finalize_observation.return_value = {"result_code": "completed"}
        ctx = _make_attempt_ctx()
        result = executor.finalize_observation(
            ctx,
            terminal_status="completed",
            raw_result={"data": True},
            is_error=False,
            summary="Done",
        )
        assert result["result_code"] == "completed"


# ---------------------------------------------------------------------------
# ToolExecutor witness delegation tests
# ---------------------------------------------------------------------------


class TestToolExecutorWitnessDelegation:
    def test_capture_state_witness(self) -> None:
        executor = _make_executor()
        executor._witness_handler = MagicMock()
        executor._witness_handler.capture_state_witness.return_value = "wit-1"
        action = _make_action_request()
        ctx = _make_attempt_ctx()
        ref = executor._capture_state_witness(action, ctx)
        assert ref == "wit-1"

    def test_validate_state_witness(self) -> None:
        executor = _make_executor()
        executor._witness_handler = MagicMock()
        executor._witness_handler.validate_state_witness.return_value = True
        action = _make_action_request()
        ctx = _make_attempt_ctx()
        assert executor._validate_state_witness("wit-1", action, ctx) is True

    def test_load_witness_payload(self) -> None:
        executor = _make_executor()
        executor._witness_handler = MagicMock()
        executor._witness_handler.load_witness_payload.return_value = {"data": True}
        result = executor._load_witness_payload("wit-1")
        assert result["data"] is True


class TestToolExecutorBudgetTracking:
    def test_budget_exceeded(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = SimpleNamespace(
            approval_id=None,
            context={},
            execution_contract_ref=None,
            evidence_case_ref=None,
            authorization_plan_ref=None,
            workspace_lease_id=None,
            policy_version=None,
        )
        store.get_task.return_value = SimpleNamespace(
            task_id="task-1",
            budget_tokens_limit=100,
            budget_tokens_used=90,
            conversation_id="conv-1",
            source_channel="cli",
            policy_profile="default",
            title="Test",
            goal="",
        )

        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("uri://test", "hash123")
        store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")

        executor = _make_executor(store=store, artifact_store=artifact_store)
        tool = _make_tool(readonly=True, action_class="read_local", handler=lambda _: "x" * 50)
        policy = _make_policy(verdict="allow", action_class="read_local")
        executor.registry.get.return_value = tool
        executor.policy_engine.build_action_request.return_value = _make_action_request(
            action_class="read_local"
        )
        executor.policy_engine.evaluate.return_value = policy
        executor.policy_engine.infer_action_class.return_value = "read_local"

        executor._request = MagicMock()
        executor._request.record_action_request.return_value = "ar-1"
        executor._request.record_policy_evaluation.return_value = "pr-1"

        executor._approval = MagicMock()
        executor._approval.matching_approval.return_value = (None, None, None)

        executor._evidence_enricher = MagicMock()
        executor._evidence_enricher.enrich.side_effect = lambda x: x

        executor._authorization = MagicMock()
        executor._authorization.prepare_rollback_plan.return_value = {
            "supported": False,
            "strategy": None,
            "artifact_refs": [],
        }

        executor._reconciliation = MagicMock()
        executor._reconciliation.authorized_effect_summary.return_value = ""

        ctx = _make_attempt_ctx()
        executor.execute(ctx, "test_tool", {})
        store.update_task_budget.assert_called_once()
        # Should emit budget exceeded event
        budget_events = [
            c
            for c in store.append_event.call_args_list
            if c.kwargs.get("event_type") == "budget.exceeded"
        ]
        assert len(budget_events) == 1
        store.update_task_status.assert_called_with("task-1", "budget_exceeded")
