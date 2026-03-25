from __future__ import annotations

from typing import Any

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import ActionRequest, PolicyDecision


class AuthorizationPlanService:
    def __init__(self, store: KernelStore, artifact_store: ArtifactStore) -> None:
        self.store = store
        self.artifact_store = artifact_store

    def preflight(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        contract_ref: str,
        action_request: ActionRequest,
        policy: PolicyDecision,
        approval_packet_ref: str | None,
        witness_ref: str | None,
    ):
        current_gaps: list[str] = []
        if policy.verdict == "deny":
            current_gaps.append("policy_denied")
            status = "blocked"
        elif policy.obligations.require_approval:
            status = "awaiting_approval"
        else:
            status = "preflighted"
        approval_route = "operator" if policy.obligations.require_approval else "none"
        witness_requirements = ["state_witness"] if witness_ref else []
        workspace_mode = self._workspace_mode(action_request.action_class)
        authorization_plan = self.store.create_authorization_plan(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            contract_ref=contract_ref,
            policy_profile_ref=attempt_ctx.policy_profile,
            requested_action_classes=[action_request.action_class],
            required_decision_refs=[],
            approval_route=approval_route,
            witness_requirements=witness_requirements,
            proposed_grant_shape={
                "action_class": action_request.action_class,
                "resource_scope": list(action_request.resource_scopes),
            },
            downgrade_options=["gather_more_evidence", "reduce_scope", "request_authority"],
            current_gaps=current_gaps,
            status=status,
            estimated_authority_cost=1.0 if policy.obligations.require_approval else 0.2,
            expiry_constraints={
                "requires_revalidation": bool(witness_ref or policy.obligations.require_approval)
            },
            revalidation_rules={
                "check_witness": bool(witness_ref),
                "check_approval": bool(policy.obligations.require_approval),
                "check_policy_version": True,
            },
            operator_packet_ref=approval_packet_ref,
            required_workspace_mode=workspace_mode,
            required_secret_policy="default"
            if action_request.action_class.startswith("credentialed")
            else None,
            proposed_lease_shape={
                "mode": workspace_mode,
                "resource_scope": list(action_request.resource_scopes),
            },
        )
        artifact_ref = self._store_artifact(
            authorization_plan.authorization_plan_id,
            attempt_ctx=attempt_ctx,
            payload={
                "authorization_plan_id": authorization_plan.authorization_plan_id,
                "contract_ref": contract_ref,
                "status": authorization_plan.status,
                "approval_route": authorization_plan.approval_route,
                "current_gaps": authorization_plan.current_gaps,
                "proposed_grant_shape": authorization_plan.proposed_grant_shape,
                "revalidation_rules": authorization_plan.revalidation_rules,
            },
        )
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            authorization_plan_ref=authorization_plan.authorization_plan_id,
            context={
                **(dict(attempt.context or {}) if attempt is not None else {}),
                "authorization_plan_artifact_ref": artifact_ref,
            },
        )
        self.store.append_event(
            event_type="authorization_plan.selected",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "authorization_plan_ref": authorization_plan.authorization_plan_id,
                "artifact_ref": artifact_ref,
                "status": authorization_plan.status,
                "approval_route": authorization_plan.approval_route,
            },
        )
        self.store.update_execution_contract(
            contract_ref,
            authorization_plan_ref=authorization_plan.authorization_plan_id,
        )
        return authorization_plan, artifact_ref

    def invalidate(
        self,
        authorization_plan_id: str,
        *,
        gaps: list[str],
        summary: str,
        status: str = "invalidated",
    ) -> None:
        record = self.store.get_authorization_plan(authorization_plan_id)
        if record is None:
            return
        self.store.update_authorization_plan(
            authorization_plan_id,
            status=status,
            current_gaps=gaps,
            operator_packet_ref=summary,
        )
        self.store.append_event(
            event_type=f"authorization_plan.{status}",
            entity_type="authorization_plan",
            entity_id=authorization_plan_id,
            task_id=record.task_id,
            step_id=record.step_id,
            actor="kernel",
            payload={
                "current_gaps": list(gaps),
                "summary": summary,
            },
        )

    def revalidate(
        self,
        authorization_plan_id: str,
        current_policy_version: str,
    ) -> bool:
        """Re-evaluate revalidation rules on an existing authorization plan.

        If ``check_policy_version`` is ``True`` in the plan's revalidation
        rules, the method compares the *current_policy_version* against the
        step attempt's stored ``policy_version``.  A mismatch means the
        policy has changed since the plan was created, so the plan is
        invalidated.

        Returns ``True`` if the plan was invalidated, ``False`` otherwise.
        """
        record = self.store.get_authorization_plan(authorization_plan_id)
        if record is None:
            return False

        rules = record.revalidation_rules or {}
        if not rules.get("check_policy_version", False):
            return False

        # Retrieve the step attempt to compare stored policy_version.
        attempt = self.store.get_step_attempt(record.step_attempt_id)
        if attempt is None:
            return False

        stored_version = attempt.policy_version or ""
        if stored_version == current_policy_version:
            return False

        # Policy version has drifted — invalidate the plan.
        self.invalidate(
            authorization_plan_id,
            gaps=["policy_version_changed"],
            summary=(
                f"Policy version changed from '{stored_version}' to "
                f"'{current_policy_version}' — revalidation required."
            ),
        )
        return True

    def _store_artifact(
        self,
        authorization_plan_id: str,
        *,
        attempt_ctx: TaskExecutionContext,
        payload: dict[str, Any],
    ) -> str:
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind="authorization.plan",
            uri=uri,
            content_hash=content_hash,
            producer="authorization_plan_service",
            retention_class="audit",
            trust_tier="derived",
            metadata={"authorization_plan_id": authorization_plan_id},
        )
        return artifact.artifact_id

    @staticmethod
    def _workspace_mode(action_class: str) -> str:
        if action_class in {"write_local", "patch_file", "execute_command", "vcs_mutation"}:
            return "mutable"
        return "readonly"
