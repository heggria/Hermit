from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EntityTriple:
    """A subject-predicate-object triple extracted from a memory."""

    triple_id: str
    source_memory_id: str
    subject: str
    predicate: str
    object_: str
    confidence: float = 0.5
    valid_from: float = 0.0
    valid_until: float | None = None
    created_at: float = 0.0


@dataclass(frozen=True)
class GraphEdge:
    """A directed edge between two memories in the relationship graph."""

    edge_id: str
    from_memory_id: str
    to_memory_id: str
    relation_type: (
        str  # same_entity | related_topic | causal | temporal_sequence | contradicts | elaborates
    )
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0


@dataclass
class GraphQueryResult:
    """Result of a graph traversal query."""

    path: list[str]
    target_memory_id: str
    hop_count: int
    aggregate_weight: float


__all__ = [
    "EntityTriple",
    "GraphEdge",
    "GraphQueryResult",
]
