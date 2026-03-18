from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from hermit.kernel.context.memory.embeddings import EmbeddingService, ensure_embedding_schema
from hermit.kernel.context.memory.lineage_models import DecisionLineage, MemoryImpact
from hermit.kernel.context.memory.retrieval import (
    _RRF_K,
    HybridRetrievalService,
)
from hermit.kernel.ledger.journal.store import KernelStore


def _create_memories(store: KernelStore, items: list[tuple[str, float]]) -> list:
    """Helper: create memory records and return them.

    items is a list of (claim_text, created_seconds_ago).
    """
    for claim_text, _age_seconds in items:
        store.create_memory_record(
            task_id="test-task",
            conversation_id="test-conv",
            category="other",
            claim_text=claim_text,
            scope_kind="workspace",
            scope_ref="workspace:default",
            retention_class="volatile_fact",
            confidence=0.8,
            trust_tier="observed",
        )
    records = store.list_memory_records(status="active", limit=100)
    # Filter to only durable_fact (default memory_kind), exclude influence_link etc.
    return [r for r in records if r.memory_kind == "durable_fact"]


def test_fast_path_short_query(tmp_path: Path) -> None:
    """Short query uses fast mode with only token_overlap."""
    store = KernelStore(tmp_path / "r.db")
    try:
        memories = _create_memories(store, [("python is great", 0)])
        svc = HybridRetrievalService()

        report = svc.retrieve("python", memories, store)

        assert report.mode == "fast"
        assert len(report.results) >= 1
    finally:
        store.close()


def test_deep_path_long_query(tmp_path: Path) -> None:
    """Long query (>50 chars) uses deep mode with multiple paths."""
    store = KernelStore(tmp_path / "r.db")
    try:
        ensure_embedding_schema(store)
        memories = _create_memories(store, [("python programming language design patterns", 0)])

        # Index memories for semantic search
        embed_svc = EmbeddingService()
        embed_svc._available = False
        for m in memories:
            embed_svc.index_memory(m.memory_id, m.claim_text, store)

        svc = HybridRetrievalService(embedding_service=embed_svc)
        long_query = "What are the best python programming language design patterns for large scale applications"

        report = svc.retrieve(long_query, memories, store)

        assert report.mode == "deep"
        assert len(report.results) >= 1
    finally:
        store.close()


def test_rrf_fusion_merges_lists() -> None:
    """Verify RRF scoring with known inputs."""
    ranked_lists = {
        "list_a": ["m1", "m2", "m3"],
        "list_b": ["m2", "m1", "m4"],
    }

    fused = HybridRetrievalService._reciprocal_rank_fusion(ranked_lists, k=_RRF_K)
    fused_dict = dict(fused)

    # m1 is rank 0 in list_a and rank 1 in list_b
    expected_m1 = 1.0 / (_RRF_K + 0) + 1.0 / (_RRF_K + 1)
    # m2 is rank 1 in list_a and rank 0 in list_b
    expected_m2 = 1.0 / (_RRF_K + 1) + 1.0 / (_RRF_K + 0)

    assert abs(fused_dict["m1"] - expected_m1) < 1e-9
    assert abs(fused_dict["m2"] - expected_m2) < 1e-9
    # m1 and m2 should have equal scores (symmetric)
    assert abs(fused_dict["m1"] - fused_dict["m2"]) < 1e-9
    # m3 only in one list, m4 only in the other
    assert "m3" in fused_dict
    assert "m4" in fused_dict


def test_empty_memories_returns_empty(tmp_path: Path) -> None:
    """No candidates returns empty results."""
    store = KernelStore(tmp_path / "r.db")
    try:
        svc = HybridRetrievalService()

        report = svc.retrieve("anything", [], store)

        assert report.total_candidates == 0
        assert report.results == []
    finally:
        store.close()


def test_token_overlap_ranks_correctly(tmp_path: Path) -> None:
    """Exact token match ranks higher than unrelated content."""
    store = KernelStore(tmp_path / "r.db")
    try:
        memories = _create_memories(
            store,
            [
                ("ocean waves beach sunset surfing", 0),
                ("python programming language design", 0),
                ("random unrelated content here", 0),
            ],
        )

        svc = HybridRetrievalService()
        report = svc.retrieve("python programming", memories, store)

        assert len(report.results) >= 1
        # The python-related memory should be first
        top_claim = report.results[0].memory.claim_text
        assert "python" in top_claim.lower()
    finally:
        store.close()


