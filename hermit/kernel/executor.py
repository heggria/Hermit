from __future__ import annotations

import difflib
import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from hermit.core.tools import ToolRegistry, ToolSpec, serialize_tool_result
from hermit.i18n import resolve_locale, tr
from hermit.kernel.approval_copy import ApprovalCopyService
from hermit.kernel.approvals import ApprovalService
from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.contracts import contract_for
from hermit.kernel.context import TaskExecutionContext, capture_execution_environment
from hermit.kernel.decisions import DecisionService
from hermit.kernel.path_grants import PathGrantService
from hermit.kernel.permits import CapabilityGrantError, ExecutionPermitService
from hermit.kernel.observation import (
    ObservationProgress,
    ObservationPollResult,
    ObservationTicket,
    normalize_observation_progress,
    normalize_observation_ticket,
)
from hermit.kernel.progress_summary import (
    ProgressSummary,
    ProgressSummaryFormatter,
    normalize_progress_summary,
)
from hermit.kernel.policy import (
    POLICY_RULES_VERSION,
    ActionRequest,
    PolicyDecision,
    PolicyEngine,
    build_action_fingerprint,
)
from hermit.kernel.receipts import ReceiptService
from hermit.kernel.reconcile import ReconcileService
from hermit.kernel.store import KernelStore

_BLOCK_TYPES = {"text", "image"}
_RUNTIME_SNAPSHOT_KEY = "runtime_snapshot"
_PENDING_EXECUTION_KEY = "pending_observation_execution"
_RUNTIME_SNAPSHOT_SCHEMA_VERSION = 2
_RUNTIME_SNAPSHOT_TTL_SECONDS = 24 * 60 * 60
_RUNTIME_SNAPSHOT_MAX_BYTES = 256 * 1024
_RUNTIME_SNAPSHOT_V1_ALLOWED_KEYS = {
    "messages",
    "pending_tool_blocks",
    "tool_result_blocks",
    "next_turn",
    "disable_tools",
    "readonly_only",
}
_RUNTIME_SNAPSHOT_V2_ALLOWED_KEYS = {
    "suspend_kind",
    "resume_messages_ref",
    "pending_tool_blocks",
    "tool_result_blocks",
    "next_turn",
    "disable_tools",
    "readonly_only",
    "note_cursor_event_seq",
    "observation",
}
_RUNTIME_SNAPSHOT_V3_ALLOWED_KEYS = {
    "suspend_kind",
    "resume_messages_ref",
    "pending_tool_blocks",
    "tool_result_blocks",
    "next_turn",
    "disable_tools",
    "readonly_only",
    "note_cursor_event_seq",
    "observation",
}
_WITNESS_REQUIRED_ACTIONS = {
    "write_local",
    "patch_file",
    "execute_command",
    "network_write",
    "credentialed_api_call",
    "vcs_mutation",
    "publication",
    "memory_write",
}


def _t(message_key: str, *, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=resolve_locale(), default=default, **kwargs)


