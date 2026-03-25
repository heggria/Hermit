"""Singleton service registry for memory subsystem modules.

Lazily initialises all memory services with correct dependency wiring
and ensures schema migrations run exactly once per process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import structlog

if TYPE_CHECKING:
    from hermit.kernel.context.memory.consolidation import ConsolidationService
    from hermit.kernel.context.memory.embeddings import EmbeddingService
    from hermit.kernel.context.memory.episodic import EpisodicMemoryService
    from hermit.kernel.context.memory.graph import MemoryGraphService
    from hermit.kernel.context.memory.lineage import MemoryLineageService
    from hermit.kernel.context.memory.procedural import ProceduralMemoryService
    from hermit.kernel.context.memory.reranker import CrossEncoderReranker
    from hermit.kernel.context.memory.retrieval import HybridRetrievalService
    from hermit.kernel.context.memory.working_memory import WorkingMemoryManager
    from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()


class MemoryServices(NamedTuple):
    """Immutable bundle of all memory subsystem services."""

    embedding: EmbeddingService
    graph: MemoryGraphService
    procedural: ProceduralMemoryService
    episodic: EpisodicMemoryService
    lineage: MemoryLineageService
    retrieval: HybridRetrievalService
    working_memory: WorkingMemoryManager
    consolidation: ConsolidationService
    reranker: CrossEncoderReranker


_cached_services: MemoryServices | None = None
_schemas_initialized: bool = False


def _ensure_schemas(store: KernelStore) -> None:
    """Run schema migrations for embedding and graph tables exactly once."""
    global _schemas_initialized
    if _schemas_initialized:
        return

    try:
        from hermit.kernel.context.memory.embeddings import ensure_embedding_schema
        from hermit.kernel.context.memory.graph import ensure_graph_schema

        ensure_embedding_schema(store)
        ensure_graph_schema(store)
        _schemas_initialized = True
        log.debug("memory_schemas_initialized")
    except Exception:
        log.warning("memory_schema_init_failed", exc_info=True)


def get_services(store: KernelStore) -> MemoryServices:
    """Return the singleton MemoryServices bundle.

    On first call, instantiates all services with correct dependency wiring
    and ensures database schemas are initialised.  Subsequent calls return
    the cached bundle (schema init is idempotent and skipped).
    """
    global _cached_services

    _ensure_schemas(store)

    if _cached_services is not None:
        return _cached_services

    from hermit.kernel.context.memory.anti_pattern import AntiPatternService
    from hermit.kernel.context.memory.confidence import ConfidenceDecayService
    from hermit.kernel.context.memory.consolidation import ConsolidationService
    from hermit.kernel.context.memory.decay import MemoryDecayService
    from hermit.kernel.context.memory.embeddings import EmbeddingService
    from hermit.kernel.context.memory.episodic import EpisodicMemoryService
    from hermit.kernel.context.memory.graph import MemoryGraphService
    from hermit.kernel.context.memory.lineage import MemoryLineageService
    from hermit.kernel.context.memory.procedural import ProceduralMemoryService
    from hermit.kernel.context.memory.reflect import ReflectionService
    from hermit.kernel.context.memory.reranker import CrossEncoderReranker
    from hermit.kernel.context.memory.retrieval import HybridRetrievalService
    from hermit.kernel.context.memory.working_memory import WorkingMemoryManager

    embedding = EmbeddingService()
    confidence = ConfidenceDecayService()
    lineage = MemoryLineageService()
    reranker = CrossEncoderReranker()
    graph = MemoryGraphService(embedding_service=embedding)
    procedural = ProceduralMemoryService()
    episodic = EpisodicMemoryService()
    decay = MemoryDecayService()
    reflection = ReflectionService(graph_service=graph)
    anti_pattern = AntiPatternService()

    retrieval = HybridRetrievalService(
        embedding_service=embedding,
        confidence_service=confidence,
        lineage_service=lineage,
        reranker=reranker,
    )
    working_memory = WorkingMemoryManager()
    consolidation = ConsolidationService(
        decay_service=decay,
        reflection_service=reflection,
        anti_pattern_service=anti_pattern,
        embedding_service=embedding,
    )

    _cached_services = MemoryServices(
        embedding=embedding,
        graph=graph,
        procedural=procedural,
        episodic=episodic,
        lineage=lineage,
        retrieval=retrieval,
        working_memory=working_memory,
        consolidation=consolidation,
        reranker=reranker,
    )
    log.info("memory_services_initialized")
    return _cached_services


def reset_services() -> None:
    """Reset cached services (for testing only)."""
    global _cached_services, _schemas_initialized
    _cached_services = None
    _schemas_initialized = False


__all__ = ["MemoryServices", "get_services", "reset_services"]
