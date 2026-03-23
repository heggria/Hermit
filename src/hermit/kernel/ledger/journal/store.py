from __future__ import annotations

import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermit.kernel.task.models.records import BlackboardRecord

from hermit.kernel.authority.identity.models import PrincipalRecord
from hermit.kernel.execution.competition.store import CompetitionStoreMixin
from hermit.kernel.ledger.events.store_ledger import KernelLedgerStoreMixin
from hermit.kernel.ledger.journal.store_assurance import AssuranceStoreMixin
from hermit.kernel.ledger.journal.store_records import KernelStoreRecordMixin
from hermit.kernel.ledger.journal.store_scheduler import KernelSchedulerStoreMixin
from hermit.kernel.ledger.journal.store_support import canonical_json as _canonical_json
from hermit.kernel.ledger.journal.store_support import (
    canonical_json_from_raw as _canonical_json_from_raw,
)
from hermit.kernel.ledger.journal.store_support import sha256_hex as _sha256_hex
from hermit.kernel.ledger.journal.store_tasks import KernelTaskStoreMixin
from hermit.kernel.ledger.journal.store_programs import ProgramStoreMixin
from hermit.kernel.ledger.journal.store_self_iterate import SelfIterateStoreMixin
from hermit.kernel.ledger.journal.store_v2 import KernelV2StoreMixin
from hermit.kernel.ledger.journal.store_teams import KernelTeamStoreMixin
from hermit.kernel.ledger.projections.store_projection import KernelProjectionStoreMixin
from hermit.kernel.signals.store import SignalStoreMixin
from hermit.kernel.task.services.delegation_store import DelegationStoreMixin

_SCHEMA_VERSION = "18"
_MIGRATABLE_SCHEMA_VERSIONS = {
    "5", "6", "7", "8", "9", "10", "11", "12", "13", "14",
    "15", "16", "17", _SCHEMA_VERSION,
}
_KNOWN_KERNEL_TABLES = {
    "conversations",
    "conversation_projection_cache",
    "principals",
    "ingresses",
    "tasks",
    "steps",
    "step_attempts",
    "events",
    "event_hashes",
    "artifacts",
    "approvals",
    "receipts",
    "decisions",
    "capability_grants",
    "workspace_leases",
    "beliefs",
    "memory_records",
    "rollbacks",
    "projection_cache",
    "schedule_specs",
    "schedule_history",
    "execution_contracts",
    "evidence_cases",
    "authorization_plans",
    "reconciliations",
    "evidence_signals",
    "competitions",
    "competition_candidates",
    "memory_embeddings",
    "memory_graph_edges",
    "memory_entity_triples",
    "procedural_memories",
    "delegations",
    "hash_chain_checkpoints",
    "blackboard_entries",
    "observation_tickets",
    "programs",
    "spec_backlog",
    "iteration_lessons",
    "teams",
    "milestones",
    "assurance_trace_envelopes",
    "assurance_scenarios",
    "assurance_reports",
    "assurance_replay_entries",
}


class KernelSchemaError(RuntimeError):
    """Raised when an existing kernel database does not match the hard-cut schema."""


