from __future__ import annotations

from pathlib import Path

from hermit import autostart


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
        {''.join(f'<string>{arg}</string>' for arg in args)}
    </array>
</dict>
</plist>
""",
        encoding="utf-8",
    )


def test_existing_adapters_detects_current_and_legacy_plists(tmp_path, monkeypatch) -> None:
    launch_agents_dir = tmp_path / "LaunchAgents"
    launch_agents_dir.mkdir()
    monkeypatch.setattr(autostart, "_LAUNCH_AGENTS_DIR", launch_agents_dir)

    _write_plist(
        launch_agents_dir / "com.hermit.serve.feishu.plist",
        "com.hermit.serve.feishu",
        ["/usr/local/bin/hermit", "serve", "feishu"],
    )
    _write_plist(
        launch_agents_dir / "com.moltforge.serve.plist",
        "com.moltforge.serve",
        ["/usr/local/bin/moltforge", "serve", "slack"],
    )

    assert autostart.existing_adapters() == ["feishu", "slack"]


def test_enable_replaces_legacy_plist_for_same_adapter(tmp_path, monkeypatch) -> None:
    launch_agents_dir = tmp_path / "LaunchAgents"
    launch_agents_dir.mkdir()
    log_dir = tmp_path / "logs"
    exe = tmp_path / "bin" / "hermit"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n", encoding="utf-8")

    legacy_plist = launch_agents_dir / "com.moltforge.serve.plist"
    _write_plist(
        legacy_plist,
        "com.moltforge.serve",
        ["/usr/local/bin/moltforge", "serve", "feishu"],
    )

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

    assert "Removed legacy LaunchAgents" in message
    assert not legacy_plist.exists()
    assert (launch_agents_dir / "com.hermit.serve.feishu.plist").exists()
    assert ("load", str(launch_agents_dir / "com.hermit.serve.feishu.plist")) in calls
