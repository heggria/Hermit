"""macOS launchd auto-start support for Hermit.

Each adapter gets its own LaunchAgent with a unique label
``com.hermit.serve.<adapter>``, so multiple adapters can coexist without
overwriting each other.

Disabled by default; opt-in via ``hermit autostart enable``.
On non-macOS platforms every public function prints an informative message
instead of raising an error.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any, cast

from hermit.infra.system.i18n import tr

_LABEL_PREFIX = "com.hermit.serve"
_LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def _t(message_key: str, default: str | None = None, **kwargs: Any) -> str:
    return tr(message_key, default=default, **kwargs)


def _label(adapter: str) -> str:
    base_dir = _current_base_dir()
    suffix = _base_dir_label_suffix(base_dir)
    if suffix:
        return f"{_LABEL_PREFIX}.{suffix}.{adapter}"
    return f"{_LABEL_PREFIX}.{adapter}"


def _current_base_dir() -> Path:
    raw = os.environ.get("HERMIT_BASE_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".hermit"


def _base_dir_label_suffix(base_dir: Path) -> str:
    resolved = base_dir.expanduser()
    default_base_dir = Path.home() / ".hermit"
    if resolved == default_base_dir:
        return ""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", resolved.name).strip("-").lower()
    return slug or "custom"


def _plist_path(adapter: str) -> Path:
    return _LAUNCH_AGENTS_DIR / f"{_label(adapter)}.plist"


def _find_executable() -> Path | None:
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


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
    )


def _is_loaded(adapter: str) -> bool:
    return _launchctl("list", _label(adapter)).returncode == 0


def _plist_program_arguments(plist: Path) -> list[str]:
    try:
        data: dict[str, Any] = plistlib.loads(plist.read_bytes())
    except Exception:
        return []
    args = data.get("ProgramArguments")
    if not isinstance(args, list):
        return []
    return [str(arg) for arg in cast(list[Any], args)]


def _adapter_from_program_arguments(args: list[str]) -> str | None:
    if not args:
        return None
    if "--adapter" in args:
        idx = args.index("--adapter")
        if idx + 1 < len(args):
            return args[idx + 1]
    if len(args) >= 3 and args[1] == "serve":
        return args[2]
    return None


def _list_managed_plists() -> list[Path]:
    """Return all Hermit LaunchAgent plist files in ~/Library/LaunchAgents."""
    if not _LAUNCH_AGENTS_DIR.exists():
        return []
    return sorted(_LAUNCH_AGENTS_DIR.glob(f"{_LABEL_PREFIX}*.plist"))


def existing_adapters() -> list[str]:
    """Return adapters discovered from current Hermit LaunchAgent plists."""
    adapters: set[str] = set()
    for plist in _list_managed_plists():
        args = _plist_program_arguments(plist)
        adapter = _adapter_from_program_arguments(args)
        if adapter:
            adapters.add(adapter)
    return sorted(adapters)


def enable(adapter: str = "feishu", log_dir: Path | None = None) -> str:
    """Install and load a per-adapter LaunchAgent.

    Calling ``enable`` for two different adapters creates two independent plist
    files with distinct labels — they do not conflict.

    Returns a human-readable status message.
    """
    if sys.platform != "darwin":
        return _t(
            "autostart.common.mac_only",
            "Auto-start via launchd is only supported on macOS.",
        )

    exe = _find_executable()
    if exe is None:
        return _t(
            "autostart.enable.missing_executable",
            ("Cannot find the hermit executable. Make sure it is installed and available in PATH."),
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
        return _t(
            "autostart.enable.load_failed",
            "launchctl load failed:\n{stderr}",
            stderr=result.stderr.strip(),
        )

    message = _t(
        "autostart.enable.done",
        (
            "Auto-start enabled for adapter '{adapter}'.\n"
            "  Label : {label}\n"
            "  Plist : {plist}\n"
            "  Logs  : {logs}/{adapter}-{{stdout,stderr}}.log\n"
            "Hermit will start automatically at next login."
        ),
        adapter=adapter,
        label=_label(adapter),
        plist=plist,
        logs=log_dir,
    )
    return message


def disable(adapter: str = "feishu") -> str:
    """Unload and remove the LaunchAgent for the given adapter.

    Returns a human-readable status message.
    """
    if sys.platform != "darwin":
        return _t(
            "autostart.common.mac_only",
            "Auto-start via launchd is only supported on macOS.",
        )

    plist = _plist_path(adapter)
    if not plist.exists():
        return _t(
            "autostart.disable.not_configured",
            "Auto-start for '{adapter}' is not configured (plist not found).",
            adapter=adapter,
        )

    if _is_loaded(adapter):
        result = _launchctl("unload", str(plist))
        if result.returncode != 0:
            return _t(
                "autostart.disable.unload_failed",
                "launchctl unload failed:\n{stderr}",
                stderr=result.stderr.strip(),
            )

    plist.unlink()
    return _t(
        "autostart.disable.done",
        "Auto-start disabled for '{adapter}'.  Plist removed: {plist}",
        adapter=adapter,
        plist=plist,
    )


def status(adapter: str | None = None) -> str:
    """Return a human-readable summary of auto-start state.

    If ``adapter`` is given, show only that adapter.
    Otherwise show all managed LaunchAgents.
    """
    if sys.platform != "darwin":
        return _t(
            "autostart.common.mac_only",
            "Auto-start via launchd is only supported on macOS.",
        )

    plists = [_plist_path(adapter)] if adapter else _list_managed_plists()

    if not plists or not any(p.exists() for p in plists):
        scope = f"'{adapter}'" if adapter else "any adapter"
        return _t(
            "autostart.status.empty",
            "Auto-start: no agents configured for {scope}.",
            scope=scope,
        )

    lines: list[str] = []
    for plist in plists:
        if not plist.exists():
            continue
        args = _plist_program_arguments(plist)
        adp = _adapter_from_program_arguments(args)
        if not adp:
            continue
        loaded = _is_loaded(adp)
        state = (
            _t("autostart.status.state.running", "running")
            if loaded
            else _t("autostart.status.state.not_loaded", "NOT loaded")
        )
        label = plist.stem
        lines.append(f"  [{state:^10}]  {label}")
        lines.append(f"               {plist}")

    return _t("autostart.status.header", "Auto-start agents:\n") + "\n".join(lines)
