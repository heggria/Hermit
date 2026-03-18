"""Data models for recursive rollback planning and execution."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DependentReceipt:
    """A receipt node in the dependency graph with its depth and dependents."""

    receipt_id: str
    depth: int
    rollback_supported: bool
    rollback_strategy: str | None
    manual_review_required: bool = False
    dependent_ids: list[str] = field(default_factory=list[str])


@dataclass
class RollbackPlan:
    """Ordered plan for recursive rollback execution.

    ``execution_order`` lists receipt IDs leaf-first (reverse dependency order)
    so that downstream effects are undone before their causes.
    """

    root_receipt_id: str
    execution_order: list[str] = field(default_factory=list[str])
    nodes: dict[str, DependentReceipt] = field(default_factory=dict[str, DependentReceipt])
    manual_review_ids: list[str] = field(default_factory=list[str])
    cycle_detected: bool = False


@dataclass
class RollbackPlanExecution:
    """Result of executing a recursive rollback plan."""

    plan: RollbackPlan
    succeeded_ids: list[str] = field(default_factory=list[str])
    failed_ids: list[str] = field(default_factory=list[str])
    skipped_ids: list[str] = field(default_factory=list[str])
    results: dict[str, dict[str, object]] = field(default_factory=dict[str, dict[str, object]])
    status: str = "pending"
