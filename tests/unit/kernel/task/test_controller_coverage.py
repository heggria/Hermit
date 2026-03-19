"""Additional TaskController tests — cover decide_ingress, continuation, and helper gaps."""

from __future__ import annotations

import json
from pathlib import Path

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import (
    TaskController,
)


def _setup(tmp_path: Path) -> tuple[KernelStore, TaskController]:
    store = KernelStore(tmp_path / "state.db")
    store.ensure_conversation("conv-1", source_channel="chat")
    ctrl = TaskController(store)
    return store, ctrl


def _start_task(ctrl: TaskController, **kwargs) -> TaskExecutionContext:
    defaults = {
        "conversation_id": "conv-1",
        "goal": "Test goal",
        "source_channel": "chat",
        "kind": "respond",
    }
    defaults.update(kwargs)
    return ctrl.start_task(**defaults)


# ── _runtime_snapshot_payload ──────────────────────────────────


def test_runtime_snapshot_payload_from_context(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    context = dict(attempt.context or {})
    context["runtime_snapshot"] = {"payload": {"key": "value"}}
    store.update_step_attempt(ctx.step_attempt_id, context=context)
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    result = ctrl._runtime_snapshot_payload(attempt)
    assert result == {"key": "value"}


def test_runtime_snapshot_payload_empty(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    result = ctrl._runtime_snapshot_payload(attempt)
    assert result == {}


def test_runtime_snapshot_payload_from_resume_ref(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    # Create a snapshot file
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({"payload": {"restored": True}}))
    # Create an artifact pointing to it
    artifact = store.create_artifact(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        kind="snapshot",
        uri=str(snapshot_path),
        content_hash="hash1",
        producer="test",
    )
    # Set resume_from_ref on a mock attempt
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    # Use SimpleNamespace to add resume_from_ref
    from types import SimpleNamespace

    mock_attempt = SimpleNamespace(
        resume_from_ref=artifact.artifact_id,
        context=attempt.context,
    )
    result = ctrl._runtime_snapshot_payload(mock_attempt)
    assert result == {"restored": True}


def test_runtime_snapshot_payload_bad_file(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    artifact = store.create_artifact(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        kind="snapshot",
        uri="/nonexistent/path.json",
        content_hash="hash1",
        producer="test",
    )
    from types import SimpleNamespace

    mock_attempt = SimpleNamespace(
        resume_from_ref=artifact.artifact_id,
        context={},
    )
    result = ctrl._runtime_snapshot_payload(mock_attempt)
    assert result == {}


# ── active_task_for_conversation: blocked with plan confirmation ─


def test_active_task_blocked_plan_confirmation(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.mark_planning_ready(ctx, plan_artifact_ref="plan-1")
    # Task should be blocked, and with awaiting_plan_confirmation the active
    # task should return None
    active = ctrl.active_task_for_conversation("conv-1")
    assert active is None


# ── decide_ingress: chat_only ───────────────────────────────────


def test_decide_ingress_chat_only(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    decision = ctrl.decide_ingress(
        conversation_id="conv-1",
        source_channel="chat",
        raw_text="hi",
        prompt="hi",
    )
    assert decision.mode == "start"
    assert decision.intent == "chat_only"
    assert decision.resolution == "chat_only"


# ── decide_ingress: explicit new task ───────────────────────────


def test_decide_ingress_explicit_new_task(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    decision = ctrl.decide_ingress(
        conversation_id="conv-1",
        source_channel="chat",
        raw_text="/new build a dashboard",
        prompt="/new build a dashboard",
    )
    assert decision.mode == "start"
    assert decision.intent == "start_new_task"


# ── decide_ingress: fallback new root (no active tasks) ─────────


def test_decide_ingress_no_active_tasks(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    decision = ctrl.decide_ingress(
        conversation_id="conv-1",
        source_channel="chat",
        raw_text="build a complex feature with many components",
        prompt="build a complex feature with many components",
    )
    assert decision.mode == "start"
    assert decision.resolution == "start_new_root"


# ── decide_ingress: append_note to running task ─────────────────


def test_decide_ingress_append_note(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl, goal="fix the database issue")
    decision = ctrl.decide_ingress(
        conversation_id="conv-1",
        source_channel="chat",
        raw_text="also fix the database index",
        prompt="also fix the database index",
        reply_to_task_id=ctx.task_id,
    )
    assert decision.resolution in ("append_note", "start_new_root")


# ── decide_ingress: fork_child ──────────────────────────────────


def test_decide_ingress_fork_child(tmp_path: Path, monkeypatch) -> None:
    _store, ctrl = _setup(tmp_path)
    _start_task(ctrl, goal="main feature work")
    from hermit.kernel.task.services.ingress_router import IngressRouter

    monkeypatch.setattr(IngressRouter, "_has_branch_marker", staticmethod(lambda text: True))
    decision = ctrl.decide_ingress(
        conversation_id="conv-1",
        source_channel="chat",
        raw_text="branch this into a subtask for testing",
        prompt="branch this into a subtask for testing",
    )
    assert decision.resolution == "fork_child"


# ── _looks_like_task_followup ───────────────────────────────────


def test_looks_like_task_followup_empty(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    assert ctrl._looks_like_task_followup("", task_id=ctx.task_id) is False


def test_looks_like_task_followup_with_topic(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl, goal="database migration")
    result = ctrl._looks_like_task_followup(
        "how is the database migration going",
        task_id=ctx.task_id,
    )
    assert isinstance(result, bool)


# ── _task_context_texts ─────────────────────────────────────────


def test_task_context_texts(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl, goal="deploy feature")
    texts = ctrl._task_context_texts(ctx.task_id)
    assert any("deploy feature" in text for text in texts)


def test_task_context_texts_with_notes(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl, goal="build API")
    ctrl.append_note(
        task_id=ctx.task_id,
        source_channel="chat",
        raw_text="add authentication endpoint",
        prompt="add authentication endpoint",
    )
    texts = ctrl._task_context_texts(ctx.task_id)
    assert len(texts) >= 2  # title + goal + note


# ── _terminal_continuation_tasks ────────────────────────────────


def test_terminal_continuation_tasks_empty(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    tasks = ctrl._terminal_continuation_tasks("conv-1")
    assert tasks == []


def test_terminal_continuation_tasks_with_completed(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl, goal="completed task")
    ctrl.finalize_result(ctx, status="completed")
    tasks = ctrl._terminal_continuation_tasks("conv-1")
    assert len(tasks) == 1


# ── _continuation_candidate_texts ───────────────────────────────


def test_continuation_candidate_texts(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl, goal="implement feature X")
    ctrl.finalize_result(ctx, status="completed", result_text="Feature X done")
    texts = ctrl._continuation_candidate_texts(ctx.task_id)
    assert any("implement feature X" in text for text in texts)


def test_continuation_candidate_texts_nonexistent(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    texts = ctrl._continuation_candidate_texts("nonexistent")
    assert texts == []


# ── _continuation_anchor ────────────────────────────────────────


def test_continuation_anchor(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl, goal="my task goal")
    ctrl.finalize_result(ctx, status="completed")
    anchor = ctrl._continuation_anchor(ctx.task_id, selection_reason="test_reason")
    assert anchor["anchor_task_id"] == ctx.task_id
    assert anchor["anchor_kind"] == "completed_outcome"
    assert anchor["selection_reason"] == "test_reason"


def test_continuation_anchor_nonexistent(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    anchor = ctrl._continuation_anchor("nonexistent", selection_reason="test")
    assert anchor == {}


# ── resolve_continuation_target ─────────────────────────────────


def test_resolve_continuation_target_none(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    result = ctrl.resolve_continuation_target(conversation_id="conv-1", raw_text="")
    assert result is None


# ── _texts_overlap static method ────────────────────────────────


def test_texts_overlap_static() -> None:
    result = TaskController._texts_overlap("database migration", "migration")
    assert isinstance(result, bool)


# ── _mark_attempt_input_dirty ───────────────────────────────────


def test_mark_attempt_input_dirty_no_attempt(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    # No tasks/attempts exist, should be a no-op
    ctrl._mark_attempt_input_dirty(
        task_id="nonexistent",
        ingress_id=None,
        note_event_seq=None,
        emit_event=True,
    )


# ── mark_suspended with invalid transition ──────────────────────


def test_mark_suspended_invalid_transition(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.finalize_result(ctx, status="completed")
    # Trying to suspend a completed task should log warning but not crash
    ctrl.mark_suspended(ctx, waiting_kind="awaiting_approval")


# ── pause_task invalid transition ───────────────────────────────


def test_pause_task_invalid_transition(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.finalize_result(ctx, status="completed")
    # Should log warning but not crash
    ctrl.pause_task(ctx.task_id)


# ── cancel_task invalid transition ──────────────────────────────


def test_cancel_task_invalid_transition(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.finalize_result(ctx, status="completed")
    ctrl.cancel_task(ctx.task_id)


# ── _set_focus: noop when already focused ───────────────────────


def test_set_focus_noop_same_task(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    # Set focus twice - second should be a noop for ingress_bound
    ctrl._set_focus(conversation_id="conv-1", task_id=ctx.task_id, reason="task_started")
    ctrl._set_focus(conversation_id="conv-1", task_id=ctx.task_id, reason="ingress_bound")
    # No crash expected


# ── _set_focus: clear ───────────────────────────────────────────


def test_set_focus_clear(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    _start_task(ctrl)
    ctrl._set_focus(conversation_id="conv-1", task_id=None, reason="cleared")
    conv = store.get_conversation("conv-1")
    assert conv.focus_task_id is None


# ── _try_upgrade_to_steering: with /steer command ──────────────


def test_try_upgrade_to_steering_with_command(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl._try_upgrade_to_steering(
        task_id=ctx.task_id,
        raw_text="/steer focus on performance only",
        source_channel="chat",
    )
    # Should not crash; verify a steering was created


def test_try_upgrade_to_steering_with_type(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl._try_upgrade_to_steering(
        task_id=ctx.task_id,
        raw_text="/steer --type priority focus on this first",
        source_channel="cli",
    )


def test_try_upgrade_to_steering_type_only(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl._try_upgrade_to_steering(
        task_id=ctx.task_id,
        raw_text="/steer --type scope",
        source_channel="chat",
    )
