"""Unit tests for hermit.plugins.builtin.bundles.planner.commands."""

from __future__ import annotations

from unittest.mock import MagicMock

from hermit.plugins.builtin.bundles.planner import commands as planner
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner() -> MagicMock:
    """Return a minimal runner mock with a dispatch_control_action spy."""
    runner = MagicMock()
    return runner


# ---------------------------------------------------------------------------
# _cmd_plan routing
# ---------------------------------------------------------------------------


class TestCmdPlanRouting:
    """_cmd_plan dispatches the correct action based on the subcommand."""

    def test_enter_on_bare_plan(self) -> None:
        runner = _make_runner()
        planner._cmd_plan(runner, "session-1", "/plan")
        runner.dispatch_control_action.assert_called_once_with(
            "session-1", action="plan_enter", target_id=""
        )

    def test_enter_on_unknown_subcommand(self) -> None:
        runner = _make_runner()
        planner._cmd_plan(runner, "session-1", "/plan unknown")
        runner.dispatch_control_action.assert_called_once_with(
            "session-1", action="plan_enter", target_id=""
        )

    def test_confirm_subcommand(self) -> None:
        runner = _make_runner()
        planner._cmd_plan(runner, "session-1", "/plan confirm")
        runner.dispatch_control_action.assert_called_once_with(
            "session-1", action="plan_confirm", target_id=""
        )

    def test_off_subcommand(self) -> None:
        runner = _make_runner()
        planner._cmd_plan(runner, "session-1", "/plan off")
        runner.dispatch_control_action.assert_called_once_with(
            "session-1", action="plan_exit", target_id=""
        )

    def test_confirm_is_case_insensitive(self) -> None:
        runner = _make_runner()
        planner._cmd_plan(runner, "session-1", "/plan CONFIRM")
        runner.dispatch_control_action.assert_called_once_with(
            "session-1", action="plan_confirm", target_id=""
        )

    def test_off_is_case_insensitive(self) -> None:
        runner = _make_runner()
        planner._cmd_plan(runner, "session-1", "/plan OFF")
        runner.dispatch_control_action.assert_called_once_with(
            "session-1", action="plan_exit", target_id=""
        )

    def test_extra_whitespace_is_stripped(self) -> None:
        runner = _make_runner()
        planner._cmd_plan(runner, "session-1", "  /plan  confirm  ")
        runner.dispatch_control_action.assert_called_once_with(
            "session-1", action="plan_confirm", target_id=""
        )

    def test_returns_dispatch_result(self) -> None:
        runner = _make_runner()
        runner.dispatch_control_action.return_value = "ok"
        result = planner._cmd_plan(runner, "session-1", "/plan")
        assert result == "ok"


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


class TestRegister:
    def test_register_adds_exactly_one_command(self) -> None:
        ctx = PluginContext(HooksEngine())
        planner.register(ctx)
        assert len(ctx.commands) == 1

    def test_registered_command_name(self) -> None:
        ctx = PluginContext(HooksEngine())
        planner.register(ctx)
        assert ctx.commands[0].name == "/plan"

    def test_registered_handler_is_cmd_plan(self) -> None:
        ctx = PluginContext(HooksEngine())
        planner.register(ctx)
        assert ctx.commands[0].handler is planner._cmd_plan

    def test_register_does_not_add_hooks(self) -> None:
        ctx = PluginContext(HooksEngine())
        planner.register(ctx)
        assert ctx._hooks.has_handlers("pre_run") is False
        assert ctx._hooks.has_handlers("post_run") is False

    def test_registered_command_routes_correctly_via_ctx(self) -> None:
        """End-to-end: command registered through ctx and invoked."""
        ctx = PluginContext(HooksEngine())
        planner.register(ctx)
        runner = _make_runner()

        ctx.commands[0].handler(runner, "session-42", "/plan off")

        runner.dispatch_control_action.assert_called_once_with(
            "session-42", action="plan_exit", target_id=""
        )
