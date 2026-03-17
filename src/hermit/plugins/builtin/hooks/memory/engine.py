from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, cast

import structlog

from hermit.infra.storage import FileGuard, atomic_write
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
from hermit.plugins.builtin.hooks.memory.types import DEFAULT_CATEGORIES, MemoryEntry

log = structlog.get_logger()

ENTRY_RE = re.compile(
    r"^- \[(\d{4}-\d{2}-\d{2})\] \[s:(\d+)(🔒?)\] (.+?)(?:\s+<!--memory:(.+?)-->)?$"
)
HEADING_RE = re.compile(r"^## (.+)$")
MergeFn = Callable[[str, list[MemoryEntry]], list[MemoryEntry]]


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
                current_category = str(heading_match.group(1) or "")
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

    def append_entries(self, new_entries: list[MemoryEntry]) -> dict[str, list[MemoryEntry]]:
        """Append only genuinely new entries without applying score updates."""
        with FileGuard.acquire(self.path, cross_process=True):
            categories = self.load()
            for entry in new_entries:
                categories.setdefault(entry.category, [])
                self._resolve_supersedes(categories[entry.category], entry)
                if not self._is_duplicate(categories[entry.category], entry.content):
                    categories[entry.category].append(entry)
            categories = {
                category: [entry for entry in entries if entry.score > 0]
                for category, entries in categories.items()
            }
            self.save(categories)
            return categories

    def record_session(
        self,
        new_entries: list[MemoryEntry],
        used_keywords: set[str] | None = None,
        session_index: int = 1,
        merge_fn: MergeFn | None = None,
        merge_threshold: int = 8,
    ) -> dict[str, list[MemoryEntry]]:
        """Update memory scores, add new entries, and persist atomically.

        The entire load → modify → save sequence runs under FileGuard so that
        concurrent sessions cannot interleave their writes and lose data.
        cross_process=True also acquires an flock for multi-process safety.
        """
        with FileGuard.acquire(self.path, cross_process=True):
            categories = self.load()
            used_keywords = used_keywords or set()
            lowered_keywords = {keyword.lower() for keyword in used_keywords}

            for category, entries in categories.items():
                slow_decay = category == "项目约定" and session_index % 2 == 0
                for entry in entries:
                    if entry.locked:
                        continue
                    if self._entry_referenced(entry, lowered_keywords):
                        entry.score = min(10, entry.score + 1)
                    elif not slow_decay:
                        entry.score = max(0, entry.score - 1)
                    if entry.score >= 7:
                        entry.locked = True

            filtered: dict[str, list[MemoryEntry]] = {
                category: [entry for entry in entries if entry.score > 0]
                for category, entries in categories.items()
            }

            for entry in new_entries:
                filtered.setdefault(entry.category, [])
                self._resolve_supersedes(filtered[entry.category], entry)
                if not self._is_duplicate(filtered[entry.category], entry.content):
                    filtered[entry.category].append(entry)

            if merge_fn:
                for category, entries in list(filtered.items()):
                    unlocked = [entry for entry in entries if not entry.locked]
                    locked = [entry for entry in entries if entry.locked]
                    if len(unlocked) > merge_threshold:
                        filtered[category] = locked + merge_fn(category, unlocked)

            self.save(filtered)
            return filtered

    @staticmethod
    def summary_prompt(
        categories: dict[str, list[MemoryEntry]], limit_per_category: int = 10
    ) -> str:
        return render_summary_prompt(categories, limit_per_category=limit_per_category)

    def retrieval_prompt(
        self,
        query: str,
        *,
        categories: dict[str, list[MemoryEntry]] | None = None,
        limit: int = 6,
        char_budget: int = 1200,
    ) -> str:
        ranked = self.retrieve(query, categories=categories, limit=limit)
        if not ranked:
            log.info(
                "memory_retrieval_injected", query_chars=len(query), injected=0, budget=char_budget
            )
            return ""

        lines = ["以下是与当前任务最相关的跨会话记忆，只在相关时优先遵循："]
        total = len(lines[0])
        current_category: str | None = None
        injected = 0
        for entry, _score in ranked:
            if entry.category != current_category:
                heading = f"\n## {entry.category}"
                if total + len(heading) > char_budget:
                    break
                lines.append(heading)
                total += len(heading)
                current_category = entry.category
            rendered = entry.render()
            if total + len(rendered) > char_budget:
                break
            lines.append(rendered)
            total += len(rendered)
            injected += 1
        log.info(
            "memory_retrieval_injected",
            query_chars=len(query),
            injected=injected,
            candidates=len(ranked),
            budget=char_budget,
        )
        return "\n".join(lines).strip()

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


def group_entries(entries: list[MemoryEntry]) -> dict[str, list[MemoryEntry]]:
    grouped: dict[str, list[MemoryEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.category].append(entry)
    return dict(grouped)
