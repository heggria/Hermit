from __future__ import annotations

from typing import Any

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.policy.evaluators.derivation import derive_request
from hermit.kernel.policy.guards.fingerprint import build_action_fingerprint
from hermit.kernel.policy.guards.merge import merge_outcomes
from hermit.kernel.policy.guards.rules import evaluate_rules
from hermit.kernel.policy.guards.tool_spec_adapter import build_action_request, infer_action_class
from hermit.kernel.policy.models.models import ActionRequest, PolicyDecision
from hermit.runtime.capability.registry.tools import ToolSpec


class PolicyEngine:
    def infer_action_class(self, tool: ToolSpec) -> str:
        return infer_action_class(tool)

    def build_action_request(
        self,
        tool: ToolSpec,
        payload: dict[str, Any],
        *,
        attempt_ctx: TaskExecutionContext | None = None,
    ) -> ActionRequest:
        return derive_request(build_action_request(tool, payload, attempt_ctx=attempt_ctx))

    def evaluate(
        self,
        tool_or_request: ToolSpec | ActionRequest,
        payload: dict[str, Any] | None = None,
        *,
        attempt_ctx: TaskExecutionContext | None = None,
    ) -> PolicyDecision:
        if isinstance(tool_or_request, ActionRequest):
            request = derive_request(tool_or_request)
        else:
            request = self.build_action_request(
                tool_or_request, payload or {}, attempt_ctx=attempt_ctx
            )
        outcomes = evaluate_rules(request)
        decision = merge_outcomes(
            outcomes, action_class=request.action_class, default_risk=request.risk_hint
        )
        decision.rule_outcomes = [outcome.to_dict() for outcome in outcomes]
        if decision.approval_packet is not None:
            packet = dict(decision.approval_packet)
            packet.setdefault("title", f"Approve action via {request.tool_name}")
            packet.setdefault("summary", request.tool_name)
            packet.setdefault(
                "risk_level", decision.obligations.approval_risk_level or decision.risk_level
            )
            packet.setdefault("resource_scopes", list(request.resource_scopes))
            packet.setdefault(
                "fingerprint",
                build_action_fingerprint(
                    {
                        "task_id": request.task_id,
                        "step_attempt_id": request.step_attempt_id,
                        "tool_name": request.tool_name,
                        "action_class": request.action_class,
                        "target_paths": request.derived.get("target_paths", []),
                        "network_hosts": request.derived.get("network_hosts", []),
                        "command_preview": request.derived.get("command_preview"),
                    }
                ),
            )
            decision.approval_packet = packet
        return decision
