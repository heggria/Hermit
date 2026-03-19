from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from hermit.kernel.context.memory.confidence import ConfidenceDecayService
from hermit.kernel.context.memory.embeddings import EmbeddingService
from hermit.kernel.context.memory.reranker import CrossEncoderReranker
from hermit.kernel.context.memory.text import shares_topic, topic_tokens

if TYPE_CHECKING:
    from hermit.kernel.context.memory.lineage import MemoryLineageService
    from hermit.kernel.context.models.context import TaskExecutionContext
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()

_RRF_K = 60  # RRF constant
_DEEP_QUERY_THRESHOLD = 50  # chars: above this -> deep path
_DEFAULT_LIMIT = 10


@dataclass
class RetrievalResult:
    """A single retrieved memory with its fusion score."""

    memory_id: str
    memory: Any  # MemoryRecord
    rrf_score: float
    sources: list[str]  # which retrieval paths contributed


@dataclass
class RetrievalReport:
    """Summary of a hybrid retrieval operation."""

    query: str
    mode: str  # "fast" or "deep"
    total_candidates: int
    results: list[RetrievalResult] = field(default_factory=lambda: list[RetrievalResult]())
    retrieval_time_ms: float = 0.0
    reranked: bool = False


class HybridRetrievalService:
    """Four-way hybrid retrieval with Reciprocal Rank Fusion.

    Retrieval paths:
    1. Token overlap (existing logic, always used)
    2. Semantic embedding (EmbeddingService, if available)
    3. Graph traversal (MemoryLineageService, for related memories)
    4. Temporal filtering (freshness-aware scoring)

    Dual-path mode:
    - Fast path: token matching only (<5ms target, for short queries)
    - Deep path: all four ways (for len(query) > 50 or explicit signals)
    """

    def __init__(
        self,
        *,
        embedding_service: EmbeddingService | None = None,
        confidence_service: ConfidenceDecayService | None = None,
        lineage_service: MemoryLineageService | None = None,
        reranker: CrossEncoderReranker | None = None,
    ) -> None:
        self._embeddings = embedding_service or EmbeddingService()
        self._confidence = confidence_service or ConfidenceDecayService()
        self._lineage = lineage_service
        self._reranker = reranker

    def retrieve(
        self,
        query: str,
        memories: list[MemoryRecord],
        store: KernelStore | None = None,
        *,
        context: TaskExecutionContext | None = None,
        limit: int = _DEFAULT_LIMIT,
        force_deep: bool = False,
    ) -> RetrievalReport:
        """Retrieve and rank memories using hybrid retrieval."""
        start = time.monotonic()
        use_deep = force_deep or len(query) > _DEEP_QUERY_THRESHOLD
        mode = "deep" if use_deep else "fast"

        if not memories:
            return RetrievalReport(query=query, mode=mode, total_candidates=0)

        # Build ranked lists from each retrieval path
        ranked_lists: dict[str, list[str]] = {}

        # Path 1: Token overlap (always)
        token_ranked = self._token_overlap_rank(query, memories)
        ranked_lists["token_overlap"] = token_ranked

        if use_deep:
            # Path 2: Semantic embedding
            if store is not None:
                semantic_ranked = self._semantic_rank(query, memories, store)
                if semantic_ranked:
                    ranked_lists["semantic"] = semantic_ranked

            # Path 3: Graph traversal (find related memories)
            if self._lineage is not None and store is not None:
                graph_ranked = self._graph_rank(query, memories, store)
                if graph_ranked:
                    ranked_lists["graph"] = graph_ranked

            # Path 4: Temporal freshness
            temporal_ranked = self._temporal_rank(memories)
            ranked_lists["temporal"] = temporal_ranked

        # RRF fusion
        fused = self._reciprocal_rank_fusion(ranked_lists)

        # Build result
        memory_map = {m.memory_id: m for m in memories}

        # Cross-encoder reranking (deep path only)
        reranked = False
        if use_deep and self._reranker is not None:
            # Prepare candidates: (memory_id, claim_text, rrf_score)
            rerank_candidates = [
                (mid, memory_map[mid].claim_text, score)
                for mid, score in fused
                if mid in memory_map
            ]
            reranked_candidates = self._reranker.rerank(query, rerank_candidates, limit=limit)
            if reranked_candidates and reranked_candidates != rerank_candidates[:limit]:
                reranked = True
                # Rebuild fused from reranked order
                fused = [(mid, score) for mid, _, score in reranked_candidates]

        results: list[RetrievalResult] = []
        for mid, score in fused[:limit]:
            if mid not in memory_map:
                continue
            sources = [name for name, ranked in ranked_lists.items() if mid in ranked]
            if reranked:
                sources.append("cross_encoder")
            results.append(
                RetrievalResult(
                    memory_id=mid,
                    memory=memory_map[mid],
                    rrf_score=score,
                    sources=sources,
                )
            )

        elapsed = (time.monotonic() - start) * 1000
        report = RetrievalReport(
            query=query,
            mode=mode,
            total_candidates=len(memories),
            results=results,
            retrieval_time_ms=round(elapsed, 2),
            reranked=reranked,
        )
        log.debug(
            "hybrid_retrieval",
            mode=mode,
            candidates=len(memories),
            results=len(results),
            time_ms=report.retrieval_time_ms,
            paths=list(ranked_lists.keys()),
        )
        return report

    def _token_overlap_rank(self, query: str, memories: list[MemoryRecord]) -> list[str]:
        """Rank by token overlap between query and memory claim text."""
        query_tokens = {t for t in topic_tokens(query) if len(t) >= 2}
        if not query_tokens:
            return [m.memory_id for m in memories]

        scored: list[tuple[str, float]] = []
        for m in memories:
            mem_tokens = {t for t in topic_tokens(m.claim_text) if len(t) >= 2}
            if not mem_tokens:
                scored.append((m.memory_id, 0.0))
                continue
            overlap = len(query_tokens & mem_tokens)
            union = query_tokens | mem_tokens
            jaccard = overlap / len(union) if union else 0.0
            # Bonus for topic match
            bonus = 1.0 if shares_topic(m.claim_text, query) else 0.0
            scored.append((m.memory_id, jaccard + bonus))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [mid for mid, _ in scored]

    def _semantic_rank(
        self, query: str, memories: list[MemoryRecord], store: KernelStore
    ) -> list[str]:
        """Rank by embedding similarity."""
        if not self._embeddings.is_available():
            # Fallback embeddings still work
            pass

        try:
            results = self._embeddings.search(query, store, limit=len(memories))
            memory_ids = {m.memory_id for m in memories}
            return [mid for mid, _ in results if mid in memory_ids]
        except Exception:
            log.debug("semantic_rank_fallback")
            return []

    def _graph_rank(
        self, query: str, memories: list[MemoryRecord], store: KernelStore
    ) -> list[str]:
        """Rank by graph proximity to query-relevant memories."""
        if self._lineage is None:
            return []

        # Find seed memories via token overlap
        seeds = self._token_overlap_rank(query, memories)[:3]
        if not seeds:
            return []

        # Find memories that share influence links with seeds
        related: dict[str, int] = {}
        for seed_id in seeds:
            impact = self._lineage.trace_memory(seed_id, store)
            for did in impact.influenced_decisions:
                lineage = self._lineage.trace_decision(did, store)
                for mid in lineage.influencing_memories:
                    if mid != seed_id:
                        related[mid] = related.get(mid, 0) + 1

        ranked = sorted(related, key=lambda mid: related[mid], reverse=True)
        return ranked

    def _temporal_rank(self, memories: list[MemoryRecord]) -> list[str]:
        """Rank by recency and freshness."""
        now = time.time()
        scored: list[tuple[str, float]] = []
        for m in memories:
            effective_conf = self._confidence.compute_confidence(m, now=now)
            recency = (m.updated_at or m.created_at or 0.0) / 1e12
            scored.append((m.memory_id, effective_conf + recency))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [mid for mid, _ in scored]

    @staticmethod
    def _reciprocal_rank_fusion(
        ranked_lists: dict[str, list[str]],
        k: int = _RRF_K,
    ) -> list[tuple[str, float]]:
        """Merge multiple ranked lists using Reciprocal Rank Fusion.

        score(d) = sum over all lists of 1/(k + rank_in_list)
        """
        scores: dict[str, float] = {}
        for _name, ranked in ranked_lists.items():
            for rank, mid in enumerate(ranked):
                scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank)

        fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return fused


__all__ = ["HybridRetrievalService", "RetrievalReport", "RetrievalResult"]