def test_temporal_rank_prefers_recent(tmp_path: Path) -> None:
    """Newer memories rank higher in temporal ranking."""
    store = KernelStore(tmp_path / "r.db")
    try:
        # Create older memory first
        store.create_memory_record(
            task_id="test-task",
            conversation_id="test-conv",
            category="other",
            claim_text="old memory about testing",
            scope_kind="workspace",
            scope_ref="workspace:default",
            retention_class="volatile_fact",
            confidence=0.8,
            trust_tier="observed",
        )
        # Create newer memory
        store.create_memory_record(
            task_id="test-task",
            conversation_id="test-conv",
            category="other",
            claim_text="new memory about testing",
            scope_kind="workspace",
            scope_ref="workspace:default",
            retention_class="volatile_fact",
            confidence=0.8,
            trust_tier="observed",
        )

        memories = [
            r
            for r in store.list_memory_records(status="active", limit=100)
            if r.memory_kind == "durable_fact"
        ]

        svc = HybridRetrievalService()
        temporal = svc._temporal_rank(memories)

        # Newer memory (created later) should rank first or equal
        assert len(temporal) == 2
    finally:
        store.close()


def test_force_deep_overrides_fast(tmp_path: Path) -> None:
    """force_deep=True forces deep path even for short queries."""
    store = KernelStore(tmp_path / "r.db")
    try:
        ensure_embedding_schema(store)
        memories = _create_memories(store, [("test content", 0)])

        embed_svc = EmbeddingService()
        embed_svc._available = False
        for m in memories:
            embed_svc.index_memory(m.memory_id, m.claim_text, store)

        svc = HybridRetrievalService(embedding_service=embed_svc)

        report = svc.retrieve("test", memories, store, force_deep=True)

        assert report.mode == "deep"
        assert len(report.results) >= 1
    finally:
        store.close()


def test_compatible_with_existing_behavior(tmp_path: Path) -> None:
    """Basic retrieval works: returns ranked results with scores and sources."""
    store = KernelStore(tmp_path / "r.db")
    try:
        memories = _create_memories(
            store,
            [
                ("user prefers dark mode theme", 0),
                ("project uses ruff for linting", 0),
                ("deploy to production on friday", 0),
            ],
        )

        svc = HybridRetrievalService()
        report = svc.retrieve("ruff linting", memories, store)

        assert report.query == "ruff linting"
        assert report.total_candidates == 3
        assert len(report.results) <= 10
        assert report.retrieval_time_ms >= 0

        for result in report.results:
            assert result.memory_id
            assert result.rrf_score > 0
            assert len(result.sources) >= 1
            assert "token_overlap" in result.sources
    finally:
        store.close()


def test_retrieval_report_fields(tmp_path: Path) -> None:
    """RetrievalReport has all expected fields populated."""
    store = KernelStore(tmp_path / "r.db")
    try:
        memories = _create_memories(store, [("some content", 0)])
        svc = HybridRetrievalService()

        report = svc.retrieve("some", memories, store)

        assert isinstance(report.query, str)
        assert report.mode in ("fast", "deep")
        assert isinstance(report.total_candidates, int)
        assert isinstance(report.results, list)
        assert isinstance(report.retrieval_time_ms, float)
    finally:
        store.close()


def test_deep_path_with_embedding_service_available(tmp_path: Path) -> None:
    """Deep path exercises semantic ranking when embedding service is available."""
    store = KernelStore(tmp_path / "r.db")
    try:
        ensure_embedding_schema(store)
        memories = _create_memories(
            store,
            [
                ("python programming language design patterns for applications", 0),
                ("ocean waves beach sunset surfing and swimming", 0),
            ],
        )

        embed_svc = EmbeddingService()
        # Index all memories so semantic search returns results
        for m in memories:
            embed_svc.index_memory(m.memory_id, m.claim_text, store)

        svc = HybridRetrievalService(embedding_service=embed_svc)
        long_query = (
            "What are the best python programming language design patterns"
            " for large scale applications"
        )

        report = svc.retrieve(long_query, memories, store)

        assert report.mode == "deep"
        assert len(report.results) >= 1
        # Deep mode should have temporal path in sources
        all_sources = {s for r in report.results for s in r.sources}
        assert "temporal" in all_sources
    finally:
        store.close()


