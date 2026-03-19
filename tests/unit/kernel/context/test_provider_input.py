"""Tests for kernel/context/injection/provider_input.py — ProviderInputCompiler helpers."""

from __future__ import annotations

from hermit.kernel.context.injection.provider_input import (
    _strip_runtime_markup,
    _trim,
)

# ---------------------------------------------------------------------------
# _trim
# ---------------------------------------------------------------------------


class TestTrim:
    def test_empty_string(self) -> None:
        assert _trim("", 100) == ""

    def test_none_input(self) -> None:
        assert _trim(None, 100) == ""

    def test_short_string_unchanged(self) -> None:
        assert _trim("hello", 100) == "hello"

    def test_exact_limit(self) -> None:
        assert _trim("abc", 3) == "abc"

    def test_truncated_with_ellipsis(self) -> None:
        result = _trim("hello world", 6)
        assert len(result) <= 6
        assert result.endswith("\u2026")

    def test_limit_one(self) -> None:
        result = _trim("hello", 1)
        assert len(result) == 1

    def test_strips_whitespace(self) -> None:
        assert _trim("  hello  ", 100) == "hello"


# ---------------------------------------------------------------------------
# _strip_runtime_markup
# ---------------------------------------------------------------------------


class TestStripRuntimeMarkup:
    def test_empty_string(self) -> None:
        assert _strip_runtime_markup("") == ""

    def test_none_input(self) -> None:
        assert _strip_runtime_markup(None) == ""

    def test_no_markup(self) -> None:
        assert _strip_runtime_markup("plain text") == "plain text"

    def test_removes_session_time(self) -> None:
        text = "hello <session_time>2024-01-01T00:00:00</session_time> world"
        result = _strip_runtime_markup(text)
        assert "<session_time>" not in result
        assert "hello" in result
        assert "world" in result

    def test_removes_feishu_metadata(self) -> None:
        text = "hello <feishu_reply>some data</feishu_reply> world"
        result = _strip_runtime_markup(text)
        assert "<feishu_reply>" not in result

    def test_removes_multiple_markups(self) -> None:
        text = "<session_time>time</session_time> <feishu_user>data</feishu_user> actual content"
        result = _strip_runtime_markup(text)
        assert "actual content" in result
        assert "<session_time>" not in result
        assert "<feishu_user>" not in result

    def test_collapses_empty_lines(self) -> None:
        text = "line1\n\n\n\nline2"
        result = _strip_runtime_markup(text)
        assert "\n\n" not in result

    def test_multiline_session_time(self) -> None:
        text = "before\n<session_time>\nmultiline\ncontent\n</session_time>\nafter"
        result = _strip_runtime_markup(text)
        assert "<session_time>" not in result
        assert "before" in result
        assert "after" in result


# ---------------------------------------------------------------------------
# ProviderInputCompiler.normalize_ingress (static parts)
# ---------------------------------------------------------------------------


class TestNormalizeIngress:
    """Test normalize_ingress behavior through the static helpers it uses."""

    def test_code_block_regex(self) -> None:
        """Verify the regex used in normalize_ingress matches code blocks."""
        from hermit.kernel.context.injection.provider_input import _CODE_BLOCK_RE

        text = "before\n```python\nprint('hello')\n```\nafter"
        matches = list(_CODE_BLOCK_RE.finditer(text))
        assert len(matches) == 1
        assert matches[0].group("lang") == "python"
        assert "print" in matches[0].group("body")

    def test_long_prose_threshold(self) -> None:
        from hermit.kernel.context.injection.provider_input import (
            _LONG_PROSE_CHAR_THRESHOLD,
            _LONG_PROSE_LINE_THRESHOLD,
        )

        assert _LONG_PROSE_CHAR_THRESHOLD == 4096
        assert _LONG_PROSE_LINE_THRESHOLD == 80

    def test_inline_excerpt_limit(self) -> None:
        from hermit.kernel.context.injection.provider_input import _INLINE_EXCERPT_LIMIT

        assert _INLINE_EXCERPT_LIMIT == 800


