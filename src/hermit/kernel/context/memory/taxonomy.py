from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermit.kernel.task.models.records import MemoryRecord


class MemoryType(StrEnum):
    """Cognitive memory classification following CoALA architecture."""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    WORKING = "working"


def classify_memory_type(record: MemoryRecord) -> MemoryType:
    """Classify a memory record into cognitive memory type.

    Classification rules:
    - episode_index → EPISODIC
    - procedural memory_kind or "how-to" patterns → PROCEDURAL
    - influence_link, pitfall_warning → SEMANTIC
    - volatile facts with short TTL → WORKING
    - everything else → SEMANTIC
    """
    kind = record.memory_kind or "durable_fact"

    if kind == "episode_index":
        return MemoryType.EPISODIC

    if kind == "procedural":
        return MemoryType.PROCEDURAL

    if _is_procedural_text(record.claim_text):
        return MemoryType.PROCEDURAL

    if (
        record.retention_class in {"task_state", "volatile_fact"}
        and record.scope_kind == "conversation"
    ):
        return MemoryType.WORKING

    return MemoryType.SEMANTIC


def _is_procedural_text(text: str | None) -> bool:
    """Heuristic detection of procedural/how-to content.

    Returns False immediately when *text* is None so callers never need
    to guard the value before passing it in.
    """
    if text is None:
        return False

    lower = text.lower()
    markers = [
        "to do this",
        "first,",
        "step 1",
        "step 2",
        "then ",
        "how to",
        "in order to",
        "procedure:",
        "workflow:",
        "run ",
        "execute ",
    ]
    return any(marker in lower for marker in markers)


__all__ = ["MemoryType", "classify_memory_type"]
