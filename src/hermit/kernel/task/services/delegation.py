from __future__ import annotations

from dataclasses import asdict
from typing import Any

import structlog

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.delegation import (
    ApprovalDelegationPolicy,
    DelegationRecord,
    DelegationScope,
)

log = structlog.get_logger()


class DelegationError(RuntimeError):
    """Raised when a delegation operation fails."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TaskDelegationService:
    """Manages governed parent-child task delegation with authority transfer."""

    def __init__(self, store: KernelStore) -> None:
        self.store = store

    def delegate(
        self,
        *,
        parent_task_id: str,
        child_goal: str,
        delegated_principal_id: str,
        scope_constraints: DelegationScope | None = None,
        approval_delegation_policy: ApprovalDelegationPolicy | None = None,
    ) -> str:
        """Create a child task delegated from a parent task.

        Returns the child_task_id.
        """
        parent = self.store.get_task(parent_task_id)
        if parent is None:
            raise DelegationError(
                "parent_not_found",
                f"Parent task not found: {parent_task_id}",
            )
        if parent.status not in ("running", "queued", "planning_ready"):
            raise DelegationError(
                "parent_not_active",
                f"Parent task {parent_task_id} is {parent.status}, cannot delegate.",
            )

        scope = scope_constraints or DelegationScope()

        child = self.store.create_task(
            conversation_id=parent.conversation_id,
            title=f"Delegated: {child_goal[:60]}",
            goal=child_goal,
            source_channel=parent.source_channel,
            status="running",
            owner=delegated_principal_id,
            priority=parent.priority,
            policy_profile=parent.policy_profile,
            parent_task_id=parent_task_id,
            requested_by=parent.owner_principal_id,
        )

        delegation_id = self.store.generate_id("delegation")
        self.store.create_delegation(
            delegation_id=delegation_id,
            parent_task_id=parent_task_id,
            child_task_id=child.task_id,
            delegated_principal_id=delegated_principal_id,
            scope=scope,
            approval_delegation_policy=approval_delegation_policy,
        )

        payload: dict[str, Any] = {
            "delegation_id": delegation_id,
            "parent_task_id": parent_task_id,
            "child_task_id": child.task_id,
            "delegated_principal_id": delegated_principal_id,
            "scope": asdict(scope),
        }
        if approval_delegation_policy is not None:
            payload["approval_delegation_policy"] = asdict(approval_delegation_policy)

        self.store.append_event(
            event_type="delegation.created",
            entity_type="delegation",
            entity_id=delegation_id,
            task_id=parent_task_id,
            actor="kernel",
            payload=payload,
        )

        log.info(
            "delegation.created",
            delegation_id=delegation_id,
            parent_task_id=parent_task_id,
            child_task_id=child.task_id,
        )
        return child.task_id

    def recall(
        self,
        *,
        parent_task_id: str,
        child_task_id: str,
        reason: str = "operator_recall",
    ) -> None:
        """Recall a delegated child task, revoking its delegation."""
        record = self.store.find_delegation_by_pair(parent_task_id, child_task_id)
        if record is None:
            raise DelegationError(
                "delegation_not_found",
                f"No active delegation from {parent_task_id} to {child_task_id}.",
            )
        if record.status != "active":
            raise DelegationError(
                "delegation_not_active",
                f"Delegation {record.delegation_id} is {record.status}, cannot recall.",
            )

        self.store.update_delegation_status(
            record.delegation_id,
            status="recalled",
            recall_reason=reason,
        )

        self.store.update_task_status(child_task_id, "recalled")

        self.store.append_event(
            event_type="delegation.recalled",
            entity_type="delegation",
            entity_id=record.delegation_id,
            task_id=parent_task_id,
            actor="kernel",
            payload={
                "delegation_id": record.delegation_id,
                "parent_task_id": parent_task_id,
                "child_task_id": child_task_id,
                "reason": reason,
            },
        )

        log.info(
            "delegation.recalled",
            delegation_id=record.delegation_id,
            parent_task_id=parent_task_id,
            child_task_id=child_task_id,
            reason=reason,
        )

    def child_completed(self, *, child_task_id: str) -> str | None:
        """Notify the parent that a child task has completed.

        Returns the parent_task_id if delegation was found, None otherwise.
        """
        record = self.store.find_delegation_by_child(child_task_id)
        if record is None:
            return None
        if record.status != "active":
            return None

        self.store.update_delegation_status(
            record.delegation_id,
            status="completed",
        )

        self.store.append_event(
            event_type="delegation.completed",
            entity_type="delegation",
            entity_id=record.delegation_id,
            task_id=record.parent_task_id,
            actor="kernel",
            payload={
                "delegation_id": record.delegation_id,
                "parent_task_id": record.parent_task_id,
                "child_task_id": child_task_id,
            },
        )

        log.info(
            "delegation.completed",
            delegation_id=record.delegation_id,
            parent_task_id=record.parent_task_id,
            child_task_id=child_task_id,
        )
        return record.parent_task_id

    def list_children(self, parent_task_id: str) -> list[dict[str, Any]]:
        """List child task summaries for a given parent task."""
        records = self.store.list_delegations_for_parent(parent_task_id)
        results: list[dict[str, Any]] = []
        for record in records:
            child = self.store.get_task(record.child_task_id)
            results.append(
                {
                    "delegation_id": record.delegation_id,
                    "child_task_id": record.child_task_id,
                    "delegated_principal_id": record.delegated_principal_id,
                    "status": record.status,
                    "child_status": child.status if child else "unknown",
                    "child_goal": child.goal if child else "",
                    "scope": asdict(record.scope),
                }
            )
        return results

    def get_delegation(self, parent_task_id: str, child_task_id: str) -> DelegationRecord | None:
        """Retrieve a specific delegation record."""
        return self.store.find_delegation_by_pair(parent_task_id, child_task_id)

    def check_delegation_approval_policy(
        self,
        *,
        child_task_id: str,
        action_class: str,
    ) -> tuple[str, str | None]:
        """Check whether a child task's approval can be auto-resolved.

        Returns a tuple of (resolution, delegation_id):
        - ('auto_approve', delegation_id) if policy auto-approves
        - ('require_parent_approval', delegation_id) if parent must approve
        - ('deny', delegation_id) if policy denies the action class
        - ('no_policy', None) if no delegation record or no policy configured
        """
        record = self.store.find_delegation_by_child(child_task_id)
        if record is None or record.approval_delegation_policy is None:
            return ("no_policy", None)
        resolution = record.approval_delegation_policy.resolve(action_class)
        return (resolution, record.delegation_id)
