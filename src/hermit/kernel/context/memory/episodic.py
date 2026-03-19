from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import structlog

from hermit.kernel.context.memory.episodic_models import (
    EpisodeIndex,
    EpisodicResult,
)

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.models.records import MemoryRecord

log = structlog.get_logger()


class EpisodicMemoryService:
    """Links memories to task execution episodes for temporal and contextual retrieval.

    Episodes are stored as memory_records with memory_kind="episode_index",
    reusing the existing table rather than adding new schema.
    """

    def index_episode(
        self,
        task_id: str,
        store: KernelStore,
        *,
        conversation_id: str | None = None,
    ) -> EpisodeIndex | None:
        """Create an episode index entry for a completed task.

        Scans the task's memories, artifacts, and receipts to build
        a structured episode record.
        """
        memories = store.list_memory_records(task_id=task_id, status="active", limit=500)
        if not memories:
            return None

        memory_ids = tuple(m.memory_id for m in memories)

        artifact_ids: tuple[str, ...] = ()
        tool_names: tuple[str, ...] = ()
        try:
            receipts = store.list_receipts(task_id=task_id, limit=200)
            artifact_ids = tuple(ref for r in receipts for ref in (r.output_refs or []))
            tool_names = tuple(sorted({r.action_type for r in receipts if r.action_type}))
        except Exception:
            log.debug("episodic_index_receipts_unavailable", task_id=task_id)

        now = time.time()
        episode_id = f"ep-{uuid.uuid4().hex[:12]}"

        assertion = {
            "episode_id": episode_id,
            "task_id": task_id,
            "memory_ids": list(memory_ids),
            "artifact_ids": list(artifact_ids),
            "tool_names": list(tool_names),
            "indexed_at": now,
        }

        store.create_memory_record(
            task_id=task_id,
            conversation_id=conversation_id,
            category="other",
            claim_text=f"Episode index for task {task_id}: {len(memory_ids)} memories, "
            f"{len(tool_names)} tools used",
            structured_assertion=assertion,
            scope_kind="workspace",
            scope_ref="workspace:default",
            promotion_reason="episode_indexing",
            retention_class="volatile_fact",
            memory_kind="episode_index",
            confidence=0.8,
            trust_tier="observed",
        )

        index = EpisodeIndex(
            episode_id=episode_id,
            task_id=task_id,
            memory_ids=memory_ids,
            artifact_ids=artifact_ids,
            tool_names=tool_names,
            created_at=now,
        )
        log.info(
            "episode_indexed",
            episode_id=episode_id,
            task_id=task_id,
            memory_count=len(memory_ids),
            tool_count=len(tool_names),
        )
        return index

    def query_by_episode(
        self,
        task_id: str,
        store: KernelStore,
    ) -> list[MemoryRecord]:
        """Retrieve all memories associated with a specific task episode."""
        episodes = self._find_episode_records(store, task_id=task_id)
        if not episodes:
            return store.list_memory_records(task_id=task_id, status="active", limit=500)

        memory_ids: set[str] = set()
        for ep in episodes:
            assertion = dict(ep.structured_assertion or {})
            memory_ids.update(assertion.get("memory_ids", []))

        results: list[MemoryRecord] = []
        for mid in memory_ids:
            record = store.get_memory_record(mid)
            if record is not None and record.status == "active":
                results.append(record)
        return results

    def query_by_artifact(
        self,
        artifact_pattern: str,
        store: KernelStore,
        *,
        limit: int = 20,
    ) -> list[EpisodicResult]:
        """Find memories linked to episodes that produced matching artifacts."""
        all_episodes = self._find_all_episode_records(store)
        results: list[EpisodicResult] = []

        for ep in all_episodes:
            assertion = dict(ep.structured_assertion or {})
            artifact_ids = assertion.get("artifact_ids", [])
            if not any(artifact_pattern in aid for aid in artifact_ids):
                continue

            episode_id = assertion.get("episode_id", "")
            for mid in assertion.get("memory_ids", []):
                record = store.get_memory_record(mid)
                if record is not None and record.status == "active":
                    results.append(
                        EpisodicResult(
                            memory_id=record.memory_id,
                            task_id=record.task_id,
                            claim_text=record.claim_text,
                            match_reason=f"artifact_match:{artifact_pattern}",
                            episode_id=episode_id,
                        )
                    )
            if len(results) >= limit:
                break

        return results[:limit]

    def query_by_tool(
        self,
        tool_name: str,
        store: KernelStore,
        *,
        limit: int = 20,
    ) -> list[EpisodicResult]:
        """Find memories linked to episodes that used a specific tool."""
        all_episodes = self._find_all_episode_records(store)
        results: list[EpisodicResult] = []

        for ep in all_episodes:
            assertion = dict(ep.structured_assertion or {})
            tool_names = assertion.get("tool_names", [])
            if tool_name not in tool_names:
                continue

            episode_id = assertion.get("episode_id", "")
            for mid in assertion.get("memory_ids", []):
                record = store.get_memory_record(mid)
                if record is not None and record.status == "active":
                    results.append(
                        EpisodicResult(
                            memory_id=record.memory_id,
                            task_id=record.task_id,
                            claim_text=record.claim_text,
                            match_reason=f"tool_match:{tool_name}",
                            episode_id=episode_id,
                        )
                    )
            if len(results) >= limit:
                break

        return results[:limit]

    def decay_stale_episodes(
        self,
        store: KernelStore,
        *,
        max_age_days: int = 30,
    ) -> int:
        """Invalidate episode index records older than max_age_days."""
        cutoff = time.time() - (max_age_days * 86400)
        all_episodes = self._find_all_episode_records(store)
        count = 0

        for ep in all_episodes:
            if (ep.created_at or 0.0) < cutoff:
                store.update_memory_record(
                    ep.memory_id,
                    status="invalidated",
                    invalidation_reason=f"episode_decay:age>{max_age_days}d",
                    invalidated_at=time.time(),
                )
                count += 1

        if count:
            log.info("stale_episodes_decayed", count=count, max_age_days=max_age_days)
        return count

    @staticmethod
    def _find_episode_records(
        store: KernelStore,
        *,
        task_id: str,
    ) -> list[MemoryRecord]:
        """Find episode_index records for a specific task."""
        return store.list_memory_records(
            task_id=task_id,
            status="active",
            memory_kind="episode_index",
            limit=100,
        )

    @staticmethod
    def _find_all_episode_records(
        store: KernelStore,
    ) -> list[MemoryRecord]:
        """Find all active episode_index records."""
        return store.list_memory_records(
            status="active",
            memory_kind="episode_index",
            limit=5000,
        )


__all__ = ["EpisodicMemoryService"]
