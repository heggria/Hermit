from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TriggerRule:
    name: str
    source_kind: str  # "test_failure" | "lint_violation" | "security_vuln" | "todo_found"
    match_pattern: str  # regex pattern matching result text
    suggested_goal_template: str
    risk_level: str = "low"
    policy_profile: str = "autonomous"
    cooldown_key_template: str = ""  # supports {match} placeholder
    summary_template: str = ""  # optional; supports {match} and {context} placeholders
    enabled: bool = True


@dataclass
class TriggerMatch:
    rule: TriggerRule
    matched_text: str
    evidence_refs: list[str] = field(default_factory=list[str])
    suggested_goal: str = ""
    cooldown_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
