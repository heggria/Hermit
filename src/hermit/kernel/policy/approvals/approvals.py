from __future__ import annotations

import time
from typing import Any

import structlog

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.grants import CapabilityGrantService
from hermit.kernel.execution.controller.contracts import contract_for
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.approvals.decisions import DecisionService
from hermit.kernel.verification.receipts.receipts import ReceiptService

log = structlog.get_logger()


class ApprovalService:
    def __init__(self, store: KernelStore) -> None:
        self.store = store
        self._governed_resolution = all(
            hasattr(store, attr)
            for attr in (
                "db_path",
                "get_decision",
                "create_decision",
                "create_capability_grant",
                "create_receipt",
            )
        )
        self._artifact_store = (
            ArtifactStore(store.db_path.parent / "artifacts") if self._governed_resolution else None
        )
        self.decisions = DecisionService(store) if self._governed_resolution else None
        self.capabilities = CapabilityGrantService(store) if self._governed_resolution else None
        self.receipts = (
            ReceiptService(store, self._artifact_store) if self._governed_resolution else None
        )

    def request(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        approval_type: str,
        requested_action: dict[str, Any],
        request_packet_ref: str | None,
        requested_action_ref: str | None = None,
        approval_packet_ref: str | None = None,
        policy_result_ref: str | None = None,
        requested_contract_ref: str | None = None,
        authorization_plan_ref: str | None = None,
        evidence_case_ref: str | None = None,
        drift_expiry: float | None = None,
        fallback_contract_refs: list[str] | None = None,
        decision_ref: str | None = None,
        state_witness_ref: str | None = None,
        expires_at: float | None = None,
    ) -> str:
        approval = self.store.create_approval(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=step_attempt_id,
            approval_type=approval_type,
            requested_action=requested_action,
            request_packet_ref=request_packet_ref,
            requested_action_ref=requested_action_ref,
            approval_packet_ref=approval_packet_ref,
            policy_result_ref=policy_result_ref,
            requested_contract_ref=requested_contract_ref,
            authorization_plan_ref=authorization_plan_ref,
            evidence_case_ref=evidence_case_ref,
            drift_expiry=drift_expiry,
            fallback_contract_refs=fallback_contract_refs,
            decision_ref=decision_ref,
            state_witness_ref=state_witness_ref,
            expires_at=expires_at,
        )
        return approval.approval_id

    def approve(self, approval_id: str, *, resolved_by: str = "user") -> str | None:
        return self._resolve(
            approval_id,
            status="granted",
            resolved_by=resolved_by,
            resolution={"status": "granted", "mode": "once"},
        )

    def approve_once(self, approval_id: str, *, resolved_by: str = "user") -> str | None:
        """Alias for approve(); kept for backward compatibility."""
        return self.approve(approval_id, resolved_by=resolved_by)

    def approve_mutable_workspace(
        self, approval_id: str, *, resolved_by: str = "user"
    ) -> str | None:
        return self._resolve(
            approval_id,
            status="granted",
            resolved_by=resolved_by,
            resolution={"status": "granted", "mode": "mutable_workspace"},
        )

    def deny(self, approval_id: str, *, resolved_by: str = "user", reason: str = "") -> str | None:
        return self._resolve(
            approval_id,
            status="denied",
            resolved_by=resolved_by,
            resolution={"status": "denied", "mode": "denied", "reason": reason},
        )

    def _resolve(
        self,
        approval_id: str,
        *,
        status: str,
        resolved_by: str,
        resolution: dict[str, Any],
    ) -> str | None:
        get_approval = getattr(self.store, "get_approval", None)
        if get_approval is None:
            self.store.resolve_approval(
                approval_id,
                status=status,
                resolved_by=resolved_by,
                resolution=resolution,
            )
            return None
        approval = get_approval(approval_id)
        if approval is None:
            return None
        existing_resolution = dict(getattr(approval, "resolution", {}) or {})
        current_status = str(getattr(approval, "status", "") or "")
        if (
            current_status == status
            and str(existing_resolution.get("receipt_ref", "") or "").strip()
        ):
            return str(existing_resolution.get("receipt_ref"))

        self.store.resolve_approval(
            approval_id,
            status=status,
            resolved_by=resolved_by,
            resolution=resolution,
        )
        if (
            not self._governed_resolution
            or self.decisions is None
            or self.capabilities is None
            or self.receipts is None
        ):
            return None
        updated = get_approval(approval_id)
        if updated is None:
            return None
        return self._issue_resolution_receipt(updated, resolved_by=resolved_by)

    def _issue_resolution_receipt(self, approval: Any, *, resolved_by: str) -> str:
        assert self.decisions is not None
        assert self.capabilities is not None
        assert self.receipts is not None
        policy_ref = None
        if approval.decision_ref:
            decision = self.store.get_decision(approval.decision_ref)
            if decision is not None:
                policy_ref = decision.policy_ref
        contract = contract_for("approval_resolution")
        resolution = dict(approval.resolution or {})
        mode = str(resolution.get("mode", approval.status) or approval.status)
        reason = self._resolution_reason(
            approval.status, resolved_by=resolved_by, resolution=resolution
        )
        evidence_refs = [
            ref
            for ref in [
                approval.requested_action_ref,
                approval.approval_packet_ref,
                approval.state_witness_ref,
            ]
            if ref
        ]
        decision_id = self.decisions.record(
            task_id=approval.task_id,
            step_id=approval.step_id,
            step_attempt_id=approval.step_attempt_id,
            decision_type="approval_resolution",
            verdict=mode,
            reason=reason,
            evidence_refs=evidence_refs,
            policy_ref=policy_ref,
            approval_ref=approval.approval_id,
            contract_ref=approval.requested_contract_ref,
            authorization_plan_ref=approval.authorization_plan_ref,
            evidence_case_ref=approval.evidence_case_ref,
            action_type="approval_resolution",
            decided_by=resolved_by,
        )
        idempotency_key = f"approval-resolution:{approval.approval_id}:{approval.status}:{mode}"
        grant_id = self.capabilities.issue(
            task_id=approval.task_id,
            step_id=approval.step_id,
            step_attempt_id=approval.step_attempt_id,
            decision_ref=decision_id,
            approval_ref=approval.approval_id,
            policy_ref=policy_ref,
            issued_to_principal_id=approval.resolved_by_principal_id or "principal_user",
            issued_by_principal_id=resolved_by,
            workspace_lease_ref=None,
            action_class="approval_resolution",
            resource_scope=[f"approval:{approval.approval_id}"],
            idempotency_key=idempotency_key,
            constraints={"approval_type": approval.approval_type, "mode": mode},
        )
        receipt_id = self.receipts.issue(
            task_id=approval.task_id,
            step_id=approval.step_id,
            step_attempt_id=approval.step_attempt_id,
            action_type="approval_resolution",
            input_refs=[
                ref
                for ref in [
                    approval.requested_action_ref,
                    approval.approval_packet_ref,
                    approval.state_witness_ref,
                ]
                if ref
            ],
            environment_ref=None,
            policy_result={
                "verdict": "allow_with_receipt",
                "action_class": "approval_resolution",
                "reason": reason,
                "requires_receipt": contract.receipt_required,
            },
            approval_ref=approval.approval_id,
            output_refs=[],
            result_summary=self._result_summary(approval.status, resolution),
            result_code=str(approval.status),
            decision_ref=decision_id,
            capability_grant_ref=grant_id,
            policy_ref=policy_ref,
            action_request_ref=approval.requested_action_ref,
            policy_result_ref=approval.policy_result_ref or policy_ref,
            contract_ref=approval.requested_contract_ref,
            authorization_plan_ref=approval.authorization_plan_ref,
            witness_ref=approval.state_witness_ref,
            idempotency_key=idempotency_key,
            rollback_supported=False,
            observed_effect_summary=self._result_summary(approval.status, resolution),
        )
        self.capabilities.consume(grant_id)
        updated_resolution = dict(resolution)
        updated_resolution.update(
            {
                "decision_ref": decision_id,
                "capability_grant_ref": grant_id,
                "receipt_ref": receipt_id,
            }
        )
        self.store.update_approval_resolution(approval.approval_id, updated_resolution)
        return receipt_id

    @staticmethod
    def _resolution_reason(
        status: str,
        *,
        resolved_by: str,
        resolution: dict[str, Any],
    ) -> str:
        if status == "granted":
            mode = str(resolution.get("mode", "once") or "once")
            return f"Approval {mode} granted by {resolved_by}."
        reason = str(resolution.get("reason", "") or "").strip()
        suffix = f" Reason: {reason}" if reason else ""
        return f"Approval denied by {resolved_by}.{suffix}"

    @staticmethod
    def _result_summary(status: str, resolution: dict[str, Any]) -> str:
        if status == "granted":
            mode = str(resolution.get("mode", "once") or "once")
            return f"Approval granted ({mode})."
        reason = str(resolution.get("reason", "") or "").strip()
        if reason:
            return f"Approval denied: {reason}"
        return "Approval denied."

    def request_batch(
        self,
        *,
        task_id: str,
        approval_requests: list[dict[str, Any]],
        batch_reason: str = "",
        batch_metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        """Create correlated approvals for multiple parallel steps.

        Each request dict should contain: step_id, step_attempt_id, approval_type,
        requested_action, request_packet_ref.
        All share a batch_id stored in the resolution dict.
        """
        import uuid

        batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        ids: list[str] = []
        for req in approval_requests:
            aid = self.request(
                task_id=task_id,
                step_id=req["step_id"],
                step_attempt_id=req["step_attempt_id"],
                approval_type=req.get("approval_type", "tool_use"),
                requested_action=req.get("requested_action", {}),
                request_packet_ref=req.get("request_packet_ref"),
            )
            resolution: dict[str, Any] = {
                "batch_id": batch_id,
                "batch_reason": batch_reason,
            }
            if batch_metadata is not None:
                resolution["batch_metadata"] = batch_metadata
            self.store.resolve_approval(
                aid,
                status="pending",
                resolved_by="system",
                resolution=resolution,
            )
            ids.append(aid)
        return ids

    def approve_batch(self, batch_id: str, *, resolved_by: str = "user") -> list[str]:
        """Approve all pending approvals sharing a batch_id."""
        approved: list[str] = []
        approvals = self.store.list_approvals(status="pending", limit=1000)
        for a in approvals:
            resolution = dict(a.resolution or {})
            if resolution.get("batch_id") == batch_id:
                self.approve(a.approval_id, resolved_by=resolved_by)
                approved.append(a.approval_id)
        return approved

    def approve_batch_ids(
        self,
        approval_ids: list[str],
        *,
        resolved_by: str = "user",
    ) -> list[str]:
        """Approve specific approval IDs directly with a single decision.

        Returns list of successfully approved IDs.
        """
        approved: list[str] = []
        for aid in approval_ids:
            self.approve(aid, resolved_by=resolved_by)
            approved.append(aid)
        return approved

    def request_with_delegation_check(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        approval_type: str,
        requested_action: dict[str, Any],
        request_packet_ref: str | None,
        action_class: str,
        delegation_service: Any | None = None,
        **kwargs: Any,
    ) -> tuple[str, str]:
        """Create an approval request with delegation policy auto-resolution.

        If a delegation_service is provided, checks the parent's delegation
        policy to determine whether the approval can be auto-resolved.

        Returns (approval_id, resolution_status) where resolution_status is
        one of: 'auto_approved', 'denied', 'pending'.
        """
        approval_id = self.request(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=step_attempt_id,
            approval_type=approval_type,
            requested_action=requested_action,
            request_packet_ref=request_packet_ref,
            **kwargs,
        )

        if delegation_service is None:
            return (approval_id, "pending")

        policy_result, delegation_id = delegation_service.check_delegation_approval_policy(
            child_task_id=task_id,
            action_class=action_class,
        )

        if policy_result == "auto_approve":
            self.approve(approval_id, resolved_by="delegation_policy")
            log.info(
                "approval.auto_resolved_by_delegation",
                approval_id=approval_id,
                delegation_id=delegation_id,
                action_class=action_class,
                resolution="auto_approved",
            )
            return (approval_id, "auto_approved")

        if policy_result == "deny":
            self.deny(
                approval_id,
                resolved_by="delegation_policy",
                reason="denied_by_delegation_policy",
            )
            log.info(
                "approval.auto_resolved_by_delegation",
                approval_id=approval_id,
                delegation_id=delegation_id,
                action_class=action_class,
                resolution="denied",
            )
            return (approval_id, "denied")

        return (approval_id, "pending")


class ApprovalTimeoutService:
    """Background service that checks for expired approvals and auto-denies them.

    Optionally emits escalation events before auto-deny when escalation is enabled.
    """

    def __init__(self, store: KernelStore, *, escalation_enabled: bool = False) -> None:
        self.store = store
        self.escalation_enabled = escalation_enabled

    def check_expired(self) -> list[dict[str, Any]]:
        """Check for pending approvals that have exceeded their drift_expiry.

        For each expired approval:
        1. If escalation_enabled, emit 'approval.escalation_needed' event
        2. Auto-deny with reason 'approval_timeout'
        3. Emit 'approval.timed_out' event

        Returns a list of dicts describing each timed-out approval.
        """
        now = time.time()
        results: list[dict[str, Any]] = []
        approvals = self.store.list_approvals(status="pending", limit=1000)

        for approval in approvals:
            drift_expiry = getattr(approval, "drift_expiry", None)
            if drift_expiry is None or drift_expiry >= now:
                continue

            escalation_emitted = False
            if self.escalation_enabled:
                self.store.append_event(
                    event_type="approval.escalation_needed",
                    entity_type="approval",
                    entity_id=approval.approval_id,
                    task_id=approval.task_id,
                    actor="kernel",
                    payload={
                        "approval_id": approval.approval_id,
                        "task_id": approval.task_id,
                        "drift_expiry": drift_expiry,
                        "expired_at": now,
                    },
                )
                escalation_emitted = True
                log.info(
                    "approval.escalation_needed",
                    approval_id=approval.approval_id,
                    task_id=approval.task_id,
                )

            self.store.resolve_approval(
                approval.approval_id,
                status="denied",
                resolved_by="system",
                resolution={
                    "status": "denied",
                    "mode": "denied",
                    "reason": "approval_timeout",
                },
            )

            self.store.append_event(
                event_type="approval.timed_out",
                entity_type="approval",
                entity_id=approval.approval_id,
                task_id=approval.task_id,
                actor="kernel",
                payload={
                    "approval_id": approval.approval_id,
                    "task_id": approval.task_id,
                    "drift_expiry": drift_expiry,
                    "timed_out_at": now,
                },
            )

            log.info(
                "approval.timed_out",
                approval_id=approval.approval_id,
                task_id=approval.task_id,
            )

            results.append(
                {
                    "approval_id": approval.approval_id,
                    "task_id": approval.task_id,
                    "timed_out_at": now,
                    "escalation_emitted": escalation_emitted,
                }
            )

        return results
