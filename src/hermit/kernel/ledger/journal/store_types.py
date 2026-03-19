from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from hermit.kernel.authority.grants.models import CapabilityGrantRecord
from hermit.kernel.authority.identity.models import PrincipalRecord
from hermit.kernel.authority.workspaces.models import WorkspaceLeaseRecord
from hermit.kernel.task.models.delegation import DelegationRecord, DelegationScope
from hermit.kernel.task.models.records import (
    ApprovalRecord,
    ArtifactRecord,
    AuthorizationPlanRecord,
    BeliefRecord,
    ConversationRecord,
    DecisionRecord,
    EvidenceCaseRecord,
    ExecutionContractRecord,
    IngressRecord,
    MemoryRecord,
    ReceiptRecord,
    ReconciliationRecord,
    RollbackRecord,
    StepAttemptRecord,
    StepRecord,
    TaskRecord,
)
from hermit.plugins.builtin.hooks.scheduler.models import ScheduledJob


class KernelStoreTypingBase:
    def _get_conn(self) -> sqlite3.Connection: ...

    def _id(self, prefix: str) -> str: ...

    def _row(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Row | None: ...

    def _rows(self, query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]: ...

    def _append_event_tx(
        self,
        *,
        event_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        task_id: str | None,
        step_id: str | None = None,
        actor: str = "kernel",
        payload: dict[str, Any] | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> str: ...

    def _ensure_principal_id(
        self,
        actor: str | None,
        *,
        source_channel: str | None = None,
        principal_type: str | None = None,
    ) -> str: ...

    def _conversation_from_row(self, row: sqlite3.Row) -> ConversationRecord: ...

    def _task_from_row(self, row: sqlite3.Row) -> TaskRecord: ...

    def _step_from_row(self, row: sqlite3.Row) -> StepRecord: ...

    def _step_attempt_from_row(self, row: sqlite3.Row) -> StepAttemptRecord: ...

    def _ingress_from_row(self, row: sqlite3.Row) -> IngressRecord: ...

    def _artifact_from_row(self, row: sqlite3.Row) -> ArtifactRecord: ...

    def _approval_from_row(self, row: sqlite3.Row) -> ApprovalRecord: ...

    def _decision_from_row(self, row: sqlite3.Row) -> DecisionRecord: ...

    def _principal_from_row(self, row: sqlite3.Row) -> PrincipalRecord: ...

    def _capability_grant_from_row(self, row: sqlite3.Row) -> CapabilityGrantRecord: ...

    def _workspace_lease_from_row(self, row: sqlite3.Row) -> WorkspaceLeaseRecord: ...

    def _receipt_from_row(self, row: sqlite3.Row) -> ReceiptRecord: ...

    def _belief_from_row(self, row: sqlite3.Row) -> BeliefRecord: ...

    def _memory_record_from_row(self, row: sqlite3.Row) -> MemoryRecord: ...

    def _rollback_from_row(self, row: sqlite3.Row) -> RollbackRecord: ...

    def _execution_contract_from_row(self, row: sqlite3.Row) -> ExecutionContractRecord: ...

    def _evidence_case_from_row(self, row: sqlite3.Row) -> EvidenceCaseRecord: ...

    def _authorization_plan_from_row(self, row: sqlite3.Row) -> AuthorizationPlanRecord: ...

    def _reconciliation_from_row(self, row: sqlite3.Row) -> ReconciliationRecord: ...

    def _schedule_from_row(self, row: sqlite3.Row) -> ScheduledJob: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def get_step(self, step_id: str) -> StepRecord | None: ...

    def get_step_attempt(self, step_attempt_id: str) -> StepAttemptRecord | None: ...

    def batch_get_step_attempts(
        self, step_attempt_ids: list[str]
    ) -> dict[str, StepAttemptRecord]: ...

    def get_ingress(self, ingress_id: str) -> IngressRecord | None: ...

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None: ...

    def get_approval(self, approval_id: str) -> ApprovalRecord | None: ...

    def get_decision(self, decision_id: str) -> DecisionRecord | None: ...

    def get_capability_grant(self, grant_id: str) -> CapabilityGrantRecord | None: ...

    def list_capability_grants_by_parent(
        self, *, parent_grant_ref: str
    ) -> list[CapabilityGrantRecord]: ...

    def get_workspace_lease(self, lease_id: str) -> WorkspaceLeaseRecord | None: ...

    def get_receipt(self, receipt_id: str) -> ReceiptRecord | None: ...

    def get_belief(self, belief_id: str) -> BeliefRecord | None: ...

    def get_memory_record(self, memory_id: str) -> MemoryRecord | None: ...

    def get_rollback(self, rollback_id: str) -> RollbackRecord | None: ...

    def get_execution_contract(self, contract_id: str) -> ExecutionContractRecord | None: ...

    def get_evidence_case(self, evidence_case_id: str) -> EvidenceCaseRecord | None: ...

    def get_authorization_plan(
        self, authorization_plan_id: str
    ) -> AuthorizationPlanRecord | None: ...

    def get_reconciliation(self, reconciliation_id: str) -> ReconciliationRecord | None: ...

    def list_approvals(
        self, *, task_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[ApprovalRecord]: ...

    def list_events(
        self,
        *,
        task_id: str | None = None,
        after_event_seq: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]: ...

    def list_events_for_tasks(
        self,
        task_ids: list[str],
        *,
        limit_per_task: int = 500,
    ) -> dict[str, list[dict[str, Any]]]: ...

    def get_last_event_per_task(
        self,
        task_ids: list[str],
    ) -> dict[str, dict[str, Any]]: ...

    def list_artifacts_for_tasks(
        self,
        task_ids: list[str],
        *,
        limit_per_task: int = 50,
    ) -> dict[str, list[Any]]: ...

    def batch_get_artifacts(self, artifact_ids: list[str]) -> dict[str, Any]: ...

    def list_open_tasks_for_conversation(
        self, *, conversation_id: str, limit: int = 50
    ) -> list[TaskRecord]: ...

    def set_conversation_focus(
        self, conversation_id: str, *, task_id: str | None, reason: str = ""
    ) -> None: ...

    def create_delegation(
        self,
        *,
        delegation_id: str,
        parent_task_id: str,
        child_task_id: str,
        delegated_principal_id: str,
        scope: DelegationScope,
        delegation_grant_ref: str | None = None,
        created_at: float | None = None,
    ) -> DelegationRecord: ...

    def get_delegation_record(self, delegation_id: str) -> DelegationRecord | None: ...

    def find_delegation_by_pair(
        self, parent_task_id: str, child_task_id: str
    ) -> DelegationRecord | None: ...

    def find_delegation_by_child(self, child_task_id: str) -> DelegationRecord | None: ...

    def list_delegations_for_parent(self, parent_task_id: str) -> list[DelegationRecord]: ...

    def update_delegation_status(
        self,
        delegation_id: str,
        *,
        status: str,
        recall_reason: str | None = None,
    ) -> None: ...