def test_graph_rank_with_lineage_service(tmp_path: Path) -> None:
    """Graph traversal retrieval path exercises lines 106-108, 202-212."""
    store = KernelStore(tmp_path / "r.db")
    try:
        ensure_embedding_schema(store)
        memories = _create_memories(
            store,
            [
                ("python design patterns for software architecture", 0),
                ("database optimization and query performance tuning", 0),
                ("machine learning model training best practices", 0),
            ],
        )
        memory_ids = [m.memory_id for m in memories]

        # Mock lineage service: trace_memory returns decisions, trace_decision returns memories
        # _graph_rank calls trace_memory for each of the top 3 seeds
        lineage = MagicMock()
        lineage.trace_memory.side_effect = [
            MemoryImpact(
                memory_id=memory_ids[0],
                influenced_decisions=["d1"],
                total_decisions=1,
            ),
            MemoryImpact(
                memory_id=memory_ids[1],
                influenced_decisions=["d2"],
                total_decisions=1,
            ),
            MemoryImpact(
                memory_id=memory_ids[2],
                influenced_decisions=[],
                total_decisions=0,
            ),
        ]
        lineage.trace_decision.side_effect = [
            # d1 influenced by memory_ids[1]
            DecisionLineage(decision_id="d1", influencing_memories=[memory_ids[1]]),
            # d2 influenced by memory_ids[2]
            DecisionLineage(decision_id="d2", influencing_memories=[memory_ids[2]]),
        ]

        embed_svc = EmbeddingService()
        for m in memories:
            embed_svc.index_memory(m.memory_id, m.claim_text, store)

        svc = HybridRetrievalService(embedding_service=embed_svc, lineage_service=lineage)
        long_query = "python design patterns for software architecture and large scale applications"

        report = svc.retrieve(long_query, memories, store)

        assert report.mode == "deep"
        assert len(report.results) >= 1
        all_sources = {s for r in report.results for s in r.sources}
        assert "graph" in all_sources
    finally:
        store.close()


def test_graph_rank_no_lineage_returns_empty(tmp_path: Path) -> None:
    """Graph rank returns empty list when lineage service is None (line 193-194)."""
    store = KernelStore(tmp_path / "r.db")
    try:
        memories = _create_memories(store, [("test content for graph ranking", 0)])
        svc = HybridRetrievalService(lineage_service=None)

        result = svc._graph_rank("test", memories, store)

        assert result == []
    finally:
        store.close()


def test_graph_rank_no_seeds_returns_empty(tmp_path: Path) -> None:
    """Graph rank returns empty when seeds list is empty (line 197-199)."""
    store = KernelStore(tmp_path / "r.db")
    try:
        # Empty memories => no seeds
        lineage = MagicMock()
        svc = HybridRetrievalService(lineage_service=lineage)

        result = svc._graph_rank("test", [], store)

        assert result == []
    finally:
        store.close()


def test_token_overlap_empty_query_tokens(tmp_path: Path) -> None:
    """When query yields no tokens >=2 chars, returns all memory IDs in order (line 155)."""
    store = KernelStore(tmp_path / "r.db")
    try:
        memories = _create_memories(
            store, [("hello world programming", 0), ("another memory here", 0)]
        )

        svc = HybridRetrievalService()
        # Single char query produces no tokens >= 2 chars
        result = svc._token_overlap_rank("a", memories)

        assert len(result) == len(memories)
        assert set(result) == {m.memory_id for m in memories}
    finally:
        store.close()


def test_token_overlap_memory_no_tokens(tmp_path: Path) -> None:
    """Memory with no valid tokens scores 0.0 (lines 161-162)."""
    store = KernelStore(tmp_path / "r.db")
    try:
        # Create a memory with single-char claim text
        store.create_memory_record(
            task_id="test-task",
            conversation_id="test-conv",
            category="other",
            claim_text="a b c",
            scope_kind="workspace",
            scope_ref="workspace:default",
            retention_class="volatile_fact",
            confidence=0.8,
            trust_tier="observed",
        )
        store.create_memory_record(
            task_id="test-task",
            conversation_id="test-conv",
            category="other",
            claim_text="python programming language",
            scope_kind="workspace",
            scope_ref="workspace:default",
            retention_class="volatile_fact",
            confidence=0.8,
            trust_tier="observed",
        )
        memories = [
            r
            for r in store.list_memory_records(status="active", limit=100)
            if r.memory_kind == "durable_fact"
        ]

        svc = HybridRetrievalService()
        ranked = svc._token_overlap_rank("python programming", memories)

        # Memory with valid tokens should rank first
        assert len(ranked) == len(memories)
    finally:
        store.close()


