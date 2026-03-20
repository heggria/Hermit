"""Tests for kernel/context/memory/governance.py — MemoryGovernanceService."""

from __future__ import annotations

import time
from pathlib import Path

from hermit.kernel.context.memory.governance import (
    ClaimSignals,
    MemoryClassification,
    MemoryGovernanceService,
)
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.task.models.records import BeliefRecord, MemoryRecord
from hermit.plugins.builtin.hooks.memory.types import MemoryEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = time.time()


def _ctx(
    *,
    conversation_id: str = "conv-1",
    task_id: str = "task-1",
    workspace_root: str = "",
) -> TaskExecutionContext:
    return TaskExecutionContext(
        conversation_id=conversation_id,
        task_id=task_id,
        step_id="step-1",
        step_attempt_id="attempt-1",
        source_channel="cli",
        workspace_root=workspace_root,
    )


def _belief(
    *,
    category: str = "user_preference",
    claim_text: str = "User prefers dark mode",
    conversation_id: str | None = "conv-1",
) -> BeliefRecord:
    return BeliefRecord(
        belief_id="b-1",
        task_id="task-1",
        conversation_id=conversation_id,
        scope_kind="global",
        scope_ref="global",
        category=category,
        claim_text=claim_text,
    )


def _memory(
    *,
    memory_id: str = "mem-1",
    category: str = "user_preference",
    claim_text: str = "User prefers dark mode",
    scope_kind: str = "global",
    scope_ref: str = "global",
    retention_class: str = "user_preference",
    status: str = "active",
    expires_at: float | None = None,
    structured_assertion: dict | None = None,
    trust_tier: str = "durable",
    conversation_id: str | None = "conv-1",
    promotion_reason: str = "belief_promotion",
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        task_id="task-1",
        conversation_id=conversation_id,
        category=category,
        claim_text=claim_text,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        retention_class=retention_class,
        status=status,
        trust_tier=trust_tier,
        expires_at=expires_at,
        structured_assertion=structured_assertion or {},
        promotion_reason=promotion_reason,
    )


# ---------------------------------------------------------------------------
# policy_for
# ---------------------------------------------------------------------------


class TestPolicyFor:
    def test_known_category(self) -> None:
        svc = MemoryGovernanceService()
        policy = svc.policy_for("user_preference")
        assert policy.retention_class == "user_preference"
        assert policy.scope_kind == "global"
        assert policy.static_injection is True

    def test_unknown_category_returns_default(self) -> None:
        svc = MemoryGovernanceService()
        policy = svc.policy_for("nonexistent_category")
        assert policy.retention_class == "volatile_fact"
        assert policy.scope_kind == "conversation"

    def test_project_convention(self) -> None:
        svc = MemoryGovernanceService()
        policy = svc.policy_for("project_convention")
        assert policy.scope_kind == "workspace"
        assert policy.static_injection is True

    def test_active_task_deprecated_falls_back_to_default(self) -> None:
        svc = MemoryGovernanceService()
        policy = svc.policy_for("active_task")
        # active_task is deprecated; normalize_category maps it to "other"
        assert policy.retention_class == "volatile_fact"

    def test_pitfall_warning(self) -> None:
        svc = MemoryGovernanceService()
        policy = svc.policy_for("pitfall_warning")
        assert policy.retention_class == "pitfall_warning"
        assert policy.static_injection is True


# ---------------------------------------------------------------------------
# classify_belief / classify_claim
# ---------------------------------------------------------------------------


class TestClassification:
    def test_classify_belief_user_preference(self) -> None:
        svc = MemoryGovernanceService()
        result = svc.classify_belief(
            _belief(category="user_preference", claim_text="I prefer dark mode"),
            workspace_root="",
        )
        assert result.category == "user_preference"
        assert result.scope_kind == "global"
        assert result.static_injection is True

    def test_classify_claim_project_convention(self) -> None:
        svc = MemoryGovernanceService()
        result = svc.classify_claim(
            category="project_convention",
            claim_text="Use ruff for formatting",
            conversation_id="conv-1",
            workspace_root="/project",
        )
        assert result.category == "project_convention"
        assert result.scope_kind == "workspace"

    def test_classify_claim_sensitive_overrides_retention(self) -> None:
        svc = MemoryGovernanceService()
        result = svc.classify_claim(
            category="other",
            claim_text="medical history records and phone number details",
            conversation_id="conv-1",
        )
        assert result.retention_class == "sensitive_fact"

    def test_classify_claim_with_ttl(self) -> None:
        svc = MemoryGovernanceService()
        result = svc.classify_claim(
            category="other",
            claim_text="Some transient fact",
            conversation_id="conv-1",
        )
        assert result.expires_at is not None
        assert result.expires_at > _NOW

    def test_classify_claim_returns_explanation(self) -> None:
        svc = MemoryGovernanceService()
        result = svc.classify_claim(
            category="user_preference",
            claim_text="I prefer tabs",
            conversation_id="conv-1",
        )
        assert result.explanation is not None
        assert len(result.explanation) > 0

    def test_classify_claim_structured_assertion(self) -> None:
        svc = MemoryGovernanceService()
        result = svc.classify_claim(
            category="user_preference",
            claim_text="test claim",
            conversation_id="conv-1",
        )
        assert result.structured_assertion is not None
        assert "resolved_category" in result.structured_assertion


