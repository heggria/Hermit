from __future__ import annotations

import json
import time
from typing import Any

from hermit.kernel.models import (
    ConversationRecord,
    IngressRecord,
    StepAttemptRecord,
    StepRecord,
    TaskRecord,
)
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
                        conversation_id, source_channel, source_ref, last_task_id, focus_task_id,
                        focus_reason, focus_updated_at, status, metadata_json, total_input_tokens, total_output_tokens,
                        total_cache_read_tokens, total_cache_creation_tokens,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, NULL, NULL, NULL, NULL, 'open', '{}', 0, 0, 0, 0, ?, ?)
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
            row = self._row(
                "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
            )
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

    def set_conversation_focus(
        self, conversation_id: str, *, task_id: str | None, reason: str = ""
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE conversations
                SET focus_task_id = ?, focus_reason = ?, focus_updated_at = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (task_id, reason or None, now if task_id else None, now, conversation_id),
            )

    def ensure_valid_focus(self, conversation_id: str) -> str | None:
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            return None
        if conversation.focus_task_id:
            task = self.get_task(conversation.focus_task_id)
            if task is not None and task.status in {
                "queued",
                "running",
                "blocked",
                "planning_ready",
            }:
                return task.task_id
        open_tasks = self.list_open_tasks_for_conversation(conversation_id=conversation_id, limit=1)
        if not open_tasks:
            self.set_conversation_focus(conversation_id, task_id=None, reason="no_open_tasks")
            return None
        fallback = open_tasks[0]
        self.set_conversation_focus(
            conversation_id, task_id=fallback.task_id, reason="fallback_latest_open"
        )
        return fallback.task_id

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
        status: str = "running",
        owner: str = "hermit",
        priority: str = "normal",
        policy_profile: str = "default",
        parent_task_id: str | None = None,
        requested_by: str | None = None,
        task_contract_ref: str | None = None,
        continuation_anchor: dict[str, Any] | None = None,
    ) -> TaskRecord:
        now = time.time()
        task_id = self._id("task")
        owner_principal_id = self._ensure_principal_id(owner, source_channel=source_channel)
        requested_by_principal_id = (
            self._ensure_principal_id(requested_by, source_channel=source_channel)
            if requested_by is not None
            else None
        )
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    task_id, conversation_id, title, goal, status, priority, owner_principal_id,
                    policy_profile, source_channel, parent_task_id, task_contract_ref,
                    requested_by_principal_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    conversation_id,
                    title,
                    goal,
                    status,
                    priority,
                    owner_principal_id,
                    policy_profile,
                    source_channel,
                    parent_task_id,
                    task_contract_ref,
                    requested_by_principal_id,
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
                    "status": status,
                    "priority": priority,
                    "owner_principal_id": owner_principal_id,
                    "policy_profile": policy_profile,
                    "source_channel": source_channel,
                    "parent_task_id": parent_task_id,
                    "task_contract_ref": task_contract_ref,
                    "requested_by_principal_id": requested_by_principal_id,
                    "continuation_anchor": dict(continuation_anchor or {}),
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

    def list_tasks(
        self, *, conversation_id: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[TaskRecord]:
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

    def list_open_tasks_for_conversation(
        self, *, conversation_id: str, limit: int = 20
    ) -> list[TaskRecord]:
        with self._lock:
            rows = self._rows(
                """
                SELECT *
                FROM tasks
                WHERE conversation_id = ?
                  AND status IN ('queued', 'running', 'blocked', 'planning_ready')
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            )
        return [self._task_from_row(row) for row in rows]

    def update_task_status(
        self, task_id: str, status: str, *, payload: dict[str, Any] | None = None
    ) -> None:
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

    def create_step(
        self,
        *,
        task_id: str,
        kind: str,
        status: str = "running",
        title: str | None = None,
        contract_ref: str | None = None,
        depends_on: list[str] | None = None,
        max_attempts: int = 1,
    ) -> StepRecord:
        now = time.time()
        step_id = self._id("step")
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO steps (
                    step_id, task_id, kind, status, attempt, title, contract_ref,
                    depends_on_json, max_attempts, started_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    task_id,
                    kind,
                    status,
                    title or kind,
                    contract_ref,
                    json.dumps(list(depends_on or []), ensure_ascii=False),
                    max(int(max_attempts or 1), 1),
                    now,
                    now,
                    now,
                ),
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
                    "title": title or kind,
                    "contract_ref": contract_ref,
                    "depends_on": list(depends_on or []),
                    "max_attempts": max(int(max_attempts or 1), 1),
                    "input_ref": None,
                    "output_ref": None,
                    "started_at": now,
                    "finished_at": None,
                    "created_at": now,
                    "updated_at": now,
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
            "updated_at": now,
        }
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE steps
                SET status = ?, output_ref = ?, finished_at = ?, updated_at = ?
                WHERE step_id = ?
                """,
                (
                    values["status"],
                    values["output_ref"],
                    values["finished_at"],
                    values["updated_at"],
                    step_id,
                ),
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
                    "title": step.title,
                    "contract_ref": step.contract_ref,
                    "depends_on": list(step.depends_on),
                    "max_attempts": step.max_attempts,
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
        queue_priority: int = 0,
        context_pack_ref: str | None = None,
        working_state_ref: str | None = None,
        environment_ref: str | None = None,
        action_request_ref: str | None = None,
        policy_result_ref: str | None = None,
        approval_packet_ref: str | None = None,
        pending_execution_ref: str | None = None,
        idempotency_key: str | None = None,
        executor_mode: str | None = None,
        policy_version: str | None = None,
        resume_from_ref: str | None = None,
    ) -> StepAttemptRecord:
        now = time.time()
        step_attempt_id = self._id("attempt")
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO step_attempts (
                    step_attempt_id, task_id, step_id, attempt, status, context_json,
                    queue_priority, context_pack_ref, working_state_ref, environment_ref,
                    action_request_ref, policy_result_ref, approval_packet_ref, pending_execution_ref, idempotency_key,
                    executor_mode, policy_version, resume_from_ref, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_attempt_id,
                    task_id,
                    step_id,
                    attempt,
                    status,
                    json.dumps(context or {}, ensure_ascii=False),
                    int(queue_priority),
                    context_pack_ref,
                    working_state_ref,
                    environment_ref,
                    action_request_ref,
                    policy_result_ref,
                    approval_packet_ref,
                    pending_execution_ref,
                    idempotency_key,
                    executor_mode,
                    policy_version,
                    resume_from_ref,
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
                    "queue_priority": int(queue_priority),
                    "waiting_reason": None,
                    "approval_id": None,
                    "decision_id": None,
                    "capability_grant_id": None,
                    "workspace_lease_id": None,
                    "state_witness_ref": None,
                    "context_pack_ref": context_pack_ref,
                    "working_state_ref": working_state_ref,
                    "environment_ref": environment_ref,
                    "action_request_ref": action_request_ref,
                    "policy_result_ref": policy_result_ref,
                    "approval_packet_ref": approval_packet_ref,
                    "pending_execution_ref": pending_execution_ref,
                    "idempotency_key": idempotency_key,
                    "executor_mode": executor_mode,
                    "policy_version": policy_version,
                    "resume_from_ref": resume_from_ref,
                    "superseded_by_step_attempt_id": None,
                    "started_at": now,
                    "finished_at": None,
                },
            )
        return self.get_step_attempt(step_attempt_id)  # type: ignore[return-value]

    def get_step_attempt(self, step_attempt_id: str) -> StepAttemptRecord | None:
        with self._lock:
            row = self._row(
                "SELECT * FROM step_attempts WHERE step_attempt_id = ?", (step_attempt_id,)
            )
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

    def list_ready_step_attempts(self, *, limit: int = 100) -> list[StepAttemptRecord]:
        query = """
            SELECT sa.*
            FROM step_attempts sa
            JOIN steps s ON s.step_id = sa.step_id
            JOIN tasks t ON t.task_id = sa.task_id
            WHERE sa.status = 'ready'
              AND s.status = 'ready'
              AND t.status IN ('queued', 'running')
            ORDER BY sa.queue_priority DESC, sa.started_at ASC
            LIMIT ?
        """
        with self._lock:
            rows = self._rows(query, (limit,))
        return [self._step_attempt_from_row(row) for row in rows]

    def claim_next_ready_step_attempt(self) -> StepAttemptRecord | None:
        with self._lock, self._conn:
            row = self._row(
                """
                SELECT sa.*
                FROM step_attempts sa
                JOIN steps s ON s.step_id = sa.step_id
                JOIN tasks t ON t.task_id = sa.task_id
                WHERE sa.status = 'ready'
                  AND s.status = 'ready'
                  AND t.status IN ('queued', 'running')
                ORDER BY sa.queue_priority DESC, sa.started_at ASC
                LIMIT 1
                """
            )
            if row is None:
                return None
            attempt = self._step_attempt_from_row(row)
            self._conn.execute(
                "UPDATE step_attempts SET status = ? WHERE step_attempt_id = ?",
                ("running", attempt.step_attempt_id),
            )
            self._conn.execute(
                "UPDATE steps SET status = ?, finished_at = NULL WHERE step_id = ?",
                ("running", attempt.step_id),
            )
            self._conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                ("running", time.time(), attempt.task_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="step_attempt.claimed",
                entity_type="step_attempt",
                entity_id=attempt.step_attempt_id,
                task_id=attempt.task_id,
                step_id=attempt.step_id,
                actor="kernel",
                payload={"status": "running", "attempt": attempt.attempt},
            )
        return self.get_step_attempt(attempt.step_attempt_id)

    def update_step_attempt(
        self,
        step_attempt_id: str,
        *,
        status: str | None = None,
        context: dict[str, Any] | object = _UNSET,
        queue_priority: int | object = _UNSET,
        waiting_reason: str | None | object = _UNSET,
        approval_id: str | None | object = _UNSET,
        decision_id: str | None | object = _UNSET,
        capability_grant_id: str | None | object = _UNSET,
        workspace_lease_id: str | None | object = _UNSET,
        state_witness_ref: str | None | object = _UNSET,
        context_pack_ref: str | None | object = _UNSET,
        working_state_ref: str | None | object = _UNSET,
        environment_ref: str | None | object = _UNSET,
        action_request_ref: str | None | object = _UNSET,
        policy_result_ref: str | None | object = _UNSET,
        approval_packet_ref: str | None | object = _UNSET,
        pending_execution_ref: str | None | object = _UNSET,
        idempotency_key: str | None | object = _UNSET,
        executor_mode: str | None | object = _UNSET,
        policy_version: str | None | object = _UNSET,
        resume_from_ref: str | None | object = _UNSET,
        superseded_by_step_attempt_id: str | None | object = _UNSET,
        finished_at: float | None | object = _UNSET,
    ) -> None:
        attempt = self.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        payload = {
            "status": status or attempt.status,
            "queue_priority": attempt.queue_priority
            if queue_priority is _UNSET
            else queue_priority,
            "waiting_reason": attempt.waiting_reason
            if waiting_reason is _UNSET
            else waiting_reason,
            "approval_id": attempt.approval_id if approval_id is _UNSET else approval_id,
            "decision_id": attempt.decision_id if decision_id is _UNSET else decision_id,
            "capability_grant_id": (
                attempt.capability_grant_id
                if capability_grant_id is _UNSET
                else capability_grant_id
            ),
            "workspace_lease_id": (
                attempt.workspace_lease_id if workspace_lease_id is _UNSET else workspace_lease_id
            ),
            "state_witness_ref": attempt.state_witness_ref
            if state_witness_ref is _UNSET
            else state_witness_ref,
            "context_pack_ref": (
                attempt.context_pack_ref if context_pack_ref is _UNSET else context_pack_ref
            ),
            "working_state_ref": (
                attempt.working_state_ref if working_state_ref is _UNSET else working_state_ref
            ),
            "environment_ref": (
                attempt.environment_ref if environment_ref is _UNSET else environment_ref
            ),
            "action_request_ref": (
                attempt.action_request_ref if action_request_ref is _UNSET else action_request_ref
            ),
            "policy_result_ref": (
                attempt.policy_result_ref if policy_result_ref is _UNSET else policy_result_ref
            ),
            "approval_packet_ref": (
                attempt.approval_packet_ref
                if approval_packet_ref is _UNSET
                else approval_packet_ref
            ),
            "pending_execution_ref": (
                attempt.pending_execution_ref
                if pending_execution_ref is _UNSET
                else pending_execution_ref
            ),
            "idempotency_key": (
                attempt.idempotency_key if idempotency_key is _UNSET else idempotency_key
            ),
            "executor_mode": (attempt.executor_mode if executor_mode is _UNSET else executor_mode),
            "policy_version": (
                attempt.policy_version if policy_version is _UNSET else policy_version
            ),
            "resume_from_ref": (
                attempt.resume_from_ref if resume_from_ref is _UNSET else resume_from_ref
            ),
            "superseded_by_step_attempt_id": (
                attempt.superseded_by_step_attempt_id
                if superseded_by_step_attempt_id is _UNSET
                else superseded_by_step_attempt_id
            ),
            "finished_at": attempt.finished_at if finished_at is _UNSET else finished_at,
        }
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE step_attempts
                SET status = ?, context_json = ?, queue_priority = ?, waiting_reason = ?, approval_id = ?, decision_id = ?, capability_grant_id = ?, workspace_lease_id = ?, state_witness_ref = ?, context_pack_ref = ?, working_state_ref = ?, environment_ref = ?, action_request_ref = ?, policy_result_ref = ?, approval_packet_ref = ?, pending_execution_ref = ?, idempotency_key = ?, executor_mode = ?, policy_version = ?, resume_from_ref = ?, superseded_by_step_attempt_id = ?, finished_at = ?
                WHERE step_attempt_id = ?
                """,
                (
                    payload["status"],
                    json.dumps(
                        attempt.context if context is _UNSET else context, ensure_ascii=False
                    ),
                    int(payload["queue_priority"] or 0),
                    payload["waiting_reason"],
                    payload["approval_id"],
                    payload["decision_id"],
                    payload["capability_grant_id"],
                    payload["workspace_lease_id"],
                    payload["state_witness_ref"],
                    payload["context_pack_ref"],
                    payload["working_state_ref"],
                    payload["environment_ref"],
                    payload["action_request_ref"],
                    payload["policy_result_ref"],
                    payload["approval_packet_ref"],
                    payload["pending_execution_ref"],
                    payload["idempotency_key"],
                    payload["executor_mode"],
                    payload["policy_version"],
                    payload["resume_from_ref"],
                    payload["superseded_by_step_attempt_id"],
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

    def create_ingress(
        self,
        *,
        conversation_id: str,
        source_channel: str,
        raw_text: str,
        normalized_text: str,
        actor: str | None = None,
        prompt_ref: str | None = None,
        reply_to_ref: str | None = None,
        quoted_message_ref: str | None = None,
        explicit_task_ref: str | None = None,
        referenced_artifact_refs: list[str] | None = None,
    ) -> IngressRecord:
        now = time.time()
        ingress_id = self._id("ingress")
        actor_principal_id = (
            self._ensure_principal_id(actor, source_channel=source_channel)
            if actor is not None
            else None
        )
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO ingresses (
                    ingress_id, conversation_id, source_channel, actor_principal_id, raw_text, normalized_text,
                    prompt_ref, reply_to_ref, quoted_message_ref, explicit_task_ref,
                    referenced_artifact_refs_json, status, resolution, chosen_task_id, parent_task_id,
                    confidence, margin, rationale_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'received', 'none', NULL, NULL, NULL, NULL, '{}', ?, ?)
                """,
                (
                    ingress_id,
                    conversation_id,
                    source_channel,
                    actor_principal_id,
                    raw_text,
                    normalized_text,
                    prompt_ref,
                    reply_to_ref,
                    quoted_message_ref,
                    explicit_task_ref,
                    json.dumps(list(referenced_artifact_refs or []), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="ingress.received",
                entity_type="ingress",
                entity_id=ingress_id,
                task_id=None,
                actor=actor or "user",
                payload={
                    "conversation_id": conversation_id,
                    "source_channel": source_channel,
                    "raw_text": raw_text,
                    "normalized_text": normalized_text,
                    "reply_to_ref": reply_to_ref,
                    "quoted_message_ref": quoted_message_ref,
                    "explicit_task_ref": explicit_task_ref,
                    "referenced_artifact_refs": list(referenced_artifact_refs or []),
                },
            )
        ingress = self.get_ingress(ingress_id)
        assert ingress is not None
        return ingress

    def get_ingress(self, ingress_id: str) -> IngressRecord | None:
        with self._lock:
            row = self._row("SELECT * FROM ingresses WHERE ingress_id = ?", (ingress_id,))
        return self._ingress_from_row(row) if row is not None else None

    def list_ingresses(
        self,
        *,
        conversation_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[IngressRecord]:
        clauses = []
        params: list[Any] = []
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        if task_id:
            clauses.append("chosen_task_id = ?")
            params.append(task_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM ingresses {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._rows(query, tuple(params))
        return [self._ingress_from_row(row) for row in rows]

    def count_pending_ingresses(self, *, conversation_id: str) -> int:
        with self._lock:
            row = self._row(
                """
                SELECT COUNT(*) AS count
                FROM ingresses
                WHERE conversation_id = ?
                  AND status IN ('received', 'pending_disambiguation')
                """,
                (conversation_id,),
            )
        return int(row["count"] if row is not None else 0)

    def update_ingress(
        self,
        ingress_id: str,
        *,
        status: str | object = _UNSET,
        resolution: str | object = _UNSET,
        chosen_task_id: str | None | object = _UNSET,
        parent_task_id: str | None | object = _UNSET,
        confidence: float | None | object = _UNSET,
        margin: float | None | object = _UNSET,
        rationale: dict[str, Any] | object = _UNSET,
    ) -> None:
        ingress = self.get_ingress(ingress_id)
        if ingress is None:
            return
        payload = {
            "status": ingress.status if status is _UNSET else status,
            "resolution": ingress.resolution if resolution is _UNSET else resolution,
            "chosen_task_id": ingress.chosen_task_id
            if chosen_task_id is _UNSET
            else chosen_task_id,
            "parent_task_id": ingress.parent_task_id
            if parent_task_id is _UNSET
            else parent_task_id,
            "confidence": ingress.confidence if confidence is _UNSET else confidence,
            "margin": ingress.margin if margin is _UNSET else margin,
            "rationale": ingress.rationale if rationale is _UNSET else rationale,
        }
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE ingresses
                SET status = ?, resolution = ?, chosen_task_id = ?, parent_task_id = ?,
                    confidence = ?, margin = ?, rationale_json = ?, updated_at = ?
                WHERE ingress_id = ?
                """,
                (
                    payload["status"],
                    payload["resolution"],
                    payload["chosen_task_id"],
                    payload["parent_task_id"],
                    payload["confidence"],
                    payload["margin"],
                    json.dumps(payload["rationale"] or {}, ensure_ascii=False),
                    now,
                    ingress_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type=(
                    "ingress.pending_disambiguation"
                    if payload["status"] == "pending_disambiguation"
                    else "ingress.bound"
                ),
                entity_type="ingress",
                entity_id=ingress_id,
                task_id=payload["chosen_task_id"],
                actor="kernel",
                payload={
                    "conversation_id": ingress.conversation_id,
                    "status": payload["status"],
                    "resolution": payload["resolution"],
                    "chosen_task_id": payload["chosen_task_id"],
                    "parent_task_id": payload["parent_task_id"],
                    "confidence": payload["confidence"],
                    "margin": payload["margin"],
                    "rationale": payload["rationale"] or {},
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
                "actor_principal_id": str(row["actor_principal_id"]),
                "actor": str(row["actor_principal_id"]),
                "payload": _json_loads(row["payload_json"]),
                "occurred_at": float(row["occurred_at"]),
                "event_hash": row["event_hash"],
                "prev_event_hash": row["prev_event_hash"],
                "hash_chain_algo": row["hash_chain_algo"],
            }
            for row in rows
        ]

    def iter_events(
        self,
        *,
        task_id: str | None = None,
        after_event_seq: int | None = None,
        batch_size: int = 200,
    ):
        cursor = after_event_seq
        while True:
            batch = self.list_events(
                task_id=task_id,
                after_event_seq=cursor,
                limit=batch_size,
            )
            if not batch:
                break
            for event in batch:
                yield event
            cursor = int(batch[-1]["event_seq"])
