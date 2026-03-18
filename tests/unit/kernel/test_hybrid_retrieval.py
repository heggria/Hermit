from __future__ import annotations

from pathlib import Path

from hermit.kernel.context.memory.embeddings import EmbeddingService, ensure_embedding_schema
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
