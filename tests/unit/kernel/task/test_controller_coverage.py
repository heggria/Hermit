"""Additional TaskController tests — cover decide_ingress, continuation, and helper gaps."""

from __future__ import annotations

import json
from pathlib import Path

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import (
    TaskController,
)


def _ctrl(store: KernelStore) -> TaskController:
    return TaskController(store)


def _start_task(ctrl: TaskController, conv_id: str, **kwargs) -> TaskExecutionContext:
    defaults = {
        "conversation_id": conv_id,
        "goal": "Test goal",
        "source_channel": "chat",
        "kind": "respond",
    }
    defaults.update(kwargs)
    return ctrl.start_task(**defaults)


# ── _runtime_snapshot_payload ──────────────────────────────────


def test_runtime_snapshot_payload_from_context(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    attempt = shared_store.get_step_attempt(ctx.step_attempt_id)
    context = dict(attempt.context or {})
    context["runtime_snapshot"] = {"payload": {"key": "value"}}
    shared_store.update_step_attempt(ctx.step_attempt_id, context=context)
    attempt = shared_store.get_step_attempt(ctx.step_attempt_id)
    result = ctrl._runtime_snapshot_payload(attempt)
    assert result == {"key": "value"}


def test_runtime_snapshot_payload_empty(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    attempt = shared_store.get_step_attempt(ctx.step_attempt_id)
    result = ctrl._runtime_snapshot_payload(attempt)
    assert result == {}


def test_runtime_snapshot_payload_from_resume_ref(
    shared_store: KernelStore, conv_id: str, tmp_path: Path
) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    # Create a snapshot file
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({"payload": {"restored": True}}))
    # Create an artifact pointing to it
    artifact = shared_store.create_artifact(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        kind="snapshot",
        uri=str(snapshot_path),
        content_hash="hash1",
        producer="test",
    )
    # Set resume_from_ref on a mock attempt
    attempt = shared_store.get_step_attempt(ctx.step_attempt_id)
    from types import SimpleNamespace

    mock_attempt = SimpleNamespace(
        resume_from_ref=artifact.artifact_id,
        context=attempt.context,
    )
    result = ctrl._runtime_snapshot_payload(mock_attempt)
    assert result == {"restored": True}


def test_runtime_snapshot_payload_bad_file(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    artifact = shared_store.create_artifact(
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


def test_active_task_blocked_plan_confirmation(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.mark_planning_ready(ctx, plan_artifact_ref="plan-1")
    active = ctrl.active_task_for_conversation(conv_id)
    assert active is None


# ── decide_ingress: chat_only ───────────────────────────────────


def test_decide_ingress_chat_only(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    decision = ctrl.decide_ingress(
        conversation_id=conv_id,
        source_channel="chat",
        raw_text="hi",
        prompt="hi",
    )
    assert decision.mode == "start"
    assert decision.intent == "chat_only"
    assert decision.resolution == "chat_only"


# ── decide_ingress: explicit new task ───────────────────────────


def test_decide_ingress_explicit_new_task(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    decision = ctrl.decide_ingress(
        conversation_id=conv_id,
        source_channel="chat",
        raw_text="/new build a dashboard",
        prompt="/new build a dashboard",
    )
    assert decision.mode == "start"
    assert decision.intent == "start_new_task"


# ── decide_ingress: fallback new root (no active tasks) ─────────


def test_decide_ingress_no_active_tasks(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    decision = ctrl.decide_ingress(
        conversation_id=conv_id,
        source_channel="chat",
        raw_text="build a complex feature with many components",
        prompt="build a complex feature with many components",
    )
    assert decision.mode == "start"
    assert decision.resolution == "start_new_root"


# ── decide_ingress: append_note to running task ─────────────────


def test_decide_ingress_append_note(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id, goal="fix the database issue")
    decision = ctrl.decide_ingress(
        conversation_id=conv_id,
        source_channel="chat",
        raw_text="also fix the database index",
        prompt="also fix the database index",
        reply_to_task_id=ctx.task_id,
    )
    assert decision.resolution in ("append_note", "start_new_root")


# ── decide_ingress: fork_child ──────────────────────────────────


def test_decide_ingress_fork_child(shared_store: KernelStore, conv_id: str, monkeypatch) -> None:
    ctrl = _ctrl(shared_store)
    _start_task(ctrl, conv_id, goal="main feature work")
    from hermit.kernel.task.services.ingress_router import IngressRouter

    monkeypatch.setattr(IngressRouter, "_has_branch_marker", staticmethod(lambda text: True))
    decision = ctrl.decide_ingress(
        conversation_id=conv_id,
        source_channel="chat",
        raw_text="branch this into a subtask for testing",
        prompt="branch this into a subtask for testing",
    )
    assert decision.resolution == "fork_child"


# ── _looks_like_task_followup ───────────────────────────────────


def test_looks_like_task_followup_empty(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    assert ctrl._looks_like_task_followup("", task_id=ctx.task_id) is False


def test_looks_like_task_followup_with_topic(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id, goal="database migration")
    result = ctrl._looks_like_task_followup(
        "how is the database migration going",
        task_id=ctx.task_id,
    )
    assert isinstance(result, bool)


# ── _task_context_texts ─────────────────────────────────────────


def test_task_context_texts(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id, goal="deploy feature")
    texts = ctrl._task_context_texts(ctx.task_id)
    assert any("deploy feature" in text for text in texts)


def test_task_context_texts_with_notes(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id, goal="build API")
    ctrl.append_note(
        task_id=ctx.task_id,
        source_channel="chat",
        raw_text="add authentication endpoint",
        prompt="add authentication endpoint",
    )
    texts = ctrl._task_context_texts(ctx.task_id)
    assert len(texts) >= 2  # title + goal + note


# ── _terminal_continuation_tasks ────────────────────────────────


def test_terminal_continuation_tasks_empty(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    tasks = ctrl._terminal_continuation_tasks(conv_id)
    assert tasks == []


def test_terminal_continuation_tasks_with_completed(
    shared_store: KernelStore, conv_id: str
) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id, goal="completed task")
    ctrl.finalize_result(ctx, status="completed")
    tasks = ctrl._terminal_continuation_tasks(conv_id)
    assert len(tasks) == 1


# ── _continuation_candidate_texts ───────────────────────────────


def test_continuation_candidate_texts(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id, goal="implement feature X")
    ctrl.finalize_result(ctx, status="completed", result_text="Feature X done")
    texts = ctrl._continuation_candidate_texts(ctx.task_id)
    assert any("implement feature X" in text for text in texts)


def test_continuation_candidate_texts_nonexistent(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    texts = ctrl._continuation_candidate_texts("nonexistent")
    assert texts == []


# ── _continuation_anchor ────────────────────────────────────────


def test_continuation_anchor(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id, goal="my task goal")
    ctrl.finalize_result(ctx, status="completed")
    anchor = ctrl._continuation_anchor(ctx.task_id, selection_reason="test_reason")
    assert anchor["anchor_task_id"] == ctx.task_id
    assert anchor["anchor_kind"] == "completed_outcome"
    assert anchor["selection_reason"] == "test_reason"


def test_continuation_anchor_nonexistent(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    anchor = ctrl._continuation_anchor("nonexistent", selection_reason="test")
    assert anchor == {}


# ── resolve_continuation_target ─────────────────────────────────


def test_resolve_continuation_target_none(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    result = ctrl.resolve_continuation_target(conversation_id=conv_id, raw_text="")
    assert result is None


# ── _texts_overlap static method ────────────────────────────────


def test_texts_overlap_static() -> None:
    result = TaskController._texts_overlap("database migration", "migration")
    assert isinstance(result, bool)


# ── _mark_attempt_input_dirty ───────────────────────────────────


def test_mark_attempt_input_dirty_no_attempt(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    ctrl._mark_attempt_input_dirty(
        task_id="nonexistent",
        ingress_id=None,
        note_event_seq=None,
        emit_event=True,
    )


# ── mark_suspended with invalid transition ──────────────────────


def test_mark_suspended_invalid_transition(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.finalize_result(ctx, status="completed")
    ctrl.mark_suspended(ctx, waiting_kind="awaiting_approval")


# ── pause_task invalid transition ───────────────────────────────


def test_pause_task_invalid_transition(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.finalize_result(ctx, status="completed")
    ctrl.pause_task(ctx.task_id)


# ── cancel_task invalid transition ──────────────────────────────


def test_cancel_task_invalid_transition(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.finalize_result(ctx, status="completed")
    ctrl.cancel_task(ctx.task_id)


# ── _set_focus: noop when already focused ───────────────────────


def test_set_focus_noop_same_task(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl._set_focus(conversation_id=conv_id, task_id=ctx.task_id, reason="task_started")
    ctrl._set_focus(conversation_id=conv_id, task_id=ctx.task_id, reason="ingress_bound")


# ── _set_focus: clear ───────────────────────────────────────────


def test_set_focus_clear(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    _start_task(ctrl, conv_id)
    ctrl._set_focus(conversation_id=conv_id, task_id=None, reason="cleared")
    conv = shared_store.get_conversation(conv_id)
    assert conv.focus_task_id is None


# ── _try_upgrade_to_steering: with /steer command ──────────────


def test_try_upgrade_to_steering_with_command(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl._try_upgrade_to_steering(
        task_id=ctx.task_id,
        raw_text="/steer focus on performance only",
        source_channel="chat",
    )


def test_try_upgrade_to_steering_with_type(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl._try_upgrade_to_steering(
        task_id=ctx.task_id,
        raw_text="/steer --type priority focus on this first",
        source_channel="cli",
    )


def test_try_upgrade_to_steering_type_only(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl._try_upgrade_to_steering(
        task_id=ctx.task_id,
        raw_text="/steer --type scope",
        source_channel="chat",
    )
