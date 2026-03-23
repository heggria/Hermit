from __future__ import annotations

import heapq
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from hermit.kernel.context.memory.confidence import ConfidenceDecayService
from hermit.kernel.context.memory.embeddings import EmbeddingService
from hermit.kernel.context.memory.reranker import CrossEncoderReranker
from hermit.kernel.context.memory.text import normalize_topic, shares_topic, topic_tokens
from hermit.kernel.context.memory.token_index import TokenIndex

if TYPE_CHECKING:
    from hermit.kernel.context.memory.lineage import MemoryLineageService
    from hermit.kernel.context.memory.memory_quality import MemoryQualityService
    from hermit.kernel.context.models.context import TaskExecutionContext
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()

_RRF_K = 60  # RRF constant
_DEEP_QUERY_THRESHOLD = 50  # chars: above this -> deep path
_DEFAULT_LIMIT = 10
_BOOKKEEPING_KINDS = frozenset({"episode_index", "influence_link"})


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
        quality_service: MemoryQualityService | None = None,
        reranker: CrossEncoderReranker | None = None,
    ) -> None:
        self._embeddings = embedding_service or EmbeddingService()
        self._confidence = confidence_service or ConfidenceDecayService()
        self._lineage = lineage_service
        self._quality = quality_service
        self._reranker = reranker
        self._token_index: TokenIndex | None = None

    def _ensure_token_index(self, memories: list[MemoryRecord]) -> TokenIndex:
        """Build or return a cached inverted token index for memories."""
        if self._token_index is not None and len(self._token_index) == len(memories):
            return self._token_index
        index = TokenIndex()
        for m in memories:
            tokens = frozenset(t for t in topic_tokens(m.claim_text) if len(t) >= 2)
            index.add(m.memory_id, tokens)
        self._token_index = index
        return index

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

        # Filter out bookkeeping memory kinds that should not appear in retrieval
        memories = [
            m for m in memories
            if getattr(m, "memory_kind", None) not in _BOOKKEEPING_KINDS
        ]

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

        # Build memory lookup once for reranking + result assembly
        memory_map = {m.memory_id: m for m in memories}

        # Quality score integration: multiply RRF scores by quality scores
        fused = self._apply_quality_scores(fused, memory_map=memory_map)

        # Apply cross-encoder reranking if available (deep path only)
        reranked = False
        if use_deep and self._reranker is not None and self._reranker.is_available():
            candidates = [
                (mid, memory_map[mid].claim_text if mid in memory_map else "", score)
                for mid, score in fused[:limit]
            ]
            fused_reranked = self._reranker.rerank(query, candidates, limit=limit)
            fused = [(mid, score) for mid, _, score in fused_reranked]
            reranked = True

        # Build result
        results: list[RetrievalResult] = []
        for mid, score in fused[:limit]:
            if mid not in memory_map:
                continue
            sources = [name for name, ranked in ranked_lists.items() if mid in ranked]
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

    def _token_overlap_rank(
        self, query: str, memories: list[MemoryRecord], limit: int = _DEFAULT_LIMIT
    ) -> list[str]:
        """Rank by token overlap between query and memory claim text."""
        query_tokens = {t for t in topic_tokens(query) if len(t) >= 2}
        if not query_tokens:
            return [m.memory_id for m in memories]

        # Layer 3: Build/use inverted index for candidate filtering
        index = self._ensure_token_index(memories)
        candidate_ids = index.candidates(query_tokens)

        # Layer 6: Early termination -- if too many candidates, pre-filter
        max_candidates = limit * 3
        if len(candidate_ids) > max_candidates:
            # Keep top candidates by raw overlap count (O(n log k) via heapq)
            overlap_counts: list[tuple[str, int]] = []
            for mid in candidate_ids:
                mem_tokens = index.get_tokens(mid)
                overlap_counts.append((mid, len(query_tokens & mem_tokens)))
            top = heapq.nlargest(max_candidates, overlap_counts, key=lambda x: x[1])
            candidate_ids = {mid for mid, _ in top}

        # Layer 4: Precompute query-side values for shares_topic
        query_norm = normalize_topic(query)
        query_paths = frozenset(re.findall(r"/[\w./-]+", query))
        query_bigrams = frozenset(
            query_norm[i : i + 2] for i in range(max(0, len(query_norm) - 1))
        )

        # Score only candidates (not all memories)
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

    def _apply_quality_scores(
        self,
        fused: list[tuple[str, float]],
        memory_map: dict[str, MemoryRecord],
    ) -> list[tuple[str, float]]:
        """Apply MemoryQualityService scores as a multiplier to RRF fused scores.

        If quality_service is not available, returns fused scores unchanged.
        """
        if self._quality is None:
            return fused
        try:
            now = time.time()
            adjusted: list[tuple[str, float]] = []
            for mid, score in fused:
                memory = memory_map.get(mid)
                if memory is None:
                    adjusted.append((mid, score))
                    continue
                record_dict = {
                    "memory_id": memory.memory_id,
                    "category": memory.category,
                    "claim_text": memory.claim_text,
                    "retention_class": memory.retention_class,
                    "confidence": memory.confidence,
                    "created_at": memory.created_at,
                    "updated_at": memory.updated_at,
                    "expires_at": memory.expires_at,
                    "last_validated_at": memory.last_validated_at,
                    "structured_assertion": dict(memory.structured_assertion or {}),
                }
                quality = self._quality.quality_score(record_dict, now=now)
                adjusted.append((mid, score * quality))
            adjusted.sort(key=lambda x: x[1], reverse=True)
            log.debug(
                "quality_scores_applied",
                total=len(adjusted),
                top_score=adjusted[0][1] if adjusted else 0.0,
            )
            return adjusted
        except Exception:
            log.debug("quality_score_fallback")
            return fused

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
