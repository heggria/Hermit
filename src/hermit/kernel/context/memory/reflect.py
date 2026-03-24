from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from hermit.kernel.context.memory.text import topic_tokens

if TYPE_CHECKING:
    from hermit.kernel.context.memory.graph import MemoryGraphService
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()

_MIN_CLUSTER_SIZE = 3
_REFLECTION_CONFIDENCE_THRESHOLD = 0.7


@dataclass(frozen=True)
class ReflectionInsight:
    """A higher-order insight synthesized from multiple memories."""

    insight_text: str
    source_memory_ids: tuple[str, ...]
    confidence: float
    insight_type: str  # generalization | pattern | contradiction_resolution


class ReflectionService:
    """Synthesizes higher-order insights from memory clusters.

    Finds groups of 3+ memories sharing graph connections or topics,
    then generates generalized insights that can be promoted as beliefs.
    """

    def __init__(self, graph_service: MemoryGraphService | None = None) -> None:
        self._graph = graph_service

    def reflect(
        self,
        store: KernelStore,
        *,
        limit: int = 50,
    ) -> list[ReflectionInsight]:
        """Find memory clusters and synthesize insights."""
        records = store.list_memory_records(status="active", limit=limit * 10)
        durable = [
            r
            for r in records
            if r.memory_kind in {"durable_fact", "pitfall_warning"} and r.status == "active"
        ]

        clusters = self._find_topic_clusters(durable)
        insights: list[ReflectionInsight] = []

        for cluster in clusters[:limit]:
            if len(cluster) < _MIN_CLUSTER_SIZE:
                continue
            insight = self._synthesize_cluster(cluster)
            if insight is not None:
                insights.append(insight)

        if insights:
            log.info("reflection_complete", insight_count=len(insights))
        return insights

    def promote_insight(
        self,
        insight: ReflectionInsight,
        store: KernelStore,
        *,
        task_id: str = "",
        conversation_id: str | None = None,
    ) -> MemoryRecord | None:
        """Promote a reflection insight as a new belief/memory.

        Reflection-origin beliefs require confidence >= 0.7.
        """
        if insight.confidence < _REFLECTION_CONFIDENCE_THRESHOLD:
            log.debug(
                "reflection_insight_below_threshold",
                confidence=insight.confidence,
                threshold=_REFLECTION_CONFIDENCE_THRESHOLD,
            )
            return None

        tid = task_id or f"reflect-{uuid.uuid4().hex[:8]}"
        record = store.create_memory_record(
            task_id=tid,
            conversation_id=conversation_id,
            category="project_convention",
            claim_text=insight.insight_text,
            structured_assertion={
                "epistemic_origin": "reflection",
                "insight_type": insight.insight_type,
                "source_memory_ids": list(insight.source_memory_ids),
                "reflected_at": time.time(),
            },
            scope_kind="workspace",
            scope_ref="workspace:default",
            promotion_reason="reflection_synthesis",
            retention_class="project_convention",
            memory_kind="durable_fact",
            confidence=insight.confidence,
            trust_tier="durable",
            evidence_refs=list(insight.source_memory_ids),
        )
        log.info(
            "reflection_insight_promoted",
            memory_id=record.memory_id,
            insight_type=insight.insight_type,
            source_count=len(insight.source_memory_ids),
        )
        return record

    def _find_topic_clusters(
        self,
        records: list[MemoryRecord],
    ) -> list[list[MemoryRecord]]:
        """Group memories by shared topic tokens."""
        topic_map: dict[str, list[MemoryRecord]] = {}
        for r in records:
            tokens = topic_tokens(r.claim_text)
            key = "-".join(sorted(tokens)[:3]) if tokens else ""
            if key:
                topic_map.setdefault(key, []).append(r)

        clusters = [group for group in topic_map.values() if len(group) >= _MIN_CLUSTER_SIZE]
        clusters.sort(key=len, reverse=True)
        return clusters

    def _synthesize_cluster(
        self,
        cluster: list[MemoryRecord],
    ) -> ReflectionInsight | None:
        """Create a generalized insight from a memory cluster."""
        if len(cluster) < _MIN_CLUSTER_SIZE:
            return None

        claims = [r.claim_text for r in cluster]
        avg_confidence = sum(r.confidence for r in cluster) / len(cluster)

        # Check for contradictions
        has_pitfall = any(r.memory_kind == "pitfall_warning" for r in cluster)
        if has_pitfall:
            insight_type = "contradiction_resolution"
            prefix = "Resolved pattern: "
        elif len(cluster) >= 5:
            insight_type = "generalization"
            prefix = "Generalized pattern: "
        else:
            insight_type = "pattern"
            prefix = "Observed pattern: "

        # Build insight text from common keywords
        all_tokens: dict[str, int] = {}
        for claim in claims:
            for token in topic_tokens(claim):
                all_tokens[token] = all_tokens.get(token, 0) + 1

        common_tokens = sorted(all_tokens, key=lambda t: all_tokens[t], reverse=True)[:5]
        common_phrase = ", ".join(common_tokens) if common_tokens else "multiple observations"

        insight_text = (
            f"{prefix}{len(cluster)} memories relate to [{common_phrase}]. "
            f"Average confidence: {avg_confidence:.2f}."
        )

        return ReflectionInsight(
            insight_text=insight_text,
            source_memory_ids=tuple(r.memory_id for r in cluster),
            confidence=min(avg_confidence * 1.1, 0.95),
            insight_type=insight_type,
        )


__all__ = ["ReflectionInsight", "ReflectionService"]
