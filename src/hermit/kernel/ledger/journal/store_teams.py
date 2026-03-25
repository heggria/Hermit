from __future__ import annotations

import time
from typing import Any

from hermit.kernel.ledger.journal.store_support import (
    canonical_json,
    json_loads,
    sqlite_optional_float,
)
from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase
from hermit.kernel.task.models.team import (
    MILESTONE_STATE_TRANSITIONS,
    TEAM_STATE_TRANSITIONS,
    MilestoneRecord,
    RoleSlotSpec,
    TeamRecord,
)


def _serialize_role_assembly(assembly: dict[str, RoleSlotSpec | Any]) -> str:
    """Serialize a role_assembly dict to JSON.

    Accepts both ``RoleSlotSpec`` values (canonical) and plain dicts
    (for backward compatibility with existing callers / tests).
    """
    out: dict[str, Any] = {}
    for key, val in assembly.items():
        if isinstance(val, RoleSlotSpec):
            out[key] = {"role": val.role, "count": val.count, "config": val.config}
        else:
            out[key] = val
    return canonical_json(out)


def _deserialize_role_assembly(raw: Any) -> dict[str, RoleSlotSpec]:
    """Deserialize JSON back into ``dict[str, RoleSlotSpec]``.

    Legacy rows that stored plain scalars (e.g. ``{"lead": "agent_1"}``)
    are wrapped into a ``RoleSlotSpec`` with ``count=1`` and the original
    value placed in ``config["legacy_value"]``.
    """
    data = json_loads(raw) if isinstance(raw, str) else raw
    result: dict[str, RoleSlotSpec] = {}
    for key, val in data.items():
        if isinstance(val, dict) and "role" in val:
            result[key] = RoleSlotSpec(
                role=str(val["role"]),
                count=int(val.get("count", 1)),
                config=dict(val.get("config", {})),
            )
        else:
            # Legacy format — wrap into RoleSlotSpec.
            result[key] = RoleSlotSpec(
                role=str(key),
                count=1,
                config={"legacy_value": val},
            )
    return result


