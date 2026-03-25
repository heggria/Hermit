"""SelfIterateStoreMixin — adds spec_backlog and iteration_lessons tables to KernelStore."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase

_logger = logging.getLogger(__name__)

SPEC_BACKLOG_DDL = """
CREATE TABLE IF NOT EXISTS spec_backlog (
    spec_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    source TEXT NOT NULL DEFAULT 'human',
    status TEXT NOT NULL DEFAULT 'pending',
    trust_zone TEXT NOT NULL DEFAULT 'normal',
    produced_from_signal_id TEXT,
    dag_task_id TEXT,
    research_hints TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

SPEC_BACKLOG_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_spec_backlog_status ON spec_backlog(status)",
    "CREATE INDEX IF NOT EXISTS idx_spec_backlog_priority ON spec_backlog(priority)",
]

ITERATION_LESSONS_DDL = """
CREATE TABLE IF NOT EXISTS iteration_lessons (
    lesson_id TEXT PRIMARY KEY,
    iteration_id TEXT NOT NULL,
    category TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_ref TEXT,
    trigger_condition TEXT,
    resolution TEXT,
    applicable_files TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);
"""

ITERATION_LESSONS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_lessons_category ON iteration_lessons(category)",
    "CREATE INDEX IF NOT EXISTS idx_lessons_iteration ON iteration_lessons(iteration_id)",
]


def _json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_dict(row: Any) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


