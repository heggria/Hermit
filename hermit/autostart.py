"""macOS launchd auto-start support for Hermit.

Each adapter gets its own LaunchAgent with a unique label
``com.hermit.serve.<adapter>``, so multiple adapters can coexist without
overwriting each other.

Disabled by default; opt-in via ``hermit autostart enable``.
On non-macOS platforms every public function prints an informative message
instead of raising an error.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Optional

_LABEL_PREFIX = "com.hermit.serve"
_LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def _label(adapter: str) -> str:
    return f"{_LABEL_PREFIX}.{adapter}"


def _plist_path(adapter: str) -> Path:
    return _LAUNCH_AGENTS_DIR / f"{_label(adapter)}.plist"


def _find_executable() -> Optional[Path]:
    """Return the absolute path to the ``hermit`` binary.

    Search order:
    1. Same directory as the current Python interpreter (covers venv installs).
    2. PATH via ``shutil.which``.
    """
    candidate = Path(sys.executable).parent / "hermit"
    if candidate.exists():
        return candidate
    found = shutil.which("hermit")
    return Path(found) if found else None


def _build_plist(exe: Path, adapter: str, log_dir: Path) -> str:
    label = _label(adapter)
    stdout_log = log_dir / f"{adapter}-stdout.log"
    stderr_log = log_dir / f"{adapter}-stderr.log"
    return dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
            "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{label}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{exe}</string>
                <string>serve</string>
                <string>--adapter</string>
                <string>{adapter}</string>
            </array>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{stdout_log}</string>
            <key>StandardErrorPath</key>
            <string>{stderr_log}</string>
            <key>WorkingDirectory</key>
            <string>{Path.home()}</string>
        </dict>
        </plist>
    """)


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
    )


def _is_loaded(adapter: str) -> bool:
    return _launchctl("list", _label(adapter)).returncode == 0


def _list_managed_plists() -> list[Path]:
    """Return all Hermit LaunchAgent plist files in ~/Library/LaunchAgents."""
    if not _LAUNCH_AGENTS_DIR.exists():
        return []
    return sorted(_LAUNCH_AGENTS_DIR.glob(f"{_LABEL_PREFIX}.*.plist"))


def enable(adapter: str = "feishu", log_dir: Optional[Path] = None) -> str:
    """Install and load a per-adapter LaunchAgent.

    Calling ``enable`` for two different adapters creates two independent plist
    files with distinct labels — they do not conflict.

    Returns a human-readable status message.
    """
    if sys.platform != "darwin":
        return "Auto-start via launchd is only supported on macOS."

    exe = _find_executable()
    if exe is None:
        return (
            "Cannot find the hermit executable. "
            "Make sure it is installed and available in PATH."
        )

    if log_dir is None:
        log_dir = Path.home() / ".hermit" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    plist = _plist_path(adapter)

    # Reload if already present (update exe path or log dir).
    if plist.exists() and _is_loaded(adapter):
        _launchctl("unload", str(plist))

    plist.write_text(_build_plist(exe, adapter, log_dir), encoding="utf-8")

    result = _launchctl("load", str(plist))
    if result.returncode != 0:
        return f"launchctl load failed:\n{result.stderr.strip()}"

    return (
        f"Auto-start enabled for adapter '{adapter}'.\n"
        f"  Label : {_label(adapter)}\n"
        f"  Plist : {plist}\n"
        f"  Logs  : {log_dir}/{adapter}-{{stdout,stderr}}.log\n"
        f"Hermit will start automatically at next login."
    )


def disable(adapter: str = "feishu") -> str:
    """Unload and remove the LaunchAgent for the given adapter.

    Returns a human-readable status message.
    """
    if sys.platform != "darwin":
        return "Auto-start via launchd is only supported on macOS."

    plist = _plist_path(adapter)
    if not plist.exists():
        return f"Auto-start for '{adapter}' is not configured (plist not found)."

    if _is_loaded(adapter):
        result = _launchctl("unload", str(plist))
        if result.returncode != 0:
            return f"launchctl unload failed:\n{result.stderr.strip()}"

    plist.unlink()
    return f"Auto-start disabled for '{adapter}'.  Plist removed: {plist}"


def status(adapter: Optional[str] = None) -> str:
    """Return a human-readable summary of auto-start state.

    If ``adapter`` is given, show only that adapter.
    Otherwise show all managed LaunchAgents.
    """
    if sys.platform != "darwin":
        return "Auto-start via launchd is only supported on macOS."

    plists = [_plist_path(adapter)] if adapter else _list_managed_plists()

    if not plists or not any(p.exists() for p in plists):
        scope = f"'{adapter}'" if adapter else "any adapter"
        return f"Auto-start: no agents configured for {scope}."

    lines: list[str] = []
    for plist in plists:
        if not plist.exists():
            continue
        # Derive adapter name from plist filename: com.hermit.serve.<adapter>.plist
        stem = plist.stem  # com.hermit.serve.feishu
        adp = stem.removeprefix(f"{_LABEL_PREFIX}.")
        loaded = _is_loaded(adp)
        state = "running" if loaded else "NOT loaded"
        lines.append(f"  [{state:^10}]  {_label(adp)}")
        lines.append(f"               {plist}")

    return "Auto-start agents:\n" + "\n".join(lines)
