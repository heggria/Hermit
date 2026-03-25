from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class InfluenceLink:
    """Records that a memory influenced a specific decision."""

    link_id: str
    context_pack_id: str
    decision_id: str
    memory_id: str
    created_at: float


@dataclass
class DecisionLineage:
    """Trace of all memories that influenced a decision."""

    decision_id: str
    influencing_memories: list[str] = field(default_factory=list)
    context_pack_ids: list[str] = field(default_factory=list)
    link_count: int = 0


@dataclass
class MemoryImpact:
    """Trace of all decisions influenced by a memory."""

    memory_id: str
    influenced_decisions: list[str] = field(default_factory=list)
    total_decisions: int = 0
    success_count: int = 0
    failure_count: int = 0
    failure_rate: float = 0.0


@dataclass(frozen=True)
class StaleMemory:
    """A memory identified as having high failure influence."""

    memory_id: str
    claim_text: str
    decision_count: int
    failure_rate: float
    category: str


__all__ = [
    "DecisionLineage",
    "InfluenceLink",
    "MemoryImpact",
    "StaleMemory",
]
