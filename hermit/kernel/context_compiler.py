from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from hermit.builtin.memory.engine import MemoryEngine
from hermit.builtin.memory.types import MemoryEntry
from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.context import TaskExecutionContext, WorkingStateSnapshot
from hermit.kernel.memory_governance import MemoryGovernanceService
from hermit.kernel.models import BeliefRecord, MemoryRecord
from hermit.kernel.store_support import _canonical_json, _sha256_hex


@dataclass
class ContextPack:
    static_memory: list[dict[str, Any]]
    retrieval_memory: list[dict[str, Any]]
    selected_beliefs: list[dict[str, Any]]
    working_state: dict[str, Any]
    selection_reasons: dict[str, str]
    excluded_memory_ids: list[str]
    excluded_reasons: dict[str, str]
    pack_hash: str
    artifact_uri: str | None = None
    artifact_hash: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": "context.pack/v1",
            "static_memory": self.static_memory,
            "retrieval_memory": self.retrieval_memory,
            "selected_beliefs": self.selected_beliefs,
            "working_state": self.working_state,
            "selection_reasons": self.selection_reasons,
            "excluded_memory_ids": self.excluded_memory_ids,
            "excluded_reasons": self.excluded_reasons,
            "pack_hash": self.pack_hash,
            "artifact_uri": self.artifact_uri,
            "artifact_hash": self.artifact_hash,
        }


