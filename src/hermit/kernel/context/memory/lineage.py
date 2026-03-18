from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

import structlog

from hermit.kernel.context.memory.lineage_models import (
    DecisionLineage,
    InfluenceLink,
    MemoryImpact,
    StaleMemory,
)

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()


class MemoryLineageService:
    """Tracks causal chains from memories to decisions.

    Influence links are stored as memory_records with memory_kind="influence_link",
    reusing the existing table for zero-schema-change integration.
    """

    def record_influence(
        self,
        context_pack_id: str,
        decision_ids: list[str],
        memory_ids: list[str],
        store: KernelStore,
        *,
        task_id: str = "",
        conversation_id: str | None = None,
    ) -> list[InfluenceLink]:
        """Record that a set of memories influenced a set of decisions."""
        if not decision_ids or not memory_ids:
            return []

        now = time.time()
        links: list[InfluenceLink] = []

        for decision_id in decision_ids:
            for memory_id in memory_ids:
                link_id = f"infl-{uuid.uuid4().hex[:12]}"
                link = InfluenceLink(
                    link_id=link_id,
                    context_pack_id=context_pack_id,
                    decision_id=decision_id,
                    memory_id=memory_id,
                    created_at=now,
                )
                assertion = {
                    "link_id": link_id,
                    "context_pack_id": context_pack_id,
                    "decision_id": decision_id,
                    "memory_id": memory_id,
                    "linked_at": now,
                }
                store.create_memory_record(
                    task_id=task_id or f"lineage-{link_id}",
                    conversation_id=conversation_id,
                    category="other",
                    claim_text=f"Memory {memory_id} influenced decision {decision_id}",
                    structured_assertion=assertion,
                    scope_kind="workspace",
                    scope_ref="workspace:default",
                    promotion_reason="lineage_tracking",
                    retention_class="volatile_fact",
                    memory_kind="influence_link",
                    confidence=0.9,
                    trust_tier="observed",
                )
                links.append(link)

        log.debug(
            "influence_recorded",
            context_pack_id=context_pack_id,
            decisions=len(decision_ids),
            memories=len(memory_ids),
            links=len(links),
        )
        return links

    def trace_decision(
        self,
        decision_id: str,
        store: KernelStore,
    ) -> DecisionLineage:
        """Find all memories that influenced a given decision."""
        all_links: list[dict[str, Any]] = self._find_all_influence_links(store)
        matching: list[dict[str, Any]] = [
            lnk for lnk in all_links if lnk.get("decision_id") == decision_id
        ]

        memory_ids: list[str] = list(
            dict.fromkeys(str(lnk.get("memory_id", "")) for lnk in matching)
        )
        pack_ids: list[str] = list(
            dict.fromkeys(str(lnk.get("context_pack_id", "")) for lnk in matching)
        )

        return DecisionLineage(
            decision_id=decision_id,
            influencing_memories=memory_ids,
            context_pack_ids=pack_ids,
            link_count=len(matching),
        )

    def trace_memory(
        self,
        memory_id: str,
        store: KernelStore,
    ) -> MemoryImpact:
        """Find all decisions influenced by a given memory and compute outcomes."""
        all_links: list[dict[str, Any]] = self._find_all_influence_links(store)
        matching: list[dict[str, Any]] = [
            lnk for lnk in all_links if lnk.get("memory_id") == memory_id
        ]

        decision_ids: list[str] = list(
            dict.fromkeys(str(lnk.get("decision_id", "")) for lnk in matching)
        )

        success_count = 0
        failure_count = 0
        for did in decision_ids:
            decision = store.get_decision(did)
            if decision is None:
                continue
            if decision.verdict in {"approved", "succeeded", "granted"}:
                success_count += 1
            elif decision.verdict in {"denied", "failed", "rejected"}:
                failure_count += 1

        total = success_count + failure_count
        failure_rate = failure_count / total if total > 0 else 0.0

        return MemoryImpact(
            memory_id=memory_id,
            influenced_decisions=decision_ids,
            total_decisions=len(decision_ids),
            success_count=success_count,
            failure_count=failure_count,
            failure_rate=failure_rate,
        )

    def find_stale_influencers(
        self,
        store: KernelStore,
        *,
        min_decisions: int = 5,
        failure_rate_threshold: float = 0.5,
    ) -> list[StaleMemory]:
        """Find memories with high failure rates across decisions they influenced."""
        all_links: list[dict[str, Any]] = self._find_all_influence_links(store)

        memory_decision_map: dict[str, set[str]] = {}
        for lnk in all_links:
            mid: str = str(lnk.get("memory_id", ""))
            did: str = str(lnk.get("decision_id", ""))
            if mid and did:
                memory_decision_map.setdefault(mid, set()).add(did)

        stale: list[StaleMemory] = []
        for mid, dids in memory_decision_map.items():
            if len(dids) < min_decisions:
                continue

            impact = self.trace_memory(mid, store)
            if impact.failure_rate < failure_rate_threshold:
                continue

            record = store.get_memory_record(mid)
            if record is None or record.status != "active":
                continue

            stale.append(
                StaleMemory(
                    memory_id=mid,
                    claim_text=record.claim_text,
                    decision_count=impact.total_decisions,
                    failure_rate=impact.failure_rate,
                    category=record.category,
                )
            )

        stale.sort(key=lambda s: s.failure_rate, reverse=True)
        if stale:
            log.info(
                "stale_influencers_found",
                count=len(stale),
                threshold=failure_rate_threshold,
            )
        return stale

    @staticmethod
    def _find_all_influence_links(store: KernelStore) -> list[dict[str, Any]]:
        """Retrieve all influence_link records as dicts."""
        all_records = store.list_memory_records(status="active", limit=5000)
        links: list[dict[str, Any]] = []
        for r in all_records:
            if r.memory_kind != "influence_link":
                continue
            links.append(dict(r.structured_assertion or {}))
        return links


__all__ = ["MemoryLineageService"]
