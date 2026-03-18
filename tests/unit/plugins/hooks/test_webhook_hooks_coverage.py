"""Tests for webhook/hooks.py — covers _on_serve_stop and register."""

from __future__ import annotations

from hermit.plugins.builtin.hooks.webhook import hooks as webhook_hooks
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


def test_on_serve_stop_with_active_server() -> None:
    """_on_serve_stop calls stop() on the server and clears the module-level reference."""
    old_server = webhook_hooks._server
    try:
        stopped: list[bool] = []

        class FakeServer:
            def stop(self) -> None:
                stopped.append(True)

        webhook_hooks._server = FakeServer()
        webhook_hooks._on_serve_stop()
        assert stopped == [True]
        assert webhook_hooks._server is None
    finally:
        webhook_hooks._server = old_server


def test_on_serve_stop_when_no_server() -> None:
    """_on_serve_stop is a no-op when _server is None."""
    old_server = webhook_hooks._server
    try:
        webhook_hooks._server = None
        # Should not raise
        webhook_hooks._on_serve_stop()
        assert webhook_hooks._server is None
    finally:
        webhook_hooks._server = old_server


def test_register_sets_hooks_ref_and_adds_hooks() -> None:
    """register() stores _hooks_ref and adds SERVE_START and SERVE_STOP hooks."""
    old_hooks_ref = webhook_hooks._hooks_ref
    try:
        engine = HooksEngine()
        ctx = PluginContext(engine)
        webhook_hooks.register(ctx)
        assert webhook_hooks._hooks_ref is engine
        assert len(engine._handlers.get(HookEvent.SERVE_START, [])) >= 1
        assert len(engine._handlers.get(HookEvent.SERVE_STOP, [])) >= 1
    finally:
        webhook_hooks._hooks_ref = old_hooks_ref
