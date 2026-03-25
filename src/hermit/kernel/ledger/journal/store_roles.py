"""RoleDefinitionStoreMixin — adds role_definitions table to KernelStore."""

from __future__ import annotations

import time
from typing import Any

import structlog

from hermit.kernel.ledger.journal.store_support import (
    canonical_json,
    json_loads,
)
from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase
from hermit.kernel.task.models.role_definition import RoleDefinition

log = structlog.get_logger()


class RoleDefinitionStoreMixin(KernelStoreTypingBase):
    """Store methods for RoleDefinition records."""

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_role_definition(
        self,
        *,
        name: str,
        description: str = "",
        mcp_servers: list[str] | None = None,
        skills: list[str] | None = None,
        config: dict[str, Any] | None = None,
        is_builtin: bool = False,
    ) -> RoleDefinition:
        now = time.time()
        role_id = self._id("role_def")
        normalized_mcp_servers = list(mcp_servers or [])
        normalized_skills = list(skills or [])
        normalized_config = dict(config or {})
        with self._get_conn():
            self._get_conn().execute(
                """
                INSERT INTO role_definitions (
                    role_id, name, description,
                    mcp_servers_json, skills_json, config_json,
                    is_builtin, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    role_id,
                    name,
                    description,
                    canonical_json(normalized_mcp_servers),
                    canonical_json(normalized_skills),
                    canonical_json(normalized_config),
                    1 if is_builtin else 0,
                    now,
                    now,
                ),
            )
        role = self.get_role_definition(role_id)
        assert role is not None
        return role

    def get_role_definition(self, role_id: str) -> RoleDefinition | None:
        row = self._row("SELECT * FROM role_definitions WHERE role_id = ?", (role_id,))
        return self._role_definition_from_row(row) if row is not None else None

    def list_role_definitions(
        self,
        *,
        include_builtin: bool = True,
        limit: int = 100,
    ) -> list[RoleDefinition]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_builtin:
            clauses.append("is_builtin = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM role_definitions {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._rows(query, params)
        return [self._role_definition_from_row(row) for row in rows]

    def update_role_definition(self, role_id: str, **kwargs: Any) -> None:
        """Update a role definition's fields.

        Accepts optional keyword arguments: name, description, mcp_servers,
        skills, config.  Only provided fields are updated.

        Raises ValueError if the role is builtin.
        """
        existing = self.get_role_definition(role_id)
        if existing is None:
            return
        if existing.is_builtin:
            msg = f"Cannot update builtin role definition: {role_id}"
            raise ValueError(msg)

        allowed_fields = {"name", "description", "mcp_servers", "skills", "config"}
        updates: list[str] = []
        params: list[Any] = []
        for key, value in kwargs.items():
            if key not in allowed_fields:
                continue
            if key == "mcp_servers":
                updates.append("mcp_servers_json = ?")
                params.append(canonical_json(list(value)))
            elif key == "skills":
                updates.append("skills_json = ?")
                params.append(canonical_json(list(value)))
            elif key == "config":
                updates.append("config_json = ?")
                params.append(canonical_json(dict(value)))
            else:
                updates.append(f"{key} = ?")
                params.append(value)

        if not updates:
            return

        now = time.time()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(role_id)

        set_clause = ", ".join(updates)
        with self._get_conn():
            self._get_conn().execute(
                f"UPDATE role_definitions SET {set_clause} WHERE role_id = ?",
                params,
            )

    def delete_role_definition(self, role_id: str) -> None:
        """Delete a role definition.

        Raises ValueError if the role is builtin.
        """
        existing = self.get_role_definition(role_id)
        if existing is None:
            return
        if existing.is_builtin:
            msg = f"Cannot delete builtin role definition: {role_id}"
            raise ValueError(msg)
        with self._get_conn():
            self._get_conn().execute(
                "DELETE FROM role_definitions WHERE role_id = ?",
                (role_id,),
            )

    # ------------------------------------------------------------------
    # Row mapping
    # ------------------------------------------------------------------

    def _role_definition_from_row(self, row: Any) -> RoleDefinition:
        mcp_servers_raw = json_loads(row["mcp_servers_json"])
        skills_raw = json_loads(row["skills_json"])
        return RoleDefinition(
            role_id=str(row["role_id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            mcp_servers=list(mcp_servers_raw) if isinstance(mcp_servers_raw, list) else [],
            skills=list(skills_raw) if isinstance(skills_raw, list) else [],
            config=json_loads(row["config_json"]),
            is_builtin=bool(row["is_builtin"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )
