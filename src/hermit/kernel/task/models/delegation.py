from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DelegationScope:
    """Defines the authority boundaries for a delegated child task."""

    allowed_action_classes: list[str] = field(default_factory=list[str])
    allowed_resource_scopes: list[str] = field(default_factory=list[str])
    max_steps: int = 0
    budget_tokens: int = 0
    # Remaining token budget for this delegation subtree (decremented as children
    # consume tokens).  Zero means "unbounded / tracking disabled".
    budget_remaining: int = 0


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
    # How many tokens remain in the authority budget granted to this child.
    # Derived from scope.budget_tokens × attenuation_factor at spawn time and
    # decremented by the kernel as steps execute.  Zero means "no budget cap".
    authority_budget_remaining: int = 0
    # Multiplicative factor applied to the parent's budget_tokens when creating
    # an attenuated child scope.  Stored for audit: 1.0 = full authority pass-
    # through, 0.5 = half authority, etc.
    attenuation_factor: float = 1.0
    created_at: float = 0.0
    updated_at: float = 0.0
