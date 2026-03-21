from __future__ import annotations

import functools
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hermit.infra.system.i18n import tr_list_all_locales
from hermit.kernel.context.memory.text import is_duplicate, shares_topic
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.task.models.records import BeliefRecord, MemoryRecord
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry, normalize_category

MemoryScopeKind = str
MemoryRetentionClass = str

_TASK_STATE_TTL_SECONDS = 7 * 24 * 60 * 60
_VOLATILE_FACT_TTL_SECONDS = 24 * 60 * 60


@functools.cache
def _signal_tokens(key: str) -> tuple[str, ...]:
    return tuple(tr_list_all_locales(key))


@functools.cache
def _claim_stop_tokens() -> frozenset[str]:
    return frozenset(tr_list_all_locales("kernel.nlp.claim_stop_tokens"))


@functools.cache
def _subject_hint_patterns() -> tuple[tuple[str, str], ...]:
    raw = tr_list_all_locales("kernel.nlp.subject_hint_patterns")
    result: list[tuple[str, str]] = []
    for item in raw:
        if ":" not in item:
            continue
        pattern, subject = item.rsplit(":", 1)
        result.append((pattern, subject))
    return tuple(result)


@dataclass(frozen=True)
class ClaimSignals:
    sensitive: bool = False
    stable_preference: bool = False
    task_state: bool = False
    project_convention: bool = False
    tooling_environment: bool = False
    subject_key: str = ""
    topic_key: str = ""
    matched_signals: dict[str, list[str]] | None = None


@dataclass(frozen=True)
class MemoryClassification:
    category: str
    scope_kind: MemoryScopeKind
    scope_ref: str
    promotion_reason: str
    retention_class: MemoryRetentionClass
    static_injection: bool
    retrieval_allowed: bool
    subject_key: str = ""
    topic_key: str = ""
    explanation: list[str] | None = None
    structured_assertion: dict[str, object] | None = None
    expires_at: float | None = None


@dataclass(frozen=True)
class MemoryCategoryPolicy:
    retention_class: MemoryRetentionClass
    scope_kind: MemoryScopeKind
    static_injection: bool = False
    retrieval_allowed: bool = True
    ttl_seconds: int | None = None


_DEFAULT_POLICY = MemoryCategoryPolicy(
    retention_class="volatile_fact",
    scope_kind="conversation",
    static_injection=False,
    retrieval_allowed=True,
    ttl_seconds=_VOLATILE_FACT_TTL_SECONDS,
)
_CATEGORY_POLICIES: dict[str, MemoryCategoryPolicy] = {
    "user_preference": MemoryCategoryPolicy(
        "user_preference", "global", static_injection=True, retrieval_allowed=True
    ),
    "project_convention": MemoryCategoryPolicy(
        "project_convention", "workspace", static_injection=True, retrieval_allowed=True
    ),
    "tooling_environment": MemoryCategoryPolicy(
        "tooling_environment", "workspace", static_injection=True, retrieval_allowed=True
    ),
    "tech_decision": MemoryCategoryPolicy(
        "volatile_fact",
        "conversation",
        static_injection=False,
        retrieval_allowed=True,
        ttl_seconds=3 * 24 * 60 * 60,  # 3 days — decisions decay but slower than noise
    ),
    "active_task": MemoryCategoryPolicy(
        "task_state",
        "conversation",
        static_injection=False,
        retrieval_allowed=True,
        ttl_seconds=_TASK_STATE_TTL_SECONDS,
    ),
    "other": MemoryCategoryPolicy(
        "volatile_fact",
        "conversation",
        static_injection=False,
        retrieval_allowed=True,
        ttl_seconds=_VOLATILE_FACT_TTL_SECONDS,
    ),
    "pitfall_warning": MemoryCategoryPolicy(
        "pitfall_warning",
        "workspace",
        static_injection=True,
        retrieval_allowed=True,
    ),
    "procedural": MemoryCategoryPolicy(
        "procedural",
        "workspace",
        static_injection=False,
        retrieval_allowed=True,
    ),
}


