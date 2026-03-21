"""ProgramStoreMixin — adds program table to KernelStore."""

from __future__ import annotations

import time
from typing import Any

from hermit.kernel.ledger.journal.store_support import (
    canonical_json,
    json_loads,
    sqlite_optional_text,
)
from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase
from hermit.kernel.task.models.program import (
    PROGRAM_STATE_TRANSITIONS,
    ProgramRecord,
    ProgramState,
)


class ProgramStoreMixin(KernelStoreTypingBase):
    """Store methods for Program/Initiative records."""

    def _ensure_program_column(self, column: str, definition: str) -> None:
        """Add a column to the programs table if it does not already exist."""
        existing = {
            str(row["name"])
            for row in self._get_conn().execute("PRAGMA table_info(programs)").fetchall()
        }
        if column in existing:
            return
        self._get_conn().execute(f"ALTER TABLE programs ADD COLUMN {column} {definition}")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_program(
        self,
        *,
        title: str,
        goal: str,
        description: str = "",
        priority: str = "normal",
        program_contract_ref: str | None = None,
        budget_limits: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProgramRecord:
        now = time.time()
        program_id = self._id("program")
        normalized_budget = dict(budget_limits or {})
        normalized_metadata = dict(metadata or {})
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO programs (
                    program_id, title, goal, status, description, priority,
                    program_contract_ref,
                    budget_limits_json, milestone_ids_json, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, '[]', ?, ?, ?)
                """,
                (
                    program_id,
                    title,
                    goal,
                    description,
                    priority,
                    program_contract_ref,
                    canonical_json(normalized_budget),
                    canonical_json(normalized_metadata),
                    now,
                    now,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="program.created",
                entity_type="program",
                entity_id=program_id,
                task_id=None,
                actor="kernel",
                payload={
                    "title": title,
                    "goal": goal,
                    "description": description,
                    "priority": priority,
                    "program_contract_ref": program_contract_ref,
                    "budget_limits": normalized_budget,
                    "metadata": normalized_metadata,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        program = self.get_program(program_id)
        assert program is not None
        return program

    def get_program(self, program_id: str) -> ProgramRecord | None:
        row = self._row("SELECT * FROM programs WHERE program_id = ?", (program_id,))
        return self._program_from_row(row) if row is not None else None

    def list_programs(
        self,
        *,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[ProgramRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if priority:
            clauses.append("priority = ?")
            params.append(priority)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM programs {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._rows(query, params)
        return [self._program_from_row(row) for row in rows]

    def update_program_status(
        self,
        program_id: str,
        status: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Update program status with state-transition validation.

        Raises ValueError if the transition is not allowed by the spec lifecycle:
        draft → active → paused/blocked → completed/failed
        Terminal states (completed, failed) cannot transition further.
        """
        program = self.get_program(program_id)
        if program is None:
            return
        current = ProgramState(program.status)
        target = ProgramState(status)
        allowed = PROGRAM_STATE_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            msg = (
                f"Invalid program state transition: {current.value} → {target.value}. "
                f"Allowed transitions from '{current.value}': "
                f"{sorted(s.value for s in allowed) if allowed else 'none (terminal state)'}"
            )
            raise ValueError(msg)
        now = time.time()
        with self._get_conn():
            self._get_conn().execute(
                "UPDATE programs SET status = ?, updated_at = ? WHERE program_id = ?",
                (status, now, program_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type=f"program.{status}",
                entity_type="program",
                entity_id=program_id,
                task_id=None,
                actor="kernel",
                payload={"status": status, **(payload or {})},
            )

    def update_program_contract_ref(
        self,
        program_id: str,
        contract_ref: str,
    ) -> None:
        """Attach or update the governing contract reference for a program."""
        now = time.time()
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE programs
                SET program_contract_ref = ?, updated_at = ?
                WHERE program_id = ?
                """,
                (contract_ref, now, program_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="program.contract_updated",
                entity_type="program",
                entity_id=program_id,
                task_id=None,
                actor="kernel",
                payload={"program_contract_ref": contract_ref},
            )

    def add_milestone_to_program(
        self,
        program_id: str,
        milestone_id: str,
    ) -> None:
        program = self.get_program(program_id)
        if program is None:
            return
        updated_milestones = list(program.milestone_ids)
        if milestone_id not in updated_milestones:
            updated_milestones.append(milestone_id)
        now = time.time()
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE programs
                SET milestone_ids_json = ?, updated_at = ?
                WHERE program_id = ?
                """,
                (
                    canonical_json(updated_milestones),
                    now,
                    program_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="program.milestone_added",
                entity_type="program",
                entity_id=program_id,
                task_id=None,
                actor="kernel",
                payload={
                    "milestone_id": milestone_id,
                    "milestone_ids": updated_milestones,
                },
            )

    def remove_milestone_from_program(
        self,
        program_id: str,
        milestone_id: str,
    ) -> None:
        """Remove a milestone from a program's milestone list."""
        program = self.get_program(program_id)
        if program is None:
            return
        updated_milestones = [m for m in program.milestone_ids if m != milestone_id]
        if len(updated_milestones) == len(program.milestone_ids):
            return  # milestone not found, no-op
        now = time.time()
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE programs
                SET milestone_ids_json = ?, updated_at = ?
                WHERE program_id = ?
                """,
                (
                    canonical_json(updated_milestones),
                    now,
                    program_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="program.milestone_removed",
                entity_type="program",
                entity_id=program_id,
                task_id=None,
                actor="kernel",
                payload={
                    "milestone_id": milestone_id,
                    "milestone_ids": updated_milestones,
                },
            )

    def update_program_metadata(
        self,
        program_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """Merge new metadata keys into the program's existing metadata."""
        program = self.get_program(program_id)
        if program is None:
            return
        merged = {**program.metadata, **metadata}
        now = time.time()
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE programs
                SET metadata_json = ?, updated_at = ?
                WHERE program_id = ?
                """,
                (
                    canonical_json(merged),
                    now,
                    program_id,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="program.metadata_updated",
                entity_type="program",
                entity_id=program_id,
                task_id=None,
                actor="kernel",
                payload={"metadata": merged},
            )

    # ------------------------------------------------------------------
    # Row mapping
    # ------------------------------------------------------------------

    def _program_from_row(self, row: Any) -> ProgramRecord:
        return ProgramRecord(
            program_id=str(row["program_id"]),
            title=str(row["title"]),
            goal=str(row["goal"]),
            status=str(row["status"]),
            description=str(row["description"]),
            priority=str(row["priority"]),
            program_contract_ref=sqlite_optional_text(row["program_contract_ref"]),
            budget_limits=json_loads(row["budget_limits_json"]),
            milestone_ids=list(json_loads(row["milestone_ids_json"])),
            metadata=json_loads(row["metadata_json"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )
