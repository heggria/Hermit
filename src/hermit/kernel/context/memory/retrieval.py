from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from hermit.kernel.context.memory.confidence import ConfidenceDecayService
from hermit.kernel.context.memory.embeddings import EmbeddingService
from hermit.kernel.context.memory.text import (
    cached_topic_tokens,
    normalize_topic,
    shares_topic_precomputed,
    topic_tokens,
)
from hermit.kernel.context.memory.token_index import TokenIndex

if TYPE_CHECKING:
    from hermit.kernel.context.memory.lineage import MemoryLineageService
    from hermit.kernel.context.memory.reranker import CrossEncoderReranker
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
        self._token_index: TokenIndex | None = None

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

        # Filter out internal bookkeeping records (enrichment noise)
        _EXCLUDED_KINDS = {"episode_index", "influence_link", "contract_template", "task_pattern"}
        memories = [m for m in memories if getattr(m, "memory_kind", "") not in _EXCLUDED_KINDS]

        if not memories:
            return RetrievalReport(query=query, mode=mode, total_candidates=0)

        # Build ranked lists from each retrieval path
        ranked_lists: dict[str, list[str]] = {}

        # Path 1: Token overlap (always) — uses index + cache
        token_ranked = self._token_overlap_rank(query, memories, limit=limit)
        ranked_lists["token_overlap"] = token_ranked

        if use_deep:
            # Path 2: Semantic embedding
            if store is not None:
                semantic_ranked = self._semantic_rank(query, memories, store)
                if semantic_ranked:
                    ranked_lists["semantic"] = semantic_ranked

            # Path 3: Graph traversal — reuse token_ranked seeds (Layer 5)
            if self._lineage is not None and store is not None:
                graph_ranked = self._graph_rank(query, memories, store, seeds=token_ranked[:3])
                if graph_ranked:
                    ranked_lists["graph"] = graph_ranked

            # Path 4: Temporal freshness
            temporal_ranked = self._temporal_rank(memories)
            ranked_lists["temporal"] = temporal_ranked

            # Path 5: Importance score
            importance_ranked = self._importance_rank(memories)
            ranked_lists["importance"] = importance_ranked

            # Path 6: Procedural memory matching
            if store is not None:
                procedural_ranked = self._procedural_rank(query, store, memories)
                if procedural_ranked:
                    ranked_lists["procedural"] = procedural_ranked

            # Path 7: Entity knowledge graph
            if store is not None:
                entity_ranked = self._entity_rank(query, store, memories)
                if entity_ranked:
                    ranked_lists["entity"] = entity_ranked

        # RRF fusion
        fused = self._reciprocal_rank_fusion(ranked_lists)

        # Apply cross-encoder reranking if available (deep path only)
        reranked = False
        if use_deep and self._reranker is not None and self._reranker.is_available():
            memory_map_pre = {m.memory_id: m for m in memories}
            candidates = [
                (mid, memory_map_pre[mid].claim_text if mid in memory_map_pre else "", score)
                for mid, score in fused[:limit]
            ]
            fused_reranked = self._reranker.rerank(query, candidates, limit=limit)
            fused = [(mid, score) for mid, _, score in fused_reranked]
            reranked = True

        # Build result
        memory_map = {m.memory_id: m for m in memories}
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
        self,
        query: str,
        memories: list[MemoryRecord],
        *,
        limit: int = _DEFAULT_LIMIT,
    ) -> list[str]:
        """Rank by token overlap between query and memory claim text.

        Uses inverted index (Layer 3) to filter candidates, cached tokens
        (Layer 2) to avoid recomputation, and precomputed query-side values
        (Layer 4) to eliminate redundant normalize_topic calls.
        """
        query_tokens = frozenset(t for t in topic_tokens(query) if len(t) >= 2)
        if not query_tokens:
            return [m.memory_id for m in memories]

        # Layer 3: Build/use inverted index for candidate filtering
        index = self._ensure_token_index(memories)
        candidate_ids = index.candidates(query_tokens)

        # Layer 6: Early termination — if too many candidates, pre-filter
        max_candidates = limit * 3
        if len(candidate_ids) > max_candidates:
            # Quick pre-sort by raw overlap count, keep top candidates
            overlap_counts: list[tuple[str, int]] = []
            for mid in candidate_ids:
                mem_tokens = index.get_tokens(mid)
                overlap_counts.append((mid, len(query_tokens & mem_tokens)))
            overlap_counts.sort(key=lambda x: x[1], reverse=True)
            candidate_ids = {mid for mid, _ in overlap_counts[:max_candidates]}

        # Layer 4: Precompute query-side values for shares_topic
        query_norm = normalize_topic(query)
        query_paths = frozenset(re.findall(r"/[\w./-]+", query))
        query_bigrams = frozenset(query_norm[i : i + 2] for i in range(max(0, len(query_norm) - 1)))

        # Score only candidates (not all memories)
        scored: list[tuple[str, float]] = []
        for m in memories:
            if m.memory_id not in candidate_ids:
                scored.append((m.memory_id, 0.0))
                continue
            # Layer 2: Use cached tokens
            mem_tokens = cached_topic_tokens(m.memory_id, m.claim_text)
            mem_tokens_filtered = frozenset(t for t in mem_tokens if len(t) >= 2)
            if not mem_tokens_filtered:
                scored.append((m.memory_id, 0.0))
                continue
            overlap = len(query_tokens & mem_tokens_filtered)
            union = query_tokens | mem_tokens_filtered
            jaccard = overlap / len(union) if union else 0.0
            # Layer 4: Use precomputed query values
            bonus = (
                1.0
                if shares_topic_precomputed(m.claim_text, query_norm, query_paths, query_bigrams)
                else 0.0
            )
            scored.append((m.memory_id, jaccard + bonus))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [mid for mid, _ in scored]

    def _ensure_token_index(self, memories: list[MemoryRecord]) -> TokenIndex:
        """Lazily build or reuse the inverted token index."""
        if self._token_index is not None and len(self._token_index) == len(memories):
            return self._token_index
        index = TokenIndex()
        for m in memories:
            tokens = cached_topic_tokens(m.memory_id, m.claim_text)
            filtered = frozenset(t for t in tokens if len(t) >= 2)
            index.add(m.memory_id, filtered)
        self._token_index = index
        return index

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
        except (ImportError, OSError, RuntimeError) as exc:
            log.warning("semantic_rank_failed", error=str(exc))
            return []

    def _graph_rank(
        self,
        query: str,
        memories: list[MemoryRecord],
        store: KernelStore,
        *,
        seeds: list[str] | None = None,
    ) -> list[str]:
        """Rank by graph proximity to query-relevant memories."""
        if self._lineage is None:
            return []

        # Layer 5: Reuse pre-computed seeds if provided
        if seeds is None:
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

    @staticmethod
    def _procedural_rank(query: str, store: KernelStore, memories: list[MemoryRecord]) -> list[str]:
        """Rank by procedural memory trigger match."""
        try:
            from hermit.kernel.context.memory.procedural import ProceduralMemoryService

            svc = ProceduralMemoryService()
            matched_procs = svc.match_procedures(query, store, limit=10)
            if not matched_procs:
                return []
            # Collect all source memory IDs from matched procedures
            memory_id_set = {m.memory_id for m in memories}
            ranked: list[str] = []
            for proc in matched_procs:
                for mid in proc.source_memory_ids:
                    if mid in memory_id_set and mid not in ranked:
                        ranked.append(mid)
            return ranked
        except (ImportError, RuntimeError) as exc:
            structlog.get_logger().warning("procedural_rank_failed", error=str(exc))
            return []

    @staticmethod
    def _entity_rank(query: str, store: KernelStore, memories: list[MemoryRecord]) -> list[str]:
        """Rank by entity co-occurrence with query terms."""
        try:
            # Extract candidate entity names from query
            query_tokens = set(re.findall(r"\b\w{3,}\b", query.lower()))
            # Also extract file paths
            query_tokens.update(re.findall(r"/[\w./-]+", query))
            # Also extract PascalCase identifiers
            query_tokens.update(
                m.lower() for m in re.findall(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b", query)
            )
            if not query_tokens:
                return []

            memory_id_set = {m.memory_id for m in memories}
            # Query entity_links table for each candidate token
            hit_counts: dict[str, int] = {}
            for token in query_tokens:
                mids = store.find_memories_by_entity(token)
                for mid in mids:
                    if mid in memory_id_set:
                        hit_counts[mid] = hit_counts.get(mid, 0) + 1

            if not hit_counts:
                return []
            ranked = sorted(hit_counts, key=lambda mid: hit_counts[mid], reverse=True)
            return ranked
        except (OSError, RuntimeError) as exc:
            structlog.get_logger().warning("entity_rank_failed", error=str(exc))
            return []

    @staticmethod
    def _importance_rank(memories: list[MemoryRecord]) -> list[str]:
        """Rank by importance score descending."""
        sorted_mems = sorted(memories, key=lambda m: getattr(m, "importance", 5), reverse=True)
        return [m.memory_id for m in sorted_mems]

    def _temporal_rank(self, memories: list[MemoryRecord]) -> list[str]:
        """Rank by recency and freshness."""
        now = time.time()
        scored: list[tuple[str, float]] = []
        for m in memories:
            effective_conf = self._confidence.compute_confidence(m, now=now)
            recency = (m.updated_at or m.created_at or 0.0) / now if now > 0 else 0.0
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
