from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from hermit.kernel.store_ledger import KernelLedgerStoreMixin
from hermit.kernel.store_projection import KernelProjectionStoreMixin
from hermit.kernel.store_records import KernelStoreRecordMixin
from hermit.kernel.store_scheduler import KernelSchedulerStoreMixin
from hermit.kernel.store_support import _canonical_json, _canonical_json_from_raw, _sha256_hex
from hermit.kernel.store_tasks import KernelTaskStoreMixin

_SCHEMA_VERSION = "4"
_KNOWN_KERNEL_TABLES = {
    "conversations",
    "conversation_projection_cache",
    "tasks",
    "steps",
    "step_attempts",
    "events",
    "artifacts",
    "approvals",
    "receipts",
    "decisions",
    "execution_permits",
    "path_grants",
    "beliefs",
    "memory_records",
    "rollbacks",
    "projection_cache",
    "schedule_specs",
    "schedule_history",
}


class KernelSchemaError(RuntimeError):
    """Raised when an existing kernel database does not match the hard-cut schema."""


class KernelStore(
    KernelTaskStoreMixin,
    KernelLedgerStoreMixin,
    KernelProjectionStoreMixin,
    KernelSchedulerStoreMixin,
    KernelStoreRecordMixin,
):
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._validate_existing_schema()
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

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
        if version not in {"3", _SCHEMA_VERSION}:
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
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    policy_profile TEXT NOT NULL,
                    source_channel TEXT NOT NULL,
                    parent_task_id TEXT,
                    requested_by TEXT,
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
                    started_at REAL,
                    finished_at REAL
                );
                CREATE TABLE IF NOT EXISTS step_attempts (
                    step_attempt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    waiting_reason TEXT,
                    approval_id TEXT,
                    decision_id TEXT,
                    permit_id TEXT,
                    state_witness_ref TEXT,
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
                    actor TEXT NOT NULL,
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
                    evidence_refs_json TEXT NOT NULL,
                    policy_ref TEXT,
                    approval_ref TEXT,
                    action_type TEXT,
                    decided_by TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_permits (
                    permit_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    decision_ref TEXT NOT NULL,
                    approval_ref TEXT,
                    policy_ref TEXT,
                    action_class TEXT NOT NULL,
                    resource_scope_json TEXT NOT NULL,
                    idempotency_key TEXT,
                    status TEXT NOT NULL,
                    issued_at REAL NOT NULL,
                    expires_at REAL,
                    consumed_at REAL
                );
                CREATE TABLE IF NOT EXISTS path_grants (
                    grant_id TEXT PRIMARY KEY,
                    subject_kind TEXT NOT NULL,
                    subject_ref TEXT NOT NULL,
                    action_class TEXT NOT NULL,
                    path_prefix TEXT NOT NULL,
                    path_display TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    approval_ref TEXT,
                    decision_ref TEXT,
                    policy_ref TEXT,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    last_used_at REAL
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
                    decision_ref TEXT,
                    state_witness_ref TEXT,
                    requested_at REAL NOT NULL,
                    resolved_at REAL,
                    resolved_by TEXT,
                    resolution_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    receipt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    input_refs_json TEXT NOT NULL,
                    environment_ref TEXT,
                    policy_result_json TEXT NOT NULL,
                    approval_ref TEXT,
                    output_refs_json TEXT NOT NULL,
                    result_summary TEXT NOT NULL,
                    result_code TEXT NOT NULL,
                    decision_ref TEXT,
                    permit_ref TEXT,
                    grant_ref TEXT,
                    policy_ref TEXT,
                    witness_ref TEXT,
                    idempotency_key TEXT,
                    receipt_bundle_ref TEXT,
                    proof_mode TEXT NOT NULL DEFAULT 'none',
                    signature TEXT,
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
                    supersedes_json TEXT NOT NULL,
                    contradicts_json TEXT NOT NULL,
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
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON tasks(conversation_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, event_seq);
                CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, requested_at);
                CREATE INDEX IF NOT EXISTS idx_receipts_task ON receipts(task_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_decisions_task ON decisions(task_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_permits_task ON execution_permits(task_id, issued_at);
                CREATE INDEX IF NOT EXISTS idx_path_grants_subject ON path_grants(subject_kind, subject_ref, status, action_class);
                CREATE INDEX IF NOT EXISTS idx_path_grants_prefix ON path_grants(path_prefix);
                CREATE INDEX IF NOT EXISTS idx_beliefs_scope ON beliefs(scope_kind, scope_ref, status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_records_status ON memory_records(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_rollbacks_receipt ON rollbacks(receipt_ref, created_at DESC);
                """
            )
            self._ensure_column("receipts", "grant_ref", "TEXT")
            self._ensure_column("receipts", "receipt_bundle_ref", "TEXT")
            self._ensure_column("receipts", "proof_mode", "TEXT NOT NULL DEFAULT 'none'")
            self._ensure_column("receipts", "signature", "TEXT")
            self._ensure_column("receipts", "rollback_supported", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column("receipts", "rollback_strategy", "TEXT")
            self._ensure_column("receipts", "rollback_status", "TEXT NOT NULL DEFAULT 'not_requested'")
            self._ensure_column("receipts", "rollback_ref", "TEXT")
            self._ensure_column("receipts", "rollback_artifact_refs_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column("execution_permits", "constraints_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column("events", "event_hash", "TEXT")
            self._ensure_column("events", "prev_event_hash", "TEXT")
            self._ensure_column("events", "hash_chain_algo", "TEXT")
            self._ensure_column("beliefs", "claim_text", "TEXT")
            self._ensure_column("beliefs", "structured_assertion_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column("beliefs", "promotion_candidate", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column("memory_records", "claim_text", "TEXT")
            self._ensure_column("memory_records", "structured_assertion_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column("memory_records", "scope_kind", "TEXT")
            self._ensure_column("memory_records", "scope_ref", "TEXT")
            self._ensure_column("memory_records", "promotion_reason", "TEXT")
            self._ensure_column("memory_records", "retention_class", "TEXT")
            self._ensure_column("memory_records", "supersedes_memory_ids_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column("memory_records", "superseded_by_memory_id", "TEXT")
            self._ensure_column("memory_records", "invalidation_reason", "TEXT")
            self._ensure_column("memory_records", "expires_at", "REAL")
            self._migrate_memory_schema_v4()
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
            str(row["name"])
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
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
                    WHEN category IN ('用户偏好') THEN 'global'
                    WHEN category IN ('项目约定', '工具与环境', '环境与工具') THEN 'workspace'
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
                    WHEN category = '用户偏好' THEN 'user_preference'
                    WHEN category = '项目约定' THEN 'project_convention'
                    WHEN category IN ('工具与环境', '环境与工具') THEN 'tooling_environment'
                    WHEN category = '进行中的任务' THEN 'task_state'
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

    def _id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def _row(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        cursor = self._conn.execute(query, tuple(params))
        return cursor.fetchone()

    def _rows(self, query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        cursor = self._conn.execute(query, tuple(params))
        return list(cursor.fetchall())

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
            actor=actor,
            payload_json=payload_json,
            occurred_at=occurred_at,
            causation_id=causation_id,
            correlation_id=correlation_id,
            prev_event_hash=prev_event_hash,
        )
        self._conn.execute(
            """
            INSERT INTO events (
                event_id, task_id, step_id, entity_type, entity_id, event_type,
                actor, payload_json, occurred_at, causation_id, correlation_id,
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
                actor,
                payload_json,
                occurred_at,
                causation_id,
                correlation_id,
                event_hash,
                prev_event_hash,
                "sha256-v1",
            ),
        )
        return event_id

    def _latest_task_event_hash(self, task_id: str | None) -> str | None:
        if not task_id:
            return None
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

    def _backfill_event_hash_chain(self) -> None:
        rows = self._rows("SELECT * FROM events ORDER BY event_seq ASC")
        previous_by_task: dict[str, str] = {}
        for row in rows:
            task_key = str(row["task_id"]) if row["task_id"] is not None else ""
            stored_hash = str(row["event_hash"] or "").strip()
            stored_prev = str(row["prev_event_hash"] or "").strip()
            stored_algo = str(row["hash_chain_algo"] or "").strip()
            prev_event_hash = previous_by_task.get(task_key) if task_key else None
            if not stored_hash or not stored_prev and prev_event_hash or not stored_algo:
                event_hash = self._compute_event_hash(
                    event_id=str(row["event_id"]),
                    task_id=row["task_id"],
                    step_id=row["step_id"],
                    entity_type=str(row["entity_type"]),
                    entity_id=str(row["entity_id"]),
                    event_type=str(row["event_type"]),
                    actor=str(row["actor"]),
                    payload_json=str(row["payload_json"]),
                    occurred_at=float(row["occurred_at"]),
                    causation_id=row["causation_id"],
                    correlation_id=row["correlation_id"],
                    prev_event_hash=prev_event_hash,
                )
                self._conn.execute(
                    """
                    UPDATE events
                    SET event_hash = ?, prev_event_hash = ?, hash_chain_algo = ?
                    WHERE event_seq = ?
                    """,
                    (event_hash, prev_event_hash, "sha256-v1", int(row["event_seq"])),
                )
                stored_hash = event_hash
            if task_key and stored_hash:
                previous_by_task[task_key] = stored_hash


__all__ = ["KernelSchemaError", "KernelStore"]
