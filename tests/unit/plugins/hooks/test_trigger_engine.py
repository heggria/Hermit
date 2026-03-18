from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hermit.plugins.builtin.hooks.trigger.engine import TriggerEngine, _extract_text
from hermit.plugins.builtin.hooks.trigger.models import TriggerRule

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeResult:
    result_text: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_outputs: list[str] = field(default_factory=list)


class FakeRunner:
    def __init__(self, *, has_controller: bool = False) -> None:
        self.task_controller = FakeTaskController() if has_controller else None


class FakeTaskController:
    def __init__(self) -> None:
        self.store = FakeStore()


class FakeStore:
    pass


# ---------------------------------------------------------------------------
# analyze — built-in rules
# ---------------------------------------------------------------------------


def test_analyze_test_failure() -> None:
    engine = TriggerEngine()
    matches = engine.analyze("tests/test_foo.py FAILED due to AssertionError")
    assert len(matches) >= 1
    found = [m for m in matches if m.rule.source_kind == "test_failure"]
    assert found
    assert "Fix failing test" in found[0].suggested_goal


def test_analyze_lint_violation() -> None:
    engine = TriggerEngine()
    matches = engine.analyze("ruff check: src/foo.py:10 E501 line too long")
    found = [m for m in matches if m.rule.source_kind == "lint_violation"]
    assert found
    assert "Fix lint violation" in found[0].suggested_goal


def test_analyze_todo() -> None:
    engine = TriggerEngine()
    matches = engine.analyze("# TODO: refactor this module into smaller pieces")
    found = [m for m in matches if m.rule.source_kind == "todo_scan"]
    assert found
    assert "Address TODO" in found[0].suggested_goal


def test_analyze_security_cve() -> None:
    engine = TriggerEngine()
    matches = engine.analyze("Found CVE-2024-12345 in dependency foo-bar")
    found = [m for m in matches if m.rule.source_kind == "security_vuln"]
    assert found
    assert "Investigate security issue" in found[0].suggested_goal
    assert found[0].rule.risk_level == "critical"


def test_analyze_security_keyword() -> None:
    engine = TriggerEngine()
    matches = engine.analyze("Detected a critical severity vulnerability in auth module")
    found = [m for m in matches if m.rule.source_kind == "security_vuln"]
    assert found


# ---------------------------------------------------------------------------
# max_tasks_per_run cap
# ---------------------------------------------------------------------------


def test_max_tasks_per_run_cap() -> None:
    engine = TriggerEngine(max_tasks_per_run=2)
    text = "FAILED test1\nFAILED test2\nFAILED test3\nERROR test4"
    matches = engine.analyze(text)
    assert len(matches) <= 2


# ---------------------------------------------------------------------------
# No match
# ---------------------------------------------------------------------------


def test_no_match_returns_empty() -> None:
    engine = TriggerEngine()
    matches = engine.analyze("Everything passed successfully with no issues.")
    assert matches == []


def test_empty_text_returns_empty() -> None:
    engine = TriggerEngine()
    assert engine.analyze("") == []
    assert engine.analyze(None) == []


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------


def test_extract_text_string() -> None:
    assert _extract_text("hello world") == "hello world"


def test_extract_text_result_object() -> None:
    result = FakeResult(result_text="test output here")
    text = _extract_text(result)
    assert "test output here" in text


def test_extract_text_messages() -> None:
    result = FakeResult(messages=[{"content": "message content"}])
    text = _extract_text(result)
    assert "message content" in text


def test_extract_text_message_with_content_attr() -> None:
    @dataclass
    class Msg:
        content: str = "attr content"

    result = FakeResult()
    result.messages = [Msg()]  # type: ignore[list-item]
    text = _extract_text(result)
    assert "attr content" in text


def test_extract_text_tool_outputs() -> None:
    result = FakeResult(tool_outputs=["output line 1", "output line 2"])
    text = _extract_text(result)
    assert "output line 1" in text
    assert "output line 2" in text


def test_extract_text_fallback_dict() -> None:
    class Opaque:
        def __init__(self) -> None:
            self.data = "opaque_data"

    text = _extract_text(Opaque())
    assert "opaque_data" in text


# ---------------------------------------------------------------------------
# analyze_and_dispatch
# ---------------------------------------------------------------------------


def test_analyze_and_dispatch_no_runner() -> None:
    engine = TriggerEngine()
    # Should not raise even without runner
    engine.analyze_and_dispatch("FAILED test_foo", session_id="s1")


def test_analyze_and_dispatch_with_runner_no_controller() -> None:
    engine = TriggerEngine()
    engine.set_runner(FakeRunner(has_controller=False))
    # Should not raise: runner exists but no task_controller
    engine.analyze_and_dispatch("FAILED test_foo", session_id="s1")


def test_analyze_and_dispatch_with_runner_and_controller() -> None:
    engine = TriggerEngine()
    runner = FakeRunner(has_controller=True)
    engine.set_runner(runner)
    # Should not raise: calls _create_followup which logs
    engine.analyze_and_dispatch("FAILED test_foo", session_id="s1")


# ---------------------------------------------------------------------------
# Evidence refs and cooldown keys
# ---------------------------------------------------------------------------


def test_evidence_refs_populated() -> None:
    engine = TriggerEngine()
    matches = engine.analyze("FAILED test_bar", session_id="sess-1", task_id="task-42")
    assert matches
    assert matches[0].evidence_refs == ["result://sess-1/task-42"]


def test_cooldown_key_populated() -> None:
    engine = TriggerEngine()
    matches = engine.analyze("FAILED test_bar", session_id="sess-1")
    assert matches
    assert matches[0].cooldown_key.startswith("test_failure:")


# ---------------------------------------------------------------------------
# Disabled rule
# ---------------------------------------------------------------------------


def test_disabled_rule_skipped() -> None:
    rule = TriggerRule(
        name="disabled",
        source_kind="test",
        match_pattern=r"BOOM",
        suggested_goal_template="Fix: {match}",
        enabled=False,
    )
    engine = TriggerEngine(rules=[rule])
    assert engine.analyze("BOOM") == []


# ---------------------------------------------------------------------------
# Custom rules
# ---------------------------------------------------------------------------


def test_custom_rule() -> None:
    rule = TriggerRule(
        name="custom",
        source_kind="custom_scan",
        match_pattern=r"DEPRECATION WARNING: (.+)",
        suggested_goal_template="Handle deprecation: {match}",
        cooldown_key_template="deprecation:{match}",
    )
    engine = TriggerEngine(rules=[rule])
    matches = engine.analyze("DEPRECATION WARNING: use new_api instead")
    assert len(matches) == 1
    assert "Handle deprecation" in matches[0].suggested_goal
    assert matches[0].rule.source_kind == "custom_scan"
