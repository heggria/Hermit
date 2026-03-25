"""Data models for contract template learning.

``ContractTemplate`` captures the reusable shape of a successfully reconciled
execution contract.  ``TemplateMatch`` pairs a template reference with a
confidence score and the reasons why the match was selected.
``PolicySuggestion`` carries template-confidence-based policy adjustment hints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PolicySuggestion:
    """Suggestion to adjust policy based on template confidence."""

    template_ref: str
    suggested_risk_level: str | None = None
    skip_approval_eligible: bool = False
    confidence_basis: str = ""
    reason: str = ""


@dataclass
class ContractTemplate:
    """Lightweight descriptor extracted from a satisfied execution contract."""

    action_class: str
    tool_name: str
    risk_level: str
    reversibility_class: str
    expected_effects: list[str] = field(default_factory=list)
    success_criteria: dict[str, Any] = field(default_factory=dict)
    drift_budget: dict[str, Any] = field(default_factory=dict)
    source_contract_ref: str = ""
    source_reconciliation_ref: str = ""
    invocation_count: int = 0
    success_count: int = 1
    failure_count: int = 0
    success_rate: float = 0.0
    last_failure_at: float | None = None
    last_used_at: float = 0.0
    resource_scope_pattern: list[str] = field(default_factory=list)
    constraint_defaults: dict[str, Any] = field(default_factory=dict)
    evidence_requirements: list[str] = field(default_factory=list)
    workspace_ref: str = ""
    scope_kind: str = "global"


@dataclass
class TemplateMatch:
    """Result of matching a proposed action against stored templates."""

    template_ref: str
    confidence: float
    match_reasons: list[str] = field(default_factory=list)
    template: ContractTemplate | None = None
