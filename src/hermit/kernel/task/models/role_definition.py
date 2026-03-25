"""RoleDefinition model — defines a reusable agent role with capabilities.

Roles specify the MCP servers, skills, and configuration that a worker
should be provisioned with.  Builtin roles mirror the WorkerRole enum values
and are seeded during schema initialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "RoleDefinition",
]


@dataclass
class RoleDefinition:
    """A reusable role definition specifying agent capabilities.

    Builtin roles (``is_builtin=True``) are seeded from WorkerRole enum
    values and cannot be updated or deleted.
    """

    role_id: str
    name: str
    description: str = ""
    mcp_servers: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    is_builtin: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
