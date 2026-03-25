"""Unit tests for RecoveryHandler — uncertain and dispatch-denied outcome handling."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.authority.grants.service import CapabilityGrantError
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.recovery_handler import RecoveryHandler
from hermit.kernel.execution.recovery.reconcile import ReconcileOutcome
from hermit.kernel.policy.models.models import (
    ActionRequest,
    PolicyDecision,
    PolicyObligations,
)


def _make_attempt_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults = {
        "conversation_id": "conv-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "source_channel": "chat",
        "workspace_root": "/tmp/workspace",
    }
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


def _make_policy(**overrides: Any) -> PolicyDecision:
    defaults = {
        "verdict": "allow",
        "action_class": "write_local",
        "obligations": PolicyObligations(require_receipt=True),
    }
    defaults.update(overrides)
    return PolicyDecision(**defaults)


def _make_action_request(**overrides: Any) -> ActionRequest:
    defaults = {
        "request_id": "req-1",
        "derived": {"key": "value"},
    }
    defaults.update(overrides)
    return ActionRequest(**defaults)


def _make_tool_spec(**overrides: Any) -> MagicMock:
    spec = MagicMock()
    spec.action_class = overrides.get("action_class", "write_local")
    spec.name = overrides.get("name", "write_file")
    return spec


def _build_handler(**overrides: Any) -> RecoveryHandler:
    """Build a RecoveryHandler with all dependencies mocked."""
    store = MagicMock()
    # Default: get_artifact returns None so _load_witness_payload short-circuits
    store.get_artifact.return_value = None
    kwargs: dict[str, Any] = {
        "store": store,
        "artifact_store": MagicMock(),
        "reconciliations": MagicMock(),
        "receipt_service": MagicMock(),
        "decision_service": MagicMock(),
        "capability_service": MagicMock(),
        "policy_engine": MagicMock(),
        "registry": MagicMock(),
        "_witness": MagicMock(),
    }
    kwargs.update(overrides)
    return RecoveryHandler(**kwargs)


def _uncertain_call_kwargs(
    handler: RecoveryHandler,
    *,
    exc: Exception | None = None,
    outcome_result_code: str = "reconciled_applied",
    outcome_summary: str = "reconciled ok",
    tool_action_class: str | None = "write_local",
    receipt_id: str = "rcpt-1",
    reconciliation: Any = None,
) -> dict[str, Any]:
    """Build the full keyword-argument dict for handle_uncertain_outcome."""
    tool = _make_tool_spec(action_class=tool_action_class)
    attempt_ctx = _make_attempt_ctx()
    policy = _make_policy()
    action_request = _make_action_request()

    outcome = ReconcileOutcome(
        result_code=outcome_result_code,
        summary=outcome_summary,
        observed_refs=["ref-1"],
    )
    handler.reconciliations.reconcile_service.reconcile.return_value = outcome

    issue_receipt = MagicMock(return_value=receipt_id)
    contract_refs_fn = MagicMock(return_value=("contract-1", "bundle-1", "plan-1"))
    load_contract_bundle_fn = MagicMock(return_value=("contract-obj", "bundle-obj"))
    if reconciliation is None:
        reconciliation = SimpleNamespace(reconciliation_id="recon-1")
    record_reconciliation_fn = MagicMock(return_value=(reconciliation, "artifact-ref"))
    reconciliation_execution_status_fn = MagicMock(return_value="reconciling")
    authorized_effect_summary_fn = MagicMock(return_value="effect summary")

    return {
        "tool": tool,
        "tool_name": "write_file",
        "tool_input": {"path": "test.txt", "content": "hello"},
        "attempt_ctx": attempt_ctx,
        "policy": policy,
        "policy_ref": "pol-ref-1",
        "decision_id": "dec-1",
        "capability_grant_id": "grant-1",
        "workspace_lease_id": "lease-1",
        "approval_ref": "appr-1",
        "witness_ref": "witness-1",
        "exc": exc or RuntimeError("tool failed"),
        "idempotency_key": "idem-1",
        "action_request": action_request,
        "action_request_ref": "ar-ref-1",
        "policy_result_ref": "pr-ref-1",
        "environment_ref": "env-ref-1",
        "issue_receipt": issue_receipt,
        "contract_refs_fn": contract_refs_fn,
        "load_contract_bundle_fn": load_contract_bundle_fn,
        "record_reconciliation_fn": record_reconciliation_fn,
        "reconciliation_execution_status_fn": reconciliation_execution_status_fn,
        "authorized_effect_summary_fn": authorized_effect_summary_fn,
    }


class TestHandleUncertainOutcome:
    """Tests for RecoveryHandler.handle_uncertain_outcome."""

    def test_returns_expected_result_keys(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler)
        result = handler.handle_uncertain_outcome(**kwargs)

        expected_keys = {
            "model_content",
            "raw_result",
            "denied",
            "policy_decision",
            "receipt_id",
            "decision_id",
            "capability_grant_id",
            "workspace_lease_id",
            "policy_ref",
            "witness_ref",
            "result_code",
            "execution_status",
            "state_applied",
        }
        assert set(result.keys()) == expected_keys

    def test_result_code_mapped_from_still_unknown(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler, outcome_result_code="still_unknown")
        result = handler.handle_uncertain_outcome(**kwargs)

        assert result["result_code"] == "unknown_outcome"

    def test_result_code_preserved_when_not_still_unknown(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler, outcome_result_code="reconciled_applied")
        result = handler.handle_uncertain_outcome(**kwargs)

        assert result["result_code"] == "reconciled_applied"

    def test_task_status_needs_attention_when_unknown(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler, outcome_result_code="still_unknown")
        handler.handle_uncertain_outcome(**kwargs)

        handler.store.update_task_status.assert_called_once_with("task-1", "needs_attention")

    def test_task_status_reconciling_when_known(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler, outcome_result_code="reconciled_applied")
        handler.handle_uncertain_outcome(**kwargs)

        handler.store.update_task_status.assert_called_once_with("task-1", "reconciling")

    def test_appends_uncertain_outcome_event(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler)
        handler.handle_uncertain_outcome(**kwargs)

        handler.store.append_event.assert_called_once()
        call_kwargs = handler.store.append_event.call_args[1]
        assert call_kwargs["event_type"] == "outcome.uncertain"
        assert call_kwargs["entity_type"] == "step_attempt"
        assert call_kwargs["entity_id"] == "attempt-1"
        assert call_kwargs["task_id"] == "task-1"

    def test_updates_step_attempt_to_reconciling(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler)
        handler.handle_uncertain_outcome(**kwargs)

        handler.store.update_step_attempt.assert_called_once()
        call_kwargs = handler.store.update_step_attempt.call_args
        assert call_kwargs[0][0] == "attempt-1"
        assert call_kwargs[1]["status"] == "reconciling"

    def test_updates_step_to_reconciling(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler)
        handler.handle_uncertain_outcome(**kwargs)

        handler.store.update_step.assert_called_once_with("step-1", status="reconciling")

    def test_issues_receipt(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler, receipt_id="rcpt-42")
        result = handler.handle_uncertain_outcome(**kwargs)

        assert result["receipt_id"] == "rcpt-42"
        kwargs["issue_receipt"].assert_called_once()

    def test_records_reconciliation(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler)
        handler.handle_uncertain_outcome(**kwargs)

        kwargs["record_reconciliation_fn"].assert_called_once()

    def test_model_content_includes_error_info(self) -> None:
        handler = _build_handler()
        exc = ValueError("bad input")
        kwargs = _uncertain_call_kwargs(handler, exc=exc)
        result = handler.handle_uncertain_outcome(**kwargs)

        assert "ValueError" in result["model_content"]
        assert "bad input" in result["model_content"]
        assert "[Execution Requires Attention]" in result["model_content"]

    def test_raw_result_contains_error(self) -> None:
        handler = _build_handler()
        exc = RuntimeError("boom")
        kwargs = _uncertain_call_kwargs(handler, exc=exc)
        result = handler.handle_uncertain_outcome(**kwargs)

        assert result["raw_result"] == {"error": "boom"}

    def test_denied_is_true(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler)
        result = handler.handle_uncertain_outcome(**kwargs)

        assert result["denied"] is True

    def test_state_applied_is_true(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler)
        result = handler.handle_uncertain_outcome(**kwargs)

        assert result["state_applied"] is True

    def test_infers_action_class_when_tool_has_none(self) -> None:
        handler = _build_handler()
        handler.policy_engine.infer_action_class.return_value = "execute_command"
        kwargs = _uncertain_call_kwargs(handler, tool_action_class=None)
        handler.handle_uncertain_outcome(**kwargs)

        handler.policy_engine.infer_action_class.assert_called_once()

    def test_uses_tool_action_class_when_set(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler, tool_action_class="write_local")
        handler.handle_uncertain_outcome(**kwargs)

        handler.policy_engine.infer_action_class.assert_not_called()

    def test_execution_status_from_reconciliation_fn(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler)
        kwargs["reconciliation_execution_status_fn"].return_value = "needs_review"
        result = handler.handle_uncertain_outcome(**kwargs)

        assert result["execution_status"] == "needs_review"

    def test_passes_refs_through(self) -> None:
        handler = _build_handler()
        kwargs = _uncertain_call_kwargs(handler)
        result = handler.handle_uncertain_outcome(**kwargs)

        assert result["decision_id"] == "dec-1"
        assert result["capability_grant_id"] == "grant-1"
        assert result["workspace_lease_id"] == "lease-1"
        assert result["policy_ref"] == "pol-ref-1"
        assert result["witness_ref"] == "witness-1"


class TestHandleDispatchDenied:
    """Tests for RecoveryHandler.handle_dispatch_denied."""

    def _call_kwargs(
        self,
        handler: RecoveryHandler,
        *,
        error_code: str = "expired",
        error_msg: str = "grant expired",
        requires_receipt: bool = True,
        receipt_id: str = "rcpt-denied-1",
    ) -> dict[str, Any]:
        tool = _make_tool_spec()
        attempt_ctx = _make_attempt_ctx()
        policy = _make_policy(
            obligations=PolicyObligations(require_receipt=requires_receipt),
        )
        error = CapabilityGrantError(error_code, error_msg)
        issue_receipt = MagicMock(return_value=receipt_id)
        contract_refs_fn = MagicMock(return_value=("contract-1", "bundle-1", "plan-1"))
        record_reconciliation_fn = MagicMock(
            return_value=(SimpleNamespace(reconciliation_id="recon-d1"), "artifact-ref")
        )

        return {
            "tool": tool,
            "tool_name": "write_file",
            "tool_input": {"path": "test.txt"},
            "attempt_ctx": attempt_ctx,
            "policy": policy,
            "policy_ref": "pol-ref-1",
            "decision_id": "dec-1",
            "capability_grant_id": "grant-1",
            "workspace_lease_id": "lease-1",
            "approval_ref": "appr-1",
            "witness_ref": "witness-1",
            "error": error,
            "idempotency_key": "idem-1",
            "action_request_ref": "ar-ref-1",
            "policy_result_ref": "pr-ref-1",
            "environment_ref": "env-ref-1",
            "issue_receipt": issue_receipt,
            "contract_refs_fn": contract_refs_fn,
            "record_reconciliation_fn": record_reconciliation_fn,
        }

    def test_returns_expected_result_keys(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler)
        result = handler.handle_dispatch_denied(**kwargs)

        expected_keys = {
            "model_content",
            "raw_result",
            "denied",
            "policy_decision",
            "receipt_id",
            "decision_id",
            "capability_grant_id",
            "workspace_lease_id",
            "policy_ref",
            "witness_ref",
            "result_code",
            "execution_status",
            "state_applied",
        }
        assert set(result.keys()) == expected_keys

    def test_result_code_is_dispatch_denied(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler)
        result = handler.handle_dispatch_denied(**kwargs)

        assert result["result_code"] == "dispatch_denied"

    def test_execution_status_is_failed(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler)
        result = handler.handle_dispatch_denied(**kwargs)

        assert result["execution_status"] == "failed"

    def test_appends_dispatch_denied_event(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler)
        handler.handle_dispatch_denied(**kwargs)

        handler.store.append_event.assert_called_once()
        call_kwargs = handler.store.append_event.call_args[1]
        assert call_kwargs["event_type"] == "dispatch.denied"
        assert call_kwargs["entity_type"] == "capability_grant"
        assert call_kwargs["payload"]["error_code"] == "expired"

    def test_updates_step_attempt_to_failed(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler)
        handler.handle_dispatch_denied(**kwargs)

        first_call = handler.store.update_step_attempt.call_args_list[0]
        assert first_call[0][0] == "attempt-1"
        assert first_call[1]["status"] == "failed"

    def test_updates_step_to_failed(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler)
        handler.handle_dispatch_denied(**kwargs)

        first_call = handler.store.update_step.call_args_list[0]
        assert first_call[0] == ("step-1",)
        assert first_call[1]["status"] == "failed"

    def test_updates_task_to_failed(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler)
        handler.handle_dispatch_denied(**kwargs)

        handler.store.update_task_status.assert_any_call("task-1", "failed")

    def test_issues_receipt_when_policy_requires_it(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler, requires_receipt=True, receipt_id="rcpt-abc")
        result = handler.handle_dispatch_denied(**kwargs)

        assert result["receipt_id"] == "rcpt-abc"
        kwargs["issue_receipt"].assert_called_once()

    def test_no_receipt_when_policy_does_not_require(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler, requires_receipt=False)
        result = handler.handle_dispatch_denied(**kwargs)

        assert result["receipt_id"] is None
        kwargs["issue_receipt"].assert_not_called()

    def test_records_reconciliation_when_receipt_issued(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler, requires_receipt=True)
        handler.handle_dispatch_denied(**kwargs)

        kwargs["record_reconciliation_fn"].assert_called_once()

    def test_no_reconciliation_when_no_receipt(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler, requires_receipt=False)
        handler.handle_dispatch_denied(**kwargs)

        kwargs["record_reconciliation_fn"].assert_not_called()

    def test_model_content_includes_capability_denied(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler, error_msg="grant expired")
        result = handler.handle_dispatch_denied(**kwargs)

        assert "[Capability Denied]" in result["model_content"]
        assert "grant expired" in result["model_content"]

    def test_raw_result_contains_error_code(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler, error_code="scope_mismatch")
        result = handler.handle_dispatch_denied(**kwargs)

        assert result["raw_result"]["error_code"] == "scope_mismatch"

    def test_denied_is_true(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler)
        result = handler.handle_dispatch_denied(**kwargs)

        assert result["denied"] is True

    def test_state_applied_is_true(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler)
        result = handler.handle_dispatch_denied(**kwargs)

        assert result["state_applied"] is True

    def test_passes_refs_through(self) -> None:
        handler = _build_handler()
        kwargs = self._call_kwargs(handler)
        result = handler.handle_dispatch_denied(**kwargs)

        assert result["decision_id"] == "dec-1"
        assert result["capability_grant_id"] == "grant-1"
        assert result["workspace_lease_id"] == "lease-1"
        assert result["policy_ref"] == "pol-ref-1"
        assert result["witness_ref"] == "witness-1"

    def test_second_status_update_when_receipt_issued(self) -> None:
        """When a receipt is issued, the handler does a second round of status updates."""
        handler = _build_handler()
        kwargs = self._call_kwargs(handler, requires_receipt=True)
        handler.handle_dispatch_denied(**kwargs)

        # Two rounds of updates: first immediate, second after receipt
        assert handler.store.update_step_attempt.call_count == 2
        assert handler.store.update_step.call_count == 2
        assert handler.store.update_task_status.call_count == 2


class TestLoadWitnessPayload:
    """Tests for RecoveryHandler._load_witness_payload."""

    def test_returns_empty_dict_when_no_ref(self) -> None:
        handler = _build_handler()
        result = handler._load_witness_payload(None)
        assert result == {}

    def test_returns_empty_dict_when_artifact_not_found(self) -> None:
        handler = _build_handler()
        handler.store.get_artifact.return_value = None
        result = handler._load_witness_payload("witness-ref-1")
        assert result == {}

    def test_returns_parsed_payload(self) -> None:
        handler = _build_handler()
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        handler.store.get_artifact.return_value = artifact
        handler.artifact_store.read_text.return_value = json.dumps({"key": "value"})

        result = handler._load_witness_payload("witness-ref-1")
        assert result == {"key": "value"}

    def test_returns_empty_dict_on_json_decode_error(self) -> None:
        handler = _build_handler()
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        handler.store.get_artifact.return_value = artifact
        handler.artifact_store.read_text.return_value = "not json"

        result = handler._load_witness_payload("witness-ref-1")
        assert result == {}

    def test_returns_empty_dict_on_os_error(self) -> None:
        handler = _build_handler()
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        handler.store.get_artifact.return_value = artifact
        handler.artifact_store.read_text.side_effect = OSError("file not found")

        result = handler._load_witness_payload("witness-ref-1")
        assert result == {}

    def test_returns_empty_dict_when_payload_is_not_dict(self) -> None:
        handler = _build_handler()
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        handler.store.get_artifact.return_value = artifact
        handler.artifact_store.read_text.return_value = json.dumps([1, 2, 3])

        result = handler._load_witness_payload("witness-ref-1")
        assert result == {}
