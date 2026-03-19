"""Comprehensive tests for ApprovalService (approvals.py).

Target: bring coverage from 63% to 95%+.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermit.kernel.policy.approvals.approvals import ApprovalService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(*, governed: bool = True) -> MagicMock:
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
        # Remove the attrs so hasattr returns False
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


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    @patch("hermit.kernel.policy.approvals.approvals.ArtifactStore")
    @patch("hermit.kernel.policy.approvals.approvals.DecisionService")
    @patch("hermit.kernel.policy.approvals.approvals.CapabilityGrantService")
    @patch("hermit.kernel.policy.approvals.approvals.ReceiptService")
    def test_governed_store(self, mock_receipt, mock_cap, mock_dec, mock_art) -> None:
        store = _make_store(governed=True)
        svc = ApprovalService(store)
        assert svc._governed_resolution is True
        assert svc.decisions is not None
        assert svc.capabilities is not None
        assert svc.receipts is not None

    def test_non_governed_store(self) -> None:
        store = _make_store(governed=False)
        svc = ApprovalService(store)
        assert svc._governed_resolution is False
        assert svc.decisions is None
        assert svc.capabilities is None
        assert svc.receipts is None


# ---------------------------------------------------------------------------
# request
# ---------------------------------------------------------------------------


class TestRequest:
    def test_creates_approval_and_returns_id(self) -> None:
        store = _make_store(governed=False)
        store.create_approval.return_value = SimpleNamespace(approval_id="ap-123")
        svc = ApprovalService(store)
        result = svc.request(
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            approval_type="tool_use",
            requested_action={"tool_name": "bash"},
            request_packet_ref="req-ref",
        )
        assert result == "ap-123"
        store.create_approval.assert_called_once()


# ---------------------------------------------------------------------------
# approve / approve_once / approve_mutable_workspace / deny
# ---------------------------------------------------------------------------


class TestApprove:
    def test_approve(self) -> None:
        store = _make_store(governed=False)
        svc = ApprovalService(store)
        # Store has no get_approval since non-governed mock doesn't set it explicitly
        # But _resolve checks getattr, so let's ensure resolve_approval is called
        MagicMock(spec=[])
        store.configure_mock(**{"resolve_approval": MagicMock()})
        del store.get_approval
        svc.store = store
        result = svc.approve("ap-1")
        assert result is None
        store.resolve_approval.assert_called_once_with(
            "ap-1",
            status="granted",
            resolved_by="user",
            resolution={"status": "granted", "mode": "once"},
        )

    def test_approve_once_alias(self) -> None:
        store = _make_store(governed=False)
        svc = ApprovalService(store)
        del store.get_approval
        svc.approve_once("ap-1", resolved_by="admin")
        store.resolve_approval.assert_called_once_with(
            "ap-1",
            status="granted",
            resolved_by="admin",
            resolution={"status": "granted", "mode": "once"},
        )

    def test_approve_mutable_workspace(self) -> None:
        store = _make_store(governed=False)
        svc = ApprovalService(store)
        del store.get_approval
        svc.approve_mutable_workspace("ap-1")
        store.resolve_approval.assert_called_once_with(
            "ap-1",
            status="granted",
            resolved_by="user",
            resolution={"status": "granted", "mode": "mutable_workspace"},
        )

    def test_deny_with_reason(self) -> None:
        store = _make_store(governed=False)
        svc = ApprovalService(store)
        del store.get_approval
        svc.deny("ap-1", reason="not allowed")
        store.resolve_approval.assert_called_once_with(
            "ap-1",
            status="denied",
            resolved_by="user",
            resolution={"status": "denied", "mode": "denied", "reason": "not allowed"},
        )

    def test_deny_without_reason(self) -> None:
        store = _make_store(governed=False)
        svc = ApprovalService(store)
        del store.get_approval
        svc.deny("ap-1")
        call_kwargs = store.resolve_approval.call_args.kwargs
        assert call_kwargs["resolution"]["reason"] == ""


# ---------------------------------------------------------------------------
# _resolve
# ---------------------------------------------------------------------------


class TestResolve:
    def test_no_get_approval_attr(self) -> None:
        store = _make_store(governed=False)
        svc = ApprovalService(store)
        # Ensure get_approval is not present
        if hasattr(store, "get_approval"):
            del store.get_approval
        result = svc.approve("ap-1")
        assert result is None
        store.resolve_approval.assert_called_once()

    def test_get_approval_returns_none(self) -> None:
        store = _make_store(governed=False)
        svc = ApprovalService(store)
        store.get_approval = MagicMock(return_value=None)
        result = svc.approve("ap-1")
        assert result is None

    def test_already_resolved_with_receipt_ref(self) -> None:
        store = _make_store(governed=False)
        svc = ApprovalService(store)
        existing_approval = _make_approval(
            status="granted",
            resolution={"receipt_ref": "receipt-123"},
        )
        store.get_approval = MagicMock(return_value=existing_approval)
        result = svc.approve("ap-1")
        assert result == "receipt-123"
        store.resolve_approval.assert_not_called()

    def test_non_governed_after_resolve_returns_none(self) -> None:
        store = _make_store(governed=False)
        svc = ApprovalService(store)
        approval = _make_approval(status="pending")
        updated = _make_approval(status="granted")
        store.get_approval = MagicMock(side_effect=[approval, updated])
        result = svc.approve("ap-1")
        # _governed_resolution is False, so returns None
        assert result is None

    @patch("hermit.kernel.policy.approvals.approvals.ArtifactStore")
    @patch("hermit.kernel.policy.approvals.approvals.DecisionService")
    @patch("hermit.kernel.policy.approvals.approvals.CapabilityGrantService")
    @patch("hermit.kernel.policy.approvals.approvals.ReceiptService")
    def test_governed_resolution_issues_receipt(
        self, mock_receipt_cls, mock_cap_cls, mock_dec_cls, mock_art_cls
    ) -> None:
        store = _make_store(governed=True)
        approval = _make_approval(status="pending")
        updated = _make_approval(
            status="granted",
            decision_ref="dec-1",
            requested_action_ref="action-ref",
            approval_packet_ref="packet-ref",
        )
        store.get_approval = MagicMock(side_effect=[approval, updated])
        store.get_decision = MagicMock(return_value=SimpleNamespace(policy_ref="policy-1"))

        svc = ApprovalService(store)
        svc.decisions.record.return_value = "decision-id"
        svc.capabilities.issue.return_value = "grant-id"
        svc.receipts.issue.return_value = "receipt-id"

        result = svc.approve("ap-1")
        assert result == "receipt-id"
        svc.decisions.record.assert_called_once()
        svc.capabilities.issue.assert_called_once()
        svc.receipts.issue.assert_called_once()
        svc.capabilities.consume.assert_called_once_with("grant-id")
        store.update_approval_resolution.assert_called_once()

    @patch("hermit.kernel.policy.approvals.approvals.ArtifactStore")
    @patch("hermit.kernel.policy.approvals.approvals.DecisionService")
    @patch("hermit.kernel.policy.approvals.approvals.CapabilityGrantService")
    @patch("hermit.kernel.policy.approvals.approvals.ReceiptService")
    def test_governed_updated_returns_none(
        self, mock_receipt_cls, mock_cap_cls, mock_dec_cls, mock_art_cls
    ) -> None:
        store = _make_store(governed=True)
        approval = _make_approval(status="pending")
        store.get_approval = MagicMock(side_effect=[approval, None])
        svc = ApprovalService(store)
        result = svc.approve("ap-1")
        assert result is None


# ---------------------------------------------------------------------------
# _issue_resolution_receipt
# ---------------------------------------------------------------------------


class TestIssueResolutionReceipt:
    @patch("hermit.kernel.policy.approvals.approvals.ArtifactStore")
    @patch("hermit.kernel.policy.approvals.approvals.DecisionService")
    @patch("hermit.kernel.policy.approvals.approvals.CapabilityGrantService")
    @patch("hermit.kernel.policy.approvals.approvals.ReceiptService")
    def test_without_decision_ref(
        self, mock_receipt_cls, mock_cap_cls, mock_dec_cls, mock_art_cls
    ) -> None:
        store = _make_store(governed=True)
        svc = ApprovalService(store)
        svc.decisions.record.return_value = "dec-id"
        svc.capabilities.issue.return_value = "grant-id"
        svc.receipts.issue.return_value = "receipt-id"

        approval = _make_approval(
            status="granted",
            resolution={"status": "granted", "mode": "once"},
            decision_ref=None,
        )
        result = svc._issue_resolution_receipt(approval, resolved_by="user")
        assert result == "receipt-id"
        # policy_ref should be None since no decision_ref
        dec_call = svc.decisions.record.call_args
        assert dec_call.kwargs["policy_ref"] is None

    @patch("hermit.kernel.policy.approvals.approvals.ArtifactStore")
    @patch("hermit.kernel.policy.approvals.approvals.DecisionService")
    @patch("hermit.kernel.policy.approvals.approvals.CapabilityGrantService")
    @patch("hermit.kernel.policy.approvals.approvals.ReceiptService")
    def test_decision_ref_with_none_decision(
        self, mock_receipt_cls, mock_cap_cls, mock_dec_cls, mock_art_cls
    ) -> None:
        store = _make_store(governed=True)
        store.get_decision = MagicMock(return_value=None)
        svc = ApprovalService(store)
        svc.decisions.record.return_value = "dec-id"
        svc.capabilities.issue.return_value = "grant-id"
        svc.receipts.issue.return_value = "receipt-id"

        approval = _make_approval(
            status="granted",
            resolution={"status": "granted", "mode": "once"},
            decision_ref="dec-ref-1",
        )
        result = svc._issue_resolution_receipt(approval, resolved_by="user")
        assert result == "receipt-id"

    @patch("hermit.kernel.policy.approvals.approvals.ArtifactStore")
    @patch("hermit.kernel.policy.approvals.approvals.DecisionService")
    @patch("hermit.kernel.policy.approvals.approvals.CapabilityGrantService")
    @patch("hermit.kernel.policy.approvals.approvals.ReceiptService")
    def test_evidence_refs_filtering(
        self, mock_receipt_cls, mock_cap_cls, mock_dec_cls, mock_art_cls
    ) -> None:
        store = _make_store(governed=True)
        svc = ApprovalService(store)
        svc.decisions.record.return_value = "dec-id"
        svc.capabilities.issue.return_value = "grant-id"
        svc.receipts.issue.return_value = "receipt-id"

        approval = _make_approval(
            status="granted",
            resolution={"status": "granted", "mode": "once"},
            requested_action_ref="action-ref",
            approval_packet_ref=None,
            state_witness_ref="witness-ref",
        )
        svc._issue_resolution_receipt(approval, resolved_by="user")
        dec_call = svc.decisions.record.call_args
        evidence_refs = dec_call.kwargs["evidence_refs"]
        assert "action-ref" in evidence_refs
        assert "witness-ref" in evidence_refs
        assert None not in evidence_refs


# ---------------------------------------------------------------------------
# _resolution_reason
# ---------------------------------------------------------------------------


class TestResolutionReason:
    def test_granted_once(self) -> None:
        result = ApprovalService._resolution_reason(
            "granted", resolved_by="user", resolution={"mode": "once"}
        )
        assert "once" in result
        assert "user" in result

    def test_granted_mutable_workspace(self) -> None:
        result = ApprovalService._resolution_reason(
            "granted", resolved_by="admin", resolution={"mode": "mutable_workspace"}
        )
        assert "mutable_workspace" in result
        assert "admin" in result

    def test_denied_with_reason(self) -> None:
        result = ApprovalService._resolution_reason(
            "denied", resolved_by="user", resolution={"reason": "too risky"}
        )
        assert "denied" in result
        assert "too risky" in result

    def test_denied_without_reason(self) -> None:
        result = ApprovalService._resolution_reason("denied", resolved_by="user", resolution={})
        assert "denied" in result
        assert "Reason:" not in result

    def test_granted_none_mode(self) -> None:
        result = ApprovalService._resolution_reason(
            "granted", resolved_by="user", resolution={"mode": None}
        )
        assert "once" in result  # defaults to 'once'


# ---------------------------------------------------------------------------
# _result_summary
# ---------------------------------------------------------------------------


class TestResultSummary:
    def test_granted_once(self) -> None:
        result = ApprovalService._result_summary("granted", {"mode": "once"})
        assert "granted" in result
        assert "once" in result

    def test_granted_mutable(self) -> None:
        result = ApprovalService._result_summary("granted", {"mode": "mutable_workspace"})
        assert "mutable_workspace" in result

    def test_denied_with_reason(self) -> None:
        result = ApprovalService._result_summary("denied", {"reason": "bad"})
        assert "denied" in result.lower()
        assert "bad" in result

    def test_denied_without_reason(self) -> None:
        result = ApprovalService._result_summary("denied", {})
        assert result == "Approval denied."


# ---------------------------------------------------------------------------
# request_batch
# ---------------------------------------------------------------------------


class TestRequestBatch:
    def test_creates_batch(self) -> None:
        store = _make_store(governed=False)
        store.create_approval.return_value = SimpleNamespace(approval_id="ap-1")
        svc = ApprovalService(store)
        requests = [
            {
                "step_id": "s1",
                "step_attempt_id": "a1",
                "approval_type": "tool_use",
                "requested_action": {"tool_name": "bash"},
                "request_packet_ref": "ref1",
            },
            {
                "step_id": "s2",
                "step_attempt_id": "a2",
                "requested_action": {},
                "request_packet_ref": "ref2",
            },
        ]
        ids = svc.request_batch(
            task_id="task-1",
            approval_requests=requests,
            batch_reason="parallel steps",
        )
        assert len(ids) == 2
        assert store.resolve_approval.call_count == 2
        # Check batch_id is consistent
        call1 = store.resolve_approval.call_args_list[0]
        call2 = store.resolve_approval.call_args_list[1]
        batch_id1 = call1.kwargs["resolution"]["batch_id"]
        batch_id2 = call2.kwargs["resolution"]["batch_id"]
        assert batch_id1 == batch_id2
        assert batch_id1.startswith("batch_")


# ---------------------------------------------------------------------------
# approve_batch
# ---------------------------------------------------------------------------


class TestApproveBatch:
    def test_approves_matching_batch(self) -> None:
        store = _make_store(governed=False)
        svc = ApprovalService(store)
        # Remove get_approval so _resolve takes the simple path
        del store.get_approval

        approvals = [
            _make_approval(
                approval_id="ap-1",
                status="pending",
                resolution={"batch_id": "batch_abc"},
            ),
            _make_approval(
                approval_id="ap-2",
                status="pending",
                resolution={"batch_id": "batch_abc"},
            ),
            _make_approval(
                approval_id="ap-3",
                status="pending",
                resolution={"batch_id": "batch_other"},
            ),
        ]
        store.list_approvals.return_value = approvals

        result = svc.approve_batch("batch_abc")
        assert len(result) == 2
        assert "ap-1" in result
        assert "ap-2" in result
        assert "ap-3" not in result