class KernelTeamStoreMixin(KernelStoreTypingBase):
    def create_team(
        self,
        *,
        program_id: str,
        title: str,
        workspace_id: str,
        status: str = "active",
        role_assembly: dict[str, RoleSlotSpec | Any] | None = None,
        context_boundary: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TeamRecord:
        now = time.time()
        team_id = self._id("team")
        normalized_role_assembly: dict[str, RoleSlotSpec | Any] = dict(role_assembly or {})
        normalized_context_boundary = list(context_boundary or [])
        normalized_metadata = dict(metadata or {})
        serialized_assembly = _serialize_role_assembly(normalized_role_assembly)
        # Ensure updated_at column exists for DBs created before this migration.
        self._ensure_team_column("updated_at", "REAL")
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO teams (
                    team_id, program_id, title, workspace_id, status,
                    role_assembly_json, context_boundary_json,
                    created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team_id,
                    program_id,
                    title,
                    workspace_id,
                    status,
                    serialized_assembly,
                    canonical_json(normalized_context_boundary),
                    now,
                    now,
                    canonical_json(normalized_metadata),
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="team.created",
                entity_type="team",
                entity_id=team_id,
                task_id=None,
                actor="kernel",
                payload={
                    "program_id": program_id,
                    "title": title,
                    "workspace_id": workspace_id,
                    "status": status,
                    "role_assembly": json_loads(serialized_assembly),
                    "context_boundary": normalized_context_boundary,
                    "metadata": normalized_metadata,
                    "created_at": now,
                },
            )
        team = self.get_team(team_id)
        assert team is not None
        return team

    def get_team(self, team_id: str) -> TeamRecord | None:
        row = self._row("SELECT * FROM teams WHERE team_id = ?", (team_id,))
        return self._team_from_row(row) if row is not None else None

    def list_teams_by_program(self, *, program_id: str, limit: int = 50) -> list[TeamRecord]:
        rows = self._rows(
            "SELECT * FROM teams WHERE program_id = ? ORDER BY created_at DESC LIMIT ?",
            (program_id, limit),
        )
        return [self._team_from_row(row) for row in rows]

    def update_team_status(self, team_id: str, status: str) -> None:
        """Transition a team's status and emit an event."""
        team = self.get_team(team_id)
        if team is None:
            raise ValueError(f"Team {team_id} not found")
        allowed = TEAM_STATE_TRANSITIONS.get(team.status, frozenset())
        if status not in allowed:
            raise ValueError(f"Invalid team status transition: {team.status!r} -> {status!r}")
        now = time.time()
        with self._get_conn():
            self._get_conn().execute(
                "UPDATE teams SET status = ?, updated_at = ? WHERE team_id = ?",
                (status, now, team_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type=f"team.{status}",
                entity_type="team",
                entity_id=team_id,
                task_id=None,
                actor="kernel",
                payload={"status": status},
            )

    def create_milestone(
        self,
        *,
        team_id: str,
        title: str,
        description: str = "",
        status: str = "pending",
        dependency_ids: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> MilestoneRecord:
        now = time.time()
        milestone_id = self._id("milestone")
        normalized_dependency_ids = list(dependency_ids or [])
        normalized_acceptance_criteria = list(acceptance_criteria or [])
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO milestones (
                    milestone_id, team_id, title, description, status,
                    dependency_ids_json, acceptance_criteria_json,
                    created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    milestone_id,
                    team_id,
                    title,
                    description,
                    status,
                    canonical_json(normalized_dependency_ids),
                    canonical_json(normalized_acceptance_criteria),
                    now,
                ),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type="milestone.created",
                entity_type="milestone",
                entity_id=milestone_id,
                task_id=None,
                actor="kernel",
                payload={
                    "team_id": team_id,
                    "title": title,
                    "description": description,
                    "status": status,
                    "dependency_ids": normalized_dependency_ids,
                    "acceptance_criteria": normalized_acceptance_criteria,
                    "created_at": now,
                },
            )
        milestone = self.get_milestone(milestone_id)
        assert milestone is not None
        return milestone

    def get_milestone(self, milestone_id: str) -> MilestoneRecord | None:
        row = self._row("SELECT * FROM milestones WHERE milestone_id = ?", (milestone_id,))
        return self._milestone_from_row(row) if row is not None else None

    def list_teams_with_milestones(
        self, *, program_id: str, limit: int = 50
    ) -> dict[str, tuple[TeamRecord, list[MilestoneRecord]]]:
        """Batch-fetch teams and their milestones in a single JOIN query.

        Returns a dict mapping ``team_id`` to a ``(TeamRecord, list[MilestoneRecord])``
        tuple.  This eliminates the N+1 query pattern where teams are fetched first,
        then milestones are fetched per-team in a loop.
        """
        rows = self._rows(
            """
            SELECT t.team_id AS t_team_id,
                   t.program_id AS t_program_id,
                   t.title AS t_title,
                   t.workspace_id AS t_workspace_id,
                   t.status AS t_status,
                   t.role_assembly_json AS t_role_assembly_json,
                   t.context_boundary_json AS t_context_boundary_json,
                   t.created_at AS t_created_at,
                   t.updated_at AS t_updated_at,
                   t.metadata_json AS t_metadata_json,
                   m.milestone_id AS m_milestone_id,
                   m.team_id AS m_team_id,
                   m.title AS m_title,
                   m.description AS m_description,
                   m.status AS m_status,
                   m.dependency_ids_json AS m_dependency_ids_json,
                   m.acceptance_criteria_json AS m_acceptance_criteria_json,
                   m.created_at AS m_created_at,
                   m.completed_at AS m_completed_at
            FROM teams t
            LEFT JOIN milestones m ON m.team_id = t.team_id
            WHERE t.program_id = ?
            ORDER BY t.created_at, m.created_at
            """,
            (program_id,),
        )
        result: dict[str, tuple[TeamRecord, list[MilestoneRecord]]] = {}
        for row in rows:
            team_id = str(row["t_team_id"])
            if team_id not in result:
                team = TeamRecord(
                    team_id=team_id,
                    program_id=str(row["t_program_id"]),
                    title=str(row["t_title"]),
                    workspace_id=str(row["t_workspace_id"]),
                    status=str(row["t_status"]),
                    role_assembly=_deserialize_role_assembly(row["t_role_assembly_json"]),
                    context_boundary=list(json_loads(row["t_context_boundary_json"])),
                    created_at=float(row["t_created_at"]),
                    updated_at=(
                        float(row["t_updated_at"]) if row["t_updated_at"] is not None else 0.0
                    ),
                    metadata=json_loads(row["t_metadata_json"]),
                )
                result[team_id] = (team, [])
            # LEFT JOIN produces NULL milestone columns when a team has no milestones.
            if row["m_milestone_id"] is not None:
                milestone = MilestoneRecord(
                    milestone_id=str(row["m_milestone_id"]),
                    team_id=str(row["m_team_id"]),
                    title=str(row["m_title"]),
                    description=str(row["m_description"]),
                    status=str(row["m_status"]),
                    dependency_ids=list(json_loads(row["m_dependency_ids_json"])),
                    acceptance_criteria=list(json_loads(row["m_acceptance_criteria_json"])),
                    created_at=float(row["m_created_at"]),
                    completed_at=(sqlite_optional_float(row["m_completed_at"])),
                )
                result[team_id][1].append(milestone)
        return result

    def list_milestones_by_team(self, *, team_id: str, limit: int = 50) -> list[MilestoneRecord]:
        rows = self._rows(
            "SELECT * FROM milestones WHERE team_id = ? ORDER BY created_at ASC LIMIT ?",
            (team_id, limit),
        )
        return [self._milestone_from_row(row) for row in rows]

    def update_milestone_status(self, milestone_id: str, status: str) -> None:
        milestone = self.get_milestone(milestone_id)
        if milestone is None:
            raise ValueError(f"Milestone {milestone_id} not found")
        allowed = MILESTONE_STATE_TRANSITIONS.get(milestone.status, frozenset())
        if status not in allowed:
            raise ValueError(
                f"Invalid milestone status transition: {milestone.status!r} -> {status!r}"
            )
        now = time.time()
        completed_at = now if status == "completed" else None
        with self._get_conn():
            self._get_conn().execute(
                """
                UPDATE milestones
                SET status = ?, completed_at = COALESCE(?, completed_at)
                WHERE milestone_id = ?
                """,
                (status, completed_at, milestone_id),
            )
            self._append_event_tx(
                event_id=self._id("event"),
                event_type=f"milestone.{status}",
                entity_type="milestone",
                entity_id=milestone_id,
                task_id=None,
                actor="kernel",
                payload={
                    "status": status,
                    "completed_at": completed_at,
                },
            )

    def _ensure_team_column(self, column: str, definition: str) -> None:
        """Add a column to the teams table if it does not already exist."""
        existing = {
            str(row["name"])
            for row in self._get_conn().execute("PRAGMA table_info(teams)").fetchall()
        }
        if column in existing:
            return
        self._get_conn().execute(f"ALTER TABLE teams ADD COLUMN {column} {definition}")

    # ── Row mappers ──────────────────────────────────────────────────────

    def _team_from_row(self, row: Any) -> TeamRecord:
        return TeamRecord(
            team_id=str(row["team_id"]),
            program_id=str(row["program_id"]),
            title=str(row["title"]),
            workspace_id=str(row["workspace_id"]),
            status=str(row["status"]),
            role_assembly=_deserialize_role_assembly(row["role_assembly_json"]),
            context_boundary=list(json_loads(row["context_boundary_json"])),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]) if row["updated_at"] is not None else 0.0,
            metadata=json_loads(row["metadata_json"]),
        )

    def _milestone_from_row(self, row: Any) -> MilestoneRecord:
        return MilestoneRecord(
            milestone_id=str(row["milestone_id"]),
            team_id=str(row["team_id"]),
            title=str(row["title"]),
            description=str(row["description"]),
            status=str(row["status"]),
            dependency_ids=list(json_loads(row["dependency_ids_json"])),
            acceptance_criteria=list(json_loads(row["acceptance_criteria_json"])),
            created_at=float(row["created_at"]),
            completed_at=sqlite_optional_float(row["completed_at"]),
        )
