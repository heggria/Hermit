"""Tests for hermit.runtime.control.runner.utils — shared utilities."""

from __future__ import annotations

from hermit.runtime.control.runner.utils import (
    DispatchResult,
    _locale_for,
    _strip_internal_markup,
    _t,
    _trim_session_messages,
    result_preview,
    result_status,
)
from hermit.runtime.provider_host.execution.runtime import AgentResult

# ---------------------------------------------------------------------------
# _strip_internal_markup
# ---------------------------------------------------------------------------


class TestStripInternalMarkup:
    def test_empty_string(self) -> None:
        assert _strip_internal_markup("") == ""

    def test_no_markup(self) -> None:
        assert _strip_internal_markup("hello world") == "hello world"

    def test_removes_session_time_block(self) -> None:
        text = "<session_time>current_time=2024-01-01</session_time>\nHello"
        result = _strip_internal_markup(text)
        assert "<session_time>" not in result
        assert "Hello" in result

    def test_removes_feishu_metadata_block(self) -> None:
        text = "<feishu_chat>some data</feishu_chat>\nContent here"
        result = _strip_internal_markup(text)
        assert "<feishu_" not in result
        assert "Content here" in result

    def test_removes_multiple_blocks(self) -> None:
        text = "<session_time>t=1</session_time>\n<feishu_msg>data</feishu_msg>\nActual content"
        result = _strip_internal_markup(text)
        assert result == "Actual content"

    def test_collapses_empty_lines(self) -> None:
        text = "Line1\n\n\n\nLine2"
        result = _strip_internal_markup(text)
        assert result == "Line1\nLine2"


# ---------------------------------------------------------------------------
# result_preview
# ---------------------------------------------------------------------------


class TestResultPreview:
    def test_empty_string(self) -> None:
        assert result_preview("") == ""

    def test_short_text(self) -> None:
        assert result_preview("hello") == "hello"

    def test_truncates_long_text(self) -> None:
        text = "a" * 500
        result = result_preview(text)
        assert len(result) <= 280
        assert result.endswith("…")

    def test_strips_markup_before_preview(self) -> None:
        text = "<session_time>t=1</session_time>\nShort answer"
        result = result_preview(text)
        assert "<session_time>" not in result
        assert "Short answer" in result

    def test_custom_limit(self) -> None:
        text = "x" * 100
        result = result_preview(text, limit=50)
        assert len(result) <= 50
        assert result.endswith("…")

    def test_collapses_whitespace(self) -> None:
        text = "hello   world\n\nfoo"
        result = result_preview(text)
        assert result == "hello world foo"


# ---------------------------------------------------------------------------
# result_status
# ---------------------------------------------------------------------------


class TestResultStatus:
    def test_explicit_execution_status(self) -> None:
        result = AgentResult(text="ok", turns=1, tool_calls=0, execution_status="custom_status")
        assert result_status(result) == "custom_status"

    def test_succeeded_by_default(self) -> None:
        result = AgentResult(text="done", turns=1, tool_calls=0, execution_status="")
        assert result_status(result) == "succeeded"

    def test_needs_attention(self) -> None:
        result = AgentResult(
            text="[Execution Requires Attention] something",
            turns=1,
            tool_calls=0,
            execution_status="",
        )
        assert result_status(result) == "needs_attention"

    def test_api_error(self) -> None:
        result = AgentResult(
            text="[API Error] connection failed",
            turns=1,
            tool_calls=0,
            execution_status="",
        )
        assert result_status(result) == "failed"

    def test_policy_denied(self) -> None:
        result = AgentResult(
            text="[Policy Denied] not allowed",
            turns=1,
            tool_calls=0,
            execution_status="",
        )
        assert result_status(result) == "failed"

    def test_normal_text_succeeds(self) -> None:
        result = AgentResult(text="All good", turns=1, tool_calls=0, execution_status="")
        assert result_status(result) == "succeeded"


# ---------------------------------------------------------------------------
# DispatchResult
# ---------------------------------------------------------------------------


class TestDispatchResult:
    def test_defaults(self) -> None:
        dr = DispatchResult(text="hi")
        assert dr.text == "hi"
        assert dr.is_command is False
        assert dr.should_exit is False
        assert dr.agent_result is None

    def test_command_result(self) -> None:
        dr = DispatchResult(text="ok", is_command=True, should_exit=True)
        assert dr.is_command is True
        assert dr.should_exit is True


# ---------------------------------------------------------------------------
# _trim_session_messages
# ---------------------------------------------------------------------------


class TestTrimSessionMessages:
    def test_no_trimming_needed(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        result = _trim_session_messages(messages, max_messages=10)
        assert len(result) == 1

    def test_trims_to_max(self) -> None:
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(20)]
        result = _trim_session_messages(messages, max_messages=5)
        assert len(result) == 5

    def test_preserves_system_first_message(self) -> None:
        messages = [{"role": "system", "content": "system prompt"}]
        messages += [{"role": "user", "content": f"msg{i}"} for i in range(20)]
        result = _trim_session_messages(messages, max_messages=5)
        assert result[0]["role"] == "system"
        assert len(result) == 5

    def test_empty_messages(self) -> None:
        result = _trim_session_messages([], max_messages=5)
        assert result == []


# ---------------------------------------------------------------------------
# _locale_for
# ---------------------------------------------------------------------------


class TestLocaleFor:
    def test_no_sources_returns_default(self) -> None:
        locale = _locale_for()
        assert isinstance(locale, str)
        assert len(locale) > 0

    def test_runner_with_pm_settings(self) -> None:
        from types import SimpleNamespace

        settings = SimpleNamespace(locale="zh-CN")
        pm = SimpleNamespace(settings=settings)
        runner = SimpleNamespace(pm=pm)
        locale = _locale_for(runner=runner)
        assert locale == "zh-CN"

    def test_pm_without_runner(self) -> None:
        from types import SimpleNamespace

        settings = SimpleNamespace(locale="en-US")
        pm = SimpleNamespace(settings=settings)
        locale = _locale_for(pm=pm)
        assert locale == "en-US"


# ---------------------------------------------------------------------------
# _t
# ---------------------------------------------------------------------------


class TestTranslationHelper:
    def test_with_default(self) -> None:
        result = _t("nonexistent.key", default="fallback text")
        assert result == "fallback text"

    def test_returns_string(self) -> None:
        result = _t("kernel.runner.new_session", default="New session started.")
        assert isinstance(result, str)
