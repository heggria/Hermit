from __future__ import annotations

from typing import Any

from hermit.capabilities import CapabilityGrantService
from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.contracts import contract_for
from hermit.kernel.decisions import DecisionService
from hermit.kernel.receipts import ReceiptService
from hermit.kernel.store import KernelStore


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
        return self._resolve(
            approval_id,
            status="granted",
            resolved_by=resolved_by,
            resolution={"status": "granted", "mode": "once"},
        )

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