class MemoryGovernanceService:
    def policy_for(self, category: str) -> MemoryCategoryPolicy:
        normalized = normalize_category(category)
        return _CATEGORY_POLICIES.get(normalized, _DEFAULT_POLICY)

    def classify_belief(
        self,
        belief: BeliefRecord,
        *,
        workspace_root: str = "",
    ) -> MemoryClassification:
        return self.classify_claim(
            category=belief.category,
            claim_text=belief.claim_text,
            conversation_id=belief.conversation_id,
            workspace_root=workspace_root,
            promotion_reason="belief_promotion",
        )

    def classify_claim(
        self,
        *,
        category: str,
        claim_text: str,
        conversation_id: str | None,
        workspace_root: str = "",
        promotion_reason: str = "belief_promotion",
    ) -> MemoryClassification:
        signals = self.analyze_claim(category=category, claim_text=claim_text)
        resolved_category = self.resolve_category(category=category, signals=signals)
        policy = self.policy_for(resolved_category)
        retention_class = policy.retention_class
        if signals.sensitive:
            retention_class = "sensitive_fact"
        scope_kind = policy.scope_kind
        scope_ref = self._scope_ref_for(
            scope_kind=scope_kind,
            conversation_id=conversation_id,
            workspace_root=workspace_root,
        )
        expires_at = None
        if policy.ttl_seconds:
            expires_at = time.time() + float(policy.ttl_seconds)
        explanation = self._classification_explanation(
            original_category=category,
            resolved_category=resolved_category,
            signals=signals,
            retention_class=retention_class,
            scope_kind=scope_kind,
        )
        return MemoryClassification(
            category=resolved_category,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            promotion_reason=promotion_reason,
            retention_class=retention_class,
            static_injection=policy.static_injection
            and retention_class
            in {
                "user_preference",
                "project_convention",
                "tooling_environment",
                "pitfall_warning",
            },
            retrieval_allowed=policy.retrieval_allowed,
            subject_key=signals.subject_key,
            topic_key=signals.topic_key,
            explanation=explanation,
            structured_assertion={
                "original_category": category,
                "resolved_category": resolved_category,
                "retention_class": retention_class,
                "scope_kind": scope_kind,
                "scope_ref": scope_ref,
                "subject_key": signals.subject_key,
                "topic_key": signals.topic_key,
                "matched_signals": signals.matched_signals or {},
                "explanation": explanation,
            },
            expires_at=expires_at,
        )

    def analyze_claim(self, *, category: str, claim_text: str) -> ClaimSignals:
        text = claim_text.strip()
        cat = normalize_category(category)
        matched_signals = {
            "sensitive": [
                token for token in _signal_tokens("kernel.nlp.signal.sensitive") if token in text
            ],
            "stable_preference": [
                token for token in _signal_tokens("kernel.nlp.signal.preference") if token in text
            ],
            "task_state": [
                token for token in _signal_tokens("kernel.nlp.signal.task_state") if token in text
            ],
            "project_convention": [
                token
                for token in _signal_tokens("kernel.nlp.signal.project_convention")
                if token in text
            ],
            "tooling_environment": [
                token for token in _signal_tokens("kernel.nlp.signal.tooling") if token in text
            ],
        }
        return ClaimSignals(
            sensitive=bool(matched_signals["sensitive"]),
            stable_preference=cat == "user_preference"
            or bool(matched_signals["stable_preference"]),
            task_state=cat == "active_task" or bool(matched_signals["task_state"]),
            project_convention=cat == "project_convention"
            or bool(matched_signals["project_convention"]),
            tooling_environment=cat == "tooling_environment"
            or bool(matched_signals["tooling_environment"]),
            subject_key=self._subject_key(text),
            topic_key=self._topic_key(text),
            matched_signals={name: hits for name, hits in matched_signals.items() if hits},
        )

    def resolve_category(self, *, category: str, signals: ClaimSignals) -> str:
        cat = normalize_category(category)
        if signals.stable_preference:
            return "user_preference"
        if signals.tooling_environment and not signals.project_convention:
            return "tooling_environment"
        if signals.tooling_environment and cat in {"other", "tooling_environment", "tech_decision"}:
            return "tooling_environment"
        if signals.project_convention:
            return "project_convention"
        if signals.task_state:
            return "active_task"
        return cat

    def filter_static_categories(
        self, categories: dict[str, list[MemoryEntry]]
    ) -> dict[str, list[MemoryEntry]]:
        filtered: dict[str, list[MemoryEntry]] = {}
        for category, entries in categories.items():
            if not entries:
                continue
            if not self.policy_for(category).static_injection:
                continue
            filtered[category] = entries
        return filtered

    def eligible_for_static(self, memory: MemoryRecord, *, context: TaskExecutionContext) -> bool:
        if memory.retention_class not in {
            "user_preference",
            "project_convention",
            "tooling_environment",
            "pitfall_warning",
        }:
            return False
        return self.scope_matches(memory.scope_kind, memory.scope_ref, context=context)

    def retrieval_reason(
        self, memory: MemoryRecord, *, context: TaskExecutionContext
    ) -> str | None:
        # Enforce retrieval_allowed from classification policy.
        policy = self.policy_for(memory.category)
        if not policy.retrieval_allowed:
            return None
        if memory.retention_class in {"invalidated", "revoked"}:
            return None
        if memory.retention_class == "sensitive_fact":
            return (
                "scope_match"
                if self.scope_matches(memory.scope_kind, memory.scope_ref, context=context)
                else None
            )
        return (
            "retrieval_policy"
            if self.scope_matches(memory.scope_kind, memory.scope_ref, context=context)
            else None
        )

    def scope_matches(
        self,
        scope_kind: str,
        scope_ref: str,
        *,
        context: TaskExecutionContext,
    ) -> bool:
        if scope_kind == "global":
            return True
        if scope_kind == "conversation":
            return scope_ref == context.conversation_id
        if scope_kind == "workspace":
            normalized = (
                str(Path(context.workspace_root or "").resolve())
                if context.workspace_root
                else "workspace:default"
            )
            return scope_ref in {normalized, "workspace:default"}
        if scope_kind == "entity":
            return scope_ref in {context.task_id, context.step_id, context.step_attempt_id}
        return False

    def is_expired(self, memory: MemoryRecord) -> bool:
        return memory.expires_at is not None and float(memory.expires_at) <= time.time()

    def candidate_records_for_supersede(
        self,
        *,
        classification: MemoryClassification,
        active_records: list[MemoryRecord],
    ) -> list[MemoryRecord]:
        candidates: list[MemoryRecord] = []
        for record in active_records:
            if record.status != "active":
                continue
            if record.retention_class != classification.retention_class:
                continue
            if classification.retention_class == "task_state":
                if not self._subject_matches(
                    classification.subject_key, self.subject_key_for_memory(record)
                ):
                    continue
                candidates.append(record)
                continue
            if record.scope_kind != classification.scope_kind:
                continue
            if record.scope_ref != classification.scope_ref:
                continue
            candidates.append(record)
        return candidates

    def find_superseded_records(
        self,
        *,
        classification: MemoryClassification,
        claim_text: str,
        active_records: list[MemoryRecord],
        entry_from_record: Callable[[MemoryRecord], MemoryEntry],
    ) -> tuple[MemoryRecord | None, list[MemoryRecord]]:
        duplicate: MemoryRecord | None = None
        superseded: list[MemoryRecord] = []
        for record in self.candidate_records_for_supersede(
            classification=classification,
            active_records=active_records,
        ):
            entry = entry_from_record(record)
            if is_duplicate([entry], claim_text):
                duplicate = record
                break
            if classification.retention_class == "task_state":
                if self._task_state_conflicts(
                    left_claim=record.claim_text,
                    right_claim=claim_text,
                    left_subject=self.subject_key_for_memory(record),
                    right_subject=classification.subject_key,
                ):
                    superseded.append(record)
                continue
            if shares_topic(record.claim_text, claim_text):
                superseded.append(record)
        return duplicate, superseded

    def inspect_claim(
        self,
        *,
        category: str,
        claim_text: str,
        conversation_id: str | None,
        workspace_root: str = "",
        promotion_reason: str = "belief_promotion",
    ) -> dict[str, object]:
        classification = self.classify_claim(
            category=category,
            claim_text=claim_text,
            conversation_id=conversation_id,
            workspace_root=workspace_root,
            promotion_reason=promotion_reason,
        )
        return {
            "category": classification.category,
            "retention_class": classification.retention_class,
            "scope_kind": classification.scope_kind,
            "scope_ref": classification.scope_ref,
            "subject_key": classification.subject_key,
            "topic_key": classification.topic_key,
            "explanation": list(classification.explanation or []),
            "structured_assertion": dict(classification.structured_assertion or {}),
            "expires_at": classification.expires_at,
        }

    def subject_key_for_memory(self, record: MemoryRecord) -> str:
        assertion = dict(record.structured_assertion or {})
        subject_key = str(assertion.get("subject_key") or "").strip()
        if subject_key:
            return subject_key
        return self._subject_key(record.claim_text)

    def topic_key_for_memory(self, record: MemoryRecord) -> str:
        assertion = dict(record.structured_assertion or {})
        topic_key = str(assertion.get("topic_key") or "").strip()
        if topic_key:
            return topic_key
        return self._topic_key(record.claim_text)

    def _scope_ref_for(
        self,
        *,
        scope_kind: str,
        conversation_id: str | None,
        workspace_root: str,
    ) -> str:
        if scope_kind == "global":
            return "global"
        if scope_kind == "workspace":
            return str(Path(workspace_root).resolve()) if workspace_root else "workspace:default"
        if scope_kind == "entity":
            if not conversation_id:
                return "entity:unknown"
            return conversation_id
        if scope_kind == "conversation":
            if not conversation_id:
                return "conversation:ephemeral"
            return conversation_id
        raise ValueError(f"unrecognized scope_kind: {scope_kind!r}")

    def _classification_explanation(
        self,
        *,
        original_category: str,
        resolved_category: str,
        signals: ClaimSignals,
        retention_class: str,
        scope_kind: str,
    ) -> list[str]:
        reasons = [f"category:{original_category}->{resolved_category}"]
        if signals.matched_signals:
            for name, hits in sorted(signals.matched_signals.items()):
                reasons.append(f"signal:{name}={','.join(hits[:3])}")
        if signals.subject_key:
            reasons.append(f"subject:{signals.subject_key}")
        if signals.topic_key:
            reasons.append(f"topic:{signals.topic_key}")
        reasons.append(f"retention:{retention_class}")
        reasons.append(f"scope:{scope_kind}")
        return reasons

    def _subject_key(self, text: str) -> str:
        for pattern, subject in _subject_hint_patterns():
            if re.search(pattern, text, re.IGNORECASE):
                return subject
        path_match = re.search(r"/[\w./-]+", text)
        if path_match:
            return f"path:{path_match.group(0).lower()}"
        tokens = self._normalized_tokens(text)
        return tokens[0] if tokens else ""

    def _topic_key(self, text: str) -> str:
        tokens = self._normalized_tokens(text)
        return "-".join(tokens[:3])

    def _normalized_tokens(self, text: str) -> list[str]:
        raw_tokens = re.findall(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]{2,}", text.lower())
        stop = _claim_stop_tokens()
        tokens = [token for token in raw_tokens if token not in stop]
        return tokens[:8]

    def _subject_matches(self, left_subject: str, right_subject: str) -> bool:
        if left_subject and right_subject:
            return left_subject == right_subject
        return True

    def _task_state_conflicts(
        self,
        *,
        left_claim: str,
        right_claim: str,
        left_subject: str,
        right_subject: str,
    ) -> bool:
        if not self._subject_matches(left_subject, right_subject):
            return False
        if left_subject and right_subject and left_subject == right_subject:
            return True
        return shares_topic(left_claim, right_claim)


__all__ = [
    "MemoryCategoryPolicy",
    "MemoryClassification",
    "MemoryGovernanceService",
]
