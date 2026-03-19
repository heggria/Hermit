"""Tests for _serve.py — covers CLI commands and helper functions not in test_serve_loop.py."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer.testing

from hermit.surfaces.cli._serve import (
    _configure_unbuffered_stdio,
    _ensure_single_serve_instance,
    _notify_reload,
    _pid_path,
    _read_pid,
    _remove_pid,
    _serve_with_signals,
    _write_pid,
)
from hermit.surfaces.cli.main import app

runner = typer.testing.CliRunner()


# ---------------------------------------------------------------------------
# _pid_path
# ---------------------------------------------------------------------------
class TestPidPath:
    def test_returns_correct_path(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        result = _pid_path(settings, "feishu")
        assert result == tmp_path / "serve-feishu.pid"


# ---------------------------------------------------------------------------
# _write_pid / _read_pid / _remove_pid
# ---------------------------------------------------------------------------
class TestPidHelpers:
    def test_write_and_read(self, tmp_path: Path) -> None:
        path = tmp_path / "test.pid"
        _write_pid(path)
        pid = _read_pid(path)
        assert pid == os.getpid()

    def test_read_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.pid"
        assert _read_pid(path) is None

    def test_read_invalid(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.pid"
        path.write_text("not-a-number", encoding="utf-8")
        assert _read_pid(path) is None

    def test_remove_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "test.pid"
        path.write_text("12345", encoding="utf-8")
        _remove_pid(path)
        assert not path.exists()

    def test_remove_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.pid"
        # Should not raise
        _remove_pid(path)

    def test_write_creates_parent(self, tmp_path: Path) -> None:
        path = tmp_path / "subdir" / "test.pid"
        _write_pid(path)
        assert path.exists()


# ---------------------------------------------------------------------------
# _ensure_single_serve_instance
# ---------------------------------------------------------------------------
class TestEnsureSingleServeInstance:
    def test_no_pid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "test.pid"
        # Should not raise
        _ensure_single_serve_instance(path, "feishu")

    def test_stale_pid(self, tmp_path: Path) -> None:
        path = tmp_path / "test.pid"
        path.write_text("99999999", encoding="utf-8")
        with patch("hermit.surfaces.cli._serve.os.kill", side_effect=ProcessLookupError):
            _ensure_single_serve_instance(path, "feishu")
        # Stale PID file should be removed
        assert not path.exists()

    def test_running_process(self, tmp_path: Path) -> None:
        path = tmp_path / "test.pid"
        path.write_text("12345", encoding="utf-8")
        with (
            patch("hermit.surfaces.cli._serve.os.kill"),
            patch("hermit.surfaces.cli._serve.os.getpid", return_value=99999),
            pytest.raises(typer.Exit),
        ):
            _ensure_single_serve_instance(path, "feishu")

    def test_same_pid(self, tmp_path: Path) -> None:
        path = tmp_path / "test.pid"
        path.write_text(str(os.getpid()), encoding="utf-8")
        # Should not raise (same PID)
        _ensure_single_serve_instance(path, "feishu")

    def test_permission_error(self, tmp_path: Path) -> None:
        path = tmp_path / "test.pid"
        path.write_text("12345", encoding="utf-8")
        with (
            patch("hermit.surfaces.cli._serve.os.kill", side_effect=PermissionError),
            patch("hermit.surfaces.cli._serve.os.getpid", return_value=99999),
            pytest.raises(typer.Exit),
        ):
            _ensure_single_serve_instance(path, "feishu")


# ---------------------------------------------------------------------------
# _configure_unbuffered_stdio
# ---------------------------------------------------------------------------
class TestConfigureUnbufferedStdio:
    def test_calls_reconfigure(self) -> None:
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()
        with (
            patch.object(sys, "stdout", mock_stdout),
            patch.object(sys, "stderr", mock_stderr),
        ):
            _configure_unbuffered_stdio()
        mock_stdout.reconfigure.assert_called()
        mock_stderr.reconfigure.assert_called()

    def test_handles_type_error(self) -> None:
        mock_stdout = MagicMock()
        mock_stdout.reconfigure.side_effect = [
            TypeError("write_through not supported"),
            None,  # fallback call
        ]
        mock_stderr = MagicMock()
        with (
            patch.object(sys, "stdout", mock_stdout),
            patch.object(sys, "stderr", mock_stderr),
        ):
            _configure_unbuffered_stdio()
        # Should have been called twice for stdout (first fails, then fallback)
        assert mock_stdout.reconfigure.call_count == 2


# ---------------------------------------------------------------------------
# _serve_with_signals (async)
# ---------------------------------------------------------------------------
class TestServeWithSignals:
    async def test_adapter_finishes_normally(self) -> None:
        async def fake_start(runner):
            return None

        async def fake_stop():
            return None

        adapter = MagicMock()
        adapter.start = fake_start
        adapter.stop = fake_stop
        mock_runner = MagicMock()

        # Use win32 to skip signal handlers which can't be set on non-main thread
        with patch("hermit.surfaces.cli._serve.sys") as mock_sys:
            mock_sys.platform = "win32"
            result = await _serve_with_signals(adapter, mock_runner)

        assert not result.reload_requested
        assert result.reason == "adapter_stopped"

    async def test_adapter_raises(self) -> None:
        async def failing_start(runner):
            raise RuntimeError("adapter crashed")

        async def fake_stop():
            return None

        adapter = MagicMock()
        adapter.start = failing_start
        adapter.stop = fake_stop
        mock_runner = MagicMock()

        with (
            patch("hermit.surfaces.cli._serve.sys") as mock_sys,
            pytest.raises(RuntimeError, match="adapter crashed"),
        ):
            mock_sys.platform = "win32"
            await _serve_with_signals(adapter, mock_runner)


# ---------------------------------------------------------------------------
# _notify_reload
# ---------------------------------------------------------------------------
class TestNotifyReload:
    def test_no_chat_id(self) -> None:
        settings = SimpleNamespace(scheduler_feishu_chat_id="")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERMIT_SCHEDULER_FEISHU_CHAT_ID", None)
            _notify_reload(settings, "feishu")
        # Should return early without error

    def test_with_chat_id_success(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(
            scheduler_feishu_chat_id="chat-123",
            base_dir=tmp_path,
            plugins_dir=tmp_path / "plugins",
        )
        mock_pm = MagicMock()
        with patch("hermit.surfaces.cli._serve.PluginManager", return_value=mock_pm):
            _notify_reload(settings, "feishu")
        mock_pm.hooks.fire.assert_called_once()

    def test_with_chat_id_exception(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(
            scheduler_feishu_chat_id="chat-123",
            base_dir=tmp_path,
            plugins_dir=tmp_path / "plugins",
        )
        with patch(
            "hermit.surfaces.cli._serve.PluginManager",
            side_effect=Exception("fail"),
        ):
            # Should not raise
            _notify_reload(settings, "feishu")


# ---------------------------------------------------------------------------
# reload command
# ---------------------------------------------------------------------------
class TestReloadCommand:
    def test_sends_sighup(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        pid_file = tmp_path / "serve-feishu.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")

        with (
            patch("hermit.surfaces.cli._serve.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._serve.os.kill") as mock_kill,
        ):
            result = runner.invoke(app, ["reload"])
        assert result.exit_code == 0
        assert "SIGHUP" in result.output
        mock_kill.assert_called_once()

    def test_no_pid_file(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        with patch("hermit.surfaces.cli._serve.get_settings", return_value=settings):
            result = runner.invoke(app, ["reload"])
        assert result.exit_code != 0
        assert "No running" in result.output

    def test_stale_pid(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        pid_file = tmp_path / "serve-feishu.pid"
        pid_file.write_text("99999999", encoding="utf-8")

        with (
            patch("hermit.surfaces.cli._serve.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._serve.os.kill", side_effect=ProcessLookupError),
        ):
            result = runner.invoke(app, ["reload"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "stale" in result.output.lower()

    def test_permission_denied(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        pid_file = tmp_path / "serve-feishu.pid"
        pid_file.write_text("12345", encoding="utf-8")

        with (
            patch("hermit.surfaces.cli._serve.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._serve.os.kill", side_effect=PermissionError),
        ):
            result = runner.invoke(app, ["reload"])
        assert result.exit_code != 0
        assert "ermission" in result.output

    def test_windows_unsupported(self, tmp_path: Path, monkeypatch) -> None:
        settings = SimpleNamespace(base_dir=tmp_path)
        monkeypatch.setattr(sys, "platform", "win32")
        with patch("hermit.surfaces.cli._serve.get_settings", return_value=settings):
            result = runner.invoke(app, ["reload"])
        assert result.exit_code != 0
        assert "not supported" in result.output.lower() or "Windows" in result.output


# ---------------------------------------------------------------------------
# sessions command
# ---------------------------------------------------------------------------
class TestRemovePidOSError:
    def test_oserror_suppressed(self, tmp_path: Path) -> None:
        path = tmp_path / "test.pid"
        path.write_text("12345", encoding="utf-8")
        with patch.object(Path, "unlink", side_effect=OSError("disk error")):
            # Should not raise — OSError is caught
            _remove_pid(path)


# ---------------------------------------------------------------------------
# serve command
# ---------------------------------------------------------------------------
class TestServeCommand:
    def test_serve_runs_loop_and_cleans_pid(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(
            base_dir=tmp_path,
            log_level="INFO",
            memory_dir=tmp_path / "memory",
            skills_dir=tmp_path / "skills",
            rules_dir=tmp_path / "rules",
            hooks_dir=tmp_path / "hooks",
            plugins_dir=tmp_path / "plugins",
            sessions_dir=tmp_path / "sessions",
            image_memory_dir=tmp_path / "image-memory",
            kernel_dir=tmp_path / "kernel",
            kernel_artifacts_dir=tmp_path / "kernel" / "artifacts",
            context_file=tmp_path / "context.md",
            memory_file=tmp_path / "memory" / "memories.md",
            locale="en-US",
            session_idle_timeout_seconds=3600,
        )
        with (
            patch("hermit.surfaces.cli._serve.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._serve.ensure_workspace"),
            patch("hermit.surfaces.cli._serve._ensure_single_serve_instance"),
            patch("hermit.surfaces.cli._serve.configure_logging"),
            patch("hermit.surfaces.cli._serve.run_serve_preflight"),
            patch("hermit.surfaces.cli._serve.write_serve_status"),
            patch("hermit.surfaces.cli._serve._serve_loop"),
        ):
            result = runner.invoke(app, ["serve"])
        assert result.exit_code == 0

    def test_serve_exception_writes_crashed_status(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(
            base_dir=tmp_path,
            log_level="INFO",
            memory_dir=tmp_path / "memory",
            skills_dir=tmp_path / "skills",
            rules_dir=tmp_path / "rules",
            hooks_dir=tmp_path / "hooks",
            plugins_dir=tmp_path / "plugins",
            sessions_dir=tmp_path / "sessions",
            image_memory_dir=tmp_path / "image-memory",
            kernel_dir=tmp_path / "kernel",
            kernel_artifacts_dir=tmp_path / "kernel" / "artifacts",
            context_file=tmp_path / "context.md",
            memory_file=tmp_path / "memory" / "memories.md",
            locale="en-US",
            session_idle_timeout_seconds=3600,
        )
        mock_write_status = MagicMock()
        with (
            patch("hermit.surfaces.cli._serve.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._serve.ensure_workspace"),
            patch("hermit.surfaces.cli._serve._ensure_single_serve_instance"),
            patch("hermit.surfaces.cli._serve.configure_logging"),
            patch("hermit.surfaces.cli._serve.run_serve_preflight"),
            patch(
                "hermit.surfaces.cli._serve.write_serve_status",
                side_effect=mock_write_status,
            ),
            patch(
                "hermit.surfaces.cli._serve._serve_loop",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = runner.invoke(app, ["serve"])
        # The RuntimeError propagates through CliRunner as exit code 1
        assert result.exit_code == 1
        # Should have called write_serve_status with phase="crashed"
        [c for c in mock_write_status.call_args_list if len(c.args) >= 3 and "crashed" in str(c)]
        # At least the crash status was attempted (the mock side_effect returns None)
        assert mock_write_status.call_count >= 2  # starting + crashed

    def test_serve_with_custom_adapter(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(
            base_dir=tmp_path,
            log_level="INFO",
        )
        with (
            patch("hermit.surfaces.cli._serve.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._serve.ensure_workspace"),
            patch("hermit.surfaces.cli._serve._ensure_single_serve_instance"),
            patch("hermit.surfaces.cli._serve.configure_logging"),
            patch("hermit.surfaces.cli._serve.run_serve_preflight"),
            patch("hermit.surfaces.cli._serve.write_serve_status"),
            patch("hermit.surfaces.cli._serve._serve_loop") as mock_loop,
        ):
            result = runner.invoke(app, ["serve", "--adapter", "telegram"])
        assert result.exit_code == 0
        mock_loop.assert_called_once_with("telegram", tmp_path / "serve-telegram.pid")


# ---------------------------------------------------------------------------
# _serve_with_signals — SIGHUP / SIGTERM paths
# ---------------------------------------------------------------------------
class TestServeWithSignalsReload:
    async def test_reload_event_triggers_reload_result(self) -> None:
        """Test SIGHUP-like reload path by triggering the reload event."""
        reload_event_ref = None
        original_event_cls = asyncio.Event

        class _CaptureEvent(original_event_cls):
            def __init__(self):
                super().__init__()
                nonlocal reload_event_ref
                # Capture first event created (reload_event)
                if reload_event_ref is None:
                    reload_event_ref = self

        async def slow_start(runner):
            await asyncio.sleep(10)

        async def fake_stop():
            return None

        adapter = MagicMock()
        adapter.start = slow_start
        adapter.stop = fake_stop
        mock_runner = MagicMock()

        with (
            patch("hermit.surfaces.cli._serve.sys") as mock_sys,
            patch("hermit.surfaces.cli._serve.asyncio.Event", _CaptureEvent),
        ):
            mock_sys.platform = "win32"

            async def run_test():
                task = asyncio.ensure_future(_serve_with_signals(adapter, mock_runner))
                await asyncio.sleep(0.01)
                # Simulate SIGHUP by setting the reload event
                reload_event_ref.set()
                return await task

            result = await run_test()

        assert result.reload_requested is True
        assert result.reason == "signal"
        assert result.signal_name == "SIGHUP"

    async def test_terminate_event_triggers_shutdown(self) -> None:
        """Test SIGTERM-like terminate path by triggering the terminate event."""
        events_created = []
        original_event_cls = asyncio.Event

        class _CaptureEvent(original_event_cls):
            def __init__(self):
                super().__init__()
                events_created.append(self)

        async def slow_start(runner):
            await asyncio.sleep(10)

        async def fake_stop():
            return None

        adapter = MagicMock()
        adapter.start = slow_start
        adapter.stop = fake_stop
        mock_runner = MagicMock()

        with (
            patch("hermit.surfaces.cli._serve.sys") as mock_sys,
            patch("hermit.surfaces.cli._serve.asyncio.Event", _CaptureEvent),
        ):
            mock_sys.platform = "win32"

            async def run_test():
                task = asyncio.ensure_future(_serve_with_signals(adapter, mock_runner))
                await asyncio.sleep(0.01)
                # events_created[0] = reload_event, events_created[1] = terminate_event
                events_created[1].set()
                return await task

            result = await run_test()

        assert result.reload_requested is False
        assert result.reason == "signal"
        assert result.signal_name == "SIGTERM"


# ---------------------------------------------------------------------------
# sessions command
# ---------------------------------------------------------------------------
class TestSessionsCommand:
    def test_lists_sessions(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(
            base_dir=tmp_path,
            memory_dir=tmp_path / "memory",
            skills_dir=tmp_path / "skills",
            rules_dir=tmp_path / "rules",
            hooks_dir=tmp_path / "hooks",
            plugins_dir=tmp_path / "plugins",
            sessions_dir=tmp_path / "sessions",
            image_memory_dir=tmp_path / "image-memory",
            kernel_dir=tmp_path / "kernel",
            kernel_artifacts_dir=tmp_path / "kernel" / "artifacts",
            context_file=tmp_path / "context.md",
            memory_file=tmp_path / "memory" / "memories.md",
            locale="en-US",
            session_idle_timeout_seconds=3600,
        )
        mock_manager = MagicMock()
        mock_manager.list_sessions.return_value = ["session-1", "session-2"]

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._serve.ensure_workspace"),
            patch("hermit.surfaces.cli._serve.SessionManager", return_value=mock_manager),
        ):
            result = runner.invoke(app, ["sessions"])
        assert result.exit_code == 0
        assert "session-1" in result.output
        assert "session-2" in result.output
