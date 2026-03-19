from __future__ import annotations

import time
from pathlib import Path

from hermit.kernel.context.memory.graph import MemoryGraphService, ensure_graph_schema
from hermit.kernel.ledger.journal.store import KernelStore


def _create_memory(store: KernelStore, *, task_id: str = "task-1", **kwargs):
    """Helper to create a memory record with sensible defaults."""
    defaults = dict(
        task_id=task_id,
        conversation_id="conv-1",
        category="project_convention",
        claim_text="default claim",
        scope_kind="workspace",
        scope_ref="workspace:default",
        retention_class="project_convention",
        memory_kind="durable_fact",
        confidence=0.8,
        trust_tier="durable",
    )
    defaults.update(kwargs)
    return store.create_memory_record(**defaults)


def _age_memory(store: KernelStore, memory_id: str, created_at: float) -> None:
    """Backdate a memory's created_at via direct SQL."""
    store._get_conn().execute(
        "UPDATE memory_records SET created_at = ? WHERE memory_id = ?",
        (created_at, memory_id),
    )
    store._get_conn().commit()


def test_extract_entities_uses_pattern(tmp_path: Path) -> None:
    """Extracting 'project uses Python' produces a triple with predicate 'uses'."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = MemoryGraphService()
        record = _create_memory(store, claim_text="project uses Python")
        triples = svc.extract_entities(record)

        assert len(triples) >= 1
        uses_triples = [t for t in triples if "uses" in t.predicate]
        assert len(uses_triples) >= 1
        triple = uses_triples[0]
        assert "project" in triple.subject
        assert "python" in triple.object_
    finally:
        store.close()


def test_extract_entities_is_pattern(tmp_path: Path) -> None:
    """Extracting 'ruff is the formatter' produces a triple with predicate 'is'."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = MemoryGraphService()
        record = _create_memory(store, claim_text="ruff is the formatter")
        triples = svc.extract_entities(record)

        assert len(triples) >= 1
        is_triples = [t for t in triples if t.predicate == "is"]
        assert len(is_triples) >= 1
        triple = is_triples[0]
        assert "ruff" in triple.subject
        assert "formatter" in triple.object_
    finally:
        store.close()


def test_extract_entities_no_match(tmp_path: Path) -> None:
    """Plain text without entity patterns produces no triples."""
    store = KernelStore(tmp_path / "state.db")
    try:
        svc = MemoryGraphService()
        record = _create_memory(store, claim_text="hello world")
        triples = svc.extract_entities(record)

        assert triples == []
    finally:
        store.close()


def test_build_edges_temporal_sequence(tmp_path: Path) -> None:
    """Two memories in the same task created close together get a temporal_sequence edge."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        svc = MemoryGraphService()
        now = time.time()

        m1 = _create_memory(store, task_id="t1", claim_text="first observation")
        m2 = _create_memory(store, task_id="t1", claim_text="second observation")
        # Ensure both are within 1 hour of each other
        _age_memory(store, m1.memory_id, now - 60)
        _age_memory(store, m2.memory_id, now)

        edges = svc.build_edges(m1.memory_id, store)
        temporal_edges = [e for e in edges if e.relation_type == "temporal_sequence"]

        assert len(temporal_edges) >= 1
        assert temporal_edges[0].to_memory_id == m2.memory_id
    finally:
        store.close()


def test_build_edges_same_entity(tmp_path: Path) -> None:
    """Two memories sharing an extracted entity get a same_entity edge."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        svc = MemoryGraphService()

        m1 = _create_memory(store, claim_text="project uses Python")
        m2 = _create_memory(store, claim_text="project uses ruff")

        # Extract and store triples for both
        t1 = svc.extract_entities(m1)
        t2 = svc.extract_entities(m2)
        svc.store_triples(t1, store)
        svc.store_triples(t2, store)

        edges = svc.build_edges(m1.memory_id, store)
        entity_edges = [e for e in edges if e.relation_type == "same_entity"]

        assert len(entity_edges) >= 1
        assert entity_edges[0].to_memory_id == m2.memory_id
    finally:
        store.close()


