from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, cast

import structlog

from hermit.infra.storage import atomic_write
from hermit.kernel.context.memory.text import (
    is_duplicate,
    looks_like_override,
    normalize_topic,
    shares_topic,
    topic_tokens,
)
from hermit.kernel.context.memory.text import (
    summary_prompt as render_summary_prompt,
)
from hermit.plugins.builtin.hooks.memory.types import (
    DEFAULT_CATEGORIES,
    MemoryEntry,
    normalize_category,
)

log = structlog.get_logger()

ENTRY_RE = re.compile(
    r"^- \[(\d{4}-\d{2}-\d{2})\] \[s:(\d+)(🔒?)\] (.+?)(?:\s+<!--memory:(.+?)-->)?$"
)
HEADING_RE = re.compile(r"^## (.+)$")


class MemoryEngine:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, list[MemoryEntry]]:
        if not self.path.exists():
            return {category: [] for category in DEFAULT_CATEGORIES}

        categories: dict[str, list[MemoryEntry]] = {category: [] for category in DEFAULT_CATEGORIES}
        current_category: str | None = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            heading_match = HEADING_RE.match(line)
            if heading_match:
                current_category = normalize_category(str(heading_match.group(1) or ""))
                categories.setdefault(current_category, [])
                continue
            entry_match = ENTRY_RE.match(line)
            if entry_match and current_category:
                created_at, score, locked, content, raw_meta = entry_match.groups()
                meta = self._parse_meta(raw_meta)
                categories[current_category].append(
                    MemoryEntry(
                        category=current_category,
                        content=content,
                        score=int(score),
                        locked=bool(locked),
                        created_at=date.fromisoformat(created_at),
                        updated_at=date.fromisoformat(str(meta.get("updated_at", created_at))),
                        confidence=float(meta.get("confidence", 0.5)),
                        supersedes=list(meta.get("supersedes", [])),
                        scope_kind=str(meta.get("scope_kind", "")),
                        scope_ref=str(meta.get("scope_ref", "")),
                        retention_class=str(meta.get("retention_class", "")),
                    )
                )
        return categories

    def save(self, categories: dict[str, list[MemoryEntry]]) -> None:
        """Atomically overwrite memories.md with *categories*."""
        lines: list[str] = []
        ordered_categories = list(DEFAULT_CATEGORIES)
        ordered_categories.extend(
            category for category in categories if category not in ordered_categories
        )

        for category in ordered_categories:
            entries = categories.get(category, [])
            lines.append(f"## {category}")
            if not entries:
                lines.append("")
                continue
            lines.extend(entry.render() for entry in entries)
            lines.append("")

        atomic_write(self.path, "\n".join(lines).rstrip() + "\n")

    @staticmethod
    def summary_prompt(
        categories: dict[str, list[MemoryEntry]], limit_per_category: int = 10
    ) -> str:
        return render_summary_prompt(categories, limit_per_category=limit_per_category)

    def retrieve(
        self,
        query: str,
        *,
        categories: dict[str, list[MemoryEntry]] | None = None,
        limit: int = 6,
    ) -> list[tuple[MemoryEntry, float]]:
        query_tokens = self._topic_tokens(query)
        query_paths = set(re.findall(r"/[\w./-]+", query))
        query_numbers = set(re.findall(r"\d+(?:\.\d+)?", query))
        if not query_tokens and not query_paths and not query_numbers:
            return []

        categories = categories or self.load()
        ranked: list[tuple[MemoryEntry, float]] = []
        for entries in categories.values():
            for entry in entries:
                score = self._retrieval_score(entry, query_tokens, query_paths, query_numbers)
                if score > 0:
                    ranked.append((entry, score))
        ranked.sort(
            key=lambda item: (
                item[1],
                item[0].locked,
                item[0].score,
                item[0].confidence,
                item[0].updated_at,
            ),
            reverse=True,
        )
        log.info(
            "memory_retrieval_ranked",
            query_chars=len(query),
            query_tokens=len(query_tokens),
            candidates=len(ranked),
            returned=min(limit, len(ranked)),
        )
        return ranked[:limit]

    @staticmethod
    def _entry_referenced(entry: MemoryEntry, keywords: set[str]) -> bool:
        text = entry.content.lower()
        return any(keyword in text for keyword in keywords)

    @classmethod
    def _retrieval_score(
        cls,
        entry: MemoryEntry,
        query_tokens: set[str],
        query_paths: set[str],
        query_numbers: set[str],
    ) -> float:
        entry_tokens = cls._topic_tokens(entry.content)
        entry_paths = set(re.findall(r"/[\w./-]+", entry.content))
        entry_numbers = set(re.findall(r"\d+(?:\.\d+)?", entry.content))

        overlap = len(entry_tokens & query_tokens)
        if overlap == 0 and not (entry_paths & query_paths) and not (entry_numbers & query_numbers):
            return 0.0

        score = float(overlap) * 2.0
        if entry_paths & query_paths:
            score += 3.0
        if entry_numbers & query_numbers:
            score += 1.5
        if entry.locked:
            score += 1.0
        score += entry.score * 0.15
        score += entry.confidence * 0.5
        return score

    @staticmethod
    def _is_duplicate(entries: list[MemoryEntry], content: str) -> bool:
        return is_duplicate(entries, content)

    @staticmethod
    def _parse_meta(raw_meta: str | None) -> dict[str, Any]:
        if not raw_meta:
            return {}
        try:
            raw = json.loads(raw_meta)
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}
        return cast(dict[str, Any], raw)

    @classmethod
    def _resolve_supersedes(cls, entries: list[MemoryEntry], new_entry: MemoryEntry) -> None:
        for existing in entries:
            if existing.locked and existing.score >= 8:
                continue
            if cls._is_duplicate([existing], new_entry.content):
                continue
            if not cls._looks_like_override(existing.content, new_entry.content):
                continue
            existing.score = 0
            if existing.content not in new_entry.supersedes:
                new_entry.supersedes.append(existing.content)
            new_entry.updated_at = max(
                new_entry.updated_at or new_entry.created_at,
                existing.updated_at or existing.created_at,
                existing.created_at,
            )
            new_entry.confidence = max(new_entry.confidence, min(0.95, existing.confidence + 0.1))
            log.info(
                "memory_superseded",
                category=new_entry.category,
                old_content=existing.content[:120],
                new_content=new_entry.content[:120],
            )

    @staticmethod
    def _looks_like_override(old_content: str, new_content: str) -> bool:
        return looks_like_override(old_content, new_content)

    @staticmethod
    def _topic_tokens(content: str) -> set[str]:
        return topic_tokens(content)

    @staticmethod
    def _normalize_topic(content: str) -> str:
        return normalize_topic(content)

    @classmethod
    def _shares_topic(cls, left: str, right: str) -> bool:
        return shares_topic(left, right)