# ---------------------------------------------------------------------------
# ProviderInputCompiler._carry_forward (static method)
# ---------------------------------------------------------------------------


class TestCarryForward:
    def test_carry_forward_from_ingress_anchor(self) -> None:
        from hermit.kernel.context.injection.provider_input import ProviderInputCompiler
        from hermit.kernel.context.models.context import TaskExecutionContext

        ctx = TaskExecutionContext(
            conversation_id="conv-1",
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            source_channel="cli",
            ingress_metadata={"continuation_anchor": {"task_id": "prev-task"}},
        )
        result = ProviderInputCompiler._carry_forward(ctx, {})
        assert result == {"task_id": "prev-task"}

    def test_carry_forward_from_projection(self) -> None:
        from hermit.kernel.context.injection.provider_input import ProviderInputCompiler
        from hermit.kernel.context.models.context import TaskExecutionContext

        ctx = TaskExecutionContext(
            conversation_id="conv-1",
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            source_channel="cli",
        )
        projection = {"projection": {"task": {"continuation_anchor": {"goal": "previous goal"}}}}
        result = ProviderInputCompiler._carry_forward(ctx, projection)
        assert result == {"goal": "previous goal"}

    def test_carry_forward_no_anchor(self) -> None:
        from hermit.kernel.context.injection.provider_input import ProviderInputCompiler
        from hermit.kernel.context.models.context import TaskExecutionContext

        ctx = TaskExecutionContext(
            conversation_id="conv-1",
            task_id="task-1",
            step_id="step-1",
            step_attempt_id="attempt-1",
            source_channel="cli",
        )
        result = ProviderInputCompiler._carry_forward(ctx, {})
        assert result is None


# ---------------------------------------------------------------------------
# ProviderInputCompiler._render_continuation_guidance
# ---------------------------------------------------------------------------


class TestRenderContinuationGuidance:
    def _compiler(self):
        from unittest.mock import MagicMock

        from hermit.kernel.context.injection.provider_input import ProviderInputCompiler

        store = MagicMock()
        return ProviderInputCompiler(store)

    def test_empty_guidance(self) -> None:
        compiler = self._compiler()
        assert compiler._render_continuation_guidance({}) == ""

    def test_no_anchor(self) -> None:
        compiler = self._compiler()
        assert compiler._render_continuation_guidance({"has_anchor": False}) == ""

    def test_explicit_topic_shift(self) -> None:
        compiler = self._compiler()
        result = compiler._render_continuation_guidance(
            {
                "has_anchor": True,
                "mode": "explicit_topic_shift",
                "anchor_task_id": "t-1",
            }
        )
        assert "explicit" in result.lower() or "new topic" in result.lower()

    def test_strong_topic_shift(self) -> None:
        compiler = self._compiler()
        result = compiler._render_continuation_guidance(
            {
                "has_anchor": True,
                "mode": "strong_topic_shift",
                "anchor_task_id": "t-1",
            }
        )
        assert "strong" in result.lower() or "new" in result.lower()

    def test_anchor_correction(self) -> None:
        compiler = self._compiler()
        result = compiler._render_continuation_guidance(
            {
                "has_anchor": True,
                "mode": "anchor_correction",
                "anchor_task_id": "t-1",
            }
        )
        assert "correction" in result.lower() or "clarification" in result.lower()

    def test_default_mode(self) -> None:
        compiler = self._compiler()
        result = compiler._render_continuation_guidance(
            {
                "has_anchor": True,
                "mode": "plain_new_task",
                "anchor_task_id": "t-1",
                "anchor_user_request": "previous request",
                "anchor_goal": "previous goal",
                "outcome_summary": "it was done",
            }
        )
        assert "anchor_task_id=t-1" in result
        assert "previous request" in result
        assert "previous goal" in result
        assert "it was done" in result
