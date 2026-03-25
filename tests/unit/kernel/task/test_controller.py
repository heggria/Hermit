"""Tests for TaskController — target 80%+ coverage on controller.py."""

from __future__ import annotations

import uuid

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
    def test_webhook(self, shared_store: KernelStore) -> None:
        ctrl = _ctrl(shared_store)
        assert ctrl.source_from_session("webhook-abc") == "webhook"

    def test_scheduler(self, shared_store: KernelStore) -> None:
        ctrl = _ctrl(shared_store)
        assert ctrl.source_from_session("schedule-xyz") == "scheduler"

    def test_cli(self, shared_store: KernelStore) -> None:
        ctrl = _ctrl(shared_store)
        assert ctrl.source_from_session("cli") == "cli"
        assert ctrl.source_from_session("cli-session-1") == "cli"

    def test_feishu_with_colon(self, shared_store: KernelStore) -> None:
        ctrl = _ctrl(shared_store)
        assert ctrl.source_from_session("oc_abc:def") == "feishu"

    def test_feishu_oc_prefix(self, shared_store: KernelStore) -> None:
        ctrl = _ctrl(shared_store)
        assert ctrl.source_from_session("oc_12345") == "feishu"

    def test_default_chat(self, shared_store: KernelStore) -> None:
        ctrl = _ctrl(shared_store)
        assert ctrl.source_from_session("random-session") == "chat"


# ── ensure_conversation ──────────────────────────────────────────


