"""Data models for evidence signals and steering directives."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _sig_id() -> str:
    return f"sig_{uuid.uuid4().hex[:12]}"


def _steer_id() -> str:
    return f"sig_steer_{uuid.uuid4().hex[:12]}"


@dataclass
class EvidenceSignal:
    """Structured signal representing discovered evidence."""

    source_kind: str = ""
    source_ref: str = ""
    signal_id: str = field(default_factory=_sig_id)
    conversation_id: str | None = None
    task_id: str | None = None
    summary: str = ""
    confidence: float = 0.5
    evidence_refs: list[str] = field(default_factory=list[str])
    suggested_goal: str = ""
    suggested_policy_profile: str = "default"
    risk_level: str = "low"
    disposition: str = "pending"
    cooldown_key: str = ""
    cooldown_seconds: int = 86400
    produced_task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    acted_at: float | None = None


_STEERING_RISK_LEVEL: dict[str, str] = {
    "scope": "medium",
    "strategy": "medium",
    "constraint": "low",
    "priority": "low",
    "policy": "high",
}


@dataclass
class SteeringDirective:
    """Structured guidance from operators for mid-execution task redirection."""

    task_id: str = ""
    steering_type: str = ""
    directive: str = ""
    directive_id: str = field(default_factory=_steer_id)
    evidence_refs: list[str] = field(default_factory=list[str])
    issued_by: str = "operator"
    disposition: str = "pending"
    supersedes_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
    created_at: float = field(default_factory=time.time)
    applied_at: float | None = None

    def to_signal(self) -> EvidenceSignal:
        """Convert to EvidenceSignal for storage."""
        meta = dict(self.metadata)
        meta["steering_type"] = self.steering_type
        meta["issued_by"] = self.issued_by
        if self.supersedes_id:
            meta["supersedes_id"] = self.supersedes_id
        risk_level = _STEERING_RISK_LEVEL.get(self.steering_type, "low")
        return EvidenceSignal(
            signal_id=self.directive_id,
            source_kind=f"steering:{self.steering_type}",
            source_ref=f"task://{self.task_id}",
            task_id=self.task_id,
            summary=self.directive,
            confidence=0.9,
            evidence_refs=list(self.evidence_refs),
            suggested_goal=self.directive,
            suggested_policy_profile="default",
            risk_level=risk_level,
            disposition=self.disposition,
            metadata=meta,
            created_at=self.created_at,
        )

    @classmethod
    def from_signal(cls, signal: EvidenceSignal) -> SteeringDirective:
        """Reconstruct from stored EvidenceSignal."""
        steering_type = signal.metadata.get("steering_type", "")
        if not steering_type and signal.source_kind.startswith("steering:"):
            steering_type = signal.source_kind.removeprefix("steering:")
        return cls(
            directive_id=signal.signal_id,
            task_id=signal.task_id or "",
            steering_type=steering_type,
            directive=signal.summary,
            evidence_refs=list(signal.evidence_refs),
            issued_by=signal.metadata.get("issued_by", "operator"),
            disposition=signal.disposition,
            supersedes_id=signal.metadata.get("supersedes_id"),
            metadata=dict(signal.metadata),
            created_at=signal.created_at,
            applied_at=signal.metadata.get("applied_at"),
        )