# ---------------------------------------------------------------------------
# analyze_claim
# ---------------------------------------------------------------------------


class TestAnalyzeClaim:
    def test_analyze_empty_text(self) -> None:
        svc = MemoryGovernanceService()
        signals = svc.analyze_claim(category="other", claim_text="")
        assert isinstance(signals, ClaimSignals)

    def test_analyze_user_preference_category(self) -> None:
        svc = MemoryGovernanceService()
        signals = svc.analyze_claim(category="user_preference", claim_text="generic text")
        assert signals.stable_preference is True

    def test_analyze_active_task_category_deprecated(self) -> None:
        """active_task is deprecated and mapped to 'other'; task_state signal
        now depends only on keyword matching, not category name."""
        svc = MemoryGovernanceService()
        signals = svc.analyze_claim(category="active_task", claim_text="generic text")
        # No task_state keywords in "generic text" → task_state is False
        assert signals.task_state is False

    def test_analyze_matched_signals_populated(self) -> None:
        svc = MemoryGovernanceService()
        signals = svc.analyze_claim(
            category="other",
            claim_text="medical history and phone number",
        )
        assert signals.sensitive is True
        assert signals.matched_signals is not None


# ---------------------------------------------------------------------------
# resolve_category
# ---------------------------------------------------------------------------


class TestResolveCategory:
    def test_stable_preference_wins(self) -> None:
        svc = MemoryGovernanceService()
        signals = ClaimSignals(stable_preference=True, project_convention=True)
        assert svc.resolve_category(category="other", signals=signals) == "user_preference"

    def test_task_state_without_convention_deprecated(self) -> None:
        """task_state signal no longer resolves to active_task (deprecated)."""
        svc = MemoryGovernanceService()
        signals = ClaimSignals(task_state=True)
        # active_task is deprecated; task_state signal alone falls through to original category
        assert svc.resolve_category(category="other", signals=signals) == "other"

    def test_tooling_without_convention(self) -> None:
        svc = MemoryGovernanceService()
        signals = ClaimSignals(tooling_environment=True)
        assert svc.resolve_category(category="other", signals=signals) == "tooling_environment"

    def test_project_convention_fallback(self) -> None:
        svc = MemoryGovernanceService()
        signals = ClaimSignals(project_convention=True)
        assert svc.resolve_category(category="other", signals=signals) == "project_convention"

    def test_no_signal_returns_category(self) -> None:
        svc = MemoryGovernanceService()
        signals = ClaimSignals()
        assert svc.resolve_category(category="tech_decision", signals=signals) == "tech_decision"


# ---------------------------------------------------------------------------
# filter_static_categories
# ---------------------------------------------------------------------------


class TestFilterStaticCategories:
    def test_filters_non_static(self) -> None:
        svc = MemoryGovernanceService()
        entry = MemoryEntry(category="other", content="transient")
        result = svc.filter_static_categories({"other": [entry]})
        assert result == {}

    def test_keeps_static_category(self) -> None:
        svc = MemoryGovernanceService()
        entry = MemoryEntry(category="user_preference", content="dark mode")
        result = svc.filter_static_categories({"user_preference": [entry]})
        assert "user_preference" in result

    def test_filters_empty_lists(self) -> None:
        svc = MemoryGovernanceService()
        result = svc.filter_static_categories({"user_preference": []})
        assert result == {}


# ---------------------------------------------------------------------------
# eligible_for_static / retrieval_reason
# ---------------------------------------------------------------------------