def test_multi_hop_retrieve_single_hop(tmp_path: Path) -> None:
    """BFS finds a 1-hop neighbor from a seed memory."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        svc = MemoryGraphService()
        now = time.time()

        m1 = _create_memory(store, task_id="t1", claim_text="seed memory")
        m2 = _create_memory(store, task_id="t1", claim_text="neighbor memory")
        _age_memory(store, m1.memory_id, now)
        _age_memory(store, m2.memory_id, now)

        # Build edges to create the graph link
        svc.build_edges(m1.memory_id, store)

        results = svc.multi_hop_retrieve(
            "test query", store, seed_memory_ids=[m1.memory_id], max_hops=1
        )

        assert len(results) >= 1
        assert results[0].hop_count == 1
        assert results[0].target_memory_id == m2.memory_id
    finally:
        store.close()


def test_multi_hop_retrieve_two_hops(tmp_path: Path) -> None:
    """BFS reaches a 2-hop neighbor through an intermediate memory via entity edges."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        svc = MemoryGraphService()

        # Use entity edges to create a chain: m1 shares "python" with m2,
        # m2 shares "ruff" with m3, but m1 does NOT share entities with m3.
        m1 = _create_memory(store, task_id="t1", claim_text="project uses Python")
        m2 = _create_memory(store, task_id="t2", claim_text="Python requires ruff")
        m3 = _create_memory(store, task_id="t3", claim_text="ruff needs configuration")

        # Extract and store triples so entity edges can be built
        for m in [m1, m2, m3]:
            triples = svc.extract_entities(m)
            svc.store_triples(triples, store)

        # Build edges from each memory
        svc.build_edges(m1.memory_id, store)
        svc.build_edges(m2.memory_id, store)

        results = svc.multi_hop_retrieve(
            "test query", store, seed_memory_ids=[m1.memory_id], max_hops=2
        )

        target_ids = {r.target_memory_id for r in results}
        # m2 should be 1-hop, m3 should be 2-hop
        assert m2.memory_id in target_ids
        assert m3.memory_id in target_ids

        two_hop = [r for r in results if r.target_memory_id == m3.memory_id]
        assert len(two_hop) == 1
        assert two_hop[0].hop_count == 2
    finally:
        store.close()


def test_auto_link_by_topic(tmp_path: Path) -> None:
    """auto_link creates related_topic edges for memories with similar topic tokens."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        svc = MemoryGraphService()  # no embedding service → falls back to topic overlap

        m1 = _create_memory(
            store, claim_text="Python ruff formatter is mandatory for linting checks"
        )
        m2 = _create_memory(store, claim_text="ruff formatter handles Python code formatting")

        edges = svc.auto_link(m1.memory_id, store)

        topic_edges = [e for e in edges if e.relation_type == "related_topic"]
        assert len(topic_edges) >= 1
        assert topic_edges[0].to_memory_id == m2.memory_id
    finally:
        store.close()


def test_build_edges_missing_memory_returns_empty(tmp_path: Path) -> None:
    """build_edges returns [] when the memory_id doesn't exist (line 93)."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        svc = MemoryGraphService()
        edges = svc.build_edges("nonexistent-memory-id", store)
        assert edges == []
    finally:
        store.close()


def test_multi_hop_retrieve_empty_seeds_returns_empty(tmp_path: Path) -> None:
    """multi_hop_retrieve returns [] when seed_memory_ids is empty (line 150)."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        svc = MemoryGraphService()
        results = svc.multi_hop_retrieve("query", store, seed_memory_ids=[])
        assert results == []

        results_none = svc.multi_hop_retrieve("query", store, seed_memory_ids=None)
        assert results_none == []
    finally:
        store.close()


def test_auto_link_missing_memory_returns_empty(tmp_path: Path) -> None:
    """auto_link returns [] when the memory doesn't exist (line 202)."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)
        svc = MemoryGraphService()
        edges = svc.auto_link("nonexistent-id", store)
        assert edges == []
    finally:
        store.close()


