from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkspaceLeaseRecord:
    lease_id: str
    task_id: str
    step_attempt_id: str
    workspace_id: str
    root_path: str
    holder_principal_id: str
    mode: str
    resource_scope: list[str] = field(default_factory=list)
    environment_ref: str | None = None
    status: str = "active"
    acquired_at: float | None = None
    expires_at: float | None = None
    released_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
