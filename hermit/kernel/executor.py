from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermit.core.tools import ToolRegistry, ToolSpec, serialize_tool_result
from hermit.kernel.approval_copy import ApprovalCopyService
from hermit.kernel.approvals import ApprovalService
from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.context import TaskExecutionContext, capture_execution_environment
from hermit.kernel.policy import (
    ActionRequest,
    PolicyDecision,
    PolicyEngine,
    build_action_fingerprint,
)
from hermit.kernel.receipts import ReceiptService
from hermit.kernel.store import KernelStore

_BLOCK_TYPES = {"text", "image"}


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


@dataclass
class ToolExecutionResult:
    model_content: Any
    raw_result: Any = None
    blocked: bool = False
    denied: bool = False
    approval_id: str | None = None
    approval_message: str | None = None
    policy_decision: PolicyDecision | None = None
    receipt_id: str | None = None


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
        tool_output_limit: int = 4000,
    ) -> None:
        self.registry = registry
        self.store = store
        self.artifact_store = artifact_store
        self.policy_engine = policy_engine
        self.approval_service = approval_service
        self.approval_copy = approval_copy_service or ApprovalCopyService()
        self.receipt_service = receipt_service
        self.tool_output_limit = tool_output_limit

    def execute(
        self,
        attempt_ctx: TaskExecutionContext,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> ToolExecutionResult:
        tool = self.registry.get(tool_name)
        action_request = self.policy_engine.build_action_request(tool, tool_input, attempt_ctx=attempt_ctx)
        policy = self.policy_engine.evaluate(action_request)
        approval = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        approval_id = approval.approval_id if approval else None
        approval_record = self.store.get_approval(approval_id) if approval_id else None
        preview_artifact = None
        if policy.obligations.require_preview:
            preview_artifact = self._build_preview_artifact(tool, tool_input, attempt_ctx)

        matched_approval = self._matching_approval(approval_record, action_request, policy, preview_artifact)

        if policy.verdict == "deny":
            message = f"[Policy Denied] {policy.reason or f'{tool_name} denied by policy.'}"
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                status="failed",
                waiting_reason=policy.reason,
                approval_id=None,
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
                    "policy": policy.to_dict(),
                },
            )
            return ToolExecutionResult(
                model_content=message,
                raw_result=message,
                denied=True,
                policy_decision=policy,
            )

        if policy.obligations.require_approval and matched_approval is None:
            requested_action = self._requested_action_payload(
                action_request,
                policy,
                preview_artifact,
            )
            requested_action["display_copy"] = self.approval_copy.build_canonical_copy(requested_action)
            approval_id = self.approval_service.request(
                task_id=attempt_ctx.task_id,
                step_id=attempt_ctx.step_id,
                step_attempt_id=attempt_ctx.step_attempt_id,
                approval_type=tool.action_class or self.policy_engine.infer_action_class(tool),
                requested_action=requested_action,
                request_packet_ref=preview_artifact,
            )
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                status="awaiting_approval",
                waiting_reason=policy.reason,
                approval_id=approval_id,
            )
            self.store.update_step(attempt_ctx.step_id, status="awaiting_approval")
            self.store.update_task_status(attempt_ctx.task_id, "blocked")
            blocked_message = self.approval_copy.model_prompt(requested_action, approval_id)
            approval_message = self.approval_copy.blocked_message(requested_action, approval_id)
            return ToolExecutionResult(
                model_content=blocked_message,
                blocked=True,
                approval_id=approval_id,
                approval_message=approval_message,
                policy_decision=policy,
            )

        raw_result = tool.handler(tool_input)
        model_content = _format_model_content(raw_result, self.tool_output_limit)
        receipt_id = None
        if policy.requires_receipt:
            receipt_id = self._issue_receipt(
                tool=tool,
                tool_name=tool_name,
                tool_input=tool_input,
                raw_result=raw_result,
                attempt_ctx=attempt_ctx,
                approval_ref=matched_approval.approval_id if matched_approval is not None else None,
                policy=policy,
            )
        return ToolExecutionResult(
            model_content=model_content,
            raw_result=raw_result,
            blocked=False,
            approval_id=matched_approval.approval_id if matched_approval else None,
            policy_decision=policy,
            receipt_id=receipt_id,
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
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="awaiting_approval",
            context={
                "runtime_snapshot": {
                    "messages": messages,
                    "pending_tool_blocks": pending_tool_blocks,
                    "tool_result_blocks": tool_result_blocks,
                    "next_turn": next_turn,
                    "disable_tools": disable_tools,
                    "readonly_only": readonly_only,
                }
            },
        )

    def load_blocked_state(self, step_attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            raise KeyError(f"Unknown step attempt: {step_attempt_id}")
        return dict(attempt.context.get("runtime_snapshot", {}))

    def clear_blocked_state(self, step_attempt_id: str) -> None:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        context = dict(attempt.context)
        context.pop("runtime_snapshot", None)
        self.store.update_step_attempt(step_attempt_id, context=context, waiting_reason=None)

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
            return f"# Write Preview\n\nPath: `{path}`\n\n```diff\n{diff or '(new file or no textual diff)'}\n```"
        if tool.name == "bash":
            command = str(tool_input.get("command", ""))
            return f"# Command Preview\n\n```bash\n{command}\n```"
        return json.dumps({"tool": tool.name, "input": tool_input}, ensure_ascii=False, indent=2)

    def _requested_action_payload(
        self,
        action_request: ActionRequest,
        policy: PolicyDecision,
        preview_artifact: str | None,
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
            "approval_packet": packet,
        }

    def _matching_approval(
        self,
        approval_record: Any,
        action_request: ActionRequest,
        policy: PolicyDecision,
        preview_artifact: str | None,
    ) -> Any:
        if approval_record is None or approval_record.status != "granted":
            return None
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
        if approved_fingerprint == current_fingerprint:
            return approval_record
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
        return None

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
            kind="tool_output",
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
            result_summary=f"{tool_name} executed successfully",
        )
