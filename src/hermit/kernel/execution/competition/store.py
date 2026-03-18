from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from hermit.kernel.execution.competition.models import (
    CANDIDATE_TRANSITIONS,
    COMPETITION_TRANSITIONS,
    CompetitionCandidateRecord,
    CompetitionRecord,
    validate_transition,
)
from hermit.kernel.ledger.journal.store_support import json_loads
from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase


class CompetitionStoreMixin(KernelStoreTypingBase):
    """Mixin providing competition-related persistence operations."""

    # -- Competition CRUD -----------------------------------------------------

    def create_competition(
        self,
        *,
        parent_task_id: str,
        goal: str,
        candidate_count: int,
        min_candidates: int = 1,
        strategy: str = "parallel_tasks",
        evaluation_criteria: dict[str, Any] | None = None,
        scoring_weights: dict[str, Any] | None = None,
        timeout_policy: str = "evaluate_completed",
        timeout_seconds: float | None = None,
    ) -> CompetitionRecord:
        competition_id = self._id("comp")
        now = time.time()
        criteria_json = json.dumps(evaluation_criteria or {}, ensure_ascii=False)
        weights_json = json.dumps(scoring_weights or {}, ensure_ascii=False)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO competitions (
                    competition_id, parent_task_id, goal, strategy,
                    candidate_count, min_candidates,
                    evaluation_criteria_json, scoring_weights_json,
                    status, timeout_policy, timeout_seconds,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)
                """,
                (
                    competition_id,
                    parent_task_id,
                    goal,
                    strategy,
                    candidate_count,
                    min_candidates,
                    criteria_json,
                    weights_json,
                    timeout_policy,
                    timeout_seconds,
                    now,
                    now,
                ),
            )
            row = self._row(
                "SELECT * FROM competitions WHERE competition_id = ?",
                (competition_id,),
            )
        assert row is not None
        return self._competition_from_row(row)

    def get_competition(self, competition_id: str) -> CompetitionRecord | None:
        with self._lock:
            row = self._row(
                "SELECT * FROM competitions WHERE competition_id = ?",
                (competition_id,),
            )
        return self._competition_from_row(row) if row is not None else None

    def find_competition_by_parent_task(self, task_id: str) -> CompetitionRecord | None:
        with self._lock:
            row = self._row(
                "SELECT * FROM competitions WHERE parent_task_id = ? ORDER BY created_at DESC",
                (task_id,),
            )
        return self._competition_from_row(row) if row is not None else None

    def find_competition_by_candidate_task(self, task_id: str) -> CompetitionRecord | None:
        with self._lock:
            row = self._row(
                """
                SELECT c.* FROM competitions c
                JOIN competition_candidates cc ON cc.competition_id = c.competition_id
                WHERE cc.task_id = ?
                """,
                (task_id,),
            )
        return self._competition_from_row(row) if row is not None else None

    def update_competition_status(
        self,
        competition_id: str,
        new_status: str,
        *,
        winner_task_id: str | None = None,
        winner_score: float | None = None,
        decision_ref: str | None = None,
        evaluation_artifact_ref: str | None = None,
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            row = self._row(
                "SELECT status FROM competitions WHERE competition_id = ?",
                (competition_id,),
            )
            if row is None:
                raise ValueError(f"Competition not found: {competition_id}")
            current = str(row["status"])
            validate_transition(current, new_status, COMPETITION_TRANSITIONS, label="competition")
            sets = ["status = ?", "updated_at = ?"]
            params: list[Any] = [new_status, now]
            if winner_task_id is not None:
                sets.append("winner_task_id = ?")
                params.append(winner_task_id)
            if winner_score is not None:
                sets.append("winner_score = ?")
                params.append(winner_score)
            if decision_ref is not None:
                sets.append("decision_ref = ?")
                params.append(decision_ref)
            if evaluation_artifact_ref is not None:
                sets.append("evaluation_artifact_ref = ?")
                params.append(evaluation_artifact_ref)
            params.append(competition_id)
            self._conn.execute(
                f"UPDATE competitions SET {', '.join(sets)} WHERE competition_id = ?",
                tuple(params),
            )

    # -- Candidate CRUD -------------------------------------------------------

    def create_candidate(
        self,
        *,
        competition_id: str,
        task_id: str,
        label: str,
        workspace_ref: str | None = None,
    ) -> CompetitionCandidateRecord:
        candidate_id = self._id("cand")
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO competition_candidates (
                    candidate_id, competition_id, task_id, label,
                    workspace_ref, status, score_breakdown_json,
                    promoted, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', '{}', 0, ?)
                """,
                (candidate_id, competition_id, task_id, label, workspace_ref, now),
            )
            row = self._row(
                "SELECT * FROM competition_candidates WHERE candidate_id = ?",
                (candidate_id,),
            )
        assert row is not None
        return self._candidate_from_row(row)

    def list_candidates(
        self,
        competition_id: str,
        *,
        status: str | None = None,
    ) -> list[CompetitionCandidateRecord]:
        query = "SELECT * FROM competition_candidates WHERE competition_id = ?"
        params: list[Any] = [competition_id]
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at ASC"
        with self._lock:
            rows = self._rows(query, params)
        return [self._candidate_from_row(r) for r in rows]

    def find_candidate_by_task(self, task_id: str) -> CompetitionCandidateRecord | None:
        with self._lock:
            row = self._row(
                "SELECT * FROM competition_candidates WHERE task_id = ?",
                (task_id,),
            )
        return self._candidate_from_row(row) if row is not None else None

    def update_candidate_status(
        self,
        candidate_id: str,
        new_status: str,
        *,
        score: float | None = None,
        score_breakdown: dict[str, Any] | None = None,
        evaluation_receipt_ref: str | None = None,
        promoted: bool | None = None,
        discard_reason: str | None = None,
    ) -> None:
        now = time.time()
        with self._lock, self._conn:
            row = self._row(
                "SELECT status FROM competition_candidates WHERE candidate_id = ?",
                (candidate_id,),
            )
            if row is None:
                raise ValueError(f"Candidate not found: {candidate_id}")
            current = str(row["status"])
            validate_transition(current, new_status, CANDIDATE_TRANSITIONS, label="candidate")
            sets = ["status = ?"]
            params: list[Any] = [new_status]
            if new_status in ("completed", "failed", "disqualified"):
                sets.append("finished_at = ?")
                params.append(now)
            if score is not None:
                sets.append("score = ?")
                params.append(score)
            if score_breakdown is not None:
                sets.append("score_breakdown_json = ?")
                params.append(json.dumps(score_breakdown, ensure_ascii=False))
            if evaluation_receipt_ref is not None:
                sets.append("evaluation_receipt_ref = ?")
                params.append(evaluation_receipt_ref)
            if promoted is not None:
                sets.append("promoted = ?")
                params.append(1 if promoted else 0)
            if discard_reason is not None:
                sets.append("discard_reason = ?")
                params.append(discard_reason)
            params.append(candidate_id)
            self._conn.execute(
                f"UPDATE competition_candidates SET {', '.join(sets)} WHERE candidate_id = ?",
                tuple(params),
            )

    def update_candidate_score(
        self,
        candidate_id: str,
        *,
        score: float,
        score_breakdown: dict[str, Any] | None = None,
        evaluation_receipt_ref: str | None = None,
        promoted: bool | None = None,
    ) -> None:
        """Update scoring fields without changing candidate status."""
        with self._lock, self._conn:
            row = self._row(
                "SELECT candidate_id FROM competition_candidates WHERE candidate_id = ?",
                (candidate_id,),
            )
            if row is None:
                raise ValueError(f"Candidate not found: {candidate_id}")
            sets = ["score = ?"]
            params: list[Any] = [score]
            if score_breakdown is not None:
                sets.append("score_breakdown_json = ?")
                params.append(json.dumps(score_breakdown, ensure_ascii=False))
            if evaluation_receipt_ref is not None:
                sets.append("evaluation_receipt_ref = ?")
                params.append(evaluation_receipt_ref)
            if promoted is not None:
                sets.append("promoted = ?")
                params.append(1 if promoted else 0)
            params.append(candidate_id)
            self._conn.execute(
                f"UPDATE competition_candidates SET {', '.join(sets)} WHERE candidate_id = ?",
                tuple(params),
            )

    # -- Row conversion -------------------------------------------------------

    def _competition_from_row(self, row: sqlite3.Row) -> CompetitionRecord:
        return CompetitionRecord(
            competition_id=str(row["competition_id"]),
            parent_task_id=str(row["parent_task_id"]),
            goal=str(row["goal"]),
            strategy=str(row["strategy"]),
            candidate_count=int(row["candidate_count"]),
            min_candidates=int(row["min_candidates"]),
            evaluation_criteria=json_loads(row["evaluation_criteria_json"]),
            scoring_weights=json_loads(row["scoring_weights_json"]),
            status=str(row["status"]),
            timeout_policy=str(row["timeout_policy"]),
            winner_task_id=row["winner_task_id"],
            winner_score=float(row["winner_score"]) if row["winner_score"] is not None else None,
            decision_ref=row["decision_ref"],
            evaluation_artifact_ref=row["evaluation_artifact_ref"],
            timeout_seconds=float(row["timeout_seconds"])
            if row["timeout_seconds"] is not None
            else None,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _candidate_from_row(self, row: sqlite3.Row) -> CompetitionCandidateRecord:
        return CompetitionCandidateRecord(
            candidate_id=str(row["candidate_id"]),
            competition_id=str(row["competition_id"]),
            task_id=str(row["task_id"]),
            label=str(row["label"]),
            workspace_ref=row["workspace_ref"],
            status=str(row["status"]),
            score=float(row["score"]) if row["score"] is not None else None,
            score_breakdown=json_loads(row["score_breakdown_json"]),
            evaluation_receipt_ref=row["evaluation_receipt_ref"],
            promoted=bool(row["promoted"]),
            discard_reason=row["discard_reason"],
            created_at=float(row["created_at"]),
            finished_at=float(row["finished_at"]) if row["finished_at"] is not None else None,
        )