def test_ensure_conversation(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    cid = f"conv-new-{uuid.uuid4().hex[:6]}"
    ctrl.ensure_conversation(cid, source_channel="feishu")
    conv = shared_store.get_conversation(cid)
    assert conv is not None
    assert conv.source_channel == "feishu"


def test_ensure_conversation_auto_source(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    cid = f"webhook-conv-{uuid.uuid4().hex[:6]}"
    ctrl.ensure_conversation(cid)
    conv = shared_store.get_conversation(cid)
    assert conv is not None
    assert conv.source_channel == "webhook"


# ── latest_task ──────────────────────────────────────────────────


def test_latest_task_none(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    assert ctrl.latest_task(conv_id) is None


def test_latest_task_returns_last(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    _start_task(ctrl, conv_id, goal="First")
    ctx2 = _start_task(ctrl, conv_id, goal="Second")
    latest = ctrl.latest_task(conv_id)
    assert latest is not None
    assert latest.task_id == ctx2.task_id


# ── active_task_for_conversation ─────────────────────────────────


def test_active_task_returns_running(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    active = ctrl.active_task_for_conversation(conv_id)
    assert active is not None
    assert active.task_id == ctx.task_id


def test_active_task_returns_none_when_completed(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.finalize_result(ctx, status="completed")
    active = ctrl.active_task_for_conversation(conv_id)
    assert active is None


def test_active_task_returns_none_for_empty(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    assert ctrl.active_task_for_conversation(conv_id) is None


# ── start_task ───────────────────────────────────────────────────


class TestStartTask:
    def test_creates_task_step_attempt(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        ctx = _start_task(ctrl, conv_id)
        assert ctx.conversation_id == conv_id
        assert ctx.source_channel == "chat"
        task = shared_store.get_task(ctx.task_id)
        assert task is not None
        assert task.goal == "Test goal"

    def test_auto_parent(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        ctx1 = _start_task(ctrl, conv_id, goal="First")
        ctx2 = _start_task(ctrl, conv_id, goal="Second")
        task2 = shared_store.get_task(ctx2.task_id)
        assert task2 is not None
        assert task2.parent_task_id == ctx1.task_id

    def test_explicit_parent_none(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        _start_task(ctrl, conv_id, goal="First")
        ctx2 = _start_task(ctrl, conv_id, goal="Second", parent_task_id=None)
        task2 = shared_store.get_task(ctx2.task_id)
        assert task2 is not None
        assert task2.parent_task_id is None

    def test_with_workspace_root(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        ctx = _start_task(ctrl, conv_id, workspace_root="/home/user")
        assert ctx.workspace_root == "/home/user"

    def test_default_title_from_goal(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        ctx = _start_task(ctrl, conv_id, goal="My goal text")
        task = shared_store.get_task(ctx.task_id)
        assert task is not None
        assert task.title == "My goal text"

    def test_empty_goal_gets_default(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        ctx = _start_task(ctrl, conv_id, goal="")
        task = shared_store.get_task(ctx.task_id)
        assert task is not None
        assert task.title  # should be non-empty default

    def test_policy_profile(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        ctx = _start_task(ctrl, conv_id, policy_profile="supervised")
        assert ctx.policy_profile == "supervised"

    def test_ingress_metadata_binding(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        ctx = _start_task(
            ctrl,
            conv_id,
            ingress_metadata={
                "ingress_id": "test_ingress_1",
                "ingress_resolution": "start_new_root",
            },
        )
        shared_store.list_ingresses(conversation_id=conv_id, limit=10)
        assert ctx.task_id


# ── enqueue_task ─────────────────────────────────────────────────


def test_enqueue_task(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = ctrl.enqueue_task(
        conversation_id=conv_id,
        goal="Queued task",
        source_channel="scheduler",
        kind="respond",
    )
    task = shared_store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "queued"
    assert task.goal == "Queued task"


def test_enqueue_task_with_source_ref(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = ctrl.enqueue_task(
        conversation_id=conv_id,
        goal="From webhook",
        source_channel="webhook",
        kind="respond",
        source_ref="webhook-ref-123",
    )
    assert ctx.source_channel == "webhook"


# ── start_followup_step ──────────────────────────────────────────


def test_start_followup_step(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    followup = ctrl.start_followup_step(task_id=ctx.task_id, kind="respond")
    assert followup.task_id == ctx.task_id
    assert followup.step_id != ctx.step_id


def test_start_followup_step_unknown_task(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    with pytest.raises(KeyError):
        ctrl.start_followup_step(task_id="nonexistent", kind="respond")


# ── context_for_attempt ──────────────────────────────────────────


def test_context_for_attempt(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    recovered = ctrl.context_for_attempt(ctx.step_attempt_id)
    assert recovered.task_id == ctx.task_id
    assert recovered.step_id == ctx.step_id


def test_context_for_attempt_unknown(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    with pytest.raises(KeyError):
        ctrl.context_for_attempt("nonexistent")


# ── finalize_result ──────────────────────────────────────────────


class TestFinalizeResult:
    def test_completed(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        ctx = _start_task(ctrl, conv_id)
        ctrl.finalize_result(ctx, status="completed", result_preview="Done!")
        task = shared_store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "completed"

    def test_failed(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        ctx = _start_task(ctrl, conv_id)
        ctrl.finalize_result(ctx, status="failed")
        task = shared_store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "failed"

    def test_with_result_text(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        ctx = _start_task(ctrl, conv_id)
        ctrl.finalize_result(ctx, status="completed", result_text="Full result")
        task = shared_store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "completed"

    def test_double_finalize_is_idempotent(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        ctx = _start_task(ctrl, conv_id)
        ctrl.finalize_result(ctx, status="completed")
        ctrl.finalize_result(ctx, status="failed")
        task = shared_store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "completed"

    def test_workspace_lease_release(self, shared_store: KernelStore, conv_id: str) -> None:
        released: list[str] = []

        class FakeLeaseService:
            def release_all_for_task(self, task_id: str) -> list[str]:
                released.append(task_id)
                return ["lease-1"]

        ctrl = TaskController(shared_store, workspace_lease_service=FakeLeaseService())
        ctx = ctrl.start_task(
            conversation_id=conv_id,
            goal="test",
            source_channel="chat",
            kind="respond",
        )
        ctrl.finalize_result(ctx, status="completed")
        assert released == [ctx.task_id]

    def test_workspace_lease_error_ignored(self, shared_store: KernelStore, conv_id: str) -> None:
        class FailingLeaseService:
            def release_all_for_task(self, task_id: str) -> list[str]:
                raise RuntimeError("lease service error")

        ctrl = TaskController(shared_store, workspace_lease_service=FailingLeaseService())
        ctx = ctrl.start_task(
            conversation_id=conv_id,
            goal="test",
            source_channel="chat",
            kind="respond",
        )
        ctrl.finalize_result(ctx, status="completed")
        task = shared_store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "completed"


# ── mark_planning_ready ──────────────────────────────────────────


def test_mark_planning_ready(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.mark_planning_ready(ctx, plan_artifact_ref="plan_ref_1")
    task = shared_store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "blocked"


def test_mark_planning_ready_with_preview(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.mark_planning_ready(
        ctx,
        plan_artifact_ref="plan_ref_1",
        result_preview="Preview text",
        result_text="Full text",
    )
    task = shared_store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "blocked"


# ── mark_blocked / mark_suspended ────────────────────────────────


def test_mark_blocked(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.mark_blocked(ctx)
    task = shared_store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "blocked"


def test_mark_suspended(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.mark_suspended(ctx, waiting_kind="awaiting_approval")
    task = shared_store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "blocked"


# ── pause_task / cancel_task ─────────────────────────────────────


def test_pause_task(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.pause_task(ctx.task_id)
    task = shared_store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "paused"


def test_pause_task_unknown(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    with pytest.raises(KeyError):
        ctrl.pause_task("nonexistent")


def test_cancel_task(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.cancel_task(ctx.task_id)
    task = shared_store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "cancelled"


def test_cancel_task_unknown(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    with pytest.raises(KeyError):
        ctrl.cancel_task("nonexistent")


# ── cancel_task cascade ──────────────────────────────────────────


class TestCancelTaskCascade:
    """Verify that cancelling a parent task cascades to all descendants."""

    def test_cancel_parent_cancels_children(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        parent_ctx = _start_task(ctrl, conv_id, goal="Parent task")
        child_ctx = _start_task(ctrl, conv_id, goal="Child task", parent_task_id=parent_ctx.task_id)
        cascaded = ctrl.cancel_task(parent_ctx.task_id)
        parent = shared_store.get_task(parent_ctx.task_id)
        child = shared_store.get_task(child_ctx.task_id)
        assert parent is not None and parent.status == "cancelled"
        assert child is not None and child.status == "cancelled"
        assert child_ctx.task_id in cascaded

    def test_cancel_cascades_recursively_to_grandchildren(
        self, shared_store: KernelStore, conv_id: str
    ) -> None:
        ctrl = _ctrl(shared_store)
        root_ctx = _start_task(ctrl, conv_id, goal="Root")
        child_ctx = _start_task(ctrl, conv_id, goal="Child", parent_task_id=root_ctx.task_id)
        grandchild_ctx = _start_task(
            ctrl, conv_id, goal="Grandchild", parent_task_id=child_ctx.task_id
        )
        cascaded = ctrl.cancel_task(root_ctx.task_id)
        root = shared_store.get_task(root_ctx.task_id)
        child = shared_store.get_task(child_ctx.task_id)
        grandchild = shared_store.get_task(grandchild_ctx.task_id)
        assert root is not None and root.status == "cancelled"
        assert child is not None and child.status == "cancelled"
        assert grandchild is not None and grandchild.status == "cancelled"
        assert grandchild_ctx.task_id in cascaded
        assert child_ctx.task_id in cascaded

    def test_cancel_skips_terminal_children(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        parent_ctx = _start_task(ctrl, conv_id, goal="Parent")
        completed_child_ctx = _start_task(
            ctrl, conv_id, goal="Already done", parent_task_id=parent_ctx.task_id
        )
        running_child_ctx = _start_task(
            ctrl, conv_id, goal="Still running", parent_task_id=parent_ctx.task_id
        )
        ctrl.finalize_result(completed_child_ctx, status="completed")
        cascaded = ctrl.cancel_task(parent_ctx.task_id)
        completed = shared_store.get_task(completed_child_ctx.task_id)
        running = shared_store.get_task(running_child_ctx.task_id)
        assert completed is not None and completed.status == "completed"
        assert running is not None and running.status == "cancelled"
        assert running_child_ctx.task_id in cascaded
        assert completed_child_ctx.task_id not in cascaded

    def test_cascade_emits_events(self, shared_store: KernelStore, conv_id: str) -> None:
        ctrl = _ctrl(shared_store)
        parent_ctx = _start_task(ctrl, conv_id, goal="Parent")
        child_ctx = _start_task(ctrl, conv_id, goal="Child", parent_task_id=parent_ctx.task_id)
        ctrl.cancel_task(parent_ctx.task_id)
        child_events = shared_store.list_events(
            task_id=child_ctx.task_id,
            event_type="task.cascade_cancelled",
            limit=10,
        )
        assert len(child_events) == 1
        payload = child_events[0]["payload"]
        assert payload["cascaded_from"] == parent_ctx.task_id
        assert payload["task_status"] == "cancelled"

    def test_cascade_releases_workspace_leases_for_children(
        self, shared_store: KernelStore, conv_id: str
    ) -> None:
        released_task_ids: list[str] = []

        class TrackingLeaseService:
            def release_all_for_task(self, task_id: str) -> list[str]:
                released_task_ids.append(task_id)
                return [f"lease-{task_id}"]

        ctrl = TaskController(shared_store, workspace_lease_service=TrackingLeaseService())
        parent_ctx = ctrl.start_task(
            conversation_id=conv_id, goal="Parent", source_channel="chat", kind="respond"
        )
        child_ctx = ctrl.start_task(
            conversation_id=conv_id,
            goal="Child",
            source_channel="chat",
            kind="respond",
            parent_task_id=parent_ctx.task_id,
        )
        ctrl.cancel_task(parent_ctx.task_id)
        assert child_ctx.task_id in released_task_ids
        assert parent_ctx.task_id in released_task_ids

    def test_cancel_no_children_returns_empty(
        self, shared_store: KernelStore, conv_id: str
    ) -> None:
        ctrl = _ctrl(shared_store)
        ctx = _start_task(ctrl, conv_id, goal="Lone task")
        cascaded = ctrl.cancel_task(ctx.task_id)
        assert cascaded == []

    def test_cancel_task_lease_release_raises_still_completes(
        self, shared_store: KernelStore, conv_id: str
    ) -> None:
        """When workspace lease service raises during cancel_task, cancel still completes."""

        class ExplodingLeaseService:
            def release_all_for_task(self, task_id: str) -> list[str]:
                raise RuntimeError("lease service exploded")

        ctrl = TaskController(shared_store, workspace_lease_service=ExplodingLeaseService())
        ctx = ctrl.start_task(
            conversation_id=conv_id, goal="Test", source_channel="chat", kind="respond"
        )
        ctrl.cancel_task(ctx.task_id)
        task = shared_store.get_task(ctx.task_id)
        assert task is not None
        assert task.status == "cancelled"


# ── focus_task ───────────────────────────────────────────────────


def test_focus_task(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.focus_task(conv_id, ctx.task_id)
    conv = shared_store.get_conversation(conv_id)
    assert conv is not None
    assert conv.focus_task_id == ctx.task_id


def test_focus_task_unknown(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    with pytest.raises(KeyError):
        ctrl.focus_task(conv_id, "nonexistent")


def test_focus_task_wrong_conversation(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    cid2 = f"conv-other-{uuid.uuid4().hex[:6]}"
    shared_store.ensure_conversation(cid2, source_channel="chat")
    with pytest.raises(KeyError):
        ctrl.focus_task(cid2, ctx.task_id)


# ── reprioritize_task ────────────────────────────────────────────


def test_reprioritize_task(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.reprioritize_task(ctx.task_id, priority="high")
    task = shared_store.get_task(ctx.task_id)
    assert task is not None
    assert task.priority == "high"


def test_reprioritize_unknown_task(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    with pytest.raises(KeyError):
        ctrl.reprioritize_task("nonexistent", priority="high")


# ── resume_attempt ───────────────────────────────────────────────


def test_resume_attempt(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.mark_blocked(ctx)
    resumed = ctrl.resume_attempt(ctx.step_attempt_id)
    assert resumed.task_id == ctx.task_id


def test_resume_attempt_unknown(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    with pytest.raises(KeyError):
        ctrl.resume_attempt("nonexistent")


def test_resume_attempt_with_recovery_required(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    attempt = shared_store.get_step_attempt(ctx.step_attempt_id)
    context = dict(attempt.context or {})
    context["recovery_required"] = True
    context["reentry_reason"] = "worker_interrupted"
    shared_store.update_step_attempt(ctx.step_attempt_id, context=context)
    resumed = ctrl.resume_attempt(ctx.step_attempt_id)
    assert resumed.task_id == ctx.task_id


# ── enqueue_resume ───────────────────────────────────────────────


def test_enqueue_resume_normal(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.mark_blocked(ctx)
    resumed = ctrl.enqueue_resume(ctx.step_attempt_id)
    assert resumed.task_id == ctx.task_id
    task = shared_store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "queued"


def test_enqueue_resume_unknown(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    with pytest.raises(KeyError):
        ctrl.enqueue_resume("nonexistent")


def test_enqueue_resume_input_dirty_approval(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    attempt = shared_store.get_step_attempt(ctx.step_attempt_id)
    context = dict(attempt.context or {})
    context["input_dirty"] = True
    shared_store.update_step_attempt(
        ctx.step_attempt_id, context=context, status_reason="awaiting_approval"
    )
    resumed = ctrl.enqueue_resume(ctx.step_attempt_id)
    assert resumed.step_attempt_id != ctx.step_attempt_id
    assert resumed.task_id == ctx.task_id


# ── resolve_text_command ─────────────────────────────────────────


def test_resolve_text_command_returns_none_for_normal(
    shared_store: KernelStore, conv_id: str
) -> None:
    ctrl = _ctrl(shared_store)
    result = ctrl.resolve_text_command(conv_id, "hello there")
    assert result is None


# ── append_note ──────────────────────────────────────────────────


def test_append_note(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    seq = ctrl.append_note(
        task_id=ctx.task_id,
        source_channel="chat",
        raw_text="Additional context",
        prompt="Additional context",
    )
    assert isinstance(seq, int)


def test_append_note_unknown_task(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    with pytest.raises(KeyError):
        ctrl.append_note(
            task_id="nonexistent", source_channel="chat", raw_text="text", prompt="prompt"
        )


# ── update_attempt_phase ─────────────────────────────────────────


def test_update_attempt_phase(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.update_attempt_phase(ctx.step_attempt_id, phase="executing")
    attempt = shared_store.get_step_attempt(ctx.step_attempt_id)
    assert attempt.context.get("phase") == "executing"


def test_update_attempt_phase_noop_same(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl.update_attempt_phase(ctx.step_attempt_id, phase="planning")
    ctrl.update_attempt_phase(ctx.step_attempt_id, phase="planning")


def test_update_attempt_phase_unknown(shared_store: KernelStore) -> None:
    ctrl = _ctrl(shared_store)
    ctrl.update_attempt_phase("nonexistent", phase="test")


# ── _ingress_queue_priority ──────────────────────────────────────


class TestIngressQueuePriority:
    def test_approval_resume(self) -> None:
        p = TaskController._ingress_queue_priority(
            source_channel="chat", requested_by=None, metadata={"resume_kind": "approval"}
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


def test_try_upgrade_to_steering_noop(shared_store: KernelStore, conv_id: str) -> None:
    ctrl = _ctrl(shared_store)
    ctx = _start_task(ctrl, conv_id)
    ctrl._try_upgrade_to_steering(
        task_id=ctx.task_id, raw_text="normal message", source_channel="chat"
    )


# ── AUTO_PARENT alias ────────────────────────────────────────────


def test_auto_parent_alias() -> None:
    assert AUTO_PARENT is _AUTO_PARENT
