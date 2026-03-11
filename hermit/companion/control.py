from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from hermit.provider.profiles import load_profile_catalog


@dataclass
class ServiceStatus:
    adapter: str
    pid_file: Path
    pid: int | None
    running: bool
    autostart_installed: bool
    autostart_loaded: bool


def hermit_base_dir() -> Path:
    raw = os.environ.get("HERMIT_BASE_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".hermit"


def hermit_log_dir(base_dir: Path | None = None) -> Path:
    root = base_dir or hermit_base_dir()
    return root / "logs"


def companion_log_path(base_dir: Path | None = None) -> Path:
    return hermit_log_dir(base_dir) / "companion.log"


def log_companion_event(
    action: str,
    message: str,
    *,
    base_dir: Path | None = None,
    level: str = "INFO",
    detail: str | None = None,
) -> Path:
    path = companion_log_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"[{timestamp}] {level.upper()} {action}: {message}"]
    if detail:
        lines.append(detail.rstrip())
    if not path.exists():
        path.touch()
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n\n")
    return path


def format_exception_message(exc: Exception) -> tuple[str, str | None]:
    if isinstance(exc, subprocess.CalledProcessError):
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        detail_parts = []
        if stdout:
            detail_parts.append(f"stdout:\n{stdout}")
        if stderr:
            detail_parts.append(f"stderr:\n{stderr}")
        message = stderr or stdout or str(exc)
        return message, "\n\n".join(detail_parts) or None
    return str(exc), traceback.format_exc()


def config_path(base_dir: Path | None = None) -> Path:
    root = base_dir or hermit_base_dir()
    return root / "config.toml"


