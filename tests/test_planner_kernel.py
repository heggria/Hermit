from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermit.builtin.planner.commands import _cmd_plan
from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.controller import TaskController
from hermit.kernel.planning import PlanningService
from hermit.kernel.projections import ProjectionService
from hermit.kernel.store import KernelStore


@pytest.fixture(autouse=True)
def _force_planner_locale(monkeypatch):
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")


def test_plan_command_routes_to_kernel_control_actions() -> None:
    calls: list[tuple[str, str]] = []
    runner = SimpleNamespace(
        dispatch_control_action=lambda session_id, *, action, target_id: (
            calls.append((action, target_id))
            or SimpleNamespace(
                text=action,
                is_command=True,
            )
        )
    )

    assert _cmd_plan(runner, "chat-1", "/plan").text == "plan_enter"
    assert _cmd_plan(runner, "chat-1", "/plan confirm").text == "plan_confirm"
    assert _cmd_plan(runner, "chat-1", "/plan off").text == "plan_exit"
    assert calls == [
        ("plan_enter", ""),
        ("plan_confirm", ""),
        ("plan_exit", ""),
    ]


def test_planning_service_persists_plan_artifact_and_events(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-1",
        goal="Plan the migration",
        source_channel="chat",
        kind="plan",
        policy_profile="readonly",
    )
    planning = PlanningService(store, artifacts)

    planning.enter_planning(ctx.task_id)
    artifact_ref = planning.capture_plan_result(ctx, plan_text="## Plan\n\n1. Read\n2. Execute")

    state = planning.state_for_task(ctx.task_id)
    events = [event["event_type"] for event in store.list_events(task_id=ctx.task_id, limit=50)]

    assert artifact_ref is not None
    assert state.planning_mode is True
    assert state.selected_plan_ref == artifact_ref
    assert state.plan_status == "selected"
    assert "planning.entered" in events
    assert "plan.artifact_created" in events
    assert "plan.selected" in events


def test_confirmed_plan_records_decision_and_projection(tmp_path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-2",
        goal="Plan a rollout",
        source_channel="chat",
        kind="plan",
        policy_profile="readonly",
    )
    planning = PlanningService(store, artifacts)

    planning.enter_planning(ctx.task_id)
    artifact_ref = planning.capture_plan_result(ctx, plan_text="## Plan\n\nShip it")
    state, decision_id = planning.confirm_selected_plan(ctx)

    payload = ProjectionService(store).rebuild_task(ctx.task_id)

    assert artifact_ref is not None
    assert decision_id is not None
    assert state.planning_mode is False
    assert state.plan_status == "executing"
    assert payload["selected_plan_ref"] == artifact_ref
    assert payload["planning"]["latest_planning_decision_id"] == decision_id
    assert payload["latest_planning_decision_id"] == decision_id
    assert artifact_ref in payload["latest_plan_artifact_refs"]
