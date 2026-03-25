from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any, cast

from hermit.infra.system.i18n import t
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.errors import ContractError
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import (
    POLICY_RULES_VERSION,
    ActionRequest,
    PolicyDecision,
    PolicyEngine,
    build_action_fingerprint,
)
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec


class RequestBuilder:
    """Builds action requests, policy records, and approval payloads for governed execution."""

    def __init__(
        self,
        *,
        store: KernelStore,
        artifact_store: ArtifactStore,
        policy_engine: PolicyEngine,
        registry: ToolRegistry,
        tool_output_limit: int,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.policy_engine = policy_engine
        self.registry = registry
        self.tool_output_limit = tool_output_limit

    def apply_request_overrides(
        self,
        action_request: ActionRequest,
        request_overrides: dict[str, Any],
    ) -> ActionRequest:
        if "actor" in request_overrides:
            actor: Any = request_overrides["actor"]
            if not isinstance(actor, dict):
                raise ContractError(
                    "invalid_override",
                    t(
                        "kernel.executor.error.request_overrides_actor_dict",
                        default="request_overrides.actor must be a dict",
                    ),
                )
            action_request.actor = dict(cast(dict[str, Any], actor))
        if "context" in request_overrides:
            context: Any = request_overrides["context"]
            if not isinstance(context, dict):
                raise ContractError(
                    "invalid_override",
                    t(
                        "kernel.executor.error.request_overrides_context_dict",
                        default="request_overrides.context must be a dict",
                    ),
                )
            merged_context = dict(action_request.context)
            merged_context.update(cast(dict[str, Any], context))
            action_request.context = merged_context
        if "idempotency_key" in request_overrides:
            action_request.idempotency_key = str(request_overrides["idempotency_key"])
        return action_request

    def record_action_request(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        return self.store_json_artifact(
            payload=action_request.to_dict(),
            kind="action_request",
            attempt_ctx=attempt_ctx,
            metadata={"tool_name": action_request.tool_name},
            event_type="action.requested",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            payload_summary={
                "tool_name": action_request.tool_name,
                "action_class": action_request.action_class,
                "risk_hint": action_request.risk_hint,
                "resource_scopes": list(action_request.resource_scopes),
                "idempotency_key": action_request.idempotency_key,
            },
        )

    def record_policy_evaluation(
        self,
        action_request: ActionRequest,
        policy: PolicyDecision,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        payload = {
            "tool_name": action_request.tool_name,
            "action_class": action_request.action_class,
            "risk_band": policy.risk_level,
            "verdict": policy.verdict,
            "reason": policy.reason,
            "reasons": [reason.to_dict() for reason in policy.reasons],
            "obligations": policy.obligations.to_dict(),
            "normalized_constraints": dict(policy.normalized_constraints),
            "policy_profile": action_request.context.get("policy_profile", "default"),
            "policy_rules_version": POLICY_RULES_VERSION,
        }
        return self.store_json_artifact(
            payload=payload,
            kind="policy_evaluation",
            attempt_ctx=attempt_ctx,
            metadata={"tool_name": action_request.tool_name},
            event_type="policy.evaluated",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            payload_summary=payload,
        )

    def store_json_artifact(
        self,
        *,
        payload: Any,
        kind: str,
        attempt_ctx: TaskExecutionContext,
        metadata: dict[str, Any],
        event_type: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        payload_summary: dict[str, Any] | None = None,
    ) -> str:
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind=kind,
            uri=uri,
            content_hash=content_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata=metadata,
        )
        if event_type and entity_type and entity_id:
            self.store.append_event(
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                actor=getattr(attempt_ctx, "actor_principal_id", "principal_user"),
                payload={"artifact_ref": artifact.artifact_id, **(payload_summary or {})},
            )
        return artifact.artifact_id

    def build_preview_artifact(
        self,
        tool: ToolSpec,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
    ) -> str | None:
        preview_text = self.preview_text(tool, tool_input)
        if not preview_text:
            return None
        uri, content_hash = self.artifact_store.store_text(preview_text, extension="md")
        artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind="approval_packet",
            uri=uri,
            content_hash=content_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata={"tool_name": tool.name},
        )
        return artifact.artifact_id

    def preview_text(self, tool: ToolSpec, tool_input: dict[str, Any]) -> str:
        if tool.name in {"write_file", "write_hermit_file"}:
            path = str(tool_input.get("path", ""))
            new_content = str(tool_input.get("content", ""))
            scope_hint = tool.resource_scope_hint
            root_hint = str(scope_hint[0] if isinstance(scope_hint, list) else scope_hint or "")
            old_content = ""
            if root_hint:
                try:
                    file_path = (Path(root_hint) / path).resolve()
                    if file_path.exists():
                        old_content = file_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    old_content = ""
            diff = "\n".join(
                difflib.unified_diff(
                    old_content.splitlines(),
                    new_content.splitlines(),
                    fromfile=f"{path} (current)",
                    tofile=f"{path} (proposed)",
                    lineterm="",
                )
            )
            return t(
                "kernel.executor.preview.write",
                default=(
                    f"# Write Preview\n\nPath: `{path}`\n\n"
                    f"```diff\n{diff or '(new file or no textual diff)'}\n```"
                ),
                path=path,
                diff=diff
                or t(
                    "kernel.executor.preview.write.empty_diff",
                    default="(new file or no textual diff)",
                ),
            )
        if tool.name == "bash":
            command = str(tool_input.get("command", ""))
            return t(
                "kernel.executor.preview.command",
                default=f"# Command Preview\n\n```bash\n{command}\n```",
                command=command,
            )
        return json.dumps({"tool": tool.name, "input": tool_input}, ensure_ascii=False, indent=2)

    def requested_action_payload(
        self,
        action_request: ActionRequest,
        policy: PolicyDecision,
        preview_artifact: str | None,
        *,
        decision_ref: str | None,
        policy_ref: str | None,
        state_witness_ref: str | None,
        contract_ref: str | None = None,
        evidence_case_ref: str | None = None,
        authorization_plan_ref: str | None = None,
    ) -> dict[str, Any]:
        packet = dict(policy.approval_packet or {})
        contract = (
            self.store.get_execution_contract(contract_ref)
            if contract_ref and hasattr(self.store, "get_execution_contract")
            else None
        )
        evidence_case = (
            self.store.get_evidence_case(evidence_case_ref)
            if evidence_case_ref and hasattr(self.store, "get_evidence_case")
            else None
        )
        authorization_plan = (
            self.store.get_authorization_plan(authorization_plan_ref)
            if authorization_plan_ref and hasattr(self.store, "get_authorization_plan")
            else None
        )
        fingerprint_payload = {
            "task_id": action_request.task_id,
            "step_attempt_id": action_request.step_attempt_id,
            "tool_name": action_request.tool_name,
            "action_class": action_request.action_class,
            "target_paths": action_request.derived.get("target_paths", []),
            "network_hosts": action_request.derived.get("network_hosts", []),
            "command_preview": action_request.derived.get("command_preview"),
        }
        fingerprint = build_action_fingerprint(fingerprint_payload)
        packet.setdefault("fingerprint", fingerprint)
        if preview_artifact is not None:
            packet["artifact_ids"] = list(
                dict.fromkeys(list(packet.get("artifact_ids", [])) + [preview_artifact])
            )
        contract_packet = None
        if contract is not None:
            contract_packet = {
                "contract_ref": contract.contract_id,
                "objective": contract.objective,
                "expected_effects": list(contract.expected_effects),
                "evidence_case_ref": evidence_case_ref,
                "evidence_sufficiency": {
                    "status": getattr(evidence_case, "status", None),
                    "score": getattr(evidence_case, "sufficiency_score", None),
                    "unresolved_gaps": list(getattr(evidence_case, "unresolved_gaps", []) or []),
                },
                "authorization_plan_ref": authorization_plan_ref,
                "authority_scope": dict(
                    getattr(authorization_plan, "proposed_grant_shape", {}) or {}
                ),
                "approval_route": getattr(authorization_plan, "approval_route", None),
                "current_gaps": list(getattr(authorization_plan, "current_gaps", []) or []),
                "drift_expiry": contract.expiry_at,
                "rollback_expectation": contract.rollback_expectation,
                "operator_summary": contract.operator_summary,
            }
            packet.setdefault(
                "title",
                f"Confirm {action_request.action_class.replace('_', ' ').title()} Contract",
            )
            packet.setdefault(
                "summary",
                contract.operator_summary
                or policy.reason
                or f"{action_request.tool_name} requires explicit approval.",
            )
            packet["contract_packet"] = contract_packet
        return {
            "tool_name": action_request.tool_name,
            "tool_input": action_request.tool_input,
            "risk_level": policy.risk_level,
            "reason": policy.reason,
            "fingerprint": fingerprint,
            "resource_scopes": list(action_request.resource_scopes),
            "target_paths": list(action_request.derived.get("target_paths", [])),
            "network_hosts": list(action_request.derived.get("network_hosts", [])),
            "command_preview": action_request.derived.get("command_preview"),
            "workspace_root": action_request.context.get("workspace_root", ""),
            "outside_workspace": bool(action_request.derived.get("outside_workspace")),
            "grant_scope_dir": action_request.derived.get("grant_candidate_prefix"),
            "approval_packet": packet,
            "contract_packet": contract_packet,
            "contract_ref": contract_ref,
            "evidence_case_ref": evidence_case_ref,
            "authorization_plan_ref": authorization_plan_ref,
            "decision_ref": decision_ref,
            "policy_ref": policy_ref,
            "state_witness_ref": state_witness_ref,
            "idempotency_key": action_request.idempotency_key,
        }
