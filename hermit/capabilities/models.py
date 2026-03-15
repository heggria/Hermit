from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapabilityGrantRecord:
    grant_id: str
    task_id: str
    step_id: str
    step_attempt_id: str
    decision_ref: str
    approval_ref: str | None
    policy_ref: str | None
    issued_to_principal_id: str
    issued_by_principal_id: str
    workspace_lease_ref: str | None
    action_class: str
    resource_scope: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    status: str = "issued"
    issued_at: float | None = None
    expires_at: float | None = None
    consumed_at: float | None = None
    revoked_at: float | None = None
