"""Tests for trigger/engine.py — covers missing lines: cooldown, signal creation, exceptions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.plugins.builtin.hooks.trigger.engine import TriggerEngine
from hermit.plugins.builtin.hooks.trigger.models import TriggerMatch, TriggerRule

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_RULE = TriggerRule(
    name="test_fail",
    source_kind="test_failure",
    match_pattern=r"FAILED",
    suggested_goal_template="Fix: {match}",
    cooldown_key_template="fail:{match}",
    risk_level="medium",
    policy_profile="default",
)


def _engine_with_rule() -> TriggerEngine:
    return TriggerEngine(rules=[_TEST_RULE])


def _make_runner(
    *,
    has_controller: bool = True,
    has_cooldown: bool = False,
    cooldown_active: bool = False,
    has_signal: bool = False,
) -> SimpleNamespace:
    store = SimpleNamespace()
    if has_cooldown:
        store.check_cooldown = MagicMock(return_value=cooldown_active)  # type: ignore[attr-defined]
    if has_signal:
        store.create_signal = MagicMock()  # type: ignore[attr-defined]
    tc = SimpleNamespace(store=store) if has_controller else None
    return SimpleNamespace(task_controller=tc)


# ---------------------------------------------------------------------------
# _create_followup — exception in followup logs but doesn't raise
# ---------------------------------------------------------------------------


def test_analyze_and_dispatch_exception_in_followup() -> None:
    """When _create_followup raises, analyze_and_dispatch logs but doesn't propagate."""
    engine = _engine_with_rule()
    runner = _make_runner(has_controller=True)
    # Make store.check_cooldown raise to trigger the exception path
    runner.task_controller.store = MagicMock(spec=[])
    runner.task_controller.store.check_cooldown = MagicMock(side_effect=RuntimeError("boom"))
    # Give it check_cooldown so the hasattr check passes
    engine.set_runner(runner)
    # Should not raise
    engine.analyze_and_dispatch("FAILED test_foo", session_id="s1")


# ---------------------------------------------------------------------------
# _create_followup — runner is None
# ---------------------------------------------------------------------------


def test_create_followup_runner_none() -> None:
    """_create_followup returns None when runner is None."""
    engine = _engine_with_rule()
    match = TriggerMatch(
        rule=_TEST_RULE,
        matched_text="FAILED",
        suggested_goal="Fix: FAILED",
        cooldown_key="fail:FAILED",
    )
    result = engine._create_followup(match, session_id="s1")
    assert result is None


# ---------------------------------------------------------------------------
# _create_followup — cooldown active
# ---------------------------------------------------------------------------


def test_create_followup_cooldown_active() -> None:
    """When cooldown is active, _create_followup returns None."""
    engine = _engine_with_rule()
    runner = _make_runner(has_controller=True, has_cooldown=True, cooldown_active=True)
    engine.set_runner(runner)
    match = TriggerMatch(
        rule=_TEST_RULE,
        matched_text="FAILED",
        suggested_goal="Fix: FAILED",
        cooldown_key="fail:FAILED",
    )
    result = engine._create_followup(match, session_id="s1")
    assert result is None
    runner.task_controller.store.check_cooldown.assert_called_once()


# ---------------------------------------------------------------------------
# _create_followup — signal creation
# ---------------------------------------------------------------------------


def test_create_followup_creates_signal() -> None:
    """When store has create_signal, an EvidenceSignal is created."""
    engine = _engine_with_rule()
    runner = _make_runner(has_controller=True, has_signal=True)
    engine.set_runner(runner)
    match = TriggerMatch(
        rule=_TEST_RULE,
        matched_text="FAILED",
        evidence_refs=["result://s1/t1"],
        suggested_goal="Fix: FAILED",
        cooldown_key="fail:FAILED",
    )
    result = engine._create_followup(match, session_id="s1")
    assert result == "Fix: FAILED"
    runner.task_controller.store.create_signal.assert_called_once()
    signal_arg = runner.task_controller.store.create_signal.call_args[0][0]
    assert signal_arg.source_kind == "test_failure"
    assert signal_arg.suggested_goal == "Fix: FAILED"


def test_create_followup_no_signal_no_cooldown() -> None:
    """When store has neither create_signal nor check_cooldown, followup still succeeds."""
    engine = _engine_with_rule()
    runner = _make_runner(has_controller=True)
    engine.set_runner(runner)
    match = TriggerMatch(
        rule=_TEST_RULE,
        matched_text="FAILED",
        suggested_goal="Fix: FAILED",
        cooldown_key="fail:FAILED",
    )
    result = engine._create_followup(match, session_id="s1")
    assert result == "Fix: FAILED"