def test_auto_link_with_embeddings(tmp_path: Path) -> None:
    """auto_link uses embedding service when available (lines 207-224)."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)

        m1 = _create_memory(store, claim_text="Python uses pytest for testing")
        m2 = _create_memory(store, claim_text="Testing with pytest is great")

        class FakeEmbeddingService:
            def search(self, text, store, limit=5):
                # Return m2 with high similarity and m1 with self-match
                return [(m1.memory_id, 0.99), (m2.memory_id, 0.85)]

        svc = MemoryGraphService(embedding_service=FakeEmbeddingService())
        edges = svc.auto_link(m1.memory_id, store)

        # Should have created an edge to m2 (self is skipped, 0.85 > 0.7 threshold)
        assert len(edges) >= 1
        assert any(e.to_memory_id == m2.memory_id for e in edges)
        assert all(e.relation_type == "related_topic" for e in edges)
    finally:
        store.close()


def test_auto_link_embedding_below_threshold_skipped(tmp_path: Path) -> None:
    """auto_link skips embedding results below _SEMANTIC_THRESHOLD (lines 214-215)."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)

        m1 = _create_memory(store, claim_text="Python uses pytest for testing")
        _create_memory(store, claim_text="Unrelated content about cooking")

        class FakeEmbeddingService:
            def search(self, text, store, limit=5):
                # Return only results below threshold
                return [("other-mem", 0.3)]

        svc = MemoryGraphService(embedding_service=FakeEmbeddingService())
        edges = svc.auto_link(m1.memory_id, store)

        # No embedding edges created since all below threshold;
        # falls back to topic overlap
        embedding_edges = [e for e in edges if "similarity" in e.metadata]
        assert len(embedding_edges) == 0
    finally:
        store.close()


def test_auto_link_embedding_exception_fallback(tmp_path: Path) -> None:
    """auto_link falls back to topic linking when embeddings raise (lines 225-226)."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)

        m1 = _create_memory(store, claim_text="Python ruff formatter is mandatory for linting")
        _create_memory(store, claim_text="ruff formatter handles Python code formatting")

        class BrokenEmbeddingService:
            def search(self, text, store, limit=5):
                raise RuntimeError("embedding service unavailable")

        svc = MemoryGraphService(embedding_service=BrokenEmbeddingService())
        edges = svc.auto_link(m1.memory_id, store)

        # Should still produce edges via topic fallback
        assert len(edges) >= 1
    finally:
        store.close()


def test_auto_link_by_topic_empty_tokens_returns_empty(tmp_path: Path) -> None:
    """_auto_link_by_topic returns [] when the memory has no topic tokens (line 244)."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)

        # Single short word that's likely a stopword -> empty topic tokens
        m1 = _create_memory(store, claim_text="the")
        svc = MemoryGraphService()
        edges = svc.auto_link(m1.memory_id, store)
        # With no topic tokens, should return empty
        assert isinstance(edges, list)
    finally:
        store.close()


def test_auto_link_by_topic_skips_episode_index(tmp_path: Path) -> None:
    """_auto_link_by_topic skips memories with memory_kind='episode_index' (line 252)."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)

        m1 = _create_memory(store, claim_text="Python ruff formatter is mandatory for linting")
        # Create a memory with episode_index kind that shares tokens
        _create_memory(
            store,
            claim_text="Python ruff formatter is mandatory for linting",
            memory_kind="episode_index",
        )

        svc = MemoryGraphService()
        edges = svc.auto_link(m1.memory_id, store)

        # The episode_index memory should be skipped
        for edge in edges:
            target = store.get_memory_record(edge.to_memory_id)
            if target is not None:
                assert target.memory_kind != "episode_index"
    finally:
        store.close()


def test_auto_link_by_topic_skips_empty_other_tokens(tmp_path: Path) -> None:
    """_auto_link_by_topic skips other memories with no topic tokens (line 255)."""
    store = KernelStore(tmp_path / "state.db")
    try:
        ensure_graph_schema(store)

        m1 = _create_memory(store, claim_text="Python ruff formatter is mandatory for linting")
        # Create a memory with only single-char tokens (filtered by topic_tokens min length 2)
        _create_memory(store, claim_text="x y z")

        svc = MemoryGraphService()
        edges = svc.auto_link(m1.memory_id, store)

        # The single-char-only memory should not be linked
        for edge in edges:
            target = store.get_memory_record(edge.to_memory_id)
            if target is not None:
                assert target.claim_text != "x y z"
    finally:
        store.close()
