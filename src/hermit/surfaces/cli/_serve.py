from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from hermit.runtime.assembly.config import get_settings
from hermit.runtime.capability.registry.manager import PluginManager
from hermit.runtime.control.lifecycle.session import SessionManager
from hermit.runtime.observation.logging.setup import configure_logging

from ._helpers import (
    caffeinate,
    ensure_workspace,
    stop_runner_background_services,
)
from ._preflight import (
    iso_now,
    run_serve_preflight,
    write_serve_status,
)
from .main import app, t

_serve_log = logging.getLogger("hermit.serve")


@dataclass(frozen=True)
class _ServeRunResult:
    reload_requested: bool
    reason: str
    detail: str
    signal_name: str | None = None


def _pid_path(settings: Any, adapter: str) -> Path:
    return settings.base_dir / f"serve-{adapter}.pid"


def _write_pid(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pid(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _ensure_single_serve_instance(path: Path, adapter: str) -> None:
    existing_pid = _read_pid(path)
    if existing_pid is None or existing_pid == os.getpid():
        return

    try:
        os.kill(existing_pid, 0)
    except ProcessLookupError:
        typer.echo(
            t(
                "cli.serve.stale_pid",
                "Found stale PID file for '{adapter}' (PID {pid}). Cleaning up {pid_file}.",
                adapter=adapter,
                pid=existing_pid,
                pid_file=path,
            )
        )
        _remove_pid(path)
        return
    except PermissionError:
        pass

    typer.echo(
        t(
            "cli.serve.already_running",
            "Hermit serve is already running for '{adapter}' (PID {pid}).\n  PID file: {pid_file}",
            adapter=adapter,
            pid=existing_pid,
            pid_file=path,
        )
    )
    raise typer.Exit(1)


def _configure_unbuffered_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(line_buffering=True, write_through=True)
            except TypeError:
                reconfigure(line_buffering=True)


async def _serve_with_signals(
    adapter_instance: Any,
    runner: Any,
) -> _ServeRunResult:
    """Run the adapter until it exits or a lifecycle signal is received.

    Returns a structured result describing why the adapter stopped.
    """
    loop = asyncio.get_running_loop()
    reload_event = asyncio.Event()
    terminate_event = asyncio.Event()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGHUP, reload_event.set)
        loop.add_signal_handler(signal.SIGTERM, terminate_event.set)

    start_task = asyncio.ensure_future(adapter_instance.start(runner))
    reload_task = asyncio.ensure_future(reload_event.wait())
    terminate_task = asyncio.ensure_future(terminate_event.wait())

    try:
        done, pending = await asyncio.wait(
            {start_task, reload_task, terminate_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if reload_event.is_set():
            _serve_log.info("SIGHUP received — stopping adapter for reload...")
            await adapter_instance.stop()
            return _ServeRunResult(
                reload_requested=True,
                reason="signal",
                detail="SIGHUP received — stopping adapter for reload.",
                signal_name="SIGHUP",
            )

        if terminate_event.is_set():
            _serve_log.warning("SIGTERM received — stopping adapter for shutdown...")
            await adapter_instance.stop()
            return _ServeRunResult(
                reload_requested=False,
                reason="signal",
                detail="SIGTERM received — stopping adapter for shutdown.",
                signal_name="SIGTERM",
            )

        if start_task in done:
            exc = start_task.exception()
            if exc is not None:
                raise exc
            return _ServeRunResult(
                reload_requested=False,
                reason="adapter_stopped",
                detail="Adapter returned control without an explicit reload request.",
            )

        return _ServeRunResult(
            reload_requested=False,
            reason="unknown",
            detail="Serve loop exited without a recognized stop condition.",
        )
    finally:
        if sys.platform != "win32":
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(signal.SIGHUP)
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(signal.SIGTERM)


def _notify_reload(settings: Any, adapter: str) -> None:
    """Fire a DISPATCH_RESULT so the Feishu hook sends a reload notification."""
    from hermit.runtime.capability.contracts.base import HookEvent

    chat_id = getattr(settings, "scheduler_feishu_chat_id", "") or os.environ.get(
        "HERMIT_SCHEDULER_FEISHU_CHAT_ID", ""
    )
    if not chat_id:
        return
    try:
        pm = PluginManager(settings=settings)
        builtin_dir = Path(__file__).resolve().parents[2] / "plugins" / "builtin"
        pm.discover_and_load(builtin_dir, settings.plugins_dir)
        pm.hooks.fire(
            HookEvent.DISPATCH_RESULT,
            source="system",
            title=t("cli.reload.notify.title", "Hermit Reloaded"),
            result_text=t(
                "cli.reload.notify.body",
                "Hermit (`{adapter}`) has been reloaded successfully.\n\nConfiguration, plugins, and tools were rebuilt.",
                adapter=adapter,
            ),
            success=True,
            notify={"feishu_chat_id": chat_id},
        )
    except Exception:
        _serve_log.debug("Failed to send reload notification", exc_info=True)


@app.command()
def serve(adapter: str = "feishu") -> None:
    """Start Hermit as a long-running service with a message adapter.

    Supports graceful reload via SIGHUP: the adapter is stopped, all
    configuration / plugins / tools are rebuilt from scratch, and the adapter
    restarts.  Use ``hermit reload`` to send SIGHUP conveniently.
    """

    _configure_unbuffered_stdio()
    settings = get_settings()
    ensure_workspace(settings)
    pid_file = _pid_path(settings, adapter)
    _ensure_single_serve_instance(pid_file, adapter)
    configure_logging(settings.log_level)
    run_serve_preflight(adapter, settings)
    _write_pid(pid_file)
    write_serve_status(
        settings,
        adapter,
        phase="starting",
        reason="startup",
        detail=f"Serve command is starting for adapter '{adapter}'.",
    )

    try:
        _serve_loop(adapter, pid_file)
    except BaseException as exc:
        refreshed_settings = get_settings()
        write_serve_status(
            refreshed_settings,
            adapter,
            phase="crashed",
            reason="exception",
            detail=f"Serve process exited because of an unhandled {type(exc).__name__}.",
            exc=exc,
            append_history=True,
        )
        raise
    finally:
        _remove_pid(pid_file)


def _serve_loop(adapter: str, pid_file: Path) -> None:
    """Inner restart loop — rebuilds runner on each reload cycle.

    On the first iteration the webhook HTTP server is started normally.
    On subsequent (reload) iterations the webhook server is kept alive and
    only the runner reference is hot-swapped, achieving zero-downtime reload.
    """
    from hermit.runtime.capability.contracts.base import HookEvent

    from ._commands_core import build_runner  # lazy to avoid circular import

    is_first_cycle = True
    prev_runner: Any = None

    while True:
        reload_mode = not is_first_cycle
        get_settings.cache_clear()
        settings = get_settings()
        configure_logging(settings.log_level)
        cycle_started_at = iso_now()

        pm = PluginManager(settings=settings)
        builtin_dir = Path(__file__).resolve().parents[2] / "plugins" / "builtin"
        pm.discover_and_load(builtin_dir, settings.plugins_dir)

        try:
            adapter_instance = pm.get_adapter(adapter)
        except KeyError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1)

        preloaded = getattr(adapter_instance, "required_skills", [])
        runner, _ = build_runner(settings, preloaded_skills=preloaded, pm=pm, serve_mode=True)

        # Start new background services before swapping (overlap window for zero loss)
        pm.hooks.fire(
            HookEvent.SERVE_START,
            runner=runner,
            settings=settings,
            reload_mode=reload_mode,
        )

        # After SERVE_START fires, stop old runner's background services
        if prev_runner is not None:
            stop_runner_background_services(prev_runner)
            prev_runner = None

        write_serve_status(
            settings,
            adapter,
            phase="running",
            reason="reload" if reload_mode else "startup",
            detail=f"Adapter '{adapter}' is running and waiting for events.",
            run_started_at=cycle_started_at,
        )

        typer.echo(
            t(
                "cli.serve.starting",
                "Starting Hermit with '{adapter}' adapter...",
                adapter=adapter,
            )
        )

        run_result = _ServeRunResult(
            reload_requested=False,
            reason="unknown",
            detail="Serve loop exited without updating the run result.",
        )
        with caffeinate(settings):
            try:
                run_result = asyncio.run(_serve_with_signals(adapter_instance, runner))
            except KeyboardInterrupt:
                typer.echo("\n" + t("cli.serve.shutting_down", "Shutting down..."))
                asyncio.run(adapter_instance.stop())
                run_result = _ServeRunResult(
                    reload_requested=False,
                    reason="signal",
                    detail="SIGINT received — stopping adapter for shutdown.",
                    signal_name="SIGINT",
                )
            finally:
                if run_result.reload_requested:
                    # Reload: stop adapter/MCP but keep webhook server alive
                    pm.hooks.fire(HookEvent.SERVE_STOP, reload_mode=True)
                    prev_runner = runner
                    pm.stop_mcp_servers()
                else:
                    # Full shutdown: tear everything down
                    pm.hooks.fire(HookEvent.SERVE_STOP, reload_mode=False)
                    stop_runner_background_services(runner)
                    pm.stop_mcp_servers()

        if run_result.reload_requested:
            is_first_cycle = False
            _serve_log.info("Reloading Hermit...")
            typer.echo(
                t(
                    "cli.serve.reloading",
                    "Reloading Hermit - rebuilding config, plugins, tools...",
                )
            )
            write_serve_status(
                settings,
                adapter,
                phase="reloading",
                reason=run_result.reason,
                detail=run_result.detail,
                signal_name=run_result.signal_name,
                run_started_at=cycle_started_at,
            )
            _write_pid(pid_file)
            _notify_reload(settings, adapter)
            continue

        write_serve_status(
            settings,
            adapter,
            phase="stopped",
            reason=run_result.reason,
            detail=run_result.detail,
            signal_name=run_result.signal_name,
            run_started_at=cycle_started_at,
            append_history=True,
        )
        break


@app.command()
def reload(adapter: str = "feishu") -> None:
    """Send SIGHUP to a running ``hermit serve`` process to trigger a graceful reload.

    The serve process re-reads configuration, rediscovers plugins, rebuilds
    the tool registry and system prompt, and restarts the adapter — all without
    losing the PID.
    """
    if sys.platform == "win32":
        typer.echo(
            t(
                "cli.reload.windows_unsupported",
                "Reload via signal is not supported on Windows.",
            )
        )
        raise typer.Exit(1)

    settings = get_settings()
    pid_file = _pid_path(settings, adapter)
    pid = _read_pid(pid_file)

    if pid is None:
        typer.echo(
            t(
                "cli.reload.no_process",
                "No running serve process found for adapter '{adapter}'.\n  PID file: {pid_file}",
                adapter=adapter,
                pid_file=pid_file,
            )
        )
        raise typer.Exit(1)

    try:
        os.kill(pid, signal.SIGHUP)
    except ProcessLookupError:
        typer.echo(
            t(
                "cli.reload.process_missing",
                "Process {pid} not found (stale PID file). Cleaning up.",
                pid=pid,
            )
        )
        _remove_pid(pid_file)
        raise typer.Exit(1)
    except PermissionError:
        typer.echo(
            t(
                "cli.reload.permission_denied",
                "Permission denied sending SIGHUP to PID {pid}.",
                pid=pid,
            )
        )
        raise typer.Exit(1)

    typer.echo(
        t(
            "cli.reload.sent",
            "Sent SIGHUP to Hermit serve (PID {pid}, adapter='{adapter}').",
            pid=pid,
            adapter=adapter,
        )
    )
    typer.echo(
        t(
            "cli.reload.followup",
            "The service will reload configuration, plugins, and tools.",
        )
    )


@app.command()
def sessions() -> None:
    """List known sessions."""
    settings = get_settings()
    ensure_workspace(settings)
    manager = SessionManager(settings.sessions_dir, settings.session_idle_timeout_seconds)
    for sid in manager.list_sessions():
        typer.echo(sid)
