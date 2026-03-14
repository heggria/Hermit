from __future__ import annotations

import json
import time
from typing import Any

from hermit.kernel.store_support import _json_loads


class KernelProjectionStoreMixin:
    def build_task_projection(self, task_id: str) -> dict[str, Any]:
        rows = self._rows(
            "SELECT * FROM events WHERE task_id = ? ORDER BY event_seq ASC",
            (task_id,),
        )
        projection: dict[str, Any] = {
            "task": None,
            "steps": {},
            "step_attempts": {},
            "approvals": {},
            "decisions": {},
            "permits": {},
            "receipts": {},
            "beliefs": {},
            "memory_records": {},
            "rollbacks": {},
            "events_processed": 0,
            "last_event_seq": None,
        }
        entity_maps = {
            "step": projection["steps"],
            "step_attempt": projection["step_attempts"],
            "approval": projection["approvals"],
            "decision": projection["decisions"],
            "execution_permit": projection["permits"],
            "receipt": projection["receipts"],
            "belief": projection["beliefs"],
            "memory_record": projection["memory_records"],
            "rollback": projection["rollbacks"],
        }

        for row in rows:
            event_type = str(row["event_type"])
            entity_type = str(row["entity_type"])
            entity_id = str(row["entity_id"])
            payload = _json_loads(row["payload_json"])
            occurred_at = float(row["occurred_at"])

            projection["events_processed"] += 1
            projection["last_event_seq"] = int(row["event_seq"])

            if entity_type == "task":
                current = dict(projection["task"] or {})
                current.update(payload)
                current["task_id"] = entity_id
                if event_type.startswith("task.") and event_type != "task.created":
                    current["status"] = payload.get("status", event_type.split(".", 1)[1])
                current["last_event_type"] = event_type
                current["last_event_at"] = occurred_at
                projection["task"] = current
                continue

            target = entity_maps.get(entity_type)
            if target is None:
                continue

            current = dict(target.get(entity_id, {}))
            current.update(payload)
            current.setdefault("task_id", row["task_id"])
            if row["step_id"] is not None:
                current.setdefault("step_id", row["step_id"])
            current[f"{entity_type}_id"] = entity_id
            if "status" not in current and "." in event_type:
                current["status"] = event_type.split(".", 1)[1]
            current["last_event_type"] = event_type
            current["last_event_at"] = occurred_at
            target[entity_id] = current

        return projection

    def get_projection_cache(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._row("SELECT * FROM projection_cache WHERE task_id = ?", (task_id,))
        if row is None:
            return None
        return {
            "task_id": str(row["task_id"]),
            "schema_version": str(row["schema_version"]),
            "event_head_hash": row["event_head_hash"],
            "payload": _json_loads(row["payload_json"]),
            "built_at": float(row["built_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def get_conversation_projection_cache(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._row(
                "SELECT * FROM conversation_projection_cache WHERE conversation_id = ?",
                (conversation_id,),
            )
        if row is None:
            return None
        return {
            "conversation_id": str(row["conversation_id"]),
            "schema_version": str(row["schema_version"]),
            "event_head_hash": row["event_head_hash"],
            "payload": _json_loads(row["payload_json"]),
            "built_at": float(row["built_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def upsert_projection_cache(
        self,
        task_id: str,
        *,
        schema_version: str,
        event_head_hash: str | None,
        payload: dict[str, Any],
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO projection_cache (task_id, schema_version, event_head_hash, payload_json, built_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    event_head_hash = excluded.event_head_hash,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (task_id, schema_version, event_head_hash, json.dumps(payload, ensure_ascii=False), now, now),
            )

    def upsert_conversation_projection_cache(
        self,
        conversation_id: str,
        *,
        schema_version: str,
        event_head_hash: str | None,
        payload: dict[str, Any],
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO conversation_projection_cache (
                    conversation_id, schema_version, event_head_hash, payload_json, built_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    event_head_hash = excluded.event_head_hash,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    conversation_id,
                    schema_version,
                    event_head_hash,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    def list_projection_cache_tasks(self) -> list[str]:
        with self._lock:
            rows = self._rows("SELECT task_id FROM projection_cache ORDER BY updated_at DESC")
        return [str(row["task_id"]) for row in rows]

    def list_conversation_projection_cache(self) -> list[str]:
        with self._lock:
            rows = self._rows(
                "SELECT conversation_id FROM conversation_projection_cache ORDER BY updated_at DESC"
            )
        return [str(row["conversation_id"]) for row in rows]