def _truncate_middle(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 32:
        return text[:limit]
    head = max(1, limit // 2 - 8)
    tail = max(1, limit - head - len("\n...\n"))
    return f"{text[:head]}\n...\n{text[-tail:]}"


def _format_model_content(value: Any, limit: int) -> Any:
    serialized = serialize_tool_result(value)
    if isinstance(serialized, str):
        return _truncate_middle(serialized, limit)
    if isinstance(serialized, dict) and serialized.get("type") in _BLOCK_TYPES:
        return [serialized]
    if isinstance(serialized, list) and all(isinstance(item, dict) and item.get("type") in _BLOCK_TYPES for item in serialized):
        return serialized
    text = json.dumps(serialized, ensure_ascii=True, indent=2, sort_keys=True)
    return _truncate_middle(text, limit)


def _progress_signature(value: dict[str, Any] | None) -> tuple[str, str, str | None, int | None, bool] | None:
    progress = normalize_observation_progress(value)
    if progress is None:
        return None
    return progress.signature()


def _progress_summary_signature(value: dict[str, Any] | None) -> tuple[str, str | None, str | None, int | None] | None:
    summary = normalize_progress_summary(value)
    if summary is None:
        return None
    return summary.signature()


def _compact_progress_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _is_governed_action(tool: ToolSpec, policy: PolicyDecision) -> bool:
    if tool.readonly and policy.verdict == "allow":
        return False
    if policy.action_class in {"read_local", "network_read"} and not policy.requires_receipt:
        return False
    if policy.action_class == "ephemeral_ui_mutation":
        return False
    return True


def _needs_witness(action_class: str) -> bool:
    return action_class in _WITNESS_REQUIRED_ACTIONS


def _execution_status_from_result_code(result_code: str) -> str:
    if result_code in {"approval_required"}:
        return "awaiting_approval"
    if result_code in {"observation_submitted"}:
        return "observing"
    if result_code in {"denied"}:
        return "failed"
    if result_code in {"failed", "timeout", "cancelled"}:
        return "failed"
    if result_code in {"reconciled_applied", "reconciled_not_applied", "reconciled_observed"}:
        return "reconciling"
    if result_code == "unknown_outcome":
        return "needs_attention"
    return "succeeded"


@dataclass
class ToolExecutionResult:
    model_content: Any
    raw_result: Any = None
    blocked: bool = False
    suspended: bool = False
    waiting_kind: str | None = None
    denied: bool = False
    approval_id: str | None = None
    approval_message: str | None = None
    observation: ObservationTicket | None = None
    policy_decision: PolicyDecision | None = None
    receipt_id: str | None = None
    decision_id: str | None = None
    permit_id: str | None = None
    grant_ref: str | None = None
    policy_ref: str | None = None
    witness_ref: str | None = None
    result_code: str = "succeeded"
    execution_status: str = "succeeded"
    state_applied: bool = False


class ToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        store: KernelStore,
        artifact_store: ArtifactStore,
        policy_engine: PolicyEngine,
        approval_service: ApprovalService,
        approval_copy_service: ApprovalCopyService | None = None,
        receipt_service: ReceiptService,
        decision_service: DecisionService | None = None,
        path_grant_service: PathGrantService | None = None,
        permit_service: ExecutionPermitService | None = None,
        reconcile_service: ReconcileService | None = None,
        progress_summarizer: ProgressSummaryFormatter | None = None,
        progress_summary_keepalive_seconds: float = 15.0,
        tool_output_limit: int = 4000,
    ) -> None:
        self.registry = registry
        self.store = store
        self.artifact_store = artifact_store
        self.policy_engine = policy_engine
        self.approval_service = approval_service
        self.approval_copy = approval_copy_service or ApprovalCopyService()
        self.receipt_service = receipt_service
        self.decision_service = decision_service or DecisionService(store)
        self.path_grant_service = path_grant_service or PathGrantService(store)
        self.permit_service = permit_service or ExecutionPermitService(store)
        self.reconcile_service = reconcile_service or ReconcileService()
        self.progress_summarizer = progress_summarizer
        self.progress_summary_keepalive_seconds = max(float(progress_summary_keepalive_seconds or 0.0), 0.0)
        self.tool_output_limit = tool_output_limit

    def execute(
        self,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        request_overrides: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        tool = self.registry.get(tool_name)
        action_request = self.policy_engine.build_action_request(tool, tool_input, attempt_ctx=attempt_ctx)
        if request_overrides:
            action_request = self._apply_request_overrides(action_request, request_overrides)
        matched_grant = self._matching_path_grant(action_request)
        if matched_grant is not None:
            action_request.context["path_grant_ref"] = matched_grant.grant_id
            action_request.context["path_grant_prefix"] = matched_grant.path_prefix
        action_ref = self._record_action_request(action_request, attempt_ctx)
        policy = self.policy_engine.evaluate(action_request)
        policy_ref = self._record_policy_evaluation(action_request, policy, attempt_ctx)
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        governed = _is_governed_action(tool, policy)

        attempt_record = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        approval_record = None
        if attempt_record is not None and attempt_record.approval_id:
            approval_record = self.store.get_approval(attempt_record.approval_id)

        preview_artifact = None
        if policy.obligations.require_preview:
            preview_artifact = self._build_preview_artifact(tool, tool_input, attempt_ctx)

        matched_approval, witness_ref, witness_drift = self._matching_approval(
            approval_record,
            action_request,
            policy,
            preview_artifact,
            attempt_ctx=attempt_ctx,
        )
        if witness_drift:
            return self._supersede_attempt_for_witness_drift(
                attempt_ctx=attempt_ctx,
                tool_name=tool_name,
                tool_input=tool_input,
            )

        if policy.verdict == "deny":
            decision_id = self.decision_service.record(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                decision_type="policy_gate",
                verdict="deny",
                reason=policy.reason or f"{tool_name} denied by policy.",
                evidence_refs=[ref for ref in [action_ref, policy_ref, preview_artifact] if ref],
                policy_ref=policy_ref,
                action_type=action_type,
            )
            message = f"[Policy Denied] {policy.reason or f'{tool_name} denied by policy.'}"
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                status="failed",
                waiting_reason=policy.reason,
                approval_id=None,
                decision_id=decision_id,
                state_witness_ref=witness_ref,
            )
            self.store.update_step(attempt_ctx.step_id, status="failed")
            self.store.update_task_status(attempt_ctx.task_id, "failed")
            self.store.append_event(
                event_type="policy.denied",
                entity_type="step_attempt",
                entity_id=attempt_ctx.step_attempt_id,
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                actor="kernel",
                payload={
                    "tool_name": tool_name,
                    "policy_ref": policy_ref,
                    "decision_ref": decision_id,
                    "policy": policy.to_dict(),
                },
            )
            return ToolExecutionResult(
                model_content=message,
                raw_result=message,
                denied=True,
                policy_decision=policy,
                decision_id=decision_id,
                policy_ref=policy_ref,
                witness_ref=witness_ref,
                result_code="denied",
                execution_status="failed",
                state_applied=True,
            )

        if policy.obligations.require_approval and matched_approval is None:
            if witness_ref is None and _needs_witness(action_type):
                witness_ref = self._capture_state_witness(action_request, attempt_ctx)
            decision_id = self.decision_service.record(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                decision_type="policy_gate",
                verdict="require_approval",
                reason=policy.reason or "Approval required before execution.",
                evidence_refs=[ref for ref in [action_ref, policy_ref, preview_artifact, witness_ref] if ref],
                policy_ref=policy_ref,
                action_type=action_type,
            )
            requested_action = self._requested_action_payload(
                action_request,
                policy,
                preview_artifact,
                decision_ref=decision_id,
                policy_ref=policy_ref,
                state_witness_ref=witness_ref,
            )
            requested_action["display_copy"] = self.approval_copy.build_canonical_copy(requested_action)
            approval_id = self.approval_service.request(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                approval_type=action_type,
                requested_action=requested_action,
                request_packet_ref=preview_artifact,
                decision_ref=decision_id,
                state_witness_ref=witness_ref,
            )
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                status="awaiting_approval",
                waiting_reason=policy.reason,
                approval_id=approval_id,
                decision_id=decision_id,
                state_witness_ref=witness_ref,
            )
            self.store.update_step(attempt_ctx.step_id, status="blocked")
            self.store.update_task_status(attempt_ctx.task_id, "blocked")
            blocked_message = self.approval_copy.model_prompt(requested_action, approval_id)
            approval_message = self.approval_copy.blocked_message(requested_action, approval_id)
            return ToolExecutionResult(
                model_content=blocked_message,
                blocked=True,
                suspended=True,
                waiting_kind="awaiting_approval",
                approval_id=approval_id,
                approval_message=approval_message,
                policy_decision=policy,
                decision_id=decision_id,
                policy_ref=policy_ref,
                witness_ref=witness_ref,
                result_code="approval_required",
                execution_status="awaiting_approval",
                state_applied=True,
            )

        decision_id = None
        permit_id = None
        grant_id = matched_grant.grant_id if matched_grant is not None else None
        approval_mode = ""
        if matched_approval is not None:
            approval_mode = str((matched_approval.resolution or {}).get("mode", "once") or "once")
            self.decision_service.record(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                decision_type="approval_resolution",
                verdict=approval_mode,
                reason="User approval was applied before execution.",
                policy_ref=policy_ref,
                approval_ref=matched_approval.approval_id,
                action_type=action_type,
                decided_by=str(matched_approval.resolved_by or "user"),
            )
            if approval_mode == "always_directory" and grant_id is None:
                grant_id = self._ensure_directory_grant(
                    approval_record=matched_approval,
                    action_request=action_request,
                    policy_ref=policy_ref,
                )
        if governed:
            decision_id = self.decision_service.record(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                decision_type="execution_authorization",
                verdict="allow",
                reason=self._authorization_reason(policy=policy, approval_mode=approval_mode, grant_id=grant_id),
                evidence_refs=[ref for ref in [action_ref, policy_ref, preview_artifact, witness_ref] if ref],
                policy_ref=policy_ref,
                approval_ref=matched_approval.approval_id if matched_approval is not None else None,
                action_type=action_type,
            )
            permit_id = self.permit_service.issue(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                decision_ref=decision_id,
                approval_ref=matched_approval.approval_id if matched_approval is not None else None,
                policy_ref=policy_ref,
                action_class=action_type,
                resource_scope=list(action_request.resource_scopes),
                idempotency_key=action_request.idempotency_key,
                constraints=self._permit_constraints(action_request, grant_ref=grant_id),
            )
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                status="dispatching",
                waiting_reason=None,
                decision_id=decision_id,
                permit_id=permit_id,
                state_witness_ref=witness_ref,
            )
            self.store.update_step(attempt_ctx.step_id, status="dispatching")

        if governed and permit_id is not None:
            try:
                self.permit_service.enforce(
                    permit_id,
                    action_class=action_type,
                    resource_scope=list(action_request.resource_scopes),
                    constraints=self._permit_constraints(action_request, grant_ref=grant_id),
                )
            except CapabilityGrantError as exc:
                return self._handle_dispatch_denied(
                    tool=tool,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    attempt_ctx=attempt_ctx,
                    policy=policy,
                    policy_ref=policy_ref,
                    decision_id=decision_id,
                    permit_id=permit_id,
                    grant_ref=grant_id,
                    approval_ref=matched_approval.approval_id if matched_approval is not None else None,
                    witness_ref=witness_ref,
                    error=exc,
                    idempotency_key=action_request.idempotency_key,
                )
            if grant_id is not None:
                self.path_grant_service.mark_used(grant_id)
            if matched_approval is not None:
                self.store.consume_approval(matched_approval.approval_id)

        rollback_plan = self._prepare_rollback_plan(
            action_type=action_type,
            tool_name=tool_name,
            tool_input=tool_input,
            attempt_ctx=attempt_ctx,
        )

        try:
            raw_result = tool.handler(tool_input)
        except Exception as exc:
            if governed and permit_id is not None:
                self.permit_service.mark_uncertain(permit_id)
                return self._handle_uncertain_outcome(
                    tool=tool,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    attempt_ctx=attempt_ctx,
                    policy=policy,
                    policy_ref=policy_ref,
                    decision_id=decision_id,
                    permit_id=permit_id,
                    grant_ref=grant_id,
                    approval_ref=matched_approval.approval_id if matched_approval is not None else None,
                    witness_ref=witness_ref,
                    exc=exc,
                    idempotency_key=action_request.idempotency_key,
                    action_request=action_request,
                )
            raise

        observation = normalize_observation_ticket(raw_result)
        if observation is not None:
            observation.tool_name = tool_name
            observation.tool_input = dict(tool_input)
            return self._handle_observation_submission(
                tool=tool,
                tool_name=tool_name,
                tool_input=tool_input,
                attempt_ctx=attempt_ctx,
                observation=observation,
                policy=policy,
                policy_ref=policy_ref,
                decision_id=decision_id,
                permit_id=permit_id,
                grant_ref=grant_id,
                approval_ref=matched_approval.approval_id if matched_approval is not None else None,
                witness_ref=witness_ref,
                action_request=action_request,
                approval_mode=approval_mode,
                rollback_plan=rollback_plan,
            )

        model_content = _format_model_content(raw_result, self.tool_output_limit)
        receipt_id = None
        if governed:
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                status="receipt_pending",
                decision_id=decision_id,
                permit_id=permit_id,
                state_witness_ref=witness_ref,
            )
            self.store.update_step(attempt_ctx.step_id, status="receipt_pending")
        if policy.requires_receipt:
            if permit_id is not None:
                self.permit_service.consume(permit_id)
            receipt_id = self._issue_receipt(
                tool=tool,
                tool_name=tool_name,
                tool_input=tool_input,
                raw_result=raw_result,
                attempt_ctx=attempt_ctx,
                approval_ref=matched_approval.approval_id if matched_approval is not None else None,
                policy=policy,
                policy_ref=policy_ref,
                decision_ref=decision_id,
                permit_ref=permit_id,
                grant_ref=grant_id,
                witness_ref=witness_ref,
                result_code="succeeded",
                idempotency_key=action_request.idempotency_key,
                result_summary=self._successful_result_summary(
                    tool_name=tool_name,
                    approval_mode=approval_mode,
                    grant_id=grant_id,
                ),
                rollback_supported=rollback_plan["supported"],
                rollback_strategy=rollback_plan["strategy"],
                rollback_artifact_refs=rollback_plan["artifact_refs"],
            )
        return ToolExecutionResult(
            model_content=model_content,
            raw_result=raw_result,
            blocked=False,
            approval_id=matched_approval.approval_id if matched_approval else None,
            policy_decision=policy,
            receipt_id=receipt_id,
            decision_id=decision_id,
            permit_id=permit_id,
            grant_ref=grant_id,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            result_code="succeeded",
            execution_status="succeeded",
        )

    def persist_suspended_state(
        self,
        attempt_ctx: TaskExecutionContext,
        *,
        suspend_kind: str,
        pending_tool_blocks: list[dict[str, Any]],
        tool_result_blocks: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        next_turn: int,
        disable_tools: bool,
        readonly_only: bool,
        note_cursor_event_seq: int = 0,
        observation: ObservationTicket | None = None,
    ) -> None:
        resume_messages_ref = self._store_resume_messages(messages, attempt_ctx=attempt_ctx)
        payload = {
            "suspend_kind": suspend_kind,
            "resume_messages_ref": resume_messages_ref,
            "pending_tool_blocks": pending_tool_blocks,
            "tool_result_blocks": tool_result_blocks,
            "next_turn": next_turn,
            "disable_tools": disable_tools,
            "readonly_only": readonly_only,
            "note_cursor_event_seq": note_cursor_event_seq,
            "observation": observation.to_dict() if observation is not None else None,
        }
        envelope = self._runtime_snapshot_envelope(payload)
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        if attempt_ctx.workspace_root:
            context["workspace_root"] = attempt_ctx.workspace_root
        context["note_cursor_event_seq"] = note_cursor_event_seq
        context[_RUNTIME_SNAPSHOT_KEY] = envelope
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status=suspend_kind,
            context=context,
        )

    def persist_blocked_state(
        self,
        attempt_ctx: TaskExecutionContext,
        *,
        pending_tool_blocks: list[dict[str, Any]],
        tool_result_blocks: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        next_turn: int,
        disable_tools: bool,
        readonly_only: bool,
    ) -> None:
        self.persist_suspended_state(
            attempt_ctx,
            suspend_kind="awaiting_approval",
            pending_tool_blocks=pending_tool_blocks,
            tool_result_blocks=tool_result_blocks,
            messages=messages,
            next_turn=next_turn,
            disable_tools=disable_tools,
            readonly_only=readonly_only,
        )

    def load_suspended_state(self, step_attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            raise KeyError(
                _t(
                    "kernel.executor.error.unknown_step_attempt",
                    default="Unknown step attempt: {step_attempt_id}",
                    step_attempt_id=step_attempt_id,
                )
            )
        envelope = dict(attempt.context.get(_RUNTIME_SNAPSHOT_KEY, {}))
        if not envelope:
            return {}
        payload = self._runtime_snapshot_payload(envelope)
        if "messages" not in payload:
            resume_messages_ref = str(payload.get("resume_messages_ref", "") or "").strip()
            payload["messages"] = self._load_resume_messages(resume_messages_ref)
        return payload

    def load_blocked_state(self, step_attempt_id: str) -> dict[str, Any]:
        return self.load_suspended_state(step_attempt_id)

    def clear_suspended_state(self, step_attempt_id: str) -> None:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        context = dict(attempt.context)
        context.pop(_RUNTIME_SNAPSHOT_KEY, None)
        context.pop(_PENDING_EXECUTION_KEY, None)
        self.store.update_step_attempt(step_attempt_id, context=context, waiting_reason=None)

    def clear_blocked_state(self, step_attempt_id: str) -> None:
        self.clear_suspended_state(step_attempt_id)

    def _runtime_snapshot_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        unknown = set(payload) - _RUNTIME_SNAPSHOT_V3_ALLOWED_KEYS
        if unknown:
            raise RuntimeError(
                _t(
                    "kernel.executor.error.unsupported_working_state_keys",
                    default="Unsupported working-state keys: {keys}",
                    keys=sorted(unknown),
                )
            )
        envelope = {
            "schema_version": _RUNTIME_SNAPSHOT_SCHEMA_VERSION,
            "kind": _RUNTIME_SNAPSHOT_KEY,
            "expires_at": time.time() + _RUNTIME_SNAPSHOT_TTL_SECONDS,
            "payload": payload,
        }
        encoded = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        if len(encoded) > _RUNTIME_SNAPSHOT_MAX_BYTES:
            raise RuntimeError(
                _t(
                    "kernel.executor.error.snapshot_too_large",
                    default="Runtime snapshot exceeds working-state size limit",
                )
            )
        return envelope

    def _runtime_snapshot_payload(self, envelope: dict[str, Any]) -> dict[str, Any]:
        schema_version = int(envelope.get("schema_version", 0))
        if schema_version not in {1, 2, _RUNTIME_SNAPSHOT_SCHEMA_VERSION}:
            raise RuntimeError(
                _t(
                    "kernel.executor.error.unsupported_snapshot_schema",
                    default="Unsupported runtime snapshot schema version",
                )
            )
        if str(envelope.get("kind", "")) != _RUNTIME_SNAPSHOT_KEY:
            raise RuntimeError(
                _t(
                    "kernel.executor.error.invalid_snapshot_kind",
                    default="Invalid runtime snapshot kind",
                )
            )
        expires_at = float(envelope.get("expires_at", 0) or 0)
        if expires_at and expires_at < time.time():
            raise RuntimeError(
                _t(
                    "kernel.executor.error.snapshot_expired",
                    default="Runtime snapshot expired",
                )
            )
        payload = dict(envelope.get("payload", {}))
        allowed_keys = (
            _RUNTIME_SNAPSHOT_V1_ALLOWED_KEYS
            if schema_version == 1
            else _RUNTIME_SNAPSHOT_V2_ALLOWED_KEYS
            if schema_version == 2
            else _RUNTIME_SNAPSHOT_V3_ALLOWED_KEYS
        )
        unknown = set(payload) - allowed_keys
        if unknown:
            raise RuntimeError(
                _t(
                    "kernel.executor.error.snapshot_contains_unsupported_keys",
                    default="Runtime snapshot contains unsupported keys: {keys}",
                    keys=sorted(unknown),
                )
            )
        encoded = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        if len(encoded) > _RUNTIME_SNAPSHOT_MAX_BYTES:
            raise RuntimeError(
                _t(
                    "kernel.executor.error.snapshot_too_large",
                    default="Runtime snapshot exceeds working-state size limit",
                )
            )
        return payload

    def _store_resume_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        return self._store_json_artifact(
            payload=messages,
            kind="runtime.resume_messages",
            attempt_ctx=attempt_ctx,
            metadata={"message_count": len(messages)},
        )

    def _load_resume_messages(self, resume_messages_ref: str) -> list[dict[str, Any]]:
        artifact = self.store.get_artifact(resume_messages_ref)
        if artifact is None:
            raise RuntimeError(
                _t(
                    "kernel.executor.error.unknown_resume_messages_artifact",
                    default="Unknown resume messages artifact: {resume_messages_ref}",
                    resume_messages_ref=resume_messages_ref,
                )
            )
        payload = json.loads(self.artifact_store.read_text(artifact.uri))
        if not isinstance(payload, list):
            raise RuntimeError(
                _t(
                    "kernel.executor.error.resume_messages_not_list",
                    default="Runtime resume messages artifact is not a list",
                )
            )
        return [dict(message) for message in payload if isinstance(message, dict)]

    def current_note_cursor(self, step_attempt_id: str) -> int:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return 0
        return int(attempt.context.get("note_cursor_event_seq", 0) or 0)

    def consume_appended_notes(self, attempt_ctx: TaskExecutionContext) -> tuple[list[dict[str, Any]], int]:
        cursor = self.current_note_cursor(attempt_ctx.step_attempt_id)
        events = self.store.list_events(
            task_id=attempt_ctx.task_id,
            after_event_seq=cursor,
            limit=200,
        )
        note_events = [event for event in events if event["event_type"] == "task.note.appended"]
        if not note_events:
            return [], cursor
        latest = int(note_events[-1]["event_seq"])
        messages = []
        for event in note_events:
            payload = dict(event.get("payload", {}) or {})
            prompt = str(payload.get("prompt", "") or payload.get("raw_text", "")).strip()
            if not prompt:
                continue
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "[Task Note Appended]\n"
                        f"{prompt}"
                    ),
                }
            )
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        context["note_cursor_event_seq"] = latest
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, context=context)
        return messages, latest

    def _store_pending_execution(self, attempt_ctx: TaskExecutionContext, payload: dict[str, Any]) -> None:
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        context[_PENDING_EXECUTION_KEY] = payload
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            context=context,
            decision_id=str(payload.get("decision_id", "") or "") or None,
            permit_id=str(payload.get("permit_id", "") or "") or None,
            state_witness_ref=str(payload.get("witness_ref", "") or "") or None,
        )

    def _load_pending_execution(self, step_attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return {}
        payload = attempt.context.get(_PENDING_EXECUTION_KEY, {})
        return dict(payload) if isinstance(payload, dict) else {}

    def _clear_pending_execution(self, step_attempt_id: str) -> None:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        context = dict(attempt.context)
        context.pop(_PENDING_EXECUTION_KEY, None)
        self.store.update_step_attempt(step_attempt_id, context=context)

    def _handle_observation_submission(
        self,
        *,
        tool: ToolSpec,
        tool_name: str,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
        observation: ObservationTicket,
        policy: PolicyDecision,
        policy_ref: str | None,
        decision_id: str | None,
        permit_id: str | None,
        grant_ref: str | None,
        approval_ref: str | None,
        witness_ref: str | None,
        action_request: ActionRequest,
        approval_mode: str,
        rollback_plan: dict[str, Any],
    ) -> ToolExecutionResult:
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        self._store_pending_execution(
            attempt_ctx,
            {
                "tool_name": tool_name,
                "tool_input": dict(tool_input),
                "action_type": action_type,
                "policy": policy.to_dict(),
                "policy_ref": policy_ref,
                "decision_id": decision_id,
                "permit_id": permit_id,
                "grant_ref": grant_ref,
                "approval_ref": approval_ref,
                "witness_ref": witness_ref,
                "idempotency_key": action_request.idempotency_key,
                "approval_mode": approval_mode,
                "rollback_plan": rollback_plan,
            },
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="observing",
            waiting_reason=observation.topic_summary,
            decision_id=decision_id,
            permit_id=permit_id,
            state_witness_ref=witness_ref,
        )
        self.store.update_step(attempt_ctx.step_id, status="blocked")
        self.store.update_task_status(attempt_ctx.task_id, "blocked")
        self.store.append_event(
            event_type="tool.submitted",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "tool_name": tool_name,
                "observer_kind": observation.observer_kind,
                "job_id": observation.job_id,
                "status_ref": observation.status_ref,
                "display_name": observation.display_name or tool_name,
                "topic_summary": observation.topic_summary,
                "poll_after_seconds": observation.poll_after_seconds,
                "ready_return": observation.ready_return,
            },
        )
        return ToolExecutionResult(
            model_content=observation.topic_summary,
            raw_result={"job_id": observation.job_id, "status_ref": observation.status_ref},
            blocked=True,
            suspended=True,
            waiting_kind="observing",
            observation=observation,
            approval_id=approval_ref,
            policy_decision=policy,
            decision_id=decision_id,
            permit_id=permit_id,
            grant_ref=grant_ref,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            result_code="observation_submitted",
            execution_status="observing",
            state_applied=True,
        )

    def _poll_tool_call_observation(self, ticket: ObservationTicket) -> dict[str, Any]:
        tool_name = ticket.status_tool_name or ticket.tool_name
        if not tool_name:
            return {
                "status": "failed",
                "topic_summary": "Observation ticket is missing a status tool name.",
                "result": {"error": "missing status tool"},
                "is_error": True,
            }
        tool = self.registry.get(tool_name)
        payload = dict(ticket.status_tool_input or {})
        payload.setdefault("job_id", ticket.job_id)
        payload.setdefault("status_ref", ticket.status_ref)
        result = tool.handler(payload)
        nested = normalize_observation_ticket(result)
        if nested is not None:
            return {
                "status": "observing",
                "topic_summary": nested.topic_summary,
                "poll_after_seconds": nested.poll_after_seconds,
                "progress": nested.progress,
            }
        return result

    def _poll_ticket(self, ticket: ObservationTicket) -> dict[str, Any]:
        if ticket.observer_kind == "local_process":
            sandbox = getattr(getattr(self.registry, "_tools", {}).get("bash"), "handler", None)
            sandbox_self = getattr(sandbox, "_sandbox", None) or getattr(sandbox, "__self__", None)
            if sandbox_self is None or not hasattr(sandbox_self, "poll"):
                return {
                    "status": "failed",
                    "topic_summary": f"Observation handler unavailable for job {ticket.job_id}.",
                    "result": {"error": "local process observer unavailable"},
                    "is_error": True,
                }
            return sandbox_self.poll(ticket.job_id)
        if ticket.observer_kind == "tool_call":
            return self._poll_tool_call_observation(ticket)
        return {
            "status": "failed",
            "topic_summary": f"Unsupported observer kind: {ticket.observer_kind}",
            "result": {"error": f"unsupported observer kind: {ticket.observer_kind}"},
            "is_error": True,
        }

    def finalize_observation(
        self,
        attempt_ctx: TaskExecutionContext,
        *,
        terminal_status: str,
        raw_result: Any,
        is_error: bool,
        summary: str,
        model_content_override: Any = None,
    ) -> dict[str, Any]:
        pending = self._load_pending_execution(attempt_ctx.step_attempt_id)
        if not pending:
            model_content = (
                model_content_override
                if model_content_override is not None
                else _format_model_content(raw_result, self.tool_output_limit)
            )
            return {
                "raw_result": raw_result,
                "model_content": model_content,
                "is_error": is_error,
                "result_code": terminal_status,
            }
        tool_name = str(pending.get("tool_name", ""))
        tool_input = dict(pending.get("tool_input", {}) or {})
        tool = self.registry.get(tool_name)
        policy = PolicyDecision.from_dict(dict(pending.get("policy", {}) or {}))
        policy_ref = str(pending.get("policy_ref", "") or "") or None
        decision_ref = str(pending.get("decision_id", "") or "") or None
        permit_ref = str(pending.get("permit_id", "") or "") or None
        grant_ref = str(pending.get("grant_ref", "") or "") or None
        approval_ref = str(pending.get("approval_ref", "") or "") or None
        witness_ref = str(pending.get("witness_ref", "") or "") or None
        approval_mode = str(pending.get("approval_mode", "") or "")
        rollback_plan = dict(pending.get("rollback_plan", {}) or {})

        result_code = terminal_status if terminal_status in {"failed", "timeout", "cancelled"} else "succeeded"
        model_content = (
            model_content_override
            if model_content_override is not None
            else _format_model_content(raw_result, self.tool_output_limit)
        )
        if policy.requires_receipt:
            if permit_ref and result_code == "succeeded":
                self.permit_service.consume(permit_ref)
            self._issue_receipt(
                tool=tool,
                tool_name=tool_name,
                tool_input=tool_input,
                raw_result=raw_result,
                attempt_ctx=attempt_ctx,
                approval_ref=approval_ref,
                policy=policy,
                policy_ref=policy_ref,
                decision_ref=decision_ref,
                permit_ref=permit_ref,
                grant_ref=grant_ref,
                witness_ref=witness_ref,
                result_code=result_code,
                idempotency_key=str(pending.get("idempotency_key", "") or "") or None,
                result_summary=summary if result_code != "succeeded" else self._successful_result_summary(
                    tool_name=tool_name,
                    approval_mode=approval_mode,
                    grant_id=grant_ref,
                ),
                output_kind="tool_error" if is_error else "tool_output",
                rollback_supported=bool(rollback_plan.get("supported", False)),
                rollback_strategy=str(rollback_plan.get("strategy", "") or "") or None,
                rollback_artifact_refs=list(rollback_plan.get("artifact_refs", []) or []),
            )
        self._clear_pending_execution(attempt_ctx.step_attempt_id)
        return {
            "raw_result": raw_result,
            "model_content": model_content if not is_error else f"Error: {summary}",
            "is_error": is_error,
            "result_code": result_code,
        }

    def _progress_summary_facts(
        self,
        *,
        task_id: str,
        step_attempt_id: str,
        ticket: ObservationTicket,
        status: str,
        progress: ObservationProgress | None,
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        events = self.store.list_events(task_id=task_id, limit=500)[-80:]
        relevant_types = {
            "task.created",
            "task.note.appended",
            "tool.submitted",
            "tool.progressed",
            "tool.status.changed",
            "approval.requested",
            "approval.granted",
            "approval.denied",
            "approval.consumed",
        }
        recent_events: list[dict[str, Any]] = []
        for event in events:
            if event["event_type"] not in relevant_types:
                continue
            payload = dict(event.get("payload", {}) or {})
            text = ""
            if event["event_type"] == "task.note.appended":
                text = str(payload.get("raw_text", "") or payload.get("prompt", "")).strip()
            else:
                text = (
                    str(payload.get("summary", "") or "")
                    or str(payload.get("topic_summary", "") or "")
                    or str(payload.get("detail", "") or "")
                    or str(payload.get("status", "") or "")
                ).strip()
            phase = str(payload.get("phase", "") or "").strip()
            recent_events.append(
                {
                    "event_type": event["event_type"],
                    "text": _compact_progress_text(text, limit=180),
                    "phase": phase or None,
                    "progress_percent": payload.get("progress_percent"),
                }
            )
        latest_progress = progress or normalize_observation_progress(ticket.progress)
        return {
            "task": {
                "title": _compact_progress_text(getattr(task, "title", ""), limit=120),
                "goal": _compact_progress_text(getattr(task, "goal", ""), limit=600),
                "status": status,
                "source_channel": getattr(task, "source_channel", "") if task is not None else "",
            },
            "attempt": {
                "step_attempt_id": step_attempt_id,
                "tool_name": ticket.tool_name,
                "display_name": ticket.display_name or ticket.tool_name or "observed task",
                "topic_summary": ticket.topic_summary,
                "observer_kind": ticket.observer_kind,
            },
            "progress": latest_progress.to_dict() if latest_progress is not None else None,
            "recent_events": recent_events[-8:],
        }

    def _maybe_emit_progress_summary(
        self,
        *,
        step_attempt_id: str,
        task_id: str | None,
        step_id: str | None,
        ticket: ObservationTicket,
        status: str,
        progress: ObservationProgress | None,
        progress_changed: bool,
        now: float,
    ) -> None:
        if self.progress_summarizer is None or not task_id:
            return
        keepalive_due = (
            status == "observing"
            and self.progress_summary_keepalive_seconds > 0
            and ticket.last_progress_summary_at is not None
            and (now - ticket.last_progress_summary_at) >= self.progress_summary_keepalive_seconds
        )
        if not progress_changed and not keepalive_due:
            return
        try:
            summary = self.progress_summarizer.summarize(
                facts=self._progress_summary_facts(
                    task_id=task_id,
                    step_attempt_id=step_attempt_id,
                    ticket=ticket,
                    status=status,
                    progress=progress,
                )
            )
        except Exception:
            return
        if summary is None or not summary.summary.strip():
            return
        if not summary.phase:
            if progress is not None and progress.phase:
                summary.phase = progress.phase
            elif status:
                summary.phase = status
        if summary.progress_percent is None and progress is not None:
            summary.progress_percent = progress.progress_percent
        previous_signature = _progress_summary_signature(ticket.progress_summary)
        current_signature = summary.signature()
        ticket.last_progress_summary_at = now
        if current_signature == previous_signature:
            return
        ticket.progress_summary = summary.to_dict()
        self.store.append_event(
            event_type="task.progress.summarized",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            step_id=step_id,
            actor="kernel",
            payload={
                **summary.to_dict(),
                "job_id": ticket.job_id,
                "status": status,
            },
        )

    def poll_observation(self, step_attempt_id: str, *, now: float | None = None) -> ObservationPollResult | None:
        payload = self.load_suspended_state(step_attempt_id)
        if str(payload.get("suspend_kind", "")) != "observing":
            return None
        observation_data = payload.get("observation")
        if not isinstance(observation_data, dict):
            return None
        ticket = ObservationTicket.from_dict(observation_data)
        current = time.time() if now is None else now
        if ticket.next_poll_at and current < ticket.next_poll_at:
            return ObservationPollResult(ticket=ticket, should_resume=False)

        status_payload = self._poll_ticket(ticket)
        status = str(status_payload.get("status", "observing") or "observing")
        progress = normalize_observation_progress(status_payload.get("progress"))
        summary = str(status_payload.get("topic_summary", ticket.topic_summary) or ticket.topic_summary)
        if progress is not None and progress.summary:
            summary = progress.summary
        task_attempt = self.store.get_step_attempt(step_attempt_id)
        task_id = task_attempt.task_id if task_attempt else None
        step_id = task_attempt.step_id if task_attempt else None

        previous_progress_sig = _progress_signature(ticket.progress)
        current_progress_sig = progress.signature() if progress is not None else None
        if progress is not None:
            ticket.progress = progress.to_dict()
            ticket.topic_summary = progress.summary or summary
            if current_progress_sig != previous_progress_sig:
                self.store.append_event(
                    event_type="tool.progressed",
                    entity_type="step_attempt",
                    entity_id=step_attempt_id,
                    task_id=task_id,
                    step_id=step_id,
                    actor="kernel",
                    payload={
                        "job_id": ticket.job_id,
                        "phase": progress.phase,
                        "summary": progress.summary,
                        "detail": progress.detail,
                        "progress_percent": progress.progress_percent,
                        "ready": bool(progress.ready),
                    },
                )
        else:
            ticket.topic_summary = summary
            progress = normalize_observation_progress(ticket.progress)

        if status != ticket.last_status or summary != ticket.last_status_summary:
            self.store.append_event(
                event_type="tool.status.changed",
                entity_type="step_attempt",
                entity_id=step_attempt_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "job_id": ticket.job_id,
                    "status": status,
                    "topic_summary": summary,
                },
            )
        ticket.last_status = status
        ticket.last_status_summary = summary
        self._maybe_emit_progress_summary(
            step_attempt_id=step_attempt_id,
            task_id=task_id,
            step_id=step_id,
            ticket=ticket,
            status=status,
            progress=progress,
            progress_changed=current_progress_sig != previous_progress_sig,
            now=current,
        )

        if status == "observing" and ticket.ready_return and progress is not None and progress.ready:
            attempt_ctx = self._attempt_context_from_snapshot(step_attempt_id)
            ready_result = status_payload.get("result")
            if ready_result is None:
                ready_result = {
                    "job_id": ticket.job_id,
                    "status_ref": ticket.status_ref,
                    "ready": True,
                }
            final = self.finalize_observation(
                attempt_ctx,
                terminal_status="completed",
                raw_result=ready_result,
                is_error=False,
                summary=ticket.topic_summary,
                model_content_override=ticket.topic_summary,
            )
            ticket.terminal_status = "completed"
            ticket.final_result = final["raw_result"]
            ticket.final_model_content = final["model_content"]
            ticket.final_is_error = bool(final["is_error"])
            payload["observation"] = ticket.to_dict()
            self._update_runtime_snapshot(step_attempt_id, payload)
            return ObservationPollResult(ticket=ticket, should_resume=True)

        if status == "observing":
            ticket.poll_after_seconds = float(
                status_payload.get("poll_after_seconds", ticket.poll_after_seconds) or ticket.poll_after_seconds
            )
            ticket.schedule_next_poll(now=current)
            payload["observation"] = ticket.to_dict()
            self._update_runtime_snapshot(step_attempt_id, payload)
            return ObservationPollResult(ticket=ticket, should_resume=False)

        attempt_ctx = self._attempt_context_from_snapshot(step_attempt_id)
        final = self.finalize_observation(
            attempt_ctx,
            terminal_status=status,
            raw_result=status_payload.get("result"),
            is_error=bool(status_payload.get("is_error", False) or status != "completed"),
            summary=summary,
        )
        ticket.terminal_status = status
        ticket.final_result = final["raw_result"]
        ticket.final_model_content = final["model_content"]
        ticket.final_is_error = bool(final["is_error"])
        payload["observation"] = ticket.to_dict()
        self._update_runtime_snapshot(step_attempt_id, payload)
        return ObservationPollResult(ticket=ticket, should_resume=True)

    def _attempt_context_from_snapshot(self, step_attempt_id: str) -> TaskExecutionContext:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            raise KeyError(f"Unknown step attempt: {step_attempt_id}")
        task = self.store.get_task(attempt.task_id)
        if task is None:
            raise KeyError(f"Unknown task for step attempt: {step_attempt_id}")
        return TaskExecutionContext(
            conversation_id=task.conversation_id,
            task_id=task.task_id,
            step_id=attempt.step_id,
            step_attempt_id=attempt.step_attempt_id,
            source_channel=task.source_channel,
            policy_profile=task.policy_profile,
            workspace_root=str(attempt.context.get("workspace_root", "") or ""),
        )

    def _update_runtime_snapshot(self, step_attempt_id: str, payload: dict[str, Any]) -> None:
        snapshot_payload = dict(payload)
        snapshot_payload.pop("messages", None)
        envelope = self._runtime_snapshot_envelope(snapshot_payload)
        attempt = self.store.get_step_attempt(step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        context[_RUNTIME_SNAPSHOT_KEY] = envelope
        if "note_cursor_event_seq" in snapshot_payload:
            context["note_cursor_event_seq"] = int(snapshot_payload.get("note_cursor_event_seq", 0) or 0)
        self.store.update_step_attempt(step_attempt_id, context=context)

    def _apply_request_overrides(
        self,
        action_request: ActionRequest,
        request_overrides: dict[str, Any],
    ) -> ActionRequest:
        if "actor" in request_overrides:
            actor = request_overrides["actor"]
            if not isinstance(actor, dict):
                raise RuntimeError(
                    _t(
                        "kernel.executor.error.request_overrides_actor_dict",
                        default="request_overrides.actor must be a dict",
                    )
                )
            action_request.actor = dict(actor)
        if "context" in request_overrides:
            context = request_overrides["context"]
            if not isinstance(context, dict):
                raise RuntimeError(
                    _t(
                        "kernel.executor.error.request_overrides_context_dict",
                        default="request_overrides.context must be a dict",
                    )
                )
            merged_context = dict(action_request.context)
            merged_context.update(context)
            action_request.context = merged_context
        if "idempotency_key" in request_overrides:
            action_request.idempotency_key = str(request_overrides["idempotency_key"])
        return action_request

    def _record_action_request(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        return self._store_json_artifact(
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

    def _record_policy_evaluation(
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
        return self._store_json_artifact(
            payload=payload,
            kind="policy_evaluation",
            attempt_ctx=attempt_ctx,
            metadata={"tool_name": action_request.tool_name},
            event_type="policy.evaluated",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            payload_summary=payload,
        )

    def _store_json_artifact(
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
                actor="kernel",
                payload={"artifact_ref": artifact.artifact_id, **(payload_summary or {})},
            )
        return artifact.artifact_id

    def _build_preview_artifact(
        self,
        tool: ToolSpec,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
    ) -> str | None:
        preview_text = self._preview_text(tool, tool_input)
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

    def _preview_text(self, tool: ToolSpec, tool_input: dict[str, Any]) -> str:
        if tool.name in {"write_file", "write_hermit_file"}:
            path = str(tool_input.get("path", ""))
            new_content = str(tool_input.get("content", ""))
            root_hint = tool.resource_scope_hint or ""
            old_content = ""
            if root_hint:
                try:
                    file_path = (Path(root_hint) / path).resolve()
                    if file_path.exists():
                        old_content = file_path.read_text(encoding="utf-8")
                except OSError:
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
            return _t(
                "kernel.executor.preview.write",
                default=f"# Write Preview\n\nPath: `{path}`\n\n```diff\n{diff or '(new file or no textual diff)'}\n```",
                path=path,
                diff=diff or _t("kernel.executor.preview.write.empty_diff", default="(new file or no textual diff)"),
            )
        if tool.name == "bash":
            command = str(tool_input.get("command", ""))
            return _t(
                "kernel.executor.preview.command",
                default=f"# Command Preview\n\n```bash\n{command}\n```",
                command=command,
            )
        return json.dumps({"tool": tool.name, "input": tool_input}, ensure_ascii=False, indent=2)

    def _capture_state_witness(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        payload = self._state_witness_payload(action_request, attempt_ctx)
        witness_ref = self._store_json_artifact(
            payload=payload,
            kind="state.witness",
            attempt_ctx=attempt_ctx,
            metadata={"tool_name": action_request.tool_name},
            event_type="witness.captured",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            payload_summary={"tool_name": action_request.tool_name, "action_class": action_request.action_class},
        )
        return witness_ref

    def _state_witness_payload(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> dict[str, Any]:
        workspace_root = Path(attempt_ctx.workspace_root or ".").resolve()
        target_paths = list(action_request.derived.get("target_paths", []))
        files = [self._path_witness(path, workspace_root=workspace_root) for path in target_paths]
        return {
            "action_class": action_request.action_class,
            "tool_name": action_request.tool_name,
            "resource_scopes": list(action_request.resource_scopes),
            "cwd": str(workspace_root),
            "git": self._git_witness(workspace_root),
            "files": files,
            "network_hosts": list(action_request.derived.get("network_hosts", [])),
            "command_preview": action_request.derived.get("command_preview"),
        }

    def _path_witness(self, path: str, *, workspace_root: Path) -> dict[str, Any]:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = (workspace_root / candidate).resolve()
        result: dict[str, Any] = {"path": str(candidate)}
        try:
            exists = candidate.exists()
        except OSError as exc:
            return {"path": str(candidate), "error": str(exc), "exists": False}
        result["exists"] = exists
        if not exists:
            return result
        try:
            stat = candidate.stat()
            result["mtime_ns"] = stat.st_mtime_ns
            result["size"] = stat.st_size
            if candidate.is_file():
                result["sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
            else:
                result["kind"] = "directory"
        except OSError as exc:
            result["error"] = str(exc)
        return result

    def _git_witness(self, workspace_root: Path) -> dict[str, Any]:
        git_dir = workspace_root / ".git"
        if not git_dir.exists():
            return {"present": False}
        head = ""
        dirty = False
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            dirty = bool(
                subprocess.run(
                    ["git", "status", "--short"],
                    cwd=workspace_root,
                    capture_output=True,
                    text=True,
                    check=False,
                ).stdout.strip()
            )
        except OSError:
            return {"present": True, "head": "", "dirty": False, "error": "git unavailable"}
        return {"present": True, "head": head, "dirty": dirty}

    def _validate_state_witness(
        self,
        witness_ref: str,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> bool:
        artifact = self.store.get_artifact(witness_ref)
        if artifact is None:
            return False
        stored = json.loads(self.artifact_store.read_text(artifact.uri))
        current = self._state_witness_payload(action_request, attempt_ctx)
        valid = stored == current
        self.store.append_event(
            event_type="witness.validated" if valid else "witness.failed",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "state_witness_ref": witness_ref,
                "tool_name": action_request.tool_name,
            },
        )
        return valid

    def _requested_action_payload(
        self,
        action_request: ActionRequest,
        policy: PolicyDecision,
        preview_artifact: str | None,
        *,
        decision_ref: str | None,
        policy_ref: str | None,
        state_witness_ref: str | None,
    ) -> dict[str, Any]:
        packet = dict(policy.approval_packet or {})
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
            packet["artifact_ids"] = list(dict.fromkeys(list(packet.get("artifact_ids", [])) + [preview_artifact]))
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
            "decision_ref": decision_ref,
            "policy_ref": policy_ref,
            "state_witness_ref": state_witness_ref,
            "idempotency_key": action_request.idempotency_key,
        }

    def _matching_approval(
        self,
        approval_record: Any,
        action_request: ActionRequest,
        policy: PolicyDecision,
        preview_artifact: str | None,
        *,
        attempt_ctx: TaskExecutionContext,
    ) -> tuple[Any, str | None, bool]:
        if approval_record is None or approval_record.status != "granted":
            return None, None, False
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
            return None, approval_record.state_witness_ref, False
        witness_ref = approval_record.state_witness_ref
        if witness_ref and _needs_witness(action_request.action_class):
            if not self._validate_state_witness(witness_ref, action_request, attempt_ctx):
                return None, witness_ref, True
        return approval_record, witness_ref, False

    def _matching_path_grant(self, action_request: ActionRequest) -> Any:
        if action_request.action_class != "write_local":
            return None
        if not action_request.conversation_id:
            return None
        if not action_request.derived.get("outside_workspace"):
            return None
        target_paths = list(action_request.derived.get("target_paths", []))
        if not target_paths:
            return None
        return self.path_grant_service.match(
            conversation_id=str(action_request.conversation_id),
            action_class=action_request.action_class,
            target_path=str(target_paths[0]),
        )

    def _ensure_directory_grant(
        self,
        *,
        approval_record: Any,
        action_request: ActionRequest,
        policy_ref: str | None,
    ) -> str | None:
        if action_request.action_class != "write_local" or not action_request.conversation_id:
            return None
        if not action_request.derived.get("outside_workspace"):
            return None
        path_prefix = str(
            approval_record.requested_action.get("grant_scope_dir")
            or action_request.derived.get("grant_candidate_prefix")
            or ""
        ).strip()
        if not path_prefix:
            return None
        grant_id = self.path_grant_service.create(
            conversation_id=str(action_request.conversation_id),
            action_class=action_request.action_class,
            path_prefix=path_prefix,
            path_display=path_prefix,
            created_by=str(approval_record.resolved_by or "user"),
            approval_ref=approval_record.approval_id,
            decision_ref=approval_record.decision_ref,
            policy_ref=policy_ref,
        )
        resolution = dict(approval_record.resolution or {})
        resolution["grant_ref"] = grant_id
        self.store.update_approval_resolution(approval_record.approval_id, resolution)
        return grant_id

    def _authorization_reason(
        self,
        *,
        policy: PolicyDecision,
        approval_mode: str,
        grant_id: str | None,
    ) -> str:
        if grant_id and approval_mode == "always_directory":
            return _t("kernel.executor.authorization.always_directory")
        if grant_id:
            return _t("kernel.executor.authorization.existing_grant")
        if approval_mode == "once":
            return _t("kernel.executor.authorization.once")
        return policy.reason or _t("kernel.executor.authorization.policy_allowed")

    def _successful_result_summary(
        self,
        *,
        tool_name: str,
        approval_mode: str,
        grant_id: str | None,
    ) -> str:
        if grant_id and approval_mode == "always_directory":
            return _t("kernel.executor.result.always_directory", tool_name=tool_name)
        if grant_id:
            return _t("kernel.executor.result.existing_grant", tool_name=tool_name)
        if approval_mode == "once":
            return _t("kernel.executor.result.once", tool_name=tool_name)
        return _t("kernel.executor.result.success", tool_name=tool_name)

    def _prepare_rollback_plan(
        self,
        *,
        action_type: str,
        tool_name: str,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
    ) -> dict[str, Any]:
        contract = contract_for(action_type)
        strategy = contract.rollback_strategy
        artifact_refs: list[str] = []
        supported = False
        if action_type in {"write_local", "patch_file"}:
            raw_path = str(tool_input.get("path", "") or "").strip()
            if raw_path:
                target = Path(raw_path)
                if not target.is_absolute():
                    target = Path(attempt_ctx.workspace_root or ".") / raw_path
                payload = {
                    "tool_name": tool_name,
                    "path": str(target),
                    "existed": target.exists(),
                    "content": target.read_text(encoding="utf-8") if target.exists() else "",
                }
                artifact_refs.append(
                    self._store_inline_json_artifact(
                        task_id=attempt_ctx.task_id,
                        step_id=attempt_ctx.step_id,
                        kind="rollback.prestate",
                        payload=payload,
                        metadata={"action_type": action_type, "strategy": strategy},
                    )
                )
                supported = True
        elif action_type == "vcs_mutation":
            repo = Path(attempt_ctx.workspace_root or ".")
            try:
                head = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                dirty = bool(
                    subprocess.run(
                        ["git", "status", "--porcelain"],
                        cwd=repo,
                        check=True,
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                )
                artifact_refs.append(
                    self._store_inline_json_artifact(
                        task_id=attempt_ctx.task_id,
                        step_id=attempt_ctx.step_id,
                        kind="rollback.prestate",
                        payload={"repo_path": str(repo), "head": head, "dirty": dirty},
                        metadata={"action_type": action_type, "strategy": strategy},
                    )
                )
                supported = not dirty
            except Exception:
                supported = False
        elif action_type == "memory_write":
            strategy = "supersede_or_invalidate"
            supported = True
        return {"supported": supported, "strategy": strategy, "artifact_refs": artifact_refs}

    def _store_inline_json_artifact(
        self,
        *,
        task_id: str,
        step_id: str,
        kind: str,
        payload: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str:
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=task_id,
            step_id=step_id,
            kind=kind,
            uri=uri,
            content_hash=content_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata=metadata,
        )
        return artifact.artifact_id

    def _permit_constraints(
        self,
        action_request: ActionRequest,
        *,
        grant_ref: str | None,
    ) -> dict[str, Any]:
        constraints = dict(action_request.derived.get("constraints", {}))
        constraints.update(
            {
                "target_paths": list(action_request.derived.get("target_paths", [])),
                "network_hosts": list(action_request.derived.get("network_hosts", [])),
                "command_preview": action_request.derived.get("command_preview"),
            }
        )
        if grant_ref:
            constraints["grant_ref"] = grant_ref
        return {key: value for key, value in constraints.items() if value not in (None, [], {}, "")}

    def _supersede_attempt_for_witness_drift(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> ToolExecutionResult:
        current = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        if current is None:
            raise KeyError(f"Unknown step attempt: {attempt_ctx.step_attempt_id}")
        now = time.time()
        self.store.update_step_attempt(
            current.step_attempt_id,
            status="superseded",
            finished_at=now,
        )
        self.store.update_step(attempt_ctx.step_id, status="awaiting_approval")
        self.store.update_task_status(attempt_ctx.task_id, "blocked")
        successor = self.store.create_step_attempt(
            task_id=current.task_id,
            step_id=current.step_id,
            attempt=current.attempt + 1,
            status="running",
            context=dict(current.context),
        )
        successor_ctx = replace(attempt_ctx, step_attempt_id=successor.step_attempt_id, created_at=time.time())
        return self.execute(successor_ctx, tool_name, tool_input)

    def _handle_uncertain_outcome(
        self,
        *,
        tool: ToolSpec,
        tool_name: str,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
        policy: PolicyDecision,
        policy_ref: str | None,
        decision_id: str | None,
        permit_id: str,
        grant_ref: str | None,
        approval_ref: str | None,
        witness_ref: str | None,
        exc: Exception,
        idempotency_key: str | None,
        action_request: ActionRequest,
    ) -> ToolExecutionResult:
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        outcome = self.reconcile_service.reconcile(
            action_type=action_type,
            tool_input=tool_input,
            workspace_root=attempt_ctx.workspace_root,
            observables=dict(action_request.derived),
            witness=self._load_witness_payload(witness_ref),
        )
        result_code = outcome.result_code if outcome.result_code != "still_unknown" else "unknown_outcome"
        task_status = "needs_attention" if result_code == "unknown_outcome" else "reconciling"
        summary = (
            f"{outcome.summary} Original error: {type(exc).__name__}: {exc}"
        )
        self.store.append_event(
            event_type="outcome.uncertain",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "tool_name": tool_name,
                "permit_ref": permit_id,
                "decision_ref": decision_id,
                "result_code": result_code,
                "error": str(exc),
            },
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="reconciling",
            waiting_reason=str(exc),
            decision_id=decision_id,
            permit_id=permit_id,
            state_witness_ref=witness_ref,
        )
        self.store.update_step(attempt_ctx.step_id, status="reconciling")
        self.store.update_task_status(attempt_ctx.task_id, task_status)
        receipt_id = self._issue_receipt(
            tool=tool,
            tool_name=tool_name,
            tool_input=tool_input,
            raw_result={"error": str(exc), "reconcile_summary": outcome.summary},
            attempt_ctx=attempt_ctx,
            approval_ref=approval_ref,
            policy=policy,
            policy_ref=policy_ref,
            decision_ref=decision_id,
            permit_ref=permit_id,
            grant_ref=grant_ref,
            witness_ref=witness_ref,
            result_code=result_code,
            idempotency_key=idempotency_key,
            result_summary=summary,
            output_kind="tool_error",
        )
        return ToolExecutionResult(
            model_content=f"[Execution Requires Attention] {summary}",
            raw_result={"error": str(exc)},
            denied=True,
            policy_decision=policy,
            receipt_id=receipt_id,
            decision_id=decision_id,
            permit_id=permit_id,
            grant_ref=grant_ref,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            result_code=result_code,
            execution_status=_execution_status_from_result_code(result_code),
            state_applied=True,
        )

    def _load_witness_payload(self, witness_ref: str | None) -> dict[str, Any]:
        if not witness_ref:
            return {}
        artifact = self.store.get_artifact(witness_ref)
        if artifact is None:
            return {}
        try:
            payload = json.loads(self.artifact_store.read_text(artifact.uri))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _handle_dispatch_denied(
        self,
        *,
        tool: ToolSpec,
        tool_name: str,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
        policy: PolicyDecision,
        policy_ref: str | None,
        decision_id: str | None,
        permit_id: str,
        grant_ref: str | None,
        approval_ref: str | None,
        witness_ref: str | None,
        error: CapabilityGrantError,
        idempotency_key: str | None,
    ) -> ToolExecutionResult:
        self.store.append_event(
            event_type="dispatch.denied",
            entity_type="execution_permit",
            entity_id=permit_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "permit_ref": permit_id,
                "decision_ref": decision_id,
                "error_code": error.code,
                "error": str(error),
                "tool_name": tool_name,
            },
        )
        now = time.time()
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="failed",
            waiting_reason=str(error),
            decision_id=decision_id,
            permit_id=permit_id,
            state_witness_ref=witness_ref,
            finished_at=now,
        )
        self.store.update_step(attempt_ctx.step_id, status="failed", finished_at=now)
        self.store.update_task_status(attempt_ctx.task_id, "failed")

        receipt_id = None
        if policy.requires_receipt:
            receipt_id = self._issue_receipt(
                tool=tool,
                tool_name=tool_name,
                tool_input=tool_input,
                raw_result={"error": str(error), "error_code": error.code},
                attempt_ctx=attempt_ctx,
                approval_ref=approval_ref,
                policy=policy,
                policy_ref=policy_ref,
                decision_ref=decision_id,
                permit_ref=permit_id,
                grant_ref=grant_ref,
                witness_ref=witness_ref,
                result_code="dispatch_denied",
                idempotency_key=idempotency_key,
                result_summary=str(error),
                output_kind="dispatch_error",
            )

        return ToolExecutionResult(
            model_content=f"[Capability Denied] {error}",
            raw_result={"error": str(error), "error_code": error.code},
            denied=True,
            policy_decision=policy,
            receipt_id=receipt_id,
            decision_id=decision_id,
            permit_id=permit_id,
            grant_ref=grant_ref,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            result_code="dispatch_denied",
            execution_status="failed",
            state_applied=True,
        )

    def _issue_receipt(
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
        permit_ref: str | None,
        grant_ref: str | None,
        witness_ref: str | None,
        result_code: str,
        idempotency_key: str | None,
        result_summary: str | None = None,
        output_kind: str = "tool_output",
        rollback_supported: bool = False,
        rollback_strategy: str | None = None,
        rollback_artifact_refs: list[str] | None = None,
    ) -> str:
        input_uri, input_hash = self.artifact_store.store_json({"tool": tool_name, "input": tool_input})
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
        env_uri, env_hash = self.artifact_store.store_json(
            capture_execution_environment(cwd=Path(attempt_ctx.workspace_root or "."))
        )
        env_artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind="environment",
            uri=env_uri,
            content_hash=env_hash,
            producer="tool_executor",
            retention_class="audit",
            trust_tier="observed",
            metadata={"tool_name": tool_name},
        )
        return self.receipt_service.issue(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            action_type=tool.action_class or self.policy_engine.infer_action_class(tool),
            input_refs=[input_artifact.artifact_id],
            environment_ref=env_artifact.artifact_id,
            policy_result=policy.to_dict(),
            approval_ref=approval_ref,
            output_refs=[output_artifact.artifact_id],
            result_summary=result_summary or f"{tool_name} executed successfully",
            result_code=result_code,
            decision_ref=decision_ref,
            permit_ref=permit_ref,
            grant_ref=grant_ref,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            idempotency_key=idempotency_key,
            rollback_supported=rollback_supported,
            rollback_strategy=rollback_strategy,
            rollback_artifact_refs=rollback_artifact_refs,
        )
