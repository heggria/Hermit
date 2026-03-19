"""Tests for src/hermit/surfaces/cli/_helpers.py"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from hermit.surfaces.cli._helpers import (
    _StreamPrinter,
    _tool_result_preview,
    auth_status_summary,
    caffeinate,
    ensure_workspace,
    format_epoch,
    get_kernel_store,
    on_tool_call,
    print_result,
    require_auth,
    resolved_config_snapshot,
    stop_runner_background_services,
)


# ---------------------------------------------------------------------------
# _tool_result_preview
# ---------------------------------------------------------------------------
class TestToolResultPreview:
    def test_short_string(self) -> None:
        assert _tool_result_preview("hello") == "hello"

    def test_exact_limit(self) -> None:
        text = "x" * 200
        assert _tool_result_preview(text) == text

    def test_over_limit(self) -> None:
        text = "x" * 250
        result = _tool_result_preview(text)
        assert result.endswith("...")
        assert len(result) == 203  # 200 chars + "..."

    def test_custom_limit(self) -> None:
        text = "hello world"
        result = _tool_result_preview(text, limit=5)
        assert result == "hello..."

    def test_non_string_input(self) -> None:
        result = _tool_result_preview(42)
        assert result == "42"

    def test_newlines_replaced(self) -> None:
        result = _tool_result_preview("line1\nline2")
        assert "\n" not in result
        assert result == "line1 line2"


# ---------------------------------------------------------------------------
# on_tool_call
# ---------------------------------------------------------------------------
class TestOnToolCall:
    def test_prints_formatted_output(self, capsys) -> None:
        on_tool_call("read_file", {"path": "/tmp/test.txt"}, "file content")
        captured = capsys.readouterr()
        assert "read_file" in captured.out
        assert "path=" in captured.out
        assert "file content" in captured.out


# ---------------------------------------------------------------------------
# print_result
# ---------------------------------------------------------------------------
class TestPrintResult:
    def test_without_thinking(self, capsys) -> None:
        result = SimpleNamespace(thinking=None, text="Hello world")
        print_result(result)
        captured = capsys.readouterr()
        assert "Hello world" in captured.out
        assert "thinking" not in captured.out

    def test_with_thinking(self, capsys) -> None:
        result = SimpleNamespace(thinking="I am thinking\nabout this", text="Answer")
        print_result(result)
        captured = capsys.readouterr()
        assert "thinking" in captured.out
        assert "I am thinking" in captured.out
        assert "Answer" in captured.out


# ---------------------------------------------------------------------------
# _StreamPrinter
# ---------------------------------------------------------------------------
class TestStreamPrinter:
    def test_text_token(self) -> None:
        printer = _StreamPrinter()
        with patch.object(sys, "stdout", new_callable=lambda: MagicMock()):
            printer.on_token("text", "hello")
            assert not printer._in_thinking

    def test_thinking_token_starts_thinking(self) -> None:
        printer = _StreamPrinter()
        with patch.object(sys, "stdout", new_callable=lambda: MagicMock()):
            printer.on_token("thinking", "hmm")
            assert printer._in_thinking

    def test_thinking_to_text_transition(self) -> None:
        printer = _StreamPrinter()
        with patch.object(sys, "stdout", new_callable=lambda: MagicMock()):
            printer.on_token("thinking", "hmm")
            assert printer._in_thinking
            printer.on_token("text", "answer")
            assert not printer._in_thinking

    def test_finish_while_thinking(self) -> None:
        printer = _StreamPrinter()
        with patch.object(sys, "stdout", new_callable=lambda: MagicMock()):
            printer.on_token("thinking", "hmm")
            printer.finish()
            assert not printer._in_thinking

    def test_finish_without_thinking(self) -> None:
        printer = _StreamPrinter()
        with patch.object(sys, "stdout", new_callable=lambda: MagicMock()):
            printer.finish()
            assert not printer._in_thinking


# ---------------------------------------------------------------------------
# stop_runner_background_services
# ---------------------------------------------------------------------------
class TestStopRunnerBackgroundServices:
    def test_with_callable_stopper(self) -> None:
        runner = MagicMock()
        stop_runner_background_services(runner)
        runner.stop_background_services.assert_called_once()

    def test_without_stopper_attr(self) -> None:
        runner = SimpleNamespace()
        # Should not raise
        stop_runner_background_services(runner)

    def test_with_non_callable_stopper(self) -> None:
        runner = SimpleNamespace(stop_background_services="not_callable")
        # Should not raise
        stop_runner_background_services(runner)


# ---------------------------------------------------------------------------
# auth_status_summary
# ---------------------------------------------------------------------------
class TestAuthStatusSummary:
    def test_claude_with_api_key(self) -> None:
        settings = SimpleNamespace(
            provider="claude",
            claude_api_key="sk-test",
            claude_auth_token=None,
        )
        result = auth_status_summary(settings)
        assert result["provider"] == "claude"
        assert result["ok"] is True
        assert "API_KEY" in result["source"]

    def test_claude_with_auth_token(self) -> None:
        settings = SimpleNamespace(
            provider="claude",
            claude_api_key=None,
            claude_auth_token="token-test",
            claude_base_url="https://proxy.example.com",
        )
        result = auth_status_summary(settings)
        assert result["ok"] is True
        assert "AUTH_TOKEN" in result["source"]
        assert result["base_url"] == "https://proxy.example.com"

    def test_claude_no_auth(self) -> None:
        settings = SimpleNamespace(
            provider="claude",
            claude_api_key=None,
            claude_auth_token=None,
        )
        result = auth_status_summary(settings)
        assert result["ok"] is False
        assert result["source"] is None

    def test_codex_with_api_key(self) -> None:
        settings = SimpleNamespace(
            provider="codex",
            openai_api_key="sk-openai",
        )
        result = auth_status_summary(settings)
        assert result["ok"] is True

    def test_codex_with_resolved_key(self) -> None:
        settings = SimpleNamespace(
            provider="codex",
            openai_api_key=None,
            resolved_openai_api_key="resolved-key",
        )
        result = auth_status_summary(settings)
        assert result["ok"] is True
        assert "auth.json" in result["source"]

    def test_codex_no_auth(self) -> None:
        settings = SimpleNamespace(
            provider="codex",
            openai_api_key=None,
            resolved_openai_api_key=None,
            codex_auth_mode="unknown",
        )
        result = auth_status_summary(settings)
        assert result["ok"] is False

    def test_codex_oauth_ok(self) -> None:
        settings = SimpleNamespace(
            provider="codex-oauth",
            codex_auth_file_exists=True,
            codex_access_token="access",
            codex_refresh_token="refresh",
            codex_auth_mode="oauth",
        )
        result = auth_status_summary(settings)
        assert result["ok"] is True

    def test_codex_oauth_not_ok(self) -> None:
        settings = SimpleNamespace(
            provider="codex-oauth",
            codex_auth_file_exists=True,
            codex_access_token=None,
            codex_refresh_token=None,
            codex_auth_mode="oauth",
        )
        result = auth_status_summary(settings)
        assert result["ok"] is False

    def test_unknown_provider(self) -> None:
        settings = SimpleNamespace(
            provider="other",
            has_auth=True,
        )
        result = auth_status_summary(settings)
        assert result["provider"] == "other"
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# resolved_config_snapshot
# ---------------------------------------------------------------------------
class TestResolvedConfigSnapshot:
    def test_contains_expected_keys(self) -> None:
        settings = SimpleNamespace(
            base_dir=Path("/tmp/hermit"),
            config_file=Path("/tmp/hermit/config.toml"),
            default_profile="default",
            resolved_profile="default",
            provider="claude",
            model="claude-3",
            image_model="claude-3",
            max_tokens=4096,
            max_turns=10,
            tool_output_limit=2000,
            thinking_budget=1000,
            openai_base_url=None,
            claude_base_url=None,
            sandbox_mode="strict",
            log_level="INFO",
            feishu_app_id="",
            feishu_thread_progress=False,
            scheduler_enabled=False,
            scheduler_catch_up=False,
            scheduler_feishu_chat_id="",
            webhook_enabled=False,
            resolved_webhook_host="localhost",
            resolved_webhook_port=8080,
            claude_api_key="test",
            claude_auth_token=None,
        )
        # config_file.exists() is checked, so use a real tmp_path
        settings.config_file = Path("/tmp/nonexistent")
        result = resolved_config_snapshot(settings)
        assert "base_dir" in result
        assert "provider" in result
        assert "auth" in result
        assert "feishu" in result
        assert "scheduler" in result
        assert "webhook" in result


# ---------------------------------------------------------------------------
# ensure_workspace
# ---------------------------------------------------------------------------
class TestEnsureWorkspace:
    def test_creates_directories(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(
            base_dir=tmp_path / "hermit",
            memory_dir=tmp_path / "hermit" / "memory",
            skills_dir=tmp_path / "hermit" / "skills",
            rules_dir=tmp_path / "hermit" / "rules",
            hooks_dir=tmp_path / "hermit" / "hooks",
            plugins_dir=tmp_path / "hermit" / "plugins",
            sessions_dir=tmp_path / "hermit" / "sessions",
            image_memory_dir=tmp_path / "hermit" / "image-memory",
            kernel_dir=tmp_path / "hermit" / "kernel",
            kernel_artifacts_dir=tmp_path / "hermit" / "kernel" / "artifacts",
            context_file=tmp_path / "hermit" / "context.md",
            memory_file=tmp_path / "hermit" / "memory" / "memories.md",
            locale="en-US",
        )
        with (
            patch("hermit.surfaces.cli._helpers.ensure_default_context_file"),
            patch("hermit.plugins.builtin.hooks.memory.engine.MemoryEngine"),
        ):
            ensure_workspace(settings)

        assert settings.base_dir.exists()
        assert settings.memory_dir.exists()
        assert settings.kernel_dir.exists()


# ---------------------------------------------------------------------------
# caffeinate
# ---------------------------------------------------------------------------
class TestCaffeinate:
    def test_no_op_when_disabled(self) -> None:
        settings = SimpleNamespace(prevent_sleep=False)
        with caffeinate(settings):
            pass  # Should not raise

    def test_no_op_on_non_darwin(self) -> None:
        settings = SimpleNamespace(prevent_sleep=True)
        with patch("hermit.surfaces.cli._helpers.sys") as mock_sys:
            mock_sys.platform = "linux"
            with caffeinate(settings):
                pass

    def test_starts_caffeinate_on_darwin(self) -> None:
        settings = SimpleNamespace(prevent_sleep=True)
        mock_proc = MagicMock()
        with (
            patch("hermit.surfaces.cli._helpers.sys") as mock_sys,
            patch("hermit.surfaces.cli._helpers.shutil") as mock_shutil,
            patch("hermit.surfaces.cli._helpers.subprocess") as mock_subprocess,
        ):
            mock_sys.platform = "darwin"
            mock_shutil.which.return_value = "/usr/bin/caffeinate"
            mock_subprocess.Popen.return_value = mock_proc
            mock_subprocess.DEVNULL = subprocess.DEVNULL
            with caffeinate(settings):
                mock_subprocess.Popen.assert_called_once()
            mock_proc.terminate.assert_called_once()
            mock_proc.wait.assert_called_once()


# ---------------------------------------------------------------------------
# require_auth
# ---------------------------------------------------------------------------
class TestRequireAuth:
    def test_no_error_when_auth_ok(self) -> None:
        settings = SimpleNamespace(has_auth=True, provider="claude")
        # Should not raise
        require_auth(settings)

    def test_claude_no_auth(self) -> None:
        settings = SimpleNamespace(has_auth=False, provider="claude")
        with pytest.raises(typer.BadParameter):
            require_auth(settings)

    def test_codex_no_auth_no_file(self) -> None:
        settings = SimpleNamespace(has_auth=False, provider="codex", codex_auth_file_exists=False)
        with pytest.raises(typer.BadParameter, match="Responses API"):
            require_auth(settings)

    def test_codex_no_auth_with_file(self) -> None:
        settings = SimpleNamespace(
            has_auth=False,
            provider="codex",
            codex_auth_file_exists=True,
            codex_auth_mode="desktop-login",
        )
        with pytest.raises(typer.BadParameter, match="local"):
            require_auth(settings)

    def test_codex_oauth_no_auth(self) -> None:
        settings = SimpleNamespace(has_auth=False, provider="codex-oauth")
        with pytest.raises(typer.BadParameter, match="Codex OAuth"):
            require_auth(settings)

    def test_generic_no_auth(self) -> None:
        settings = SimpleNamespace(has_auth=False, provider="other")
        with pytest.raises(typer.BadParameter, match="Missing"):
            require_auth(settings)


# ---------------------------------------------------------------------------
# get_kernel_store
# ---------------------------------------------------------------------------
class TestGetKernelStore:
    def test_returns_store(self, tmp_path: Path) -> None:
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
            kernel_db_path=Path(":memory:"),
            locale="en-US",
        )
        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._helpers.ensure_default_context_file"),
            patch("hermit.plugins.builtin.hooks.memory.engine.MemoryEngine"),
        ):
            store = get_kernel_store()
        assert store is not None


# ---------------------------------------------------------------------------
# format_epoch
# ---------------------------------------------------------------------------
class TestFormatEpoch:
    def test_none_returns_dash(self) -> None:
        assert format_epoch(None) == "-"

    def test_valid_timestamp(self) -> None:
        ts = 1700000000.0
        result = format_epoch(ts)
        # Should be a valid ISO format
        parsed = datetime.fromisoformat(result)
        assert parsed is not None
