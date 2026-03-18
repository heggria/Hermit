from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DelegationScope:
    """Defines the authority boundaries for a delegated child task."""

    allowed_action_classes: list[str] = field(default_factory=list[str])
    allowed_resource_scopes: list[str] = field(default_factory=list[str])
    max_steps: int = 0
    budget_tokens: int = 0


@dataclass
class DelegationRecord:
    """Tracks a parent-to-child task delegation with authority transfer."""

    delegation_id: str
    parent_task_id: str
    child_task_id: str
    delegated_principal_id: str
    scope: DelegationScope
    status: str = "active"
    delegation_grant_ref: str | None = None
    recall_reason: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
