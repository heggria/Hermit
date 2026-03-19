"""Comprehensive tests for Approval Orchestration (Spec 06).

Tests cover:
- ApprovalDelegationPolicy resolution logic
- DelegationRecord with policy field
- TaskDelegationService delegation approval policy checks
- ApprovalTimeoutService expired approval handling
- ApprovalService.request_with_delegation_check
- Batch improvements (metadata, approve_batch_ids)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.kernel.policy.approvals.approvals import ApprovalService, ApprovalTimeoutService
from hermit.kernel.task.models.delegation import (
    ApprovalDelegationPolicy,
    DelegationRecord,
    DelegationScope,
)
from hermit.kernel.task.services.delegation import TaskDelegationService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(*, governed: bool = False) -> MagicMock:
    store = MagicMock()
    if governed:
        store.db_path = MagicMock()
        store.db_path.parent = MagicMock()
        store.db_path.parent.__truediv__ = MagicMock(return_value=MagicMock())
        store.get_decision = MagicMock()
        store.create_decision = MagicMock()
        store.create_capability_grant = MagicMock()
        store.create_receipt = MagicMock()
    else:
        del store.db_path
        del store.get_decision
        del store.create_decision
        del store.create_capability_grant
        del store.create_receipt
    return store


def _make_approval(
    *,
    approval_id: str = "ap-1",
    task_id: str = "task-1",
    step_id: str = "step-1",
    step_attempt_id: str = "attempt-1",
    status: str = "pending",
    resolution: dict | None = None,
    approval_type: str = "tool_use",
    drift_expiry: float | None = None,
    decision_ref: str | None = None,
    requested_action_ref: str | None = None,
    approval_packet_ref: str | None = None,
    state_witness_ref: str | None = None,
    policy_result_ref: str | None = None,
    requested_contract_ref: str | None = None,
    authorization_plan_ref: str | None = None,
    evidence_case_ref: str | None = None,
    resolved_by_principal_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        approval_id=approval_id,
        task_id=task_id,
        step_id=step_id,
        step_attempt_id=step_attempt_id,
        status=status,
        resolution=resolution or {},
        approval_type=approval_type,
        drift_expiry=drift_expiry,
        decision_ref=decision_ref,
        requested_action_ref=requested_action_ref,
        approval_packet_ref=approval_packet_ref,
        state_witness_ref=state_witness_ref,
        policy_result_ref=policy_result_ref,
        requested_contract_ref=requested_contract_ref,
        authorization_plan_ref=authorization_plan_ref,
        evidence_case_ref=evidence_case_ref,
        resolved_by_principal_id=resolved_by_principal_id,
    )


def _make_delegation_store() -> MagicMock:
    """Create a mock store suitable for TaskDelegationService."""
    store = MagicMock()
    store.get_task.return_value = SimpleNamespace(
        task_id="parent-1",
        conversation_id="conv-1",
        status="running",
        source_channel="cli",
        priority="normal",
        policy_profile="default",
        owner_principal_id="principal_user",
        owner="principal_user",
        goal="parent goal",
    )
    store.create_task.return_value = SimpleNamespace(
        task_id="child-1",
        conversation_id="conv-1",
        status="running",
    )
    _id_counter = [0]

    def _gen_id(prefix: str) -> str:
        _id_counter[0] += 1
        return f"{prefix}_{_id_counter[0]:04d}"

    store.generate_id = _gen_id
    store.append_event = MagicMock()
    return store


# ---------------------------------------------------------------------------
# TestApprovalDelegationPolicy
# ---------------------------------------------------------------------------


class TestApprovalDelegationPolicy:
    def test_resolve_auto_approve(self) -> None:
        policy = ApprovalDelegationPolicy(
            auto_approve=["read_local", "list_files"],
            require_parent_approval=["write_local"],
            deny=["network_write"],
        )
        assert policy.resolve("read_local") == "auto_approve"
        assert policy.resolve("list_files") == "auto_approve"

    def test_resolve_require_parent(self) -> None:
        policy = ApprovalDelegationPolicy(
            auto_approve=["read_local"],
            require_parent_approval=["write_local"],
            deny=["network_write"],
        )
        assert policy.resolve("write_local") == "require_parent_approval"

    def test_resolve_deny_explicit(self) -> None:
        policy = ApprovalDelegationPolicy(
            auto_approve=["read_local"],
            require_parent_approval=["write_local"],
            deny=["network_write"],
        )
        assert policy.resolve("network_write") == "deny"

    def test_resolve_deny_by_default(self) -> None:
        policy = ApprovalDelegationPolicy(
            auto_approve=["read_local"],
            require_parent_approval=["write_local"],
            deny=["network_write"],
        )
        assert policy.resolve("unknown_action") == "deny"

    def test_empty_policy_denies_all(self) -> None:
        policy = ApprovalDelegationPolicy()
        assert policy.resolve("read_local") == "deny"
        assert policy.resolve("write_local") == "deny"
        assert policy.resolve("anything") == "deny"


# ---------------------------------------------------------------------------
# TestDelegationRecordWithPolicy
# ---------------------------------------------------------------------------


class TestDelegationRecordWithPolicy:
    def test_default_no_policy(self) -> None:
        record = DelegationRecord(
            delegation_id="del-1",
            parent_task_id="parent-1",
            child_task_id="child-1",
            delegated_principal_id="principal_agent",
            scope=DelegationScope(),
        )
        assert record.approval_delegation_policy is None

    def test_with_policy(self) -> None:
        policy = ApprovalDelegationPolicy(
            auto_approve=["read_local"],
            deny=["network_write"],
        )
        record = DelegationRecord(
            delegation_id="del-1",
            parent_task_id="parent-1",
            child_task_id="child-1",
            delegated_principal_id="principal_agent",
            scope=DelegationScope(),
            approval_delegation_policy=policy,
        )
        assert record.approval_delegation_policy is policy
        assert record.approval_delegation_policy.resolve("read_local") == "auto_approve"
        assert record.approval_delegation_policy.resolve("network_write") == "deny"


# ---------------------------------------------------------------------------
# TestTaskDelegationServiceApprovalPolicy
# ---------------------------------------------------------------------------


class TestTaskDelegationServiceApprovalPolicy:
    def test_delegate_with_policy(self) -> None:
        store = _make_delegation_store()
        svc = TaskDelegationService(store)
        policy = ApprovalDelegationPolicy(
            auto_approve=["read_local"],
            deny=["network_write"],
        )
        child_id = svc.delegate(
            parent_task_id="parent-1",
            child_goal="do something",
            delegated_principal_id="principal_agent",
            approval_delegation_policy=policy,
        )
        assert child_id == "child-1"
        # Verify the delegation record stores the policy
        records = svc.list_children("parent-1")
        assert len(records) == 1
        # Check event payload includes the policy
        event_call = store.append_event.call_args
        payload = event_call.kwargs["payload"]
        assert "approval_delegation_policy" in payload
        assert payload["approval_delegation_policy"]["auto_approve"] == ["read_local"]

    def test_delegate_without_policy_no_policy_in_event(self) -> None:
        store = _make_delegation_store()
        svc = TaskDelegationService(store)
        svc.delegate(
            parent_task_id="parent-1",
            child_goal="do something",
            delegated_principal_id="principal_agent",
        )
        event_call = store.append_event.call_args
        payload = event_call.kwargs["payload"]
        assert "approval_delegation_policy" not in payload

    def test_check_delegation_no_record(self) -> None:
        store = _make_delegation_store()
        svc = TaskDelegationService(store)
        result, delegation_id = svc.check_delegation_approval_policy(
            child_task_id="unknown-child",
            action_class="read_local",
        )
        assert result == "no_policy"
        assert delegation_id is None

    def test_check_delegation_no_policy(self) -> None:
        store = _make_delegation_store()
        svc = TaskDelegationService(store)
        svc.delegate(
            parent_task_id="parent-1",
            child_goal="do something",
            delegated_principal_id="principal_agent",
        )
        result, delegation_id = svc.check_delegation_approval_policy(
            child_task_id="child-1",
            action_class="read_local",
        )
        assert result == "no_policy"
        assert delegation_id is None

    def test_check_delegation_auto_approve(self) -> None:
        store = _make_delegation_store()
        svc = TaskDelegationService(store)
        policy = ApprovalDelegationPolicy(auto_approve=["read_local"])
        svc.delegate(
            parent_task_id="parent-1",
            child_goal="do something",
            delegated_principal_id="principal_agent",
            approval_delegation_policy=policy,
        )
        result, delegation_id = svc.check_delegation_approval_policy(
            child_task_id="child-1",
            action_class="read_local",
        )
        assert result == "auto_approve"
        assert delegation_id is not None

    def test_check_delegation_deny(self) -> None:
        store = _make_delegation_store()
        svc = TaskDelegationService(store)
        policy = ApprovalDelegationPolicy(deny=["network_write"])
        svc.delegate(
            parent_task_id="parent-1",
            child_goal="do something",
            delegated_principal_id="principal_agent",
            approval_delegation_policy=policy,
        )
        result, delegation_id = svc.check_delegation_approval_policy(
            child_task_id="child-1",
            action_class="network_write",
        )
        assert result == "deny"
        assert delegation_id is not None

    def test_check_delegation_require_parent(self) -> None:
        store = _make_delegation_store()
        svc = TaskDelegationService(store)
        policy = ApprovalDelegationPolicy(require_parent_approval=["write_local"])
        svc.delegate(
            parent_task_id="parent-1",
            child_goal="do something",
            delegated_principal_id="principal_agent",
            approval_delegation_policy=policy,
        )
        result, delegation_id = svc.check_delegation_approval_policy(
            child_task_id="child-1",
            action_class="write_local",
        )
        assert result == "require_parent_approval"
        assert delegation_id is not None

    def test_check_delegation_unknown_action_denied(self) -> None:
        store = _make_delegation_store()
        svc = TaskDelegationService(store)
        policy = ApprovalDelegationPolicy(auto_approve=["read_local"])
        svc.delegate(
            parent_task_id="parent-1",
            child_goal="do something",
            delegated_principal_id="principal_agent",
            approval_delegation_policy=policy,
        )
        result, delegation_id = svc.check_delegation_approval_policy(
            child_task_id="child-1",
            action_class="unknown_action",
        )
        assert result == "deny"
        assert delegation_id is not None


# ---------------------------------------------------------------------------
# TestApprovalTimeoutService
# ---------------------------------------------------------------------------


class TestApprovalTimeoutService:
    def test_no_expired_approvals(self) -> None:
        store = _make_store()
        store.list_approvals.return_value = []
        svc = ApprovalTimeoutService(store)
        results = svc.check_expired()
        assert results == []

    def test_expired_approval_auto_denied(self) -> None:
        store = _make_store()
        expired = _make_approval(
            approval_id="ap-expired",
            task_id="task-1",
            drift_expiry=1000.0,  # well in the past
        )
        store.list_approvals.return_value = [expired]
        svc = ApprovalTimeoutService(store)
        results = svc.check_expired()

        assert len(results) == 1
        assert results[0]["approval_id"] == "ap-expired"
        assert results[0]["task_id"] == "task-1"
        assert results[0]["escalation_emitted"] is False

        store.resolve_approval.assert_called_once_with(
            "ap-expired",
            status="denied",
            resolved_by="system",
            resolution={
                "status": "denied",
                "mode": "denied",
                "reason": "approval_timeout",
            },
        )
        # Should emit timed_out event but NOT escalation event
        assert store.append_event.call_count == 1
        event_call = store.append_event.call_args
        assert event_call.kwargs["event_type"] == "approval.timed_out"

    def test_expired_with_escalation(self) -> None:
        store = _make_store()
        expired = _make_approval(
            approval_id="ap-expired",
            task_id="task-1",
            drift_expiry=1000.0,
        )
        store.list_approvals.return_value = [expired]
        svc = ApprovalTimeoutService(store, escalation_enabled=True)
        results = svc.check_expired()

        assert len(results) == 1
        assert results[0]["escalation_emitted"] is True

        # Should emit both escalation and timed_out events
        assert store.append_event.call_count == 2
        escalation_call = store.append_event.call_args_list[0]
        timeout_call = store.append_event.call_args_list[1]
        assert escalation_call.kwargs["event_type"] == "approval.escalation_needed"
        assert timeout_call.kwargs["event_type"] == "approval.timed_out"

    def test_non_expired_not_touched(self) -> None:
        import time

        store = _make_store()
        future_approval = _make_approval(
            approval_id="ap-future",
            drift_expiry=time.time() + 3600,  # 1 hour from now
        )
        store.list_approvals.return_value = [future_approval]
        svc = ApprovalTimeoutService(store)
        results = svc.check_expired()

        assert results == []
        store.resolve_approval.assert_not_called()
        store.append_event.assert_not_called()

    def test_no_drift_expiry_not_touched(self) -> None:
        store = _make_store()
        no_expiry = _make_approval(
            approval_id="ap-no-expiry",
            drift_expiry=None,
        )
        store.list_approvals.return_value = [no_expiry]
        svc = ApprovalTimeoutService(store)
        results = svc.check_expired()

        assert results == []
        store.resolve_approval.assert_not_called()

    def test_multiple_expired(self) -> None:
        store = _make_store()
        expired1 = _make_approval(
            approval_id="ap-1",
            task_id="task-1",
            drift_expiry=500.0,
        )
        expired2 = _make_approval(
            approval_id="ap-2",
            task_id="task-2",
            drift_expiry=600.0,
        )
        store.list_approvals.return_value = [expired1, expired2]
        svc = ApprovalTimeoutService(store)
        results = svc.check_expired()

        assert len(results) == 2
        assert results[0]["approval_id"] == "ap-1"
        assert results[1]["approval_id"] == "ap-2"
        assert store.resolve_approval.call_count == 2
        assert store.append_event.call_count == 2

    def test_mixed_expired_and_valid(self) -> None:
        import time

        store = _make_store()
        expired = _make_approval(
            approval_id="ap-expired",
            drift_expiry=500.0,
        )
        valid = _make_approval(
            approval_id="ap-valid",
            drift_expiry=time.time() + 3600,
        )
        no_expiry = _make_approval(
            approval_id="ap-none",
            drift_expiry=None,
        )
        store.list_approvals.return_value = [expired, valid, no_expiry]
        svc = ApprovalTimeoutService(store)
        results = svc.check_expired()

        assert len(results) == 1
        assert results[0]["approval_id"] == "ap-expired"


# ---------------------------------------------------------------------------
# TestRequestWithDelegationCheck
# ---------------------------------------------------------------------------


class TestRequestWithDelegationCheck:
    def test_no_delegation_service(self) -> None:
        store = _make_store()
        store.create_approval.return_value = SimpleNamespace(approval_id="ap-1")
        svc = ApprovalService(store)
        aid, status = svc.request_with_delegation_check(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            approval_type="tool_use",
            requested_action={"tool_name": "bash"},
            request_packet_ref="ref-1",
            action_class="read_local",
        )
        assert aid == "ap-1"
        assert status == "pending"

    def test_auto_approved_by_delegation(self) -> None:
        store = _make_store()
        store.create_approval.return_value = SimpleNamespace(approval_id="ap-1")
        del store.get_approval
        svc = ApprovalService(store)

        delegation_svc = MagicMock()
        delegation_svc.check_delegation_approval_policy.return_value = (
            "auto_approve",
            "del-1",
        )

        aid, status = svc.request_with_delegation_check(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            approval_type="tool_use",
            requested_action={"tool_name": "bash"},
            request_packet_ref="ref-1",
            action_class="read_local",
            delegation_service=delegation_svc,
        )
        assert aid == "ap-1"
        assert status == "auto_approved"
        store.resolve_approval.assert_called_once()
        call_kwargs = store.resolve_approval.call_args.kwargs
        assert call_kwargs["status"] == "granted"
        assert call_kwargs["resolved_by"] == "delegation_policy"

    def test_denied_by_delegation(self) -> None:
        store = _make_store()
        store.create_approval.return_value = SimpleNamespace(approval_id="ap-1")
        del store.get_approval
        svc = ApprovalService(store)

        delegation_svc = MagicMock()
        delegation_svc.check_delegation_approval_policy.return_value = (
            "deny",
            "del-1",
        )

        aid, status = svc.request_with_delegation_check(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            approval_type="tool_use",
            requested_action={"tool_name": "bash"},
            request_packet_ref="ref-1",
            action_class="network_write",
            delegation_service=delegation_svc,
        )
        assert aid == "ap-1"
        assert status == "denied"
        store.resolve_approval.assert_called_once()
        call_kwargs = store.resolve_approval.call_args.kwargs
        assert call_kwargs["status"] == "denied"
        assert call_kwargs["resolution"]["reason"] == "denied_by_delegation_policy"

    def test_require_parent_stays_pending(self) -> None:
        store = _make_store()
        store.create_approval.return_value = SimpleNamespace(approval_id="ap-1")
        svc = ApprovalService(store)

        delegation_svc = MagicMock()
        delegation_svc.check_delegation_approval_policy.return_value = (
            "require_parent_approval",
            "del-1",
        )

        aid, status = svc.request_with_delegation_check(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            approval_type="tool_use",
            requested_action={"tool_name": "bash"},
            request_packet_ref="ref-1",
            action_class="write_local",
            delegation_service=delegation_svc,
        )
        assert aid == "ap-1"
        assert status == "pending"

    def test_no_policy_stays_pending(self) -> None:
        store = _make_store()
        store.create_approval.return_value = SimpleNamespace(approval_id="ap-1")
        svc = ApprovalService(store)

        delegation_svc = MagicMock()
        delegation_svc.check_delegation_approval_policy.return_value = (
            "no_policy",
            None,
        )

        aid, status = svc.request_with_delegation_check(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            approval_type="tool_use",
            requested_action={"tool_name": "bash"},
            request_packet_ref="ref-1",
            action_class="read_local",
            delegation_service=delegation_svc,
        )
        assert aid == "ap-1"
        assert status == "pending"


# ---------------------------------------------------------------------------
# TestBatchImprovements
# ---------------------------------------------------------------------------


class TestBatchImprovements:
    def test_request_batch_with_metadata(self) -> None:
        store = _make_store()
        store.create_approval.return_value = SimpleNamespace(approval_id="ap-1")
        svc = ApprovalService(store)

        metadata = {"ui_group": "file_operations", "display_order": 1}
        requests = [
            {
                "step_id": "s1",
                "step_attempt_id": "a1",
                "requested_action": {},
                "request_packet_ref": "ref1",
            },
        ]
        ids = svc.request_batch(
            task_id="task-1",
            approval_requests=requests,
            batch_reason="grouped ops",
            batch_metadata=metadata,
        )
        assert len(ids) == 1
        call_kwargs = store.resolve_approval.call_args.kwargs
        resolution = call_kwargs["resolution"]
        assert resolution["batch_metadata"] == metadata
        assert resolution["batch_reason"] == "grouped ops"
        assert resolution["batch_id"].startswith("batch_")

    def test_request_batch_without_metadata(self) -> None:
        store = _make_store()
        store.create_approval.return_value = SimpleNamespace(approval_id="ap-1")
        svc = ApprovalService(store)

        requests = [
            {
                "step_id": "s1",
                "step_attempt_id": "a1",
                "requested_action": {},
                "request_packet_ref": "ref1",
            },
        ]
        ids = svc.request_batch(
            task_id="task-1",
            approval_requests=requests,
        )
        assert len(ids) == 1
        call_kwargs = store.resolve_approval.call_args.kwargs
        resolution = call_kwargs["resolution"]
        assert "batch_metadata" not in resolution

    def test_approve_batch_ids(self) -> None:
        store = _make_store()
        svc = ApprovalService(store)
        del store.get_approval

        result = svc.approve_batch_ids(["ap-1", "ap-2", "ap-3"])
        assert result == ["ap-1", "ap-2", "ap-3"]
        assert store.resolve_approval.call_count == 3

    def test_approve_batch_ids_empty(self) -> None:
        store = _make_store()
        svc = ApprovalService(store)
        result = svc.approve_batch_ids([])
        assert result == []

    def test_approve_batch_ids_custom_resolved_by(self) -> None:
        store = _make_store()
        svc = ApprovalService(store)
        del store.get_approval

        result = svc.approve_batch_ids(["ap-1"], resolved_by="admin")
        assert result == ["ap-1"]
        call_kwargs = store.resolve_approval.call_args.kwargs
        assert call_kwargs["resolved_by"] == "admin"
