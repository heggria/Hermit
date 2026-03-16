from __future__ import annotations

import time
from typing import Any

from hermit.core.tools import ToolSpec
from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.context import TaskExecutionContext
from hermit.kernel.contracts import ActionContract, contract_for
from hermit.kernel.policy import ActionRequest, PolicyDecision
from hermit.kernel.store import KernelStore


class ExecutionContractService:
    def __init__(self, store: KernelStore, artifact_store: ArtifactStore) -> None:
        self.store = store
        self.artifact_store = artifact_store

    def synthesize_default(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool: ToolSpec,
        action_request: ActionRequest,
        policy: PolicyDecision,
        action_request_ref: str | None,
        witness_ref: str | None,
    ):
        action_contract = contract_for(action_request.action_class)
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        expected_effects = self._expected_effects(action_request)
        required_receipts = (
            [action_request.action_class]
            if policy.requires_receipt or action_contract.receipt_required
            else []
        )
        contract = self.store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective=self._objective(action_request, tool=tool),
            proposed_action_refs=[ref for ref in [action_request_ref] if ref],
            expected_effects=expected_effects,
            success_criteria={
                "tool_name": action_request.tool_name,
                "action_class": action_request.action_class,
                "requires_receipt": bool(required_receipts),
            },
            reversibility_class=self._reversibility_class(action_contract),
            required_receipt_classes=required_receipts,
            drift_budget={
                "resource_scopes": list(action_request.resource_scopes),
                "outside_workspace": bool(action_request.derived.get("outside_workspace")),
                "requires_witness": bool(witness_ref or action_contract.witness_required),
            },
            expiry_at=self._expiry_at(policy=policy, witness_ref=witness_ref),
            status="admissibility_pending",
            operator_summary=self._operator_summary(
                action_request=action_request,
                policy=policy,
                expected_effects=expected_effects,
            ),
            risk_budget={
                "risk_level": policy.risk_level,
                "approval_required": bool(policy.obligations.require_approval),
            },
            expected_artifact_shape={"expected_effects": expected_effects},
            contract_version=max(1, int(getattr(attempt, "contract_version", 0) or 0)),
            action_contract_refs=[action_contract.action_class],
            state_witness_ref=witness_ref,
            rollback_expectation=action_contract.rollback_strategy,
        )
        artifact_ref = self._store_artifact(
            contract.contract_id,
            kind="execution.contract",
            payload={
                "contract_id": contract.contract_id,
                "objective": contract.objective,
                "expected_effects": contract.expected_effects,
                "required_receipt_classes": contract.required_receipt_classes,
                "risk_budget": contract.risk_budget,
                "drift_budget": contract.drift_budget,
                "reversibility_class": contract.reversibility_class,
                "operator_summary": contract.operator_summary,
            },
            attempt_ctx=attempt_ctx,
        )
        self.store.update_step(
            attempt_ctx.step_id,
            contract_ref=contract.contract_id,
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            execution_contract_ref=contract.contract_id,
            contract_version=contract.contract_version,
            context={
                **(dict(attempt.context or {}) if attempt is not None else {}),
                "execution_contract_artifact_ref": artifact_ref,
            },
        )
        self.store.append_event(
            event_type="execution_contract.selected",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "contract_ref": contract.contract_id,
                "artifact_ref": artifact_ref,
                "objective": contract.objective,
                "status": contract.status,
            },
        )
        return contract, artifact_ref

    def supersede(
        self,
        contract_id: str,
        *,
        superseded_by_contract_id: str,
        attempt_ctx: TaskExecutionContext,
        reason: str,
    ) -> None:
        self.store.update_execution_contract(
            contract_id,
            status="superseded",
            superseded_by_contract_id=superseded_by_contract_id,
        )
        self.store.append_event(
            event_type="execution_contract.superseded",
            entity_type="execution_contract",
            entity_id=contract_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "superseded_by_contract_id": superseded_by_contract_id,
                "reason": reason,
            },
        )

    def _store_artifact(
        self,
        contract_id: str,
        *,
        kind: str,
        payload: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind=kind,
            uri=uri,
            content_hash=content_hash,
            producer="execution_contract_service",
            retention_class="audit",
            trust_tier="derived",
            metadata={"contract_id": contract_id},
        )
        return artifact.artifact_id

    @staticmethod
    def _objective(action_request: ActionRequest, *, tool: ToolSpec) -> str:
        return f"{tool.name}: {action_request.action_class}"

    @staticmethod
    def _expected_effects(action_request: ActionRequest) -> list[str]:
        effects: list[str] = []
        for path in action_request.derived.get("target_paths", []):
            effects.append(f"path:{path}")
        for host in action_request.derived.get("network_hosts", []):
            effects.append(f"host:{host}")
        preview = str(action_request.derived.get("command_preview", "") or "").strip()
        if preview:
            effects.append(f"command:{preview}")
        if not effects:
            effects.append(f"action:{action_request.action_class}")
        return effects

    @staticmethod
    def _reversibility_class(action_contract: ActionContract) -> str:
        if action_contract.rollback_strategy in {"file_restore", "supersede_or_invalidate"}:
            return "reversible"
        if action_contract.rollback_strategy in {"compensating_action", "manual_or_followup"}:
            return "compensatable"
        return "limited"

    @staticmethod
    def _expiry_at(*, policy: PolicyDecision, witness_ref: str | None) -> float:
        ttl_seconds = 15 * 60
        if policy.obligations.require_approval or witness_ref:
            ttl_seconds = 5 * 60
        return time.time() + ttl_seconds

    @staticmethod
    def _operator_summary(
        *,
        action_request: ActionRequest,
        policy: PolicyDecision,
        expected_effects: list[str],
    ) -> str:
        return (
            f"{action_request.tool_name} intends {', '.join(expected_effects)}; "
            f"risk={policy.risk_level}; approval_required={policy.obligations.require_approval}"
        )
