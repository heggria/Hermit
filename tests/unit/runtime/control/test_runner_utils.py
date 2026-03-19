"""Tests for runtime/control/runner/utils.py — shared runner utilities."""

from __future__ import annotations

from types import SimpleNamespace

from hermit.runtime.control.runner.utils import (
    DispatchResult,
    _locale_for,
    _strip_internal_markup,
    _t,
    _trim_session_messages,
    result_preview,
    result_status,
)

# ---------------------------------------------------------------------------
# _strip_internal_markup
# ---------------------------------------------------------------------------


class TestStripInternalMarkup:
    def test_empty_string(self) -> None:
        assert _strip_internal_markup("") == ""

    def test_no_markup(self) -> None:
        assert _strip_internal_markup("plain text") == "plain text"

    def test_strips_session_time(self) -> None:
        text = "hello <session_time>2024-01-01</session_time> world"
        result = _strip_internal_markup(text)
        assert "<session_time>" not in result
        assert "hello" in result
        assert "world" in result

    def test_strips_feishu_metadata(self) -> None:
        text = "hello <feishu_reply>data</feishu_reply> world"
        result = _strip_internal_markup(text)
        assert "<feishu_reply>" not in result

    def test_collapses_blank_lines(self) -> None:
        text = "line1\n\n\n\nline2"
        result = _strip_internal_markup(text)
        lines = result.split("\n")
        assert all(line.strip() for line in lines)

    def test_strips_multiple_feishu_tags(self) -> None:
        text = "<feishu_user>u</feishu_user> mid <feishu_msg>m</feishu_msg>"
        result = _strip_internal_markup(text)
        assert "mid" in result
        assert "<feishu_" not in result


# ---------------------------------------------------------------------------
# result_preview
# ---------------------------------------------------------------------------


class TestResultPreview:
    def test_empty_returns_empty(self) -> None:
        assert result_preview("") == ""

    def test_short_text_unchanged(self) -> None:
        assert result_preview("hello world") == "hello world"

    def test_strips_markup_first(self) -> None:
        text = "<session_time>t</session_time> actual content"
        result = result_preview(text)
        assert "<session_time>" not in result
        assert "actual content" in result

    def test_truncates_long_text(self) -> None:
        long_text = "x" * 500
        result = result_preview(long_text)
        assert len(result) <= 280

    def test_custom_limit(self) -> None:
        text = "x" * 50
        result = result_preview(text, limit=20)
        assert len(result) <= 20

    def test_collapses_whitespace(self) -> None:
        text = "  hello   world  "
        result = result_preview(text)
        assert result == "hello world"


# ---------------------------------------------------------------------------
# result_status
# ---------------------------------------------------------------------------


class TestResultStatus:
    def test_explicit_execution_status(self) -> None:
        result = SimpleNamespace(text="", execution_status="completed")
        assert result_status(result) == "completed"

    def test_needs_attention_prefix(self) -> None:
        result = SimpleNamespace(
            text="[Execution Requires Attention] something", execution_status=""
        )
        assert result_status(result) == "needs_attention"

    def test_api_error_prefix(self) -> None:
        result = SimpleNamespace(text="[API Error] 500", execution_status="")
        assert result_status(result) == "failed"

    def test_policy_denied_prefix(self) -> None:
        result = SimpleNamespace(text="[Policy Denied] not allowed", execution_status="")
        assert result_status(result) == "failed"

    def test_default_succeeded(self) -> None:
        result = SimpleNamespace(text="all good", execution_status="")
        assert result_status(result) == "succeeded"

    def test_no_execution_status_attr(self) -> None:
        result = SimpleNamespace(text="all good")
        assert result_status(result) == "succeeded"


# ---------------------------------------------------------------------------
# DispatchResult
# ---------------------------------------------------------------------------


class TestDispatchResult:
    def test_default_values(self) -> None:
        dr = DispatchResult(text="hello")
        assert dr.text == "hello"
        assert dr.is_command is False
        assert dr.should_exit is False
        assert dr.agent_result is None

    def test_command_result(self) -> None:
        dr = DispatchResult(text="/help", is_command=True)
        assert dr.is_command is True

    def test_exit_result(self) -> None:
        dr = DispatchResult(text="bye", should_exit=True)
        assert dr.should_exit is True


# ---------------------------------------------------------------------------
# _trim_session_messages
# ---------------------------------------------------------------------------


class TestTrimSessionMessages:
    def test_short_list_unchanged(self) -> None:
        messages = [{"role": "user", "content": f"msg-{i}"} for i in range(5)]
        result = _trim_session_messages(messages)
        assert len(result) == 5

    def test_trims_to_max_messages(self) -> None:
        messages = [{"role": "user", "content": f"msg-{i}"} for i in range(150)]
        result = _trim_session_messages(messages, max_messages=100)
        assert len(result) <= 100

    def test_preserves_system_first_message(self) -> None:
        messages = [
            {"role": "system", "content": "system prompt"},
            *[{"role": "user", "content": f"msg-{i}"} for i in range(150)],
        ]
        result = _trim_session_messages(messages, max_messages=10)
        assert result[0]["role"] == "system"
        assert len(result) <= 10

    def test_no_system_first(self) -> None:
        messages = [{"role": "user", "content": f"msg-{i}"} for i in range(150)]
        result = _trim_session_messages(messages, max_messages=10)
        assert len(result) <= 10

    def test_empty_list(self) -> None:
        result = _trim_session_messages([])
        assert result == []


# ---------------------------------------------------------------------------
# _locale_for / _t
# ---------------------------------------------------------------------------


class TestLocaleHelpers:
    def test_locale_for_no_sources(self) -> None:
        locale = _locale_for()
        assert isinstance(locale, str)
        assert locale  # non-empty

    def test_locale_for_runner(self) -> None:
        runner = SimpleNamespace(pm=SimpleNamespace(settings=SimpleNamespace(locale="zh-CN")))
        locale = _locale_for(runner=runner)
        assert locale == "zh-CN"

    def test_locale_for_pm(self) -> None:
        pm = SimpleNamespace(settings=SimpleNamespace(locale="en-US"))
        locale = _locale_for(pm=pm)
        assert locale == "en-US"

    def test_t_returns_string(self) -> None:
        # Should not raise, and should return a string
        result = _t("kernel.memory.static_intro")
        assert isinstance(result, str)

    def test_t_with_default(self) -> None:
        result = _t("nonexistent.key", default="fallback")
        assert isinstance(result, str)
