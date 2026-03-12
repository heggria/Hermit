from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from hermit.builtin.scheduler.models import JobExecutionRecord, ScheduledJob
from hermit.kernel.models import (
    ApprovalRecord,
    ArtifactRecord,
    ConversationRecord,
    ReceiptRecord,
    StepAttemptRecord,
    StepRecord,
    TaskRecord,
)

_UNSET = object()


def _json_loads(raw: str | None) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


class KernelStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
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
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    task_id TEXT,
                    created_at REAL NOT NULL
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
                    started_at REAL,
                    finished_at REAL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    step_id TEXT,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    causation_id TEXT,
                    correlation_id TEXT
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
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    step_attempt_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approval_type TEXT NOT NULL,
                    requested_action_json TEXT NOT NULL,
                    request_packet_ref TEXT,
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
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedule_specs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    cron_expr TEXT,
                    once_at REAL,
                    interval_seconds INTEGER,
                    enabled INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    last_run_at REAL,
                    next_run_at REAL,
                    max_retries INTEGER NOT NULL,
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
                CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, requested_at);
                CREATE INDEX IF NOT EXISTS idx_receipts_task ON receipts(task_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_messages_conversation ON conversation_messages(conversation_id, id);
                """
            )

    def _id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def _row(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        cursor = self._conn.execute(query, tuple(params))
        return cursor.fetchone()

    def _rows(self, query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        cursor = self._conn.execute(query, tuple(params))
        return list(cursor.fetchall())

    def ensure_conversation(
        self,
        conversation_id: str,
        *,
        source_channel: str,
        source_ref: str | None = None,
    ) -> ConversationRecord:
        now = time.time()
        with self._lock, self._conn:
            row = self._row(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO conversations (
                        conversation_id, source_channel, source_ref, last_task_id, status,
                        metadata_json, total_input_tokens, total_output_tokens,
                        total_cache_read_tokens, total_cache_creation_tokens,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, NULL, 'open', '{}', 0, 0, 0, 0, ?, ?)
                    """,
                    (conversation_id, source_channel, source_ref, now, now),
                )
                row = self._row(
                    "SELECT * FROM conversations WHERE conversation_id = ?",
                    (conversation_id,),
                )
            else:
                self._conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                    (now, conversation_id),
                )
                row = self._row(
                    "SELECT * FROM conversations WHERE conversation_id = ?",
                    (conversation_id,),
                )
        assert row is not None
        return self._conversation_from_row(row)

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        with self._lock:
            row = self._row("SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,))
        return self._conversation_from_row(row) if row is not None else None

    def list_conversations(self) -> list[str]:
        with self._lock:
            rows = self._rows("SELECT conversation_id FROM conversations ORDER BY updated_at DESC")
        return [str(row["conversation_id"]) for row in rows]

    def update_conversation_metadata(self, conversation_id: str, metadata: dict[str, Any]) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE conversations SET metadata_json = ?, updated_at = ? WHERE conversation_id = ?",
                (json.dumps(metadata, ensure_ascii=False), now, conversation_id),
            )

    def replace_messages(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
        *,
        task_id: str | None = None,
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            for message in messages:
                self._conn.execute(
                    """
                    INSERT INTO conversation_messages (conversation_id, role, content_json, task_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        str(message.get("role", "assistant")),
                        json.dumps(message.get("content"), ensure_ascii=False),
                        task_id,
                        now,
                    ),
                )
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )

    def load_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._rows(
                """
                SELECT role, content_json
                FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY id ASC
                """,
                (conversation_id,),
            )
        return [{"role": str(row["role"]), "content": _json_loads(row["content_json"])} for row in rows]

    def clear_messages(self, conversation_id: str) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )

    def update_conversation_usage(
        self,
        conversation_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_creation_tokens: int,
        last_task_id: str | None,
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE conversations
                SET total_input_tokens = ?,
                    total_output_tokens = ?,
                    total_cache_read_tokens = ?,
                    total_cache_creation_tokens = ?,
                    last_task_id = COALESCE(?, last_task_id),
                    updated_at = ?
                WHERE conversation_id = ?
                """,
                (
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_creation_tokens,
                    last_task_id,
                    now,
                    conversation_id,
                ),
            )

    def create_task(
        self,
        *,
        conversation_id: str,
        title: str,
        goal: str,
        source_channel: str,
        owner: str = "hermit",
        priority: str = "normal",
        policy_profile: str = "default",
        parent_task_id: str | None = None,
        requested_by: str | None = None,
    ) -> TaskRecord:
        now = time.time()
        task_id = self._id("task")
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    task_id, conversation_id, title, goal, status, priority, owner,
                    policy_profile, source_channel, parent_task_id, requested_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    conversation_id,
                    title,
                    goal,
                    priority,
                    owner,
                    policy_profile,
                    source_channel,
                    parent_task_id,
                    requested_by,
                    now,
                    now,
                ),
            )
            self._conn.execute(
                "UPDATE conversations SET last_task_id = ?, updated_at = ? WHERE conversation_id = ?",
                (task_id, now, conversation_id),
            )
        self.append_event(
            event_type="task.created",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            actor=requested_by or owner,
            payload={"conversation_id": conversation_id, "goal": goal, "source_channel": source_channel},
        )
        task = self.get_task(task_id)
        assert task is not None
        return task

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            row = self._row("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        return self._task_from_row(row) if row is not None else None

    def list_tasks(self, *, conversation_id: str | None = None, status: str | None = None, limit: int = 50) -> list[TaskRecord]:
        clauses = []
        params: list[Any] = []
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._rows(query, params)
        return [self._task_from_row(row) for row in rows]

    def update_task_status(self, task_id: str, status: str) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (status, now, task_id),
            )
        self.append_event(
            event_type=f"task.{status}",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            actor="kernel",
            payload={"status": status},
        )

    def get_last_task_for_conversation(self, conversation_id: str) -> TaskRecord | None:
        with self._lock:
            row = self._row(
                "SELECT * FROM tasks WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1",
                (conversation_id,),
            )
        return self._task_from_row(row) if row is not None else None

    def create_step(self, *, task_id: str, kind: str, status: str = "running") -> StepRecord:
        now = time.time()
        step_id = self._id("step")
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO steps (step_id, task_id, kind, status, attempt, started_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (step_id, task_id, kind, status, now),
            )
        self.append_event(
            event_type="step.started",
            entity_type="step",
            entity_id=step_id,
            task_id=task_id,
            step_id=step_id,
            actor="kernel",
            payload={"kind": kind, "status": status},
        )
        step = self.get_step(step_id)
        assert step is not None
        return step

    def get_step(self, step_id: str) -> StepRecord | None:
        with self._lock:
            row = self._row("SELECT * FROM steps WHERE step_id = ?", (step_id,))
        return self._step_from_row(row) if row is not None else None

    def update_step(
        self,
        step_id: str,
        *,
        status: str | None = None,
        output_ref: str | None = None,
        finished_at: float | None = None,
    ) -> None:
        now = time.time()
        step = self.get_step(step_id)
        if step is None:
            return
        values = {
            "status": status or step.status,
            "output_ref": output_ref if output_ref is not None else step.output_ref,
            "finished_at": finished_at if finished_at is not None else step.finished_at,
        }
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE steps
                SET status = ?, output_ref = ?, finished_at = ?
                WHERE step_id = ?
                """,
                (values["status"], values["output_ref"], values["finished_at"], step_id),
            )
            self._conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
                (now, step.task_id),
            )

    def create_step_attempt(
        self,
        *,
        task_id: str,
        step_id: str,
        attempt: int = 1,
        status: str = "running",
        context: dict[str, Any] | None = None,
    ) -> StepAttemptRecord:
        now = time.time()
        step_attempt_id = self._id("attempt")
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO step_attempts (
                    step_attempt_id, task_id, step_id, attempt, status, context_json, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_attempt_id,
                    task_id,
                    step_id,
                    attempt,
                    status,
                    json.dumps(context or {}, ensure_ascii=False),
                    now,
                ),
            )
        return self.get_step_attempt(step_attempt_id)  # type: ignore[return-value]

    def get_step_attempt(self, step_attempt_id: str) -> StepAttemptRecord | None:
        with self._lock:
            row = self._row("SELECT * FROM step_attempts WHERE step_attempt_id = ?", (step_attempt_id,))
        return self._step_attempt_from_row(row) if row is not None else None

    def update_step_attempt(
        self,
        step_attempt_id: str,
        *,
        status: str | None = None,
        context: dict[str, Any] | object = _UNSET,
        waiting_reason: str | None | object = _UNSET,
        approval_id: str | None | object = _UNSET,
        finished_at: float | None | object = _UNSET,
    ) -> None:
        attempt = self.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE step_attempts
                SET status = ?, context_json = ?, waiting_reason = ?, approval_id = ?, finished_at = ?
                WHERE step_attempt_id = ?
                """,
                (
                    status or attempt.status,
                    json.dumps(attempt.context if context is _UNSET else context, ensure_ascii=False),
                    attempt.waiting_reason if waiting_reason is _UNSET else waiting_reason,
                    attempt.approval_id if approval_id is _UNSET else approval_id,
                    attempt.finished_at if finished_at is _UNSET else finished_at,
                    step_attempt_id,
                ),
            )

    def append_event(
        self,
        *,
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
        event_id = self._id("event")
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO events (
                    event_id, task_id, step_id, entity_type, entity_id, event_type,
                    actor, payload_json, occurred_at, causation_id, correlation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    task_id,
                    step_id,
                    entity_type,
                    entity_id,
                    event_type,
                    actor,
                    json.dumps(payload or {}, ensure_ascii=False),
                    time.time(),
                    causation_id,
                    correlation_id,
                ),
            )
        return event_id

    def list_events(self, *, task_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if task_id:
            query = "SELECT * FROM events WHERE task_id = ? ORDER BY occurred_at ASC LIMIT ?"
            params: tuple[Any, ...] = (task_id, limit)
        else:
            query = "SELECT * FROM events ORDER BY occurred_at DESC LIMIT ?"
            params = (limit,)
        with self._lock:
            rows = self._rows(query, params)
        return [
            {
                "event_id": str(row["event_id"]),
                "task_id": row["task_id"],
                "step_id": row["step_id"],
                "entity_type": str(row["entity_type"]),
                "entity_id": str(row["entity_id"]),
                "event_type": str(row["event_type"]),
                "actor": str(row["actor"]),
                "payload": _json_loads(row["payload_json"]),
                "occurred_at": float(row["occurred_at"]),
            }
            for row in rows
        ]

    def create_artifact(
        self,
        *,
        task_id: str | None,
        step_id: str | None,
        kind: str,
        uri: str,
        content_hash: str,
        producer: str,
        retention_class: str = "default",
        trust_tier: str = "observed",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        artifact_id = self._id("artifact")
        created_at = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, task_id, step_id, kind, uri, content_hash, producer,
                    retention_class, trust_tier, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    task_id,
                    step_id,
                    kind,
                    uri,
                    content_hash,
                    producer,
                    retention_class,
                    trust_tier,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    created_at,
                ),
            )
        return ArtifactRecord(
            artifact_id=artifact_id,
            task_id=task_id,
            step_id=step_id,
            kind=kind,
            uri=uri,
            content_hash=content_hash,
            producer=producer,
            retention_class=retention_class,
            trust_tier=trust_tier,
            metadata=metadata or {},
            created_at=created_at,
        )

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self._lock:
            row = self._row("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,))
        return self._artifact_from_row(row) if row is not None else None

    def create_approval(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        approval_type: str,
        requested_action: dict[str, Any],
        request_packet_ref: str | None,
    ) -> ApprovalRecord:
        approval_id = self._id("approval")
        requested_at = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, task_id, step_id, step_attempt_id, status,
                    approval_type, requested_action_json, request_packet_ref,
                    requested_at, resolution_json
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, '{}')
                """,
                (
                    approval_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    approval_type,
                    json.dumps(requested_action, ensure_ascii=False),
                    request_packet_ref,
                    requested_at,
                ),
            )
        self.append_event(
            event_type="approval.requested",
            entity_type="approval",
            entity_id=approval_id,
            task_id=task_id,
            step_id=step_id,
            actor="kernel",
            payload=requested_action,
        )
        approval = self.get_approval(approval_id)
        assert approval is not None
        return approval

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            row = self._row("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,))
        return self._approval_from_row(row) if row is not None else None

    def list_approvals(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[ApprovalRecord]:
        clauses = []
        params: list[Any] = []
        if conversation_id:
            clauses.append("task_id IN (SELECT task_id FROM tasks WHERE conversation_id = ?)")
            params.append(conversation_id)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._rows(f"SELECT * FROM approvals {where} ORDER BY requested_at DESC LIMIT ?", params)
        return [self._approval_from_row(row) for row in rows]

    def get_latest_pending_approval(self, conversation_id: str) -> ApprovalRecord | None:
        approvals = self.list_approvals(conversation_id=conversation_id, status="pending", limit=1)
        return approvals[0] if approvals else None

    def resolve_approval(
        self,
        approval_id: str,
        *,
        status: str,
        resolved_by: str,
        resolution: dict[str, Any],
    ) -> None:
        now = time.time()
        approval = self.get_approval(approval_id)
        if approval is None:
            return
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE approvals
                SET status = ?, resolved_at = ?, resolved_by = ?, resolution_json = ?
                WHERE approval_id = ?
                """,
                (status, now, resolved_by, json.dumps(resolution, ensure_ascii=False), approval_id),
            )
        self.append_event(
            event_type=f"approval.{status}",
            entity_type="approval",
            entity_id=approval_id,
            task_id=approval.task_id,
            step_id=approval.step_id,
            actor=resolved_by,
            payload=resolution,
        )

    def create_receipt(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        action_type: str,
        input_refs: list[str],
        environment_ref: str | None,
        policy_result: dict[str, Any],
        approval_ref: str | None,
        output_refs: list[str],
        result_summary: str,
    ) -> ReceiptRecord:
        receipt_id = self._id("receipt")
        created_at = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO receipts (
                    receipt_id, task_id, step_id, step_attempt_id, action_type,
                    input_refs_json, environment_ref, policy_result_json,
                    approval_ref, output_refs_json, result_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    task_id,
                    step_id,
                    step_attempt_id,
                    action_type,
                    json.dumps(input_refs, ensure_ascii=False),
                    environment_ref,
                    json.dumps(policy_result, ensure_ascii=False),
                    approval_ref,
                    json.dumps(output_refs, ensure_ascii=False),
                    result_summary,
                    created_at,
                ),
            )
        self.append_event(
            event_type="receipt.issued",
            entity_type="receipt",
            entity_id=receipt_id,
            task_id=task_id,
            step_id=step_id,
            actor="kernel",
            payload={"action_type": action_type, "result_summary": result_summary},
        )
        return ReceiptRecord(
            receipt_id=receipt_id,
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=step_attempt_id,
            action_type=action_type,
            input_refs=input_refs,
            environment_ref=environment_ref,
            policy_result=policy_result,
            approval_ref=approval_ref,
            output_refs=output_refs,
            result_summary=result_summary,
            created_at=created_at,
        )

    def list_receipts(self, *, task_id: str | None = None, limit: int = 50) -> list[ReceiptRecord]:
        if task_id:
            query = "SELECT * FROM receipts WHERE task_id = ? ORDER BY created_at DESC LIMIT ?"
            params: tuple[Any, ...] = (task_id, limit)
        else:
            query = "SELECT * FROM receipts ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        with self._lock:
            rows = self._rows(query, params)
        return [self._receipt_from_row(row) for row in rows]

    def create_schedule(self, job: ScheduledJob) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO schedule_specs (
                    id, name, prompt, schedule_type, cron_expr, once_at, interval_seconds,
                    enabled, created_at, last_run_at, next_run_at, max_retries, feishu_chat_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.name,
                    job.prompt,
                    job.schedule_type,
                    job.cron_expr,
                    job.once_at,
                    job.interval_seconds,
                    1 if job.enabled else 0,
                    job.created_at,
                    job.last_run_at,
                    job.next_run_at,
                    job.max_retries,
                    job.feishu_chat_id,
                ),
            )

    def update_schedule(self, job_id: str, **updates: Any) -> ScheduledJob | None:
        job = self.get_schedule(job_id)
        if job is None:
            return None
        for key, value in updates.items():
            if hasattr(job, key):
                setattr(job, key, value)
        self.create_schedule(job)
        return job

    def delete_schedule(self, job_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM schedule_specs WHERE id = ?", (job_id,))
        return cursor.rowcount > 0

    def get_schedule(self, job_id: str) -> ScheduledJob | None:
        with self._lock:
            row = self._row("SELECT * FROM schedule_specs WHERE id = ?", (job_id,))
        return self._schedule_from_row(row) if row is not None else None

    def list_schedules(self) -> list[ScheduledJob]:
        with self._lock:
            rows = self._rows("SELECT * FROM schedule_specs ORDER BY created_at DESC")
        return [self._schedule_from_row(row) for row in rows]

    def append_schedule_history(self, record: JobExecutionRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO schedule_history (job_id, job_name, started_at, finished_at, success, result_text, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.job_id,
                    record.job_name,
                    record.started_at,
                    record.finished_at,
                    1 if record.success else 0,
                    record.result_text,
                    record.error,
                ),
            )

    def list_schedule_history(self, *, job_id: str | None = None, limit: int = 20) -> list[JobExecutionRecord]:
        if job_id:
            query = """
                SELECT job_id, job_name, started_at, finished_at, success, result_text, error
                FROM schedule_history WHERE job_id = ? ORDER BY started_at DESC LIMIT ?
            """
            params: tuple[Any, ...] = (job_id, limit)
        else:
            query = """
                SELECT job_id, job_name, started_at, finished_at, success, result_text, error
                FROM schedule_history ORDER BY started_at DESC LIMIT ?
            """
            params = (limit,)
        with self._lock:
            rows = self._rows(query, params)
        return [
            JobExecutionRecord(
                job_id=str(row["job_id"]),
                job_name=str(row["job_name"]),
                started_at=float(row["started_at"]),
                finished_at=float(row["finished_at"]),
                success=bool(row["success"]),
                result_text=str(row["result_text"]),
                error=row["error"],
            )
            for row in rows
        ]

    def _conversation_from_row(self, row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=str(row["conversation_id"]),
            source_channel=str(row["source_channel"]),
            source_ref=row["source_ref"],
            last_task_id=row["last_task_id"],
            status=str(row["status"]),
            metadata=_json_loads(row["metadata_json"]),
            total_input_tokens=int(row["total_input_tokens"]),
            total_output_tokens=int(row["total_output_tokens"]),
            total_cache_read_tokens=int(row["total_cache_read_tokens"]),
            total_cache_creation_tokens=int(row["total_cache_creation_tokens"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _task_from_row(self, row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=str(row["task_id"]),
            conversation_id=str(row["conversation_id"]),
            title=str(row["title"]),
            goal=str(row["goal"]),
            status=str(row["status"]),
            priority=str(row["priority"]),
            owner=str(row["owner"]),
            policy_profile=str(row["policy_profile"]),
            source_channel=str(row["source_channel"]),
            parent_task_id=row["parent_task_id"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            requested_by=row["requested_by"],
        )

    def _step_from_row(self, row: sqlite3.Row) -> StepRecord:
        return StepRecord(
            step_id=str(row["step_id"]),
            task_id=str(row["task_id"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            attempt=int(row["attempt"]),
            input_ref=row["input_ref"],
            output_ref=row["output_ref"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def _step_attempt_from_row(self, row: sqlite3.Row) -> StepAttemptRecord:
        return StepAttemptRecord(
            step_attempt_id=str(row["step_attempt_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            attempt=int(row["attempt"]),
            status=str(row["status"]),
            context=_json_loads(row["context_json"]),
            waiting_reason=row["waiting_reason"],
            approval_id=row["approval_id"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def _artifact_from_row(self, row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=str(row["artifact_id"]),
            task_id=row["task_id"],
            step_id=row["step_id"],
            kind=str(row["kind"]),
            uri=str(row["uri"]),
            content_hash=str(row["content_hash"]),
            producer=str(row["producer"]),
            retention_class=str(row["retention_class"]),
            trust_tier=str(row["trust_tier"]),
            metadata=_json_loads(row["metadata_json"]),
            created_at=float(row["created_at"]),
        )

    def _approval_from_row(self, row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=str(row["approval_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            step_attempt_id=str(row["step_attempt_id"]),
            status=str(row["status"]),
            approval_type=str(row["approval_type"]),
            requested_action=_json_loads(row["requested_action_json"]),
            request_packet_ref=row["request_packet_ref"],
            requested_at=float(row["requested_at"]),
            resolved_at=row["resolved_at"],
            resolved_by=row["resolved_by"],
            resolution=_json_loads(row["resolution_json"]),
        )

    def _receipt_from_row(self, row: sqlite3.Row) -> ReceiptRecord:
        return ReceiptRecord(
            receipt_id=str(row["receipt_id"]),
            task_id=str(row["task_id"]),
            step_id=str(row["step_id"]),
            step_attempt_id=str(row["step_attempt_id"]),
            action_type=str(row["action_type"]),
            input_refs=list(_json_loads(row["input_refs_json"])),
            environment_ref=row["environment_ref"],
            policy_result=_json_loads(row["policy_result_json"]),
            approval_ref=row["approval_ref"],
            output_refs=list(_json_loads(row["output_refs_json"])),
            result_summary=str(row["result_summary"]),
            created_at=float(row["created_at"]),
        )

    def _schedule_from_row(self, row: sqlite3.Row) -> ScheduledJob:
        return ScheduledJob(
            id=str(row["id"]),
            name=str(row["name"]),
            prompt=str(row["prompt"]),
            schedule_type=str(row["schedule_type"]),
            cron_expr=row["cron_expr"],
            once_at=row["once_at"],
            interval_seconds=row["interval_seconds"],
            enabled=bool(row["enabled"]),
            created_at=float(row["created_at"]),
            last_run_at=row["last_run_at"],
            next_run_at=row["next_run_at"],
            max_retries=int(row["max_retries"]),
            feishu_chat_id=row["feishu_chat_id"],
        )
