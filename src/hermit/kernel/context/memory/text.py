from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from hermit.infra.system.i18n import tr, tr_list_all_locales
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry


def _topic_stopwords() -> set[str]:
    return set(tr_list_all_locales("kernel.nlp.topic_stopwords"))


def _directional_terms() -> tuple[str, ...]:
    return tuple(tr_list_all_locales("kernel.nlp.directional_terms"))


def summary_prompt(
    categories: dict[str, list[MemoryEntry]],
    *,
    limit_per_category: int = 3,
    intro: str = "",
) -> str:
    if not intro:
        intro = tr("kernel.memory.static_intro")
    if not any(entries for entries in categories.values()):
        return ""
    lines = [intro]
    for category, entries in categories.items():
        if not entries:
            continue
        lines.append(f"\n## {category}")
        for entry in entries[:limit_per_category]:
            lines.append(entry.render())
    return "\n".join(lines).strip()


def topic_tokens(content: str) -> set[str]:
    raw_tokens = re.findall(r"[\w\-/\.]{2,}|[\u4e00-\u9fff]{2,}", str(content or "").lower())
    stopwords = _topic_stopwords()
    return {token for token in raw_tokens if token not in stopwords}


def normalize_topic(content: str) -> str:
    text = str(content or "").lower()
    text = re.sub(r"/[\w./-]+", "<path>", text)
    text = re.sub(r"\d+(?:\.\d+)?", "<num>", text)
    for word in _directional_terms():
        text = text.replace(word, "")
    text = re.sub(r"[^\w\u4e00-\u9fff<>]+", "", text)
    return text


def shares_topic(left: str, right: str) -> bool:
    left_norm = normalize_topic(left)
    right_norm = normalize_topic(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    if left_norm in right_norm or right_norm in left_norm:
        return True
    left_paths = set(re.findall(r"/[\w./-]+", str(left or "")))
    right_paths = set(re.findall(r"/[\w./-]+", str(right or "")))
    if left_paths & right_paths:
        return True
    left_bigrams = {left_norm[index : index + 2] for index in range(max(0, len(left_norm) - 1))}
    right_bigrams = {right_norm[index : index + 2] for index in range(max(0, len(right_norm) - 1))}
    return len(left_bigrams & right_bigrams) >= 2


def is_duplicate(entries: Iterable[Any], content: str) -> bool:
    normalized = str(content or "").strip().lower()
    for existing in entries:
        other_raw = getattr(existing, "content", existing)
        other = str(other_raw or "").strip().lower()
        shorter = min(len(normalized), len(other))
        longer = max(len(normalized), len(other))
        overlap_ratio = shorter / longer if longer else 1
        if normalized == other:
            return True
        if overlap_ratio >= 0.6 and (normalized in other or other in normalized):
            return True
    return False


def looks_like_override(old_content: str, new_content: str) -> bool:
    if not shares_topic(old_content, new_content):
        return False
    old_numbers = set(re.findall(r"\d+(?:\.\d+)?", str(old_content or "")))
    new_numbers = set(re.findall(r"\d+(?:\.\d+)?", str(new_content or "")))
    if old_numbers != new_numbers and (old_numbers or new_numbers):
        return True
    old_paths = set(re.findall(r"/[\w./-]+", str(old_content or "")))
    new_paths = set(re.findall(r"/[\w./-]+", str(new_content or "")))
    if old_paths != new_paths and (old_paths or new_paths):
        return True
    return any(term in str(new_content or "") for term in _directional_terms())
