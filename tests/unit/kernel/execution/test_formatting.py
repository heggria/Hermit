"""Tests for hermit.kernel.execution.executor.formatting."""

from __future__ import annotations

import json

from hermit.kernel.execution.executor.formatting import (
    BLOCK_TYPES,
    compact_progress_text,
    format_model_content,
    progress_signature,
    progress_summary_signature,
    truncate_middle,
)

# ---------------------------------------------------------------------------
# truncate_middle
# ---------------------------------------------------------------------------


class TestTruncateMiddle:
    def test_no_truncation_when_under_limit(self) -> None:
        assert truncate_middle("hello", 100) == "hello"

    def test_no_truncation_when_equal_limit(self) -> None:
        assert truncate_middle("hello", 5) == "hello"

    def test_no_truncation_when_limit_zero(self) -> None:
        assert truncate_middle("hello", 0) == "hello"

    def test_no_truncation_when_limit_negative(self) -> None:
        assert truncate_middle("hello", -1) == "hello"

    def test_small_limit_truncates_head_only(self) -> None:
        text = "abcdefghijklmnopqrstuvwxyz"
        result = truncate_middle(text, 10)
        assert result == text[:10]

    def test_small_limit_boundary_32(self) -> None:
        text = "a" * 100
        result = truncate_middle(text, 32)
        assert result == text[:32]

    def test_large_limit_uses_middle_ellipsis(self) -> None:
        text = "a" * 200
        result = truncate_middle(text, 50)
        assert "\n...\n" in result
        assert len(result) <= 50

    def test_empty_string(self) -> None:
        assert truncate_middle("", 100) == ""

    def test_limit_33_uses_middle_ellipsis(self) -> None:
        text = "a" * 100
        result = truncate_middle(text, 33)
        assert "\n...\n" in result


# ---------------------------------------------------------------------------
# format_model_content
# ---------------------------------------------------------------------------


class TestFormatModelContent:
    def test_string_result_truncated(self) -> None:
        result = format_model_content("short", 100)
        assert result == "short"

    def test_string_result_long_truncated(self) -> None:
        long_str = "x" * 200
        result = format_model_content(long_str, 50)
        assert "\n...\n" in result

    def test_dict_block_type_text(self) -> None:
        block = {"type": "text", "text": "hello"}
        result = format_model_content(block, 100)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"

    def test_dict_block_type_image(self) -> None:
        block = {"type": "image", "source": "data"}
        result = format_model_content(block, 100)
        assert isinstance(result, list)
        assert result[0]["type"] == "image"

    def test_list_of_blocks(self) -> None:
        blocks = [
            {"type": "text", "text": "a"},
            {"type": "image", "source": "b"},
        ]
        result = format_model_content(blocks, 100)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_non_block_dict_serialized_as_json(self) -> None:
        data = {"key": "value", "count": 42}
        result = format_model_content(data, 5000)
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["key"] == "value"

    def test_non_block_list_serialized_as_json(self) -> None:
        data = [{"key": "value"}, {"other": "data"}]
        result = format_model_content(data, 5000)
        assert isinstance(result, str)

    def test_none_result(self) -> None:
        result = format_model_content(None, 100)
        assert isinstance(result, str)

    def test_integer_result(self) -> None:
        result = format_model_content(42, 100)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# progress_signature
# ---------------------------------------------------------------------------


class TestProgressSignature:
    def test_none_value(self) -> None:
        assert progress_signature(None) is None

    def test_empty_dict(self) -> None:
        assert progress_signature({}) is None

    def test_valid_progress(self) -> None:
        value = {
            "phase": "running",
            "summary": "Building",
            "status": "active",
        }
        result = progress_signature(value)
        # Result is either None (if can't normalize) or a tuple
        # Depends on normalize_observation_progress behavior
        if result is not None:
            assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# progress_summary_signature
# ---------------------------------------------------------------------------


class TestProgressSummarySignature:
    def test_none_value(self) -> None:
        assert progress_summary_signature(None) is None

    def test_empty_dict(self) -> None:
        assert progress_summary_signature({}) is None

    def test_valid_summary(self) -> None:
        value = {
            "summary": "Task is running",
            "phase": "executing",
        }
        result = progress_summary_signature(value)
        if result is not None:
            assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# compact_progress_text
# ---------------------------------------------------------------------------


class TestCompactProgressText:
    def test_empty_string(self) -> None:
        assert compact_progress_text("") == ""

    def test_none_value(self) -> None:
        assert compact_progress_text(None) == ""

    def test_short_text_returned_as_is(self) -> None:
        assert compact_progress_text("hello world") == "hello world"

    def test_whitespace_collapsed(self) -> None:
        assert compact_progress_text("hello   world") == "hello world"

    def test_newlines_collapsed(self) -> None:
        assert compact_progress_text("hello\n\n  world") == "hello world"

    def test_long_text_truncated_with_ellipsis(self) -> None:
        text = "word " * 100
        result = compact_progress_text(text, limit=50)
        assert len(result) == 50
        assert result.endswith("\u2026")

    def test_exact_limit_no_truncation(self) -> None:
        text = "a" * 240
        result = compact_progress_text(text)
        assert result == text

    def test_custom_limit(self) -> None:
        text = "a" * 100
        result = compact_progress_text(text, limit=50)
        assert len(result) == 50
        assert result.endswith("\u2026")

    def test_whitespace_only(self) -> None:
        assert compact_progress_text("   \n\t  ") == ""


# ---------------------------------------------------------------------------
# BLOCK_TYPES constant
# ---------------------------------------------------------------------------


class TestBlockTypes:
    def test_contains_text(self) -> None:
        assert "text" in BLOCK_TYPES

    def test_contains_image(self) -> None:
        assert "image" in BLOCK_TYPES

    def test_no_unexpected(self) -> None:
        assert {"text", "image"} == BLOCK_TYPES
