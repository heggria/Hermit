"""Tests for memory services singleton registry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hermit.plugins.builtin.hooks.memory.services import (
    MemoryServices,
    get_services,
    reset_services,
)


def _mock_all_memory_modules():
    """Create patches for all memory module imports used by get_services."""

    class FakeEmbeddingService:
        pass

    class FakeConfidenceDecayService:
        pass

    class FakeMemoryLineageService:
        pass

    class FakeCrossEncoderReranker:
        pass

    class FakeMemoryGraphService:
        def __init__(self, embedding_service=None):
            self.embedding_service = embedding_service

    class FakeProceduralMemoryService:
        pass

    class FakeEpisodicMemoryService:
        pass

    class FakeMemoryDecayService:
        pass

    class FakeReflectionService:
        def __init__(self, graph_service=None):
            self.graph_service = graph_service

    class FakeAntiPatternService:
        pass

    class FakeHybridRetrievalService:
        def __init__(
            self,
            embedding_service=None,
            confidence_service=None,
            lineage_service=None,
            reranker=None,
        ):
            pass

    class FakeWorkingMemoryManager:
        pass

    class FakeConsolidationService:
        def __init__(
            self,
            decay_service=None,
            reflection_service=None,
            anti_pattern_service=None,
            embedding_service=None,
        ):
            pass

    patches = [
        patch(
            "hermit.plugins.builtin.hooks.memory.services.EmbeddingService",
            FakeEmbeddingService,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.embeddings.EmbeddingService",
            FakeEmbeddingService,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.confidence.ConfidenceDecayService",
            FakeConfidenceDecayService,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.consolidation.ConsolidationService",
            FakeConsolidationService,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.decay.MemoryDecayService",
            FakeMemoryDecayService,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.episodic.EpisodicMemoryService",
            FakeEpisodicMemoryService,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.graph.MemoryGraphService",
            FakeMemoryGraphService,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.lineage.MemoryLineageService",
            FakeMemoryLineageService,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.procedural.ProceduralMemoryService",
            FakeProceduralMemoryService,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.reflect.ReflectionService",
            FakeReflectionService,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.reranker.CrossEncoderReranker",
            FakeCrossEncoderReranker,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.retrieval.HybridRetrievalService",
            FakeHybridRetrievalService,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.working_memory.WorkingMemoryManager",
            FakeWorkingMemoryManager,
            create=True,
        ),
        patch(
            "hermit.kernel.context.memory.anti_pattern.AntiPatternService",
            FakeAntiPatternService,
            create=True,
        ),
    ]
    return patches


# ── reset_services ──


def test_reset_services_clears_cache() -> None:
    from hermit.plugins.builtin.hooks.memory import services

    services._cached_services = "sentinel"
    services._schemas_initialized = True
    reset_services()
    assert services._cached_services is None
    assert services._schemas_initialized is False


# ── _ensure_schemas ──


def test_ensure_schemas_runs_once() -> None:
    from hermit.plugins.builtin.hooks.memory import services

    reset_services()

    store = MagicMock()
    call_count = 0

    def fake_ensure_embedding(s):
        nonlocal call_count
        call_count += 1

    with (
        patch(
            "hermit.kernel.context.memory.embeddings.ensure_embedding_schema",
            fake_ensure_embedding,
        ),
        patch("hermit.kernel.context.memory.graph.ensure_graph_schema", lambda s: None),
    ):
        services._ensure_schemas(store)
        services._ensure_schemas(store)

    assert call_count == 1
    assert services._schemas_initialized is True
    reset_services()


def test_ensure_schemas_handles_exception() -> None:
    from hermit.plugins.builtin.hooks.memory import services

    reset_services()
    store = MagicMock()

    with patch(
        "hermit.kernel.context.memory.embeddings.ensure_embedding_schema",
        side_effect=RuntimeError("schema error"),
    ):
        services._ensure_schemas(store)

    assert services._schemas_initialized is False
    reset_services()


# ── get_services ──


def test_get_services_returns_named_tuple() -> None:

    reset_services()
    store = MagicMock()

    with (
        patch("hermit.kernel.context.memory.embeddings.ensure_embedding_schema", lambda s: None),
        patch("hermit.kernel.context.memory.graph.ensure_graph_schema", lambda s: None),
    ):
        patches = _mock_all_memory_modules()
        for p in patches:
            p.start()
        try:
            result = get_services(store)
            assert isinstance(result, MemoryServices)
            assert result.embedding is not None
            assert result.graph is not None
            assert result.retrieval is not None
            assert result.working_memory is not None
            assert result.consolidation is not None
            assert result.reranker is not None
        finally:
            for p in patches:
                p.stop()
            reset_services()


def test_get_services_caches_result() -> None:

    reset_services()
    store = MagicMock()

    with (
        patch("hermit.kernel.context.memory.embeddings.ensure_embedding_schema", lambda s: None),
        patch("hermit.kernel.context.memory.graph.ensure_graph_schema", lambda s: None),
    ):
        patches = _mock_all_memory_modules()
        for p in patches:
            p.start()
        try:
            result1 = get_services(store)
            result2 = get_services(store)
            assert result1 is result2
        finally:
            for p in patches:
                p.stop()
            reset_services()


# ── MemoryServices ──


def test_memory_services_is_named_tuple() -> None:
    assert issubclass(MemoryServices, tuple)
    assert hasattr(MemoryServices, "_fields")
    expected_fields = {
        "embedding",
        "graph",
        "procedural",
        "episodic",
        "lineage",
        "retrieval",
        "working_memory",
        "consolidation",
        "reranker",
    }
    assert set(MemoryServices._fields) == expected_fields
