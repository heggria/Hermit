"""Tests for patrol plugin hooks — SERVE_START / SERVE_STOP lifecycle."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermit.plugins.builtin.hooks.patrol import hooks as hooks_mod
from hermit.plugins.builtin.hooks.patrol.hooks import (
    _on_serve_start,
    _on_serve_stop,
    register,
)
from hermit.runtime.capability.contracts.base import HookEvent


class TestOnServeStart:
    def setup_method(self) -> None:
        hooks_mod._engine = None

    def test_patrol_disabled_does_nothing(self) -> None:
        settings = SimpleNamespace(patrol_enabled=False)
        _on_serve_start(settings=settings)
        assert hooks_mod._engine is None

    def test_patrol_disabled_missing_attr(self) -> None:
        settings = SimpleNamespace()
        _on_serve_start(settings=settings)
        assert hooks_mod._engine is None

    @patch("hermit.plugins.builtin.hooks.patrol.hooks.PatrolEngine")
    @patch("hermit.plugins.builtin.hooks.patrol.hooks.set_engine")
    def test_patrol_enabled_creates_engine(
        self, mock_set_engine: MagicMock, mock_engine_cls: MagicMock
    ) -> None:
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine

        settings = SimpleNamespace(
            patrol_enabled=True,
            patrol_interval_minutes=30,
            patrol_checks="lint,test",
            workspace_root="/my/workspace",
        )
        runner = MagicMock()
        _on_serve_start(settings=settings, runner=runner)

        mock_engine_cls.assert_called_once_with(
            interval_minutes=30,
            enabled_checks="lint,test",
            workspace_root="/my/workspace",
        )
        mock_engine.set_runner.assert_called_once_with(runner)
        mock_set_engine.assert_called_once_with(mock_engine)
        mock_engine.start.assert_called_once()

    @patch("hermit.plugins.builtin.hooks.patrol.hooks.PatrolEngine")
    @patch("hermit.plugins.builtin.hooks.patrol.hooks.set_engine")
    def test_patrol_enabled_no_runner(
        self, mock_set_engine: MagicMock, mock_engine_cls: MagicMock
    ) -> None:
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine

        settings = SimpleNamespace(
            patrol_enabled=True,
            patrol_interval_minutes=60,
            patrol_checks="lint,test,todo_scan",
            workspace_root="",
        )
        _on_serve_start(settings=settings, runner=None)
        mock_engine.set_runner.assert_not_called()
        mock_engine.start.assert_called_once()

    @patch("hermit.plugins.builtin.hooks.patrol.hooks.PatrolEngine")
    @patch("hermit.plugins.builtin.hooks.patrol.hooks.set_engine")
    def test_patrol_enabled_defaults(
        self, mock_set_engine: MagicMock, mock_engine_cls: MagicMock
    ) -> None:
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine

        settings = SimpleNamespace(patrol_enabled=True)
        _on_serve_start(settings=settings)

        mock_engine_cls.assert_called_once_with(
            interval_minutes=60,
            enabled_checks="lint,test,todo_scan",
            workspace_root="",
        )


class TestOnServeStop:
    def setup_method(self) -> None:
        hooks_mod._engine = None

    def test_stop_when_no_engine(self) -> None:
        _on_serve_stop()
        assert hooks_mod._engine is None

    @patch("hermit.plugins.builtin.hooks.patrol.hooks.set_engine")
    def test_stop_when_engine_exists(self, mock_set_engine: MagicMock) -> None:
        mock_engine = MagicMock()
        hooks_mod._engine = mock_engine
        _on_serve_stop()
        mock_engine.stop.assert_called_once()
        assert hooks_mod._engine is None


class TestRegister:
    def test_register_hooks(self) -> None:
        ctx = MagicMock()
        register(ctx)
        assert ctx.add_hook.call_count == 2
        events = {call.args[0] for call in ctx.add_hook.call_args_list}
        assert HookEvent.SERVE_START in events
        assert HookEvent.SERVE_STOP in events
