from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any, cast

from hermit.kernel.ledger.journal.store_support import (
    UNSET,
    json_loads,
    sqlite_dict,
    sqlite_int,
    sqlite_list,
    sqlite_optional_float,
    sqlite_optional_text,
)
from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase
from hermit.kernel.task.models.records import (
    ConversationRecord,
    IngressRecord,
    StepAttemptRecord,
    StepRecord,
    TaskRecord,
)


class KernelTaskStoreMixin(KernelStoreTypingBase):
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
        normalized_parent_task_id = sqlite_optional_text(parent_task_id)
        normalized_task_contract_ref = sqlite_optional_text(task_contract_ref)
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
                    normalized_parent_task_id,
                    normalized_task_contract_ref,
                    sqlite_optional_text(requested_by_principal_id),
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
                    "parent_task_id": normalized_parent_task_id,
                    "task_contract_ref": normalized_task_contract_ref,
                    "requested_by_principal_id": sqlite_optional_text(requested_by_principal_id),
                    "continuation_anchor": sqlite_dict(continuation_anchor),
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
        clauses: list[str] = []
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

    def list_child_tasks(
        self, *, parent_task_id: str, limit: int | None = None
    ) -> list[TaskRecord]:
        """Return tasks whose parent_task_id matches the given value."""
        with self._lock:
            if limit is not None:
                rows = self._rows(
                    "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY created_at DESC LIMIT ?",
                    (parent_task_id, limit),
                )
            else:
                rows = self._rows(
                    "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY created_at DESC",
                    (parent_task_id,),
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

    def update_task_budget(
        self,
        task_id: str,
        *,
        budget_tokens_used: int | None = None,
        budget_tokens_limit: int | None = None,
    ) -> None:
        """Update budget tracking fields on a task."""
        now = time.time()
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [now]
        if budget_tokens_used is not None:
            sets.append("budget_tokens_used = ?")
            params.append(budget_tokens_used)
        if budget_tokens_limit is not None:
            sets.append("budget_tokens_limit = ?")
            params.append(budget_tokens_limit)
        params.append(task_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE tasks SET {', '.join(sets)} WHERE task_id = ?",
                params,
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
        join_strategy: str | None = None,
        input_bindings: dict[str, str] | None = None,
        node_key: str | None = None,
        verification_required: bool = False,
        verifies: list[str] | None = None,
        supersedes: list[str] | None = None,
    ) -> StepRecord:
        now = time.time()
        step_id = self._id("step")
        resolved_join_strategy = join_strategy or "all_required"
        resolved_input_bindings = dict(input_bindings or {})
        resolved_verifies = list(verifies or [])
        resolved_supersedes = list(supersedes or [])
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO steps (
                    step_id, task_id, kind, status, attempt, title, contract_ref,
                    depends_on_json, max_attempts, join_strategy, input_bindings_json,
                    node_key, verification_required, verifies_json, supersedes_json,
                    started_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    resolved_join_strategy,
                    json.dumps(resolved_input_bindings, ensure_ascii=False),
                    node_key,
                    1 if verification_required else 0,
                    json.dumps(resolved_verifies, ensure_ascii=False),
                    json.dumps(resolved_supersedes, ensure_ascii=False),
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
                    "join_strategy": resolved_join_strategy,
                    "input_bindings": resolved_input_bindings,
                    "node_key": node_key,
                    "verification_required": verification_required,
                    "verifies": resolved_verifies,
                    "supersedes": resolved_supersedes,
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

    def get_step_by_node_key(self, task_id: str, node_key: str) -> StepRecord | None:
        """Look up a step by its ``node_key`` within a task.

        Returns the matching :class:`StepRecord`, or ``None`` if no step has
        that ``node_key`` in the given task.
        """
        with self._lock:
            row = self._row(
                "SELECT * FROM steps WHERE task_id = ? AND node_key = ? LIMIT 1",
                (task_id, node_key),
            )
        return self._step_from_row(row) if row is not None else None

    def update_step(
        self,
        step_id: str,
        *,
        status: str | None = None,
        output_ref: str | None = None,
        contract_ref: str | None | object = UNSET,
        finished_at: float | None = None,
    ) -> None:
        now = time.time()
        step = self.get_step(step_id)
        if step is None:
            return
        values = {
            "status": status or step.status,
            "output_ref": sqlite_optional_text(
                output_ref, default=sqlite_optional_text(step.output_ref)
            ),
            "contract_ref": sqlite_optional_text(
                step.contract_ref if contract_ref is UNSET else contract_ref,
                default=sqlite_optional_text(step.contract_ref),
            ),
            "finished_at": finished_at if finished_at is not None else step.finished_at,
            "updated_at": now,
        }
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE steps
                SET status = ?, output_ref = ?, contract_ref = ?, finished_at = ?, updated_at = ?
                WHERE step_id = ?
                """,
                (
                    values["status"],
                    values["output_ref"],
                    values["contract_ref"],
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
                    "contract_ref": values["contract_ref"],
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
        execution_contract_ref: str | None = None,
        evidence_case_ref: str | None = None,
        authorization_plan_ref: str | None = None,
        reconciliation_ref: str | None = None,
        pending_execution_ref: str | None = None,
        idempotency_key: str | None = None,
        executor_mode: str | None = None,
        policy_version: str | None = None,
        contract_version: int = 0,
        reentry_boundary: str | None = None,
        reentry_reason: str | None = None,
        selected_contract_template_ref: str | None = None,
        resume_from_ref: str | None = None,
    ) -> StepAttemptRecord:
        now = time.time()
        step_attempt_id = self._id("attempt")
        normalized_context = sqlite_dict(context)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO step_attempts (
                    step_attempt_id, task_id, step_id, attempt, status, context_json,
                    queue_priority, context_pack_ref, working_state_ref, environment_ref,
                    action_request_ref, policy_result_ref, approval_packet_ref,
                    execution_contract_ref, evidence_case_ref, authorization_plan_ref, reconciliation_ref,
                    pending_execution_ref, idempotency_key,
                    executor_mode, policy_version, contract_version, reentry_boundary, reentry_reason,
                    selected_contract_template_ref, resume_from_ref, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_attempt_id,
                    task_id,
                    step_id,
                    attempt,
                    status,
                    json.dumps(normalized_context, ensure_ascii=False),
                    sqlite_int(queue_priority),
                    sqlite_optional_text(context_pack_ref),
                    sqlite_optional_text(working_state_ref),
                    sqlite_optional_text(environment_ref),
                    sqlite_optional_text(action_request_ref),
                    sqlite_optional_text(policy_result_ref),
                    sqlite_optional_text(approval_packet_ref),
                    sqlite_optional_text(execution_contract_ref),
                    sqlite_optional_text(evidence_case_ref),
                    sqlite_optional_text(authorization_plan_ref),
                    sqlite_optional_text(reconciliation_ref),
                    sqlite_optional_text(pending_execution_ref),
                    sqlite_optional_text(idempotency_key),
                    sqlite_optional_text(executor_mode),
                    sqlite_optional_text(policy_version),
                    sqlite_int(contract_version),
                    sqlite_optional_text(reentry_boundary),
                    sqlite_optional_text(reentry_reason),
                    sqlite_optional_text(selected_contract_template_ref),
                    sqlite_optional_text(resume_from_ref),
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
                    "context": normalized_context,
                    "queue_priority": sqlite_int(queue_priority),
                    "waiting_reason": None,
                    "approval_id": None,
                    "decision_id": None,
                    "capability_grant_id": None,
                    "workspace_lease_id": None,
                    "state_witness_ref": None,
                    "context_pack_ref": sqlite_optional_text(context_pack_ref),
                    "working_state_ref": sqlite_optional_text(working_state_ref),
                    "environment_ref": sqlite_optional_text(environment_ref),
                    "action_request_ref": sqlite_optional_text(action_request_ref),
                    "policy_result_ref": sqlite_optional_text(policy_result_ref),
                    "approval_packet_ref": sqlite_optional_text(approval_packet_ref),
                    "execution_contract_ref": sqlite_optional_text(execution_contract_ref),
                    "evidence_case_ref": sqlite_optional_text(evidence_case_ref),
                    "authorization_plan_ref": sqlite_optional_text(authorization_plan_ref),
                    "reconciliation_ref": sqlite_optional_text(reconciliation_ref),
                    "pending_execution_ref": sqlite_optional_text(pending_execution_ref),
                    "idempotency_key": sqlite_optional_text(idempotency_key),
                    "executor_mode": sqlite_optional_text(executor_mode),
                    "policy_version": sqlite_optional_text(policy_version),
                    "contract_version": sqlite_int(contract_version),
                    "reentry_boundary": sqlite_optional_text(reentry_boundary),
                    "reentry_reason": sqlite_optional_text(reentry_reason),
                    "selected_contract_template_ref": sqlite_optional_text(
                        selected_contract_template_ref
                    ),
                    "resume_from_ref": sqlite_optional_text(resume_from_ref),
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
        clauses: list[str] = []
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
        now = time.time()
        with self._lock, self._conn:
            row = self._row(
                """
                UPDATE step_attempts
                SET status = 'running', claimed_at = ?
                WHERE step_attempt_id = (
                    SELECT sa.step_attempt_id
                    FROM step_attempts sa
                    JOIN steps s ON s.step_id = sa.step_id
                    JOIN tasks t ON t.task_id = sa.task_id
                    WHERE sa.status = 'ready'
                      AND s.status = 'ready'
                      AND t.status IN ('queued', 'running', 'reconciling', 'blocked', 'planning_ready')
                      AND NOT EXISTS (
                          SELECT 1 FROM steps dep
                          WHERE dep.task_id = s.task_id
                            AND dep.step_id IN (
                                SELECT value FROM json_each(s.depends_on_json)
                            )
                            AND dep.status NOT IN (
                                'succeeded', 'completed', 'skipped', 'needs_attention'
                            )
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM step_attempts sa2
                          WHERE sa2.step_id = sa.step_id
                            AND sa2.status IN (
                                'running', 'dispatching', 'reconciling',
                                'observing', 'contracting', 'preflighting'
                            )
                      )
                    ORDER BY sa.queue_priority DESC, sa.started_at ASC
                    LIMIT 1
                )
                RETURNING *
                """,
                (now,),
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

    _TERMINAL_STEP_STATUSES = frozenset({"succeeded", "completed", "skipped", "failed"})

    def activate_waiting_dependents(self, task_id: str, completed_step_id: str) -> list[str]:
        """Activate waiting steps whose dependencies are all satisfied.

        Returns the list of step_ids that were activated (waiting -> ready).
        """
        activated: list[str] = []
        candidates = self._rows(
            """
            SELECT s.step_id, s.depends_on_json, s.join_strategy
            FROM steps s, json_each(s.depends_on_json) dep
            WHERE s.task_id = ? AND s.status = 'waiting'
              AND dep.value = ?
            GROUP BY s.step_id
            """,
            (task_id, completed_step_id),
        )
        for row in candidates:
            step_id = str(row["step_id"])
            deps = list(json.loads(str(row["depends_on_json"] or "[]")))
            strategy = str(row["join_strategy"] or "all_required")
            if self._join_barrier_satisfied(task_id, deps, strategy):
                now = time.time()
                with self._lock, self._conn:
                    self._conn.execute(
                        "UPDATE steps SET status = 'ready', updated_at = ? WHERE step_id = ?",
                        (now, step_id),
                    )
                    self._conn.execute(
                        """
                        UPDATE step_attempts SET status = 'ready'
                        WHERE step_id = ? AND status = 'waiting'
                        """,
                        (step_id,),
                    )
                    self._append_event_tx(
                        event_id=self._id("event"),
                        event_type="step.dependency_satisfied",
                        entity_type="step",
                        entity_id=step_id,
                        task_id=task_id,
                        step_id=step_id,
                        actor="kernel",
                        payload={
                            "activated_by": completed_step_id,
                            "strategy": strategy,
                        },
                    )
                activated.append(step_id)
        return activated

    def _join_barrier_satisfied(self, task_id: str, deps: list[str], strategy: str) -> bool:
        """Check if the join barrier for a step is satisfied based on strategy."""
        if not deps:
            return True
        rows = self._rows(
            "SELECT step_id, status FROM steps WHERE task_id = ? AND step_id IN ({})".format(
                ",".join("?" for _ in deps)
            ),
            (task_id, *deps),
        )
        statuses = {str(r["step_id"]): str(r["status"]) for r in rows}
        succeeded = sum(1 for s in statuses.values() if s in ("succeeded", "completed", "skipped"))
        failed = sum(1 for s in statuses.values() if s in ("failed", "needs_attention"))
        total = len(deps)
        terminal = succeeded + failed

        if strategy == "all_required":
            return succeeded == total
        elif strategy == "any_sufficient":
            return succeeded >= 1
        elif strategy == "majority":
            return succeeded > total / 2
        elif strategy == "best_effort":
            return terminal == total
        return succeeded == total

    def propagate_step_failure(self, task_id: str, failed_step_id: str) -> list[str]:
        """Cascade failure to downstream waiting steps (all_required strategy)."""
        cascaded: list[str] = []
        waiting = self._rows(
            """
            SELECT s.step_id, s.depends_on_json, s.join_strategy
            FROM steps s, json_each(s.depends_on_json) dep
            WHERE s.task_id = ? AND s.status = 'waiting'
              AND dep.value = ?
            GROUP BY s.step_id
            """,
            (task_id, failed_step_id),
        )
        for row in waiting:
            step_id = str(row["step_id"])
            deps = list(json.loads(str(row["depends_on_json"] or "[]")))
            strategy = str(row["join_strategy"] or "all_required")
            should_cascade = False
            if strategy == "all_required":
                should_cascade = True
            elif strategy == "any_sufficient":
                all_deps_rows = self._rows(
                    "SELECT step_id, status FROM steps"
                    " WHERE task_id = ? AND step_id IN ({})".format(",".join("?" for _ in deps)),
                    (task_id, *deps),
                )
                all_failed = all(str(r["status"]) == "failed" for r in all_deps_rows)
                should_cascade = all_failed
            elif strategy == "majority":
                all_deps_rows = self._rows(
                    "SELECT step_id, status FROM steps"
                    " WHERE task_id = ? AND step_id IN ({})".format(",".join("?" for _ in deps)),
                    (task_id, *deps),
                )
                failed_count = sum(1 for r in all_deps_rows if str(r["status"]) == "failed")
                should_cascade = failed_count > len(deps) / 2

            if should_cascade:
                now = time.time()
                with self._lock, self._conn:
                    self._conn.execute(
                        "UPDATE steps SET status = 'failed', finished_at = ?,"
                        " updated_at = ? WHERE step_id = ?",
                        (now, now, step_id),
                    )
                    self._conn.execute(
                        """
                        UPDATE step_attempts SET status = 'failed',
                            waiting_reason = 'dependency_failed', finished_at = ?
                        WHERE step_id = ? AND status = 'waiting'
                        """,
                        (now, step_id),
                    )
                    self._append_event_tx(
                        event_id=self._id("event"),
                        event_type="step.dependency_failed",
                        entity_type="step",
                        entity_id=step_id,
                        task_id=task_id,
                        step_id=step_id,
                        actor="kernel",
                        payload={
                            "failed_dependency": failed_step_id,
                            "strategy": strategy,
                        },
                    )
                cascaded.append(step_id)
                cascaded.extend(self.propagate_step_failure(task_id, step_id))

        self.activate_waiting_dependents(task_id, failed_step_id)
        return cascaded

    def retry_step(
        self,
        task_id: str,
        step_id: str,
        *,
        queue_priority: int = 0,
    ) -> StepAttemptRecord:
        """Atomically increment the step attempt counter and create a new ready attempt."""
        step = self.get_step(step_id)
        if step is None:
            raise ValueError(f"Step {step_id!r} not found")
        next_attempt_num = step.attempt + 1
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE steps
                SET attempt = ?, status = 'ready', finished_at = NULL, updated_at = ?
                WHERE step_id = ?
                """,
                (next_attempt_num, now, step_id),
            )
            self._conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="step.retry_scheduled",
                entity_type="step",
                entity_id=step_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "attempt": next_attempt_num,
                    "max_attempts": step.max_attempts,
                    "previous_attempt": step.attempt,
                },
            )
        return self.create_step_attempt(
            task_id=task_id,
            step_id=step_id,
            attempt=next_attempt_num,
            status="ready",
            queue_priority=queue_priority,
        )

    def skip_step(
        self,
        task_id: str,
        step_id: str,
        *,
        reason: str = "",
    ) -> None:
        """Mark a step as skipped (terminal) and activate downstream dependents."""
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE steps
                SET status = 'skipped', finished_at = ?, updated_at = ?
                WHERE step_id = ?
                """,
                (now, now, step_id),
            )
            self._conn.execute(
                """
                UPDATE step_attempts
                SET status = 'skipped', finished_at = ?
                WHERE step_id = ? AND status IN ('waiting', 'ready')
                """,
                (now, step_id),
            )
            self._conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="dag.topology_changed",
                entity_type="step",
                entity_id=step_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "mutation": "skip_step",
                    "step_id": step_id,
                    "reason": reason,
                },
            )
        self.activate_waiting_dependents(task_id, step_id)

    def update_step_depends_on(
        self,
        step_id: str,
        *,
        task_id: str,
        new_depends_on: list[str],
    ) -> None:
        """Update the dependencies of a step and recalculate its status."""
        step = self.get_step(step_id)
        if step is None:
            return
        old_depends_on = list(step.depends_on)
        now = time.time()

        if not new_depends_on:
            new_status = "ready"
        else:
            rows = self._rows(
                "SELECT step_id, status FROM steps WHERE task_id = ? AND step_id IN ({})".format(
                    ",".join("?" for _ in new_depends_on)
                ),
                (task_id, *new_depends_on),
            )
            dep_statuses = {str(r["step_id"]): str(r["status"]) for r in rows}
            all_success = all(
                dep_statuses.get(d) in ("succeeded", "completed", "skipped") for d in new_depends_on
            )
            new_status = "ready" if all_success else "waiting"

        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE steps
                SET depends_on_json = ?, status = ?, updated_at = ?
                WHERE step_id = ?
                """,
                (json.dumps(new_depends_on, ensure_ascii=False), new_status, now, step_id),
            )
            if new_status == "ready" and step.status == "waiting":
                self._conn.execute(
                    """
                    UPDATE step_attempts
                    SET status = 'ready'
                    WHERE step_id = ? AND status = 'waiting'
                    """,
                    (step_id,),
                )
            elif new_status == "waiting" and step.status == "ready":
                self._conn.execute(
                    """
                    UPDATE step_attempts
                    SET status = 'waiting'
                    WHERE step_id = ? AND status = 'ready'
                    """,
                    (step_id,),
                )
            self._conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="dag.topology_changed",
                entity_type="step",
                entity_id=step_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "mutation": "rewire_dependency",
                    "step_id": step_id,
                    "old_depends_on": old_depends_on,
                    "new_depends_on": new_depends_on,
                    "new_status": new_status,
                },
            )

    def list_stale_tasks(self, threshold_seconds: float, limit: int = 50) -> list[TaskRecord]:
        """Return tasks that have been idle longer than *threshold_seconds*.

        Only considers tasks in active (non-terminal) statuses.
        """
        cutoff = time.time() - threshold_seconds
        with self._lock:
            rows = self._rows(
                """
                SELECT * FROM tasks
                WHERE status IN ('running', 'blocked', 'queued', 'planning_ready', 'reconciling')
                  AND updated_at < ?
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (cutoff, limit),
            )
        return [self._task_from_row(row) for row in rows]

    def count_tasks_by_status(self) -> dict[str, int]:
        """Return a mapping of task status to count across all tasks."""
        with self._lock:
            rows = self._rows("SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status")
        return {str(row["status"]): int(row["cnt"]) for row in rows}

    def count_completed_in_window(self, window_seconds: float) -> int:
        """Count tasks that completed within the given look-back window."""
        cutoff = time.time() - window_seconds
        with self._lock:
            row = self._row(
                """
                SELECT COUNT(*) AS cnt FROM tasks
                WHERE status = 'completed' AND updated_at >= ?
                """,
                (cutoff,),
            )
        return int(row["cnt"]) if row is not None else 0

    def list_recent_failures(self, window_seconds: float, limit: int = 200) -> list[TaskRecord]:
        """Return tasks that failed within the given look-back window."""
        cutoff = time.time() - window_seconds
        with self._lock:
            rows = self._rows(
                """
                SELECT * FROM tasks
                WHERE status = 'failed' AND updated_at >= ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (cutoff, limit),
            )
        return [self._task_from_row(row) for row in rows]

    def list_active_tasks(self, *, limit: int = 200) -> list[TaskRecord]:
        """Return tasks in active (non-terminal) states."""
        with self._lock:
            rows = self._rows(
                """
                SELECT * FROM tasks
                WHERE status IN ('queued', 'running', 'blocked', 'planning_ready')
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [self._task_from_row(row) for row in rows]

    def list_terminal_tasks_since(self, *, since: float, limit: int = 200) -> list[TaskRecord]:
        """Return tasks that reached a terminal state since the given timestamp."""
        with self._lock:
            rows = self._rows(
                """
                SELECT * FROM tasks
                WHERE status IN ('completed', 'failed', 'cancelled')
                  AND updated_at >= ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (since, limit),
            )
        return [self._task_from_row(row) for row in rows]

    def has_active_task_with_goal(self, goal: str, *, policy_profile: str | None = None) -> bool:
        """Check if any active task contains the given goal substring.

        Used by memory promotion hooks to prevent duplicate task creation.
        """
        if not goal:
            return False
        clauses = [
            "status IN ('queued', 'running', 'blocked', 'planning_ready')",
            "goal LIKE ?",
        ]
        params: list[Any] = [f"%{goal}%"]
        if policy_profile is not None:
            clauses.append("policy_profile = ?")
            params.append(policy_profile)
        where = " AND ".join(clauses)
        with self._lock:
            row = self._row(
                f"SELECT COUNT(*) AS cnt FROM tasks WHERE {where}",
                tuple(params),
            )
        return bool(row and int(row["cnt"]) > 0)

    def batch_get_step_attempts(self, step_attempt_ids: list[str]) -> dict[str, StepAttemptRecord]:
        """Return a mapping of step_attempt_id -> StepAttemptRecord for the given ids."""
        if not step_attempt_ids:
            return {}
        placeholders = ",".join("?" for _ in step_attempt_ids)
        with self._lock:
            rows = self._rows(
                f"SELECT * FROM step_attempts WHERE step_attempt_id IN ({placeholders})",
                tuple(step_attempt_ids),
            )
        return {str(row["step_attempt_id"]): self._step_attempt_from_row(row) for row in rows}

    def list_events_for_tasks(
        self,
        task_ids: list[str],
        *,
        limit_per_task: int = 500,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return events grouped by task_id for multiple tasks."""
        if not task_ids:
            return {}
        result: dict[str, list[dict[str, Any]]] = {}
        for task_id in task_ids:
            result[task_id] = self.list_events(task_id=task_id, limit=limit_per_task)
        return result

    def get_last_event_per_task(
        self,
        task_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Return the most recent event for each task_id."""
        if not task_ids:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for task_id in task_ids:
            events = self.list_events(task_id=task_id, limit=1)
            if events:
                result[task_id] = events[-1]
        return result

    def count_steps_by_status(self, task_id: str) -> dict[str, int]:
        """Return a mapping of step status to count for a given task."""
        with self._lock:
            rows = self._rows(
                "SELECT status, COUNT(*) AS cnt FROM steps WHERE task_id = ? GROUP BY status",
                (task_id,),
            )
        return {str(row["status"]): int(row["cnt"]) for row in rows}

    def list_steps(self, *, task_id: str | None = None, limit: int = 200) -> list[StepRecord]:
        """Return steps, optionally filtered by task_id."""
        if task_id:
            with self._lock:
                rows = self._rows(
                    "SELECT * FROM steps WHERE task_id = ? ORDER BY created_at ASC LIMIT ?",
                    (task_id, limit),
                )
        else:
            with self._lock:
                rows = self._rows(
                    "SELECT * FROM steps ORDER BY created_at ASC LIMIT ?",
                    (limit,),
                )
        return [self._step_from_row(row) for row in rows]

    def get_key_to_step_id(self, task_id: str) -> dict[str, str]:
        """Return a mapping of node_key -> step_id for all steps in a task.

        Steps without a node_key are excluded from the mapping.
        """
        with self._lock:
            rows = self._rows(
                "SELECT step_id, node_key FROM steps WHERE task_id = ? AND node_key IS NOT NULL",
                (task_id,),
            )
        return {str(row["node_key"]): str(row["step_id"]) for row in rows}

    def has_non_terminal_steps(self, task_id: str) -> bool:
        """Check if there are any non-terminal steps for a task."""
        row = self._row(
            """
            SELECT COUNT(*) as cnt FROM steps
            WHERE task_id = ? AND status NOT IN (
                'succeeded', 'completed', 'skipped', 'failed', 'cancelled', 'needs_attention'
            )
            """,
            (task_id,),
        )
        return bool(row and int(row["cnt"]) > 0)

    _TERMINAL_ATTEMPT_STATUSES = frozenset(
        {
            "succeeded",
            "completed",
            "skipped",
            "failed",
            "cancelled",
            "superseded",
        }
    )

    def try_finalize_step_attempt(
        self,
        step_attempt_id: str,
        *,
        status: str,
        finished_at: float | None = None,
    ) -> bool:
        """Atomically finalize a step attempt if it is not already terminal.

        Returns True if the update was applied, False if the attempt was already
        in a terminal state (another worker finalized it first).
        """
        with self._lock:
            attempt = self.get_step_attempt(step_attempt_id)
            if attempt is None:
                return False
            if attempt.status in self._TERMINAL_ATTEMPT_STATUSES:
                return False
            self.update_step_attempt(
                step_attempt_id,
                status=status,
                finished_at=finished_at,
            )
            return True

    def try_supersede_step_attempt(
        self,
        step_attempt_id: str,
        *,
        finished_at: float | None = None,
    ) -> bool:
        """Atomically mark a step attempt as superseded if it is not already terminal.

        Returns True if the update was applied, False if the attempt was already
        in a terminal state (another worker superseded or finalized it first).
        """
        with self._lock:
            attempt = self.get_step_attempt(step_attempt_id)
            if attempt is None:
                return False
            if attempt.status in self._TERMINAL_ATTEMPT_STATUSES:
                return False
            self.update_step_attempt(
                step_attempt_id,
                status="superseded",
                finished_at=finished_at,
            )
            return True

    def update_step_attempt(
        self,
        step_attempt_id: str,
        *,
        status: str | None = None,
        context: dict[str, Any] | object = UNSET,
        queue_priority: int | object = UNSET,
        waiting_reason: str | None | object = UNSET,
        approval_id: str | None | object = UNSET,
        decision_id: str | None | object = UNSET,
        capability_grant_id: str | None | object = UNSET,
        workspace_lease_id: str | None | object = UNSET,
        state_witness_ref: str | None | object = UNSET,
        context_pack_ref: str | None | object = UNSET,
        working_state_ref: str | None | object = UNSET,
        environment_ref: str | None | object = UNSET,
        action_request_ref: str | None | object = UNSET,
        policy_result_ref: str | None | object = UNSET,
        approval_packet_ref: str | None | object = UNSET,
        execution_contract_ref: str | None | object = UNSET,
        evidence_case_ref: str | None | object = UNSET,
        authorization_plan_ref: str | None | object = UNSET,
        reconciliation_ref: str | None | object = UNSET,
        pending_execution_ref: str | None | object = UNSET,
        idempotency_key: str | None | object = UNSET,
        executor_mode: str | None | object = UNSET,
        policy_version: str | None | object = UNSET,
        contract_version: int | object = UNSET,
        reentry_boundary: str | None | object = UNSET,
        reentry_reason: str | None | object = UNSET,
        selected_contract_template_ref: str | None | object = UNSET,
        resume_from_ref: str | None | object = UNSET,
        superseded_by_step_attempt_id: str | None | object = UNSET,
        finished_at: float | None | object = UNSET,
        status_reason: str | None | object = UNSET,
    ) -> None:
        attempt = self.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        # Resolve the effective waiting_reason: callers may pass either
        # ``waiting_reason`` (DB column name) or ``status_reason`` (model
        # field name).  ``status_reason`` takes precedence when
        # ``waiting_reason`` is not explicitly provided so that the many
        # call-sites using the model-level name actually persist the value.
        effective_waiting_reason: str | None | object
        if waiting_reason is not UNSET:
            effective_waiting_reason = waiting_reason
        elif status_reason is not UNSET:
            effective_waiting_reason = status_reason
        else:
            effective_waiting_reason = UNSET
        # ``attempt.status_reason`` holds the DB value (mapped from the
        # ``waiting_reason`` column in ``_step_attempt_from_row``).
        current_reason = attempt.status_reason
        payload = {
            "status": status or attempt.status,
            "queue_priority": sqlite_int(
                attempt.queue_priority if queue_priority is UNSET else queue_priority,
                default=int(attempt.queue_priority or 0),
            ),
            "waiting_reason": sqlite_optional_text(
                current_reason if effective_waiting_reason is UNSET else effective_waiting_reason,
                default=sqlite_optional_text(current_reason),
            ),
            "approval_id": sqlite_optional_text(
                attempt.approval_id if approval_id is UNSET else approval_id,
                default=sqlite_optional_text(attempt.approval_id),
            ),
            "decision_id": sqlite_optional_text(
                attempt.decision_id if decision_id is UNSET else decision_id,
                default=sqlite_optional_text(attempt.decision_id),
            ),
            "capability_grant_id": (
                sqlite_optional_text(
                    attempt.capability_grant_id
                    if capability_grant_id is UNSET
                    else capability_grant_id,
                    default=sqlite_optional_text(attempt.capability_grant_id),
                )
            ),
            "workspace_lease_id": (
                sqlite_optional_text(
                    attempt.workspace_lease_id
                    if workspace_lease_id is UNSET
                    else workspace_lease_id,
                    default=sqlite_optional_text(attempt.workspace_lease_id),
                )
            ),
            "state_witness_ref": sqlite_optional_text(
                attempt.state_witness_ref if state_witness_ref is UNSET else state_witness_ref,
                default=sqlite_optional_text(attempt.state_witness_ref),
            ),
            "context_pack_ref": (
                sqlite_optional_text(
                    attempt.context_pack_ref if context_pack_ref is UNSET else context_pack_ref,
                    default=sqlite_optional_text(attempt.context_pack_ref),
                )
            ),
            "working_state_ref": (
                sqlite_optional_text(
                    attempt.working_state_ref if working_state_ref is UNSET else working_state_ref,
                    default=sqlite_optional_text(attempt.working_state_ref),
                )
            ),
            "environment_ref": (
                sqlite_optional_text(
                    attempt.environment_ref if environment_ref is UNSET else environment_ref,
                    default=sqlite_optional_text(attempt.environment_ref),
                )
            ),
            "action_request_ref": (
                sqlite_optional_text(
                    attempt.action_request_ref
                    if action_request_ref is UNSET
                    else action_request_ref,
                    default=sqlite_optional_text(attempt.action_request_ref),
                )
            ),
            "policy_result_ref": (
                sqlite_optional_text(
                    attempt.policy_result_ref if policy_result_ref is UNSET else policy_result_ref,
                    default=sqlite_optional_text(attempt.policy_result_ref),
                )
            ),
            "approval_packet_ref": (
                sqlite_optional_text(
                    attempt.approval_packet_ref
                    if approval_packet_ref is UNSET
                    else approval_packet_ref,
                    default=sqlite_optional_text(attempt.approval_packet_ref),
                )
            ),
            "execution_contract_ref": (
                sqlite_optional_text(
                    attempt.execution_contract_ref
                    if execution_contract_ref is UNSET
                    else execution_contract_ref,
                    default=sqlite_optional_text(attempt.execution_contract_ref),
                )
            ),
            "evidence_case_ref": (
                sqlite_optional_text(
                    attempt.evidence_case_ref if evidence_case_ref is UNSET else evidence_case_ref,
                    default=sqlite_optional_text(attempt.evidence_case_ref),
                )
            ),
            "authorization_plan_ref": (
                sqlite_optional_text(
                    attempt.authorization_plan_ref
                    if authorization_plan_ref is UNSET
                    else authorization_plan_ref,
                    default=sqlite_optional_text(attempt.authorization_plan_ref),
                )
            ),
            "reconciliation_ref": (
                sqlite_optional_text(
                    attempt.reconciliation_ref
                    if reconciliation_ref is UNSET
                    else reconciliation_ref,
                    default=sqlite_optional_text(attempt.reconciliation_ref),
                )
            ),
            "pending_execution_ref": (
                sqlite_optional_text(
                    attempt.pending_execution_ref
                    if pending_execution_ref is UNSET
                    else pending_execution_ref,
                    default=sqlite_optional_text(attempt.pending_execution_ref),
                )
            ),
            "idempotency_key": (
                sqlite_optional_text(
                    attempt.idempotency_key if idempotency_key is UNSET else idempotency_key,
                    default=sqlite_optional_text(attempt.idempotency_key),
                )
            ),
            "executor_mode": sqlite_optional_text(
                attempt.executor_mode if executor_mode is UNSET else executor_mode,
                default=sqlite_optional_text(attempt.executor_mode),
            ),
            "policy_version": (
                sqlite_optional_text(
                    attempt.policy_version if policy_version is UNSET else policy_version,
                    default=sqlite_optional_text(attempt.policy_version),
                )
            ),
            "contract_version": (
                sqlite_int(
                    attempt.contract_version if contract_version is UNSET else contract_version,
                    default=int(attempt.contract_version or 0),
                )
            ),
            "reentry_boundary": (
                sqlite_optional_text(
                    attempt.reentry_boundary if reentry_boundary is UNSET else reentry_boundary,
                    default=sqlite_optional_text(attempt.reentry_boundary),
                )
            ),
            "reentry_reason": (
                sqlite_optional_text(
                    attempt.reentry_reason if reentry_reason is UNSET else reentry_reason,
                    default=sqlite_optional_text(attempt.reentry_reason),
                )
            ),
            "selected_contract_template_ref": (
                sqlite_optional_text(
                    attempt.selected_contract_template_ref
                    if selected_contract_template_ref is UNSET
                    else selected_contract_template_ref,
                    default=sqlite_optional_text(attempt.selected_contract_template_ref),
                )
            ),
            "resume_from_ref": (
                sqlite_optional_text(
                    attempt.resume_from_ref if resume_from_ref is UNSET else resume_from_ref,
                    default=sqlite_optional_text(attempt.resume_from_ref),
                )
            ),
            "superseded_by_step_attempt_id": (
                sqlite_optional_text(
                    attempt.superseded_by_step_attempt_id
                    if superseded_by_step_attempt_id is UNSET
                    else superseded_by_step_attempt_id,
                    default=sqlite_optional_text(attempt.superseded_by_step_attempt_id),
                )
            ),
            "finished_at": sqlite_optional_float(
                attempt.finished_at if finished_at is UNSET else finished_at,
                default=attempt.finished_at,
            ),
        }
        normalized_context = sqlite_dict(
            attempt.context if context is UNSET else context, default=dict(attempt.context or {})
        )
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE step_attempts
                SET status = ?, context_json = ?, queue_priority = ?, waiting_reason = ?, approval_id = ?, decision_id = ?, capability_grant_id = ?, workspace_lease_id = ?, state_witness_ref = ?, context_pack_ref = ?, working_state_ref = ?, environment_ref = ?, action_request_ref = ?, policy_result_ref = ?, approval_packet_ref = ?, execution_contract_ref = ?, evidence_case_ref = ?, authorization_plan_ref = ?, reconciliation_ref = ?, pending_execution_ref = ?, idempotency_key = ?, executor_mode = ?, policy_version = ?, contract_version = ?, reentry_boundary = ?, reentry_reason = ?, selected_contract_template_ref = ?, resume_from_ref = ?, superseded_by_step_attempt_id = ?, finished_at = ?
                WHERE step_attempt_id = ?
                """,
                (
                    payload["status"],
                    json.dumps(normalized_context, ensure_ascii=False),
                    payload["queue_priority"],
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
                    payload["execution_contract_ref"],
                    payload["evidence_case_ref"],
                    payload["authorization_plan_ref"],
                    payload["reconciliation_ref"],
                    payload["pending_execution_ref"],
                    payload["idempotency_key"],
                    payload["executor_mode"],
                    payload["policy_version"],
                    payload["contract_version"],
                    payload["reentry_boundary"],
                    payload["reentry_reason"],
                    payload["selected_contract_template_ref"],
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
                    "context": normalized_context,
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
        normalized_reply_to_ref = sqlite_optional_text(reply_to_ref)
        normalized_quoted_message_ref = sqlite_optional_text(quoted_message_ref)
        normalized_explicit_task_ref = sqlite_optional_text(explicit_task_ref)
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
                    sqlite_optional_text(prompt_ref),
                    normalized_reply_to_ref,
                    normalized_quoted_message_ref,
                    normalized_explicit_task_ref,
                    json.dumps(sqlite_list(referenced_artifact_refs), ensure_ascii=False),
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
                    "reply_to_ref": normalized_reply_to_ref,
                    "quoted_message_ref": normalized_quoted_message_ref,
                    "explicit_task_ref": normalized_explicit_task_ref,
                    "referenced_artifact_refs": sqlite_list(referenced_artifact_refs),
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
        clauses: list[str] = []
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
        status: str | object = UNSET,
        resolution: str | object = UNSET,
        chosen_task_id: str | None | object = UNSET,
        parent_task_id: str | None | object = UNSET,
        confidence: float | None | object = UNSET,
        margin: float | None | object = UNSET,
        rationale: dict[str, Any] | object = UNSET,
    ) -> None:
        ingress = self.get_ingress(ingress_id)
        if ingress is None:
            return
        payload = {
            "status": sqlite_optional_text(
                ingress.status if status is UNSET else status,
                default=sqlite_optional_text(ingress.status),
            )
            or ingress.status,
            "resolution": sqlite_optional_text(
                ingress.resolution if resolution is UNSET else resolution,
                default=sqlite_optional_text(ingress.resolution),
            )
            or ingress.resolution,
            "chosen_task_id": sqlite_optional_text(
                ingress.chosen_task_id if chosen_task_id is UNSET else chosen_task_id,
                default=sqlite_optional_text(ingress.chosen_task_id),
            ),
            "parent_task_id": sqlite_optional_text(
                ingress.parent_task_id if parent_task_id is UNSET else parent_task_id,
                default=sqlite_optional_text(ingress.parent_task_id),
            ),
            "confidence": sqlite_optional_float(
                ingress.confidence if confidence is UNSET else confidence,
                default=ingress.confidence,
            ),
            "margin": sqlite_optional_float(
                ingress.margin if margin is UNSET else margin,
                default=ingress.margin,
            ),
            "rationale": sqlite_dict(
                ingress.rationale if rationale is UNSET else rationale,
                default=dict(ingress.rationale or {}),
            ),
        }
        chosen_task_id_value = cast(str | None, payload["chosen_task_id"])
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
                task_id=chosen_task_id_value,
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
        event_type: str | None = None,
        after_event_seq: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("e.task_id = ?")
            params.append(task_id)
        if event_type:
            clauses.append("e.event_type = ?")
            params.append(event_type)
        if after_event_seq is not None:
            clauses.append("e.event_seq > ?")
            params.append(after_event_seq)
        if clauses:
            where = f"WHERE {' AND '.join(clauses)}"
            query = f"""
                SELECT e.*, eh.event_hash AS eh_event_hash,
                       eh.prev_event_hash AS eh_prev_event_hash,
                       eh.hash_chain_algo AS eh_hash_chain_algo
                FROM events e
                LEFT JOIN event_hashes eh ON eh.event_seq = e.event_seq
                {where}
                ORDER BY e.event_seq ASC LIMIT ?
            """
            params.append(limit)
        else:
            query = """
                SELECT e.*, eh.event_hash AS eh_event_hash,
                       eh.prev_event_hash AS eh_prev_event_hash,
                       eh.hash_chain_algo AS eh_hash_chain_algo
                FROM events e
                LEFT JOIN event_hashes eh ON eh.event_seq = e.event_seq
                ORDER BY e.event_seq DESC LIMIT ?
            """
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
                "payload": json_loads(row["payload_json"]),
                "occurred_at": float(row["occurred_at"]),
                "event_hash": row["eh_event_hash"] or row["event_hash"],
                "prev_event_hash": row["eh_prev_event_hash"]
                if row["eh_event_hash"]
                else row["prev_event_hash"],
                "hash_chain_algo": row["eh_hash_chain_algo"] or row["hash_chain_algo"],
            }
            for row in rows
        ]

    def iter_events(
        self,
        *,
        task_id: str | None = None,
        after_event_seq: int | None = None,
        batch_size: int = 200,
    ) -> Iterator[dict[str, Any]]:
        cursor = after_event_seq
        while True:
            batch = self.list_events(
                task_id=task_id,
                after_event_seq=cursor,
                limit=batch_size,
            )
            if not batch:
                break
            yield from batch
            cursor = int(batch[-1]["event_seq"])

    def count_events_by_type(
        self,
        *,
        entity_type: str,
        entity_id: str,
        event_type: str,
    ) -> int:
        """Count events matching entity_type, entity_id, and event_type."""
        with self._lock:
            row = self._row(
                """
                SELECT COUNT(*) AS cnt FROM events
                WHERE entity_type = ? AND entity_id = ? AND event_type = ?
                """,
                (entity_type, entity_id, event_type),
            )
        return int(row["cnt"]) if row is not None else 0
