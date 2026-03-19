from __future__ import annotations

import re
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any

import structlog

from hermit.kernel.context.memory.graph_models import (
    EntityTriple,
    GraphEdge,
    GraphQueryResult,
)

if TYPE_CHECKING:
    from hermit.kernel.context.memory.embeddings import EmbeddingService
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()

# Patterns for entity extraction
_ENTITY_PATTERNS = [
    # "X uses Y", "X prefers Y", "X requires Y"
    (r"(\b\w[\w\s]{1,30})\s+(uses?|prefers?|requires?|needs?)\s+(\w[\w\s./:-]{1,40})", "uses"),
    # "X is Y"
    (r"(\b\w[\w\s]{1,30})\s+(?:is|are)\s+(\w[\w\s]{1,40})", "is"),
    # paths like /foo/bar
    (r"([\w.-]+)\s+(?:at|in|from)\s+((?:/[\w./-]+)+)", "located_at"),
]

_AUTO_LINK_TOP_N = 5
_SEMANTIC_THRESHOLD = 0.7


class MemoryGraphService:
    """Memory relationship graph with entity triples and multi-hop retrieval.

    Stores edges and triples in dedicated SQLite tables, with BFS-based
    multi-hop traversal and Zettelkasten-style auto-linking.
    """

    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self._embeddings = embedding_service

    def extract_entities(self, memory: MemoryRecord) -> list[EntityTriple]:
        """Extract (subject, predicate, object) triples from memory text."""
        text = memory.claim_text
        now = memory.created_at or time.time()
        triples: list[EntityTriple] = []

        for pattern, default_pred in _ENTITY_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                groups = match.groups()
                if len(groups) >= 3:
                    subj, pred_text, obj = groups[0].strip(), groups[1].strip(), groups[2].strip()
                elif len(groups) == 2:
                    subj, obj = groups[0].strip(), groups[1].strip()
                    pred_text = default_pred
                else:
                    continue

                triple = EntityTriple(
                    triple_id=f"tri-{uuid.uuid4().hex[:12]}",
                    source_memory_id=memory.memory_id,
                    subject=subj.lower(),
                    predicate=pred_text.lower(),
                    object_=obj.lower(),
                    confidence=memory.confidence,
                    valid_from=now,
                    created_at=time.time(),
                )
                triples.append(triple)

        return triples

    def build_edges(
        self,
        memory_id: str,
        store: KernelStore,
    ) -> list[GraphEdge]:
        """Build graph edges from a memory to related memories.

        Relation types:
        - same_entity: share extracted entity
        - related_topic: semantic similarity (if embeddings available)
        - temporal_sequence: created within 1 hour of each other in same task
        """
        record = store.get_memory_record(memory_id)
        if record is None:
            return []

        edges: list[GraphEdge] = []
        all_records = store.list_memory_records(status="active", limit=2000)
        other_records = [r for r in all_records if r.memory_id != memory_id]

        # Same entity edges
        my_triples = self._get_triples_for_memory(memory_id, store)
        my_entities = {t.subject for t in my_triples} | {t.object_ for t in my_triples}

        if my_entities:
            for other in other_records:
                other_triples = self._get_triples_for_memory(other.memory_id, store)
                other_entities = {t.subject for t in other_triples} | {
                    t.object_ for t in other_triples
                }
                shared = my_entities & other_entities
                if shared:
                    edge = self._create_edge(
                        memory_id,
                        other.memory_id,
                        "same_entity",
                        weight=len(shared),
                        metadata={"shared_entities": sorted(shared)[:5]},
                        store=store,
                    )
                    edges.append(edge)

        # Temporal sequence edges (same task, within 1 hour)
        for other in other_records:
            if other.task_id != record.task_id:
                continue
            time_diff = abs((record.created_at or 0) - (other.created_at or 0))
            if time_diff < 3600:
                edge = self._create_edge(
                    memory_id,
                    other.memory_id,
                    "temporal_sequence",
                    weight=max(0.1, 1.0 - time_diff / 3600),
                    store=store,
                )
                edges.append(edge)

        log.debug("edges_built", memory_id=memory_id, edge_count=len(edges))
        return edges

    def multi_hop_retrieve(
        self,
        query: str,
        store: KernelStore,
        *,
        seed_memory_ids: list[str] | None = None,
        max_hops: int = 2,
        limit: int = 10,
    ) -> list[GraphQueryResult]:
        """BFS from seed memories, expanding along graph edges."""
        if not seed_memory_ids:
            return []

        visited: set[str] = set()
        results: list[GraphQueryResult] = []
        queue: deque[tuple[str, list[str], float, int]] = deque()

        for seed in seed_memory_ids:
            queue.append((seed, [seed], 1.0, 0))
            visited.add(seed)

        while queue and len(results) < limit:
            current_id, path, weight, hops = queue.popleft()

            if hops > 0:
                results.append(
                    GraphQueryResult(
                        path=path,
                        target_memory_id=current_id,
                        hop_count=hops,
                        aggregate_weight=weight,
                    )
                )

            if hops >= max_hops:
                continue

            edges = self._get_edges_from(current_id, store)
            for edge in edges:
                neighbor = edge.to_memory_id
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append(
                    (
                        neighbor,
                        path + [neighbor],
                        weight * edge.weight,
                        hops + 1,
                    )
                )

        results.sort(key=lambda r: r.aggregate_weight, reverse=True)
        return results[:limit]

    def auto_link(
        self,
        new_memory_id: str,
        store: KernelStore,
    ) -> list[GraphEdge]:
        """Zettelkasten-style auto-linking: find top-N similar memories and create edges."""
        record = store.get_memory_record(new_memory_id)
        if record is None:
            return []

        edges: list[GraphEdge] = []

        if self._embeddings is not None:
            try:
                results = self._embeddings.search(
                    record.claim_text, store, limit=_AUTO_LINK_TOP_N + 1
                )
                for mid, sim in results:
                    if mid == new_memory_id:
                        continue
                    if sim < _SEMANTIC_THRESHOLD:
                        continue
                    edge = self._create_edge(
                        new_memory_id,
                        mid,
                        "related_topic",
                        weight=sim,
                        metadata={"similarity": round(sim, 4)},
                        store=store,
                    )
                    edges.append(edge)
            except Exception:
                log.debug("auto_link_embedding_fallback", memory_id=new_memory_id)

        if not edges:
            edges = self._auto_link_by_topic(record, store)

        log.debug("auto_linked", memory_id=new_memory_id, link_count=len(edges))
        return edges

    def _auto_link_by_topic(
        self,
        record: MemoryRecord,
        store: KernelStore,
    ) -> list[GraphEdge]:
        """Fallback auto-linking by topic token overlap."""
        from hermit.kernel.context.memory.text import shares_topic, topic_tokens

        my_tokens = set(topic_tokens(record.claim_text))
        if not my_tokens:
            return []

        all_records = store.list_memory_records(status="active", limit=2000)
        scored: list[tuple[str, float]] = []
        for other in all_records:
            if other.memory_id == record.memory_id:
                continue
            if other.memory_kind in {"episode_index", "influence_link"}:
                continue
            other_tokens = set(topic_tokens(other.claim_text))
            if not other_tokens:
                continue
            overlap = len(my_tokens & other_tokens)
            if overlap >= 2 or shares_topic(record.claim_text, other.claim_text):
                score = overlap / max(len(my_tokens | other_tokens), 1)
                scored.append((other.memory_id, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        edges: list[GraphEdge] = []
        for mid, score in scored[:_AUTO_LINK_TOP_N]:
            edge = self._create_edge(
                record.memory_id,
                mid,
                "related_topic",
                weight=score,
                store=store,
            )
            edges.append(edge)
        return edges

    def _create_edge(
        self,
        from_id: str,
        to_id: str,
        relation_type: str,
        *,
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
        store: KernelStore,
    ) -> GraphEdge:
        edge_id = f"edge-{uuid.uuid4().hex[:12]}"
        now = time.time()
        edge = GraphEdge(
            edge_id=edge_id,
            from_memory_id=from_id,
            to_memory_id=to_id,
            relation_type=relation_type,
            weight=weight,
            metadata=metadata or {},
            created_at=now,
        )
        _store_edge(edge, store)
        return edge

    def _get_edges_from(self, memory_id: str, store: KernelStore) -> list[GraphEdge]:
        return _load_edges_from(memory_id, store)

    def _get_triples_for_memory(self, memory_id: str, store: KernelStore) -> list[EntityTriple]:
        return _load_triples_for(memory_id, store)

    def store_triples(
        self,
        triples: list[EntityTriple],
        store: KernelStore,
    ) -> None:
        """Persist extracted entity triples."""
        for triple in triples:
            _store_triple(triple, store)


def ensure_graph_schema(store: KernelStore) -> None:
    """Create graph tables if they don't exist."""
    with store._get_conn():
        store._get_conn().executescript(  # pyright: ignore[reportPrivateUsage]
            """
            CREATE TABLE IF NOT EXISTS memory_graph_edges (
                edge_id TEXT PRIMARY KEY,
                from_memory_id TEXT NOT NULL,
                to_memory_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_graph_edges_from
                ON memory_graph_edges(from_memory_id);
            CREATE INDEX IF NOT EXISTS idx_graph_edges_to
                ON memory_graph_edges(to_memory_id);

            CREATE TABLE IF NOT EXISTS memory_entity_triples (
                triple_id TEXT PRIMARY KEY,
                source_memory_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                valid_from REAL NOT NULL,
                valid_until REAL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_triples_subject
                ON memory_entity_triples(subject);
            CREATE INDEX IF NOT EXISTS idx_triples_object
                ON memory_entity_triples(object);
            CREATE INDEX IF NOT EXISTS idx_triples_source
                ON memory_entity_triples(source_memory_id);
            """
        )


def _store_edge(edge: GraphEdge, store: KernelStore) -> None:
    import json

    with store._get_conn():
        store._get_conn().execute(  # pyright: ignore[reportPrivateUsage]
            """
            INSERT OR REPLACE INTO memory_graph_edges
                (edge_id, from_memory_id, to_memory_id, relation_type, weight, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.edge_id,
                edge.from_memory_id,
                edge.to_memory_id,
                edge.relation_type,
                edge.weight,
                json.dumps(edge.metadata),
                edge.created_at,
            ),
        )


def _load_edges_from(memory_id: str, store: KernelStore) -> list[GraphEdge]:
    import json

    rows = (
        store._get_conn()
        .execute(
            "SELECT * FROM memory_graph_edges WHERE from_memory_id = ?",
            (memory_id,),
        )
        .fetchall()
    )

    edges: list[GraphEdge] = []
    for row in rows:
        edges.append(
            GraphEdge(
                edge_id=str(row["edge_id"]),
                from_memory_id=str(row["from_memory_id"]),
                to_memory_id=str(row["to_memory_id"]),
                relation_type=str(row["relation_type"]),
                weight=float(row["weight"]),
                metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                created_at=float(row["created_at"]),
            )
        )
    return edges


def _store_triple(triple: EntityTriple, store: KernelStore) -> None:
    with store._get_conn():
        store._get_conn().execute(  # pyright: ignore[reportPrivateUsage]
            """
            INSERT OR REPLACE INTO memory_entity_triples
                (triple_id, source_memory_id, subject, predicate, object, confidence, valid_from, valid_until, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                triple.triple_id,
                triple.source_memory_id,
                triple.subject,
                triple.predicate,
                triple.object_,
                triple.confidence,
                triple.valid_from,
                triple.valid_until,
                triple.created_at,
            ),
        )


def _load_triples_for(memory_id: str, store: KernelStore) -> list[EntityTriple]:
    rows = (
        store._get_conn()
        .execute(
            "SELECT * FROM memory_entity_triples WHERE source_memory_id = ?",
            (memory_id,),
        )
        .fetchall()
    )

    triples: list[EntityTriple] = []
    for row in rows:
        triples.append(
            EntityTriple(
                triple_id=str(row["triple_id"]),
                source_memory_id=str(row["source_memory_id"]),
                subject=str(row["subject"]),
                predicate=str(row["predicate"]),
                object_=str(row["object"]),
                confidence=float(row["confidence"]),
                valid_from=float(row["valid_from"]),
                valid_until=float(row["valid_until"]) if row["valid_until"] else None,
                created_at=float(row["created_at"]),
            )
        )
    return triples


__all__ = ["MemoryGraphService", "ensure_graph_schema"]