class KernelStore(
    KernelTaskStoreMixin,
    KernelLedgerStoreMixin,
    KernelProjectionStoreMixin,
    KernelSchedulerStoreMixin,
    KernelStoreRecordMixin,
    KernelV2StoreMixin,
    SignalStoreMixin,
    CompetitionStoreMixin,
    AssuranceStoreMixin,
    DelegationStoreMixin,
    SelfIterateStoreMixin,
    ProgramStoreMixin,
    KernelTeamStoreMixin,
):
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._in_memory = str(db_path) == ":memory:"
        if not self._in_memory:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        connect_target: str | Path = ":memory:" if self._in_memory else self.db_path
        self._conn = sqlite3.connect(connect_target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._validate_existing_schema()
        self._init_schema()


    def _get_conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __del__(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def schema_version(self) -> str:
        with self._lock:
            row = self._row("SELECT value FROM kernel_meta WHERE key = 'schema_version'")
        return str(row["value"]) if row is not None else ""

    def _existing_tables(self) -> set[str]:
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        return {str(row[0]) for row in cursor.fetchall()}

    def _validate_existing_schema(self) -> None:
        tables = self._existing_tables()
        if not tables:
            return
        if "kernel_meta" not in tables:
            if tables & _KNOWN_KERNEL_TABLES:
                raise KernelSchemaError(
                    f"Existing kernel database at {self.db_path} uses an unsupported pre-v3 schema. "
                    "This is a hard cut release: archive or delete kernel/state.db before restarting Hermit."
                )
            return
        row = self._conn.execute(
            "SELECT value FROM kernel_meta WHERE key = 'schema_version'"
        ).fetchone()
        version = str(row[0]) if row is not None else ""
        if version not in _MIGRATABLE_SCHEMA_VERSIONS:
            raise KernelSchemaError(
                f"Existing kernel database at {self.db_path} has schema_version={version or 'unknown'}, "
                f"but Hermit requires schema_version={_SCHEMA_VERSION}. "
                "Archive or delete kernel/state.db before restarting Hermit."
            )

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kernel_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    source_channel TEXT NOT NULL,
                    source_ref TEXT,
                    last_task_id TEXT,
                    focus_task_id TEXT,
                    focus_reason TEXT,
                    focus_updated_at REAL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    total_input_tokens INTEGER NOT NULL DEFAULT 0,
                    total_output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                    total_cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_projection_cache (
                    conversation_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    event_head_hash TEXT,
                    payload_json TEXT NOT NULL,
                    built_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS principals (
                    principal_id TEXT PRIMARY KEY,
                    principal_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    source_channel TEXT,
                    external_ref TEXT,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ingresses (
                    ingress_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    source_channel TEXT NOT NULL,
                    actor_principal_id TEXT,
                    raw_text TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    prompt_ref TEXT,
                    reply_to_ref TEXT,
                    quoted_message_ref TEXT,
                    explicit_task_ref TEXT,
                    referenced_artifact_refs_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resolution TEXT,
                    chosen_task_id TEXT,
                    parent_task_id TEXT,
                    confidence REAL,
                    margin REAL,
                    rationale_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    owner_principal_id TEXT NOT NULL,
                    policy_profile TEXT NOT NULL,
                    source_channel TEXT NOT NULL,
                    parent_task_id TEXT,
                    task_contract_ref TEXT,
                    requested_by_principal_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS steps (
                    step_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    input_ref TEXT,
                    output_ref TEXT,
                    title TEXT,
                    contract_ref TEXT,
                    depends_on_json TEXT NOT NULL DEFAULT '[]',
                    max_attempts INTEGER NOT NULL DEFAULT 1,
                    started_at REAL,
                    finished_at REAL,
                    created_at REAL,
                    updated_at REAL
                );
                CREATE TABLE IF NOT EXISTS step_attempts (
                    step_attempt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    queue_priority INTEGER NOT NULL DEFAULT 0,
                    waiting_reason TEXT,
                    approval_id TEXT,
                    decision_id TEXT,
                    capability_grant_id TEXT,
                    workspace_lease_id TEXT,
                    state_witness_ref TEXT,
                    context_pack_ref TEXT,
                    working_state_ref TEXT,
                    environment_ref TEXT,
                    action_request_ref TEXT,
                    policy_result_ref TEXT,
                    approval_packet_ref TEXT,
                    execution_contract_ref TEXT,
                    evidence_case_ref TEXT,
                    authorization_plan_ref TEXT,
                    reconciliation_ref TEXT,
                    pending_execution_ref TEXT,
                    idempotency_key TEXT,
                    executor_mode TEXT,
                    policy_version TEXT,
                    contract_version INTEGER NOT NULL DEFAULT 0,
                    reentry_boundary TEXT,
                    reentry_reason TEXT,
                    selected_contract_template_ref TEXT,
                    resume_from_ref TEXT,
                    superseded_by_step_attempt_id TEXT,
                    started_at REAL,
                    finished_at REAL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    task_id TEXT,
                    step_id TEXT,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_principal_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    causation_id TEXT,
                    correlation_id TEXT,
                    event_hash TEXT,
                    prev_event_hash TEXT,
                    hash_chain_algo TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    step_id TEXT,
                    kind TEXT NOT NULL,
                    uri TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    producer TEXT NOT NULL,
                    retention_class TEXT NOT NULL,
                    trust_tier TEXT NOT NULL,
                    artifact_class TEXT,
                    media_type TEXT,
                    byte_size INTEGER,
                    sensitivity_class TEXT,
                    lineage_ref TEXT,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    decision_type TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    summary TEXT,
                    rationale TEXT,
                    evidence_refs_json TEXT NOT NULL,
                    policy_ref TEXT,
                    approval_ref TEXT,
                    contract_ref TEXT,
                    authorization_plan_ref TEXT,
                    evidence_case_ref TEXT,
                    reconciliation_ref TEXT,
                    action_type TEXT,
                    risk_level TEXT,
                    reversible INTEGER,
                    decided_by_principal_id TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_leases (
                    lease_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    holder_principal_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    resource_scope_json TEXT NOT NULL,
                    environment_ref TEXT,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    expires_at REAL,
                    released_at REAL
                );
                CREATE TABLE IF NOT EXISTS capability_grants (
                    grant_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    decision_ref TEXT NOT NULL,
                    approval_ref TEXT,
                    policy_ref TEXT,
                    issued_to_principal_id TEXT NOT NULL,
                    issued_by_principal_id TEXT NOT NULL,
                    workspace_lease_ref TEXT,
                    action_class TEXT NOT NULL,
                    resource_scope_json TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    idempotency_key TEXT,
                    status TEXT NOT NULL,
                    issued_at REAL NOT NULL,
                    expires_at REAL,
                    consumed_at REAL,
                    revoked_at REAL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approval_type TEXT NOT NULL,
                    requested_action_json TEXT NOT NULL,
                    request_packet_ref TEXT,
                    requested_action_ref TEXT,
                    approval_packet_ref TEXT,
                    policy_result_ref TEXT,
                    requested_contract_ref TEXT,
                    authorization_plan_ref TEXT,
                    evidence_case_ref TEXT,
                    drift_expiry REAL,
                    fallback_contract_refs_json TEXT NOT NULL DEFAULT '[]',
                    decision_ref TEXT,
                    state_witness_ref TEXT,
                    requested_at REAL NOT NULL,
                    expires_at REAL,
                    resolved_at REAL,
                    resolved_by_principal_id TEXT,
                    resolution_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    receipt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    receipt_class TEXT,
                    input_refs_json TEXT NOT NULL,
                    environment_ref TEXT,
                    policy_result_json TEXT NOT NULL,
                    approval_ref TEXT,
                    output_refs_json TEXT NOT NULL,
                    result_summary TEXT NOT NULL,
                    result_code TEXT NOT NULL,
                    decision_ref TEXT,
                    capability_grant_ref TEXT,
                    workspace_lease_ref TEXT,
                    policy_ref TEXT,
                    action_request_ref TEXT,
                    policy_result_ref TEXT,
                    contract_ref TEXT,
                    authorization_plan_ref TEXT,
                    witness_ref TEXT,
                    idempotency_key TEXT,
                    receipt_bundle_ref TEXT,
                    proof_mode TEXT NOT NULL DEFAULT 'none',
                    verifiability TEXT,
                    signature TEXT,
                    signer_ref TEXT,
                    observed_effect_summary TEXT,
                    reconciliation_required INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_contracts (
                    contract_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    proposed_action_refs_json TEXT NOT NULL,
                    expected_effects_json TEXT NOT NULL,
                    success_criteria_json TEXT NOT NULL,
                    evidence_case_ref TEXT,
                    authorization_plan_ref TEXT,
                    reversibility_class TEXT NOT NULL,
                    required_receipt_classes_json TEXT NOT NULL,
                    drift_budget_json TEXT NOT NULL,
                    expiry_at REAL,
                    status TEXT NOT NULL,
                    fallback_contract_refs_json TEXT NOT NULL,
                    operator_summary TEXT,
                    risk_budget_json TEXT NOT NULL,
                    expected_artifact_shape_json TEXT NOT NULL,
                    contract_version INTEGER NOT NULL DEFAULT 1,
                    action_contract_refs_json TEXT NOT NULL,
                    state_witness_ref TEXT,
                    rollback_expectation TEXT,
                    selected_template_ref TEXT,
                    superseded_by_contract_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_cases (
                    evidence_case_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    subject_kind TEXT NOT NULL,
                    subject_ref TEXT NOT NULL,
                    support_refs_json TEXT NOT NULL,
                    contradiction_refs_json TEXT NOT NULL,
                    freshness_window_json TEXT NOT NULL,
                    sufficiency_score REAL NOT NULL,
                    drift_sensitivity TEXT NOT NULL,
                    unresolved_gaps_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    witness_refs_json TEXT NOT NULL,
                    invalidates_refs_json TEXT NOT NULL,
                    last_checked_at REAL,
                    confidence_interval_json TEXT NOT NULL,
                    freshness_basis TEXT,
                    operator_summary TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS authorization_plans (
                    authorization_plan_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    contract_ref TEXT NOT NULL,
                    policy_profile_ref TEXT NOT NULL,
                    requested_action_classes_json TEXT NOT NULL,
                    required_decision_refs_json TEXT NOT NULL,
                    approval_route TEXT NOT NULL,
                    witness_requirements_json TEXT NOT NULL,
                    proposed_grant_shape_json TEXT NOT NULL,
                    downgrade_options_json TEXT NOT NULL,
                    current_gaps_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    estimated_authority_cost REAL,
                    expiry_constraints_json TEXT NOT NULL,
                    revalidation_rules_json TEXT NOT NULL,
                    operator_packet_ref TEXT,
                    required_workspace_mode TEXT,
                    required_secret_policy TEXT,
                    proposed_lease_shape_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reconciliations (
                    reconciliation_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    contract_ref TEXT NOT NULL,
                    receipt_refs_json TEXT NOT NULL,
                    observed_output_refs_json TEXT NOT NULL,
                    intended_effect_summary TEXT NOT NULL,
                    authorized_effect_summary TEXT NOT NULL,
                    observed_effect_summary TEXT NOT NULL,
                    receipted_effect_summary TEXT NOT NULL,
                    result_class TEXT NOT NULL,
                    confidence_delta REAL NOT NULL,
                    recommended_resolution TEXT NOT NULL,
                    rollback_recommendation_ref TEXT,
                    invalidated_belief_refs_json TEXT NOT NULL,
                    superseded_memory_refs_json TEXT NOT NULL,
                    promoted_template_ref TEXT,
                    promoted_memory_refs_json TEXT NOT NULL,
                    operator_summary TEXT,
                    final_state_witness_ref TEXT,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS beliefs (
                    belief_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    conversation_id TEXT,
                    scope_kind TEXT NOT NULL,
                    scope_ref TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    trust_tier TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    evidence_case_ref TEXT,
                    supersedes_json TEXT NOT NULL,
                    contradicts_json TEXT NOT NULL,
                    epistemic_origin TEXT NOT NULL DEFAULT 'observed',
                    freshness_class TEXT,
                    last_validated_at REAL,
                    validation_basis TEXT,
                    supersession_reason TEXT,
                    memory_ref TEXT,
                    invalidated_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_records (
                    memory_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    conversation_id TEXT,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    trust_tier TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    memory_kind TEXT NOT NULL DEFAULT 'durable_fact',
                    validation_basis TEXT,
                    last_validated_at REAL,
                    supersession_reason TEXT,
                    learned_from_reconciliation_ref TEXT,
                    supersedes_json TEXT NOT NULL,
                    source_belief_ref TEXT,
                    invalidated_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rollbacks (
                    rollback_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    receipt_ref TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_summary TEXT,
                    artifact_refs_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    executed_at REAL
                );
                CREATE TABLE IF NOT EXISTS projection_cache (
                    task_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    event_head_hash TEXT,
                    payload_json TEXT NOT NULL,
                    built_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedule_specs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    cron_expr TEXT,
                    once_at REAL,
                    interval_seconds INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    last_run_at REAL,
                    next_run_at REAL,
                    max_retries INTEGER NOT NULL DEFAULT 0,
                    feishu_chat_id TEXT
                );
                CREATE TABLE IF NOT EXISTS schedule_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    job_name TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL NOT NULL,
                    success INTEGER NOT NULL,
                    result_text TEXT NOT NULL,
                    error TEXT,
                    delivery_status TEXT,
                    delivery_channel TEXT,
                    delivery_mode TEXT,
                    delivery_target TEXT,
                    delivery_message_id TEXT,
                    delivery_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON tasks(conversation_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ingresses_conversation ON ingresses(conversation_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ingresses_chosen_task ON ingresses(chosen_task_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ingresses_status ON ingresses(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, event_seq);
                CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, requested_at);
                CREATE INDEX IF NOT EXISTS idx_receipts_task ON receipts(task_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_decisions_task ON decisions(task_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_capability_grants_task ON capability_grants(task_id, issued_at);
                CREATE INDEX IF NOT EXISTS idx_workspace_leases_attempt ON workspace_leases(step_attempt_id, acquired_at);
                CREATE INDEX IF NOT EXISTS idx_workspace_leases_holder ON workspace_leases(holder_principal_id, status, acquired_at);
                CREATE INDEX IF NOT EXISTS idx_beliefs_scope ON beliefs(scope_kind, scope_ref, status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_records_status ON memory_records(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_rollbacks_receipt ON rollbacks(receipt_ref, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_execution_contracts_attempt ON execution_contracts(step_attempt_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_evidence_cases_subject ON evidence_cases(subject_kind, subject_ref, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_authorization_plans_attempt ON authorization_plans(step_attempt_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_reconciliations_attempt ON reconciliations(step_attempt_id, created_at DESC);
                """
            )
            self._ensure_column("receipts", "receipt_bundle_ref", "TEXT")
            self._ensure_column("receipts", "proof_mode", "TEXT NOT NULL DEFAULT 'none'")
            self._ensure_column("receipts", "signature", "TEXT")
            self._ensure_column("receipts", "rollback_supported", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("receipts", "rollback_strategy", "TEXT")
            self._ensure_column(
                "receipts", "rollback_status", "TEXT NOT NULL DEFAULT 'not_requested'"
            )
            self._ensure_column("receipts", "rollback_ref", "TEXT")
            self._ensure_column(
                "receipts", "rollback_artifact_refs_json", "TEXT NOT NULL DEFAULT '[]'"
            )
            self._ensure_column("tasks", "task_contract_ref", "TEXT")
            self._ensure_column("steps", "title", "TEXT")
            self._ensure_column("steps", "contract_ref", "TEXT")
            self._ensure_column("steps", "depends_on_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column("steps", "max_attempts", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column("steps", "created_at", "REAL")
            self._ensure_column("steps", "updated_at", "REAL")
            self._ensure_column("events", "event_hash", "TEXT")
            self._ensure_column("events", "prev_event_hash", "TEXT")
            self._ensure_column("events", "hash_chain_algo", "TEXT")
            self._ensure_column("conversations", "focus_task_id", "TEXT")
            self._ensure_column("conversations", "focus_reason", "TEXT")
            self._ensure_column("conversations", "focus_updated_at", "REAL")
            self._ensure_column("beliefs", "claim_text", "TEXT")
            self._ensure_column(
                "beliefs", "structured_assertion_json", "TEXT NOT NULL DEFAULT '{}'"
            )
            self._ensure_column("beliefs", "promotion_candidate", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column("memory_records", "claim_text", "TEXT")
            self._ensure_column(
                "memory_records", "structured_assertion_json", "TEXT NOT NULL DEFAULT '{}'"
            )
            self._ensure_column("memory_records", "scope_kind", "TEXT")
            self._ensure_column("memory_records", "scope_ref", "TEXT")
            self._ensure_column("memory_records", "promotion_reason", "TEXT")
            self._ensure_column("memory_records", "retention_class", "TEXT")
            self._ensure_column(
                "memory_records", "supersedes_memory_ids_json", "TEXT NOT NULL DEFAULT '[]'"
            )
            self._ensure_column("memory_records", "superseded_by_memory_id", "TEXT")
            self._ensure_column("memory_records", "invalidation_reason", "TEXT")
            self._ensure_column("memory_records", "expires_at", "REAL")
            self._ensure_column("step_attempts", "queue_priority", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("step_attempts", "superseded_by_step_attempt_id", "TEXT")
            self._ensure_column("step_attempts", "context_pack_ref", "TEXT")
            self._ensure_column("step_attempts", "working_state_ref", "TEXT")
            self._ensure_column("step_attempts", "environment_ref", "TEXT")
            self._ensure_column("step_attempts", "action_request_ref", "TEXT")
            self._ensure_column("step_attempts", "execution_contract_ref", "TEXT")
            self._ensure_column("step_attempts", "evidence_case_ref", "TEXT")
            self._ensure_column("step_attempts", "authorization_plan_ref", "TEXT")
            self._ensure_column("step_attempts", "reconciliation_ref", "TEXT")
            self._ensure_column("step_attempts", "contract_version", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("step_attempts", "reentry_boundary", "TEXT")
            self._ensure_column("step_attempts", "reentry_reason", "TEXT")
            self._ensure_column("step_attempts", "selected_contract_template_ref", "TEXT")
            self._ensure_column("schedule_history", "delivery_status", "TEXT")
            self._ensure_column("schedule_history", "delivery_channel", "TEXT")
            self._ensure_column("schedule_history", "delivery_mode", "TEXT")
            self._ensure_column("schedule_history", "delivery_target", "TEXT")
            self._ensure_column("schedule_history", "delivery_message_id", "TEXT")
            self._ensure_column("schedule_history", "delivery_error", "TEXT")
            self._ensure_column("step_attempts", "policy_result_ref", "TEXT")
            self._ensure_column("step_attempts", "approval_packet_ref", "TEXT")
            self._ensure_column("step_attempts", "pending_execution_ref", "TEXT")
            self._ensure_column("step_attempts", "idempotency_key", "TEXT")
            self._ensure_column("step_attempts", "executor_mode", "TEXT")
            self._ensure_column("step_attempts", "policy_version", "TEXT")
            self._ensure_column("step_attempts", "resume_from_ref", "TEXT")
            self._ensure_column("artifacts", "artifact_class", "TEXT")
            self._ensure_column("artifacts", "media_type", "TEXT")
            self._ensure_column("artifacts", "byte_size", "INTEGER")
            self._ensure_column("artifacts", "sensitivity_class", "TEXT")
            self._ensure_column("artifacts", "lineage_ref", "TEXT")
            self._ensure_column("decisions", "summary", "TEXT")
            self._ensure_column("decisions", "rationale", "TEXT")
            self._ensure_column("decisions", "risk_level", "TEXT")
            self._ensure_column("decisions", "reversible", "INTEGER")
            self._ensure_column("decisions", "contract_ref", "TEXT")
            self._ensure_column("decisions", "authorization_plan_ref", "TEXT")
            self._ensure_column("decisions", "evidence_case_ref", "TEXT")
            self._ensure_column("decisions", "reconciliation_ref", "TEXT")
            self._ensure_column("approvals", "requested_action_ref", "TEXT")
            self._ensure_column("approvals", "approval_packet_ref", "TEXT")
            self._ensure_column("approvals", "policy_result_ref", "TEXT")
            self._ensure_column("approvals", "requested_contract_ref", "TEXT")
            self._ensure_column("approvals", "authorization_plan_ref", "TEXT")
            self._ensure_column("approvals", "evidence_case_ref", "TEXT")
            self._ensure_column("approvals", "drift_expiry", "REAL")
            self._ensure_column(
                "approvals", "fallback_contract_refs_json", "TEXT NOT NULL DEFAULT '[]'"
            )
            self._ensure_column("approvals", "expires_at", "REAL")
            self._ensure_column("receipts", "receipt_class", "TEXT")
            self._ensure_column("receipts", "action_request_ref", "TEXT")
            self._ensure_column("receipts", "policy_result_ref", "TEXT")
            self._ensure_column("receipts", "contract_ref", "TEXT")
            self._ensure_column("receipts", "authorization_plan_ref", "TEXT")
            self._ensure_column("receipts", "observed_effect_summary", "TEXT")
            self._ensure_column("receipts", "reconciliation_required", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("receipts", "verifiability", "TEXT")
            self._ensure_column("receipts", "signer_ref", "TEXT")
            self._ensure_column("beliefs", "evidence_case_ref", "TEXT")
            self._ensure_column("beliefs", "epistemic_origin", "TEXT NOT NULL DEFAULT 'observed'")
            self._ensure_column("beliefs", "freshness_class", "TEXT")
            self._ensure_column("beliefs", "last_validated_at", "REAL")
            self._ensure_column("beliefs", "validation_basis", "TEXT")
            self._ensure_column("beliefs", "supersession_reason", "TEXT")
            self._ensure_column(
                "memory_records", "memory_kind", "TEXT NOT NULL DEFAULT 'durable_fact'"
            )
            self._ensure_column("memory_records", "validation_basis", "TEXT")
            self._ensure_column("memory_records", "last_validated_at", "REAL")
            self._ensure_column("memory_records", "supersession_reason", "TEXT")
            self._ensure_column("memory_records", "learned_from_reconciliation_ref", "TEXT")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_step_attempts_ready_queue ON step_attempts(status, queue_priority DESC, started_at ASC)"
            )
            self._ensure_column("memory_records", "freshness_class", "TEXT")
            self._ensure_column("memory_records", "last_accessed_at", "REAL")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id TEXT PRIMARY KEY,
                    embedding BLOB NOT NULL,
                    model_name TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_graph_edges (
                    edge_id TEXT PRIMARY KEY,
                    from_memory_id TEXT NOT NULL,
                    to_memory_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_graph_edges_from
                    ON memory_graph_edges(from_memory_id);
                CREATE INDEX IF NOT EXISTS idx_graph_edges_to
                    ON memory_graph_edges(to_memory_id);
                CREATE TABLE IF NOT EXISTS memory_entity_triples (
                    triple_id TEXT PRIMARY KEY,
                    source_memory_id TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    valid_from REAL NOT NULL,
                    valid_until REAL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_triples_subject
                    ON memory_entity_triples(subject);
                CREATE INDEX IF NOT EXISTS idx_triples_object
                    ON memory_entity_triples(object);
                CREATE INDEX IF NOT EXISTS idx_triples_source
                    ON memory_entity_triples(source_memory_id);
                CREATE TABLE IF NOT EXISTS procedural_memories (
                    procedure_id TEXT PRIMARY KEY,
                    trigger_pattern TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    source_memory_ids_json TEXT NOT NULL DEFAULT '[]',
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            self._init_signal_schema()
            self._init_competition_schema()
            self._init_assurance_schema()
            self._migrate_memory_schema_v4()
            self._migrate_dag_schema_v11()
            self._migrate_kernel_convergence_v6()
            self._migrate_category_english_v8()
            self._migrate_memory_kind_index_v12()
            self._migrate_hash_chain_checkpoints_v13()
            self._migrate_verification_v14()
            self._migrate_blackboard_v15()
            self._migrate_observation_tickets_v16()
            self._migrate_budget_v17()
            self._migrate_self_iterate_v18()
            self._migrate_contract_verification_fields()
            self._ensure_column("memory_records", "importance", "INTEGER NOT NULL DEFAULT 5")
            self._ensure_column("step_attempts", "claimed_at", "REAL")
            self._ensure_column("step_attempts", "waiting_reason", "TEXT")
            self._ensure_column("step_attempts", "last_heartbeat_at", "REAL")
            self._ensure_column("step_attempts", "status_reason", "TEXT")
            self._migrate_event_hashes_table()
            self._backfill_event_hash_chain()
            self._conn.execute(
                """
                INSERT INTO kernel_meta(key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (_SCHEMA_VERSION,),
            )

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing = {
            str(row["name"]) for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column in existing:
            return
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _migrate_memory_schema_v4(self) -> None:
        self._conn.execute(
            """
            UPDATE beliefs
            SET claim_text = COALESCE(NULLIF(claim_text, ''), content)
            WHERE claim_text IS NULL OR claim_text = ''
            """
        )
        self._conn.execute(
            """
            UPDATE memory_records
            SET claim_text = COALESCE(NULLIF(claim_text, ''), content)
            WHERE claim_text IS NULL OR claim_text = ''
            """
        )
        self._conn.execute(
            """
            UPDATE memory_records
            SET scope_kind = CASE
                    WHEN category IN ('用户偏好', 'user_preference') THEN 'global'
                    WHEN category IN ('项目约定', '工具与环境', '环境与工具', 'project_convention', 'tooling_environment') THEN 'workspace'
                    ELSE 'conversation'
                END
            WHERE scope_kind IS NULL OR scope_kind = ''
            """
        )
        self._conn.execute(
            """
            UPDATE memory_records
            SET scope_ref = CASE
                    WHEN scope_kind = 'global' THEN 'global'
                    WHEN scope_kind = 'workspace' THEN 'workspace:default'
                    ELSE COALESCE(conversation_id, 'conversation:unknown')
                END
            WHERE scope_ref IS NULL OR scope_ref = ''
            """
        )
        self._conn.execute(
            """
            UPDATE memory_records
            SET retention_class = CASE
                    WHEN category IN ('用户偏好', 'user_preference') THEN 'user_preference'
                    WHEN category IN ('项目约定', 'project_convention') THEN 'project_convention'
                    WHEN category IN ('工具与环境', '环境与工具', 'tooling_environment') THEN 'tooling_environment'
                    WHEN category IN ('进行中的任务', 'active_task') THEN 'task_state'
                    ELSE 'volatile_fact'
                END
            WHERE retention_class IS NULL OR retention_class = ''
            """
        )
        self._conn.execute(
            """
            UPDATE memory_records
            SET promotion_reason = COALESCE(NULLIF(promotion_reason, ''), 'legacy_memory_migration')
            WHERE promotion_reason IS NULL OR promotion_reason = ''
            """
        )
        self._conn.execute(
            """
            UPDATE memory_records
            SET status = 'invalidated',
                invalidation_reason = COALESCE(NULLIF(invalidation_reason, ''), 'superseded'),
                invalidated_at = COALESCE(invalidated_at, updated_at, created_at, ?)
            WHERE status = 'superseded'
            """,
            (time.time(),),
        )

    def _migrate_kernel_convergence_v6(self) -> None:
        now = time.time()
        self._conn.execute(
            """
            UPDATE steps
            SET title = COALESCE(NULLIF(title, ''), kind),
                max_attempts = COALESCE(NULLIF(max_attempts, 0), 1),
                created_at = COALESCE(created_at, started_at, ?),
                updated_at = COALESCE(updated_at, finished_at, started_at, ?)
            WHERE title IS NULL
               OR title = ''
               OR max_attempts IS NULL
               OR max_attempts = 0
               OR created_at IS NULL
               OR updated_at IS NULL
            """,
            (now, now),
        )
        self._conn.execute(
            """
            UPDATE decisions
            SET summary = COALESCE(NULLIF(summary, ''), reason),
                rationale = COALESCE(NULLIF(rationale, ''), reason)
            WHERE summary IS NULL
               OR summary = ''
               OR rationale IS NULL
               OR rationale = ''
            """
        )
        self._conn.execute(
            """
            UPDATE approvals
            SET approval_packet_ref = COALESCE(NULLIF(approval_packet_ref, ''), request_packet_ref)
            WHERE approval_packet_ref IS NULL OR approval_packet_ref = ''
            """
        )
        self._conn.execute(
            """
            UPDATE receipts
            SET receipt_class = COALESCE(NULLIF(receipt_class, ''), action_type),
                policy_result_ref = COALESCE(NULLIF(policy_result_ref, ''), policy_ref),
                verifiability = COALESCE(
                    NULLIF(verifiability, ''),
                    CASE
                        WHEN proof_mode = 'signed_with_inclusion_proof' THEN 'strong_signed_with_inclusion_proof'
                        WHEN proof_mode = 'signed' THEN 'signed_receipt'
                        WHEN receipt_bundle_ref IS NOT NULL AND receipt_bundle_ref != '' THEN 'baseline_verifiable'
                        ELSE 'hash_linked_only'
                    END
                )
            WHERE receipt_class IS NULL
               OR receipt_class = ''
               OR policy_result_ref IS NULL
               OR policy_result_ref = ''
               OR verifiability IS NULL
               OR verifiability = ''
            """
        )

    def _migrate_category_english_v8(self) -> None:
        mapping = [
            ("用户偏好", "user_preference"),
            ("项目约定", "project_convention"),
            ("技术决策", "tech_decision"),
            ("环境与工具", "tooling_environment"),
            ("工具与环境", "tooling_environment"),
            ("其他", "other"),
            ("进行中的任务", "active_task"),
        ]
        for chinese, english in mapping:
            self._conn.execute(
                "UPDATE memory_records SET category = ? WHERE category = ?",
                (english, chinese),
            )
            self._conn.execute(
                "UPDATE beliefs SET category = ? WHERE category = ?",
                (english, chinese),
            )
        self._conn.execute(
            """
            UPDATE memory_records
            SET scope_kind = CASE
                    WHEN category IN ('user_preference') THEN 'global'
                    WHEN category IN ('project_convention', 'tooling_environment') THEN 'workspace'
                    ELSE scope_kind
                END
            WHERE scope_kind IS NULL OR scope_kind = ''
            """
        )
        self._conn.execute(
            """
            UPDATE memory_records
            SET retention_class = CASE
                    WHEN category = 'user_preference' THEN 'user_preference'
                    WHEN category = 'project_convention' THEN 'project_convention'
                    WHEN category = 'tooling_environment' THEN 'tooling_environment'
                    WHEN category = 'active_task' THEN 'task_state'
                    ELSE retention_class
                END
            WHERE retention_class IS NULL OR retention_class = ''
            """
        )


    def _ensure_columns_batch(self, table: str, columns: list[tuple[str, str]]) -> None:
        """Add multiple columns to a table with a single PRAGMA table_info query."""
        existing = {
            str(row["name"])
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, definition in columns:
            if column not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _migrate_dag_schema_v11(self) -> None:
        self._ensure_columns_batch(
            "steps",
            [
                ("join_strategy", "TEXT NOT NULL DEFAULT 'all_required'"),
                ("input_bindings_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("node_key", "TEXT"),
            ],
        )
        self._ensure_columns_batch(
            "events",
            [("causation_ids_json", "TEXT NOT NULL DEFAULT '[]'")],
        )

    def _migrate_memory_kind_index_v12(self) -> None:
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_records_kind "
            "ON memory_records(memory_kind, status, updated_at DESC)"
        )

    def _migrate_hash_chain_checkpoints_v13(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hash_chain_checkpoints (
                task_key TEXT NOT NULL,
                checkpoint_event_seq INTEGER NOT NULL,
                checkpoint_event_hash TEXT NOT NULL,
                checkpointed_at REAL NOT NULL,
                PRIMARY KEY (task_key)
            )
            """
        )

    def _migrate_verification_v14(self) -> None:
        self._ensure_columns_batch(
            "steps",
            [
                ("verification_required", "INTEGER NOT NULL DEFAULT 0"),
                ("verifies_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("supersedes_json", "TEXT NOT NULL DEFAULT '[]'"),
            ],
        )

    def _migrate_blackboard_v15(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blackboard_entries (
                entry_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                step_attempt_id TEXT,
                entry_type TEXT NOT NULL,
                content_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0.5,
                supersedes_entry_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                resolution TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_blackboard_task "
            "ON blackboard_entries(task_id, entry_type, status)"
        )

    def _migrate_observation_tickets_v16(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS observation_tickets (
                ticket_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                step_attempt_id TEXT NOT NULL,
                observer_kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                poll_after_seconds REAL NOT NULL DEFAULT 5.0,
                hard_deadline_at REAL,
                ready_patterns_json TEXT NOT NULL DEFAULT '[]',
                failure_patterns_json TEXT NOT NULL DEFAULT '[]',
                ticket_data_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                last_polled_at REAL,
                resolved_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_observation_tickets_status
                ON observation_tickets(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_observation_tickets_attempt
                ON observation_tickets(step_attempt_id, status);
            """
        )

    def _migrate_budget_v17(self) -> None:
        self._ensure_columns_batch(
            "tasks",
            [
                ("budget_tokens_used", "INTEGER NOT NULL DEFAULT 0"),
                ("budget_tokens_limit", "INTEGER"),
            ],
        )

    def _migrate_self_iterate_v18(self) -> None:
        self._init_self_iterate_schema()

    def _migrate_contract_verification_fields(self) -> None:
        self._ensure_columns_batch(
            "execution_contracts",
            [
                ("task_family", "TEXT"),
                ("verification_requirements_json", "TEXT"),
            ],
        )

    # ------------------------------------------------------------------
    # Blackboard CRUD
    # ------------------------------------------------------------------

    def insert_blackboard_entry(self, record: BlackboardRecord) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT INTO blackboard_entries (
                    entry_id, task_id, step_id, step_attempt_id, entry_type,
                    content_json, confidence, supersedes_entry_id, status,
                    resolution, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.entry_id,
                    record.task_id,
                    record.step_id,
                    record.step_attempt_id,
                    record.entry_type,
                    _canonical_json(record.content),
                    record.confidence,
                    record.supersedes_entry_id,
                    record.status,
                    record.resolution,
                    record.created_at or time.time(),
                ),
            )

    def get_blackboard_entry(self, entry_id: str) -> BlackboardRecord | None:
        row = self._row(
            "SELECT * FROM blackboard_entries WHERE entry_id = ?", (entry_id,)
        )
        if row is None:
            return None
        return self._blackboard_entry_from_row(row)

    def query_blackboard_entries(
        self,
        *,
        task_id: str,
        entry_type: str | None = None,
        status: str | None = None,
    ) -> list[BlackboardRecord]:
        clauses = ["task_id = ?"]
        params: list[Any] = [task_id]
        if entry_type is not None:
            clauses.append("entry_type = ?")
            params.append(entry_type)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = " AND ".join(clauses)
        rows = self._rows(
            f"SELECT * FROM blackboard_entries WHERE {where} ORDER BY created_at ASC",
            params,
        )
        return [self._blackboard_entry_from_row(r) for r in rows]

    def update_blackboard_entry_status(
        self, entry_id: str, status: str, *, resolution: str | None = None
    ) -> None:
        conn = self._get_conn()
        with conn:
            if resolution is not None:
                conn.execute(
                    "UPDATE blackboard_entries SET status = ?, resolution = ?"
                    " WHERE entry_id = ?",
                    (status, resolution, entry_id),
                )
            else:
                conn.execute(
                    "UPDATE blackboard_entries SET status = ? WHERE entry_id = ?",
                    (status, entry_id),
                )

    def _blackboard_entry_from_row(self, row: sqlite3.Row) -> BlackboardRecord:
        from hermit.kernel.ledger.journal.store_support import json_loads
        from hermit.kernel.task.models.records import BlackboardRecord as _BB

        return _BB(
            entry_id=str(row["entry_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            step_attempt_id=row["step_attempt_id"],
            entry_type=str(row["entry_type"]),
            content=json_loads(row["content_json"]),
            confidence=float(row["confidence"]),
            supersedes_entry_id=row["supersedes_entry_id"],
            status=str(row["status"]),
            resolution=row["resolution"],
            created_at=float(row["created_at"]),
        )

    # ------------------------------------------------------------------
    # Observation ticket CRUD
    # ------------------------------------------------------------------

    def _migrate_observation_tickets_v16(self) -> None:
        """Create observation_tickets table for durable observation tracking."""
        self._get_conn().executescript(
            """
            CREATE TABLE IF NOT EXISTS observation_tickets (
                ticket_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                step_attempt_id TEXT NOT NULL,
                observer_kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                poll_after_seconds REAL NOT NULL DEFAULT 5.0,
                hard_deadline_at REAL,
                ready_patterns_json TEXT NOT NULL DEFAULT '[]',
                failure_patterns_json TEXT NOT NULL DEFAULT '[]',
                ticket_data_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                last_polled_at REAL,
                resolved_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_observation_tickets_status
                ON observation_tickets(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_observation_tickets_attempt
                ON observation_tickets(step_attempt_id, status);
            """
        )

    def create_observation_ticket(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        observer_kind: str,
        poll_after_seconds: float = 5.0,
        hard_deadline_at: float | None = None,
        ready_patterns: list[Any] | None = None,
        failure_patterns: list[Any] | None = None,
        ticket_data: dict[str, Any] | None = None,
    ) -> str:
        ticket_id = self._id("obs")
        now = time.time()
        conn = self._get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO observation_tickets (
                    ticket_id, task_id, step_id, step_attempt_id, observer_kind,
                    status, poll_after_seconds, hard_deadline_at,
                    ready_patterns_json, failure_patterns_json, ticket_data_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_id, task_id, step_id, step_attempt_id, observer_kind,
                    poll_after_seconds, hard_deadline_at,
                    _canonical_json(ready_patterns or []),
                    _canonical_json(failure_patterns or []),
                    _canonical_json(ticket_data or {}),
                    now,
                ),
            )
        return ticket_id

    def resolve_observation(
        self, ticket_id: str, *, status: str = "completed", now: float | None = None
    ) -> None:
        ts = now or time.time()
        conn = self._get_conn()
        with conn:
            conn.execute(
                "UPDATE observation_tickets SET status = ?, resolved_at = ?"
                " WHERE ticket_id = ?",
                (status, ts, ticket_id),
            )

    def resolve_observations_for_attempt(
        self, step_attempt_id: str, *, status: str = "resolved", now: float | None = None
    ) -> list[str]:
        """Resolve all active observation tickets for a step attempt."""
        ts = now or time.time()
        conn = self._get_conn()
        rows = self._rows(
            "SELECT ticket_id FROM observation_tickets"
            " WHERE step_attempt_id = ? AND status = 'active'",
            (step_attempt_id,),
        )
        resolved_ids: list[str] = []
        for row in rows:
            tid = str(row["ticket_id"])
            with conn:
                conn.execute(
                    "UPDATE observation_tickets SET status = ?, resolved_at = ?"
                    " WHERE ticket_id = ?",
                    (status, ts, tid),
                )
            resolved_ids.append(tid)
        return resolved_ids

    def resolve_observations_for_task(
        self, task_id: str, *, status: str = "cancelled", now: float | None = None
    ) -> list[str]:
        """Resolve all active observation tickets for a task."""
        ts = now or time.time()
        conn = self._get_conn()
        rows = self._rows(
            "SELECT ticket_id FROM observation_tickets"
            " WHERE task_id = ? AND status = 'active'",
            (task_id,),
        )
        resolved_ids: list[str] = []
        for row in rows:
            tid = str(row["ticket_id"])
            with conn:
                conn.execute(
                    "UPDATE observation_tickets SET status = ?, resolved_at = ?"
                    " WHERE ticket_id = ?",
                    (status, ts, tid),
                )
            resolved_ids.append(tid)
        return resolved_ids


    # ------------------------------------------------------------------
    # Blackboard CRUD
    # ------------------------------------------------------------------

    def insert_blackboard_entry(self, record: BlackboardRecord) -> None:
        with self._conn:
            self._conn.execute(
                """INSERT INTO blackboard_entries (
                    entry_id, task_id, step_id, step_attempt_id, entry_type,
                    content_json, confidence, supersedes_entry_id, status,
                    resolution, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.entry_id,
                    record.task_id,
                    record.step_id,
                    record.step_attempt_id,
                    record.entry_type,
                    _canonical_json(record.content),
                    record.confidence,
                    record.supersedes_entry_id,
                    record.status,
                    record.resolution,
                    record.created_at or time.time(),
                ),
            )

    def get_blackboard_entry(self, entry_id: str) -> BlackboardRecord | None:
        row = self._row("SELECT * FROM blackboard_entries WHERE entry_id = ?", (entry_id,))
        if row is None:
            return None
        return self._blackboard_entry_from_row(row)

    def query_blackboard_entries(
        self,
        *,
        task_id: str,
        entry_type: str | None = None,
        status: str | None = None,
    ) -> list[BlackboardRecord]:
        clauses = ["task_id = ?"]
        params: list[Any] = [task_id]
        if entry_type is not None:
            clauses.append("entry_type = ?")
            params.append(entry_type)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = " AND ".join(clauses)
        rows = self._rows(
            f"SELECT * FROM blackboard_entries WHERE {where} ORDER BY created_at ASC",
            params,
        )
        return [self._blackboard_entry_from_row(r) for r in rows]

    def update_blackboard_entry_status(
        self, entry_id: str, status: str, *, resolution: str | None = None
    ) -> None:
        with self._conn:
            if resolution is not None:
                self._conn.execute(
                    "UPDATE blackboard_entries SET status = ?, resolution = ? WHERE entry_id = ?",
                    (status, resolution, entry_id),
                )
            else:
                self._conn.execute(
                    "UPDATE blackboard_entries SET status = ? WHERE entry_id = ?",
                    (status, entry_id),
                )

    def _blackboard_entry_from_row(self, row: sqlite3.Row) -> BlackboardRecord:
        from hermit.kernel.ledger.journal.store_support import json_loads
        from hermit.kernel.task.models.records import BlackboardRecord

        return BlackboardRecord(
            entry_id=str(row["entry_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            step_attempt_id=row["step_attempt_id"],
            entry_type=str(row["entry_type"]),
            content=json_loads(row["content_json"]),
            confidence=float(row["confidence"]),
            supersedes_entry_id=row["supersedes_entry_id"],
            status=str(row["status"]),
            resolution=row["resolution"],
            created_at=float(row["created_at"]),
        )

    # ------------------------------------------------------------------
    # Observation ticket CRUD
    # ------------------------------------------------------------------

    def create_observation_ticket(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        observer_kind: str,
        poll_after_seconds: float = 5.0,
        hard_deadline_at: float | None = None,
        ready_patterns: list[Any] | None = None,
        failure_patterns: list[Any] | None = None,
        ticket_data: dict[str, Any] | None = None,
    ) -> str:
        ticket_id = self._id("obs")
        now = time.time()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO observation_tickets (
                    ticket_id, task_id, step_id, step_attempt_id, observer_kind,
                    status, poll_after_seconds, hard_deadline_at,
                    ready_patterns_json, failure_patterns_json, ticket_data_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket_id, task_id, step_id, step_attempt_id, observer_kind,
                    poll_after_seconds, hard_deadline_at,
                    _canonical_json(ready_patterns or []),
                    _canonical_json(failure_patterns or []),
                    _canonical_json(ticket_data or {}),
                    now,
                ),
            )
            self._append_event_tx(
                event_id=self.generate_id("event"),
                event_type="observation.created",
                entity_type="observation_ticket",
                entity_id=ticket_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "ticket_id": ticket_id,
                    "observer_kind": observer_kind,
                    "step_attempt_id": step_attempt_id,
                    "poll_after_seconds": poll_after_seconds,
                    "hard_deadline_at": hard_deadline_at,
                },
            )
        return ticket_id

    def update_observation_progress(self, ticket_id: str, *, now: float | None = None) -> None:
        ts = now or time.time()
        with self._conn:
            self._conn.execute(
                "UPDATE observation_tickets SET last_polled_at = ? WHERE ticket_id = ?",
                (ts, ticket_id),
            )
            row = self._row(
                "SELECT task_id, step_id FROM observation_tickets WHERE ticket_id = ?",
                (ticket_id,),
            )
            task_id = str(row["task_id"]) if row else None
            step_id = str(row["step_id"]) if row else None
            self._append_event_tx(
                event_id=self.generate_id("event"),
                event_type="observation.polled",
                entity_type="observation_ticket",
                entity_id=ticket_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={"ticket_id": ticket_id, "polled_at": ts},
            )

    def resolve_observation(
        self, ticket_id: str, *, status: str = "completed", now: float | None = None
    ) -> None:
        ts = now or time.time()
        with self._conn:
            self._conn.execute(
                "UPDATE observation_tickets SET status = ?, resolved_at = ? WHERE ticket_id = ?",
                (status, ts, ticket_id),
            )
            row = self._row(
                "SELECT task_id, step_id FROM observation_tickets WHERE ticket_id = ?",
                (ticket_id,),
            )
            task_id = str(row["task_id"]) if row else None
            step_id = str(row["step_id"]) if row else None
            self._append_event_tx(
                event_id=self.generate_id("event"),
                event_type="observation.resolved",
                entity_type="observation_ticket",
                entity_id=ticket_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={"ticket_id": ticket_id, "status": status, "resolved_at": ts},
            )

    def timeout_observation(self, ticket_id: str, *, now: float | None = None) -> None:
        ts = now or time.time()
        with self._conn:
            self._conn.execute(
                "UPDATE observation_tickets SET status = 'timed_out', resolved_at = ?"
                " WHERE ticket_id = ?",
                (ts, ticket_id),
            )
            row = self._row(
                "SELECT task_id, step_id FROM observation_tickets WHERE ticket_id = ?",
                (ticket_id,),
            )
            task_id = str(row["task_id"]) if row else None
            step_id = str(row["step_id"]) if row else None
            self._append_event_tx(
                event_id=self.generate_id("event"),
                event_type="observation.timed_out",
                entity_type="observation_ticket",
                entity_id=ticket_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={"ticket_id": ticket_id, "timed_out_at": ts},
            )

    def list_active_observation_tickets(self) -> list[dict[str, Any]]:
        from hermit.kernel.ledger.journal.store_support import json_loads as _jl

        rows = self._rows(
            "SELECT * FROM observation_tickets WHERE status = 'active' ORDER BY created_at"
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append({
                "ticket_id": str(row["ticket_id"]),
                "task_id": str(row["task_id"]),
                "step_id": str(row["step_id"]),
                "step_attempt_id": str(row["step_attempt_id"]),
                "observer_kind": str(row["observer_kind"]),
                "status": str(row["status"]),
                "poll_after_seconds": float(row["poll_after_seconds"]),
                "hard_deadline_at": (
                    float(row["hard_deadline_at"]) if row["hard_deadline_at"] else None
                ),
                "ready_patterns": _jl(row["ready_patterns_json"]),
                "failure_patterns": _jl(row["failure_patterns_json"]),
                "ticket_data": _jl(row["ticket_data_json"]),
                "created_at": float(row["created_at"]),
                "last_polled_at": (
                    float(row["last_polled_at"]) if row["last_polled_at"] else None
                ),
                "resolved_at": None,
            })
        return result

    def get_observation_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        from hermit.kernel.ledger.journal.store_support import json_loads as _jl

        row = self._row(
            "SELECT * FROM observation_tickets WHERE ticket_id = ?", (ticket_id,)
        )
        if row is None:
            return None
        return {
            "ticket_id": str(row["ticket_id"]),
            "task_id": str(row["task_id"]),
            "step_id": str(row["step_id"]),
            "step_attempt_id": str(row["step_attempt_id"]),
            "observer_kind": str(row["observer_kind"]),
            "status": str(row["status"]),
            "poll_after_seconds": float(row["poll_after_seconds"]),
            "hard_deadline_at": (
                float(row["hard_deadline_at"]) if row["hard_deadline_at"] else None
            ),
            "ready_patterns": _jl(row["ready_patterns_json"]),
            "failure_patterns": _jl(row["failure_patterns_json"]),
            "ticket_data": _jl(row["ticket_data_json"]),
            "created_at": float(row["created_at"]),
            "last_polled_at": float(row["last_polled_at"]) if row["last_polled_at"] else None,
            "resolved_at": float(row["resolved_at"]) if row["resolved_at"] else None,
        }

    def resolve_observations_for_attempt(
        self, step_attempt_id: str, *, status: str = "resolved", now: float | None = None
    ) -> list[str]:
        """Resolve all active observation tickets for a step attempt."""
        ts = now or time.time()
        rows = self._rows(
            "SELECT ticket_id, task_id, step_id FROM observation_tickets"
            " WHERE step_attempt_id = ? AND status = 'active'",
            (step_attempt_id,),
        )
        resolved_ids: list[str] = []
        for row in rows:
            ticket_id = str(row["ticket_id"])
            task_id = str(row["task_id"])
            step_id = str(row["step_id"])
            with self._conn:
                self._conn.execute(
                    "UPDATE observation_tickets SET status = ?, resolved_at = ?"
                    " WHERE ticket_id = ?",
                    (status, ts, ticket_id),
                )
                self._append_event_tx(
                    event_id=self.generate_id("event"),
                    event_type="observation.resolved",
                    entity_type="observation_ticket",
                    entity_id=ticket_id,
                    task_id=task_id,
                    step_id=step_id,
                    actor="kernel",
                    payload={
                        "ticket_id": ticket_id,
                        "status": status,
                        "resolved_at": ts,
                        "resolved_via": "attempt_cleanup",
                    },
                )
            resolved_ids.append(ticket_id)
        return resolved_ids

    def resolve_observations_for_task(
        self, task_id: str, *, status: str = "cancelled", now: float | None = None
    ) -> list[str]:
        """Resolve all active observation tickets for a task."""
        ts = now or time.time()
        rows = self._rows(
            "SELECT ticket_id, step_id FROM observation_tickets"
            " WHERE task_id = ? AND status = 'active'",
            (task_id,),
        )
        resolved_ids: list[str] = []
        for row in rows:
            ticket_id = str(row["ticket_id"])
            step_id = str(row["step_id"])
            with self._conn:
                self._conn.execute(
                    "UPDATE observation_tickets SET status = ?, resolved_at = ?"
                    " WHERE ticket_id = ?",
                    (status, ts, ticket_id),
                )
                self._append_event_tx(
                    event_id=self.generate_id("event"),
                    event_type="observation.resolved",
                    entity_type="observation_ticket",
                    entity_id=ticket_id,
                    task_id=task_id,
                    step_id=step_id,
                    actor="kernel",
                    payload={
                        "ticket_id": ticket_id,
                        "status": status,
                        "resolved_at": ts,
                        "resolved_via": "task_cleanup",
                    },
                )
            resolved_ids.append(ticket_id)
        return resolved_ids

    def generate_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def _id(self, prefix: str) -> str:
        return self.generate_id(prefix)

    def _row(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        cursor = self._conn.execute(query, tuple(params))
        return cursor.fetchone()

    def _rows(self, query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        cursor = self._conn.execute(query, tuple(params))
        return list(cursor.fetchall())

    def _infer_principal_type(self, value: str) -> str:
        normalized = value.strip().lower()
        if normalized in {"user", "operator", "human"}:
            return "user"
        if normalized in {"kernel", "hermit", "system"}:
            return "kernel"
        if normalized in {"feishu", "scheduler", "webhook", "mcp", "cli"}:
            return "adapter"
        return "service"

    def _principal_slug(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
        return slug or "unknown"

    def ensure_principal(
        self,
        *,
        principal_type: str,
        display_name: str,
        principal_id: str | None = None,
        source_channel: str | None = None,
        external_ref: str | None = None,
        status: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> PrincipalRecord:
        now = time.time()
        resolved_id = principal_id or f"principal_{self._principal_slug(display_name)}"
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO principals (
                    principal_id, principal_type, display_name, source_channel, external_ref,
                    status, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(principal_id) DO UPDATE SET
                    principal_type = excluded.principal_type,
                    display_name = excluded.display_name,
                    source_channel = COALESCE(excluded.source_channel, principals.source_channel),
                    external_ref = COALESCE(excluded.external_ref, principals.external_ref),
                    status = excluded.status,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    resolved_id,
                    principal_type,
                    display_name,
                    source_channel,
                    external_ref,
                    status,
                    _canonical_json(metadata or {}),
                    now,
                    now,
                ),
            )
            row = self._row("SELECT * FROM principals WHERE principal_id = ?", (resolved_id,))
        assert row is not None
        return self._principal_from_row(row)

    def _ensure_principal_id(
        self,
        actor: str | None,
        *,
        source_channel: str | None = None,
        principal_type: str | None = None,
    ) -> str:
        raw = str(actor or "kernel").strip()
        if not raw:
            raw = "kernel"
        inferred_type = principal_type or self._infer_principal_type(raw)
        principal_id = (
            raw if raw.startswith("principal_") else f"principal_{self._principal_slug(raw)}"
        )
        display_name = (
            raw.replace("principal_", "").replace("_", " ") if raw.startswith("principal_") else raw
        )
        self.ensure_principal(
            principal_type=inferred_type,
            display_name=display_name,
            principal_id=principal_id,
            source_channel=source_channel,
            external_ref=None if raw.startswith("principal_") else raw,
            metadata={"legacy_actor": raw},
        )
        return principal_id

    def update_task_priority(self, task_id: str, *, priority: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET priority = ?, updated_at = ? WHERE task_id = ?",
                (priority, time.time(), task_id),
            )
            self._append_event_tx(
                event_id=self.generate_id("event"),
                event_type="task.reprioritized",
                entity_type="task",
                entity_id=task_id,
                task_id=task_id,
                actor="user",
                payload={"priority": priority},
            )

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
    ) -> str:
        actor_principal_id = self._ensure_principal_id(actor)
        payload_json = _canonical_json(payload or {})
        occurred_at = time.time()
        prev_event_hash = self._latest_task_event_hash(task_id)
        event_hash = self._compute_event_hash(
            event_id=event_id,
            task_id=task_id,
            step_id=step_id,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            actor=actor_principal_id,
            payload_json=payload_json,
            occurred_at=occurred_at,
            causation_id=causation_id,
            correlation_id=correlation_id,
            prev_event_hash=prev_event_hash,
        )
        # Insert event row — also write hash columns for backward compatibility
        # with code that reads event_hash directly from the events table.
        cursor = self._conn.execute(
            """
            INSERT INTO events (
                event_id, task_id, step_id, entity_type, entity_id, event_type,
                actor_principal_id, payload_json, occurred_at, causation_id, correlation_id,
                event_hash, prev_event_hash, hash_chain_algo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task_id,
                step_id,
                entity_type,
                entity_id,
                event_type,
                actor_principal_id,
                payload_json,
                occurred_at,
                causation_id,
                correlation_id,
                event_hash,
                prev_event_hash,
                "sha256-v1",
            ),
        )
        # Insert into the append-only event_hashes table (authoritative source).
        event_seq = cursor.lastrowid
        self._conn.execute(
            """
            INSERT INTO event_hashes
                (event_seq, task_id, event_hash, prev_event_hash,
                 hash_chain_algo, computed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_seq, task_id, event_hash, prev_event_hash, "sha256-v1", occurred_at),
        )
        return event_id

    def _latest_task_event_hash(self, task_id: str | None) -> str | None:
        if not task_id:
            return None
        # Prefer the append-only event_hashes table (authoritative source).
        row = self._row(
            """
            SELECT event_hash
            FROM event_hashes
            WHERE task_id = ? AND event_hash IS NOT NULL AND event_hash != ''
            ORDER BY event_seq DESC
            LIMIT 1
            """,
            (task_id,),
        )
        if row is not None and row["event_hash"]:
            return str(row["event_hash"])
        # Fallback to legacy events table for backward compatibility.
        row = self._row(
            """
            SELECT event_hash
            FROM events
            WHERE task_id = ? AND event_hash IS NOT NULL AND event_hash != ''
            ORDER BY event_seq DESC
            LIMIT 1
            """,
            (task_id,),
        )
        return str(row["event_hash"]) if row is not None and row["event_hash"] else None

    def _compute_event_hash(
        self,
        *,
        event_id: str,
        task_id: str | None,
        step_id: str | None,
        entity_type: str,
        entity_id: str,
        event_type: str,
        actor: str,
        payload_json: str,
        occurred_at: float,
        causation_id: str | None,
        correlation_id: str | None,
        prev_event_hash: str | None,
    ) -> str:
        payload = {
            "event_id": event_id,
            "task_id": task_id,
            "step_id": step_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "event_type": event_type,
            "actor": actor,
            "payload": _canonical_json_from_raw(payload_json),
            "occurred_at": occurred_at,
            "causation_id": causation_id,
            "correlation_id": correlation_id,
            "prev_event_hash": prev_event_hash or "",
        }
        return _sha256_hex(_canonical_json(payload))

    def _migrate_event_hashes_table(self) -> None:
        """Create the event_hashes table for append-only hash chain storage.

        This table stores computed hashes separately from the events table,
        ensuring the events table remains truly append-only. The events table
        columns (event_hash, prev_event_hash, hash_chain_algo) are kept for
        backward compatibility but the event_hashes table is the authoritative
        source for hash chain data.
        """
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS event_hashes (
                event_seq INTEGER PRIMARY KEY,
                task_id TEXT,
                event_hash TEXT NOT NULL,
                prev_event_hash TEXT,
                hash_chain_algo TEXT NOT NULL DEFAULT 'sha256-v1',
                computed_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_event_hashes_task
                ON event_hashes(task_id, event_seq DESC);
            """
        )

    def _backfill_event_hash_chain(self) -> None:
        rows = self._rows("SELECT * FROM events ORDER BY event_seq ASC")
        previous_by_task: dict[str, str] = {}
        now = time.time()
        for row in rows:
            event_seq = int(row["event_seq"])
            task_key = str(row["task_id"]) if row["task_id"] is not None else ""

            # Check if hash already exists in event_hashes table
            existing = self._row(
                "SELECT event_hash FROM event_hashes WHERE event_seq = ?",
                (event_seq,),
            )

            # Also check the legacy columns on the events table
            stored_hash = str(row["event_hash"] or "").strip()
            stored_prev = str(row["prev_event_hash"] or "").strip()
            stored_algo = str(row["hash_chain_algo"] or "").strip()

            prev_event_hash = previous_by_task.get(task_key) if task_key else None

            if existing is not None:
                # Already in event_hashes, use that as authoritative
                stored_hash = str(existing["event_hash"])
            elif stored_hash and stored_prev == (prev_event_hash or "") and stored_algo:
                # Legacy hash exists and is consistent; migrate to event_hashes
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO event_hashes
                        (event_seq, task_id, event_hash, prev_event_hash,
                         hash_chain_algo, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_seq,
                        row["task_id"],
                        stored_hash,
                        stored_prev or None,
                        stored_algo,
                        now,
                    ),
                )
            else:
                # Need to compute and store in event_hashes (append-only)
                event_hash = self._compute_event_hash(
                    event_id=str(row["event_id"]),
                    task_id=row["task_id"],
                    step_id=row["step_id"],
                    entity_type=str(row["entity_type"]),
                    entity_id=str(row["entity_id"]),
                    event_type=str(row["event_type"]),
                    actor=str(row["actor_principal_id"]),
                    payload_json=str(row["payload_json"]),
                    occurred_at=float(row["occurred_at"]),
                    causation_id=row["causation_id"],
                    correlation_id=row["correlation_id"],
                    prev_event_hash=prev_event_hash,
                )
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO event_hashes
                        (event_seq, task_id, event_hash, prev_event_hash,
                         hash_chain_algo, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_seq,
                        row["task_id"],
                        event_hash,
                        prev_event_hash,
                        "sha256-v1",
                        now,
                    ),
                )
                stored_hash = event_hash

            if task_key and stored_hash:
                previous_by_task[task_key] = stored_hash


__all__ = ["KernelSchemaError", "KernelStore"]
