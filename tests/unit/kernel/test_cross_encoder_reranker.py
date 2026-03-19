from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from hermit.kernel.context.memory.embeddings import EmbeddingService, ensure_embedding_schema
from hermit.kernel.context.memory.reranker import CrossEncoderReranker
from hermit.kernel.context.memory.retrieval import HybridRetrievalService
from hermit.kernel.ledger.journal.store import KernelStore


def _create_memories(store: KernelStore, items: list[tuple[str, float]]) -> list:
    """Helper: create memory records and return them."""
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
    return [r for r in records if r.memory_kind == "durable_fact"]


# --- CrossEncoderReranker unit tests ---


def test_reranker_not_available_returns_input_order() -> None:
    """When cross-encoder model is unavailable, rerank returns candidates unchanged."""
    reranker = CrossEncoderReranker()
    reranker._available = False

    candidates = [
        ("m1", "memory one text", 0.5),
        ("m2", "memory two text", 0.8),
        ("m3", "memory three text", 0.3),
    ]

    result = reranker.rerank("test query", candidates, limit=3)

    # Should return in original order (no reranking)
    assert [mid for mid, _, _ in result] == ["m1", "m2", "m3"]


def test_reranker_empty_candidates() -> None:
    """Empty candidates returns empty list."""
    reranker = CrossEncoderReranker()
    result = reranker.rerank("test query", [], limit=5)
    assert result == []


def test_reranker_single_candidate() -> None:
    """Single candidate returned as-is without model call."""
    reranker = CrossEncoderReranker()
    reranker._available = False

    candidates = [("m1", "memory text", 0.9)]
    result = reranker.rerank("query", candidates, limit=5)

    assert len(result) == 1
    assert result[0][0] == "m1"


def test_reranker_respects_limit() -> None:
    """Rerank output is truncated to limit."""
    reranker = CrossEncoderReranker()
    reranker._available = False

    candidates = [
        ("m1", "text one", 0.5),
        ("m2", "text two", 0.8),
        ("m3", "text three", 0.3),
        ("m4", "text four", 0.6),
    ]

    result = reranker.rerank("query", candidates, limit=2)
    assert len(result) == 2


def test_reranker_with_mock_model() -> None:
    """When model is available, rerank uses cross-encoder scores to reorder."""
    reranker = CrossEncoderReranker()
    reranker._available = True

    # Mock the model to return scores that reverse the order
    mock_model = MagicMock()
    # predict returns scores: m3 highest, m1 lowest
    mock_model.predict.return_value = [0.1, 0.5, 0.9]
    reranker._model = mock_model

    candidates = [
        ("m1", "text one", 0.9),
        ("m2", "text two", 0.5),
        ("m3", "text three", 0.1),
    ]

    result = reranker.rerank("test query", candidates, limit=3)

    # m3 should be first (highest cross-encoder score)
    assert result[0][0] == "m3"
    assert result[1][0] == "m2"
    assert result[2][0] == "m1"


def test_reranker_model_exception_graceful_fallback() -> None:
    """If model.predict raises, fallback to original order."""
    reranker = CrossEncoderReranker()
    reranker._available = True

    mock_model = MagicMock()
    mock_model.predict.side_effect = RuntimeError("model error")
    reranker._model = mock_model

    candidates = [
        ("m1", "text one", 0.9),
        ("m2", "text two", 0.5),
    ]

    result = reranker.rerank("test query", candidates, limit=2)

    # Fallback: original order preserved
    assert [mid for mid, _, _ in result] == ["m1", "m2"]


def test_reranker_is_available_checks_import() -> None:
    """is_available() checks if sentence_transformers.CrossEncoder exists."""
    reranker = CrossEncoderReranker()
    reranker._available = None  # Reset to trigger check

    # is_available should return a bool
    result = reranker.is_available()
    assert isinstance(result, bool)


# --- Integration with HybridRetrievalService ---


def test_retrieval_with_reranker_deep_path(tmp_path: Path) -> None:
    """Deep path uses cross-encoder reranking when reranker is provided."""
    store = KernelStore(tmp_path / "r.db")
    try:
        ensure_embedding_schema(store)
        memories = _create_memories(
            store,
            [
                ("python design patterns for large applications", 0),
                ("ocean waves beach surfing swimming", 0),
                ("python programming best practices and testing", 0),
            ],
        )

        embed_svc = EmbeddingService()
        embed_svc._available = False
        for m in memories:
            embed_svc.index_memory(m.memory_id, m.claim_text, store)

        # Create reranker that's "unavailable" (passthrough)
        reranker = CrossEncoderReranker()
        reranker._available = False

        svc = HybridRetrievalService(
            embedding_service=embed_svc,
            reranker=reranker,
        )
        long_query = (
            "What are the best python programming design patterns"
            " for building large scale applications"
        )

        report = svc.retrieve(long_query, memories, store)

        assert report.mode == "deep"
        assert len(report.results) >= 1
        assert report.reranked is False  # Reranker unavailable, so not reranked
    finally:
        store.close()


def test_retrieval_with_reranker_active(tmp_path: Path) -> None:
    """When reranker model is available, results are reranked and flagged."""
    store = KernelStore(tmp_path / "r.db")
    try:
        ensure_embedding_schema(store)
        memories = _create_memories(
            store,
            [
                ("python design patterns", 0),
                ("ocean waves surfing", 0),
            ],
        )

        embed_svc = EmbeddingService()
        embed_svc._available = False
        for m in memories:
            embed_svc.index_memory(m.memory_id, m.claim_text, store)

        # Create reranker with mock model
        reranker = CrossEncoderReranker()
        reranker._available = True
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.1]  # Keep same order
        reranker._model = mock_model

        svc = HybridRetrievalService(
            embedding_service=embed_svc,
            reranker=reranker,
        )
        long_query = "What are the best python programming design patterns for applications"

        report = svc.retrieve(long_query, memories, store)

        assert report.mode == "deep"
        assert report.reranked is True
    finally:
        store.close()


def test_retrieval_fast_path_skips_reranker(tmp_path: Path) -> None:
    """Fast path does NOT use reranker even if provided."""
    store = KernelStore(tmp_path / "r.db")
    try:
        memories = _create_memories(store, [("python code", 0)])

        reranker = CrossEncoderReranker()
        reranker._available = True
        mock_model = MagicMock()
        reranker._model = mock_model

        svc = HybridRetrievalService(reranker=reranker)
        report = svc.retrieve("python", memories, store)  # Short query -> fast

        assert report.mode == "fast"
        assert report.reranked is False
        mock_model.predict.assert_not_called()
    finally:
        store.close()