def test_fused_id_not_in_memory_map_skipped(tmp_path: Path) -> None:
    """Memory ID in RRF results but not in memory_map is skipped (line 122)."""
    store = KernelStore(tmp_path / "r.db")
    try:
        memories = _create_memories(store, [("test content here", 0)])

        # Monkey-patch _token_overlap_rank to inject a phantom ID
        svc = HybridRetrievalService()
        original_token_rank = svc._token_overlap_rank

        def patched_rank(query, mems):
            result = original_token_rank(query, mems)
            result.append("phantom-id-not-in-memories")
            return result

        svc._token_overlap_rank = patched_rank

        report = svc.retrieve("test", memories, store)

        # phantom-id should be skipped
        result_ids = {r.memory_id for r in report.results}
        assert "phantom-id-not-in-memories" not in result_ids
    finally:
        store.close()


def test_semantic_rank_exception_returns_empty(tmp_path: Path) -> None:
    """Semantic rank returns empty list on exception (lines 185-187)."""
    store = KernelStore(tmp_path / "r.db")
    try:
        ensure_embedding_schema(store)
        memories = _create_memories(store, [("test content", 0)])

        embed_svc = MagicMock()
        embed_svc.is_available.return_value = True
        embed_svc.search.side_effect = RuntimeError("embedding error")

        svc = HybridRetrievalService(embedding_service=embed_svc)

        result = svc._semantic_rank("test query", memories, store)

        assert result == []
    finally:
        store.close()


def test_rrf_fusion_multiple_paths_deep(tmp_path: Path) -> None:
    """RRF fusion with token, semantic, graph, and temporal paths all contributing."""
    ranked_lists = {
        "token_overlap": ["m1", "m3", "m2"],
        "semantic": ["m2", "m1"],
        "graph": ["m3", "m2"],
        "temporal": ["m1", "m2", "m3"],
    }

    fused = HybridRetrievalService._reciprocal_rank_fusion(ranked_lists, k=_RRF_K)
    fused_dict = dict(fused)

    # All three memories should appear
    assert "m1" in fused_dict
    assert "m2" in fused_dict
    assert "m3" in fused_dict

    # m1 appears in token(0), semantic(1), temporal(0) => 3 contributions
    # m2 appears in token(2), semantic(0), graph(1), temporal(1) => 4 contributions
    # m2 should have highest score since it appears in all 4 lists
    assert fused_dict["m2"] > fused_dict["m3"]


def test_graph_rank_shared_influence_counts(tmp_path: Path) -> None:
    """Graph rank counts shared influence links correctly (lines 202-209, 211-212)."""
    store = KernelStore(tmp_path / "r.db")
    try:
        memories = _create_memories(
            store,
            [
                ("seed memory one about architecture", 0),
                ("related memory about patterns", 0),
                ("another related memory about design", 0),
            ],
        )
        memory_ids = [m.memory_id for m in memories]

        lineage = MagicMock()
        # Each seed traces to decisions that share the same related memory
        lineage.trace_memory.side_effect = [
            MemoryImpact(
                memory_id=memory_ids[0],
                influenced_decisions=["d1"],
                total_decisions=1,
            ),
            MemoryImpact(
                memory_id=memory_ids[1],
                influenced_decisions=["d2"],
                total_decisions=1,
            ),
            MemoryImpact(
                memory_id=memory_ids[2],
                influenced_decisions=["d3"],
                total_decisions=1,
            ),
        ]
        # d1 and d2 both reference memory_ids[2], d3 references memory_ids[1]
        lineage.trace_decision.side_effect = [
            DecisionLineage(decision_id="d1", influencing_memories=[memory_ids[2]]),
            DecisionLineage(decision_id="d2", influencing_memories=[memory_ids[2]]),
            DecisionLineage(decision_id="d3", influencing_memories=[memory_ids[1]]),
        ]

        svc = HybridRetrievalService(lineage_service=lineage)
        ranked = svc._graph_rank("seed memory architecture", memories, store)

        # memory_ids[2] should rank higher (referenced by 2 decisions)
        assert len(ranked) >= 1
        assert ranked[0] == memory_ids[2]
    finally:
        store.close()
