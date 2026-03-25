"""Tests for kernel/context/compiler/compiler.py — ContextCompiler."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.kernel.context.compiler.compiler import ContextCompiler, ContextPack
from hermit.kernel.context.models.context import TaskExecutionContext, WorkingStateSnapshot
from hermit.kernel.task.models.records import BeliefRecord, MemoryRecord

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


def _ws() -> WorkingStateSnapshot:
    return WorkingStateSnapshot(goal_summary="Test goal")


def _memory(
    *,
    memory_id: str = "mem-1",
    category: str = "user_preference",
    claim_text: str = "User prefers dark mode",
    scope_kind: str = "global",
    scope_ref: str = "global",
    retention_class: str = "user_preference",
    status: str = "active",
    trust_tier: str = "durable",
    confidence: float = 0.9,
    expires_at: float | None = None,
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
        confidence=confidence,
        expires_at=expires_at,
        promotion_reason=promotion_reason,
    )


def _belief(
    *,
    belief_id: str = "b-1",
    category: str = "user_preference",
    claim_text: str = "User likes dark mode",
    scope_kind: str = "global",
    scope_ref: str = "global",
) -> BeliefRecord:
    return BeliefRecord(
        belief_id=belief_id,
        task_id="task-1",
        conversation_id="conv-1",
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        category=category,
        claim_text=claim_text,
    )


# ---------------------------------------------------------------------------
# ContextPack
# ---------------------------------------------------------------------------


class TestContextPack:
    def test_to_payload_returns_dict(self) -> None:
        pack = ContextPack(
            static_memory=[],
            retrieval_memory=[],
            selected_beliefs=[],
            working_state={},
            selection_reasons={},
            excluded_memory_ids=[],
            excluded_reasons={},
            pack_hash="abc123",
        )
        payload = pack.to_payload()
        assert payload["kind"] == "context.pack/v3"
        assert payload["pack_hash"] == "abc123"
        assert isinstance(payload["static_memory"], list)

    def test_to_payload_includes_all_fields(self) -> None:
        pack = ContextPack(
            static_memory=[{"test": 1}],
            retrieval_memory=[{"test": 2}],
            selected_beliefs=[{"test": 3}],
            working_state={"goal": "test"},
            selection_reasons={"m-1": "static_policy"},
            excluded_memory_ids=["m-2"],
            excluded_reasons={"m-2": "expired"},
            pack_hash="hash",
            task_summary={"task_id": "t-1"},
            step_summary={"step_id": "s-1"},
            policy_summary={"profile": "default"},
            carry_forward={"anchor": True},
            blackboard_entries=[{"key": "val"}],
        )
        payload = pack.to_payload()
        assert payload["task_summary"] == {"task_id": "t-1"}
        assert payload["carry_forward"] == {"anchor": True}
        assert len(payload["blackboard_entries"]) == 1


# ---------------------------------------------------------------------------
# ContextCompiler.compile
# ---------------------------------------------------------------------------


class TestCompile:
    def test_compile_empty_inputs(self) -> None:
        compiler = ContextCompiler()
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[],
            memories=[],
            query="test query",
        )
        assert pack.pack_hash
        assert pack.static_memory == []
        assert pack.retrieval_memory == []
        assert pack.selected_beliefs == []

    def test_compile_static_memory(self) -> None:
        compiler = ContextCompiler()
        mem = _memory(retention_class="user_preference", scope_kind="global")
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[],
            memories=[mem],
            query="test query",
        )
        assert len(pack.static_memory) == 1
        assert pack.selection_reasons.get("mem-1") == "static_policy"

    def test_compile_excludes_inactive_memory(self) -> None:
        compiler = ContextCompiler()
        mem = _memory(status="invalidated")
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[],
            memories=[mem],
            query="test query",
        )
        assert len(pack.static_memory) == 0
        assert "mem-1" in pack.excluded_reasons
        assert "status" in pack.excluded_reasons["mem-1"]

    def test_compile_excludes_quarantined_memory(self) -> None:
        compiler = ContextCompiler()
        mem = _memory(status="quarantined")
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[],
            memories=[mem],
            query="test query",
        )
        assert pack.excluded_reasons.get("mem-1") == "quarantined"

    def test_compile_excludes_expired_memory(self) -> None:
        compiler = ContextCompiler()
        mem = _memory(expires_at=_NOW - 1000)
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[],
            memories=[mem],
            query="test query",
        )
        assert pack.excluded_reasons.get("mem-1") == "expired"

    def test_compile_retrieval_ranked_by_score(self) -> None:
        compiler = ContextCompiler()
        memories = [
            _memory(
                memory_id=f"mem-{i}",
                category="other",
                claim_text=f"Fact {i} about dark mode",
                scope_kind="conversation",
                scope_ref="conv-1",
                retention_class="volatile_fact",
                trust_tier="observed",
            )
            for i in range(8)
        ]
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[],
            memories=memories,
            query="dark mode",
        )
        # At most 5 retrieval memories
        assert len(pack.retrieval_memory) <= 5
        # Remaining are excluded
        assert any("rank_cutoff" in r for r in pack.excluded_reasons.values())

    def test_compile_includes_beliefs_in_scope(self) -> None:
        compiler = ContextCompiler()
        b = _belief(scope_kind="global", scope_ref="global")
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[b],
            memories=[],
            query="test",
        )
        assert len(pack.selected_beliefs) == 1
        assert pack.selected_beliefs[0]["belief_id"] == "b-1"

    def test_compile_excludes_out_of_scope_beliefs(self) -> None:
        compiler = ContextCompiler()
        b = _belief(scope_kind="conversation", scope_ref="other-conv")
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[b],
            memories=[],
            query="test",
        )
        assert len(pack.selected_beliefs) == 0

    def test_compile_limits_beliefs_to_10(self) -> None:
        compiler = ContextCompiler()
        beliefs = [
            _belief(belief_id=f"b-{i}", scope_kind="global", scope_ref="global") for i in range(15)
        ]
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=beliefs,
            memories=[],
            query="test",
        )
        assert len(pack.selected_beliefs) <= 10

    def test_compile_with_optional_params(self) -> None:
        compiler = ContextCompiler()
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[],
            memories=[],
            query="test",
            task_summary={"task_id": "t-1"},
            step_summary={"step_id": "s-1"},
            policy_summary={"profile": "default"},
            planning_state={"planning_mode": True},
            carry_forward={"anchor": True},
            recent_notes=[{"text": "note"}],
            blackboard_entries=[{"key": "val"}],
        )
        assert pack.task_summary == {"task_id": "t-1"}
        assert pack.carry_forward == {"anchor": True}
        assert len(pack.recent_notes) == 1

    def test_compile_stores_artifact_when_store_available(self) -> None:
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("uri://pack", "hash123")
        compiler = ContextCompiler(artifact_store=artifact_store)
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[],
            memories=[],
            query="test",
        )
        assert pack.artifact_uri == "uri://pack"
        assert pack.artifact_hash == "hash123"

    def test_compile_smalltalk_suppresses_retrieval(self) -> None:
        compiler = ContextCompiler()
        mem = _memory(
            category="other",
            claim_text="Some contextual fact",
            scope_kind="conversation",
            scope_ref="conv-1",
            retention_class="volatile_fact",
        )
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[],
            memories=[mem],
            query="hi",
        )
        # smalltalk query should suppress contextual retrieval
        assert len(pack.retrieval_memory) == 0

    def test_compile_with_hybrid_retrieval(self) -> None:
        retrieval_svc = MagicMock()
        mock_result = SimpleNamespace(
            memory_id="mem-1",
            memory=_memory(),
            sources=["semantic"],
        )
        report = SimpleNamespace(results=[mock_result])
        retrieval_svc.retrieve.return_value = report
        store_mock = MagicMock()

        compiler = ContextCompiler(
            retrieval_service=retrieval_svc,
            store=store_mock,
        )
        mem = _memory(
            category="other",
            claim_text="Some relevant fact about dark mode",
            scope_kind="conversation",
            scope_ref="conv-1",
            retention_class="volatile_fact",
        )
        pack = compiler.compile(
            context=_ctx(),
            working_state=_ws(),
            beliefs=[],
            memories=[mem],
            query="dark mode settings",
        )
        assert len(pack.retrieval_memory) >= 1


# ---------------------------------------------------------------------------
# render_static_prompt / render_retrieval_prompt
# ---------------------------------------------------------------------------


class TestRenderPrompts:
    def test_render_static_prompt_empty(self) -> None:
        compiler = ContextCompiler()
        pack = ContextPack(
            static_memory=[],
            retrieval_memory=[],
            selected_beliefs=[],
            working_state={},
            selection_reasons={},
            excluded_memory_ids=[],
            excluded_reasons={},
            pack_hash="h",
        )
        result = compiler.render_static_prompt(pack)
        assert result == ""

    def test_render_static_prompt_with_entries(self) -> None:
        compiler = ContextCompiler()
        pack = ContextPack(
            static_memory=[
                {
                    "category": "user_preference",
                    "claim_text": "Dark mode",
                    "confidence": 0.9,
                    "supersedes": [],
                }
            ],
            retrieval_memory=[],
            selected_beliefs=[],
            working_state={},
            selection_reasons={},
            excluded_memory_ids=[],
            excluded_reasons={},
            pack_hash="h",
        )
        result = compiler.render_static_prompt(pack)
        assert "Dark mode" in result

    def test_render_retrieval_prompt_empty(self) -> None:
        compiler = ContextCompiler()
        pack = ContextPack(
            static_memory=[],
            retrieval_memory=[],
            selected_beliefs=[],
            working_state={},
            selection_reasons={},
            excluded_memory_ids=[],
            excluded_reasons={},
            pack_hash="h",
        )
        result = compiler.render_retrieval_prompt(pack)
        assert result == ""


# ---------------------------------------------------------------------------
# Helper methods
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_normalize_query(self) -> None:
        assert ContextCompiler._normalize_query("  hello   world  ") == "hello world"
        assert ContextCompiler._normalize_query("") == ""
        assert ContextCompiler._normalize_query(None) == ""

    def test_is_followup_query(self) -> None:
        # This method checks for followup markers; varies by locale
        result = ContextCompiler._is_followup_query("")
        assert isinstance(result, bool)

    def test_memory_relevant_to_query_empty_query(self) -> None:
        mem = _memory(scope_kind="conversation", retention_class="volatile_fact")
        assert ContextCompiler._memory_relevant_to_query(mem, query="") is False

    def test_memory_relevant_non_conversation_always_true(self) -> None:
        mem = _memory(scope_kind="global")
        assert ContextCompiler._memory_relevant_to_query(mem, query="anything") is True

    def test_memory_relevant_non_volatile_always_true(self) -> None:
        mem = _memory(
            scope_kind="conversation",
            retention_class="user_preference",
        )
        assert ContextCompiler._memory_relevant_to_query(mem, query="anything") is True

    def test_memory_relevant_by_token_overlap(self) -> None:
        mem = _memory(
            scope_kind="conversation",
            retention_class="volatile_fact",
            claim_text="Python testing framework",
        )
        assert ContextCompiler._memory_relevant_to_query(mem, query="python testing") is True

    def test_retrieval_score_basic(self) -> None:
        compiler = ContextCompiler()
        mem = _memory(
            scope_kind="global",
            trust_tier="durable",
        )
        ctx = _ctx()
        score = compiler._retrieval_score(mem, context=ctx, query="dark mode")
        assert score > 0.0

    def test_categories_from_payload(self) -> None:
        items = [
            {"category": "user_preference", "claim_text": "test", "confidence": 0.9},
            {"category": "user_preference", "claim_text": "test2", "confidence": 0.8},
        ]
        result = ContextCompiler._categories_from_payload(items)
        assert "user_preference" in result
        assert len(result["user_preference"]) == 2
