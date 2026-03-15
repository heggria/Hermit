from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PrincipalRecord:
    principal_id: str
    principal_type: str
    display_name: str
    source_channel: str | None = None
    external_ref: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
