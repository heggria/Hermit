"""CompetitionStoreMixin — adds competition and candidate tables to KernelStore."""

from __future__ import annotations

import json
import time
from typing import Any

from hermit.kernel.execution.competition.models import CandidateRecord, CompetitionRecord
from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase

COMPETITION_DDL = """
CREATE TABLE IF NOT EXISTS competitions (
    competition_id TEXT PRIMARY KEY,
    parent_task_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    candidate_count INTEGER NOT NULL DEFAULT 2,
    status TEXT NOT NULL DEFAULT 'draft',
    winner_task_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    decided_at REAL
);
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    worktree_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    score REAL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
"""

COMPETITION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_competitions_parent ON competitions(parent_task_id)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_competition ON candidates(competition_id)",
    "CREATE INDEX IF NOT EXISTS idx_candidates_task ON candidates(task_id)",
]


def _json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


class CompetitionStoreMixin(KernelStoreTypingBase):
    """Mixin providing competition persistence."""

    def _init_competition_schema(self) -> None:
        conn = self._conn
        conn.executescript(COMPETITION_DDL)
        for idx in COMPETITION_INDEXES:
            conn.execute(idx)

    def create_competition(self, comp: CompetitionRecord) -> CompetitionRecord:
        lock = self._lock
        conn = self._conn
        with lock, conn:
            conn.execute(
                """INSERT INTO competitions (
                    competition_id, parent_task_id, conversation_id, goal,
                    candidate_count, status, winner_task_id, metadata_json,
                    created_at, decided_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    comp.competition_id,
                    comp.parent_task_id,
                    comp.conversation_id,
                    comp.goal,
                    comp.candidate_count,
                    comp.status,
                    comp.winner_task_id,
                    _json(comp.metadata),
                    comp.created_at,
                    comp.decided_at,
                ),
            )
        return comp

    def get_competition(self, competition_id: str) -> CompetitionRecord | None:
        lock = self._lock
        conn = self._conn
        with lock:
            row = conn.execute(
                "SELECT * FROM competitions WHERE competition_id = ?",
                (competition_id,),
            ).fetchone()
        if row is None:
            return None
        return self._competition_from_row(row)

    def find_competition_by_parent_task(self, task_id: str) -> CompetitionRecord | None:
        lock = self._lock
        conn = self._conn
        with lock:
            row = conn.execute(
                "SELECT * FROM competitions WHERE parent_task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._competition_from_row(row)

    def update_competition_status(
        self,
        competition_id: str,
        status: str,
        winner_task_id: str | None = None,
    ) -> None:
        lock = self._lock
        conn = self._conn
        with lock, conn:
            decided_at = time.time() if status in ("decided", "cancelled") else None
            conn.execute(
                "UPDATE competitions SET status = ?, winner_task_id = ?, decided_at = ? "
                "WHERE competition_id = ?",
                (status, winner_task_id, decided_at, competition_id),
            )

    def create_candidate(self, candidate: CandidateRecord) -> CandidateRecord:
        lock = self._lock
        conn = self._conn
        with lock, conn:
            conn.execute(
                """INSERT INTO candidates (
                    candidate_id, competition_id, task_id, worktree_path,
                    status, score, metadata_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    candidate.candidate_id,
                    candidate.competition_id,
                    candidate.task_id,
                    candidate.worktree_path,
                    candidate.status,
                    candidate.score,
                    _json(candidate.metadata),
                    candidate.created_at,
                ),
            )
        return candidate

    def list_candidates(self, competition_id: str) -> list[CandidateRecord]:
        lock = self._lock
        conn = self._conn
        with lock:
            rows = conn.execute(
                "SELECT * FROM candidates WHERE competition_id = ? ORDER BY created_at ASC",
                (competition_id,),
            ).fetchall()
        return [self._candidate_from_row(r) for r in rows]

    def find_candidate_by_task(self, task_id: str) -> CandidateRecord | None:
        lock = self._lock
        conn = self._conn
        with lock:
            row = conn.execute(
                "SELECT * FROM candidates WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._candidate_from_row(row)

    @staticmethod
    def _competition_from_row(row: Any) -> CompetitionRecord:
        return CompetitionRecord(
            competition_id=str(row["competition_id"]),
            parent_task_id=str(row["parent_task_id"]),
            conversation_id=str(row["conversation_id"]),
            goal=str(row["goal"]),
            candidate_count=int(row["candidate_count"]),
            status=str(row["status"]),
            winner_task_id=row["winner_task_id"],
            metadata=json.loads(row["metadata_json"]),
            created_at=float(row["created_at"]),
            decided_at=float(row["decided_at"]) if row["decided_at"] is not None else None,
        )

    @staticmethod
    def _candidate_from_row(row: Any) -> CandidateRecord:
        return CandidateRecord(
            candidate_id=str(row["candidate_id"]),
            competition_id=str(row["competition_id"]),
            task_id=str(row["task_id"]),
            worktree_path=row["worktree_path"],
            status=str(row["status"]),
            score=float(row["score"]) if row["score"] is not None else None,
            metadata=json.loads(row["metadata_json"]),
            created_at=float(row["created_at"]),
        )
