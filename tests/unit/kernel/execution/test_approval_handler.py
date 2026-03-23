from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermit.kernel.execution.executor.approval_handler import ApprovalHandler
from hermit.kernel.execution.executor.phase_tracker import (
    _WITNESS_REQUIRED_ACTIONS,
    needs_witness,
)
from hermit.kernel.policy.guards.fingerprint import build_action_fingerprint
from hermit.kernel.policy.models.models import ActionRequest, PolicyDecision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_action_request(
    *,
    tool_name: str = "bash",
    action_class: str = "execute_command",
    task_id: str = "task-1",
    step_attempt_id: str = "attempt-1",
    target_paths: list[str] | None = None,
    network_hosts: list[str] | None = None,
    command_preview: str | None = None,
) -> ActionRequest:
    derived: dict = {}
    if target_paths is not None:
        derived["target_paths"] = target_paths
    if network_hosts is not None:
        derived["network_hosts"] = network_hosts
    if command_preview is not None:
        derived["command_preview"] = command_preview
    return ActionRequest(
        request_id="req-1",
        task_id=task_id,
        step_attempt_id=step_attempt_id,
        tool_name=tool_name,
        action_class=action_class,
        derived=derived,
    )


def _fingerprint_for(action_request: ActionRequest) -> str:
    return build_action_fingerprint(
        {
            "task_id": action_request.task_id,
            "step_attempt_id": action_request.step_attempt_id,
            "tool_name": action_request.tool_name,
            "action_class": action_request.action_class,
            "target_paths": action_request.derived.get("target_paths", []),
            "network_hosts": action_request.derived.get("network_hosts", []),
            "command_preview": action_request.derived.get("command_preview"),
        }
    )


def _make_approval(
    *,
    status: str = "granted",
    drift_expiry: str | None = None,
    fingerprint: str | None = None,
    evidence_case_ref: str | None = None,
    authorization_plan_ref: str | None = None,
    state_witness_ref: str | None = None,
    approval_id: str = "approval-1",
    task_id: str = "task-1",
    step_id: str = "step-1",
) -> SimpleNamespace:
    requested_action = {}
    if fingerprint is not None:
        requested_action["fingerprint"] = fingerprint
    return SimpleNamespace(
        approval_id=approval_id,
        task_id=task_id,
        step_id=step_id,
        status=status,
        drift_expiry=drift_expiry,
        state_witness_ref=state_witness_ref,
        requested_action=requested_action,
        evidence_case_ref=evidence_case_ref,
        authorization_plan_ref=authorization_plan_ref,
    )


def _make_policy() -> PolicyDecision:
    return PolicyDecision(verdict="allow", action_class="execute_command")


def _make_attempt_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        task_id="task-1",
        step_id="step-1",
        step_attempt_id="attempt-1",
        workspace_root="/tmp/ws",
    )


def _build_handler(
    *,
    store: MagicMock | None = None,
    witness: MagicMock | None = None,
) -> ApprovalHandler:
    return ApprovalHandler(
        store=store or MagicMock(),
        artifact_store=MagicMock(),
        approval_service=MagicMock(),
        approval_copy=MagicMock(),
        witness=witness or MagicMock(),
        policy_engine=MagicMock(),
    )


# ---------------------------------------------------------------------------
# needs_witness
# ---------------------------------------------------------------------------


class TestNeedsWitness:
    @pytest.mark.parametrize("action_class", sorted(_WITNESS_REQUIRED_ACTIONS))
    def test_returns_true_for_required_actions(self, action_class: str) -> None:
        assert needs_witness(action_class) is True

    @pytest.mark.parametrize(
        "action_class",
        ["read_local", "pure_compute", "unknown", ""],
    )
    def test_returns_false_for_non_required_actions(self, action_class: str) -> None:
        assert needs_witness(action_class) is False


# ---------------------------------------------------------------------------
# matching_approval — early exit paths
# ---------------------------------------------------------------------------


