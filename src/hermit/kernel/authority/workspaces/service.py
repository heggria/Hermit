from __future__ import annotations

import platform
import sys
import time
from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.workspaces.models import WorkspaceLeaseRecord
from hermit.kernel.ledger.journal.store import KernelStore


def capture_execution_environment(*, cwd: Path) -> dict[str, object]:
    return {
        "cwd": str(cwd),
        "os": platform.platform(),
        "python": sys.version,
        "platform": sys.platform,
    }


class WorkspaceLeaseService:
    def __init__(
        self, store: KernelStore, artifact_store: ArtifactStore, *, default_ttl_seconds: int = 300
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.default_ttl_seconds = default_ttl_seconds

    def acquire(
        self,
        *,
        task_id: str,
        step_attempt_id: str,
        workspace_id: str,
        root_path: str,
        holder_principal_id: str,
        mode: str,
        resource_scope: list[str],
        ttl_seconds: int | None = None,
    ) -> WorkspaceLeaseRecord:
        expires_at = time.time() + (
            self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        )
        payload = capture_execution_environment(cwd=Path(root_path or "."))
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=task_id,
            step_id=None,
            kind="environment.snapshot",
            uri=uri,
            content_hash=content_hash,
            producer="workspace_lease",
            retention_class="audit",
            trust_tier="observed",
            metadata={"workspace_id": workspace_id, "mode": mode},
        )
        lease = self.store.create_workspace_lease(
            task_id=task_id,
            step_attempt_id=step_attempt_id,
            workspace_id=workspace_id,
            root_path=root_path,
            holder_principal_id=holder_principal_id,
            mode=mode,
            resource_scope=resource_scope,
            environment_ref=artifact.artifact_id,
            expires_at=expires_at,
        )
        return lease

    def release(self, lease_id: str) -> None:
        self.store.update_workspace_lease(lease_id, status="released", released_at=time.time())

    def validate_active(self, lease_id: str) -> WorkspaceLeaseRecord:
        lease = self.store.get_workspace_lease(lease_id)
        if lease is None:
            raise RuntimeError(f"Workspace lease not found: {lease_id}")
        if lease.status != "active":
            raise RuntimeError(f"Workspace lease {lease_id} is {lease.status}.")
        if lease.expires_at is not None and lease.expires_at <= time.time():
            raise RuntimeError(f"Workspace lease {lease_id} expired.")
        return lease
