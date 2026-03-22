from __future__ import annotations

import time
from typing import Any

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.phase_tracker import needs_witness
from hermit.kernel.execution.executor.witness import WitnessCapture
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import (
    ActionRequest,
    PolicyDecision,
    PolicyEngine,
    build_action_fingerprint,
)
from hermit.kernel.policy.approvals.approval_copy import ApprovalCopyService
from hermit.kernel.policy.approvals.approvals import ApprovalService


class ApprovalHandler:
    """Approval matching and drift detection for governed tool execution."""

    def __init__(
        self,
        *,
        store: KernelStore,
        artifact_store: ArtifactStore,
        approval_service: ApprovalService,
        approval_copy: ApprovalCopyService,
        witness: WitnessCapture,
        policy_engine: PolicyEngine,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.approval_service = approval_service
        self.approval_copy = approval_copy
        self._witness = witness
        self.policy_engine = policy_engine

    def matching_approval(
        self,
        approval_record: Any,
        action_request: ActionRequest,
        policy: PolicyDecision,
        preview_artifact: str | None,
        *,
        attempt_ctx: TaskExecutionContext,
    ) -> tuple[Any, str | None, str | None]:
        if approval_record is None or approval_record.status != "granted":
            return None, None, None
        witness_ref = approval_record.state_witness_ref
        if approval_record.drift_expiry and float(approval_record.drift_expiry) < time.time():
            self.store.append_event(
                event_type="approval.expired",
                entity_type="approval",
                entity_id=approval_record.approval_id,
                task_id=approval_record.task_id,
                step_id=approval_record.step_id,
                actor="kernel",
                payload={
                    "approval_id": approval_record.approval_id,
                    "drift_expiry": approval_record.drift_expiry,
                    "tool_name": action_request.tool_name,
                },
            )
            return None, witness_ref, "approval_drift"
        requested_action = dict(approval_record.requested_action or {})
        fingerprint_payload = {
            "task_id": action_request.task_id,
            "step_attempt_id": action_request.step_attempt_id,
            "tool_name": action_request.tool_name,
            "action_class": action_request.action_class,
            "target_paths": action_request.derived.get("target_paths", []),
            "network_hosts": action_request.derived.get("network_hosts", []),
            "command_preview": action_request.derived.get("command_preview"),
        }
        current_fingerprint = build_action_fingerprint(fingerprint_payload)
        approved_fingerprint = str(requested_action.get("fingerprint", "")).strip()
        if approved_fingerprint != current_fingerprint:
            self.store.append_event(
                event_type="approval.mismatch",
                entity_type="approval",
                entity_id=approval_record.approval_id,
                task_id=approval_record.task_id,
                step_id=approval_record.step_id,
                actor="kernel",
                payload={
                    "approved_fingerprint": approved_fingerprint,
                    "current_fingerprint": current_fingerprint,
                    "tool_name": action_request.tool_name,
                    "preview_artifact": preview_artifact,
                    "policy": policy.to_dict(),
                },
            )
            self.store.append_event(
                event_type="approval.drifted",
                entity_type="approval",
                entity_id=approval_record.approval_id,
                task_id=approval_record.task_id,
                step_id=approval_record.step_id,
                actor="kernel",
                payload={
                    "approval_id": approval_record.approval_id,
                    "drift_kind": "fingerprint_mismatch",
                    "approved_fingerprint": approved_fingerprint,
                    "current_fingerprint": current_fingerprint,
                },
            )
            return None, witness_ref, "approval_drift"
        if approval_record.evidence_case_ref:
            evidence_case = self.store.get_evidence_case(approval_record.evidence_case_ref)
            if evidence_case is None or str(evidence_case.status or "") != "sufficient":
                return None, witness_ref, "evidence_drift"
        if approval_record.authorization_plan_ref:
            authorization_plan = self.store.get_authorization_plan(
                approval_record.authorization_plan_ref
            )
            if authorization_plan is None:
                return None, witness_ref, "approval_drift"
            plan_status = str(authorization_plan.status or "")
            if plan_status in {"invalidated", "blocked", "expired"}:
                return None, witness_ref, "approval_drift"
            if plan_status not in {"awaiting_approval", "preflighted", "authorized"}:
                return None, witness_ref, "approval_drift"
        if (
            witness_ref
            and needs_witness(action_request.action_class)
            and not self._witness.validate(witness_ref, action_request, attempt_ctx)
        ):
            return None, witness_ref, "witness_drift"
        return approval_record, witness_ref, None
