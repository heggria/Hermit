"""Tests for TaskController — target 80%+ coverage on controller.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import (
    _AUTO_PARENT,
    _LOW_SIGNAL_RE,
    AUTO_PARENT,
    IngressDecision,
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


# ── IngressDecision dataclass ─────────────────────────────────────


def test_ingress_decision_defaults() -> None:
    d = IngressDecision(mode="start")
    assert d.mode == "start"
    assert d.intent == ""
    assert d.reason == ""
    assert d.resolution == ""
    assert d.ingress_id is None
    assert d.task_id is None
    assert d.confidence == 0.0
    assert d.parent_task_id is _AUTO_PARENT


# ── source_from_session ───────────────────────────────────────────


class TestSourceFromSession:
    def test_webhook(self, tmp_path: Path) -> None:
        _, ctrl = _setup(tmp_path)
        assert ctrl.source_from_session("webhook-abc") == "webhook"

    def test_scheduler(self, tmp_path: Path) -> None:
        _, ctrl = _setup(tmp_path)
        assert ctrl.source_from_session("schedule-xyz") == "scheduler"

    def test_cli(self, tmp_path: Path) -> None:
        _, ctrl = _setup(tmp_path)
        assert ctrl.source_from_session("cli") == "cli"
        assert ctrl.source_from_session("cli-session-1") == "cli"

    def test_feishu_with_colon(self, tmp_path: Path) -> None:
        _, ctrl = _setup(tmp_path)
        assert ctrl.source_from_session("oc_abc:def") == "feishu"

    def test_feishu_oc_prefix(self, tmp_path: Path) -> None:
        _, ctrl = _setup(tmp_path)
        assert ctrl.source_from_session("oc_12345") == "feishu"

    def test_default_chat(self, tmp_path: Path) -> None:
        _, ctrl = _setup(tmp_path)
        assert ctrl.source_from_session("random-session") == "chat"


# ── ensure_conversation ──────────────────────────────────────────


def test_ensure_conversation(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctrl.ensure_conversation("conv-new", source_channel="feishu")
    conv = store.get_conversation("conv-new")
    assert conv is not None
    assert conv.source_channel == "feishu"


def test_ensure_conversation_auto_source(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctrl.ensure_conversation("webhook-conv")
    conv = store.get_conversation("webhook-conv")
    assert conv is not None
    assert conv.source_channel == "webhook"


# ── latest_task ──────────────────────────────────────────────────


def test_latest_task_none(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    assert ctrl.latest_task("conv-1") is None


def test_latest_task_returns_last(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    _start_task(ctrl, goal="First")
    ctx2 = _start_task(ctrl, goal="Second")
    latest = ctrl.latest_task("conv-1")
    assert latest is not None
    assert latest.task_id == ctx2.task_id


# ── active_task_for_conversation ─────────────────────────────────


def test_active_task_returns_running(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    active = ctrl.active_task_for_conversation("conv-1")
    assert active is not None
    assert active.task_id == ctx.task_id


def test_active_task_returns_none_when_completed(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.finalize_result(ctx, status="completed")
    active = ctrl.active_task_for_conversation("conv-1")
    assert active is None


def test_active_task_returns_none_for_empty(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    assert ctrl.active_task_for_conversation("conv-1") is None


# ── start_task ───────────────────────────────────────────────────


class TestStartTask:
    def test_creates_task_step_attempt(self, tmp_path: Path) -> None:
        store, ctrl = _setup(tmp_path)
        ctx = _start_task(ctrl)
        assert ctx.conversation_id == "conv-1"
        assert ctx.source_channel == "chat"
        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.goal == "Test goal"

    def test_auto_parent(self, tmp_path: Path) -> None:
        store, ctrl = _setup(tmp_path)
        ctx1 = _start_task(ctrl, goal="First")
        ctx2 = _start_task(ctrl, goal="Second")
        task2 = store.get_task(ctx2.task_id)
        assert task2 is not None
        assert task2.parent_task_id == ctx1.task_id

    def test_explicit_parent_none(self, tmp_path: Path) -> None:
        store, ctrl = _setup(tmp_path)
        _start_task(ctrl, goal="First")
        ctx2 = _start_task(ctrl, goal="Second", parent_task_id=None)
        task2 = store.get_task(ctx2.task_id)
        assert task2 is not None
        assert task2.parent_task_id is None

    def test_with_workspace_root(self, tmp_path: Path) -> None:
        _, ctrl = _setup(tmp_path)
        ctx = _start_task(ctrl, workspace_root="/home/user")
        assert ctx.workspace_root == "/home/user"

    def test_default_title_from_goal(self, tmp_path: Path) -> None:
        store, ctrl = _setup(tmp_path)
        ctx = _start_task(ctrl, goal="My goal text")
        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.title == "My goal text"

    def test_empty_goal_gets_default(self, tmp_path: Path) -> None:
        store, ctrl = _setup(tmp_path)
        ctx = _start_task(ctrl, goal="")
        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.title  # should be non-empty default

    def test_policy_profile(self, tmp_path: Path) -> None:
        _, ctrl = _setup(tmp_path)
        ctx = _start_task(ctrl, policy_profile="supervised")
        assert ctx.policy_profile == "supervised"

    def test_ingress_metadata_binding(self, tmp_path: Path) -> None:
        store, ctrl = _setup(tmp_path)
        ctx = _start_task(
            ctrl,
            ingress_metadata={
                "ingress_id": "test_ingress_1",
                "ingress_resolution": "start_new_root",
            },
        )
        # Should have called _bind_ingress_on_task_creation
        store.list_ingresses(conversation_id="conv-1", limit=10)
        # The ingress was created externally so this just verifies no crash
        assert ctx.task_id


# ── enqueue_task ─────────────────────────────────────────────────


def test_enqueue_task(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = ctrl.enqueue_task(
        conversation_id="conv-1",
        goal="Queued task",
        source_channel="scheduler",
        kind="respond",
    )
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "queued"
    assert task.goal == "Queued task"


def test_enqueue_task_with_source_ref(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = ctrl.enqueue_task(
        conversation_id="conv-1",
        goal="From webhook",
        source_channel="webhook",
        kind="respond",
        source_ref="webhook-ref-123",
    )
    assert ctx.source_channel == "webhook"


# ── start_followup_step ──────────────────────────────────────────


def test_start_followup_step(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    followup = ctrl.start_followup_step(task_id=ctx.task_id, kind="respond")
    assert followup.task_id == ctx.task_id
    assert followup.step_id != ctx.step_id


def test_start_followup_step_unknown_task(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    with pytest.raises(KeyError):
        ctrl.start_followup_step(task_id="nonexistent", kind="respond")


# ── context_for_attempt ──────────────────────────────────────────


def test_context_for_attempt(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    recovered = ctrl.context_for_attempt(ctx.step_attempt_id)
    assert recovered.task_id == ctx.task_id
    assert recovered.step_id == ctx.step_id


def test_context_for_attempt_unknown(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    with pytest.raises(KeyError):
        ctrl.context_for_attempt("nonexistent")


# ── finalize_result ──────────────────────────────────────────────


class TestFinalizeResult:
    def test_completed(self, tmp_path: Path) -> None:
        store, ctrl = _setup(tmp_path)
        ctx = _start_task(ctrl)
        ctrl.finalize_result(ctx, status="completed", result_preview="Done!")
        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "completed"

    def test_failed(self, tmp_path: Path) -> None:
        store, ctrl = _setup(tmp_path)
        ctx = _start_task(ctrl)
        ctrl.finalize_result(ctx, status="failed")
        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "failed"

    def test_with_result_text(self, tmp_path: Path) -> None:
        store, ctrl = _setup(tmp_path)
        ctx = _start_task(ctrl)
        ctrl.finalize_result(ctx, status="completed", result_text="Full result")
        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "completed"

    def test_double_finalize_is_idempotent(self, tmp_path: Path) -> None:
        store, ctrl = _setup(tmp_path)
        ctx = _start_task(ctrl)
        ctrl.finalize_result(ctx, status="completed")
        # Second finalize should be a no-op due to CAS guard
        ctrl.finalize_result(ctx, status="failed")
        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "completed"

    def test_workspace_lease_release(self, tmp_path: Path) -> None:
        store, _ctrl_base = _setup(tmp_path)
        released: list[str] = []

        class FakeLeaseService:
            def release_all_for_task(self, task_id: str) -> list[str]:
                released.append(task_id)
                return ["lease-1"]

        ctrl = TaskController(store, workspace_lease_service=FakeLeaseService())
        ctx = ctrl.start_task(
            conversation_id="conv-1",
            goal="test",
            source_channel="chat",
            kind="respond",
        )
        ctrl.finalize_result(ctx, status="completed")
        assert released == [ctx.task_id]

    def test_workspace_lease_error_ignored(self, tmp_path: Path) -> None:
        store, _ = _setup(tmp_path)

        class FailingLeaseService:
            def release_all_for_task(self, task_id: str) -> list[str]:
                raise RuntimeError("lease service error")

        ctrl = TaskController(store, workspace_lease_service=FailingLeaseService())
        ctx = ctrl.start_task(
            conversation_id="conv-1",
            goal="test",
            source_channel="chat",
            kind="respond",
        )
        # Should not raise
        ctrl.finalize_result(ctx, status="completed")
        task = store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "completed"


# ── mark_planning_ready ──────────────────────────────────────────


def test_mark_planning_ready(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.mark_planning_ready(ctx, plan_artifact_ref="plan_ref_1")
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "blocked"


def test_mark_planning_ready_with_preview(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.mark_planning_ready(
        ctx,
        plan_artifact_ref="plan_ref_1",
        result_preview="Preview text",
        result_text="Full text",
    )
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "blocked"


# ── mark_blocked / mark_suspended ────────────────────────────────


def test_mark_blocked(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.mark_blocked(ctx)
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "blocked"


def test_mark_suspended(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.mark_suspended(ctx, waiting_kind="awaiting_approval")
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "blocked"


# ── pause_task / cancel_task ─────────────────────────────────────


def test_pause_task(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.pause_task(ctx.task_id)
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "paused"


def test_pause_task_unknown(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    with pytest.raises(KeyError):
        ctrl.pause_task("nonexistent")


def test_cancel_task(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.cancel_task(ctx.task_id)
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "cancelled"


def test_cancel_task_unknown(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    with pytest.raises(KeyError):
        ctrl.cancel_task("nonexistent")


# ── focus_task ───────────────────────────────────────────────────


def test_focus_task(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.focus_task("conv-1", ctx.task_id)
    conv = store.get_conversation("conv-1")
    assert conv is not None
    assert conv.focus_task_id == ctx.task_id


def test_focus_task_unknown(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    with pytest.raises(KeyError):
        ctrl.focus_task("conv-1", "nonexistent")


def test_focus_task_wrong_conversation(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    store.ensure_conversation("conv-2", source_channel="chat")
    with pytest.raises(KeyError):
        ctrl.focus_task("conv-2", ctx.task_id)


# ── reprioritize_task ────────────────────────────────────────────


def test_reprioritize_task(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.reprioritize_task(ctx.task_id, priority="high")
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.priority == "high"


def test_reprioritize_unknown_task(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    with pytest.raises(KeyError):
        ctrl.reprioritize_task("nonexistent", priority="high")


# ── resume_attempt ───────────────────────────────────────────────


def test_resume_attempt(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    # Block it first
    ctrl.mark_blocked(ctx)
    # Then resume
    resumed = ctrl.resume_attempt(ctx.step_attempt_id)
    assert resumed.task_id == ctx.task_id


def test_resume_attempt_unknown(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    with pytest.raises(KeyError):
        ctrl.resume_attempt("nonexistent")


def test_resume_attempt_with_recovery_required(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    # Set recovery_required in context
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    context = dict(attempt.context or {})
    context["recovery_required"] = True
    context["reentry_reason"] = "worker_interrupted"
    store.update_step_attempt(ctx.step_attempt_id, context=context)
    resumed = ctrl.resume_attempt(ctx.step_attempt_id)
    assert resumed.task_id == ctx.task_id


# ── enqueue_resume ───────────────────────────────────────────────


def test_enqueue_resume_normal(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.mark_blocked(ctx)
    resumed = ctrl.enqueue_resume(ctx.step_attempt_id)
    assert resumed.task_id == ctx.task_id
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "queued"


def test_enqueue_resume_unknown(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    with pytest.raises(KeyError):
        ctrl.enqueue_resume("nonexistent")


def test_enqueue_resume_input_dirty_approval(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    # Set input_dirty + awaiting_approval
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    context = dict(attempt.context or {})
    context["input_dirty"] = True
    store.update_step_attempt(
        ctx.step_attempt_id,
        context=context,
        waiting_reason="awaiting_approval",
    )
    resumed = ctrl.enqueue_resume(ctx.step_attempt_id)
    # Should create a successor attempt
    assert resumed.step_attempt_id != ctx.step_attempt_id
    assert resumed.task_id == ctx.task_id


# ── resolve_text_command ─────────────────────────────────────────


def test_resolve_text_command_returns_none_for_normal(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    result = ctrl.resolve_text_command("conv-1", "hello there")
    assert result is None


# ── append_note ──────────────────────────────────────────────────


def test_append_note(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    seq = ctrl.append_note(
        task_id=ctx.task_id,
        source_channel="chat",
        raw_text="Additional context",
        prompt="Additional context",
    )
    assert isinstance(seq, int)


def test_append_note_unknown_task(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    with pytest.raises(KeyError):
        ctrl.append_note(
            task_id="nonexistent",
            source_channel="chat",
            raw_text="text",
            prompt="prompt",
        )


# ── update_attempt_phase ─────────────────────────────────────────


def test_update_attempt_phase(tmp_path: Path) -> None:
    store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.update_attempt_phase(ctx.step_attempt_id, phase="executing")
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert attempt.context.get("phase") == "executing"


def test_update_attempt_phase_noop_same(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    ctrl.update_attempt_phase(ctx.step_attempt_id, phase="planning")
    # Setting same phase again should be a no-op
    ctrl.update_attempt_phase(ctx.step_attempt_id, phase="planning")


def test_update_attempt_phase_unknown(tmp_path: Path) -> None:
    _, ctrl = _setup(tmp_path)
    # Should not raise, just return
    ctrl.update_attempt_phase("nonexistent", phase="test")


# ── _ingress_queue_priority ──────────────────────────────────────


class TestIngressQueuePriority:
    def test_approval_resume(self) -> None:
        p = TaskController._ingress_queue_priority(
            source_channel="chat",
            requested_by=None,
            metadata={"resume_kind": "approval"},
        )
        assert p == 90

    def test_user_channels(self) -> None:
        for ch in ("chat", "feishu", "cli"):
            p = TaskController._ingress_queue_priority(
                source_channel=ch, requested_by=None, metadata={}
            )
            assert p == 100

    def test_with_requested_by(self) -> None:
        p = TaskController._ingress_queue_priority(
            source_channel="webhook", requested_by="user", metadata={}
        )
        assert p == 100

    def test_scheduler_channel(self) -> None:
        p = TaskController._ingress_queue_priority(
            source_channel="scheduler", requested_by=None, metadata={}
        )
        assert p == 10

    def test_unknown_channel(self) -> None:
        p = TaskController._ingress_queue_priority(
            source_channel="unknown", requested_by=None, metadata={}
        )
        assert p == 0


# ── Static/class methods ─────────────────────────────────────────


def test_normalize_ingress_text() -> None:
    result = TaskController._normalize_ingress_text("  hello   world  ")
    assert result == "hello world"


def test_extract_artifact_refs() -> None:
    refs = TaskController._extract_artifact_refs("Check artifact_abc123 and artifact_def456", None)
    assert "artifact_abc123" in refs
    assert "artifact_def456" in refs


def test_extract_artifact_refs_dedup() -> None:
    refs = TaskController._extract_artifact_refs("artifact_abc123 artifact_abc123", None)
    assert len(refs) == 1


def test_is_chat_only_message() -> None:
    assert TaskController._is_chat_only_message("") is True
    assert TaskController._is_chat_only_message("   ") is True
    assert TaskController._is_chat_only_message("???") is True
    assert TaskController._is_chat_only_message("...") is True


def test_is_chat_only_non_greeting() -> None:
    assert TaskController._is_chat_only_message("please fix the bug") is False


def test_sanitize_context_text() -> None:
    result = TaskController._sanitize_context_text("  hello\n\n  world  ")
    assert "hello" in result
    assert "world" in result


def test_low_signal_regex() -> None:
    assert _LOW_SIGNAL_RE.match("???")
    assert _LOW_SIGNAL_RE.match("!!!")
    assert _LOW_SIGNAL_RE.match("...")
    assert not _LOW_SIGNAL_RE.match("hello")


# ── _binding_snapshot ─────────────────────────────────────────────


def test_binding_snapshot() -> None:
    snapshot = TaskController._binding_snapshot(
        resolution="start_new_root",
        chosen_task_id="task-1",
        parent_task_id=None,
        confidence=0.9,
        margin=0.1,
        reason_codes=["test"],
    )
    assert snapshot["resolution"] == "start_new_root"
    assert snapshot["chosen_task_id"] == "task-1"
    assert snapshot["reason_codes"] == ["test"]


def test_binding_snapshot_with_candidates() -> None:
    snapshot = TaskController._binding_snapshot(
        resolution="append_note",
        chosen_task_id="task-1",
        parent_task_id=None,
        confidence=0.9,
        margin=0.1,
        reason_codes=["test"],
        candidates=[{"task_id": "t1", "score": 0.9}],
    )
    assert "candidates" in snapshot


# ── _try_upgrade_to_steering ─────────────────────────────────────


def test_try_upgrade_to_steering_noop(tmp_path: Path) -> None:
    _store, ctrl = _setup(tmp_path)
    ctx = _start_task(ctrl)
    # Normal text should not trigger steering
    ctrl._try_upgrade_to_steering(
        task_id=ctx.task_id, raw_text="normal message", source_channel="chat"
    )


# ── AUTO_PARENT alias ────────────────────────────────────────────


def test_auto_parent_alias() -> None:
    assert AUTO_PARENT is _AUTO_PARENT
