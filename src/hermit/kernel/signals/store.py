"""SignalStoreMixin — adds evidence_signals table and query methods to KernelStore."""

from __future__ import annotations

import json
import time
from typing import Any

from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase
from hermit.kernel.signals.models import EvidenceSignal, SteeringDirective

SIGNAL_DDL = """
CREATE TABLE IF NOT EXISTS evidence_signals (
    signal_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    conversation_id TEXT,
    task_id TEXT,
    summary TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    suggested_goal TEXT NOT NULL DEFAULT '',
    suggested_policy_profile TEXT NOT NULL DEFAULT 'default',
    risk_level TEXT NOT NULL DEFAULT 'low',
    disposition TEXT NOT NULL DEFAULT 'pending',
    cooldown_key TEXT NOT NULL DEFAULT '',
    cooldown_seconds INTEGER NOT NULL DEFAULT 86400,
    produced_task_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    expires_at REAL,
    acted_at REAL
);
"""

SIGNAL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_evidence_signals_disposition "
    "ON evidence_signals(disposition, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_signals_cooldown "
    "ON evidence_signals(cooldown_key, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_signals_source "
    "ON evidence_signals(source_kind, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_signals_task "
    "ON evidence_signals(task_id, source_kind)",
]


def _json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


class SignalStoreMixin(KernelStoreTypingBase):
    """Mixin providing evidence signal persistence."""

    def _init_signal_schema(self) -> None:
        """Call from KernelStore._init_schema to set up the signals table."""
        conn = self._conn
        conn.executescript(SIGNAL_DDL)
        for idx in SIGNAL_INDEXES:
            conn.execute(idx)

    # ── Signal CRUD ──

    def create_signal(self, signal: EvidenceSignal) -> EvidenceSignal:
        lock = self._lock
        conn = self._conn
        with lock, conn:
            conn.execute(
                """INSERT INTO evidence_signals (
                    signal_id, source_kind, source_ref, conversation_id, task_id,
                    summary, confidence, evidence_refs_json, suggested_goal,
                    suggested_policy_profile, risk_level, disposition, cooldown_key,
                    cooldown_seconds, produced_task_id, metadata_json,
                    created_at, expires_at, acted_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    signal.signal_id,
                    signal.source_kind,
                    signal.source_ref,
                    signal.conversation_id,
                    signal.task_id,
                    signal.summary,
                    signal.confidence,
                    _json(signal.evidence_refs),
                    signal.suggested_goal,
                    signal.suggested_policy_profile,
                    signal.risk_level,
                    signal.disposition,
                    signal.cooldown_key,
                    signal.cooldown_seconds,
                    signal.produced_task_id,
                    _json(signal.metadata),
                    signal.created_at,
                    signal.expires_at,
                    signal.acted_at,
                ),
            )
        return signal

    def get_signal(self, signal_id: str) -> EvidenceSignal | None:
        lock = self._lock
        conn = self._conn
        with lock:
            row = conn.execute(
                "SELECT * FROM evidence_signals WHERE signal_id = ?", (signal_id,)
            ).fetchone()
        if row is None:
            return None
        return self._signal_from_row(row)

    def update_signal_disposition(
        self,
        signal_id: str,
        disposition: str,
        *,
        acted_at: float | None = None,
        produced_task_id: str | None = None,
    ) -> None:
        lock = self._lock
        conn = self._conn
        with lock, conn:
            if acted_at is not None:
                conn.execute(
                    "UPDATE evidence_signals SET disposition = ?, acted_at = ? WHERE signal_id = ?",
                    (disposition, acted_at, signal_id),
                )
            else:
                conn.execute(
                    "UPDATE evidence_signals SET disposition = ? WHERE signal_id = ?",
                    (disposition, signal_id),
                )
            if produced_task_id is not None:
                conn.execute(
                    "UPDATE evidence_signals SET produced_task_id = ? WHERE signal_id = ?",
                    (produced_task_id, signal_id),
                )

    # ── Query helpers ──

    def check_cooldown(self, cooldown_key: str, cooldown_seconds: int) -> bool:
        """Return True if a signal with this cooldown_key was created within cooldown_seconds."""
        since = time.time() - cooldown_seconds
        lock = self._lock
        conn = self._conn
        with lock:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM evidence_signals "
                "WHERE cooldown_key = ? AND created_at >= ?",
                (cooldown_key, since),
            ).fetchone()
        return bool(row and int(row["cnt"]) > 0)

    def actionable_signals(self, limit: int = 50) -> list[EvidenceSignal]:
        now = time.time()
        lock = self._lock
        conn = self._conn
        with lock:
            rows = conn.execute(
                "SELECT * FROM evidence_signals "
                "WHERE disposition = 'pending' AND (expires_at IS NULL OR expires_at > ?) "
                "AND source_kind NOT LIKE 'steering:%' "
                "ORDER BY created_at ASC LIMIT ?",
                (now, limit),
            ).fetchall()
        return [self._signal_from_row(r) for r in rows]

    def signal_stats(self, since: float | None = None) -> dict[str, int]:
        lock = self._lock
        conn = self._conn
        with lock:
            if since is not None:
                rows = conn.execute(
                    "SELECT disposition, COUNT(*) as cnt FROM evidence_signals "
                    "WHERE created_at >= ? GROUP BY disposition",
                    (since,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT disposition, COUNT(*) as cnt FROM evidence_signals GROUP BY disposition"
                ).fetchall()
        return {str(r["disposition"]): int(r["cnt"]) for r in rows}

    def list_signals(self, limit: int = 50) -> list[EvidenceSignal]:
        """Return the most recent signals, newest first."""
        lock = self._lock
        conn = self._conn
        with lock:
            rows = conn.execute(
                "SELECT * FROM evidence_signals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._signal_from_row(r) for r in rows]

    # ── Steering convenience ──

    def create_steering(self, directive: SteeringDirective) -> SteeringDirective:
        self.create_signal(directive.to_signal())
        return directive

    def list_steerings_for_task(
        self,
        task_id: str,
        disposition: str | None = None,
        limit: int = 50,
    ) -> list[SteeringDirective]:
        lock = self._lock
        conn = self._conn
        with lock:
            if disposition is not None:
                rows = conn.execute(
                    "SELECT * FROM evidence_signals "
                    "WHERE task_id = ? AND source_kind LIKE 'steering:%' "
                    "AND disposition = ? ORDER BY created_at ASC LIMIT ?",
                    (task_id, disposition, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM evidence_signals "
                    "WHERE task_id = ? AND source_kind LIKE 'steering:%' "
                    "ORDER BY created_at ASC LIMIT ?",
                    (task_id, limit),
                ).fetchall()
        return [SteeringDirective.from_signal(self._signal_from_row(r)) for r in rows]

    def active_steerings_for_task(self, task_id: str) -> list[SteeringDirective]:
        lock = self._lock
        conn = self._conn
        with lock:
            rows = conn.execute(
                "SELECT * FROM evidence_signals "
                "WHERE task_id = ? AND source_kind LIKE 'steering:%' "
                "AND disposition IN ('pending', 'acknowledged', 'applied') "
                "ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [SteeringDirective.from_signal(self._signal_from_row(r)) for r in rows]

    def update_steering_disposition(
        self,
        directive_id: str,
        disposition: str,
        applied_at: float | None = None,
    ) -> None:
        lock = self._lock
        conn = self._conn
        with lock, conn:
            conn.execute(
                "UPDATE evidence_signals SET disposition = ? WHERE signal_id = ?",
                (disposition, directive_id),
            )
            if applied_at is not None:
                row = conn.execute(
                    "SELECT metadata_json FROM evidence_signals WHERE signal_id = ?",
                    (directive_id,),
                ).fetchone()
                if row:
                    meta = json.loads(str(row["metadata_json"]))
                    meta["applied_at"] = applied_at
                    conn.execute(
                        "UPDATE evidence_signals SET metadata_json = ? WHERE signal_id = ?",
                        (_json(meta), directive_id),
                    )

    # ── Internal ──

    @staticmethod
    def _signal_from_row(row: Any) -> EvidenceSignal:
        return EvidenceSignal(
            signal_id=str(row["signal_id"]),
            source_kind=str(row["source_kind"]),
            source_ref=str(row["source_ref"]),
            conversation_id=row["conversation_id"],
            task_id=row["task_id"],
            summary=str(row["summary"]),
            confidence=float(row["confidence"]),
            evidence_refs=json.loads(row["evidence_refs_json"]),
            suggested_goal=str(row["suggested_goal"]),
            suggested_policy_profile=str(row["suggested_policy_profile"]),
            risk_level=str(row["risk_level"]),
            disposition=str(row["disposition"]),
            cooldown_key=str(row["cooldown_key"]),
            cooldown_seconds=int(row["cooldown_seconds"]),
            produced_task_id=row["produced_task_id"],
            metadata=json.loads(row["metadata_json"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]) if row["expires_at"] is not None else None,
            acted_at=float(row["acted_at"]) if row["acted_at"] is not None else None,
        )
