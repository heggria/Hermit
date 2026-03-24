from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()

_DEFAULT_MAX_TOKENS = 4000
_CHARS_PER_TOKEN_ESTIMATE = 4


@dataclass
class WorkingMemoryItem:
    """A single item selected for working memory context."""

    memory_id: str
    claim_text: str
    category: str
    priority: str  # pitfall | procedural | static | retrieved
    estimated_tokens: int


@dataclass
class WorkingMemoryPack:
    """The result of working memory selection."""

    # Use plain `list` as the factory; `list[WorkingMemoryItem]()` is a generic-alias
    # call that works on CPython by accident but is semantically incorrect.
    items: list[WorkingMemoryItem] = field(default_factory=list)
    total_tokens: int = 0
    budget_used_pct: float = 0.0
    overflow_count: int = 0
    overflow_footer: str = ""


class WorkingMemoryManager:
    """Bounded working memory context manager.

    Selects and prioritizes memories for context injection with a
    fixed token budget, inspired by Letta's bounded context approach.

    Priority order:
    1. PITFALL warnings (highest priority)
    2. Procedural matches
    3. Static memories (by freshness)
    4. Retrieved memories (by relevance)
    5. Overflow → truncated with footer
    """

    def __init__(self, max_tokens: int = _DEFAULT_MAX_TOKENS) -> None:
        self._max_tokens = max_tokens

    def select_for_context(
        self,
        *,
        static: list[MemoryRecord] | None = None,
        retrieved: list[MemoryRecord] | None = None,
        procedural: list[dict[str, Any]] | None = None,
        pitfalls: list[MemoryRecord] | None = None,
    ) -> WorkingMemoryPack:
        """Select memories for context injection within token budget.

        Args:
            static: Statically injected memories (user_preference etc.)
            retrieved: Query-relevant retrieved memories
            procedural: Matched procedural records as dicts
            pitfalls: PITFALL warning memories
        """
        pack = WorkingMemoryPack()
        budget_remaining = self._max_tokens
        all_overflow: list[str] = []

        # Priority 1: PITFALL warnings
        for memory in pitfalls or []:
            item = self._make_item(memory, "pitfall")
            if budget_remaining >= item.estimated_tokens:
                pack.items.append(item)
                budget_remaining -= item.estimated_tokens
            else:
                all_overflow.append(memory.memory_id)

        # Priority 2: Procedural matches
        for proc in procedural or []:
            text = str(proc.get("trigger_pattern", "")) + ": " + ", ".join(proc.get("steps", []))
            tokens = self._estimate_tokens(text)
            if budget_remaining >= tokens:
                item = WorkingMemoryItem(
                    memory_id=str(proc.get("procedure_id", "")),
                    claim_text=text,
                    category="procedural",
                    priority="procedural",
                    estimated_tokens=tokens,
                )
                pack.items.append(item)
                budget_remaining -= tokens
            else:
                all_overflow.append(str(proc.get("procedure_id", "")))

        # Priority 3: Static memories (sorted by freshness)
        static_sorted = sorted(
            static or [],
            key=lambda m: m.updated_at or m.created_at or 0.0,
            reverse=True,
        )
        for memory in static_sorted:
            item = self._make_item(memory, "static")
            if budget_remaining >= item.estimated_tokens:
                pack.items.append(item)
                budget_remaining -= item.estimated_tokens
            else:
                all_overflow.append(memory.memory_id)

        # Priority 4: Retrieved memories (assumed pre-sorted by relevance)
        for memory in retrieved or []:
            item = self._make_item(memory, "retrieved")
            if budget_remaining >= item.estimated_tokens:
                pack.items.append(item)
                budget_remaining -= item.estimated_tokens
            else:
                all_overflow.append(memory.memory_id)

        pack.overflow_count = len(all_overflow)
        pack.total_tokens = self._max_tokens - budget_remaining
        pack.budget_used_pct = (
            round(pack.total_tokens / self._max_tokens * 100, 1) if self._max_tokens > 0 else 0.0
        )

        if pack.overflow_count > 0:
            pack.overflow_footer = (
                f"({pack.overflow_count} additional memories available "
                f"but excluded due to token budget)"
            )

        return pack

    @staticmethod
    def _make_item(memory: MemoryRecord, priority: str) -> WorkingMemoryItem:
        # Delegate to _estimate_tokens rather than duplicating the ceiling-division formula.
        tokens = WorkingMemoryManager._estimate_tokens(memory.claim_text)
        return WorkingMemoryItem(
            memory_id=memory.memory_id,
            claim_text=memory.claim_text,
            category=memory.category,
            priority=priority,
            estimated_tokens=tokens,
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, -(-len(text) // _CHARS_PER_TOKEN_ESTIMATE))


__all__ = ["WorkingMemoryManager", "WorkingMemoryPack"]
