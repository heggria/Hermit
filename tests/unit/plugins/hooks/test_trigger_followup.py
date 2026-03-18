"""Tests for TriggerEngine._create_followup — covering trigger/engine.py lines 69-104."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.plugins.builtin.hooks.trigger.engine import TriggerEngine
from hermit.plugins.builtin.hooks.trigger.models import TriggerMatch, TriggerRule


def _make_rule(**overrides: Any) -> TriggerRule:
    defaults: dict[str, Any] = {
        "name": "test_failure",
        "source_kind": "test_failure",
        "match_pattern": r"FAILED",
        "suggested_goal_template": "Fix: {match}",
        "risk_level": "medium",
        "policy_profile": "default",
        "cooldown_key_template": "test:{match}",
    }
    defaults.update(overrides)
    return TriggerRule(**defaults)


def _make_match(**overrides: Any) -> TriggerMatch:
    defaults: dict[str, Any] = {
        "rule": _make_rule(),
        "matched_text": "FAILED test_foo",
        "evidence_refs": ["result://s1/adhoc"],
        "suggested_goal": "Fix: FAILED test_foo",
        "cooldown_key": "test:FAILED test_foo",
    }
    defaults.update(overrides)
    return TriggerMatch(**defaults)


class TestCreateFollowup:
    """Cover engine._create_followup lines 69-110."""

    def test_no_runner_returns_none(self) -> None:
        engine = TriggerEngine()
        result = engine._create_followup(_make_match(), session_id="s1")
        assert result is None

    def test_no_task_controller_returns_none(self) -> None:
        engine = TriggerEngine()
        runner = SimpleNamespace(task_controller=None)
        engine.set_runner(runner)
        result = engine._create_followup(_make_match(), session_id="s1")
        assert result is None

    def test_cooldown_active_returns_none(self) -> None:
        store = MagicMock()
        store.check_cooldown.return_value = True
        tc = SimpleNamespace(store=store)
        runner = SimpleNamespace(task_controller=tc)

        engine = TriggerEngine()
        engine.set_runner(runner)
        result = engine._create_followup(_make_match(), session_id="s1")
        assert result is None
        store.check_cooldown.assert_called_once()

    def test_creates_signal_when_available(self) -> None:
        store = MagicMock()
        store.check_cooldown.return_value = False
        store.create_signal = MagicMock()
        tc = SimpleNamespace(store=store)
        runner = SimpleNamespace(task_controller=tc)

        engine = TriggerEngine()
        engine.set_runner(runner)
        result = engine._create_followup(_make_match(), session_id="s1")
        assert result is not None
        assert "Fix:" in result
        store.create_signal.assert_called_once()

    def test_no_cooldown_method_still_works(self) -> None:
        """When store doesn't have check_cooldown, should skip cooldown check."""
        store = SimpleNamespace()
        tc = SimpleNamespace(store=store)
        runner = SimpleNamespace(task_controller=tc)

        engine = TriggerEngine()
        engine.set_runner(runner)
        result = engine._create_followup(_make_match(), session_id="s1")
        assert result is not None

    def test_no_create_signal_method_still_returns_goal(self) -> None:
        """When store has check_cooldown but not create_signal."""
        store = MagicMock(spec=["check_cooldown"])
        store.check_cooldown.return_value = False
        tc = SimpleNamespace(store=store)
        runner = SimpleNamespace(task_controller=tc)

        engine = TriggerEngine()
        engine.set_runner(runner)
        result = engine._create_followup(_make_match(), session_id="s1")
        assert result is not None


class TestAnalyzeAndDispatchFollowup:
    """Cover engine.analyze_and_dispatch with _create_followup exception path."""

    def test_exception_in_create_followup_is_caught(self) -> None:
        store = MagicMock()
        store.check_cooldown.side_effect = RuntimeError("boom")
        tc = SimpleNamespace(store=store)
        runner = SimpleNamespace(task_controller=tc)

        engine = TriggerEngine()
        engine.set_runner(runner)
        engine.analyze_and_dispatch("FAILED test_foo", session_id="s1")
