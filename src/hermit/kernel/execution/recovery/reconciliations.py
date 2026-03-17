from __future__ import annotations

from typing import Any

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.recovery.reconcile import ReconcileOutcome, ReconcileService
from hermit.kernel.ledger.journal.store import KernelStore


class ReconciliationService:
    def __init__(
        self,
        store: KernelStore,
        artifact_store: ArtifactStore,
        reconcile_service: ReconcileService,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.reconcile_service = reconcile_service

    def reconcile_attempt(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        contract_ref: str,
        receipt_ref: str,
        action_type: str,
        tool_input: dict[str, Any],
        workspace_root: str,
        observables: dict[str, Any] | None,
        witness: dict[str, Any] | None,
        result_code_hint: str,
        authorized_effect_summary: str,
    ):
        outcome = self.reconcile_service.reconcile(
            action_type=action_type,
            tool_input=tool_input,
            workspace_root=workspace_root,
            observables=observables,
            witness=witness,
        )
        result_class = self._result_class(outcome, result_code_hint=result_code_hint)
        recommended_resolution = self._recommended_resolution(result_class)
        reconciliation = self.store.create_reconciliation(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            contract_ref=contract_ref,
            receipt_refs=[receipt_ref],
            observed_output_refs=list(outcome.observed_refs),
            intended_effect_summary=authorized_effect_summary,
            authorized_effect_summary=authorized_effect_summary,
            observed_effect_summary=outcome.summary,
            receipted_effect_summary=outcome.summary,
            result_class=result_class,
            confidence_delta=self._confidence_delta(outcome),
            recommended_resolution=recommended_resolution,
            operator_summary=f"{result_class}: {outcome.summary}",
            final_state_witness_ref=None,
        )
        artifact_ref = self._store_artifact(
            reconciliation_ref=reconciliation.reconciliation_id,
            attempt_ctx=attempt_ctx,
            payload={
                "reconciliation_id": reconciliation.reconciliation_id,
                "contract_ref": contract_ref,
                "receipt_ref": receipt_ref,
                "result_class": reconciliation.result_class,
                "recommended_resolution": reconciliation.recommended_resolution,
                "observed_effect_summary": reconciliation.observed_effect_summary,
            },
        )
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            reconciliation_ref=reconciliation.reconciliation_id,
            context={
                **(dict(attempt.context or {}) if attempt is not None else {}),
                "reconciliation_artifact_ref": artifact_ref,
            },
        )
        self.store.append_event(
            event_type="reconciliation.closed",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "reconciliation_ref": reconciliation.reconciliation_id,
                "contract_ref": contract_ref,
                "receipt_ref": receipt_ref,
                "result_class": reconciliation.result_class,
            },
        )
        return reconciliation, outcome, artifact_ref

    def _store_artifact(
        self,
        *,
        reconciliation_ref: str,
        attempt_ctx: TaskExecutionContext,
        payload: dict[str, Any],
    ) -> str:
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind="reconciliation.record",
            uri=uri,
            content_hash=content_hash,
            producer="reconciliation_service",
            retention_class="audit",
            trust_tier="derived",
            metadata={"reconciliation_id": reconciliation_ref},
        )
        return artifact.artifact_id

    @staticmethod
    def _result_class(outcome: ReconcileOutcome, *, result_code_hint: str) -> str:
        if result_code_hint in {"dispatch_denied", "denied"}:
            return "unauthorized"
        if result_code_hint in {"unknown_outcome"} and outcome.result_code in {
            "reconciled_applied",
            "reconciled_observed",
        }:
            return "partial"
        if result_code_hint in {"unknown_outcome"}:
            return "ambiguous"
        if outcome.result_code in {"reconciled_applied", "reconciled_observed"}:
            return "satisfied"
        if outcome.result_code == "reconciled_not_applied":
            return "violated"
        if outcome.result_code == "still_unknown" and result_code_hint == "succeeded":
            return "partial"
        return "ambiguous"

    @staticmethod
    def _recommended_resolution(result_class: str) -> str:
        if result_class == "satisfied":
            return "promote_learning"
        if result_class == "violated":
            return "gather_more_evidence"
        if result_class == "unauthorized":
            return "request_authority"
        return "park_and_escalate"

    @staticmethod
    def _confidence_delta(outcome: ReconcileOutcome) -> float:
        if outcome.result_code in {"reconciled_applied", "reconciled_observed"}:
            return 0.2
        if outcome.result_code == "reconciled_not_applied":
            return -0.3
        return -0.1
