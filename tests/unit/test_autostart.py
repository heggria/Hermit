from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from hermit.surfaces.cli import autostart


@pytest.fixture(autouse=True)
def _clear_base_dir(monkeypatch):
    monkeypatch.delenv("HERMIT_BASE_DIR", raising=False)


def _write_plist(path: Path, label: str, args: list[str]) -> None:
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        {"".join(f"<string>{arg}</string>" for arg in args)}
    </array>
</dict>
</plist>
""",
        encoding="utf-8",
    )


def test_existing_adapters_detects_current_managed_plists(tmp_path, monkeypatch) -> None:
    launch_agents_dir = tmp_path / "LaunchAgents"
    launch_agents_dir.mkdir()
    monkeypatch.setattr(autostart, "_LAUNCH_AGENTS_DIR", launch_agents_dir)

    _write_plist(
        launch_agents_dir / "com.hermit.serve.feishu.plist",
        "com.hermit.serve.feishu",
        ["/usr/local/bin/hermit", "serve", "feishu"],
    )
    _write_plist(
        launch_agents_dir / "com.hermit.serve.slack.plist",
        "com.hermit.serve.slack",
        ["/usr/local/bin/hermit", "serve", "slack"],
    )

    assert autostart.existing_adapters() == ["feishu", "slack"]


def test_enable_writes_managed_plist_for_same_adapter(tmp_path, monkeypatch) -> None:
    launch_agents_dir = tmp_path / "LaunchAgents"
    launch_agents_dir.mkdir()
    log_dir = tmp_path / "logs"
    exe = tmp_path / "bin" / "hermit"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(autostart, "_LAUNCH_AGENTS_DIR", launch_agents_dir)
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    monkeypatch.setattr(autostart, "_find_executable", lambda: exe)

    calls: list[tuple[str, ...]] = []

    class _Result:
        def __init__(self, returncode: int = 0, stderr: str = "") -> None:
            self.returncode = returncode
            self.stderr = stderr

    def fake_launchctl(*args: str) -> _Result:
        calls.append(args)
        if args[:2] == ("list", "com.hermit.serve.feishu"):
            return _Result(returncode=1)
        return _Result()

    monkeypatch.setattr(autostart, "_launchctl", fake_launchctl)

    message = autostart.enable(adapter="feishu", log_dir=log_dir)

    assert "Removed legacy LaunchAgents" not in message
    assert (launch_agents_dir / "com.hermit.serve.feishu.plist").exists()
    assert ("load", str(launch_agents_dir / "com.hermit.serve.feishu.plist")) in calls


def test_enable_uses_base_dir_suffix_for_non_default_workspace(tmp_path, monkeypatch) -> None:
    launch_agents_dir = tmp_path / "LaunchAgents"
    launch_agents_dir.mkdir()
    log_dir = tmp_path / "logs"
    exe = tmp_path / "bin" / "hermit"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(autostart, "_LAUNCH_AGENTS_DIR", launch_agents_dir)
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    monkeypatch.setattr(autostart, "_find_executable", lambda: exe)
    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit-dev"))

    class _Result:
        def __init__(self, returncode: int = 0, stderr: str = "") -> None:
            self.returncode = returncode
            self.stderr = stderr

    monkeypatch.setattr(autostart, "_launchctl", lambda *args: _Result())

    message = autostart.enable(adapter="feishu", log_dir=log_dir)

    assert "com.hermit.serve.hermit-dev.feishu" in message
    assert (launch_agents_dir / "com.hermit.serve.hermit-dev.feishu.plist").exists()


def test_autostart_helper_functions_cover_edge_cases(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HERMIT_BASE_DIR", raising=False)
    assert autostart._current_base_dir() == Path.home() / ".hermit"

    monkeypatch.setenv("HERMIT_BASE_DIR", str(tmp_path / ".hermit-dev"))
    assert autostart._current_base_dir() == tmp_path / ".hermit-dev"
    assert autostart._base_dir_label_suffix(Path.home() / ".hermit") == ""
    assert autostart._base_dir_label_suffix(tmp_path / ".hermit dev") == "hermit-dev"
    assert autostart._base_dir_label_suffix(tmp_path / "!!!") == "custom"

    launch_agents_dir = tmp_path / "LaunchAgents"
    launch_agents_dir.mkdir()
    monkeypatch.setattr(autostart, "_LAUNCH_AGENTS_DIR", launch_agents_dir)
    assert (
        autostart._adapter_from_program_arguments(["hermit", "serve", "--adapter", "slack"])
        == "slack"
    )
    assert autostart._adapter_from_program_arguments(["hermit", "serve", "discord"]) == "discord"
    assert autostart._adapter_from_program_arguments([]) is None


def test_find_executable_prefers_venv_binary_then_path(tmp_path, monkeypatch) -> None:
    python_bin = tmp_path / "bin" / "python3"
    hermit_bin = tmp_path / "bin" / "hermit"
    hermit_bin.parent.mkdir(parents=True)
    python_bin.write_text("", encoding="utf-8")
    hermit_bin.write_text("", encoding="utf-8")
    monkeypatch.setattr(autostart.sys, "executable", str(python_bin))

    assert autostart._find_executable() == hermit_bin

    hermit_bin.unlink()
    monkeypatch.setattr(autostart.shutil, "which", lambda name: "/usr/local/bin/hermit")

    assert autostart._find_executable() == Path("/usr/local/bin/hermit")

    monkeypatch.setattr(autostart.shutil, "which", lambda name: None)

    assert autostart._find_executable() is None


def test_plist_program_arguments_handles_invalid_payloads(tmp_path) -> None:
    invalid_plist = tmp_path / "invalid.plist"
    invalid_plist.write_text("not a plist", encoding="utf-8")
    scalar_plist = tmp_path / "scalar.plist"
    scalar_plist.write_bytes(plistlib.dumps({"ProgramArguments": "hermit"}))

    assert autostart._plist_program_arguments(invalid_plist) == []
    assert autostart._plist_program_arguments(scalar_plist) == []


def test_enable_handles_non_macos_missing_executable_and_load_failure(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    assert autostart.enable() == "Auto-start via launchd is only supported on macOS."

    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    monkeypatch.setattr(autostart, "_find_executable", lambda: None)
    assert "Cannot find the hermit executable" in autostart.enable()

    launch_agents_dir = tmp_path / "LaunchAgents"
    launch_agents_dir.mkdir()
    exe = tmp_path / "bin" / "hermit"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n", encoding="utf-8")

    class _Result:
        def __init__(self, returncode: int = 0, stderr: str = "") -> None:
            self.returncode = returncode
            self.stderr = stderr

    monkeypatch.setattr(autostart, "_LAUNCH_AGENTS_DIR", launch_agents_dir)
    monkeypatch.setattr(autostart, "_find_executable", lambda: exe)
    monkeypatch.setattr(autostart, "_launchctl", lambda *args: _Result(returncode=1, stderr="boom"))

    assert "launchctl load failed" in autostart.enable(log_dir=tmp_path / "logs")


def test_enable_reloads_existing_loaded_plist(tmp_path, monkeypatch) -> None:
    launch_agents_dir = tmp_path / "LaunchAgents"
    launch_agents_dir.mkdir()
    log_dir = tmp_path / "logs"
    exe = tmp_path / "bin" / "hermit"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(autostart, "_LAUNCH_AGENTS_DIR", launch_agents_dir)
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    monkeypatch.setattr(autostart, "_find_executable", lambda: exe)
    existing_plist = launch_agents_dir / "com.hermit.serve.feishu.plist"
    existing_plist.write_text("old", encoding="utf-8")

    calls: list[tuple[str, ...]] = []

    class _Result:
        def __init__(self, returncode: int = 0, stderr: str = "") -> None:
            self.returncode = returncode
            self.stderr = stderr

    def fake_launchctl(*args: str) -> _Result:
        calls.append(args)
        if args[:2] == ("list", "com.hermit.serve.feishu"):
            return _Result(returncode=0)
        return _Result()

    monkeypatch.setattr(autostart, "_launchctl", fake_launchctl)

    autostart.enable(log_dir=log_dir)

    assert ("unload", str(existing_plist)) in calls
    assert ("load", str(existing_plist)) in calls


def test_disable_and_status_cover_loaded_unloaded_and_missing_cases(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    launch_agents_dir = tmp_path / "LaunchAgents"
    launch_agents_dir.mkdir()
    monkeypatch.setattr(autostart, "_LAUNCH_AGENTS_DIR", launch_agents_dir)

    missing_message = autostart.disable("feishu")
    assert "plist not found" in missing_message
    assert autostart.status() == "Auto-start: no agents configured for any adapter."

    plist = launch_agents_dir / "com.hermit.serve.feishu.plist"
    _write_plist(
        plist,
        "com.hermit.serve.feishu",
        ["/usr/local/bin/hermit", "serve", "--adapter", "feishu"],
    )

    class _Result:
        def __init__(self, returncode: int = 0, stderr: str = "") -> None:
            self.returncode = returncode
            self.stderr = stderr

    unload_failure = iter(
        [
            _Result(returncode=0),  # status() list
            _Result(returncode=0),  # disable() list
            _Result(returncode=1, stderr="cannot unload"),
        ]
    )
    monkeypatch.setattr(autostart, "_launchctl", lambda *args: next(unload_failure))

    status_text = autostart.status("feishu")
    assert "running" in status_text
    assert "com.hermit.serve.feishu" in status_text
    assert "launchctl unload failed" in autostart.disable("feishu")

    monkeypatch.setattr(autostart, "_launchctl", lambda *args: _Result(returncode=1, stderr=""))
    assert "Auto-start disabled" in autostart.disable("feishu")
    assert not plist.exists()


def test_public_functions_return_non_macos_message(monkeypatch) -> None:
    monkeypatch.setattr(autostart.sys, "platform", "linux")

    assert autostart.disable() == "Auto-start via launchd is only supported on macOS."
    assert autostart.status() == "Auto-start via launchd is only supported on macOS."


def test_public_functions_can_render_zh_cn(monkeypatch) -> None:
    monkeypatch.setenv("HERMIT_LOCALE", "zh-CN")
    monkeypatch.setattr(autostart.sys, "platform", "linux")

    assert autostart.enable() == "launchd 自启动仅支持 macOS。"
