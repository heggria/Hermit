from __future__ import annotations

from hermit.workspaces.models import WorkspaceLeaseRecord

__all__ = ["WorkspaceLeaseRecord", "WorkspaceLeaseService", "capture_execution_environment"]


def __getattr__(name: str):
    if name not in {"WorkspaceLeaseService", "capture_execution_environment"}:
        raise AttributeError(name)
    from hermit.workspaces.service import WorkspaceLeaseService, capture_execution_environment

    return {
        "WorkspaceLeaseService": WorkspaceLeaseService,
        "capture_execution_environment": capture_execution_environment,
    }[name]
