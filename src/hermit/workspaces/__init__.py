from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hermit.workspaces.models import WorkspaceLeaseRecord

if TYPE_CHECKING:
    from hermit.workspaces.service import WorkspaceLeaseService, capture_execution_environment

__all__ = ["WorkspaceLeaseRecord", "WorkspaceLeaseService", "capture_execution_environment"]


def __getattr__(name: str) -> Any:
    if name not in {"WorkspaceLeaseService", "capture_execution_environment"}:
        raise AttributeError(name)
    from hermit.workspaces.service import WorkspaceLeaseService, capture_execution_environment

    return {
        "WorkspaceLeaseService": WorkspaceLeaseService,
        "capture_execution_environment": capture_execution_environment,
    }[name]
