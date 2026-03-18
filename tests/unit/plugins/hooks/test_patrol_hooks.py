"""Tests for patrol hooks — SERVE_START / SERVE_STOP lifecycle."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermit.plugins.builtin.hooks.patrol import hooks as patrol_hooks


class TestOnServeStart:
    def setup_method(self) -> None:
        patrol_hooks._engine = None

    def teardown_method(self) -> None:
        if patrol_hooks._engine is not None:
            patrol_hooks._engine.stop()
            patrol_hooks._engine = None

    def test_disabled_when_patrol_enabled_false(self) -> None:
        """When patrol_enabled is falsy, engine should not be created."""
        settings = SimpleNamespace(patrol_enabled=False)
        patrol_hooks._on_serve_start(settings=settings)
        assert patrol_hooks._engine is None

    def test_starts_engine_when_enabled(self) -> None:
        """When patrol_enabled is true, engine should start."""
        settings = SimpleNamespace(
            patrol_enabled=True,
            patrol_interval_minutes=5,
            patrol_checks="lint",
            workspace_root="/tmp/ws",
        )
        mock_engine = MagicMock()

        with patch(
            "hermit.plugins.builtin.hooks.patrol.hooks.PatrolEngine",
            return_value=mock_engine,
        ) as mock_cls:
            patrol_hooks._on_serve_start(settings=settings, runner=None)

        mock_cls.assert_called_once_with(
            interval_minutes=5,
            enabled_checks="lint",
            workspace_root="/tmp/ws",
        )
        mock_engine.start.assert_called_once()
        # Engine is set but is our mock; reset so teardown doesn't call stop on real engine
        patrol_hooks._engine = None

    def test_sets_runner_when_provided(self) -> None:
        settings = SimpleNamespace(
            patrol_enabled=True,
            patrol_interval_minutes=10,
            patrol_checks="lint,test",
            workspace_root="/ws",
        )
        mock_engine = MagicMock()
        mock_runner = MagicMock()

        with patch(
            "hermit.plugins.builtin.hooks.patrol.hooks.PatrolEngine",
            return_value=mock_engine,
        ):
            patrol_hooks._on_serve_start(settings=settings, runner=mock_runner)

        mock_engine.set_runner.assert_called_once_with(mock_runner)
        patrol_hooks._engine = None

    def test_defaults_when_attrs_missing(self) -> None:
        """Settings without optional attrs should use defaults."""
        settings = SimpleNamespace(patrol_enabled=True)
        mock_engine = MagicMock()

        with patch(
            "hermit.plugins.builtin.hooks.patrol.hooks.PatrolEngine",
            return_value=mock_engine,
        ) as mock_cls:
            patrol_hooks._on_serve_start(settings=settings)

        mock_cls.assert_called_once_with(
            interval_minutes=60,
            enabled_checks="lint,test,todo_scan",
            workspace_root="",
        )
        patrol_hooks._engine = None


class TestOnServeStop:
    def setup_method(self) -> None:
        patrol_hooks._engine = None

    def test_stop_when_engine_running(self) -> None:
        mock_engine = MagicMock()
        patrol_hooks._engine = mock_engine

        patrol_hooks._on_serve_stop()

        mock_engine.stop.assert_called_once()
        assert patrol_hooks._engine is None

    def test_stop_when_no_engine(self) -> None:
        """Should be a no-op when no engine is running."""
        patrol_hooks._on_serve_stop()
        assert patrol_hooks._engine is None


class TestRegister:
    def test_registers_hooks(self) -> None:
        ctx = MagicMock()
        patrol_hooks.register(ctx)
        assert ctx.add_hook.call_count == 2
        hook_events = [call.args[0] for call in ctx.add_hook.call_args_list]
        from hermit.runtime.capability.contracts.base import HookEvent

        assert HookEvent.SERVE_START in hook_events
        assert HookEvent.SERVE_STOP in hook_events
