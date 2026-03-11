from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

DEFAULT_CATEGORIES = [
    "用户偏好",
    "项目约定",
    "技术决策",
    "环境与工具",
    "其他",
    "进行中的任务",
]


@dataclass
class MemoryEntry:
    category: str
    content: str
    score: int = 5
    locked: bool = False
    created_at: date = field(default_factory=date.today)
    updated_at: Optional[date] = None
    confidence: float = 0.5
    supersedes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.updated_at is None:
            self.updated_at = self.created_at
        elif self.updated_at < self.created_at:
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
        if self.updated_at != self.created_at:
            meta["updated_at"] = self.updated_at.isoformat()
        if round(self.confidence, 2) != 0.5:
            meta["confidence"] = round(self.confidence, 2)
        if self.supersedes:
            meta["supersedes"] = list(self.supersedes)
        return meta
