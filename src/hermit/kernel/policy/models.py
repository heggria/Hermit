from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast


@dataclass
class PolicyReason:
    code: str
    message: str
    severity: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyReason":
        return cls(
            code=str(data.get("code", "")),
            message=str(data.get("message", "")),
            severity=str(data.get("severity", "info")),
        )


@dataclass
class PolicyObligations:
    require_receipt: bool = False
    require_preview: bool = False
    require_approval: bool = False
    require_evidence: bool = False
    approval_risk_level: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "require_receipt": self.require_receipt,
            "require_preview": self.require_preview,
            "require_approval": self.require_approval,
            "require_evidence": self.require_evidence,
            "approval_risk_level": self.approval_risk_level,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyObligations":
        return cls(
            require_receipt=bool(data.get("require_receipt", False)),
            require_preview=bool(data.get("require_preview", False)),
            require_approval=bool(data.get("require_approval", False)),
            require_evidence=bool(data.get("require_evidence", False)),
            approval_risk_level=str(data.get("approval_risk_level", "") or "") or None,
        )


@dataclass
class ActionRequest:
    request_id: str
    idempotency_key: str = ""
    task_id: str = ""
    step_id: str = ""
    step_attempt_id: str = ""
    conversation_id: str | None = None
    tool_name: str = ""
    tool_input: Any = None
    action_class: str = "unknown"
    resource_scopes: list[str] = field(default_factory=list[str])
    risk_hint: str = "high"
    idempotent: bool = False
    requires_receipt: bool = False
    supports_preview: bool = False
    actor: dict[str, Any] = field(default_factory=lambda: {"kind": "agent", "agent_id": "hermit"})
    context: dict[str, Any] = field(default_factory=dict[str, Any])
    derived: dict[str, Any] = field(default_factory=dict[str, Any])

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "step_attempt_id": self.step_attempt_id,
            "conversation_id": self.conversation_id,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "action_class": self.action_class,
            "resource_scopes": list(self.resource_scopes),
            "risk_hint": self.risk_hint,
            "idempotent": self.idempotent,
            "requires_receipt": self.requires_receipt,
            "supports_preview": self.supports_preview,
            "actor": dict(self.actor),
            "context": dict(self.context),
            "derived": dict(self.derived),
        }


@dataclass
class PolicyDecision:
    verdict: str
    action_class: str
    reasons: list[PolicyReason] = field(default_factory=list[PolicyReason])
    obligations: PolicyObligations = field(default_factory=PolicyObligations)
    normalized_constraints: dict[str, Any] = field(default_factory=dict[str, Any])
    approval_packet: dict[str, Any] | None = None
    risk_level: str = "low"

    @property
    def decision(self) -> str:
        return self.verdict

    @property
    def requires_receipt(self) -> bool:
        return self.obligations.require_receipt

    @property
    def reason(self) -> str:
        if not self.reasons:
            return ""
        return "; ".join(reason.message for reason in self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "verdict": self.verdict,
            "action_class": self.action_class,
            "risk_level": self.risk_level,
            "requires_receipt": self.requires_receipt,
            "reason": self.reason,
            "reasons": [reason.to_dict() for reason in self.reasons],
            "obligations": self.obligations.to_dict(),
            "normalized_constraints": dict(self.normalized_constraints),
            "approval_packet": dict(self.approval_packet) if self.approval_packet else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyDecision":
        verdict = str(data.get("verdict", data.get("decision", "")) or "")
        obligations: Any = data.get("obligations", {})
        reasons: list[Any] = list(data.get("reasons", []) or [])
        approval_packet: Any = data.get("approval_packet")
        return cls(
            verdict=verdict,
            action_class=str(data.get("action_class", "unknown")),
            reasons=[
                PolicyReason.from_dict(cast(dict[str, Any], item))
                for item in reasons
                if isinstance(item, dict)
            ],
            obligations=PolicyObligations.from_dict(
                cast(dict[str, Any], obligations) if isinstance(obligations, dict) else {}
            ),
            normalized_constraints=dict(
                cast(dict[str, Any], data.get("normalized_constraints", {}) or {})
            ),
            approval_packet=dict(cast(dict[str, Any], approval_packet))
            if isinstance(approval_packet, dict)
            else None,
            risk_level=str(data.get("risk_level", "low")),
        )
