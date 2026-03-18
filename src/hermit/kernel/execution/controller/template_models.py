"""Data models for contract template learning.

``ContractTemplate`` captures the reusable shape of a successfully reconciled
execution contract.  ``TemplateMatch`` pairs a template reference with a
confidence score and the reasons why the match was selected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContractTemplate:
    """Lightweight descriptor extracted from a satisfied execution contract."""

    action_class: str
    tool_name: str
    risk_level: str
    reversibility_class: str
    expected_effects: list[str] = field(default_factory=list[str])
    success_criteria: dict[str, Any] = field(default_factory=dict[str, Any])
    drift_budget: dict[str, Any] = field(default_factory=dict[str, Any])
    source_contract_ref: str = ""
    source_reconciliation_ref: str = ""
    success_count: int = 1
    last_used_at: float = 0.0
    resource_scope_pattern: list[str] = field(default_factory=list[str])
    constraint_defaults: dict[str, Any] = field(default_factory=dict[str, Any])
    evidence_requirements: list[str] = field(default_factory=list[str])


@dataclass
class TemplateMatch:
    """Result of matching a proposed action against stored templates."""

    template_ref: str
    confidence: float
    match_reasons: list[str] = field(default_factory=list[str])
    template: ContractTemplate | None = None
