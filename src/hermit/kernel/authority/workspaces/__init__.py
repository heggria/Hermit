from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hermit.kernel.authority.workspaces.models import (
    WorkspaceLeaseQueueEntry,
    WorkspaceLeaseRecord,
)

if TYPE_CHECKING:
    from hermit.kernel.authority.workspaces.service import (
        WorkspaceLeaseConflict,
        WorkspaceLeaseQueued,
        WorkspaceLeaseService,
        capture_execution_environment,
    )

__all__ = [
    "WorkspaceLeaseConflict",
    "WorkspaceLeaseQueueEntry",
    "WorkspaceLeaseQueued",
    "WorkspaceLeaseRecord",
    "WorkspaceLeaseService",
    "capture_execution_environment",
]

_SERVICE_NAMES = {
    "WorkspaceLeaseConflict",
    "WorkspaceLeaseQueued",
    "WorkspaceLeaseService",
    "capture_execution_environment",
}


def __getattr__(name: str) -> Any:
    if name not in _SERVICE_NAMES:
        raise AttributeError(name)
    from hermit.kernel.authority.workspaces.service import (
        WorkspaceLeaseConflict,
        WorkspaceLeaseQueued,
        WorkspaceLeaseService,
        capture_execution_environment,
    )

    return {
        "WorkspaceLeaseConflict": WorkspaceLeaseConflict,
        "WorkspaceLeaseQueued": WorkspaceLeaseQueued,
        "WorkspaceLeaseService": WorkspaceLeaseService,
        "capture_execution_environment": capture_execution_environment,
    }[name]
