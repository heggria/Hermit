from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

DEFAULT_CATEGORIES = [
    "user_preference",
    "project_convention",
    "tech_decision",
    "tooling_environment",
    "active_task",
    "other",
]

_LEGACY_CATEGORY_MAP: dict[str, str] = {
    "用户偏好": "user_preference",
    "项目约定": "project_convention",
    "技术决策": "tech_decision",
    "环境与工具": "tooling_environment",
    "工具与环境": "tooling_environment",
    "其他": "other",
    "进行中的任务": "active_task",
    "active_task": "active_task",
}


def normalize_category(category: str) -> str:
    """Map legacy Chinese category names to English internal constants."""
    return _LEGACY_CATEGORY_MAP.get(category, category)


@dataclass
class MemoryEntry:
    category: str
    content: str
    score: int = 5
    locked: bool = False
    created_at: date = field(default_factory=date.today)
    updated_at: date | None = None
    confidence: float = 0.5
    supersedes: list[str] = field(default_factory=list[str])
    scope_kind: str = ""
    scope_ref: str = ""
    retention_class: str = ""
    entities: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.updated_at is None or self.updated_at < self.created_at:
            self.updated_at = self.created_at

    def render(self) -> str:
        lock = "🔒" if self.locked else ""
        line = f"- [{self.created_at.isoformat()}] [s:{self.score}{lock}] {self.content}"
        meta = self.meta_dict()
        if meta:
            return f"{line} <!--memory:{json.dumps(meta, ensure_ascii=False, separators=(',', ':'))}-->"
        return line

    def meta_dict(self) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        if self.updated_at is not None and self.updated_at != self.created_at:
            meta["updated_at"] = self.updated_at.isoformat()
        if round(self.confidence, 2) != 0.5:
            meta["confidence"] = round(self.confidence, 2)
        if self.supersedes:
            meta["supersedes"] = list(self.supersedes)
        if self.scope_kind:
            meta["scope_kind"] = self.scope_kind
        if self.scope_ref:
            meta["scope_ref"] = self.scope_ref
        if self.retention_class:
            meta["retention_class"] = self.retention_class
        return meta
