from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from hermit.kernel.authority.grants.models import CapabilityGrantRecord
from hermit.kernel.ledger.journal.store import KernelStore


class CapabilityGrantError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CapabilityGrantService:
    def __init__(self, store: KernelStore, *, default_ttl_seconds: int = 300) -> None:
        self.store = store
        self.default_ttl_seconds = default_ttl_seconds

    def issue(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        decision_ref: str,
        approval_ref: str | None,
        policy_ref: str | None,
        issued_to_principal_id: str,
        issued_by_principal_id: str,
        workspace_lease_ref: str | None,
        action_class: str,
        resource_scope: list[str],
        idempotency_key: str | None,
        constraints: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> str:
        ttl = self.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        expires_at = time.time() + ttl if ttl > 0 else None
        grant = self.store.create_capability_grant(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=step_attempt_id,
            decision_ref=decision_ref,
            approval_ref=approval_ref,
            policy_ref=policy_ref,
            issued_to_principal_id=issued_to_principal_id,
            issued_by_principal_id=issued_by_principal_id,
            workspace_lease_ref=workspace_lease_ref,
            action_class=action_class,
            resource_scope=resource_scope,
            constraints=constraints or {},
            idempotency_key=idempotency_key,
            expires_at=expires_at,
        )
        return grant.grant_id

    def consume(self, grant_id: str) -> None:
        self.store.update_capability_grant(grant_id, status="consumed", consumed_at=time.time())

    def revoke(self, grant_id: str) -> None:
        self.store.update_capability_grant(grant_id, status="revoked", revoked_at=time.time())

    def mark_uncertain(self, grant_id: str) -> None:
        self.store.update_capability_grant(grant_id, status="uncertain")

    def mark_invalid(self, grant_id: str) -> None:
        self.store.update_capability_grant(grant_id, status="invalid")

    def enforce(
        self,
        grant_id: str,
        *,
        task_id: str,
        action_class: str,
        resource_scope: list[str],
        constraints: dict[str, Any] | None = None,
    ) -> CapabilityGrantRecord:
        grant = self.store.get_capability_grant(grant_id)
        if grant is None:
            raise CapabilityGrantError("missing", f"Capability grant not found: {grant_id}")
        if grant.task_id != task_id:
            raise CapabilityGrantError(
                "task_mismatch",
                f"Capability grant {grant_id} belongs to task {grant.task_id!r}, not {task_id!r}.",
            )
        if grant.status != "issued":
            raise CapabilityGrantError(
                "inactive",
                f"Capability grant {grant_id} is {grant.status} and cannot be dispatched.",
            )
        if grant.expires_at is not None and grant.expires_at <= time.time():
            self.mark_invalid(grant_id)
            raise CapabilityGrantError(
                "expired", f"Capability grant {grant_id} expired before dispatch."
            )
        if grant.action_class != action_class:
            self.mark_invalid(grant_id)
            raise CapabilityGrantError(
                "action_mismatch",
                f"Capability grant {grant_id} only allows {grant.action_class}, not {action_class}.",
            )
        if not set(resource_scope).issubset(set(grant.resource_scope)):
            self.mark_invalid(grant_id)
            raise CapabilityGrantError(
                "scope_mismatch",
                f"Capability grant {grant_id} does not cover resource scope {sorted(resource_scope)}.",
            )
        self._validate_constraints(grant, constraints or {})
        lease_ref = str(grant.workspace_lease_ref or "").strip()
        if lease_ref:
            lease = self.store.get_workspace_lease(lease_ref)
            if lease is None or lease.status != "active":
                raise CapabilityGrantError(
                    "lease_inactive", f"Workspace lease {lease_ref} is no longer active."
                )
            if lease.expires_at is not None and lease.expires_at <= time.time():
                raise CapabilityGrantError(
                    "lease_expired", f"Workspace lease {lease_ref} expired before dispatch."
                )
        return grant

    def _validate_constraints(
        self,
        grant: CapabilityGrantRecord,
        current: dict[str, Any],
    ) -> None:
        stored = dict(grant.constraints or {})
        if not stored:
            return

        stored_paths = [str(path) for path in stored.get("target_paths", [])]
        current_paths = [str(path) for path in current.get("target_paths", [])]
        if stored_paths and current_paths and current_paths != stored_paths:
            self.mark_invalid(grant.grant_id)
            raise CapabilityGrantError(
                "target_path_mismatch",
                f"Capability grant {grant.grant_id} does not cover the current target paths.",
            )

        stored_hosts = set(str(host) for host in stored.get("network_hosts", []))
        current_hosts = set(str(host) for host in current.get("network_hosts", []))
        if stored_hosts and current_hosts and not current_hosts.issubset(stored_hosts):
            self.mark_invalid(grant.grant_id)
            raise CapabilityGrantError(
                "network_host_mismatch",
                f"Capability grant {grant.grant_id} does not cover the current network hosts.",
            )

        stored_command = str(stored.get("command_preview", "") or "").strip()
        current_command = str(current.get("command_preview", "") or "").strip()
        if stored_command and current_command and stored_command != current_command:
            self.mark_invalid(grant.grant_id)
            raise CapabilityGrantError(
                "command_mismatch",
                f"Capability grant {grant.grant_id} does not cover the current command.",
            )

        lease_root = str(stored.get("lease_root_path", "") or "").strip()
        if lease_root and current_paths:
            try:
                root = Path(lease_root).expanduser().resolve()
            except OSError as exc:
                raise CapabilityGrantError(
                    "lease_invalid", f"Workspace lease root is invalid: {exc}"
                ) from exc
            for target in current_paths:
                try:
                    candidate = Path(target).expanduser().resolve()
                except OSError as exc:
                    raise CapabilityGrantError(
                        "lease_invalid", f"Target path is invalid: {exc}"
                    ) from exc
                if candidate != root and root not in candidate.parents:
                    raise CapabilityGrantError(
                        "lease_scope_mismatch",
                        f"Workspace lease rooted at {lease_root} does not cover {candidate}.",
                    )