class TestMatchingApprovalEarlyExit:
    def test_none_approval_returns_none_tuple(self) -> None:
        handler = _build_handler()
        result = handler.matching_approval(
            None,
            _make_action_request(),
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert result == (None, None, None)

    def test_non_granted_status_returns_none_tuple(self) -> None:
        handler = _build_handler()
        approval = _make_approval(status="pending")
        result = handler.matching_approval(
            approval,
            _make_action_request(),
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert result == (None, None, None)


# ---------------------------------------------------------------------------
# matching_approval — drift expiry
# ---------------------------------------------------------------------------


class TestDriftExpiry:
    def test_expired_approval_returns_drift(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = MagicMock()
        handler = _build_handler(store=store)
        past_time = str(time.time() - 100)
        approval = _make_approval(
            drift_expiry=past_time,
            fingerprint="ignored",
            state_witness_ref="witness-1",
        )
        result = handler.matching_approval(
            approval,
            _make_action_request(),
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert result == (None, "witness-1", "approval_drift")
        store.append_event.assert_called_once_with(
            event_type="approval.expired",
            entity_type="approval",
            entity_id="approval-1",
            task_id="task-1",
            step_id="step-1",
            actor="kernel",
            payload={
                "approval_id": "approval-1",
                "drift_expiry": past_time,
                "tool_name": "bash",
            },
        )

    def test_non_expired_approval_proceeds(self) -> None:
        handler = _build_handler()
        action = _make_action_request()
        fp = _fingerprint_for(action)
        future_time = str(time.time() + 10000)
        approval = _make_approval(drift_expiry=future_time, fingerprint=fp)
        record, _witness_ref, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert record is approval
        assert drift is None


# ---------------------------------------------------------------------------
# matching_approval — fingerprint mismatch
# ---------------------------------------------------------------------------


class TestFingerprintMismatch:
    def test_mismatched_fingerprint_returns_drift(self) -> None:
        store = MagicMock()
        handler = _build_handler(store=store)
        approval = _make_approval(fingerprint="wrong-fingerprint", state_witness_ref="w-1")
        result = handler.matching_approval(
            approval,
            _make_action_request(),
            _make_policy(),
            "preview-ref",
            attempt_ctx=_make_attempt_ctx(),
        )
        assert result == (None, "w-1", "approval_drift")
        assert store.append_event.call_count == 2
        calls = store.append_event.call_args_list
        assert calls[0].kwargs["event_type"] == "approval.mismatch"
        assert calls[1].kwargs["event_type"] == "approval.drifted"

    def test_matching_fingerprint_proceeds(self) -> None:
        handler = _build_handler()
        action = _make_action_request()
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp)
        record, _, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert record is approval
        assert drift is None


# ---------------------------------------------------------------------------
# matching_approval — evidence case
# ---------------------------------------------------------------------------


class TestEvidenceCase:
    def test_missing_evidence_case_returns_evidence_drift(self) -> None:
        store = MagicMock()
        store.get_evidence_case.return_value = None
        handler = _build_handler(store=store)
        action = _make_action_request()
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp, evidence_case_ref="ev-1")
        _, _, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert drift == "evidence_drift"

    def test_insufficient_evidence_returns_evidence_drift(self) -> None:
        store = MagicMock()
        store.get_evidence_case.return_value = SimpleNamespace(status="insufficient")
        handler = _build_handler(store=store)
        action = _make_action_request()
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp, evidence_case_ref="ev-1")
        _, _, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert drift == "evidence_drift"

    def test_sufficient_evidence_proceeds(self) -> None:
        store = MagicMock()
        store.get_evidence_case.return_value = SimpleNamespace(status="sufficient")
        handler = _build_handler(store=store)
        action = _make_action_request()
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp, evidence_case_ref="ev-1")
        record, _, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert record is approval
        assert drift is None


# ---------------------------------------------------------------------------
# matching_approval — authorization plan
# ---------------------------------------------------------------------------


