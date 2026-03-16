from __future__ import annotations

import contextlib
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import hermit.main as main_mod
from hermit.builtin.scheduler.models import JobExecutionRecord, ScheduledJob
from hermit.kernel.store import KernelStore
from hermit.provider.runtime import AgentResult


def test_setup_supports_proxy_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hermit.config import get_settings

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit"))
    get_settings.cache_clear()

    confirm_answers = iter([True, False])
    prompt_answers = iter(
        ["token-1", "https://proxy.local", "X-Biz-Id: demo", "claude-proxy-model"]
    )

    monkeypatch.setattr(main_mod.typer, "confirm", lambda *args, **kwargs: next(confirm_answers))
    monkeypatch.setattr(main_mod.typer, "prompt", lambda *args, **kwargs: next(prompt_answers))

    result = CliRunner().invoke(main_mod.app, ["setup"])

    env_text = (tmp_path / ".hermit" / ".env").read_text(encoding="utf-8")
    assert result.exit_code == 0
    assert "HERMIT_AUTH_TOKEN=token-1" in env_text
    assert "HERMIT_BASE_URL=https://proxy.local" in env_text
    assert "HERMIT_CUSTOM_HEADERS=X-Biz-Id: demo" in env_text
    assert "HERMIT_MODEL=claude-proxy-model" in env_text


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
        def handle(self, session_id: str, prompt: str, on_tool_call=None) -> AgentResult:
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

    monkeypatch.setattr(main_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main_mod, "_ensure_workspace", lambda settings: events.append(("workspace", "ok"))
    )
    monkeypatch.setattr(
        main_mod, "configure_logging", lambda level: events.append(("logging", level))
    )
    monkeypatch.setattr(main_mod, "_require_auth", lambda settings: events.append(("auth", "ok")))
    monkeypatch.setattr(main_mod, "_build_runner", lambda settings: (runner, pm))
    monkeypatch.setattr(
        main_mod,
        "_stop_runner_background_services",
        lambda runner: events.append(("stop_bg", "runner")),
    )
    monkeypatch.setattr(main_mod, "_print_result", lambda result: printed.append(result.text))
    monkeypatch.setattr(main_mod.typer, "echo", lambda text="": echoes.append(text))
    monkeypatch.setattr(main_mod, "_caffeinate", lambda settings: contextlib.nullcontext())
    monkeypatch.setattr("builtins.input", lambda prompt="": next(prompts))

    main_mod.run("ship it")
    main_mod.chat(session_id="sess-1", debug=True)

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

    monkeypatch.setattr(main_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(main_mod, "_ensure_workspace", lambda settings: None)
    monkeypatch.setattr(main_mod, "PluginManager", FakePM)
    monkeypatch.setattr(main_mod, "build_base_context", lambda settings, cwd: {"cwd": str(cwd)})
    monkeypatch.setattr(main_mod, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(main_mod.typer, "echo", lambda text="": echoes.append(text))

    main_mod.startup_prompt()
    main_mod.sessions()

    assert load_calls and load_calls[0][1] == settings.plugins_dir
    assert any(line.startswith("prompt:") for line in echoes)
    assert echoes[-2:] == ["alpha", "beta"]


def test_reload_and_service_helpers_cover_permission_and_success_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermit.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()
    settings = get_settings()
    pid_path = main_mod._pid_path(settings, "feishu")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4321", encoding="utf-8")

    runner = CliRunner()
    monkeypatch.setattr(
        main_mod.os, "kill", lambda pid, sig: (_ for _ in ()).throw(PermissionError())
    )
    denied = runner.invoke(main_mod.app, ["reload"])
    assert denied.exit_code == 1
    assert "Permission denied" in denied.output

    pid_path.write_text("4321", encoding="utf-8")
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(main_mod.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    success = runner.invoke(main_mod.app, ["reload"])
    assert success.exit_code == 0
    assert sent and sent[0][0] == 4321

    main_mod._write_pid(pid_path)
    assert main_mod._read_pid(pid_path) == main_mod.os.getpid()
    main_mod._remove_pid(pid_path)
    assert main_mod._read_pid(pid_path) is None


def test_serve_refuses_duplicate_live_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermit.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    get_settings.cache_clear()
    settings = get_settings()
    pid_path = main_mod._pid_path(settings, "feishu")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4321", encoding="utf-8")

    monkeypatch.setattr(main_mod.os, "kill", lambda pid, sig: None)

    result = CliRunner().invoke(main_mod.app, ["serve"])

    assert result.exit_code == 1
    assert "already running" in result.output


def test_serve_cleans_stale_pid_before_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hermit.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "cli_xxx")
    monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "secret")
    get_settings.cache_clear()
    settings = get_settings()
    pid_path = main_mod._pid_path(settings, "feishu")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4321", encoding="utf-8")

    serve_calls: list[str] = []

    def fake_serve_loop(adapter: str, pid_file: Path) -> None:
        serve_calls.append(f"{adapter}:{pid_file}")

    def fake_kill(pid: int, sig: int) -> None:
        if pid == 4321 and sig == 0:
            raise ProcessLookupError()

    monkeypatch.setattr(main_mod.os, "kill", fake_kill)
    monkeypatch.setattr(main_mod, "_serve_loop", fake_serve_loop)

    result = CliRunner().invoke(main_mod.app, ["serve"])

    assert result.exit_code == 0
    assert "stale PID file" in result.output
    assert serve_calls
    assert not pid_path.exists()


def test_plugin_commands_manage_installed_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    monkeypatch.setattr(main_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(main_mod, "_ensure_workspace", lambda settings: None)
    monkeypatch.setattr(main_mod, "PluginManager", FakePM)
    monkeypatch.setattr(
        main_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=""),
    )

    runner = CliRunner()
    listed = runner.invoke(main_mod.app, ["plugin", "list"])
    installed = runner.invoke(
        main_mod.app, ["plugin", "install", "https://example.com/new-plugin.git"]
    )
    info = runner.invoke(main_mod.app, ["plugin", "info", "demo"])
    removed = runner.invoke(main_mod.app, ["plugin", "remove", "demo"])

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
    from hermit.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))
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
    listed = runner.invoke(main_mod.app, ["schedule", "list"])
    history = runner.invoke(main_mod.app, ["schedule", "history", "--job-id", cron_job.id])
    future_once = (dt.datetime.now() + dt.timedelta(days=1)).replace(microsecond=0).isoformat()
    added = runner.invoke(
        main_mod.app,
        ["schedule", "add", "--name", "new-once", "--prompt", "do work", "--once", future_once],
    )
    enabled = runner.invoke(main_mod.app, ["schedule", "enable", interval_job.id])
    disabled = runner.invoke(main_mod.app, ["schedule", "disable", cron_job.id])
    removed = runner.invoke(main_mod.app, ["schedule", "remove", once_job.id])

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
