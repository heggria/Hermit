from __future__ import annotations

from pathlib import Path
from typing import Any

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.workspaces import WorkspaceLeaseService, capture_execution_environment
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import PolicyDecision, PolicyEngine
from hermit.kernel.verification.receipts.receipts import ReceiptService
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec, serialize_tool_result


class ReceiptHandler:
    """Receipt issuance logic for governed tool execution."""

    def __init__(
        self,
        *,
        store: KernelStore,
        artifact_store: ArtifactStore,
        receipt_service: ReceiptService,
        registry: ToolRegistry,
        policy_engine: PolicyEngine,
        workspace_lease_service: WorkspaceLeaseService,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.receipt_service = receipt_service
        self.registry = registry
        self.policy_engine = policy_engine
        self.workspace_lease_service = workspace_lease_service

    def issue_receipt(
        self,
        *,
        tool: ToolSpec,
        tool_name: str,
        tool_input: dict[str, Any],
        raw_result: Any,
        attempt_ctx: TaskExecutionContext,
        approval_ref: str | None,
        policy: PolicyDecision,
        policy_ref: str | None,
        decision_ref: str | None,
        capability_grant_ref: str | None,
        workspace_lease_ref: str | None,
        action_request_ref: str | None = None,
        policy_result_ref: str | None = None,
        witness_ref: str | None,
        environment_ref: str | None = None,
        result_code: str,
        idempotency_key: str | None,
        result_summary: str | None = None,
        output_kind: str = "tool_output",
        rollback_supported: bool = False,
        rollback_strategy: str | None = None,
        rollback_artifact_refs: list[str] | None = None,
        contract_ref: str | None = None,
        authorization_plan_ref: str | None = None,
        observed_effect_summary: str | None = None,
        reconciliation_required: bool = False,
    ) -> str:
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        effective_contract_ref = contract_ref or getattr(attempt, "execution_contract_ref", None)
        effective_authorization_plan_ref = authorization_plan_ref or getattr(
            attempt, "authorization_plan_ref", None
        )
        input_uri, input_hash = self.artifact_store.store_json(
            {"tool": tool_name, "input": tool_input}
        )
        input_artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind="tool_input",
            uri=input_uri,
            content_hash=input_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata={"tool_name": tool_name},
        )
        output_uri, output_hash = self.artifact_store.store_json(serialize_tool_result(raw_result))
        output_artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind=output_kind,
            uri=output_uri,
            content_hash=output_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata={"tool_name": tool_name},
        )
        effective_environment_ref = environment_ref
        if effective_environment_ref is None and workspace_lease_ref:
            lease = self.store.get_workspace_lease(workspace_lease_ref)
            if lease is not None and lease.environment_ref:
                effective_environment_ref = lease.environment_ref
        if effective_environment_ref is None:
            env_uri, env_hash = self.artifact_store.store_json(
                capture_execution_environment(cwd=Path(attempt_ctx.workspace_root or "."))
            )
            env_artifact = self.store.create_artifact(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                kind="environment.snapshot",
                uri=env_uri,
                content_hash=env_hash,
                producer="tool_executor",
                retention_class="audit",
                trust_tier="observed",
                metadata={"tool_name": tool_name},
            )
            effective_environment_ref = env_artifact.artifact_id
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            environment_ref=effective_environment_ref,
        )
        return self.receipt_service.issue(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            action_type=tool.action_class or self.policy_engine.infer_action_class(tool),
            receipt_class=tool.action_class or self.policy_engine.infer_action_class(tool),
            input_refs=[input_artifact.artifact_id],
            environment_ref=effective_environment_ref,
            policy_result=policy.to_dict(),
            approval_ref=approval_ref,
            output_refs=[output_artifact.artifact_id],
            result_summary=result_summary or f"{tool_name} executed successfully",
            result_code=result_code,
            decision_ref=decision_ref,
            capability_grant_ref=capability_grant_ref,
            workspace_lease_ref=workspace_lease_ref,
            policy_ref=policy_ref,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_result_ref or policy_ref,
            contract_ref=effective_contract_ref,
            authorization_plan_ref=effective_authorization_plan_ref,
            witness_ref=witness_ref,
            idempotency_key=idempotency_key,
            verifiability="baseline_verifiable" if policy.requires_receipt else "hash_linked_only",
            rollback_supported=rollback_supported,
            rollback_strategy=rollback_strategy,
            rollback_artifact_refs=rollback_artifact_refs,
            observed_effect_summary=observed_effect_summary,
            reconciliation_required=reconciliation_required,
        )