def ensure_base_dir(base_dir: Path | None = None) -> Path:
    root = base_dir or hermit_base_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_config_file(base_dir: Path | None = None) -> Path:
    root = ensure_base_dir(base_dir)
    path = config_path(root)
    if not path.exists():
        path.write_text(
            "\n".join(
                [
                    "# Hermit profile catalog",
                    'default_profile = "default"',
                    "",
                    "[profiles.default]",
                    'provider = "claude"',
                    'model = "claude-3-7-sonnet-latest"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return path


@contextmanager
def _temporary_env(
    *,
    updates: dict[str, str] | None = None,
    removals: list[str] | None = None,
) -> Iterator[None]:
    previous: dict[str, str | None] = {}
    for key in removals or []:
        previous[key] = os.environ.get(key)
        os.environ.pop(key, None)
    for key, value in (updates or {}).items():
        previous.setdefault(key, os.environ.get(key))
        os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def load_runtime_settings(base_dir: Path | None = None):
    from hermit.config import Settings

    resolved_base_dir = (base_dir or hermit_base_dir()).expanduser()
    env_file = resolved_base_dir / ".env"
    with _temporary_env(
        updates={"HERMIT_BASE_DIR": str(resolved_base_dir)},
        removals=["HERMIT_PROFILE"],
    ):
        return Settings(base_dir=resolved_base_dir, _env_file=env_file)


def load_profile_runtime_settings(profile_name: str, base_dir: Path | None = None):
    from hermit.config import Settings

    resolved_base_dir = (base_dir or hermit_base_dir()).expanduser()
    env_file = resolved_base_dir / ".env"
    with _temporary_env(
        updates={
            "HERMIT_BASE_DIR": str(resolved_base_dir),
            "HERMIT_PROFILE": profile_name,
        },
    ):
        return Settings(base_dir=resolved_base_dir, profile=profile_name, _env_file=env_file)


def set_default_profile(profile_name: str, *, base_dir: Path | None = None) -> Path:
    resolved_base_dir = (base_dir or hermit_base_dir()).expanduser()
    catalog = load_profile_catalog(resolved_base_dir)
    if profile_name not in catalog.profiles:
        raise RuntimeError(f"Profile '{profile_name}' is not defined in {catalog.path}.")

    path = ensure_config_file(resolved_base_dir)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    default_line = f'default_profile = "{profile_name}"'
    lines = text.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().startswith("default_profile"):
            lines[index] = default_line
            replaced = True
            break
    if replaced:
        new_text = "\n".join(lines).rstrip() + "\n"
    else:
        body = text.lstrip("\n")
        new_text = f"{default_line}\n\n{body}" if body else f"{default_line}\n"
    path.write_text(new_text, encoding="utf-8")
    return path


def _profile_section_header(profile_name: str) -> str:
    return f"[profiles.{profile_name}]"


def _format_toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if value is None:
        raise ValueError("None is not supported for TOML scalar updates.")
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def update_profile_setting(
    profile_name: str,
    key: str,
    value: object,
    *,
    base_dir: Path | None = None,
) -> Path:
    resolved_base_dir = (base_dir or hermit_base_dir()).expanduser()
    path = ensure_config_file(resolved_base_dir)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    header = _profile_section_header(profile_name)
    lines = text.splitlines()
    rendered = f"{key} = {_format_toml_value(value)}"

    section_start: int | None = None
    section_end = len(lines)
    for index, line in enumerate(lines):
        if line.strip() == header:
            section_start = index
            for inner_index in range(index + 1, len(lines)):
                stripped = lines[inner_index].strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    section_end = inner_index
                    break
            break

    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([header, rendered])
    else:
        replaced = False
        for index in range(section_start + 1, section_end):
            stripped = lines[index].strip()
            if stripped.startswith(f"{key} "):
                lines[index] = rendered
                replaced = True
                break
        if not replaced:
            lines.insert(section_end, rendered)

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def pid_path(adapter: str, base_dir: Path | None = None) -> Path:
    root = base_dir or hermit_base_dir()
    return root / f"serve-{adapter}.pid"


def read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def process_exists(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def command_prefix() -> list[str]:
    project_root = _project_root()
    if project_root is not None:
        return [
            "/opt/homebrew/bin/uv",
            "run",
            "--project",
            str(project_root),
            "--python",
            "3.11",
            "python",
            "-m",
            "hermit.main",
        ]
    hermit_bin = Path(sys.executable).parent / "hermit"
    if hermit_bin.exists():
        return [str(hermit_bin)]
    installed = shutil.which("hermit")
    if installed:
        return [installed]
    return [sys.executable, "-m", "hermit.main"]


def _project_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").exists():
        return candidate
    return None


def readme_path() -> Path:
    project_root = _project_root()
    if project_root is not None:
        return project_root / "README.md"
    return Path.cwd() / "README.md"


def docs_path() -> Path:
    project_root = _project_root()
    if project_root is not None:
        return project_root / "docs"
    return Path.cwd() / "docs"


def project_repo_url() -> str:
    return "https://github.com/heggria/Hermit"


def project_wiki_url() -> str:
    return f"{project_repo_url()}/wiki"


def run_hermit_command(
    args: list[str],
    *,
    base_dir: Path | None = None,
    profile: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if base_dir is not None:
        env["HERMIT_BASE_DIR"] = str(base_dir)
    if profile:
        env["HERMIT_PROFILE"] = profile
    return subprocess.run(
        [*command_prefix(), *args],
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def service_status(adapter: str, *, base_dir: Path | None = None) -> ServiceStatus:
    from hermit import autostart as hermit_autostart

    resolved_base_dir = base_dir or hermit_base_dir()
    current_pid_path = pid_path(adapter, resolved_base_dir)
    pid = read_pid(current_pid_path)
    autostart_installed = False
    autostart_loaded = False
    if sys.platform == "darwin":
        plist_path = hermit_autostart._plist_path(adapter)
        autostart_installed = plist_path.exists()
        if autostart_installed:
            autostart_loaded = hermit_autostart._is_loaded(adapter)
    return ServiceStatus(
        adapter=adapter,
        pid_file=current_pid_path,
        pid=pid,
        running=process_exists(pid),
        autostart_installed=autostart_installed,
        autostart_loaded=autostart_loaded,
    )


def start_service(
    adapter: str,
    *,
    base_dir: Path | None = None,
    profile: str | None = None,
) -> str:
    resolved_base_dir = base_dir or hermit_base_dir()
    status = service_status(adapter, base_dir=resolved_base_dir)
    if status.running:
        return f"Hermit service is already running for '{adapter}' (PID {status.pid})."

    log_dir = hermit_log_dir(resolved_base_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{adapter}-menubar-stdout.log"
    stderr_path = log_dir / f"{adapter}-menubar-stderr.log"
    env = os.environ.copy()
    env["HERMIT_BASE_DIR"] = str(resolved_base_dir)
    if profile:
        env["HERMIT_PROFILE"] = profile

    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        subprocess.Popen(
            [*command_prefix(), "serve", "--adapter", adapter],
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    for _ in range(20):
        time.sleep(0.1)
        current_status = service_status(adapter, base_dir=resolved_base_dir)
        if current_status.running:
            return f"Started Hermit service for '{adapter}' (PID {current_status.pid}). Logs: {log_dir}"

    failure_detail = _extract_preflight_failure(stdout_path)
    if failure_detail:
        return (
            f"Failed to start Hermit service for '{adapter}'. "
            f"{failure_detail} Logs: {stdout_path} / {stderr_path}"
        )
    return (
        f"Failed to start Hermit service for '{adapter}'. Check logs: {stdout_path} / {stderr_path}"
    )


def _extract_preflight_failure(stdout_path: Path) -> str | None:
    try:
        text = stdout_path.read_text(encoding="utf-8")
    except OSError:
        return None
    marker = "启动前检查未通过："
    index = text.rfind(marker)
    if index == -1:
        return None
    tail = text[index + len(marker) :]
    lines: list[str] = []
    for raw_line in tail.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            if lines:
                break
            continue
        if not stripped.startswith("-"):
            if lines:
                break
            continue
        lines.append(stripped.removeprefix("-").strip())
    if not lines:
        return None
    return "Preflight failed: " + " ".join(lines)


def stop_service(adapter: str, *, base_dir: Path | None = None) -> str:
    current_status = service_status(adapter, base_dir=base_dir)
    if not current_status.running or current_status.pid is None:
        return f"Hermit service is not running for '{adapter}'."
    os.kill(current_status.pid, signal.SIGTERM)
    return f"Sent SIGTERM to Hermit service for '{adapter}' (PID {current_status.pid})."


def reload_service(
    adapter: str, *, base_dir: Path | None = None, profile: str | None = None
) -> str:
    run_hermit_command(["reload", "--adapter", adapter], base_dir=base_dir, profile=profile)
    return f"Reload signal sent for '{adapter}'."


def switch_profile(adapter: str, profile_name: str, *, base_dir: Path | None = None) -> str:
    resolved_base_dir = (base_dir or hermit_base_dir()).expanduser()
    set_default_profile(profile_name, base_dir=resolved_base_dir)
    status = service_status(adapter, base_dir=resolved_base_dir)
    if status.autostart_loaded:
        reload_service(adapter, base_dir=resolved_base_dir)
        return (
            f"Switched default profile to '{profile_name}' in {resolved_base_dir / 'config.toml'} "
            f"and reloaded launchd-managed '{adapter}'."
        )
    if status.running:
        stop_service(adapter, base_dir=resolved_base_dir)
        for _ in range(20):
            time.sleep(0.1)
            if not service_status(adapter, base_dir=resolved_base_dir).running:
                break
        start_message = start_service(adapter, base_dir=resolved_base_dir)
        return f"Switched default profile to '{profile_name}'. {start_message}"
    return f"Switched default profile to '{profile_name}' in {resolved_base_dir / 'config.toml'}."


def update_profile_bool_and_restart(
    adapter: str,
    profile_name: str,
    key: str,
    enabled: bool,
    *,
    base_dir: Path | None = None,
) -> str:
    resolved_base_dir = (base_dir or hermit_base_dir()).expanduser()
    update_profile_setting(profile_name, key, enabled, base_dir=resolved_base_dir)
    status = service_status(adapter, base_dir=resolved_base_dir)
    state_text = "enabled" if enabled else "disabled"
    if status.autostart_loaded:
        reload_service(adapter, base_dir=resolved_base_dir)
        return (
            f"Set '{key}' to {state_text} for profile '{profile_name}' "
            f"and reloaded launchd-managed '{adapter}'."
        )
    if status.running:
        stop_service(adapter, base_dir=resolved_base_dir)
        for _ in range(20):
            time.sleep(0.1)
            if not service_status(adapter, base_dir=resolved_base_dir).running:
                break
        start_message = start_service(adapter, base_dir=resolved_base_dir)
        return f"Set '{key}' to {state_text} for profile '{profile_name}'. {start_message}"
    return (
        f"Set '{key}' to {state_text} for profile '{profile_name}' in "
        f"{resolved_base_dir / 'config.toml'}."
    )


def open_path(path: Path) -> None:
    if sys.platform == "darwin":
        target = path if path.exists() else path.parent
        subprocess.Popen(
            ["open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return
    raise RuntimeError("Opening paths is only implemented for macOS.")


def open_in_textedit(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(
            ["open", "-a", "TextEdit", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    raise RuntimeError("Opening TextEdit is only implemented for macOS.")


def open_url(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    raise RuntimeError("Opening URLs is only implemented for macOS.")
