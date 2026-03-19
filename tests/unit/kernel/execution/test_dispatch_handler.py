"""Tests for hermit.kernel.execution.executor.dispatch_handler."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.authority.grants import CapabilityGrantError
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.dispatch_handler import DispatchDeniedHandler
from hermit.kernel.policy.models.models import PolicyDecision, PolicyObligations
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


def _make_tool(**overrides: Any) -> ToolSpec:
    defaults: dict[str, Any] = {
        "name": "test_tool",
        "description": "test",
        "input_schema": {},
        "handler": lambda _: None,
        "action_class": "write_local",
        "risk_hint": "low",
        "requires_receipt": True,
    }
    defaults.update(overrides)
    return ToolSpec(**defaults)


def _make_policy(*, requires_receipt: bool = False) -> PolicyDecision:
    return PolicyDecision(
        verdict="allow",
        action_class="write_local",
        obligations=PolicyObligations(require_receipt=requires_receipt),
    )


def _make_handler() -> tuple[DispatchDeniedHandler, MagicMock, MagicMock, MagicMock]:
    store = MagicMock()
    policy_engine = MagicMock()
    policy_engine.infer_action_class.return_value = "write_local"
    receipt_handler = MagicMock()
    receipt_handler.issue_receipt.return_value = "receipt-1"
    reconciliation_executor = MagicMock()
    handler = DispatchDeniedHandler(
        store=store,
        policy_engine=policy_engine,
        receipt_handler=receipt_handler,
        reconciliation_executor=reconciliation_executor,
    )
    return handler, store, receipt_handler, reconciliation_executor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHandleDispatchDenied:
    def _call(
        self,
        handler: DispatchDeniedHandler,
        *,
        policy: PolicyDecision | None = None,
        error: CapabilityGrantError | None = None,
    ) -> Any:
        ctx = _make_attempt_ctx()
        return handler.handle_dispatch_denied(
            tool=_make_tool(),
            tool_name="test_tool",
            tool_input={"path": "/tmp/test"},
            attempt_ctx=ctx,
            policy=policy or _make_policy(),
            policy_ref="pol-1",
            decision_id="dec-1",
            capability_grant_id="cap-1",
            workspace_lease_id="lease-1",
            approval_ref="appr-1",
            witness_ref="wit-1",
            error=error or CapabilityGrantError("scope_violation", "Scope mismatch"),
            idempotency_key="idem-1",
            action_request_ref="ar-1",
            policy_result_ref="pr-1",
            environment_ref="env-1",
        )

    def test_returns_denied_result(self) -> None:
        handler, _store, _, _ = _make_handler()
        result = self._call(handler)
        assert result.denied is True
        assert result.result_code == "dispatch_denied"
        assert result.execution_status == "failed"
        assert result.state_applied is True

    def test_appends_dispatch_denied_event(self) -> None:
        handler, store, _, _ = _make_handler()
        self._call(handler)
        store.append_event.assert_called_once()
        kwargs = store.append_event.call_args.kwargs
        assert kwargs["event_type"] == "dispatch.denied"

    def test_updates_attempt_step_task_status(self) -> None:
        handler, store, _, _ = _make_handler()
        self._call(handler)
        # At least one update_step_attempt, update_step, update_task_status
        assert store.update_step_attempt.call_count >= 1
        assert store.update_step.call_count >= 1
        assert store.update_task_status.call_count >= 1

    def test_issues_receipt_when_required(self) -> None:
        handler, _store, receipt_handler, reconciliation_executor = _make_handler()
        policy = _make_policy(requires_receipt=True)
        result = self._call(handler, policy=policy)
        receipt_handler.issue_receipt.assert_called_once()
        reconciliation_executor.record_reconciliation.assert_called_once()
        assert result.receipt_id == "receipt-1"

    def test_no_receipt_when_not_required(self) -> None:
        handler, _store, receipt_handler, _ = _make_handler()
        policy = _make_policy(requires_receipt=False)
        result = self._call(handler, policy=policy)
        receipt_handler.issue_receipt.assert_not_called()
        assert result.receipt_id is None

    def test_error_info_in_result(self) -> None:
        handler, _, _, _ = _make_handler()
        error = CapabilityGrantError("test_code", "Test message")
        result = self._call(handler, error=error)
        assert "Test message" in result.model_content
        assert result.raw_result["error_code"] == "test_code"

    def test_capability_grant_id_propagated(self) -> None:
        handler, _, _, _ = _make_handler()
        result = self._call(handler)
        assert result.capability_grant_id == "cap-1"
        assert result.decision_id == "dec-1"
        assert result.workspace_lease_id == "lease-1"
        assert result.policy_ref == "pol-1"
        assert result.witness_ref == "wit-1"
