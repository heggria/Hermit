"""Tests for trigger/hooks.py — covers _on_serve_start, _on_post_run, and disabled register."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


def test_on_serve_start_attaches_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """_on_serve_start sets runner on the engine when both are present."""
    from hermit.plugins.builtin.hooks.trigger import hooks as trigger_hooks
    from hermit.plugins.builtin.hooks.trigger.engine import TriggerEngine

    old_engine = trigger_hooks._engine
    try:
        engine = TriggerEngine()
        trigger_hooks._engine = engine
        fake_runner = SimpleNamespace(name="test-runner")
        trigger_hooks._on_serve_start(runner=fake_runner)
        assert engine._runner is fake_runner
    finally:
        trigger_hooks._engine = old_engine


def test_on_serve_start_noop_when_no_engine() -> None:
    """_on_serve_start does nothing when engine is None."""
    from hermit.plugins.builtin.hooks.trigger import hooks as trigger_hooks

    old_engine = trigger_hooks._engine
    try:
        trigger_hooks._engine = None
        # Should not raise
        trigger_hooks._on_serve_start(runner=SimpleNamespace())
    finally:
        trigger_hooks._engine = old_engine


def test_on_post_run_noop_when_engine_none() -> None:
    """_on_post_run returns early when _engine is None."""
    from hermit.plugins.builtin.hooks.trigger import hooks as trigger_hooks

    old_engine = trigger_hooks._engine
    try:
        trigger_hooks._engine = None
        # Should not raise
        trigger_hooks._on_post_run(result="FAILED something", session_id="s1")
    finally:
        trigger_hooks._engine = old_engine


def test_on_post_run_delegates_to_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """_on_post_run calls engine.analyze_and_dispatch."""
    from hermit.plugins.builtin.hooks.trigger import hooks as trigger_hooks
    from hermit.plugins.builtin.hooks.trigger.engine import TriggerEngine

    old_engine = trigger_hooks._engine
    try:
        engine = TriggerEngine()
        dispatched: list[Any] = []

        def tracking_dispatch(result: Any, **kwargs: Any) -> None:
            dispatched.append((result, kwargs))

        engine.analyze_and_dispatch = tracking_dispatch  # type: ignore[assignment]
        trigger_hooks._engine = engine
        trigger_hooks._on_post_run(result="FAILED test_foo", session_id="s1")
        assert len(dispatched) == 1
        assert dispatched[0][0] == "FAILED test_foo"
    finally:
        trigger_hooks._engine = old_engine


def test_register_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When trigger_enabled is False, register should not create engine or add hooks."""
    from hermit.plugins.builtin.hooks.trigger import hooks as trigger_hooks

    old_engine = trigger_hooks._engine
    try:
        trigger_hooks._engine = None
        engine = HooksEngine()
        ctx = PluginContext(engine)
        ctx.plugin_vars = {"trigger_enabled": False}
        trigger_hooks.register(ctx)
        assert trigger_hooks._engine is None
    finally:
        trigger_hooks._engine = old_engine
