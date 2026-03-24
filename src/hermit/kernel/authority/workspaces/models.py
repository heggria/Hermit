from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Allowed modes for workspace access.
WorkspaceMode = Literal["readonly", "mutable", "exclusive"]

# Allowed lifecycle states for a lease record.
LeaseStatus = Literal["active", "released", "expired", "revoked"]

# Allowed lifecycle states for a queue entry.
QueueEntryStatus = Literal["pending", "granted", "cancelled", "expired"]


@dataclass
class WorkspaceLeaseRecord:
    lease_id: str
    task_id: str
    step_attempt_id: str
    workspace_id: str
    root_path: str
    holder_principal_id: str
    mode: WorkspaceMode
    resource_scope: list[str] = field(default_factory=list)
    environment_ref: str | None = None
    status: LeaseStatus = "active"
    acquired_at: float | None = None
    expires_at: float | None = None
    released_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkspaceLeaseQueueEntry:
    """A queued request for a mutable workspace lease (FIFO)."""

    queue_entry_id: str
    workspace_id: str
    task_id: str
    step_attempt_id: str
    root_path: str
    holder_principal_id: str
    mode: WorkspaceMode
    resource_scope: list[str] = field(default_factory=list)
    ttl_seconds: int | None = None
    queued_at: float | None = None
    status: QueueEntryStatus = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)
