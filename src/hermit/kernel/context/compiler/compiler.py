from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from hermit.infra.system.i18n import tr, tr_list_all_locales
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.memory.governance import MemoryGovernanceService
from hermit.kernel.context.memory.text import shares_topic, summary_prompt, topic_tokens
from hermit.kernel.context.models.context import TaskExecutionContext, WorkingStateSnapshot
from hermit.kernel.ledger.journal.store_support import canonical_json as _canonical_json
from hermit.kernel.ledger.journal.store_support import sha256_hex as _sha256_hex
from hermit.kernel.task.models.records import BeliefRecord, MemoryRecord
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry


def _greeting_queries() -> set[str]:
    return set(tr_list_all_locales("kernel.nlp.greeting_texts"))


def _followup_markers() -> tuple[str, ...]:
    return tuple(tr_list_all_locales("kernel.nlp.followup_markers"))


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
    kind: str = "context.pack/v3"
    task_summary: dict[str, Any] = field(default_factory=dict[str, Any])
    step_summary: dict[str, Any] = field(default_factory=dict[str, Any])
    policy_summary: dict[str, Any] = field(default_factory=dict[str, Any])
    planning_state: dict[str, Any] = field(default_factory=dict[str, Any])
    episodic_context: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    procedural_context: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    carry_forward: dict[str, Any] | None = None
    continuation_guidance: dict[str, Any] | None = None
    recent_notes: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    relevant_artifact_refs: list[str] = field(default_factory=list[str])
    ingress_artifact_refs: list[str] = field(default_factory=list[str])
    focus_summary: dict[str, Any] | None = None
    bound_ingress_deltas: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    session_projection_ref: str | None = None
    artifact_uri: str | None = None
    artifact_hash: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "static_memory": self.static_memory,
            "retrieval_memory": self.retrieval_memory,
            "selected_beliefs": self.selected_beliefs,
            "working_state": self.working_state,
            "episodic_context": self.episodic_context,
            "procedural_context": self.procedural_context,
            "task_summary": self.task_summary,
            "step_summary": self.step_summary,
            "policy_summary": self.policy_summary,
            "planning_state": self.planning_state,
            "carry_forward": self.carry_forward,
            "continuation_guidance": self.continuation_guidance,
            "recent_notes": self.recent_notes,
            "relevant_artifact_refs": self.relevant_artifact_refs,
            "ingress_artifact_refs": self.ingress_artifact_refs,
            "focus_summary": self.focus_summary,
            "bound_ingress_deltas": self.bound_ingress_deltas,
            "session_projection_ref": self.session_projection_ref,
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
        retrieval_service: Any | None = None,
        store: Any | None = None,
    ) -> None:
        self.governance = governance or MemoryGovernanceService()
        self.artifact_store = artifact_store
        self._retrieval_service = retrieval_service
        self._store = store

    def compile(
        self,
        *,
        context: TaskExecutionContext,
        working_state: WorkingStateSnapshot,
        beliefs: list[BeliefRecord],
        memories: list[MemoryRecord],
        query: str,
        task_summary: dict[str, Any] | None = None,
        step_summary: dict[str, Any] | None = None,
        policy_summary: dict[str, Any] | None = None,
        planning_state: dict[str, Any] | None = None,
        carry_forward: dict[str, Any] | None = None,
        continuation_guidance: dict[str, Any] | None = None,
        recent_notes: list[dict[str, Any]] | None = None,
        relevant_artifact_refs: list[str] | None = None,
        ingress_artifact_refs: list[str] | None = None,
        focus_summary: dict[str, Any] | None = None,
        bound_ingress_deltas: list[dict[str, Any]] | None = None,
        session_projection_ref: str | None = None,
    ) -> ContextPack:
        selection_reasons: dict[str, str] = {}
        excluded_reasons: dict[str, str] = {}
        static_memory: list[dict[str, Any]] = []
        retrieval_candidates: list[tuple[MemoryRecord, float]] = []
        query_text = self._normalize_query(query)
        suppress_contextual_retrieval = self._is_smalltalk_query(query_text)

        for memory in memories:
            if memory.status == "quarantined":
                excluded_reasons[memory.memory_id] = "quarantined"
                continue
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
            if suppress_contextual_retrieval:
                excluded_reasons[memory.memory_id] = "smalltalk_query"
                continue
            if not self._memory_relevant_to_query(memory, query=query_text):
                excluded_reasons[memory.memory_id] = "query_irrelevant"
                continue
            retrieval_candidates.append(
                (memory, self._retrieval_score(memory, context=context, query=query_text))
            )

        # Hybrid retrieval path: use HybridRetrievalService when available
        used_hybrid = False
        if self._retrieval_service is not None and self._store is not None and retrieval_candidates:
            try:
                eligible = [m for m, _ in retrieval_candidates]
                report = self._retrieval_service.retrieve(
                    query_text, eligible, self._store, context=context, limit=5
                )
                retrieval_memory = [self._memory_payload(r.memory) for r in report.results]
                result_ids = {r.memory_id for r in report.results}
                for r in report.results:
                    selection_reasons[r.memory_id] = f"hybrid:{','.join(r.sources)}"
                for m, _ in retrieval_candidates:
                    if m.memory_id not in result_ids:
                        excluded_reasons[m.memory_id] = "hybrid_rank_cutoff"
                used_hybrid = True
            except Exception:
                pass  # Fall through to legacy scoring

        if not used_hybrid:
            retrieval_candidates.sort(
                key=lambda item: (
                    item[1],
                    item[0].confidence,
                    item[0].updated_at or 0.0,
                ),
                reverse=True,
            )
            retrieval_memory = [
                self._memory_payload(memory) for memory, _ in retrieval_candidates[:5]
            ]
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
            "kind": "context.pack/v3",
            "static_memory": static_memory,
            "retrieval_memory": retrieval_memory,
            "selected_beliefs": selected_beliefs,
            "working_state": asdict(working_state),
            "task_summary": dict(task_summary or {}),
            "step_summary": dict(step_summary or {}),
            "policy_summary": dict(policy_summary or {}),
            "planning_state": dict(planning_state or {}),
            "carry_forward": dict(carry_forward or {}) or None,
            "continuation_guidance": dict(continuation_guidance or {}) or None,
            "recent_notes": list(recent_notes or []),
            "relevant_artifact_refs": list(relevant_artifact_refs or []),
            "ingress_artifact_refs": list(ingress_artifact_refs or []),
            "focus_summary": dict(focus_summary or {}) or None,
            "bound_ingress_deltas": list(bound_ingress_deltas or []),
            "session_projection_ref": session_projection_ref,
            "selection_reasons": selection_reasons,
            "excluded_memory_ids": sorted(excluded_reasons),
            "excluded_reasons": excluded_reasons,
        }
        pack_hash = _sha256_hex(_canonical_json(payload))
        artifact_uri = None
        artifact_hash = None
        if self.artifact_store is not None:
            artifact_uri, artifact_hash = self.artifact_store.store_json(
                {**payload, "pack_hash": pack_hash}
            )
        return ContextPack(
            kind="context.pack/v3",
            static_memory=static_memory,
            retrieval_memory=retrieval_memory,
            selected_beliefs=selected_beliefs,
            working_state=asdict(working_state),
            task_summary=dict(task_summary or {}),
            step_summary=dict(step_summary or {}),
            policy_summary=dict(policy_summary or {}),
            planning_state=dict(planning_state or {}),
            carry_forward=dict(carry_forward or {}) or None,
            continuation_guidance=dict(continuation_guidance or {}) or None,
            recent_notes=list(recent_notes or []),
            relevant_artifact_refs=list(relevant_artifact_refs or []),
            ingress_artifact_refs=list(ingress_artifact_refs or []),
            focus_summary=dict(focus_summary or {}) or None,
            bound_ingress_deltas=list(bound_ingress_deltas or []),
            session_projection_ref=session_projection_ref,
            selection_reasons=selection_reasons,
            excluded_memory_ids=sorted(excluded_reasons),
            excluded_reasons=excluded_reasons,
            pack_hash=pack_hash,
            artifact_uri=artifact_uri,
            artifact_hash=artifact_hash,
        )

    def render_static_prompt(self, pack: ContextPack) -> str:
        categories = self._categories_from_payload(pack.static_memory)
        return summary_prompt(categories, limit_per_category=3)

    def render_retrieval_prompt(self, pack: ContextPack) -> str:
        categories = self._categories_from_payload(pack.retrieval_memory)
        return summary_prompt(
            categories,
            limit_per_category=5,
            intro=tr("kernel.memory.retrieval_intro"),
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
            "subject_key": assertion.get("subject_key", "")
            or self.governance.subject_key_for_memory(memory),
            "topic_key": assertion.get("topic_key", "")
            or self.governance.topic_key_for_memory(memory),
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
            "freshness_class": memory.freshness_class,
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

    def _retrieval_score(
        self, memory: MemoryRecord, *, context: TaskExecutionContext, query: str
    ) -> float:
        score = 0.0
        if self.governance.scope_matches(memory.scope_kind, memory.scope_ref, context=context):
            score += 100.0
        if memory.expires_at is not None:
            score += max(0.0, memory.expires_at) / 1_000_000_000_000.0
        if shares_topic(memory.claim_text, query):
            score += 10.0
        score += 5.0 if memory.trust_tier == "durable" else 0.0
        score += float(memory.updated_at or 0.0) / 1_000_000_000_000.0
        return score

    @staticmethod
    def _normalize_query(query: str) -> str:
        return " ".join(str(query or "").split()).strip()

    @classmethod
    def _is_smalltalk_query(cls, query: str) -> bool:
        cleaned = re.sub(r"\s+", "", str(query or "")).lower()
        return cleaned in _greeting_queries()

    @staticmethod
    def _is_followup_query(query: str) -> bool:
        cleaned = re.sub(r"\s+", "", str(query or ""))
        return any(marker in cleaned for marker in _followup_markers())

    @classmethod
    def _memory_relevant_to_query(cls, memory: MemoryRecord, *, query: str) -> bool:
        if not query:
            return False
        if memory.scope_kind != "conversation":
            return True
        if memory.retention_class not in {"task_state", "volatile_fact"}:
            return True
        if cls._is_followup_query(query):
            return True
        query_tokens_set = {token for token in topic_tokens(query) if len(token) >= 2}
        memory_tokens = {token for token in topic_tokens(memory.claim_text) if len(token) >= 2}
        if query_tokens_set & memory_tokens:
            return True
        if any(token in memory.claim_text for token in query_tokens_set):
            return True
        if any(token in query for token in memory_tokens):
            return True
        return shares_topic(memory.claim_text, query)


__all__ = ["ContextCompiler", "ContextPack"]