class TestEligibility:
    def test_eligible_for_static_user_preference(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(retention_class="user_preference", scope_kind="global")
        ctx = _ctx()
        assert svc.eligible_for_static(mem, context=ctx) is True

    def test_not_eligible_for_static_volatile(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(retention_class="volatile_fact")
        ctx = _ctx()
        assert svc.eligible_for_static(mem, context=ctx) is False

    def test_retrieval_reason_active_memory(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(
            retention_class="volatile_fact",
            scope_kind="conversation",
            scope_ref="conv-1",
        )
        ctx = _ctx(conversation_id="conv-1")
        reason = svc.retrieval_reason(mem, context=ctx)
        assert reason == "retrieval_policy"

    def test_retrieval_reason_invalidated(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(retention_class="invalidated")
        ctx = _ctx()
        assert svc.retrieval_reason(mem, context=ctx) is None

    def test_retrieval_reason_sensitive_scope_match(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(
            retention_class="sensitive_fact",
            scope_kind="global",
        )
        ctx = _ctx()
        assert svc.retrieval_reason(mem, context=ctx) == "scope_match"

    def test_retrieval_reason_out_of_scope(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(
            retention_class="volatile_fact",
            scope_kind="conversation",
            scope_ref="other-conv",
        )
        ctx = _ctx(conversation_id="conv-1")
        assert svc.retrieval_reason(mem, context=ctx) is None


# ---------------------------------------------------------------------------
# scope_matches
# ---------------------------------------------------------------------------


class TestScopeMatches:
    def test_global_always_matches(self) -> None:
        svc = MemoryGovernanceService()
        assert svc.scope_matches("global", "global", context=_ctx()) is True

    def test_conversation_match(self) -> None:
        svc = MemoryGovernanceService()
        assert svc.scope_matches("conversation", "conv-1", context=_ctx()) is True

    def test_conversation_mismatch(self) -> None:
        svc = MemoryGovernanceService()
        assert svc.scope_matches("conversation", "conv-999", context=_ctx()) is False

    def test_workspace_match(self) -> None:
        svc = MemoryGovernanceService()
        ctx = _ctx(workspace_root="/project")
        ref = str(Path("/project").resolve())
        assert svc.scope_matches("workspace", ref, context=ctx) is True

    def test_workspace_default(self) -> None:
        svc = MemoryGovernanceService()
        ctx = _ctx(workspace_root="")
        assert svc.scope_matches("workspace", "workspace:default", context=ctx) is True

    def test_entity_match(self) -> None:
        svc = MemoryGovernanceService()
        assert svc.scope_matches("entity", "task-1", context=_ctx()) is True

    def test_unknown_scope_returns_false(self) -> None:
        svc = MemoryGovernanceService()
        assert svc.scope_matches("unknown", "anything", context=_ctx()) is False


# ---------------------------------------------------------------------------
# is_expired
# ---------------------------------------------------------------------------


class TestIsExpired:
    def test_expired_memory(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(expires_at=_NOW - 100)
        assert svc.is_expired(mem) is True

    def test_not_expired_memory(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(expires_at=_NOW + 100)
        assert svc.is_expired(mem) is False

    def test_no_expiry(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(expires_at=None)
        assert svc.is_expired(mem) is False


# ---------------------------------------------------------------------------
# candidate_records_for_supersede / find_superseded_records
# ---------------------------------------------------------------------------


class TestSupersede:
    def test_candidate_records_same_scope(self) -> None:
        svc = MemoryGovernanceService()
        classification = MemoryClassification(
            category="user_preference",
            scope_kind="global",
            scope_ref="global",
            promotion_reason="test",
            retention_class="user_preference",
            static_injection=True,
            retrieval_allowed=True,
        )
        active = [
            _memory(memory_id="m-1", retention_class="user_preference"),
            _memory(memory_id="m-2", retention_class="volatile_fact"),
        ]
        candidates = svc.candidate_records_for_supersede(
            classification=classification, active_records=active
        )
        assert len(candidates) == 1
        assert candidates[0].memory_id == "m-1"

    def test_candidate_records_filters_inactive(self) -> None:
        svc = MemoryGovernanceService()
        classification = MemoryClassification(
            category="user_preference",
            scope_kind="global",
            scope_ref="global",
            promotion_reason="test",
            retention_class="user_preference",
            static_injection=True,
            retrieval_allowed=True,
        )
        inactive = _memory(memory_id="m-1", status="invalidated", retention_class="user_preference")
        candidates = svc.candidate_records_for_supersede(
            classification=classification, active_records=[inactive]
        )
        assert len(candidates) == 0

    def test_find_superseded_records_duplicate(self) -> None:
        svc = MemoryGovernanceService()
        classification = MemoryClassification(
            category="user_preference",
            scope_kind="global",
            scope_ref="global",
            promotion_reason="test",
            retention_class="user_preference",
            static_injection=True,
            retrieval_allowed=True,
        )
        existing = _memory(claim_text="User prefers dark mode")

        def entry_from(rec: MemoryRecord) -> MemoryEntry:
            return MemoryEntry(category=rec.category, content=rec.claim_text)

        dup, superseded = svc.find_superseded_records(
            classification=classification,
            claim_text="User prefers dark mode",
            active_records=[existing],
            entry_from_record=entry_from,
        )
        assert dup is not None
        assert dup.memory_id == "mem-1"
        assert len(superseded) == 0


# ---------------------------------------------------------------------------
# inspect_claim
# ---------------------------------------------------------------------------


class TestInspectClaim:
    def test_inspect_returns_dict(self) -> None:
        svc = MemoryGovernanceService()
        result = svc.inspect_claim(
            category="user_preference",
            claim_text="test",
            conversation_id="conv-1",
        )
        assert isinstance(result, dict)
        assert "category" in result
        assert "retention_class" in result
        assert "scope_kind" in result

    def test_inspect_with_workspace(self) -> None:
        svc = MemoryGovernanceService()
        result = svc.inspect_claim(
            category="project_convention",
            claim_text="Use ruff",
            conversation_id="conv-1",
            workspace_root="/project",
        )
        assert result["scope_kind"] == "workspace"


# ---------------------------------------------------------------------------
# subject / topic key helpers
# ---------------------------------------------------------------------------


class TestKeyHelpers:
    def test_subject_key_for_memory_from_assertion(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(structured_assertion={"subject_key": "custom_subject"})
        assert svc.subject_key_for_memory(mem) == "custom_subject"

    def test_subject_key_for_memory_fallback(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(structured_assertion={})
        key = svc.subject_key_for_memory(mem)
        assert isinstance(key, str)

    def test_topic_key_for_memory_from_assertion(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(structured_assertion={"topic_key": "custom_topic"})
        assert svc.topic_key_for_memory(mem) == "custom_topic"

    def test_topic_key_for_memory_fallback(self) -> None:
        svc = MemoryGovernanceService()
        mem = _memory(structured_assertion={})
        key = svc.topic_key_for_memory(mem)
        assert isinstance(key, str)

    def test_scope_ref_for_global(self) -> None:
        svc = MemoryGovernanceService()
        ref = svc._scope_ref_for(scope_kind="global", conversation_id="conv-1", workspace_root="")
        assert ref == "global"

    def test_scope_ref_for_entity(self) -> None:
        svc = MemoryGovernanceService()
        ref = svc._scope_ref_for(scope_kind="entity", conversation_id="conv-1", workspace_root="")
        assert ref == "conv-1"

    def test_scope_ref_for_entity_no_conversation(self) -> None:
        svc = MemoryGovernanceService()
        ref = svc._scope_ref_for(scope_kind="entity", conversation_id=None, workspace_root="")
        assert ref == "entity:unknown"

    def test_scope_ref_for_conversation_no_id(self) -> None:
        svc = MemoryGovernanceService()
        ref = svc._scope_ref_for(scope_kind="conversation", conversation_id=None, workspace_root="")
        assert ref == "conversation:unknown"


# ---------------------------------------------------------------------------
# task_state_conflicts / _subject_matches
# ---------------------------------------------------------------------------


class TestTaskStateConflicts:
    def test_same_subject_conflicts(self) -> None:
        svc = MemoryGovernanceService()
        assert (
            svc._task_state_conflicts(
                left_claim="Working on /src/main.py",
                right_claim="Completed /src/main.py",
                left_subject="path:/src/main.py",
                right_subject="path:/src/main.py",
            )
            is True
        )

    def test_different_subjects_no_conflict(self) -> None:
        svc = MemoryGovernanceService()
        assert (
            svc._task_state_conflicts(
                left_claim="Working on /src/a.py",
                right_claim="Working on /src/b.py",
                left_subject="path:/src/a.py",
                right_subject="path:/src/b.py",
            )
            is False
        )

    def test_empty_subjects_uses_topic_match(self) -> None:
        svc = MemoryGovernanceService()
        # With empty subjects, falls through to shares_topic
        result = svc._task_state_conflicts(
            left_claim="same topic content",
            right_claim="same topic content",
            left_subject="",
            right_subject="",
        )
        assert result is True

    def test_subject_matches_both_empty(self) -> None:
        svc = MemoryGovernanceService()
        assert svc._subject_matches("", "") is True

    def test_subject_matches_one_empty(self) -> None:
        svc = MemoryGovernanceService()
        assert svc._subject_matches("sub", "") is True
        assert svc._subject_matches("", "sub") is True

    def test_subject_matches_mismatch(self) -> None:
        svc = MemoryGovernanceService()
        assert svc._subject_matches("a", "b") is False
