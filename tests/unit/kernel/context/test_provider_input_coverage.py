"""Tests for kernel/context/injection/provider_input.py — coverage for missed lines.

Covers: ProviderInputCompiler._carry_forward, _focus_summary, _bound_ingress_deltas,
_recent_notes, _active_steerings, _render_continuation_guidance,
_render_message, normalize_ingress with code blocks and long text.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hermit.kernel.context.injection.provider_input import (
    ProviderInputCompiler,
)
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore


def _make_task_context(
    store: KernelStore,
    *,
    conversation_id: str = "conv1",
) -> TaskExecutionContext:
    store.ensure_conversation(conversation_id, source_channel="test")
    task = store.create_task(
        conversation_id=conversation_id,
        title="Test task",
        goal="Test goal",
        source_channel="test",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    return TaskExecutionContext(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        conversation_id=conversation_id,
        source_channel="test",
        policy_profile="autonomous",
        ingress_metadata={},
    )


# ---------------------------------------------------------------------------
# _carry_forward
# ---------------------------------------------------------------------------


class TestCarryForward:
    def test_ingress_anchor_preferred(self) -> None:
        ctx = SimpleNamespace(
            ingress_metadata={"continuation_anchor": {"task_id": "t1", "goal": "g1"}}
        )
        result = ProviderInputCompiler._carry_forward(ctx, {})
        assert result["task_id"] == "t1"

    def test_projection_anchor_fallback(self) -> None:
        ctx = SimpleNamespace(ingress_metadata={})
        projection = {
            "projection": {"task": {"continuation_anchor": {"task_id": "t2", "goal": "g2"}}}
        }
        result = ProviderInputCompiler._carry_forward(ctx, projection)
        assert result["task_id"] == "t2"

    def test_no_anchor_returns_none(self) -> None:
        ctx = SimpleNamespace(ingress_metadata={})
        result = ProviderInputCompiler._carry_forward(ctx, {})
        assert result is None


# ---------------------------------------------------------------------------
# _render_continuation_guidance
# ---------------------------------------------------------------------------


class TestRenderContinuationGuidance:
    def test_no_guidance_returns_empty(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        compiler = ProviderInputCompiler(store)
        assert compiler._render_continuation_guidance({}) == ""
        assert compiler._render_continuation_guidance({"has_anchor": False}) == ""

    def test_explicit_topic_shift(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        compiler = ProviderInputCompiler(store)
        guidance = {
            "has_anchor": True,
            "mode": "explicit_topic_shift",
            "anchor_task_id": "t1",
            "anchor_user_request": "original request",
            "anchor_goal": "original goal",
            "outcome_summary": "completed",
        }
        result = compiler._render_continuation_guidance(guidance)
        assert "explicit" in result.lower() or "new topic" in result.lower()
        assert "t1" in result

    def test_strong_topic_shift(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        compiler = ProviderInputCompiler(store)
        guidance = {"has_anchor": True, "mode": "strong_topic_shift", "anchor_task_id": "t2"}
        result = compiler._render_continuation_guidance(guidance)
        assert "strong" in result.lower() or "new semantics" in result.lower()

    def test_anchor_correction(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        compiler = ProviderInputCompiler(store)
        guidance = {"has_anchor": True, "mode": "anchor_correction", "anchor_task_id": "t3"}
        result = compiler._render_continuation_guidance(guidance)
        assert "correction" in result.lower() or "clarification" in result.lower()

    def test_default_mode(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        compiler = ProviderInputCompiler(store)
        guidance = {"has_anchor": True, "mode": "plain_new_task", "anchor_task_id": "t4"}
        result = compiler._render_continuation_guidance(guidance)
        assert "background context" in result.lower() or "normal new task" in result.lower()


# ---------------------------------------------------------------------------
# _active_steerings
# ---------------------------------------------------------------------------


class TestActiveSteerings:
    def test_no_method_returns_empty(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        compiler = ProviderInputCompiler(store)
        result = compiler._active_steerings("t1")
        assert result == []


# ---------------------------------------------------------------------------
# _recent_notes
# ---------------------------------------------------------------------------


class TestRecentNotes:
    def test_empty_events(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        compiler = ProviderInputCompiler(store)
        store.ensure_conversation("c1", source_channel="test")
        task = store.create_task(conversation_id="c1", title="t", goal="g", source_channel="test")
        result = compiler._recent_notes(task.task_id)
        assert result == []

    def test_collects_note_events(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        compiler = ProviderInputCompiler(store)
        store.ensure_conversation("c1", source_channel="test")
        task = store.create_task(conversation_id="c1", title="t", goal="g", source_channel="test")
        step = store.create_step(task_id=task.task_id, kind="respond")
        store.append_event(
            event_type="task.note.appended",
            entity_type="task",
            entity_id=task.task_id,
            task_id=task.task_id,
            step_id=step.step_id,
            actor="user",
            payload={"inline_excerpt": "remember this", "raw_text": "remember this"},
        )
        result = compiler._recent_notes(task.task_id)
        assert len(result) == 1
        assert "remember" in result[0]["inline_excerpt"]


# ---------------------------------------------------------------------------
# normalize_ingress with code blocks
# ---------------------------------------------------------------------------


class TestNormalizeIngress:
    def test_plain_text(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        from hermit.kernel.artifacts.models.artifacts import ArtifactStore

        artifact_store = ArtifactStore(tmp_path / "artifacts")
        compiler = ProviderInputCompiler(store, artifact_store=artifact_store)
        ctx = _make_task_context(store)
        result = compiler.normalize_ingress(
            task_context=ctx, raw_text="hello world", final_prompt="hello world"
        )
        assert result["ingress_artifact_refs"] == []
        assert result["inline_excerpt"] == "hello world"

    def test_with_code_block(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        from hermit.kernel.artifacts.models.artifacts import ArtifactStore

        artifact_store = ArtifactStore(tmp_path / "artifacts")
        compiler = ProviderInputCompiler(store, artifact_store=artifact_store)
        ctx = _make_task_context(store)
        raw = "Please review:\n```python\ndef hello():\n    print('hi')\n```\n"
        result = compiler.normalize_ingress(task_context=ctx, raw_text=raw, final_prompt=raw)
        assert len(result["ingress_artifact_refs"]) >= 1
        assert "code_block" in result["detected_payload_kinds"]

    def test_long_text_creates_artifact(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        from hermit.kernel.artifacts.models.artifacts import ArtifactStore

        artifact_store = ArtifactStore(tmp_path / "artifacts")
        compiler = ProviderInputCompiler(store, artifact_store=artifact_store)
        ctx = _make_task_context(store)
        raw = "word " * 2000  # Very long text
        result = compiler.normalize_ingress(task_context=ctx, raw_text=raw, final_prompt=raw)
        assert "long_text" in result["detected_payload_kinds"]


# ---------------------------------------------------------------------------
# _focus_summary
# ---------------------------------------------------------------------------


class TestFocusSummary:
    def test_no_focus_task_id(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        compiler = ProviderInputCompiler(store)
        ctx = _make_task_context(store)
        result = compiler._focus_summary(ctx, {})
        assert result is None

    def test_with_focus_in_open_tasks(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        compiler = ProviderInputCompiler(store)
        ctx = _make_task_context(store)
        projection = {
            "focus_task_id": "t-focus",
            "focus_reason": "user requested",
            "open_tasks": [
                {"task_id": "t-focus", "title": "Focus task", "status": "running"},
            ],
        }
        result = compiler._focus_summary(ctx, projection)
        assert result is not None
        assert result["task_id"] == "t-focus"
        assert result["title"] == "Focus task"

    def test_focus_task_not_in_open_tasks_but_exists(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        store.ensure_conversation("cf", source_channel="test")
        task = store.create_task(
            conversation_id="cf", title="Focused", goal="g", source_channel="test"
        )
        compiler = ProviderInputCompiler(store)
        ctx = _make_task_context(store)
        projection = {"focus_task_id": task.task_id, "open_tasks": []}
        result = compiler._focus_summary(ctx, projection)
        assert result is not None
        assert result["title"] == "Focused"

    def test_focus_task_not_found(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "state.db")
        compiler = ProviderInputCompiler(store)
        ctx = _make_task_context(store)
        projection = {"focus_task_id": "nonexistent", "open_tasks": []}
        result = compiler._focus_summary(ctx, projection)
        assert result is None
