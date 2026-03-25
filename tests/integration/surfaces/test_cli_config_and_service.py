from __future__ import annotations

import contextlib
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import hermit.surfaces.cli._commands_core as core_mod
import hermit.surfaces.cli._commands_plugin as plugin_mod
import hermit.surfaces.cli._serve as serve_mod
from hermit.infra.system.i18n import tr
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.plugins.builtin.hooks.scheduler.models import JobExecutionRecord, ScheduledJob
from hermit.runtime.provider_host.execution.runtime import AgentResult


def _lazy_app():
    """Import the Typer app lazily to avoid module-level side effects from main.py."""
    from hermit.surfaces.cli.main import app

    return app


def test_profiles_list_reads_config_toml(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

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
    result = runner.invoke(_lazy_app(), ["profiles", "list"])

    assert result.exit_code == 0
    assert "codex-local（默认） 提供方=codex-oauth 模型=gpt-5.4" in result.output
    assert "claude-work 提供方=claude 模型=claude-3-7-sonnet-latest" in result.output


def test_profiles_list_reports_missing_config_toml(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    base_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(_lazy_app(), ["profiles", "list"])

    assert result.exit_code == 0
    assert (
        tr(
            "cli.profiles_list.no_config",
            locale="zh-CN",
            path=base_dir / "config.toml",
        )
        in result.output
    )


def test_config_show_includes_profile_and_auth_summary(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

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
    monkeypatch.delenv("HERMIT_PROVIDER", raising=False)
    monkeypatch.delenv("HERMIT_PROFILE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(_lazy_app(), ["config", "show"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["selected_profile"] == "shared"
    assert payload["provider"] == "claude"
    assert payload["auth"]["ok"] is True


def test_auth_status_reports_codex_oauth_from_local_auth(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

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
    result = runner.invoke(_lazy_app(), ["auth", "status"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["provider"] == "codex-oauth"
    assert payload["ok"] is True
    assert payload["source"] == "~/.codex/auth.json"


def test_notify_reload_uses_settings_scheduler_chat_id(monkeypatch, tmp_path) -> None:
    fired: list[dict[str, object]] = []

    class FakeHooks:
        def fire(self, event, **kwargs):
            fired.append(kwargs)

    class FakePluginManager:
        def __init__(self, settings=None):
            self.hooks = FakeHooks()

        def discover_and_load(self, *args, **kwargs):
            return None

    monkeypatch.setattr(serve_mod, "PluginManager", FakePluginManager)
    settings = SimpleNamespace(
        scheduler_feishu_chat_id="oc_cfg_chat",
        plugins_dir=tmp_path / "plugins",
    )

    serve_mod._notify_reload(settings, "feishu")

    assert fired and fired[0]["notify"] == {"feishu_chat_id": "oc_cfg_chat"}


def test_reload_removes_stale_pid_file(tmp_path, monkeypatch) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()
    pid_path = base_dir / "serve-feishu.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("12345", encoding="utf-8")
    monkeypatch.setattr(
        serve_mod.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError())
    )

    runner = CliRunner()
    result = runner.invoke(_lazy_app(), ["reload"])

    assert result.exit_code == 1
    assert "PID 文件已过期" in result.output
    assert not pid_path.exists()


def test_run_and_chat_commands_delegate_to_runner_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str]] = []
    settings = SimpleNamespace(
        log_level="INFO",
        prevent_sleep=False,
        has_auth=True,
    )

    class FakeRunner:
        def handle(self, session_id: str, prompt: str, on_tool_call=None, **kwargs) -> AgentResult:
            events.append(("handle", f"{session_id}:{prompt}"))
            return AgentResult(text="one-shot done", turns=1, tool_calls=0)

        def close_session(self, session_id: str) -> None:
            events.append(("close_session", session_id))

        def dispatch(self, session_id: str, user_input: str, on_tool_call=None):
            events.append(("dispatch", f"{session_id}:{user_input}"))
            if user_input == "cmd":
                return SimpleNamespace(
                    is_command=True, text="command output", should_exit=False, agent_result=None
                )
            if user_input == "quit":
                return SimpleNamespace(
                    is_command=True, text="bye", should_exit=True, agent_result=None
                )
            return SimpleNamespace(
                is_command=False,
                text="",
                should_exit=False,
                agent_result=AgentResult(text="chat done", turns=1, tool_calls=0),
            )

    class FakePM:
        def stop_mcp_servers(self) -> None:
            events.append(("stop_mcp", "pm"))

    runner = FakeRunner()
    pm = FakePM()
    prompts = iter(["", "cmd", "talk", "quit"])
    echoes: list[str] = []
    printed: list[str] = []

    monkeypatch.setattr(core_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(
        core_mod, "ensure_workspace", lambda settings: events.append(("workspace", "ok"))
    )
    monkeypatch.setattr(
        core_mod, "configure_logging", lambda level: events.append(("logging", level))
    )
    monkeypatch.setattr(core_mod, "require_auth", lambda settings: events.append(("auth", "ok")))
    monkeypatch.setattr(core_mod, "build_runner", lambda settings: (runner, pm))
    monkeypatch.setattr(
        core_mod,
        "stop_runner_background_services",
        lambda runner: events.append(("stop_bg", "runner")),
    )
    monkeypatch.setattr(core_mod, "print_result", lambda result: printed.append(result.text))
    monkeypatch.setattr(core_mod.typer, "echo", lambda text="": echoes.append(text))
    monkeypatch.setattr(core_mod, "caffeinate", lambda settings: contextlib.nullcontext())
    monkeypatch.setattr("builtins.input", lambda prompt="": next(prompts))

    core_mod.run("ship it")
    core_mod.chat(session_id="sess-1", debug=True)

    assert ("handle", "cli-oneshot:ship it") in events
    assert ("close_session", "cli-oneshot") in events
    assert ("dispatch", "sess-1:cmd") in events
    assert ("dispatch", "sess-1:talk") in events
    assert ("close_session", "sess-1") in events
    assert printed == ["one-shot done", "chat done"]
    assert "command output" in echoes
    assert "bye" in echoes


def test_startup_prompt_and_sessions_use_runtime_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = SimpleNamespace(
        base_dir=tmp_path / ".hermit",
        plugins_dir=tmp_path / "plugins",
        sessions_dir=tmp_path / "sessions",
        session_idle_timeout_seconds=120,
    )
    echoes: list[str] = []
    load_calls: list[tuple[Path, Path]] = []

    class FakePM:
        def __init__(self, settings=None) -> None:
            pass

        def discover_and_load(self, builtin_dir: Path, plugins_dir: Path) -> None:
            load_calls.append((builtin_dir, plugins_dir))

        def build_system_prompt(self, base: dict[str, str]) -> str:
            return f"prompt:{base['cwd']}"

    class FakeSessionManager:
        def __init__(self, sessions_dir: Path, timeout: int) -> None:
            assert sessions_dir == settings.sessions_dir
            assert timeout == 120

        def list_sessions(self) -> list[str]:
            return ["alpha", "beta"]

    monkeypatch.setattr(core_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(core_mod, "ensure_workspace", lambda settings: None)
    monkeypatch.setattr(core_mod, "PluginManager", FakePM)
    monkeypatch.setattr(core_mod, "build_base_context", lambda settings, cwd: {"cwd": str(cwd)})
    monkeypatch.setattr(serve_mod, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(serve_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(serve_mod, "ensure_workspace", lambda settings: None)
    monkeypatch.setattr(core_mod.typer, "echo", lambda text="": echoes.append(text))
    monkeypatch.setattr(serve_mod.typer, "echo", lambda text="": echoes.append(text))

    core_mod.startup_prompt()
    serve_mod.sessions()

    assert load_calls and load_calls[0][1] == settings.plugins_dir
    assert any(line.startswith("prompt:") for line in echoes)
    assert echoes[-2:] == ["alpha", "beta"]


def test_reload_and_service_helpers_cover_permission_and_success_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    get_settings.cache_clear()
    settings = get_settings()
    pid_path = serve_mod._pid_path(settings, "feishu")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4321", encoding="utf-8")

    runner = CliRunner()
    monkeypatch.setattr(
        serve_mod.os, "kill", lambda pid, sig: (_ for _ in ()).throw(PermissionError())
    )
    denied = runner.invoke(_lazy_app(), ["reload"])
    assert denied.exit_code == 1
    assert "Permission denied" in denied.output

    pid_path.write_text("4321", encoding="utf-8")
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(serve_mod.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    success = runner.invoke(_lazy_app(), ["reload"])
    assert success.exit_code == 0
    assert sent and sent[0][0] == 4321

    serve_mod._write_pid(pid_path)
    assert serve_mod._read_pid(pid_path) == serve_mod.os.getpid()
    serve_mod._remove_pid(pid_path)
    assert serve_mod._read_pid(pid_path) is None


def test_serve_refuses_duplicate_live_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    get_settings.cache_clear()
    settings = get_settings()
    pid_path = serve_mod._pid_path(settings, "feishu")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4321", encoding="utf-8")

    monkeypatch.setattr(serve_mod.os, "kill", lambda pid, sig: None)

    result = CliRunner().invoke(_lazy_app(), ["serve"])

    assert result.exit_code == 1
    assert "already running" in result.output


def test_serve_cleans_stale_pid_before_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "secret")
    get_settings.cache_clear()
    settings = get_settings()
    pid_path = serve_mod._pid_path(settings, "feishu")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4321", encoding="utf-8")

    serve_calls: list[str] = []

    def fake_serve_loop(adapter: str, pid_file: Path) -> None:
        serve_calls.append(f"{adapter}:{pid_file}")

    def fake_kill(pid: int, sig: int) -> None:
        if pid == 4321 and sig == 0:
            raise ProcessLookupError()

    monkeypatch.setattr(serve_mod.os, "kill", fake_kill)
    monkeypatch.setattr(serve_mod, "_serve_loop", fake_serve_loop)

    result = CliRunner().invoke(_lazy_app(), ["serve"])

    assert result.exit_code == 0
    assert "stale PID file" in result.output
    assert serve_calls
    assert not pid_path.exists()


def test_plugin_commands_manage_installed_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hermit.runtime.assembly.config import get_settings

    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    get_settings.cache_clear()
    settings = SimpleNamespace(
        base_dir=tmp_path / ".hermit",
        plugins_dir=tmp_path / ".hermit" / "plugins",
    )
    settings.plugins_dir.mkdir(parents=True, exist_ok=True)

    class FakePM:
        def __init__(self, settings=None) -> None:
            self.manifests = [
                SimpleNamespace(
                    name="builtin-demo", version="1.0.0", description="builtin", builtin=True
                ),
                SimpleNamespace(
                    name="installed-demo", version="2.0.0", description="custom", builtin=False
                ),
            ]

        def discover_and_load(self, builtin_dir: Path, plugins_dir: Path) -> None:
            return None

    manifest_dir = settings.plugins_dir / "demo"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.toml").write_text(
        'name = "demo"\nversion = "0.1.0"\ndescription = "Demo plugin"\nauthor = "Beta"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(plugin_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(plugin_mod, "ensure_workspace", lambda settings: None)
    monkeypatch.setattr(plugin_mod, "PluginManager", FakePM)
    monkeypatch.setattr(
        plugin_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=""),
    )

    runner = CliRunner()
    listed = runner.invoke(_lazy_app(), ["plugin", "list"])
    installed = runner.invoke(
        _lazy_app(), ["plugin", "install", "https://example.com/new-plugin.git"]
    )
    info = runner.invoke(_lazy_app(), ["plugin", "info", "demo"])
    removed = runner.invoke(_lazy_app(), ["plugin", "remove", "demo"])

    assert listed.exit_code == 0
    assert "[builtin] builtin-demo v1.0.0" in listed.output
    assert "[installed] installed-demo v2.0.0" in listed.output
    assert installed.exit_code == 0
    assert "Installed plugin 'new-plugin'." in installed.output
    assert info.exit_code == 0
    assert "Name:        demo" in info.output
    assert removed.exit_code == 0
    assert not manifest_dir.exists()


def test_schedule_commands_cover_listing_mutation_and_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")
    get_settings.cache_clear()
    store = KernelStore(base_dir / "kernel" / "state.db")

    cron_job = ScheduledJob.create(
        name="cron-job", prompt="run", schedule_type="cron", cron_expr="0 9 * * 1-5"
    )
    once_job = ScheduledJob.create(
        name="once-job", prompt="once", schedule_type="once", once_at=1773516000.0
    )
    interval_job = ScheduledJob.create(
        name="interval-job", prompt="interval", schedule_type="interval", interval_seconds=300
    )
    interval_job.enabled = False
    store.create_schedule(cron_job)
    store.create_schedule(once_job)
    store.create_schedule(interval_job)
    store.append_schedule_history(
        JobExecutionRecord(
            job_id=cron_job.id,
            job_name=cron_job.name,
            started_at=1773516000.0,
            finished_at=1773516012.0,
            success=False,
            result_text="partial output\nsecond line",
            error="boom",
        )
    )

    runner = CliRunner()
    listed = runner.invoke(_lazy_app(), ["schedule", "list"])
    history = runner.invoke(_lazy_app(), ["schedule", "history", "--job-id", cron_job.id])
    future_once = (dt.datetime.now() + dt.timedelta(days=1)).replace(microsecond=0).isoformat()
    added = runner.invoke(
        _lazy_app(),
        ["schedule", "add", "--name", "new-once", "--prompt", "do work", "--once", future_once],
    )
    enabled = runner.invoke(_lazy_app(), ["schedule", "enable", interval_job.id])
    disabled = runner.invoke(_lazy_app(), ["schedule", "disable", cron_job.id])
    removed = runner.invoke(_lazy_app(), ["schedule", "remove", once_job.id])

    assert listed.exit_code == 0
    assert "cron-job" in listed.output
    assert "once at" in listed.output
    assert "disabled" in listed.output
    assert history.exit_code == 0
    assert "[FAIL] cron-job" in history.output
    assert "Error: boom" in history.output
    assert added.exit_code == 0
    assert "Added task [" in added.output
    assert enabled.exit_code == 0
    assert "Enabled task" in enabled.output
    assert disabled.exit_code == 0
    assert "Disabled task" in disabled.output
    assert removed.exit_code == 0
    assert "Removed task" in removed.output
