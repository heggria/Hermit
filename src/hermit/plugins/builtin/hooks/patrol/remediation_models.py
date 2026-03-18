"""Remediation data models for patrol-to-fix loop."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RemediationPlan:
    """Plan describing how to remediate a patrol-detected issue."""

    signal_ref: str
    strategy: str
    goal_prompt: str
    policy_profile: str = "default"
    priority: str = "normal"
    affected_paths: list[str] = field(default_factory=list[str])


@dataclass
class RemediationPolicy:
    """Policy governing autonomous remediation behaviour."""

    auto_fix_risk_threshold: str = "medium"
    cooldown_seconds: int = 3600
    max_concurrent: int = 3
