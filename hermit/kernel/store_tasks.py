from __future__ import annotations

import json
import time
from typing import Any

from hermit.kernel.models import ConversationRecord, StepAttemptRecord, StepRecord, TaskRecord
from hermit.kernel.store_support import _UNSET, _json_loads


class KernelTaskStoreMixin:
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
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="task.created",
                entity_type="task",
                entity_id=task_id,
                task_id=task_id,
                actor=requested_by or owner,
                payload={
                    "conversation_id": conversation_id,
                    "title": title,
                    "goal": goal,
                    "status": "running",
                    "priority": priority,
                    "owner": owner,
                    "policy_profile": policy_profile,
                    "source_channel": source_channel,
                    "parent_task_id": parent_task_id,
                    "requested_by": requested_by,
                    "created_at": now,
                    "updated_at": now,
                },
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

    def update_task_status(self, task_id: str, status: str, *, payload: dict[str, Any] | None = None) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (status, now, task_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type=f"task.{status}",
                entity_type="task",
                entity_id=task_id,
                task_id=task_id,
                actor="kernel",
                payload={"status": status, **(payload or {})},
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
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="step.started",
                entity_type="step",
                entity_id=step_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "task_id": task_id,
                    "kind": kind,
                    "status": status,
                    "attempt": 1,
                    "input_ref": None,
                    "output_ref": None,
                    "started_at": now,
                    "finished_at": None,
                },
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
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="step.updated",
                entity_type="step",
                entity_id=step_id,
                task_id=step.task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "task_id": step.task_id,
                    "kind": step.kind,
                    "attempt": step.attempt,
                    **values,
                },
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
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="step_attempt.started",
                entity_type="step_attempt",
                entity_id=step_attempt_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "task_id": task_id,
                    "step_id": step_id,
                    "attempt": attempt,
                    "status": status,
                    "context": context or {},
                    "waiting_reason": None,
                    "approval_id": None,
                    "decision_id": None,
                    "permit_id": None,
                    "state_witness_ref": None,
                    "started_at": now,
                    "finished_at": None,
                },
            )
        return self.get_step_attempt(step_attempt_id)  # type: ignore[return-value]

    def get_step_attempt(self, step_attempt_id: str) -> StepAttemptRecord | None:
        with self._lock:
            row = self._row("SELECT * FROM step_attempts WHERE step_attempt_id = ?", (step_attempt_id,))
        return self._step_attempt_from_row(row) if row is not None else None

    def list_step_attempts(
        self,
        *,
        task_id: str | None = None,
        step_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[StepAttemptRecord]:
        clauses = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if step_id:
            clauses.append("step_id = ?")
            params.append(step_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM step_attempts {where} ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._rows(query, tuple(params))
        return [self._step_attempt_from_row(row) for row in rows]

    def update_step_attempt(
        self,
        step_attempt_id: str,
        *,
        status: str | None = None,
        context: dict[str, Any] | object = _UNSET,
        waiting_reason: str | None | object = _UNSET,
        approval_id: str | None | object = _UNSET,
        decision_id: str | None | object = _UNSET,
        permit_id: str | None | object = _UNSET,
        state_witness_ref: str | None | object = _UNSET,
        finished_at: float | None | object = _UNSET,
    ) -> None:
        attempt = self.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        payload = {
            "status": status or attempt.status,
            "waiting_reason": attempt.waiting_reason if waiting_reason is _UNSET else waiting_reason,
            "approval_id": attempt.approval_id if approval_id is _UNSET else approval_id,
            "decision_id": attempt.decision_id if decision_id is _UNSET else decision_id,
            "permit_id": attempt.permit_id if permit_id is _UNSET else permit_id,
            "state_witness_ref": attempt.state_witness_ref if state_witness_ref is _UNSET else state_witness_ref,
            "finished_at": attempt.finished_at if finished_at is _UNSET else finished_at,
        }
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE step_attempts
                SET status = ?, context_json = ?, waiting_reason = ?, approval_id = ?, decision_id = ?, permit_id = ?, state_witness_ref = ?, finished_at = ?
                WHERE step_attempt_id = ?
                """,
                (
                    payload["status"],
                    json.dumps(attempt.context if context is _UNSET else context, ensure_ascii=False),
                    payload["waiting_reason"],
                    payload["approval_id"],
                    payload["decision_id"],
                    payload["permit_id"],
                    payload["state_witness_ref"],
                    payload["finished_at"],
                    step_attempt_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="step_attempt.updated",
                entity_type="step_attempt",
                entity_id=step_attempt_id,
                task_id=attempt.task_id,
                step_id=attempt.step_id,
                actor="kernel",
                payload={
                    "task_id": attempt.task_id,
                    "step_id": attempt.step_id,
                    "attempt": attempt.attempt,
                    "context": attempt.context if context is _UNSET else context,
                    **payload,
                },
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
            self._append_event_tx(
                event_id=event_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                task_id=task_id,
                step_id=step_id,
                actor=actor,
                payload=payload,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
        return event_id

    def list_events(
        self,
        *,
        task_id: str | None = None,
        after_event_seq: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if after_event_seq is not None:
            clauses.append("event_seq > ?")
            params.append(after_event_seq)
        if clauses:
            where = f"WHERE {' AND '.join(clauses)}"
            query = f"SELECT * FROM events {where} ORDER BY event_seq ASC LIMIT ?"
            params.append(limit)
        else:
            query = "SELECT * FROM events ORDER BY event_seq DESC LIMIT ?"
            params = [limit]
        with self._lock:
            rows = self._rows(query, tuple(params))
        return [
            {
                "event_seq": int(row["event_seq"]),
                "event_id": str(row["event_id"]),
                "task_id": row["task_id"],
                "step_id": row["step_id"],
                "entity_type": str(row["entity_type"]),
                "entity_id": str(row["entity_id"]),
                "event_type": str(row["event_type"]),
                "actor": str(row["actor"]),
                "payload": _json_loads(row["payload_json"]),
                "occurred_at": float(row["occurred_at"]),
                "event_hash": row["event_hash"],
                "prev_event_hash": row["prev_event_hash"],
                "hash_chain_algo": row["hash_chain_algo"],
            }
            for row in rows
        ]
