"""Tests for hermit.runtime.control.runner.session_context_builder."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.runtime.control.runner.session_context_builder import SessionContextBuilder
from hermit.runtime.provider_host.execution.runtime import AgentResult

# ---------------------------------------------------------------------------
# Fake fixtures
# ---------------------------------------------------------------------------


def _make_task_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults = dict(
        conversation_id="conv_1",
        task_id="task_1",
        step_id="step_1",
        step_attempt_id="attempt_1",
        source_channel="test",
        policy_profile="default",
        workspace_root="",
        ingress_metadata={},
    )
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


def _make_agent_result(**overrides: Any) -> AgentResult:
    defaults = dict(text="result text", turns=1, tool_calls=0)
    defaults.update(overrides)
    return AgentResult(**defaults)


@pytest.fixture()
def mock_pm() -> MagicMock:
    pm = MagicMock()
    pm.settings = MagicMock()
    pm.settings.max_session_messages = 100
    pm.on_session_start = MagicMock()
    return pm


@pytest.fixture()
def mock_session_manager() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def mock_store() -> MagicMock:
    store = MagicMock()
    return store


@pytest.fixture()
def mock_planning() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def builder(
    mock_session_manager: MagicMock,
    mock_pm: MagicMock,
    mock_store: MagicMock,
    mock_planning: MagicMock,
) -> SessionContextBuilder:
    return SessionContextBuilder(
        session_manager=mock_session_manager,
        pm=mock_pm,
        store=mock_store,
        planning_service=mock_planning,
    )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_stores_dependencies(self, builder: SessionContextBuilder) -> None:
        assert builder.session_manager is not None
        assert builder.pm is not None
        assert builder.store is not None
        assert builder.planning_service is not None

    def test_session_started_set_empty(self, builder: SessionContextBuilder) -> None:
        assert builder._session_started == set()


# ---------------------------------------------------------------------------
# max_session_messages
# ---------------------------------------------------------------------------


class TestMaxSessionMessages:
    def test_returns_settings_value(self, builder: SessionContextBuilder) -> None:
        builder.pm.settings.max_session_messages = 50
        assert builder.max_session_messages() == 50

    def test_default_when_none(self, builder: SessionContextBuilder) -> None:
        builder.pm.settings.max_session_messages = None
        assert builder.max_session_messages() == 100

    def test_default_when_no_settings(self, builder: SessionContextBuilder) -> None:
        builder.pm.settings = None
        assert builder.max_session_messages() == 100


# ---------------------------------------------------------------------------
# ensure_session_started
# ---------------------------------------------------------------------------


class TestEnsureSessionStarted:
    def test_fires_hook_first_time(self, builder: SessionContextBuilder) -> None:
        builder.ensure_session_started("session_1")
        builder.pm.on_session_start.assert_called_once_with("session_1")
        assert "session_1" in builder._session_started

    def test_does_not_fire_hook_second_time(self, builder: SessionContextBuilder) -> None:
        builder.ensure_session_started("session_1")
        builder.ensure_session_started("session_1")
        builder.pm.on_session_start.assert_called_once()

    def test_different_sessions_each_fire(self, builder: SessionContextBuilder) -> None:
        builder.ensure_session_started("session_1")
        builder.ensure_session_started("session_2")
        assert builder.pm.on_session_start.call_count == 2


# ---------------------------------------------------------------------------
# maybe_capture_planning_result
# ---------------------------------------------------------------------------


class TestMaybeCapturePlanningResult:
    def test_returns_false_when_not_readonly(self, builder: SessionContextBuilder) -> None:
        task_ctx = _make_task_ctx()
        result = _make_agent_result()
        assert (
            builder.maybe_capture_planning_result(
                task_ctx, result, readonly_only=False, task_controller=MagicMock()
            )
            is False
        )

    def test_returns_false_when_step_is_none(self, builder: SessionContextBuilder) -> None:
        builder.store.get_step.return_value = None
        task_ctx = _make_task_ctx()
        result = _make_agent_result()
        assert (
            builder.maybe_capture_planning_result(
                task_ctx, result, readonly_only=True, task_controller=MagicMock()
            )
            is False
        )

    def test_returns_false_when_step_kind_not_plan(self, builder: SessionContextBuilder) -> None:
        step = MagicMock()
        step.kind = "respond"
        builder.store.get_step.return_value = step
        task_ctx = _make_task_ctx()
        result = _make_agent_result()
        assert (
            builder.maybe_capture_planning_result(
                task_ctx, result, readonly_only=True, task_controller=MagicMock()
            )
            is False
        )

    def test_captures_plan_when_readonly_and_plan_step(
        self, builder: SessionContextBuilder
    ) -> None:
        step = MagicMock()
        step.kind = "plan"
        builder.store.get_step.return_value = step

        task_ctx = _make_task_ctx()
        result = _make_agent_result(text="My plan: do X then Y")

        controller = MagicMock()
        controller.artifact_store = MagicMock()

        with patch(
            "hermit.runtime.control.runner.session_context_builder.PlanningService"
        ) as MockPS:
            MockPS.return_value.capture_plan_result.return_value = "plan_ref_1"
            captured = builder.maybe_capture_planning_result(
                task_ctx, result, readonly_only=True, task_controller=controller
            )

        assert captured is True
        assert result.execution_status == "planning_ready"
        assert result.status_managed_by_kernel is True

    def test_calls_mark_planning_ready_when_available(self, builder: SessionContextBuilder) -> None:
        step = MagicMock()
        step.kind = "plan"
        builder.store.get_step.return_value = step

        task_ctx = _make_task_ctx()
        result = _make_agent_result(text="Plan text")

        controller = MagicMock()
        controller.mark_planning_ready = MagicMock()

        with patch(
            "hermit.runtime.control.runner.session_context_builder.PlanningService"
        ) as MockPS:
            MockPS.return_value.capture_plan_result.return_value = "plan_ref_1"
            builder.maybe_capture_planning_result(
                task_ctx, result, readonly_only=True, task_controller=controller
            )

        controller.mark_planning_ready.assert_called_once()

    def test_works_without_mark_planning_ready(self, builder: SessionContextBuilder) -> None:
        step = MagicMock()
        step.kind = "plan"
        builder.store.get_step.return_value = step

        task_ctx = _make_task_ctx()
        result = _make_agent_result(text="Plan text")

        controller = MagicMock(spec=[])  # no mark_planning_ready

        with patch(
            "hermit.runtime.control.runner.session_context_builder.PlanningService"
        ) as MockPS:
            MockPS.return_value.capture_plan_result.return_value = "plan_ref_1"
            captured = builder.maybe_capture_planning_result(
                task_ctx, result, readonly_only=True, task_controller=controller
            )

        assert captured is True