class ContextCompiler:
    def __init__(
        self,
        governance: MemoryGovernanceService | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self.governance = governance or MemoryGovernanceService()
        self.artifact_store = artifact_store

    def compile(
        self,
        *,
        context: TaskExecutionContext,
        working_state: WorkingStateSnapshot,
        beliefs: list[BeliefRecord],
        memories: list[MemoryRecord],
        query: str,
    ) -> ContextPack:
        selection_reasons: dict[str, str] = {}
        excluded_reasons: dict[str, str] = {}
        static_memory: list[dict[str, Any]] = []
        retrieval_candidates: list[tuple[MemoryRecord, float]] = []

        for memory in memories:
            if memory.status != "active":
                excluded_reasons[memory.memory_id] = f"status:{memory.status}"
                continue
            if self.governance.is_expired(memory):
                excluded_reasons[memory.memory_id] = "expired"
                continue
            if self.governance.eligible_for_static(memory, context=context):
                static_memory.append(self._memory_payload(memory))
                selection_reasons[memory.memory_id] = "static_policy"
                continue
            retrieval_reason = self.governance.retrieval_reason(memory, context=context)
            if retrieval_reason is None:
                excluded_reasons[memory.memory_id] = "out_of_scope"
                continue
            retrieval_candidates.append((memory, self._retrieval_score(memory, context=context, query=query)))

        retrieval_candidates.sort(
            key=lambda item: (
                item[1],
                item[0].confidence,
                item[0].updated_at or 0.0,
            ),
            reverse=True,
        )
        retrieval_memory = [self._memory_payload(memory) for memory, _ in retrieval_candidates[:5]]
        for memory, _score in retrieval_candidates[:5]:
            selection_reasons[memory.memory_id] = "retrieval_rank"
        for memory, _score in retrieval_candidates[5:]:
            excluded_reasons[memory.memory_id] = "rank_cutoff"

        selected_beliefs: list[dict[str, Any]] = []
        for belief in beliefs[:10]:
            if not self.governance.scope_matches(
                scope_kind=belief.scope_kind,
                scope_ref=belief.scope_ref,
                context=context,
            ):
                continue
            selected_beliefs.append(
                {
                    "belief_id": belief.belief_id,
                    "claim_text": belief.claim_text,
                    "scope_kind": belief.scope_kind,
                    "scope_ref": belief.scope_ref,
                    "confidence": belief.confidence,
                }
            )

        payload = {
            "static_memory": static_memory,
            "retrieval_memory": retrieval_memory,
            "selected_beliefs": selected_beliefs,
            "working_state": asdict(working_state),
            "selection_reasons": selection_reasons,
            "excluded_memory_ids": sorted(excluded_reasons),
            "excluded_reasons": excluded_reasons,
        }
        pack_hash = _sha256_hex(_canonical_json(payload))
        artifact_uri = None
        artifact_hash = None
        if self.artifact_store is not None:
            artifact_uri, artifact_hash = self.artifact_store.store_json(
                {"kind": "context.pack/v1", **payload, "pack_hash": pack_hash}
            )
        return ContextPack(
            static_memory=static_memory,
            retrieval_memory=retrieval_memory,
            selected_beliefs=selected_beliefs,
            working_state=asdict(working_state),
            selection_reasons=selection_reasons,
            excluded_memory_ids=sorted(excluded_reasons),
            excluded_reasons=excluded_reasons,
            pack_hash=pack_hash,
            artifact_uri=artifact_uri,
            artifact_hash=artifact_hash,
        )

    def render_static_prompt(self, pack: ContextPack) -> str:
        categories = self._categories_from_payload(pack.static_memory)
        return MemoryEngine.summary_prompt(categories, limit_per_category=3)

    def render_retrieval_prompt(self, pack: ContextPack) -> str:
        categories = self._categories_from_payload(pack.retrieval_memory)
        return MemoryEngine.summary_prompt(categories, limit_per_category=5).replace(
            "以下是跨会话记忆，请优先遵循其中的长期约定：",
            "以下是与当前任务最相关的跨会话记忆，只在相关时优先遵循：",
        )

    def _memory_payload(self, memory: MemoryRecord) -> dict[str, Any]:
        assertion = dict(memory.structured_assertion or {})
        return {
            "memory_id": memory.memory_id,
            "category": memory.category,
            "claim_text": memory.claim_text,
            "scope_kind": memory.scope_kind,
            "scope_ref": memory.scope_ref,
            "retention_class": memory.retention_class,
            "subject_key": assertion.get("subject_key", "") or self.governance.subject_key_for_memory(memory),
            "topic_key": assertion.get("topic_key", "") or self.governance.topic_key_for_memory(memory),
            "governance_explanation": list(assertion.get("explanation", []))
            or self.governance.inspect_claim(
                category=memory.category,
                claim_text=memory.claim_text,
                conversation_id=memory.conversation_id,
                workspace_root=memory.scope_ref if memory.scope_kind == "workspace" else "",
                promotion_reason=memory.promotion_reason,
            )["explanation"],
            "confidence": memory.confidence,
            "trust_tier": memory.trust_tier,
            "supersedes": list(memory.supersedes),
            "expires_at": memory.expires_at,
            "updated_at": memory.updated_at,
        }

    @staticmethod
    def _categories_from_payload(items: list[dict[str, Any]]) -> dict[str, list[MemoryEntry]]:
        categories: dict[str, list[MemoryEntry]] = {}
        for item in items:
            categories.setdefault(str(item["category"]), []).append(
                MemoryEntry(
                    category=str(item["category"]),
                    content=str(item["claim_text"]),
                    confidence=float(item.get("confidence", 0.5)),
                    supersedes=list(item.get("supersedes", [])),
                    scope_kind=str(item.get("scope_kind") or ""),
                    scope_ref=str(item.get("scope_ref") or ""),
                    retention_class=str(item.get("retention_class") or ""),
                )
            )
        return categories

    def _retrieval_score(self, memory: MemoryRecord, *, context: TaskExecutionContext, query: str) -> float:
        score = 0.0
        if self.governance.scope_matches(memory.scope_kind, memory.scope_ref, context=context):
            score += 100.0
        if memory.expires_at is not None:
            score += max(0.0, memory.expires_at) / 1_000_000_000_000.0
        if MemoryEngine._shares_topic(memory.claim_text, query):
            score += 10.0
        score += 5.0 if memory.trust_tier == "durable" else 0.0
        score += float(memory.updated_at or 0.0) / 1_000_000_000_000.0
        return score


__all__ = ["ContextCompiler", "ContextPack"]
