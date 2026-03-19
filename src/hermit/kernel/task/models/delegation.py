from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ApprovalDelegationPolicy:
    """Rules for which child approvals the parent auto-resolves.

    Action classes not listed in any category are denied by default.
    """

    auto_approve: list[str] = field(default_factory=list[str])
    require_parent_approval: list[str] = field(default_factory=list[str])
    deny: list[str] = field(default_factory=list[str])

    def resolve(self, action_class: str) -> str:
        """Return the resolution for an action class.

        Returns one of: 'auto_approve', 'require_parent_approval', 'deny'.
        Unknown action classes are denied by default.
        """
        if action_class in self.auto_approve:
            return "auto_approve"
        if action_class in self.require_parent_approval:
            return "require_parent_approval"
        # Deny-by-default: explicit deny list OR unlisted action classes
        return "deny"


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
    # Policy governing which child approval requests are auto-resolved by the
    # parent's delegation authority.  None means no delegation policy is active.
    approval_delegation_policy: ApprovalDelegationPolicy | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
