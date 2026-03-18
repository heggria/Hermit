"""Tests for trigger plugin hooks — SERVE_START / POST_RUN lifecycle."""

from __future__ import annotations

from unittest.mock import MagicMock

from hermit.plugins.builtin.hooks.trigger import hooks as hooks_mod
from hermit.plugins.builtin.hooks.trigger.hooks import (
    _on_post_run,
    _on_serve_start,
    register,
)
from hermit.runtime.capability.contracts.base import HookEvent


class TestOnServeStart:
    """Cover trigger/hooks.py lines 15-18."""

    def setup_method(self) -> None:
        hooks_mod._engine = None

    def test_attaches_runner_when_engine_exists(self) -> None:
        engine = MagicMock()
        hooks_mod._engine = engine
        runner = MagicMock()
        _on_serve_start(runner=runner)
        engine.set_runner.assert_called_once_with(runner)

    def test_noop_when_engine_is_none(self) -> None:
        _on_serve_start(runner=MagicMock())

    def test_noop_when_runner_is_none(self) -> None:
        engine = MagicMock()
        hooks_mod._engine = engine
        _on_serve_start(runner=None)
        engine.set_runner.assert_not_called()


class TestOnPostRun:
    """Cover trigger/hooks.py lines 21-24."""

    def setup_method(self) -> None:
        hooks_mod._engine = None

    def test_noop_when_engine_is_none(self) -> None:
        _on_post_run("some result", session_id="s1")

    def test_dispatches_to_engine(self) -> None:
        engine = MagicMock()
        hooks_mod._engine = engine
        _on_post_run("FAILED test_foo", session_id="s1")
        engine.analyze_and_dispatch.assert_called_once_with("FAILED test_foo", session_id="s1")


class TestRegister:
    """Cover trigger/hooks.py lines 27-37."""

    def setup_method(self) -> None:
        hooks_mod._engine = None

    def test_register_enabled(self) -> None:
        ctx = MagicMock()
        ctx.get_var.side_effect = lambda key, default: default
        register(ctx)
        assert hooks_mod._engine is not None
        assert ctx.add_hook.call_count == 2
        events = {call.args[0] for call in ctx.add_hook.call_args_list}
        assert HookEvent.SERVE_START in events
        assert HookEvent.POST_RUN in events

    def test_register_disabled(self) -> None:
        ctx = MagicMock()
        ctx.get_var.side_effect = lambda key, default: (
            False if key == "trigger_enabled" else default
        )
        register(ctx)
        ctx.add_hook.assert_not_called()

    def teardown_method(self) -> None:
        hooks_mod._engine = None
