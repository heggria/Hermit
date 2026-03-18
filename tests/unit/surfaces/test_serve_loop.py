"""Tests for _serve.py — covers the _serve_loop and related helper paths."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermit.surfaces.cli._serve import (
    _serve_loop,
    _ServeRunResult,
)

# The _serve_loop function imports build_runner lazily:
#   from ._commands_core import build_runner
# We need to patch it at the source module.
_BUILD_RUNNER_PATCH = "hermit.surfaces.cli._commands_core.build_runner"

# ------------------------------------------------------------------
# _serve_loop — first cycle (is_first_cycle=True), adapter stops normally
# ------------------------------------------------------------------


class TestServeLoopFirstCycle:
    """Cover lines 268-269, 272, 292, 300-302 (first cycle, no prev_runner)."""

    def test_first_cycle_runs_and_stops(self, tmp_path: Path) -> None:
        fake_settings = SimpleNamespace(
            base_dir=tmp_path,
            log_level="WARNING",
            plugins_dir=tmp_path / "plugins",
        )
        pid_file = tmp_path / "serve-test.pid"

        mock_pm = MagicMock()
        mock_adapter = MagicMock()
        mock_pm.get_adapter.return_value = mock_adapter
        mock_adapter.required_skills = []

        mock_runner = MagicMock()

        normal_stop = _ServeRunResult(
            reload_requested=False,
            reason="adapter_stopped",
            detail="Adapter returned control.",
        )

        with (
            patch("hermit.surfaces.cli._serve.get_settings", return_value=fake_settings),
            patch("hermit.surfaces.cli._serve.configure_logging"),
            patch("hermit.surfaces.cli._serve.iso_now", return_value="2026-01-01T00:00:00Z"),
            patch("hermit.surfaces.cli._serve.PluginManager", return_value=mock_pm),
            patch(_BUILD_RUNNER_PATCH, return_value=(mock_runner, None)),
            patch("hermit.surfaces.cli._serve.asyncio.run", return_value=normal_stop),
            patch("hermit.surfaces.cli._serve.caffeinate") as mock_caff,
            patch("hermit.surfaces.cli._serve.write_serve_status"),
            patch("hermit.surfaces.cli._serve.stop_runner_background_services") as mock_stop_bg,
        ):
            mock_caff.return_value.__enter__ = MagicMock()
            mock_caff.return_value.__exit__ = MagicMock(return_value=False)

            _serve_loop("test", pid_file)

        # Full shutdown path: SERVE_STOP with reload_mode=False
        mock_pm.hooks.fire.assert_any_call(
            mock_pm.hooks.fire.call_args_list[0][0][0],  # HookEvent.SERVE_START
            runner=mock_runner,
            settings=fake_settings,
            reload_mode=False,  # first cycle
        )
        mock_stop_bg.assert_called_once_with(mock_runner)
        mock_pm.stop_mcp_servers.assert_called_once()


# ------------------------------------------------------------------
# _serve_loop — reload cycle (is_first_cycle transitions to False)
# ------------------------------------------------------------------


class TestServeLoopReload:
    """Cover lines 339, 341-343, 346-348, 351 (reload then stop)."""

    def test_reload_then_stop(self, tmp_path: Path) -> None:
        fake_settings = SimpleNamespace(
            base_dir=tmp_path,
            log_level="WARNING",
            plugins_dir=tmp_path / "plugins",
            scheduler_feishu_chat_id="",
        )
        pid_file = tmp_path / "serve-test.pid"

        mock_pm = MagicMock()
        mock_adapter = MagicMock()
        mock_pm.get_adapter.return_value = mock_adapter
        mock_adapter.required_skills = []

        mock_runner_1 = MagicMock()
        mock_runner_2 = MagicMock()

        reload_result = _ServeRunResult(
            reload_requested=True,
            reason="signal",
            detail="SIGHUP received.",
            signal_name="SIGHUP",
        )
        stop_result = _ServeRunResult(
            reload_requested=False,
            reason="adapter_stopped",
            detail="Adapter returned control.",
        )

        call_count = 0

        def build_runner_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_runner_1, None)
            return (mock_runner_2, None)

        run_results = iter([reload_result, stop_result])

        with (
            patch("hermit.surfaces.cli._serve.get_settings") as mock_get_settings,
            patch("hermit.surfaces.cli._serve.configure_logging"),
            patch("hermit.surfaces.cli._serve.iso_now", return_value="2026-01-01T00:00:00Z"),
            patch("hermit.surfaces.cli._serve.PluginManager", return_value=mock_pm),
            patch(_BUILD_RUNNER_PATCH, side_effect=build_runner_side_effect),
            patch(
                "hermit.surfaces.cli._serve.asyncio.run",
                side_effect=lambda coro: next(run_results),
            ),
            patch("hermit.surfaces.cli._serve.caffeinate") as mock_caff,
            patch("hermit.surfaces.cli._serve.write_serve_status"),
            patch("hermit.surfaces.cli._serve.stop_runner_background_services") as mock_stop_bg,
            patch("hermit.surfaces.cli._serve._write_pid"),
            patch("hermit.surfaces.cli._serve._notify_reload"),
        ):
            mock_get_settings.return_value = fake_settings
            mock_get_settings.cache_clear = MagicMock()
            mock_caff.return_value.__enter__ = MagicMock()
            mock_caff.return_value.__exit__ = MagicMock(return_value=False)

            _serve_loop("test", pid_file)

        # After reload, prev_runner should be stopped
        mock_stop_bg.assert_any_call(mock_runner_1)
        # Final stop should stop runner_2
        mock_stop_bg.assert_any_call(mock_runner_2)
        assert mock_stop_bg.call_count == 2


# ------------------------------------------------------------------
# _serve_loop — prev_runner cleanup on second cycle (lines 300-302)
# ------------------------------------------------------------------


class TestServeLoopPrevRunnerCleanup:
    """Ensure that when reload happens, prev_runner bg services are stopped on next cycle."""

    def test_prev_runner_stopped_on_reload_cycle(self, tmp_path: Path) -> None:
        fake_settings = SimpleNamespace(
            base_dir=tmp_path,
            log_level="WARNING",
            plugins_dir=tmp_path / "plugins",
            scheduler_feishu_chat_id="",
        )
        pid_file = tmp_path / "serve-test.pid"

        mock_pm = MagicMock()
        mock_adapter = MagicMock()
        mock_pm.get_adapter.return_value = mock_adapter
        mock_adapter.required_skills = []

        runners = [MagicMock(), MagicMock()]
        runner_idx = 0

        def build_runner_fn(*a, **kw):
            nonlocal runner_idx
            r = runners[runner_idx]
            runner_idx += 1
            return (r, None)

        results = iter(
            [
                _ServeRunResult(reload_requested=True, reason="signal", detail="reload"),
                _ServeRunResult(reload_requested=False, reason="done", detail="done"),
            ]
        )

        with (
            patch("hermit.surfaces.cli._serve.get_settings") as mock_gs,
            patch("hermit.surfaces.cli._serve.configure_logging"),
            patch("hermit.surfaces.cli._serve.iso_now", return_value="now"),
            patch("hermit.surfaces.cli._serve.PluginManager", return_value=mock_pm),
            patch(_BUILD_RUNNER_PATCH, side_effect=build_runner_fn),
            patch(
                "hermit.surfaces.cli._serve.asyncio.run",
                side_effect=lambda c: next(results),
            ),
            patch("hermit.surfaces.cli._serve.caffeinate") as mc,
            patch("hermit.surfaces.cli._serve.write_serve_status"),
            patch(
                "hermit.surfaces.cli._serve.stop_runner_background_services",
            ) as mock_stop_bg,
            patch("hermit.surfaces.cli._serve._write_pid"),
            patch("hermit.surfaces.cli._serve._notify_reload"),
        ):
            mock_gs.return_value = fake_settings
            mock_gs.cache_clear = MagicMock()
            mc.return_value.__enter__ = MagicMock()
            mc.return_value.__exit__ = MagicMock(return_value=False)

            _serve_loop("test", pid_file)

        # prev_runner (runners[0]) should have been stopped during second cycle
        mock_stop_bg.assert_any_call(runners[0])
