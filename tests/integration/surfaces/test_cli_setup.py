from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import hermit.surfaces.cli._commands_core as core_mod
from hermit.surfaces.cli._preflight import _build_serve_preflight
from hermit.surfaces.cli.main import app


def test_init_creates_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    runner = CliRunner()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert (tmp_path / ".hermit" / "memory" / "memories.md").exists()
    assert (tmp_path / ".hermit" / "context.md").exists()
    assert (tmp_path / ".hermit" / "skills").exists()
    assert (tmp_path / ".hermit" / "plugins").exists()


def test_setup_writes_env_file(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    get_settings.cache_clear()

    confirm_answers = iter([False, False])

    monkeypatch.setattr(core_mod.typer, "confirm", lambda *args, **kwargs: next(confirm_answers))
    monkeypatch.setattr(core_mod.typer, "prompt", lambda *args, **kwargs: "sk-ant-test")

    runner = CliRunner()
    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert (tmp_path / ".hermit" / ".env").read_text(
        encoding="utf-8"
    ) == "ANTHROPIC_API_KEY=sk-ant-test\n"


def test_setup_shows_adapter_flag_in_next_steps(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    get_settings.cache_clear()

    confirm_answers = iter([False, True])
    prompt_answers = iter(["sk-ant-test", "cli_xxx", "secret"])

    monkeypatch.setattr(core_mod.typer, "confirm", lambda *args, **kwargs: next(confirm_answers))
    monkeypatch.setattr(core_mod.typer, "prompt", lambda *args, **kwargs: next(prompt_answers))

    runner = CliRunner()
    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert "hermit serve --adapter feishu" in result.output


def test_setup_next_steps_stay_localized_but_commands_remain_literal(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    get_settings.cache_clear()

    confirm_answers = iter([False, True])
    prompt_answers = iter(["sk-ant-test", "cli_xxx", "secret"])

    monkeypatch.setattr(core_mod.typer, "confirm", lambda *args, **kwargs: next(confirm_answers))
    monkeypatch.setattr(core_mod.typer, "prompt", lambda *args, **kwargs: next(prompt_answers))

    runner = CliRunner()
    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert "Next steps:" in result.output
    assert "  hermit chat" in result.output
    assert "  hermit serve --adapter feishu" in result.output


def test_serve_preflight_reports_missing_feishu_env(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    monkeypatch.setenv("HERMIT_PROVIDER", "claude")
    monkeypatch.delenv("HERMIT_PROFILE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("HERMIT_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("HERMIT_FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 1
    assert "Hermit 启动前环境自检" in result.output
    assert "[OK] LLM 鉴权" in result.output
    assert "[缺失] 飞书 App ID" in result.output
    assert "[缺失] 飞书 App Secret" in result.output
    assert "启动前检查未通过" in result.output


def test_serve_preflight_shows_resolved_env_sources(tmp_path, monkeypatch) -> None:
    import hermit.surfaces.cli._serve as serve_mod
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "secret")
    get_settings.cache_clear()

    serve_calls: list[tuple[str, str]] = []

    def fake_serve_loop(adapter: str, pid_file) -> None:
        serve_calls.append((adapter, str(pid_file)))

    monkeypatch.setattr(serve_mod, "_serve_loop", fake_serve_loop)

    runner = CliRunner()
    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0
    assert "Hermit 启动前环境自检" in result.output
    assert "[OK] 飞书 App ID: HERMIT_FEISHU_APP_ID (shell 环境变量)" in result.output
    assert "[OK] 飞书 App Secret: HERMIT_FEISHU_APP_SECRET (shell 环境变量)" in result.output
    assert serve_calls and serve_calls[0][0] == "feishu"


def test_write_serve_status_persists_latest_status_and_history(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings
    from hermit.surfaces.cli._preflight import write_serve_status

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    settings = get_settings()
    write_serve_status(
        settings,
        "feishu",
        phase="stopped",
        reason="signal",
        detail="SIGTERM received — stopping adapter for shutdown.",
        signal_name="SIGTERM",
        run_started_at="2026-03-12T14:03:09+08:00",
        append_history=True,
    )

    status_path = base_dir / "logs" / "serve-feishu-status.json"
    history_path = base_dir / "logs" / "serve-feishu-exit-history.jsonl"

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["phase"] == "stopped"
    assert status["reason"] == "signal"
    assert status["signal"] == "SIGTERM"
    assert status["run_started_at"] == "2026-03-12T14:03:09+08:00"

    history_lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(history_lines) == 1
    assert json.loads(history_lines[0])["detail"].startswith("SIGTERM received")


def test_serve_records_crash_status_when_serve_loop_raises(tmp_path, monkeypatch) -> None:
    import hermit.surfaces.cli._serve as serve_mod
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "secret")
    get_settings.cache_clear()

    def fake_serve_loop(adapter: str, pid_file) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(serve_mod, "_serve_loop", fake_serve_loop)

    runner = CliRunner()
    result = runner.invoke(app, ["serve"])

    status_path = base_dir / "logs" / "serve-feishu-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert not (base_dir / "serve-feishu.pid").exists()
    assert status["phase"] == "crashed"
    assert status["reason"] == "exception"
    assert status["exception_type"] == "RuntimeError"
    assert "boom" in status["exception_message"]


def test_build_serve_preflight_uses_profile_feishu_settings(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(
        base_dir=tmp_path / ".hermit",
        resolved_profile="local",
        provider="claude",
        claude_api_key="sk-ant-test",
        claude_auth_token=None,
        claude_base_url=None,
        resolved_openai_api_key=None,
        codex_auth_file_exists=False,
        codex_auth_mode=None,
        codex_access_token=None,
        codex_refresh_token=None,
        model="claude-3-7-sonnet-latest",
        feishu_app_id="cli_xxx",
        feishu_app_secret="secret",
        feishu_thread_progress=False,
        scheduler_feishu_chat_id="oc_123",
    )
    settings.base_dir.mkdir(parents=True)
    monkeypatch.delenv("HERMIT_PROFILE", raising=False)
    monkeypatch.delenv("HERMIT_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("HERMIT_FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_BASE_URL", raising=False)

    items, errors = _build_serve_preflight("feishu", settings)
    details = {item.label: item.detail for item in items}

    assert errors == []
    assert details["配置档"] == "local（config.toml）"
    assert details["LLM 鉴权"] == "来自 config.toml 配置档"
    assert details["飞书 App ID"] == "来自 config.toml 配置档"
    assert details["飞书 App Secret"] == "来自 config.toml 配置档"
    assert details["飞书进度卡片"] == "关闭"
    assert details["Scheduler 飞书通知"] == "已配置"


def test_build_serve_preflight_telegram_with_token(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(
        base_dir=tmp_path / ".hermit",
        resolved_profile="default",
        provider="claude",
        claude_api_key="sk-ant-test",
        claude_auth_token=None,
        claude_base_url=None,
        resolved_openai_api_key=None,
        codex_auth_file_exists=False,
        codex_auth_mode=None,
        codex_access_token=None,
        codex_refresh_token=None,
        model="claude-3-7-sonnet-latest",
        telegram_bot_token="tg-token-123",
    )
    settings.base_dir.mkdir(parents=True)
    monkeypatch.delenv("HERMIT_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_BASE_URL", raising=False)

    items, errors = _build_serve_preflight("telegram", settings)
    labels = {item.label for item in items}

    assert errors == []
    assert "Telegram Bot Token" in labels


def test_build_serve_preflight_telegram_missing_token(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(
        base_dir=tmp_path / ".hermit",
        resolved_profile="default",
        provider="claude",
        claude_api_key="sk-ant-test",
        claude_auth_token=None,
        claude_base_url=None,
        resolved_openai_api_key=None,
        codex_auth_file_exists=False,
        codex_auth_mode=None,
        codex_access_token=None,
        codex_refresh_token=None,
        model="claude-3-7-sonnet-latest",
        telegram_bot_token=None,
    )
    settings.base_dir.mkdir(parents=True)
    monkeypatch.delenv("HERMIT_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_BASE_URL", raising=False)

    _items, errors = _build_serve_preflight("telegram", settings)

    assert len(errors) > 0
    assert any("Telegram" in e for e in errors)


def test_build_serve_preflight_slack_with_tokens(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(
        base_dir=tmp_path / ".hermit",
        resolved_profile="default",
        provider="claude",
        claude_api_key="sk-ant-test",
        claude_auth_token=None,
        claude_base_url=None,
        resolved_openai_api_key=None,
        codex_auth_file_exists=False,
        codex_auth_mode=None,
        codex_access_token=None,
        codex_refresh_token=None,
        model="claude-3-7-sonnet-latest",
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
    )
    settings.base_dir.mkdir(parents=True)
    monkeypatch.delenv("HERMIT_SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_SLACK_APP_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_BASE_URL", raising=False)

    items, errors = _build_serve_preflight("slack", settings)
    labels = {item.label for item in items}

    assert errors == []
    assert "Slack Bot Token" in labels
    assert "Slack App Token" in labels


def test_build_serve_preflight_slack_missing_tokens(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(
        base_dir=tmp_path / ".hermit",
        resolved_profile="default",
        provider="claude",
        claude_api_key="sk-ant-test",
        claude_auth_token=None,
        claude_base_url=None,
        resolved_openai_api_key=None,
        codex_auth_file_exists=False,
        codex_auth_mode=None,
        codex_access_token=None,
        codex_refresh_token=None,
        model="claude-3-7-sonnet-latest",
        slack_bot_token=None,
        slack_app_token=None,
    )
    settings.base_dir.mkdir(parents=True)
    monkeypatch.delenv("HERMIT_SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_SLACK_APP_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HERMIT_CLAUDE_BASE_URL", raising=False)
    monkeypatch.delenv("HERMIT_BASE_URL", raising=False)

    _items, errors = _build_serve_preflight("slack", settings)

    assert len(errors) >= 2
    assert any("Slack Bot Token" in e for e in errors)
    assert any("Slack App Token" in e for e in errors)


def test_setup_supports_proxy_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    get_settings.cache_clear()

    confirm_answers = iter([True, False])
    prompt_answers = iter(
        ["token-1", "https://proxy.local", "X-Biz-Id: demo", "claude-proxy-model"]
    )

    monkeypatch.setattr(core_mod.typer, "confirm", lambda *args, **kwargs: next(confirm_answers))
    monkeypatch.setattr(core_mod.typer, "prompt", lambda *args, **kwargs: next(prompt_answers))

    result = CliRunner().invoke(core_mod.app, ["setup"])

    env_text = (tmp_path / ".hermit" / ".env").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "HERMIT_AUTH_TOKEN=token-1" in env_text
    assert "HERMIT_BASE_URL=https://proxy.local" in env_text
    assert "HERMIT_CUSTOM_HEADERS=X-Biz-Id: demo" in env_text
    assert "HERMIT_MODEL=claude-proxy-model" in env_text
