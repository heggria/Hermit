#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from watchfiles import Change, watch

from hermit.apps.companion.control import (
    matching_process_pids,
    process_exists,
    read_pid,
    watch_pid_path,
)
from hermit.apps.companion.control import (
    pid_path as serve_pid_path,
)
from hermit.infra.system.executables import resolve_uv_bin

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_WATCH_PATHS = ["src/hermit", "scripts", "pyproject.toml"]
IGNORED_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".DS_Store",
}
IGNORED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".swp",
    ".tmp",
}

APP_PATHS = {
    "prod": Path.home() / "Applications" / "Hermit.app",
    "dev": Path.home() / "Applications" / "Hermit Dev.app",
    "test": Path.home() / "Applications" / "Hermit Test.app",
}
BASE_DIRS = {
    "prod": Path.home() / ".hermit",
    "dev": Path.home() / ".hermit-dev",
    "test": Path.home() / ".hermit-test",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch the Hermit source tree and restart a dev serve process on changes.",
    )
    parser.add_argument("env", choices=["prod", "dev", "test"], help="Target Hermit environment.")
    parser.add_argument(
        "--adapter",
        default="feishu",
        help="Adapter passed to `hermit serve`.",
    )
    parser.add_argument(
        "--debounce-ms",
        type=int,
        default=700,
        help="Debounce window for file events.",
    )
    parser.add_argument(
        "--watch",
        action="append",
        dest="watch_paths",
        default=[],
        help="Extra path to watch, relative to repo root or absolute.",
    )
    parser.add_argument(
        "--no-menubar",
        action="store_true",
        help="Do not launch the macOS menubar companion.",
    )
    parser.add_argument(
        "--hard",
        action="store_true",
        help="Use kill+respawn instead of SIGHUP for reload (fallback mode).",
    )
    return parser.parse_args()


def _resolve_watch_paths(extra_paths: list[str]) -> list[Path]:
    raw_paths = DEFAULT_WATCH_PATHS + extra_paths
    resolved: list[Path] = []
    for raw in raw_paths:
        candidate = Path(raw)
        path = candidate if candidate.is_absolute() else ROOT_DIR / candidate
        if path.exists():
            resolved.append(path)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in resolved:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _should_watch(_change: Change, path: str) -> bool:
    file_path = Path(path)
    if any(part in IGNORED_PARTS for part in file_path.parts):
        return False
    return file_path.suffix not in IGNORED_SUFFIXES


def _format_paths(changes: set[tuple[Change, str]]) -> str:
    labels: list[str] = []
    for _change, raw_path in sorted(changes, key=lambda item: item[1]):
        path = Path(raw_path)
        try:
            path = path.relative_to(ROOT_DIR)
        except ValueError:
            pass
        labels.append(str(path))
    return ", ".join(labels[:5]) + (" ..." if len(labels) > 5 else "")


def _spawn(env_name: str, adapter: str) -> subprocess.Popen[str]:
    cmd = [
        str(ROOT_DIR / "scripts" / "hermit-env.sh"),
        env_name,
        "serve",
        "--adapter",
        adapter,
    ]
    return subprocess.Popen(
        cmd,
        cwd=ROOT_DIR,
        text=True,
    )


def _base_dir(env_name: str) -> Path:
    return BASE_DIRS[env_name]


def _remove_pid_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _ensure_single_watcher(env_name: str, adapter: str) -> Path:
    pid_file = watch_pid_path(adapter, _base_dir(env_name))
    existing_pid = read_pid(pid_file)
    if existing_pid is not None and existing_pid != os.getpid():
        if process_exists(existing_pid):
            print(
                f"Watcher already running for env='{env_name}' adapter='{adapter}' (PID {existing_pid}).",
                file=sys.stderr,
                flush=True,
            )
            raise SystemExit(1)
        print(f"Removing stale watcher PID file: {pid_file} (PID {existing_pid})", flush=True)
        _remove_pid_file(pid_file)

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(_remove_pid_file, pid_file)
    return pid_file


