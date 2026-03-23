"""Tests for src/hermit/surfaces/cli/_commands_core.py"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import typer.testing

from hermit.surfaces.cli.main import app

runner = typer.testing.CliRunner()


def _fake_settings(tmp_path: Path, **overrides) -> SimpleNamespace:
    defaults = dict(
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
        log_level="WARNING",
        config_file=tmp_path / "config.toml",
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
        feishu_app_id="",
        feishu_thread_progress=False,
        scheduler_enabled=False,
        scheduler_catch_up=False,
        scheduler_feishu_chat_id="",
        webhook_enabled=False,
        resolved_webhook_host="localhost",
        resolved_webhook_port=8080,
        claude_api_key="test-key",
        claude_auth_token=None,
        has_auth=True,
        prevent_sleep=False,
        session_idle_timeout_seconds=3600,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# config show
# ---------------------------------------------------------------------------
class TestConfigShow:
    def test_outputs_json(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        with patch("hermit.runtime.assembly.config.get_settings") as mock_gs:
            mock_gs.return_value = settings
            mock_gs.cache_clear = MagicMock()
            result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "provider" in parsed


# ---------------------------------------------------------------------------
# profiles list
# ---------------------------------------------------------------------------
class TestProfilesList:
    def test_with_profiles(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        catalog = SimpleNamespace(
            exists=True,
            profiles={
                "default": {"provider": "claude", "model": "claude-3"},
                "fast": {"provider": "claude"},
            },
            default_profile="default",
            path=tmp_path / "config.toml",
        )
        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch(
                "hermit.surfaces.cli._commands_core.load_profile_catalog",
                return_value=catalog,
            ),
        ):
            result = runner.invoke(app, ["profiles", "list"])
        assert result.exit_code == 0
        assert "default" in result.output
        assert "fast" in result.output

    def test_no_config_file(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        catalog = SimpleNamespace(exists=False, path=tmp_path / "config.toml")
        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch(
                "hermit.surfaces.cli._commands_core.load_profile_catalog",
                return_value=catalog,
            ),
        ):
            result = runner.invoke(app, ["profiles", "list"])
        assert result.exit_code == 0

    def test_no_profiles(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        catalog = SimpleNamespace(
            exists=True, profiles={}, default_profile=None, path=tmp_path / "config.toml"
        )
        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch(
                "hermit.surfaces.cli._commands_core.load_profile_catalog",
                return_value=catalog,
            ),
        ):
            result = runner.invoke(app, ["profiles", "list"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# profiles resolve
# ---------------------------------------------------------------------------
class TestProfilesResolve:
    def test_resolve_named(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        resolved = SimpleNamespace(
            name="default",
            source_path=tmp_path / "config.toml",
            values={"provider": "claude"},
            exists=True,
        )
        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch(
                "hermit.surfaces.cli._commands_core.resolve_profile",
                return_value=resolved,
            ),
        ):
            result = runner.invoke(app, ["profiles", "resolve", "--name", "default"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["resolved_profile"] == "default"


# ---------------------------------------------------------------------------
# auth status
# ---------------------------------------------------------------------------
class TestAuthStatus:
    def test_outputs_json(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        with patch("hermit.runtime.assembly.config.get_settings") as mock_gs:
            mock_gs.return_value = settings
            mock_gs.cache_clear = MagicMock()
            result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "provider" in parsed
        assert "selected_profile" in parsed


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
class TestInit:
    def test_default_init(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
        ):
            result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Initialized" in result.output


# ---------------------------------------------------------------------------
# startup-prompt
# ---------------------------------------------------------------------------
class TestStartupPrompt:
    def test_outputs_prompt(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_pm = MagicMock()
        mock_pm.build_system_prompt.return_value = "System prompt content"
        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_core.PluginManager", return_value=mock_pm),
            patch("hermit.surfaces.cli._commands_core.build_base_context", return_value="base"),
        ):
            result = runner.invoke(app, ["startup-prompt"])
        assert result.exit_code == 0
        assert "System prompt content" in result.output


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
class TestRun:
    def test_successful_run(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_runner = MagicMock()
        mock_result = SimpleNamespace(thinking=None, text="Answer")
        mock_runner.handle.return_value = mock_result
        mock_pm = MagicMock()

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_core.configure_logging"),
            patch("hermit.surfaces.cli._commands_core.require_auth"),
            patch(
                "hermit.surfaces.cli._commands_core.build_runner",
                return_value=(mock_runner, mock_pm),
            ),
            patch("hermit.surfaces.cli._commands_core.caffeinate") as mock_caff,
        ):
            mock_caff.return_value.__enter__ = MagicMock()
            mock_caff.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(app, ["run", "hello"])

        assert result.exit_code == 0
        assert "Answer" in result.output
        mock_runner.handle.assert_called_once()
        mock_runner.close_session.assert_called_once()

    def test_run_with_policy(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_runner = MagicMock()
        mock_result = SimpleNamespace(thinking=None, text="Done")
        mock_runner.handle.return_value = mock_result
        mock_pm = MagicMock()

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_core.configure_logging"),
            patch("hermit.surfaces.cli._commands_core.require_auth"),
            patch(
                "hermit.surfaces.cli._commands_core.build_runner",
                return_value=(mock_runner, mock_pm),
            ),
            patch("hermit.surfaces.cli._commands_core.caffeinate") as mock_caff,
        ):
            mock_caff.return_value.__enter__ = MagicMock()
            mock_caff.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(app, ["run", "hello", "--policy", "autonomous"])

        assert result.exit_code == 0
        call_kwargs = mock_runner.handle.call_args
        assert call_kwargs[1].get("run_opts", {}).get(
            "policy_profile"
        ) == "autonomous" or "policy_profile" in str(call_kwargs)


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------
class TestChat:
    def test_eof_exits(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_runner = MagicMock()
        mock_pm = MagicMock()

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_core.configure_logging"),
            patch("hermit.surfaces.cli._commands_core.require_auth"),
            patch(
                "hermit.surfaces.cli._commands_core.build_runner",
                return_value=(mock_runner, mock_pm),
            ),
            patch("hermit.surfaces.cli._commands_core.caffeinate") as mock_caff,
            patch("builtins.input", side_effect=EOFError),
        ):
            mock_caff.return_value.__enter__ = MagicMock()
            mock_caff.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(app, ["chat"])

        assert result.exit_code == 0
        assert "Bye" in result.output

    def test_empty_input_skipped(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_runner = MagicMock()
        mock_pm = MagicMock()

        inputs = iter(["", "  ", EOFError])

        def mock_input(prompt=""):
            val = next(inputs)
            if isinstance(val, type) and issubclass(val, BaseException):
                raise val()
            return val

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_core.configure_logging"),
            patch("hermit.surfaces.cli._commands_core.require_auth"),
            patch(
                "hermit.surfaces.cli._commands_core.build_runner",
                return_value=(mock_runner, mock_pm),
            ),
            patch("hermit.surfaces.cli._commands_core.caffeinate") as mock_caff,
            patch("builtins.input", side_effect=mock_input),
        ):
            mock_caff.return_value.__enter__ = MagicMock()
            mock_caff.return_value.__exit__ = MagicMock(return_value=False)
            runner.invoke(app, ["chat"])

        # dispatch should not have been called for empty inputs
        mock_runner.dispatch.assert_not_called()

    def test_command_dispatch(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_runner = MagicMock()
        mock_pm = MagicMock()

        dispatch_result = SimpleNamespace(
            is_command=True,
            text="Help text",
            should_exit=True,
            agent_result=None,
        )
        mock_runner.dispatch.return_value = dispatch_result

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_core.configure_logging"),
            patch("hermit.surfaces.cli._commands_core.require_auth"),
            patch(
                "hermit.surfaces.cli._commands_core.build_runner",
                return_value=(mock_runner, mock_pm),
            ),
            patch("hermit.surfaces.cli._commands_core.caffeinate") as mock_caff,
            patch("builtins.input", side_effect=["/quit"]),
        ):
            mock_caff.return_value.__enter__ = MagicMock()
            mock_caff.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(app, ["chat"])

        assert result.exit_code == 0
        assert "Help text" in result.output

    def test_agent_result_dispatch(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        mock_runner = MagicMock()
        mock_pm = MagicMock()

        agent_result = SimpleNamespace(thinking=None, text="Agent answer")
        dispatch_result = SimpleNamespace(
            is_command=False,
            text="",
            should_exit=False,
            agent_result=agent_result,
        )
        mock_runner.dispatch.return_value = dispatch_result

        with (
            patch("hermit.runtime.assembly.config.get_settings", return_value=settings),
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
            patch("hermit.surfaces.cli._commands_core.configure_logging"),
            patch("hermit.surfaces.cli._commands_core.require_auth"),
            patch(
                "hermit.surfaces.cli._commands_core.build_runner",
                return_value=(mock_runner, mock_pm),
            ),
            patch("hermit.surfaces.cli._commands_core.caffeinate") as mock_caff,
            patch("builtins.input", side_effect=["hello", EOFError]),
        ):
            mock_caff.return_value.__enter__ = MagicMock()
            mock_caff.return_value.__exit__ = MagicMock(return_value=False)
            result = runner.invoke(app, ["chat"])

        assert result.exit_code == 0
        assert "Agent answer" in result.output


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
class TestSetup:
    def test_fresh_setup_no_proxy(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        with (
            patch("hermit.surfaces.cli._commands_core.get_settings") as mock_gs,
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
        ):
            mock_gs.return_value = settings
            mock_gs.cache_clear = MagicMock()
            # Inputs: no proxy, API key, no feishu
            result = runner.invoke(app, ["setup"], input="n\nsk-test-key\nn\n")
        assert result.exit_code == 0
        assert "Done" in result.output
        env_file = tmp_path / ".env"
        assert env_file.exists()
        content = env_file.read_text()
        assert "ANTHROPIC_API_KEY=sk-test-key" in content

    def test_setup_with_proxy(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        with (
            patch("hermit.surfaces.cli._commands_core.get_settings") as mock_gs,
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
        ):
            mock_gs.return_value = settings
            mock_gs.cache_clear = MagicMock()
            # Inputs: yes proxy, auth token, base url, no headers, default model, no feishu
            result = runner.invoke(app, ["setup"], input="y\ntoken123\nhttps://proxy.com\n\n\nn\n")
        assert result.exit_code == 0
        content = (tmp_path / ".env").read_text()
        assert "HERMIT_CLAUDE_AUTH_TOKEN=token123" in content
        assert "HERMIT_CLAUDE_BASE_URL=https://proxy.com" in content

    def test_setup_with_feishu(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        with (
            patch("hermit.surfaces.cli._commands_core.get_settings") as mock_gs,
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
        ):
            mock_gs.return_value = settings
            mock_gs.cache_clear = MagicMock()
            # Inputs: no proxy, API key, yes feishu, app_id, app_secret
            result = runner.invoke(
                app, ["setup"], input="n\nsk-test\ny\nfeishu-id\nfeishu-secret\n"
            )
        assert result.exit_code == 0
        content = (tmp_path / ".env").read_text()
        assert "HERMIT_FEISHU_APP_ID=feishu-id" in content

    def test_setup_overwrite_cancelled(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        env_file = tmp_path / ".env"
        env_file.write_text("existing=config\n")
        with patch("hermit.surfaces.cli._commands_core.get_settings") as mock_gs:
            mock_gs.return_value = settings
            mock_gs.cache_clear = MagicMock()
            result = runner.invoke(app, ["setup"], input="n\n")
        assert result.exit_code == 0
        assert "cancelled" in result.output.lower() or "Setup cancelled" in result.output

    def test_setup_overwrite_confirmed(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        env_file = tmp_path / ".env"
        env_file.write_text("existing=config\n")
        with (
            patch("hermit.surfaces.cli._commands_core.get_settings") as mock_gs,
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
        ):
            mock_gs.return_value = settings
            mock_gs.cache_clear = MagicMock()
            result = runner.invoke(app, ["setup"], input="y\nn\nsk-key\nn\n")
        assert result.exit_code == 0
        assert "Done" in result.output

    def test_setup_with_custom_headers(self, tmp_path: Path) -> None:
        settings = _fake_settings(tmp_path)
        with (
            patch("hermit.surfaces.cli._commands_core.get_settings") as mock_gs,
            patch("hermit.surfaces.cli._commands_core.ensure_workspace"),
        ):
            mock_gs.return_value = settings
            mock_gs.cache_clear = MagicMock()
            result = runner.invoke(
                app,
                ["setup"],
                input="y\ntoken\nhttps://proxy.com\nX-Custom: val\nclaude-3\nn\n",
            )
        assert result.exit_code == 0
        content = (tmp_path / ".env").read_text()
        assert "HERMIT_CLAUDE_HEADERS=X-Custom: val" in content
