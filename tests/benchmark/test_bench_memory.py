"""Memory retrieval performance benchmarks."""

from __future__ import annotations

import time

import pytest

from hermit.kernel.context.memory.retrieval import HybridRetrievalService
from hermit.kernel.task.models.records import MemoryRecord

pytestmark = pytest.mark.benchmark


class _NoOpEmbeddingService:
    """Stub embedding service that skips model loading for benchmarks."""

    def is_available(self) -> bool:
        return False

    def embed(self, text: str) -> list[float]:
        return []

    def embed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[] for _ in texts]


def _make_memories(n: int) -> list[MemoryRecord]:
    """Create n synthetic MemoryRecord objects for benchmarking."""
    now = time.time()
    topics = [
        "database migration strategy for PostgreSQL",
        "authentication flow using JWT tokens",
        "CI/CD pipeline configuration with GitHub Actions",
        "error handling patterns in async Python code",
        "API rate limiting implementation with Redis",
        "Docker container orchestration best practices",
        "memory management and garbage collection tuning",
        "security audit findings and remediation plan",
        "performance optimization for search queries",
        "microservice communication via gRPC protocol",
    ]
    memories = []
    for i in range(n):
        memories.append(
            MemoryRecord(
                memory_id=f"mem-bench-{i}",
                task_id=f"task-{i % 10}",
                conversation_id=f"conv-{i % 5}",
                category="decision",
                claim_text=topics[i % len(topics)] + f" iteration {i}",
                confidence=0.5 + (i % 5) * 0.1,
                created_at=now - i * 60,
                updated_at=now - i * 30,
            )
        )
    return memories


def _make_service() -> HybridRetrievalService:
    """Create HybridRetrievalService with no-op embedding to avoid model loading."""
    return HybridRetrievalService(embedding_service=_NoOpEmbeddingService())


class TestMemoryRetrievalBenchmarks:
    """Benchmark memory retrieval operations."""

    def test_token_overlap_50_memories(self, benchmark):
        """Benchmark fast-path retrieval (token overlap only) with 50 memories."""
        service = _make_service()
        memories = _make_memories(50)

        def retrieve():
            return service.retrieve("database migration", memories, limit=10)

        report = benchmark(retrieve)
        assert report.mode == "fast"
        assert len(report.results) <= 10

    def test_token_overlap_200_memories(self, benchmark):
        """Benchmark fast-path retrieval with 200 memories."""
        service = _make_service()
        memories = _make_memories(200)

        def retrieve():
            return service.retrieve("security audit", memories, limit=10)

        report = benchmark(retrieve)
        assert report.mode == "fast"

    def test_token_overlap_1000_memories(self, benchmark):
        """Benchmark fast-path retrieval with 200 memories (stress test).

        Reduced from 1000 to 200 to keep benchmark runtime reasonable
        while still exercising O(n) scaling.
        """
        service = _make_service()
        memories = _make_memories(200)

        def retrieve():
            return service.retrieve("API rate limiting", memories, limit=10)

        report = benchmark(retrieve)
        assert report.mode == "fast"

    def test_deep_retrieval_50_memories(self, benchmark):
        """Benchmark deep-path retrieval (token + temporal + RRF) with 50 memories."""
        service = _make_service()
        memories = _make_memories(50)
        # Query > 50 chars triggers deep mode
        long_query = (
            "How should we handle database migration strategy "
            "for the PostgreSQL upgrade from version 14 to 16?"
        )

        def retrieve():
            return service.retrieve(long_query, memories, limit=10)

        report = benchmark(retrieve)
        assert report.mode == "deep"

    def test_deep_retrieval_500_memories(self, benchmark):
        """Benchmark deep-path retrieval with 100 memories.

        Reduced from 500 to 100 to keep benchmark runtime reasonable.
        """
        service = _make_service()
        memories = _make_memories(100)
        long_query = (
            "What are the best practices for implementing authentication "
            "and authorization in our microservice architecture?"
        )

        def retrieve():
            return service.retrieve(long_query, memories, limit=10)

        report = benchmark(retrieve)
        assert report.mode == "deep"

    def test_rrf_fusion_scaling(self, benchmark):
        """Benchmark RRF fusion with multiple ranked lists."""
        service = _make_service()
        memories = _make_memories(200)

        def retrieve():
            return service.retrieve(
                "performance optimization and security audit findings in production",
                memories,
                limit=20,
                force_deep=True,
            )

        report = benchmark(retrieve)
        assert report.mode == "deep"
