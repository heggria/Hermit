"""AssuranceStoreMixin — adds assurance trace, scenario, report, and replay tables to KernelStore."""

from __future__ import annotations

import time
from typing import Any

from hermit.kernel.ledger.journal.store_support import canonical_json
from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase

ASSURANCE_DDL = """
CREATE TABLE IF NOT EXISTS assurance_trace_envelopes (
    trace_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    scenario_id TEXT,
    task_id TEXT NOT NULL,
    event_seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    wallclock_at REAL NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS assurance_scenarios (
    scenario_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    spec_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS assurance_reports (
    report_id TEXT PRIMARY KEY,
    scenario_id TEXT,
    run_id TEXT,
    status TEXT NOT NULL,
    verdict TEXT,
    report_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS assurance_replay_entries (
    entry_id TEXT PRIMARY KEY,
    scenario_id TEXT,
    run_id TEXT,
    event_head_hash TEXT,
    source TEXT DEFAULT 'live',
    sanitized INTEGER DEFAULT 0,
    entry_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

ASSURANCE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_assurance_trace_run ON assurance_trace_envelopes(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_assurance_trace_task ON assurance_trace_envelopes(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_assurance_trace_seq ON assurance_trace_envelopes(run_id, event_seq)",
    "CREATE INDEX IF NOT EXISTS idx_assurance_report_run ON assurance_reports(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_assurance_replay_scenario ON assurance_replay_entries(scenario_id)",
]


def _row_to_dict(row: Any) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


class AssuranceStoreMixin(KernelStoreTypingBase):
    """Store methods for assurance trace envelopes, scenarios, reports, and replay entries."""

    def _init_assurance_schema(self) -> None:
        """Call from KernelStore._init_schema to set up assurance tables."""
        conn = self._get_conn()
        conn.executescript(ASSURANCE_DDL)
        for idx in ASSURANCE_INDEXES:
            conn.execute(idx)

    # ------------------------------------------------------------------
    # assurance_trace_envelopes CRUD
    # ------------------------------------------------------------------

    def create_trace_envelope(
        self,
        trace_id: str,
        run_id: str,
        task_id: str,
        event_seq: int,
        event_type: str,
        envelope_json: str | dict[str, Any],
        wallclock_at: float,
        *,
        scenario_id: str | None = None,
    ) -> dict:
        """Insert a trace envelope record. Returns the created row as a dict."""
        now = time.time()
        serialized = canonical_json(envelope_json) if isinstance(envelope_json, dict) else envelope_json
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT INTO assurance_trace_envelopes (
                    trace_id, run_id, scenario_id, task_id, event_seq,
                    event_type, envelope_json, wallclock_at, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    trace_id,
                    run_id,
                    scenario_id,
                    task_id,
                    event_seq,
                    event_type,
                    serialized,
                    wallclock_at,
                    now,
                ),
            )
        row = self._row(
            "SELECT * FROM assurance_trace_envelopes WHERE trace_id = ?",
            (trace_id,),
        )
        assert row is not None
        return _row_to_dict(row)

    def get_trace_envelopes(
        self,
        run_id: str,
        *,
        task_id: str | None = None,
        event_type: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Retrieve trace envelopes for a run, optionally filtered by task_id and event_type."""
        clauses = ["run_id = ?"]
        params: list[Any] = [run_id]
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = " AND ".join(clauses)
        sql = f"SELECT * FROM assurance_trace_envelopes WHERE {where} ORDER BY event_seq ASC LIMIT ?"
        params.append(limit)
        rows = self._rows(sql, params)
        return [_row_to_dict(r) for r in rows]

    def get_trace_envelopes_by_task(
        self,
        task_id: str,
        *,
        event_type: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Retrieve trace envelopes for a task_id across all runs."""
        clauses = ["task_id = ?"]
        params: list[Any] = [task_id]
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        where = " AND ".join(clauses)
        sql = f"SELECT * FROM assurance_trace_envelopes WHERE {where} ORDER BY event_seq ASC LIMIT ?"
        params.append(limit)
        rows = self._rows(sql, params)
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # assurance_scenarios CRUD
    # ------------------------------------------------------------------

    def create_scenario(
        self,
        scenario_id: str,
        schema_version: int,
        spec_json: str | dict[str, Any],
    ) -> dict:
        """Insert a scenario record. Returns the created row as a dict."""
        now = time.time()
        serialized = canonical_json(spec_json) if isinstance(spec_json, dict) else spec_json
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT INTO assurance_scenarios (
                    scenario_id, schema_version, spec_json, created_at, updated_at
                ) VALUES (?,?,?,?,?)""",
                (scenario_id, schema_version, serialized, now, now),
            )
        row = self._row(
            "SELECT * FROM assurance_scenarios WHERE scenario_id = ?",
            (scenario_id,),
        )
        assert row is not None
        return _row_to_dict(row)

    def get_scenario(self, scenario_id: str) -> dict | None:
        """Retrieve a single scenario by ID."""
        row = self._row(
            "SELECT * FROM assurance_scenarios WHERE scenario_id = ?",
            (scenario_id,),
        )
        if row is None:
            return None
        return _row_to_dict(row)

    # ------------------------------------------------------------------
    # assurance_reports CRUD
    # ------------------------------------------------------------------

    def create_report(
        self,
        report_id: str,
        scenario_id: str | None,
        run_id: str | None,
        status: str,
        verdict: str | None,
        report_json: str | dict[str, Any],
    ) -> dict:
        """Insert a report record. Returns the created row as a dict."""
        now = time.time()
        serialized = canonical_json(report_json) if isinstance(report_json, dict) else report_json
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT INTO assurance_reports (
                    report_id, scenario_id, run_id, status, verdict,
                    report_json, created_at
                ) VALUES (?,?,?,?,?,?,?)""",
                (report_id, scenario_id, run_id, status, verdict, serialized, now),
            )
        row = self._row(
            "SELECT * FROM assurance_reports WHERE report_id = ?",
            (report_id,),
        )
        assert row is not None
        return _row_to_dict(row)

    def get_report(self, report_id: str) -> dict | None:
        """Retrieve a single report by ID."""
        row = self._row(
            "SELECT * FROM assurance_reports WHERE report_id = ?",
            (report_id,),
        )
        if row is None:
            return None
        return _row_to_dict(row)

    def list_reports(
        self,
        *,
        scenario_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List reports, optionally filtered by scenario_id and/or run_id."""
        clauses: list[str] = []
        params: list[Any] = []
        if scenario_id is not None:
            clauses.append("scenario_id = ?")
            params.append(scenario_id)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM assurance_reports{where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._rows(sql, params)
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # assurance_replay_entries CRUD
    # ------------------------------------------------------------------

    def create_replay_entry(
        self,
        entry_id: str,
        scenario_id: str | None,
        run_id: str | None,
        event_head_hash: str | None,
        source: str,
        sanitized: bool,
        entry_json: str | dict[str, Any],
    ) -> dict:
        """Insert a replay entry. Returns the created row as a dict."""
        now = time.time()
        serialized = canonical_json(entry_json) if isinstance(entry_json, dict) else entry_json
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT INTO assurance_replay_entries (
                    entry_id, scenario_id, run_id, event_head_hash,
                    source, sanitized, entry_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    entry_id,
                    scenario_id,
                    run_id,
                    event_head_hash,
                    source,
                    1 if sanitized else 0,
                    serialized,
                    now,
                ),
            )
        row = self._row(
            "SELECT * FROM assurance_replay_entries WHERE entry_id = ?",
            (entry_id,),
        )
        assert row is not None
        return _row_to_dict(row)

    def list_replay_entries(
        self,
        scenario_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List replay entries, optionally filtered by scenario_id."""
        clauses: list[str] = []
        params: list[Any] = []
        if scenario_id is not None:
            clauses.append("scenario_id = ?")
            params.append(scenario_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM assurance_replay_entries{where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._rows(sql, params)
        return [_row_to_dict(r) for r in rows]
