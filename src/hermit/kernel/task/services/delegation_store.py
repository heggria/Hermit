from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from hermit.kernel.ledger.journal.store_support import json_loads
from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase
from hermit.kernel.task.models.delegation import (
    ApprovalDelegationPolicy,
    DelegationRecord,
    DelegationScope,
)


class DelegationStoreMixin(KernelStoreTypingBase):
    """Mixin providing delegation-record persistence backed by SQLite."""

    def _init_delegation_schema(self) -> None:
        """Create the delegations table if it does not already exist."""
        self._get_conn().execute(
            """
            CREATE TABLE IF NOT EXISTS delegations (
                delegation_id          TEXT PRIMARY KEY,
                parent_task_id         TEXT NOT NULL,
                child_task_id          TEXT NOT NULL,
                delegated_principal_id TEXT NOT NULL,
                scope_json             TEXT NOT NULL DEFAULT '{}',
                status                 TEXT NOT NULL DEFAULT 'active',
                delegation_grant_ref   TEXT,
                recall_reason          TEXT,
                approval_policy_json   TEXT,
                created_at             REAL NOT NULL,
                updated_at             REAL NOT NULL
            )
            """
        )
        self._get_conn().execute(
            "CREATE INDEX IF NOT EXISTS idx_delegations_parent ON delegations(parent_task_id)"
        )
        self._get_conn().execute(
            "CREATE INDEX IF NOT EXISTS idx_delegations_child  ON delegations(child_task_id)"
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_delegation(
        self,
        *,
        delegation_id: str,
        parent_task_id: str,
        child_task_id: str,
        delegated_principal_id: str,
        scope: DelegationScope,
        delegation_grant_ref: str | None = None,
        approval_delegation_policy: ApprovalDelegationPolicy | None = None,
        created_at: float | None = None,
    ) -> DelegationRecord:
        """Insert a new delegation record and return it."""
        now = created_at if created_at is not None else time.time()
        scope_json = json.dumps(
            {
                "allowed_action_classes": scope.allowed_action_classes,
                "allowed_resource_scopes": scope.allowed_resource_scopes,
                "max_steps": scope.max_steps,
                "budget_tokens": scope.budget_tokens,
            },
            ensure_ascii=False,
        )
        approval_policy_json: str | None = None
        if approval_delegation_policy is not None:
            approval_policy_json = json.dumps(
                {
                    "auto_approve": approval_delegation_policy.auto_approve,
                    "require_parent_approval": (approval_delegation_policy.require_parent_approval),
                    "deny": approval_delegation_policy.deny,
                },
                ensure_ascii=False,
            )
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO delegations (
                    delegation_id, parent_task_id, child_task_id,
                    delegated_principal_id, scope_json, status,
                    delegation_grant_ref, recall_reason,
                    approval_policy_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, NULL, ?, ?, ?)
                """,
                (
                    delegation_id,
                    parent_task_id,
                    child_task_id,
                    delegated_principal_id,
                    scope_json,
                    delegation_grant_ref,
                    approval_policy_json,
                    now,
                    now,
                ),
            )
        row = self._row("SELECT * FROM delegations WHERE delegation_id = ?", (delegation_id,))
        assert row is not None
        return self._delegation_from_row(row)

    def get_delegation_record(self, delegation_id: str) -> DelegationRecord | None:
        """Return a delegation record by its ID, or *None* if not found."""
        row = self._row("SELECT * FROM delegations WHERE delegation_id = ?", (delegation_id,))
        return self._delegation_from_row(row) if row is not None else None

    def find_delegation_by_pair(
        self, parent_task_id: str, child_task_id: str
    ) -> DelegationRecord | None:
        """Return the delegation linking *parent_task_id* -> *child_task_id*, or *None*."""
        row = self._row(
            """
            SELECT * FROM delegations
            WHERE parent_task_id = ? AND child_task_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (parent_task_id, child_task_id),
        )
        return self._delegation_from_row(row) if row is not None else None

    def find_delegation_by_child(self, child_task_id: str) -> DelegationRecord | None:
        """Return the delegation whose child is *child_task_id*, or *None*."""
        row = self._row(
            """
            SELECT * FROM delegations
            WHERE child_task_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (child_task_id,),
        )
        return self._delegation_from_row(row) if row is not None else None

    def list_delegations_for_parent(self, parent_task_id: str) -> list[DelegationRecord]:
        """Return all delegation records whose parent is *parent_task_id*."""
        rows = self._rows(
            "SELECT * FROM delegations WHERE parent_task_id = ? ORDER BY created_at ASC",
            (parent_task_id,),
        )
        return [self._delegation_from_row(r) for r in rows]

    def update_delegation_status(
        self,
        delegation_id: str,
        *,
        status: str,
        recall_reason: str | None = None,
    ) -> None:
        """Update the status (and optional recall_reason) of a delegation record."""
        now = time.time()
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE delegations
                SET status = ?, recall_reason = ?, updated_at = ?
                WHERE delegation_id = ?
                """,
                (status, recall_reason, now, delegation_id),
            )

    # ------------------------------------------------------------------
    # Row -> model
    # ------------------------------------------------------------------

    def _delegation_from_row(self, row: sqlite3.Row) -> DelegationRecord:
        raw_scope: dict[str, Any] = json_loads(row["scope_json"])
        scope = DelegationScope(
            allowed_action_classes=list(raw_scope.get("allowed_action_classes") or []),
            allowed_resource_scopes=list(raw_scope.get("allowed_resource_scopes") or []),
            max_steps=int(raw_scope.get("max_steps") or 0),
            budget_tokens=int(raw_scope.get("budget_tokens") or 0),
        )
        approval_policy: ApprovalDelegationPolicy | None = None
        raw_policy = row["approval_policy_json"]
        if raw_policy is not None:
            policy_data: dict[str, Any] = json_loads(raw_policy)
            approval_policy = ApprovalDelegationPolicy(
                auto_approve=list(policy_data.get("auto_approve") or []),
                require_parent_approval=list(policy_data.get("require_parent_approval") or []),
                deny=list(policy_data.get("deny") or []),
            )
        return DelegationRecord(
            delegation_id=str(row["delegation_id"]),
            parent_task_id=str(row["parent_task_id"]),
            child_task_id=str(row["child_task_id"]),
            delegated_principal_id=str(row["delegated_principal_id"]),
            scope=scope,
            status=str(row["status"]),
            delegation_grant_ref=row["delegation_grant_ref"],
            recall_reason=row["recall_reason"],
            approval_delegation_policy=approval_policy,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )
