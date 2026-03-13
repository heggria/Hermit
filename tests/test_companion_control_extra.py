from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermit.companion import control


@pytest.fixture(autouse=True)
def _force_companion_locale(monkeypatch):
    monkeypatch.setenv("HERMIT_LOCALE", "en-US")


def test_base_dir_logging_and_exception_formatting(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / "custom-hermit"))
    assert control.hermit_base_dir() == tmp_path / "custom-hermit"
    assert control.companion_log_path().name == "companion.log"

    log_path = control.log_companion_event(
        "restart",
        "Hermit restarted",
        base_dir=tmp_path / ".hermit",
        level="warn",
        detail="stacktrace\n",
    )
    text = log_path.read_text(encoding="utf-8")
    assert "WARN restart: Hermit restarted" in text
    assert "stacktrace" in text

    called = subprocess.CalledProcessError(1, ["uv"], output="out", stderr="err")
    message, detail = control.format_exception_message(called)
    assert message == "err"
    assert "stdout:\nout" in detail
    assert "stderr:\nerr" in detail

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        generic_message, generic_detail = control.format_exception_message(exc)
    assert generic_message == "boom"
    assert "RuntimeError: boom" in generic_detail


def test_temporary_env_and_config_helpers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_PROFILE", "codex")

    with control._temporary_env(  # type: ignore[attr-defined]
        updates={"HERMIT_BASE_DIR": str(tmp_path / ".hermit")},
        removals=["HERMIT_PROFILE"],
    ):
        assert "HERMIT_PROFILE" not in control.os.environ
        assert control.os.environ["HERMIT_BASE_DIR"] == str(tmp_path / ".hermit")

    assert control.os.environ["HERMIT_PROFILE"] == "codex"
    base_dir = control.ensure_base_dir(tmp_path / ".hermit")
    assert base_dir.exists()
    assert control.config_path(base_dir) == base_dir / "config.toml"


def test_set_default_profile_insert_path_and_missing_profile_error(tmp_path: Path) -> None:
    base_dir = tmp_path / ".hermit"
    base_dir.mkdir()
    config = base_dir / "config.toml"
    config.write_text(
        """
[profiles.default]
provider = "claude"
model = "claude-sonnet"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    path = control.set_default_profile("default", base_dir=base_dir)
    text = path.read_text(encoding="utf-8")
    assert text.startswith('default_profile = "default"')

    with pytest.raises(RuntimeError):
        control.set_default_profile("missing", base_dir=base_dir)


def test_format_toml_value_and_update_profile_setting_paths(tmp_path: Path) -> None:
    base_dir = tmp_path / ".hermit"
    base_dir.mkdir()
    (base_dir / "config.toml").write_text("", encoding="utf-8")

    assert control._profile_section_header("ops") == "[profiles.ops]"  # type: ignore[attr-defined]
    assert control._format_toml_value(True) == "true"  # type: ignore[attr-defined]
    assert control._format_toml_value(7) == "7"  # type: ignore[attr-defined]
    assert control._format_toml_value('say "hi"') == '"say \\"hi\\""'  # type: ignore[attr-defined]
    with pytest.raises(ValueError):
        control._format_toml_value(None)  # type: ignore[attr-defined]

    control.update_profile_setting("ops", "provider", "codex", base_dir=base_dir)
    control.update_profile_setting("ops", "poll_interval", 5, base_dir=base_dir)
    text = (base_dir / "config.toml").read_text(encoding="utf-8")
    assert "[profiles.ops]" in text
    assert 'provider = "codex"' in text
    assert "poll_interval = 5" in text


def test_pid_and_process_helpers_cover_edge_cases(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "missing.pid"
    blank = tmp_path / "blank.pid"
    blank.write_text("", encoding="utf-8")
    valid = tmp_path / "valid.pid"
    valid.write_text("321", encoding="utf-8")

    assert control.read_pid(missing) is None
    assert control.read_pid(blank) is None
    assert control.read_pid(valid) == 321
    assert control.process_exists(None) is False

    monkeypatch.setattr(control.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    assert control.process_exists(321) is False

    monkeypatch.setattr(control.os, "kill", lambda pid, sig: (_ for _ in ()).throw(PermissionError()))
    assert control.process_exists(321) is True

    monkeypatch.setattr(control.os, "kill", lambda pid, sig: None)
    assert control.process_exists(321) is True


def test_command_prefix_and_project_reference_fallbacks(tmp_path: Path, monkeypatch) -> None:
    fake_python = tmp_path / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("", encoding="utf-8")
    fake_hermit = fake_python.parent / "hermit"
    fake_hermit.write_text("", encoding="utf-8")

    monkeypatch.setattr(control, "_project_root", lambda: None)
    monkeypatch.setattr(control.sys, "executable", str(fake_python))
    monkeypatch.setattr(control.shutil, "which", lambda name: None)
    assert control.command_prefix() == [str(fake_hermit)]

    fake_hermit.unlink()
    monkeypatch.setattr(control.shutil, "which", lambda name: "/usr/local/bin/hermit")
    assert control.command_prefix() == ["/usr/local/bin/hermit"]

    monkeypatch.setattr(control.shutil, "which", lambda name: None)
    assert control.command_prefix() == [str(fake_python), "-m", "hermit.main"]

    monkeypatch.setattr(control, "_project_root", lambda: None)
    monkeypatch.setattr(control, "Path", Path)
    monkeypatch.chdir(tmp_path)
    assert control.readme_path() == tmp_path / "README.md"
    assert control.docs_path() == tmp_path / "docs"


def test_run_command_and_service_status_darwin_paths(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(control, "command_prefix", lambda: ["hermit"])
    monkeypatch.setattr(
        control.subprocess,
        "run",
        lambda args, **kwargs: captured.update({"args": args, **kwargs}) or subprocess.CompletedProcess(args, 0, "", ""),
    )

    result = control.run_hermit_command(
        ["serve", "--adapter", "feishu"],
        base_dir=tmp_path / ".hermit",
        profile="codex",
        check=False,
    )
    assert result.returncode == 0
    assert captured["args"] == ["hermit", "serve", "--adapter", "feishu"]
    assert captured["env"]["HERMIT_BASE_DIR"] == str(tmp_path / ".hermit")
    assert captured["env"]["HERMIT_PROFILE"] == "codex"
    assert captured["check"] is False

    pid_file = tmp_path / ".hermit" / "serve-feishu.pid"
    pid_file.parent.mkdir(parents=True)
    pid_file.write_text("999", encoding="utf-8")
    monkeypatch.setattr(control, "read_pid", lambda path: 999)
    monkeypatch.setattr(control, "process_exists", lambda pid: True)
    monkeypatch.setattr(control.sys, "platform", "darwin")
    monkeypatch.setattr("hermit.autostart._plist_path", lambda adapter: tmp_path / "launch.plist")
    monkeypatch.setattr("hermit.autostart._is_loaded", lambda adapter: True)
    (tmp_path / "launch.plist").write_text("", encoding="utf-8")

    status = control.service_status("feishu", base_dir=tmp_path / ".hermit")
    assert status.running is True
    assert status.autostart_installed is True
    assert status.autostart_loaded is True


def test_start_stop_reload_and_extract_preflight_paths(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / ".hermit"
    base_dir.mkdir()

    monkeypatch.setattr(
        control,
        "service_status",
        lambda adapter, base_dir=None: control.ServiceStatus(adapter, Path("pid"), 123, True, False, False),
    )
    assert "already running" in control.start_service("feishu", base_dir=base_dir).lower()

    popen_calls: list[dict[str, object]] = []

    class _FakeProc:
        pass

    statuses = iter(
        [
            control.ServiceStatus("feishu", base_dir / "serve-feishu.pid", None, False, False, False),
            control.ServiceStatus("feishu", base_dir / "serve-feishu.pid", 456, True, False, False),
        ]
    )
    monkeypatch.setattr(control, "command_prefix", lambda: ["python", "-m", "hermit.main"])
    monkeypatch.setattr(
        control.subprocess,
        "Popen",
        lambda *args, **kwargs: popen_calls.append({"args": args, "kwargs": kwargs}) or _FakeProc(),
    )
    monkeypatch.setattr(control, "service_status", lambda adapter, base_dir=None: next(statuses))
    monkeypatch.setattr(control.time, "sleep", lambda _seconds: None)

    started = control.start_service("feishu", base_dir=base_dir, profile="ops")
    assert "Started Hermit service" in started
    assert popen_calls[0]["kwargs"]["env"]["HERMIT_PROFILE"] == "ops"

    no_marker = control._extract_preflight_failure(base_dir / "missing.log")  # type: ignore[attr-defined]
    assert no_marker is None
    plain_log = base_dir / "plain.log"
    plain_log.write_text("nothing here\n", encoding="utf-8")
    assert control._extract_preflight_failure(plain_log) is None  # type: ignore[attr-defined]

    monkeypatch.setattr(
        control,
        "service_status",
        lambda adapter, base_dir=None: control.ServiceStatus(adapter, Path("pid"), None, False, False, False),
    )
    assert "not running" in control.stop_service("feishu", base_dir=base_dir).lower()

    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        control,
        "service_status",
        lambda adapter, base_dir=None: control.ServiceStatus(adapter, Path("pid"), 321, True, False, False),
    )
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    assert "sigterm" in control.stop_service("feishu", base_dir=base_dir).lower()
    assert killed == [(321, control.signal.SIGTERM)]

    reload_calls: list[tuple[list[str], Path | None, str | None]] = []
    monkeypatch.setattr(
        control,
        "run_hermit_command",
        lambda args, base_dir=None, profile=None: reload_calls.append((args, base_dir, profile)),
    )
    assert "reload signal sent" in control.reload_service("feishu", base_dir=base_dir, profile="ops").lower()
    assert reload_calls == [(["reload", "--adapter", "feishu"], base_dir, "ops")]


def test_switch_profile_and_update_profile_bool_cover_reload_and_idle_paths(tmp_path: Path, monkeypatch) -> None:
    base_dir = tmp_path / ".hermit"
    base_dir.mkdir()
    (base_dir / "config.toml").write_text(
        """
default_profile = "default"

[profiles.default]
provider = "claude"
model = "claude-sonnet"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        control,
        "service_status",
        lambda adapter, base_dir=None: control.ServiceStatus(adapter, Path("pid"), 1, False, True, True),
    )
    reloads: list[str] = []
    monkeypatch.setattr(control, "reload_service", lambda adapter, base_dir=None, profile=None: reloads.append(adapter) or "reloaded")

    switched = control.switch_profile("feishu", "default", base_dir=base_dir)
    assert "reloaded launchd-managed" in switched
    assert reloads == ["feishu"]

    monkeypatch.setattr(
        control,
        "service_status",
        lambda adapter, base_dir=None: control.ServiceStatus(adapter, Path("pid"), None, False, False, False),
    )
    switched_idle = control.switch_profile("feishu", "default", base_dir=base_dir)
    assert "config.toml" in switched_idle

    updated_reload = control.update_profile_bool_and_restart(
        "feishu",
        "default",
        "scheduler_enabled",
        True,
        base_dir=base_dir,
    )
    assert "enabled" in updated_reload
    assert "config.toml" in updated_reload


def test_open_helpers_cover_darwin_and_non_darwin(tmp_path: Path, monkeypatch) -> None:
    popen_calls: list[list[str]] = []
    monkeypatch.setattr(control.sys, "platform", "darwin")
    monkeypatch.setattr(control.subprocess, "Popen", lambda args, **kwargs: popen_calls.append(args))

    missing_target = tmp_path / "missing.txt"
    existing_target = tmp_path / "exists.txt"
    existing_target.write_text("ok", encoding="utf-8")

    control.open_path(missing_target)
    control.open_in_textedit(existing_target)
    control.open_url("https://example.com")

    assert popen_calls[0] == ["open", str(missing_target.parent)]
    assert popen_calls[1] == ["open", "-a", "TextEdit", str(existing_target)]
    assert popen_calls[2] == ["open", "https://example.com"]

    monkeypatch.setattr(control.sys, "platform", "linux")
    with pytest.raises(RuntimeError):
        control.open_path(existing_target)
    with pytest.raises(RuntimeError):
        control.open_in_textedit(existing_target)
    with pytest.raises(RuntimeError):
        control.open_url("https://example.com")