def _parse_json_field(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        _logger.warning(
            "Failed to parse JSON field (returning None): %s — value preview: %.120r",
            exc,
            raw,
        )
        return None


class SelfIterateStoreMixin(KernelStoreTypingBase):
    """Store methods for self-iteration spec backlog and lessons."""

    def _init_self_iterate_schema(self) -> None:
        """Call from KernelStore._init_schema to set up self-iteration tables."""
        conn = self._get_conn()
        conn.executescript(SPEC_BACKLOG_DDL)
        for idx in SPEC_BACKLOG_INDEXES:
            conn.execute(idx)
        conn.executescript(ITERATION_LESSONS_DDL)
        for idx in ITERATION_LESSONS_INDEXES:
            conn.execute(idx)
        # Migration: add columns introduced after initial DDL
        self._ensure_column("spec_backlog", "attempt", "INTEGER NOT NULL DEFAULT 1")
        self._ensure_column("spec_backlog", "error", "TEXT")
        self._ensure_column("iteration_lessons", "evidence_ref", "TEXT")

    # ------------------------------------------------------------------
    # spec_backlog CRUD
    # ------------------------------------------------------------------

    def create_spec_entry(
        self,
        spec_id: str,
        goal: str,
        priority: str = "normal",
        source: str = "human",
        trust_zone: str = "normal",
        research_hints: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        """Insert a new spec entry into the backlog. Returns the created row as a dict."""
        now = _now_iso()
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT INTO spec_backlog (
                    spec_id, goal, priority, source, status, trust_zone,
                    research_hints, metadata, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    spec_id,
                    goal,
                    priority,
                    source,
                    "pending",
                    trust_zone,
                    _json(research_hints) if research_hints is not None else None,
                    _json(metadata) if metadata is not None else None,
                    now,
                    now,
                ),
            )
        return self.get_spec_entry(spec_id)  # type: ignore[return-value]

    def get_spec_entry(self, spec_id: str) -> dict | None:
        """Retrieve a single spec entry by ID."""
        row = self._row("SELECT * FROM spec_backlog WHERE spec_id = ?", (spec_id,))
        if row is None:
            return None
        return _row_to_dict(row)

    def list_spec_backlog(
        self,
        status: str | None = None,
        source: str | None = None,
        priority: str | None = None,
        limit: int = 50,
        order_by: str = "priority,created_at",
    ) -> list[dict]:
        """List spec backlog entries with optional filtering."""
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if priority is not None:
            clauses.append("priority = ?")
            params.append(priority)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        # Sanitise order_by to prevent injection — allow only known column names.
        allowed_columns = {
            "priority",
            "created_at",
            "updated_at",
            "status",
            "source",
        }
        order_parts = []
        for part in order_by.split(","):
            col = part.strip()
            if col in allowed_columns:
                order_parts.append(col)
        order_clause = ", ".join(order_parts) if order_parts else "created_at"
        sql = f"SELECT * FROM spec_backlog{where} ORDER BY {order_clause} LIMIT ?"
        params.append(limit)
        rows = self._rows(sql, params)
        return [_row_to_dict(r) for r in rows]

    def update_spec_status(
        self,
        spec_id: str,
        status: str,
        *,
        expected_status: str | None = None,
        **updates: Any,
    ) -> bool:
        """Update the status (and optional extra fields) of a spec entry.

        Allowed extra fields: priority, dag_task_id, produced_from_signal_id, metadata.
        When *expected_status* is provided the UPDATE only fires if the current
        status matches (conditional/idempotent write).
        Returns True if a row was actually updated.
        """
        allowed_extra = {
            "priority",
            "dag_task_id",
            "produced_from_signal_id",
            "metadata",
            "error",
        }
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, _now_iso()]
        for key, value in updates.items():
            if key not in allowed_extra:
                continue
            if key == "metadata" and not isinstance(value, str):
                value = _json(value)
            sets.append(f"{key} = ?")
            params.append(value)
        where = "spec_id = ?"
        params.append(spec_id)
        if expected_status is not None:
            where += " AND status = ?"
            params.append(expected_status)
        conn = self._get_conn()
        with conn:
            cursor = conn.execute(
                f"UPDATE spec_backlog SET {', '.join(sets)} WHERE {where}",
                params,
            )
        return cursor.rowcount > 0

    def remove_spec_entry(self, spec_id: str) -> bool:
        """Delete a spec entry. Returns True if a row was deleted."""
        conn = self._get_conn()
        with conn:
            cursor = conn.execute("DELETE FROM spec_backlog WHERE spec_id = ?", (spec_id,))
        return cursor.rowcount > 0

    def get_spec_by_dag_task_id(self, task_id: str) -> dict | None:
        """Look up a spec entry by its associated DAG task ID."""
        row = self._row("SELECT * FROM spec_backlog WHERE dag_task_id = ?", (task_id,))
        if row is None:
            return None
        return _row_to_dict(row)

    def count_specs_by_status(self, status: str) -> int:
        """Return the count of spec entries with the given status."""
        row = self._row("SELECT COUNT(*) AS cnt FROM spec_backlog WHERE status = ?", (status,))
        return int(row["cnt"]) if row else 0

    def count_active_specs(self) -> int:
        """Return the count of spec entries NOT in a terminal state."""
        row = self._row(
            "SELECT COUNT(*) AS cnt FROM spec_backlog "
            "WHERE status NOT IN ('completed', 'failed', 'accepted', 'rejected')",
            (),
        )
        return int(row["cnt"]) if row else 0

    def find_spec_by_goal_hash(self, goal_hash: str) -> dict | None:
        """Look up an active spec entry by its goal_hash stored in metadata."""
        row = self._row(
            "SELECT * FROM spec_backlog "
            "WHERE json_extract(metadata, '$.goal_hash') = ? "
            "AND status NOT IN ('completed', 'failed', 'accepted', 'rejected') "
            "LIMIT 1",
            (goal_hash,),
        )
        if row is None:
            return None
        return _row_to_dict(row)

    def claim_next_spec(
        self,
        from_status: str = "pending",
        to_status: str = "researching",
    ) -> dict | None:
        """Atomically claim the next spec entry using a CTE.

        Selects the highest-priority pending spec (ordered by priority, then
        created_at) that currently has *from_status*, flips it to *to_status*,
        and returns the updated row.  If no matching row exists, returns None.

        Specs with a ``next_retry_at`` metadata field set to a future timestamp
        are skipped (exponential backoff after failure).
        """
        import time as _time

        now = _now_iso()
        now_epoch = _time.time()
        conn = self._get_conn()
        with conn:
            row = conn.execute(
                """
                WITH next_spec AS (
                    SELECT spec_id
                    FROM spec_backlog
                    WHERE status = ?
                      AND (
                          json_extract(metadata, '$.next_retry_at') IS NULL
                          OR CAST(json_extract(metadata, '$.next_retry_at') AS REAL) < ?
                      )
                    ORDER BY priority, created_at
                    LIMIT 1
                )
                UPDATE spec_backlog
                SET status = ?, updated_at = ?
                WHERE spec_id = (SELECT spec_id FROM next_spec)
                RETURNING *
                """,
                (from_status, now_epoch, to_status, now),
            ).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    # ------------------------------------------------------------------
    # iteration_lessons CRUD
    # ------------------------------------------------------------------

    def create_lesson(
        self,
        lesson_id: str,
        iteration_id: str,
        category: str,
        summary: str,
        trigger_condition: str | None = None,
        resolution: str | None = None,
        applicable_files: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        evidence_ref: str | None = None,
    ) -> dict:
        """Insert a new iteration lesson. Returns the created row as a dict."""
        now = _now_iso()
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT INTO iteration_lessons (
                    lesson_id, iteration_id, category, summary,
                    evidence_ref, trigger_condition, resolution,
                    applicable_files, metadata, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    lesson_id,
                    iteration_id,
                    category,
                    summary,
                    evidence_ref,
                    trigger_condition,
                    resolution,
                    _json(applicable_files) if applicable_files is not None else None,
                    _json(metadata) if metadata is not None else None,
                    now,
                ),
            )
        return self.get_lesson(lesson_id)  # type: ignore[return-value]

    def get_lesson(self, lesson_id: str) -> dict | None:
        """Retrieve a single lesson by ID."""
        row = self._row("SELECT * FROM iteration_lessons WHERE lesson_id = ?", (lesson_id,))
        if row is None:
            return None
        return _row_to_dict(row)

    def list_lessons(
        self,
        categories: list[str] | None = None,
        applicable_to: str | None = None,
        iteration_ids: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """List iteration lessons with optional filtering.

        *categories*: filter by one or more category values.
        *applicable_to*: filter lessons whose applicable_files JSON contains this path.
        *iteration_ids*: filter by one or more iteration IDs.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if categories:
            placeholders = ",".join("?" for _ in categories)
            clauses.append(f"category IN ({placeholders})")
            params.extend(categories)
        if applicable_to is not None:
            # JSON text contains the file path (simple LIKE match on serialised JSON).
            clauses.append("applicable_files LIKE ?")
            params.append(f"%{applicable_to}%")
        if iteration_ids:
            placeholders = ",".join("?" for _ in iteration_ids)
            clauses.append(f"iteration_id IN ({placeholders})")
            params.extend(iteration_ids)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM iteration_lessons{where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._rows(sql, params)
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Additional spec_backlog helpers
    # ------------------------------------------------------------------

    def reprioritize_spec_entry(self, spec_id: str, priority: str) -> bool:
        """Update the priority of a spec entry. Returns True if updated."""
        conn = self._get_conn()
        with conn:
            cursor = conn.execute(
                "UPDATE spec_backlog SET priority = ?, updated_at = ? WHERE spec_id = ?",
                (priority, _now_iso(), spec_id),
            )
        return cursor.rowcount > 0

    def increment_spec_attempt(self, spec_id: str) -> bool:
        """Increment the attempt counter for a spec entry. Returns True if updated."""
        conn = self._get_conn()
        with conn:
            cursor = conn.execute(
                "UPDATE spec_backlog SET attempt = attempt + 1, updated_at = ? WHERE spec_id = ?",
                (_now_iso(), spec_id),
            )
        return cursor.rowcount > 0

    def list_benchmark_results(
        self,
        iteration_ids: list[str] | None = None,
        spec_ids: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Return benchmark data stored in spec_backlog metadata JSON."""
        clauses: list[str] = []
        params: list[Any] = []
        # Only rows whose metadata contains benchmark data
        clauses.append("json_extract(metadata, '$.benchmark') IS NOT NULL")
        if iteration_ids:
            placeholders = ",".join("?" for _ in iteration_ids)
            clauses.append(f"json_extract(metadata, '$.iteration_id') IN ({placeholders})")
            params.extend(iteration_ids)
        if spec_ids:
            placeholders = ",".join("?" for _ in spec_ids)
            clauses.append(f"spec_id IN ({placeholders})")
            params.extend(spec_ids)
        where = " WHERE " + " AND ".join(clauses)
        sql = f"SELECT * FROM spec_backlog{where} ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._rows(sql, params)
        results: list[dict] = []
        for r in rows:
            d = _row_to_dict(r)
            d["benchmark"] = _parse_json_field(d.get("metadata"))
            if isinstance(d["benchmark"], dict):
                d["benchmark"] = d["benchmark"].get("benchmark")
            results.append(d)
        return results

    def list_lessons_learned(
        self,
        applicable_to: str | None = None,
        categories: list[str] | None = None,
        iteration_ids: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Alias for list_lessons with reordered parameters."""
        return self.list_lessons(
            categories=categories,
            applicable_to=applicable_to,
            iteration_ids=iteration_ids,
            limit=limit,
        )

    def get_iteration_findings(self, spec_id: str) -> dict | None:
        """Return the findings stored in a spec entry's metadata JSON."""
        entry = self.get_spec_entry(spec_id)
        if entry is None:
            return None
        meta = _parse_json_field(entry.get("metadata"))
        if not isinstance(meta, dict):
            return None
        return meta.get("findings")