def _wait_for_exit(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return True
        time.sleep(0.1)
    return not process_exists(pid)


def _take_over_existing_service(env_name: str, adapter: str) -> None:
    pid_file = serve_pid_path(adapter, _base_dir(env_name))
    existing_pid = read_pid(pid_file)
    if existing_pid is None:
        return
    if not process_exists(existing_pid):
        print(f"Removing stale serve PID file: {pid_file} (PID {existing_pid})", flush=True)
        _remove_pid_file(pid_file)
        return

    print(
        f"Taking over existing service for env='{env_name}' adapter='{adapter}' (PID {existing_pid})...",
        flush=True,
    )
    os.kill(existing_pid, signal.SIGTERM)
    if _wait_for_exit(existing_pid):
        return
    print(f"Service PID {existing_pid} did not exit in time; sending SIGKILL.", flush=True)
    os.kill(existing_pid, signal.SIGKILL)
    _wait_for_exit(existing_pid, timeout=1.0)


def _menubar_running(env_name: str, adapter: str) -> bool:
    matches = matching_process_pids(
        f"-m hermit.apps.companion.menubar --adapter {adapter}",
        base_dir=_base_dir(env_name),
    )
    return bool(matches)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=ROOT_DIR, check=True)


def _ensure_macos_deps() -> None:
    _run([resolve_uv_bin(), "sync", "--group", "dev", "--extra", "macos"])


def _ensure_menubar(env_name: str, adapter: str) -> None:
    if sys.platform != "darwin":
        return
    if _menubar_running(env_name, adapter):
        return
    app_path = APP_PATHS[env_name]
    _ensure_macos_deps()
    if not app_path.exists():
        _run(
            [
                str(ROOT_DIR / "scripts" / "hermit-menubar-install-env.sh"),
                env_name,
                "--adapter",
                adapter,
            ]
        )
    env = os.environ.copy()
    env["HERMIT_BASE_DIR"] = str(_base_dir(env_name))
    subprocess.run(["open", "-na", str(app_path)], cwd=ROOT_DIR, check=True, env=env)


def _stop(proc: subprocess.Popen[str] | None, timeout: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)


def main() -> int:
    args = parse_args()
    watch_paths = _resolve_watch_paths(args.watch_paths)
    if not watch_paths:
        print("No existing watch paths found.", file=sys.stderr)
        return 1
    _ensure_single_watcher(args.env, args.adapter)

    current: subprocess.Popen[str] | None = None
    stopping = False

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True
        print(f"\nStopping watcher on signal {signum}...", flush=True)
        _stop(current)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(
        f"Watching {', '.join(str(path.relative_to(ROOT_DIR)) if path.is_relative_to(ROOT_DIR) else str(path) for path in watch_paths)}",  # type: ignore[attr-defined]
        flush=True,
    )
    print(
        f"Starting Hermit dev service for env='{args.env}' adapter='{args.adapter}'",
        flush=True,
    )
    _take_over_existing_service(args.env, args.adapter)
    if not args.no_menubar:
        print("Ensuring menubar companion is running...", flush=True)
        _ensure_menubar(args.env, args.adapter)
    current = _spawn(args.env, args.adapter)

    try:
        for changes in watch(
            *watch_paths,
            debounce=args.debounce_ms,
            watch_filter=_should_watch,
            raise_interrupt=False,
        ):
            if stopping:
                break
            print(f"\nChange detected: {_format_paths(changes)}", flush=True)

            if current is not None and current.poll() is not None:
                print(
                    f"Serve process exited with code {current.returncode}; respawning.",
                    flush=True,
                )
                current = _spawn(args.env, args.adapter)
                continue

            if args.hard:
                _stop(current)
                time.sleep(0.2)
                print("Hard-restarting Hermit dev service...", flush=True)
                current = _spawn(args.env, args.adapter)
            else:
                print("Sending SIGHUP for graceful reload...", flush=True)
                assert current is not None
                os.kill(current.pid, signal.SIGHUP)
    finally:
        _stop(current)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
