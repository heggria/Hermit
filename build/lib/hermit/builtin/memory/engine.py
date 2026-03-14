from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

import structlog

from hermit.builtin.memory.types import DEFAULT_CATEGORIES, MemoryEntry
from hermit.storage import FileGuard, atomic_write

log = structlog.get_logger()

ENTRY_RE = re.compile(r"^- \[(\d{4}-\d{2}-\d{2})\] \[s:(\d+)(🔒?)\] (.+?)(?:\s+<!--memory:(.+?)-->)?$")
HEADING_RE = re.compile(r"^## (.+)$")
MergeFn = Callable[[str, List[MemoryEntry]], List[MemoryEntry]]


class MemoryEngine:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Dict[str, List[MemoryEntry]]:
        if not self.path.exists():
            return {category: [] for category in DEFAULT_CATEGORIES}

        categories: Dict[str, List[MemoryEntry]] = {category: [] for category in DEFAULT_CATEGORIES}
        current_category: Optional[str] = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            heading_match = HEADING_RE.match(line)
            if heading_match:
                current_category = heading_match.group(1)
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
                        updated_at=date.fromisoformat(
                            str(meta.get("updated_at", created_at))
                        ),
                        confidence=float(meta.get("confidence", 0.5)),
                        supersedes=list(meta.get("supersedes", [])),
                        scope_kind=str(meta.get("scope_kind", "")),
                        scope_ref=str(meta.get("scope_ref", "")),
                        retention_class=str(meta.get("retention_class", "")),
                    )
                )
        return categories

    def save(self, categories: Dict[str, List[MemoryEntry]]) -> None:
        """Atomically overwrite memories.md with *categories*."""
        lines: List[str] = []
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

    def append_entries(self, new_entries: List[MemoryEntry]) -> Dict[str, List[MemoryEntry]]:
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
        new_entries: List[MemoryEntry],
        used_keywords: Optional[Set[str]] = None,
        session_index: int = 1,
        merge_fn: Optional[MergeFn] = None,
        merge_threshold: int = 8,
    ) -> Dict[str, List[MemoryEntry]]:
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

            filtered: Dict[str, List[MemoryEntry]] = {
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
    def summary_prompt(categories: Dict[str, List[MemoryEntry]], limit_per_category: int = 10) -> str:
        if not any(entries for entries in categories.values()):
            return ""
        lines = ["以下是跨会话记忆，请优先遵循其中的长期约定："]
        for category, entries in categories.items():
            if not entries:
                continue
            lines.append(f"\n## {category}")
            for entry in entries[:limit_per_category]:
                lines.append(entry.render())
        return "\n".join(lines).strip()

    def retrieval_prompt(
        self,
        query: str,
        *,
        categories: Optional[Dict[str, List[MemoryEntry]]] = None,
        limit: int = 6,
        char_budget: int = 1200,
    ) -> str:
        ranked = self.retrieve(query, categories=categories, limit=limit)
        if not ranked:
            log.info("memory_retrieval_injected", query_chars=len(query), injected=0, budget=char_budget)
            return ""

        lines = ["以下是与当前任务最相关的跨会话记忆，只在相关时优先遵循："]
        total = len(lines[0])
        current_category = None
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
        categories: Optional[Dict[str, List[MemoryEntry]]] = None,
        limit: int = 6,
    ) -> List[Tuple[MemoryEntry, float]]:
        query_tokens = self._topic_tokens(query)
        query_paths = set(re.findall(r"/[\w./-]+", query))
        query_numbers = set(re.findall(r"\d+(?:\.\d+)?", query))
        if not query_tokens and not query_paths and not query_numbers:
            return []

        categories = categories or self.load()
        ranked: List[Tuple[MemoryEntry, float]] = []
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
    def _entry_referenced(entry: MemoryEntry, keywords: Set[str]) -> bool:
        text = entry.content.lower()
        return any(keyword in text for keyword in keywords)

    @classmethod
    def _retrieval_score(
        cls,
        entry: MemoryEntry,
        query_tokens: Set[str],
        query_paths: Set[str],
        query_numbers: Set[str],
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
    def _is_duplicate(entries: List[MemoryEntry], content: str) -> bool:
        normalized = content.strip().lower()
        for existing in entries:
            other = existing.content.strip().lower()
            shorter = min(len(normalized), len(other))
            longer = max(len(normalized), len(other))
            overlap_ratio = shorter / longer if longer else 1
            if normalized == other:
                return True
            if overlap_ratio >= 0.6 and (normalized in other or other in normalized):
                return True
        return False

    @staticmethod
    def _parse_meta(raw_meta: str | None) -> dict:
        if not raw_meta:
            return {}
        try:
            data = json.loads(raw_meta)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def _resolve_supersedes(cls, entries: List[MemoryEntry], new_entry: MemoryEntry) -> None:
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
            new_entry.updated_at = max(new_entry.updated_at, existing.updated_at, existing.created_at)
            new_entry.confidence = max(new_entry.confidence, min(0.95, existing.confidence + 0.1))
            log.info(
                "memory_superseded",
                category=new_entry.category,
                old_content=existing.content[:120],
                new_content=new_entry.content[:120],
            )

    @staticmethod
    def _looks_like_override(old_content: str, new_content: str) -> bool:
        if not MemoryEngine._shares_topic(old_content, new_content):
            return False
        old_numbers = set(re.findall(r"\d+(?:\.\d+)?", old_content))
        new_numbers = set(re.findall(r"\d+(?:\.\d+)?", new_content))
        if old_numbers != new_numbers and (old_numbers or new_numbers):
            return True
        old_paths = set(re.findall(r"/[\w./-]+", old_content))
        new_paths = set(re.findall(r"/[\w./-]+", new_content))
        if old_paths != new_paths and (old_paths or new_paths):
            return True
        directional_terms = ("改为", "切换到", "统一使用", "默认", "现在", "改成", "采用")
        return any(term in new_content for term in directional_terms)

    @staticmethod
    def _topic_tokens(content: str) -> Set[str]:
        raw_tokens = re.findall(r"[\w\-/\.]{2,}|[\u4e00-\u9fff]{2,}", content.lower())
        stopwords = {
            "默认", "现在", "以后", "统一", "使用", "需要", "必须", "采用", "改为", "切换到",
            "the", "and", "for", "with", "from", "that", "this", "use",
        }
        return {token for token in raw_tokens if token not in stopwords}

    @staticmethod
    def _normalize_topic(content: str) -> str:
        text = content.lower()
        text = re.sub(r"/[\w./-]+", "<path>", text)
        text = re.sub(r"\d+(?:\.\d+)?", "<num>", text)
        for word in ("改为", "改成", "切换到", "统一", "默认", "采用", "固定到", "使用", "现在"):
            text = text.replace(word, "")
        text = re.sub(r"[^\w\u4e00-\u9fff<>]+", "", text)
        return text

    @classmethod
    def _shares_topic(cls, left: str, right: str) -> bool:
        left_norm = cls._normalize_topic(left)
        right_norm = cls._normalize_topic(right)
        if not left_norm or not right_norm:
            return False
        if left_norm == right_norm:
            return True
        if left_norm in right_norm or right_norm in left_norm:
            return True
        left_paths = set(re.findall(r"/[\w./-]+", left))
        right_paths = set(re.findall(r"/[\w./-]+", right))
        if left_paths & right_paths:
            return True
        left_bigrams = {left_norm[index:index + 2] for index in range(max(0, len(left_norm) - 1))}
        right_bigrams = {right_norm[index:index + 2] for index in range(max(0, len(right_norm) - 1))}
        overlap = left_bigrams & right_bigrams
        return len(overlap) >= 2


def group_entries(entries: List[MemoryEntry]) -> Dict[str, List[MemoryEntry]]:
    grouped: Dict[str, List[MemoryEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.category].append(entry)
    return dict(grouped)
