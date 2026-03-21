"""Tests for PlanningService — cover uncovered methods."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.planning import PlanningService, PlanningState


def _svc(store: KernelStore, **kwargs: Any) -> PlanningService:
    return PlanningService(store, **kwargs)


def _mk_task(store: KernelStore, conv_id: str, **kwargs: Any) -> Any:
    defaults = {
        "conversation_id": conv_id,
        "title": "Test Task",
        "goal": "Cover gaps",
        "source_channel": "chat",
    }
    defaults.update(kwargs)
    return store.create_task(**defaults)


def _mk_ctx(store: KernelStore, conv_id: str, task_id: str) -> TaskExecutionContext:
    step = store.create_step(task_id=task_id, kind="plan", status="running")
    attempt = store.create_step_attempt(task_id=task_id, step_id=step.step_id, status="running")
    return TaskExecutionContext(
        conversation_id=conv_id,
        task_id=task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        source_channel="chat",
        policy_profile="default",
    )


# ── PlanningState ───────────────────────────────────────────────


def test_planning_state_to_dict() -> None:
    state = PlanningState(
        planning_mode=True,
        candidate_plan_refs=["ref-1"],
        selected_plan_ref="ref-1",
        plan_status="drafted",
    )
    d = state.to_dict()
    assert d["planning_mode"] is True
    assert d["candidate_plan_refs"] == ["ref-1"]
    assert d["selected_plan_ref"] == "ref-1"
    assert d["plan_status"] == "drafted"


# ── planning_requested ──────────────────────────────────────────


def test_planning_requested_true() -> None:
    assert PlanningService.planning_requested("please plan first then implement") is True


def test_planning_requested_false() -> None:
    assert PlanningService.planning_requested("just do it") is False


# ── pending_for_conversation ────────────────────────────────────


def test_pending_for_conversation_default_false(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    assert svc.pending_for_conversation(conv_id) is False


def test_set_pending_for_conversation(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    svc.set_pending_for_conversation(conv_id, enabled=True)
    assert svc.pending_for_conversation(conv_id) is True
    svc.set_pending_for_conversation(conv_id, enabled=False)
    assert svc.pending_for_conversation(conv_id) is False


# ── state_for_task ──────────────────────────────────────────────


def test_state_for_task_empty(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    state = svc.state_for_task(task.task_id)
    assert state.planning_mode is False
    assert state.plan_status == "none"


# ── enter_planning / exit_planning ──────────────────────────────


def test_enter_planning(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    state = svc.enter_planning(task.task_id)
    assert state.planning_mode is True


def test_enter_planning_idempotent(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    svc.enter_planning(task.task_id)
    state2 = svc.enter_planning(task.task_id)  # already in planning
    assert state2.planning_mode is True


def test_exit_planning(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    svc.enter_planning(task.task_id)
    state = svc.exit_planning(task.task_id)
    assert state.planning_mode is False


# ── confirm_selected_plan ───────────────────────────────────────


def test_confirm_selected_plan_no_selection(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    ctx = _mk_ctx(shared_store, conv_id, task.task_id)
    state, decision_id = svc.confirm_selected_plan(ctx)
    assert decision_id is None
    assert state.plan_status == "none"


# ── load_selected_plan_text ─────────────────────────────────────


def test_load_selected_plan_text_no_artifact_store(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    assert svc.load_selected_plan_text(task.task_id) is None


def test_load_selected_plan_text_no_selection(shared_store: KernelStore, conv_id: str) -> None:
    fake_artifact_store = SimpleNamespace(read_text=lambda uri: "plan text")
    svc = _svc(shared_store, artifact_store=fake_artifact_store)
    task = _mk_task(shared_store, conv_id)
    assert svc.load_selected_plan_text(task.task_id) is None


def test_load_selected_plan_text_with_artifact(shared_store: KernelStore, conv_id: str) -> None:
    fake_artifact_store = SimpleNamespace(
        read_text=lambda uri: "# My Plan\n\nStep 1: Do things",
        store_text=lambda text, extension="md": ("/tmp/plan.md", "hash1"),
    )
    svc = _svc(shared_store, artifact_store=fake_artifact_store)
    task = _mk_task(shared_store, conv_id)
    ctx = _mk_ctx(shared_store, conv_id, task.task_id)
    # Capture a plan to create the artifact and selection events
    artifact_id = svc.capture_plan_result(ctx, plan_text="# My Plan\n\nStep 1: Do things")
    assert artifact_id is not None
    # Now load_selected_plan_text should find it
    text = svc.load_selected_plan_text(task.task_id)
    assert text == "# My Plan\n\nStep 1: Do things"


def test_load_selected_plan_text_artifact_not_found(
    shared_store: KernelStore, conv_id: str
) -> None:
    fake_artifact_store = SimpleNamespace(
        read_text=lambda uri: "text",
        store_text=lambda text, extension="md": ("/tmp/plan.md", "hash1"),
    )
    svc = _svc(shared_store, artifact_store=fake_artifact_store)
    task = _mk_task(shared_store, conv_id)
    # Manually set a selected_plan_ref via events without a real artifact
    shared_store.append_event(
        event_type="plan.selected",
        entity_type="task",
        entity_id=task.task_id,
        task_id=task.task_id,
        actor="test",
        payload={"artifact_ref": "nonexistent_ref", "plan_status": "selected"},
    )
    result = svc.load_selected_plan_text(task.task_id)
    assert result is None


def test_load_selected_plan_text_os_error(shared_store: KernelStore, conv_id: str) -> None:
    def raise_os_error(uri: str) -> str:
        raise OSError("file not found")

    fake_artifact_store = SimpleNamespace(
        read_text=raise_os_error,
        store_text=lambda text, extension="md": ("/tmp/plan.md", "hash1"),
    )
    svc = _svc(shared_store, artifact_store=fake_artifact_store)
    task = _mk_task(shared_store, conv_id)
    ctx = _mk_ctx(shared_store, conv_id, task.task_id)
    artifact_id = svc.capture_plan_result(ctx, plan_text="plan text")
    assert artifact_id is not None
    result = svc.load_selected_plan_text(task.task_id)
    assert result is None


# ── latest_plan_artifact_refs ───────────────────────────────────


def test_latest_plan_artifact_refs_empty(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    assert svc.latest_plan_artifact_refs(task.task_id) == []


def test_latest_plan_artifact_refs_returns_plan_kinds(
    shared_store: KernelStore, conv_id: str
) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    step = shared_store.create_step(task_id=task.task_id, kind="plan", status="running")
    shared_store.create_artifact(
        task_id=task.task_id,
        step_id=step.step_id,
        kind="plan",
        uri="/tmp/plan.md",
        content_hash="hash1",
        producer="test",
    )
    shared_store.create_artifact(
        task_id=task.task_id,
        step_id=step.step_id,
        kind="output",  # not a plan
        uri="/tmp/output.md",
        content_hash="hash2",
        producer="test",
    )
    refs = svc.latest_plan_artifact_refs(task.task_id)
    assert len(refs) == 1


# ── latest_planning_attempt ─────────────────────────────────────


def test_latest_planning_attempt_none(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    assert svc.latest_planning_attempt(task.task_id) is None


def test_latest_planning_attempt_found(shared_store: KernelStore, conv_id: str) -> None:
    svc = _svc(shared_store)
    task = _mk_task(shared_store, conv_id)
    ctx = _mk_ctx(shared_store, conv_id, task.task_id)
    result = svc.latest_planning_attempt(task.task_id)
    assert result is not None
    assert result.task_id == task.task_id
    assert result.step_id == ctx.step_id