class TestAuthorizationPlan:
    def test_missing_plan_returns_drift(self) -> None:
        store = MagicMock()
        store.get_authorization_plan.return_value = None
        handler = _build_handler(store=store)
        action = _make_action_request()
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp, authorization_plan_ref="plan-1")
        _, _, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert drift == "approval_drift"

    @pytest.mark.parametrize("bad_status", ["invalidated", "blocked", "expired"])
    def test_bad_plan_status_returns_drift(self, bad_status: str) -> None:
        store = MagicMock()
        store.get_authorization_plan.return_value = SimpleNamespace(status=bad_status)
        handler = _build_handler(store=store)
        action = _make_action_request()
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp, authorization_plan_ref="plan-1")
        _, _, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert drift == "approval_drift"

    def test_unknown_plan_status_returns_drift(self) -> None:
        store = MagicMock()
        store.get_authorization_plan.return_value = SimpleNamespace(status="something_weird")
        handler = _build_handler(store=store)
        action = _make_action_request()
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp, authorization_plan_ref="plan-1")
        _, _, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert drift == "approval_drift"

    @pytest.mark.parametrize("good_status", ["awaiting_approval", "preflighted", "authorized"])
    def test_valid_plan_status_proceeds(self, good_status: str) -> None:
        store = MagicMock()
        store.get_authorization_plan.return_value = SimpleNamespace(status=good_status)
        handler = _build_handler(store=store)
        action = _make_action_request()
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp, authorization_plan_ref="plan-1")
        record, _, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert record is approval
        assert drift is None


# ---------------------------------------------------------------------------
# matching_approval — witness validation
# ---------------------------------------------------------------------------


class TestWitnessValidation:
    def test_witness_failure_returns_witness_drift(self) -> None:
        witness = MagicMock()
        witness.validate.return_value = False
        handler = _build_handler(witness=witness)
        action = _make_action_request(action_class="write_local")
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp, state_witness_ref="w-ref")
        _, witness_ref, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert drift == "witness_drift"
        assert witness_ref == "w-ref"

    def test_witness_success_proceeds(self) -> None:
        witness = MagicMock()
        witness.validate.return_value = True
        handler = _build_handler(witness=witness)
        action = _make_action_request(action_class="write_local")
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp, state_witness_ref="w-ref")
        record, _, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert record is approval
        assert drift is None

    def test_witness_skipped_for_non_witness_actions(self) -> None:
        witness = MagicMock()
        handler = _build_handler(witness=witness)
        action = _make_action_request(action_class="read_local")
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp, state_witness_ref="w-ref")
        record, _, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert record is approval
        assert drift is None
        witness.validate.assert_not_called()

    def test_witness_skipped_when_no_witness_ref(self) -> None:
        witness = MagicMock()
        handler = _build_handler(witness=witness)
        action = _make_action_request(action_class="write_local")
        fp = _fingerprint_for(action)
        approval = _make_approval(fingerprint=fp, state_witness_ref=None)
        record, _, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert record is approval
        assert drift is None
        witness.validate.assert_not_called()


# ---------------------------------------------------------------------------
# matching_approval — full success path
# ---------------------------------------------------------------------------


class TestFullSuccessPath:
    def test_all_checks_pass(self) -> None:
        store = MagicMock()
        store.get_evidence_case.return_value = SimpleNamespace(status="sufficient")
        store.get_authorization_plan.return_value = SimpleNamespace(status="authorized")
        witness = MagicMock()
        witness.validate.return_value = True
        handler = _build_handler(store=store, witness=witness)
        action = _make_action_request(action_class="write_local")
        fp = _fingerprint_for(action)
        future_time = str(time.time() + 10000)
        approval = _make_approval(
            fingerprint=fp,
            drift_expiry=future_time,
            evidence_case_ref="ev-1",
            authorization_plan_ref="plan-1",
            state_witness_ref="w-ref",
        )
        record, witness_ref, drift = handler.matching_approval(
            approval,
            action,
            _make_policy(),
            None,
            attempt_ctx=_make_attempt_ctx(),
        )
        assert record is approval
        assert witness_ref == "w-ref"
        assert drift is None
