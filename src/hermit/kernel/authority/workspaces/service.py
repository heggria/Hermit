from __future__ import annotations

import platform
import sys
import threading
import time
import uuid
from pathlib import Path

import structlog

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.workspaces.models import (
    WorkspaceLeaseQueueEntry,
    WorkspaceLeaseRecord,
)
from hermit.kernel.ledger.journal.store import KernelStore

logger = structlog.get_logger()


def capture_execution_environment(*, cwd: Path) -> dict[str, object]:
    return {
        "cwd": str(cwd),
        "os": platform.platform(),
        "python": sys.version,
        "platform": sys.platform,
    }


class WorkspaceLeaseConflict(RuntimeError):
    """Raised when a mutable workspace lease conflicts with an existing one."""


class WorkspaceLeaseQueued(WorkspaceLeaseConflict):
    """Raised when a mutable lease request is queued instead of immediately granted."""

    def __init__(self, message: str, *, queue_entry_id: str, position: int) -> None:
        super().__init__(message)
        self.queue_entry_id = queue_entry_id
        self.position = position


class WorkspaceLeaseService:
    def __init__(
        self, store: KernelStore, artifact_store: ArtifactStore, *, default_ttl_seconds: int = 300
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.default_ttl_seconds = default_ttl_seconds
        self._queue: dict[str, list[WorkspaceLeaseQueueEntry]] = {}
        # Phase 3: protect _queue against concurrent dispatch worker access.
        self._queue_lock = threading.Lock()

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
        if mode == "mutable":
            active_leases = self.store.list_workspace_leases(
                workspace_id=workspace_id, status="active", limit=100
            )
            now = time.time()
            for lease in active_leases:
                if lease.expires_at is not None and lease.expires_at <= now:
                    self.store.update_workspace_lease(
                        lease.lease_id, status="expired", released_at=now
                    )
                    continue
                if lease.mode == "mutable":
                    # Queue the request instead of failing immediately
                    entry = WorkspaceLeaseQueueEntry(
                        queue_entry_id=str(uuid.uuid4()),
                        workspace_id=workspace_id,
                        task_id=task_id,
                        step_attempt_id=step_attempt_id,
                        root_path=root_path,
                        holder_principal_id=holder_principal_id,
                        mode=mode,
                        resource_scope=list(resource_scope),
                        ttl_seconds=ttl_seconds,
                        queued_at=time.time(),
                        status="pending",
                    )
                    with self._queue_lock:
                        if workspace_id not in self._queue:
                            self._queue[workspace_id] = []
                        self._queue[workspace_id].append(entry)
                        position = len(self._queue[workspace_id])
                    self.store.append_event(
                        event_type="workspace.lease_queued",
                        entity_type="workspace_lease",
                        entity_id=entry.queue_entry_id,
                        task_id=task_id,
                        actor="kernel",
                        payload={
                            "workspace_id": workspace_id,
                            "queue_entry_id": entry.queue_entry_id,
                            "position": position,
                            "blocked_by_lease_id": lease.lease_id,
                        },
                    )
                    logger.info(
                        "workspace.lease_queued",
                        workspace_id=workspace_id,
                        queue_entry_id=entry.queue_entry_id,
                        position=position,
                    )
                    raise WorkspaceLeaseQueued(
                        f"Workspace {workspace_id} already has an active mutable lease: "
                        f"{lease.lease_id}. Request queued at position {position}.",
                        queue_entry_id=entry.queue_entry_id,
                        position=position,
                    )
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
        now = time.time()
        lease = self.store.get_workspace_lease(lease_id)
        self.store.update_workspace_lease(lease_id, status="released", released_at=now)
        if lease is not None:
            self._process_queue(lease.workspace_id)

    def extend(self, lease_id: str, additional_ttl: int) -> WorkspaceLeaseRecord:
        """Extend the TTL of an active workspace lease."""
        lease = self.store.get_workspace_lease(lease_id)
        if lease is None:
            raise RuntimeError(f"Workspace lease not found: {lease_id}")
        if lease.status != "active":
            raise RuntimeError(f"Workspace lease {lease_id} is {lease.status}, cannot extend.")
        now = time.time()
        if lease.expires_at is not None and lease.expires_at <= now:
            raise RuntimeError(f"Workspace lease {lease_id} has already expired.")
        base = lease.expires_at if lease.expires_at is not None else now
        new_expires_at = max(base, now) + additional_ttl
        self.store.update_workspace_lease(lease_id, expires_at=new_expires_at)
        self.store.append_event(
            event_type="workspace.lease_extended",
            entity_type="workspace_lease",
            entity_id=lease_id,
            task_id=lease.task_id,
            actor="kernel",
            payload={
                "lease_id": lease_id,
                "workspace_id": lease.workspace_id,
                "previous_expires_at": lease.expires_at,
                "new_expires_at": new_expires_at,
                "additional_ttl": additional_ttl,
            },
        )
        logger.info(
            "workspace.lease_extended",
            lease_id=lease_id,
            additional_ttl=additional_ttl,
            new_expires_at=new_expires_at,
        )
        updated = self.store.get_workspace_lease(lease_id)
        if updated is None:
            raise RuntimeError(f"Workspace lease not found after update: {lease_id}")
        return updated

    def expire_stale(self) -> list[str]:
        """Find and expire all stale (past-TTL) active leases."""
        active_leases = self.store.list_workspace_leases(status="active", limit=1000)
        now = time.time()
        expired_ids: list[str] = []
        affected_workspaces: set[str] = set()
        for lease in active_leases:
            if lease.expires_at is not None and lease.expires_at <= now:
                self.store.update_workspace_lease(lease.lease_id, status="expired", released_at=now)
                self.store.append_event(
                    event_type="workspace.auto_expired",
                    entity_type="workspace_lease",
                    entity_id=lease.lease_id,
                    task_id=lease.task_id,
                    actor="kernel",
                    payload={
                        "lease_id": lease.lease_id,
                        "workspace_id": lease.workspace_id,
                        "expired_at": now,
                        "original_expires_at": lease.expires_at,
                    },
                )
                expired_ids.append(lease.lease_id)
                affected_workspaces.add(lease.workspace_id)
                logger.info(
                    "workspace.auto_expired",
                    lease_id=lease.lease_id,
                    workspace_id=lease.workspace_id,
                )
        for ws_id in affected_workspaces:
            self._process_queue(ws_id)
        return expired_ids

    def release_all_for_task(self, task_id: str) -> list[str]:
        """Release all active workspace leases for a given task."""
        active_leases = self.store.list_workspace_leases(
            task_id=task_id, status="active", limit=1000
        )
        now = time.time()
        released_ids: list[str] = []
        affected_workspaces: set[str] = set()
        for lease in active_leases:
            self.store.update_workspace_lease(lease.lease_id, status="released", released_at=now)
            self.store.append_event(
                event_type="workspace.auto_released",
                entity_type="workspace_lease",
                entity_id=lease.lease_id,
                task_id=task_id,
                actor="kernel",
                payload={
                    "lease_id": lease.lease_id,
                    "workspace_id": lease.workspace_id,
                    "released_at": now,
                    "reason": "task_terminal",
                },
            )
            released_ids.append(lease.lease_id)
            affected_workspaces.add(lease.workspace_id)
            logger.info(
                "workspace.auto_released",
                lease_id=lease.lease_id,
                workspace_id=lease.workspace_id,
                task_id=task_id,
            )
        for ws_id in affected_workspaces:
            self._process_queue(ws_id)
        return released_ids

    def validate_active(self, lease_id: str) -> WorkspaceLeaseRecord:
        lease = self.store.get_workspace_lease(lease_id)
        if lease is None:
            raise RuntimeError(f"Workspace lease not found: {lease_id}")
        if lease.status != "active":
            raise RuntimeError(f"Workspace lease {lease_id} is {lease.status}.")
        if lease.expires_at is not None and lease.expires_at <= time.time():
            raise RuntimeError(f"Workspace lease {lease_id} expired.")
        return lease

    def queue_position(self, workspace_id: str) -> int:
        """Return the number of pending entries in queue for a workspace."""
        with self._queue_lock:
            entries = list(self._queue.get(workspace_id, []))
        return len([e for e in entries if e.status == "pending"])

    def _process_queue(self, workspace_id: str) -> WorkspaceLeaseRecord | None:
        """Try to serve the next queued request for a workspace."""
        with self._queue_lock:
            entries = self._queue.get(workspace_id, [])
            pending = [e for e in entries if e.status == "pending"]
        if not pending:
            return None
        # Check if workspace now has no active mutable lease
        active_leases = self.store.list_workspace_leases(
            workspace_id=workspace_id, status="active", limit=100
        )
        now = time.time()
        has_active_mutable = False
        for lease in active_leases:
            if lease.expires_at is not None and lease.expires_at <= now:
                continue
            if lease.mode == "mutable":
                has_active_mutable = True
                break
        if has_active_mutable:
            return None
        entry = pending[0]
        with self._queue_lock:
            entry.status = "served"
        try:
            new_lease = self.acquire(
                task_id=entry.task_id,
                step_attempt_id=entry.step_attempt_id,
                workspace_id=entry.workspace_id,
                root_path=entry.root_path,
                holder_principal_id=entry.holder_principal_id,
                mode=entry.mode,
                resource_scope=entry.resource_scope,
                ttl_seconds=entry.ttl_seconds,
            )
            self.store.append_event(
                event_type="workspace.lease_dequeued",
                entity_type="workspace_lease",
                entity_id=new_lease.lease_id,
                task_id=entry.task_id,
                actor="kernel",
                payload={
                    "queue_entry_id": entry.queue_entry_id,
                    "workspace_id": workspace_id,
                    "new_lease_id": new_lease.lease_id,
                },
            )
            logger.info(
                "workspace.lease_dequeued",
                queue_entry_id=entry.queue_entry_id,
                new_lease_id=new_lease.lease_id,
            )
            return new_lease
        except WorkspaceLeaseQueued:
            # Another mutable lease appeared; put entry back
            with self._queue_lock:
                entry.status = "pending"
            return None
