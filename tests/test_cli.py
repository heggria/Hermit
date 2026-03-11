from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from hermit.main import _build_serve_preflight, _notify_reload, app


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
    import hermit.main as main_mod
    from hermit.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    get_settings.cache_clear()

    confirm_answers = iter([False, False])

    monkeypatch.setattr(main_mod.typer, "confirm", lambda *args, **kwargs: next(confirm_answers))
    monkeypatch.setattr(main_mod.typer, "prompt", lambda *args, **kwargs: "sk-ant-test")

    runner = CliRunner()
    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert (tmp_path / ".hermit" / ".env").read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=sk-ant-test\n"


def test_setup_shows_adapter_flag_in_next_steps(tmp_path, monkeypatch) -> None:
    import hermit.main as main_mod
    from hermit.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    get_settings.cache_clear()

    confirm_answers = iter([False, True])
    prompt_answers = iter(["sk-ant-test", "cli_xxx", "secret"])

    monkeypatch.setattr(main_mod.typer, "confirm", lambda *args, **kwargs: next(confirm_answers))
    monkeypatch.setattr(main_mod.typer, "prompt", lambda *args, **kwargs: next(prompt_answers))

    runner = CliRunner()
    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert "hermit serve --adapter feishu" in result.output


def test_serve_preflight_reports_missing_feishu_env(tmp_path, monkeypatch) -> None:
    from hermit.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
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
    assert "[MISSING] 飞书 App ID" in result.output
    assert "[MISSING] 飞书 App Secret" in result.output
    assert "启动前检查未通过" in result.output


def test_serve_preflight_shows_resolved_env_sources(tmp_path, monkeypatch) -> None:
    import hermit.main as main_mod
    from hermit.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "secret")
    get_settings.cache_clear()

    serve_calls: list[tuple[str, str]] = []

    def fake_serve_loop(adapter: str, pid_file) -> None:
        serve_calls.append((adapter, str(pid_file)))

    monkeypatch.setattr(main_mod, "_serve_loop", fake_serve_loop)

    runner = CliRunner()
    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0
    assert "Hermit 启动前环境自检" in result.output
    assert "[OK] 飞书 App ID: HERMIT_FEISHU_APP_ID (shell env)" in result.output
    assert "[OK] 飞书 App Secret: HERMIT_FEISHU_APP_SECRET (shell env)" in result.output
    assert serve_calls and serve_calls[0][0] == "feishu"


def test_profiles_list_reads_config_toml(tmp_path, monkeypatch) -> None:
    from hermit.config import get_settings

    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    (base_dir / "config.toml").write_text(
        """
default_profile = "codex-local"

[profiles.codex-local]
provider = "codex-oauth"
model = "gpt-5.4"

[profiles.claude-work]
provider = "claude"
model = "claude-3-7-sonnet-latest"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["profiles", "list"])

    assert result.exit_code == 0
    assert "codex-local (default) provider=codex-oauth model=gpt-5.4" in result.output
    assert "claude-work provider=claude model=claude-3-7-sonnet-latest" in result.output


def test_profiles_list_reports_missing_config_toml(tmp_path, monkeypatch) -> None:
    from hermit.config import get_settings

    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["profiles", "list"])

    assert result.exit_code == 0
    assert f"No config.toml found at {base_dir / 'config.toml'}" in result.output


def test_config_show_includes_profile_and_auth_summary(tmp_path, monkeypatch) -> None:
    from hermit.config import get_settings

    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    (base_dir / "config.toml").write_text(
        """
default_profile = "shared"

[profiles.shared]
provider = "claude"
model = "claude-3-7-sonnet-latest"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["selected_profile"] == "shared"
    assert payload["provider"] == "claude"
    assert payload["auth"]["ok"] is True


def test_auth_status_reports_codex_oauth_from_local_auth(tmp_path, monkeypatch) -> None:
    from hermit.config import get_settings

    base_dir = tmp_path / ".hermit"
    codex_home = tmp_path / ".codex"
    base_dir.mkdir(parents=True)
    codex_home.mkdir(parents=True)
    (base_dir / "config.toml").write_text(
        """
default_profile = "local"

[profiles.local]
provider = "codex-oauth"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (codex_home / "auth.json").write_text(
        '{"auth_mode":"chatgpt","tokens":{"access_token":"a","refresh_token":"b"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["provider"] == "codex-oauth"
    assert payload["ok"] is True
    assert payload["source"] == "~/.codex/auth.json"


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
    assert details["Profile"] == "local (config.toml)"
    assert details["LLM 鉴权"] == "config.toml profile"
    assert details["飞书 App ID"] == "config.toml profile"
    assert details["飞书 App Secret"] == "config.toml profile"
    assert details["飞书进度卡片"] == "false"
    assert details["Scheduler 飞书通知"] == "已配置"


def test_notify_reload_uses_settings_scheduler_chat_id(monkeypatch, tmp_path) -> None:
    import hermit.main as main_mod

    fired: list[dict[str, object]] = []

    class FakeHooks:
        def fire(self, event, **kwargs):
            fired.append(kwargs)

    class FakePluginManager:
        def __init__(self, settings=None):
            self.hooks = FakeHooks()

        def discover_and_load(self, *args, **kwargs):
            return None

    monkeypatch.setattr(main_mod, "PluginManager", FakePluginManager)
    settings = SimpleNamespace(
        scheduler_feishu_chat_id="oc_cfg_chat",
        plugins_dir=tmp_path / "plugins",
    )

    _notify_reload(settings, "feishu")

    assert fired and fired[0]["notify"] == {"feishu_chat_id": "oc_cfg_chat"}


def test_reload_removes_stale_pid_file(tmp_path, monkeypatch) -> None:
    import hermit.main as main_mod
    from hermit.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()
    pid_path = base_dir / "serve-feishu.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(main_mod.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))

    runner = CliRunner()
    result = runner.invoke(app, ["reload"])

    assert result.exit_code == 1
    assert "stale PID file" in result.output
    assert not pid_path.exists()
