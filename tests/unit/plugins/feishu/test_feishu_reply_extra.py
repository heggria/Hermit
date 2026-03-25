"""Additional tests for reply.py to cover remaining uncovered lines.

Focuses on:
- should_use_card edge cases
- sanitize_for_feishu truncation
- _strip_markdown_for_summary
- _shorten
- _header_template color detection
- _header_tags badge generation
- _split_on_dividers
- _tokenize_rich_text
- _extract_section_blocks
- RichCardBuilder body element building (no title, with title + subtitle)
- build_task_topic_card edge cases
- tool_display / _task_topic_label / _humanize_task_topic_label
- build_thinking_card
- build_completion_status_card without text
"""

from __future__ import annotations

from hermit.plugins.builtin.adapters.feishu.reply import (
    RichCardBuilder,
    ToolStep,
    _extract_section_blocks,
    _header_tags,
    _header_template,
    _humanize_task_topic_label,
    _shorten,
    _split_on_dividers,
    _split_with_highlights,
    _strip_markdown_for_summary,
    _task_topic_label,
    _tokenize_rich_text,
    build_completion_status_card,
    build_result_card,
    build_result_card_with_process,
    build_task_topic_card,
    build_thinking_card,
    sanitize_for_feishu,
    should_use_card,
    tool_display,
)

# ---------------------------------------------------------------------------
# should_use_card
# ---------------------------------------------------------------------------


class TestShouldUseCard:
    def test_plain_text(self) -> None:
        assert should_use_card("Hello world") is False

    def test_bold_text(self) -> None:
        assert should_use_card("This is **bold**") is True

    def test_strikethrough(self) -> None:
        assert should_use_card("This is ~~deleted~~") is True

    def test_heading(self) -> None:
        assert should_use_card("# Title") is True

    def test_unordered_list(self) -> None:
        assert should_use_card("- item 1\n- item 2") is True

    def test_ordered_list(self) -> None:
        assert should_use_card("1. first\n2. second") is True

    def test_hr(self) -> None:
        assert should_use_card("text\n---\nmore") is True

    def test_inline_code(self) -> None:
        assert should_use_card("Use `code` here") is True

    def test_link(self) -> None:
        assert should_use_card("[link](https://example.com)") is True

    def test_feishu_image_tag(self) -> None:
        assert should_use_card('<feishu_image key="img_1"/>') is True

    def test_highlight_tag(self) -> None:
        assert should_use_card("<highlight>important</highlight>") is True

    def test_note_tag(self) -> None:
        assert should_use_card("<note>remember</note>") is True


# ---------------------------------------------------------------------------
# sanitize_for_feishu
# ---------------------------------------------------------------------------


class TestSanitizeForFeishu:
    def test_hr_blank_line_rule(self) -> None:
        text = "text\n---"
        result = sanitize_for_feishu(text)
        assert "\n\n---" in result

    def test_truncation_on_large_text(self) -> None:
        text = "x" * 40_000  # Way over the 28KB limit
        result = sanitize_for_feishu(text)
        assert len(result.encode("utf-8")) <= 28_000

    def test_short_text_passes_through(self) -> None:
        text = "Hello world"
        assert sanitize_for_feishu(text) == "Hello world"


# ---------------------------------------------------------------------------
# _strip_markdown_for_summary
# ---------------------------------------------------------------------------


class TestStripMarkdownForSummary:
    def test_strips_heading_markers(self) -> None:
        result = _strip_markdown_for_summary("## Title here")
        assert result == "Title here"

    def test_strips_links(self) -> None:
        result = _strip_markdown_for_summary("[click](https://example.com)")
        assert result == "click"

    def test_strips_emphasis(self) -> None:
        result = _strip_markdown_for_summary("**bold** and *italic*")
        assert "bold" in result
        assert "*" not in result

    def test_truncates_long_text(self) -> None:
        result = _strip_markdown_for_summary("a" * 200)
        assert len(result) <= 80

    def test_empty_text_returns_fallback(self) -> None:
        result = _strip_markdown_for_summary("", locale="en-US")
        assert len(result) > 0


# ---------------------------------------------------------------------------
# _shorten
# ---------------------------------------------------------------------------


class TestShorten:
    def test_short_text_unchanged(self) -> None:
        assert _shorten("hello", 10) == "hello"

    def test_exactly_at_limit(self) -> None:
        assert _shorten("hello", 5) == "hello"

    def test_truncates_with_ellipsis(self) -> None:
        result = _shorten("hello world", 6)
        assert result.endswith("\u2026")
        assert len(result) <= 6


# ---------------------------------------------------------------------------
# _header_template
# ---------------------------------------------------------------------------


class TestHeaderTemplate:
    def test_error_keywords(self) -> None:
        assert _header_template("Error occurred") == "red"
        assert _header_template("FAILED operation") == "red"

    def test_warning_keywords(self) -> None:
        assert _header_template("Warning: be careful") == "orange"

    def test_success_keywords(self) -> None:
        assert _header_template("Success! All done") == "green"
        assert _header_template("completed successfully") == "green"

    def test_default_blue(self) -> None:
        assert _header_template("Regular title") == "blue"


# ---------------------------------------------------------------------------
# _header_tags
# ---------------------------------------------------------------------------


class TestHeaderTags:
    def test_structured_tag_with_sections(self) -> None:
        tags = _header_tags("text", 2, 0, locale="en-US")
        assert any(t["color"] == "blue" for t in tags)

    def test_media_tag_with_images(self) -> None:
        tags = _header_tags("text", 0, 1, locale="en-US")
        assert any(t["color"] == "violet" for t in tags)

    def test_numbered_list_tag(self) -> None:
        tags = _header_tags("1. first\n2. second", 0, 0, locale="en-US")
        assert any(t["color"] == "indigo" for t in tags)

    def test_bullet_list_tag(self) -> None:
        tags = _header_tags("- item\n- item2", 0, 0, locale="en-US")
        assert any(t["color"] == "turquoise" for t in tags)

    def test_max_3_tags(self) -> None:
        tags = _header_tags("1. item\n- bullet\n## section\n---\nmore", 3, 2, locale="en-US")
        assert len(tags) <= 3

    def test_no_tags_for_plain_text(self) -> None:
        tags = _header_tags("simple text", 0, 0, locale="en-US")
        assert len(tags) == 0


# ---------------------------------------------------------------------------
# _split_on_dividers
# ---------------------------------------------------------------------------


class TestSplitOnDividers:
    def test_no_dividers(self) -> None:
        blocks = _split_on_dividers("just text")
        assert blocks == [("text", "just text")]

    def test_with_divider(self) -> None:
        blocks = _split_on_dividers("part1\n---\npart2")
        assert ("text", "part1") in blocks
        assert ("hr", "") in blocks
        assert ("text", "part2") in blocks

    def test_multiple_dividers(self) -> None:
        blocks = _split_on_dividers("a\n---\nb\n---\nc")
        text_blocks = [b for b in blocks if b[0] == "text"]
        hr_blocks = [b for b in blocks if b[0] == "hr"]
        assert len(text_blocks) == 3
        assert len(hr_blocks) == 2


# ---------------------------------------------------------------------------
# _tokenize_rich_text
# ---------------------------------------------------------------------------


class TestTokenizeRichText:
    def test_plain_text(self) -> None:
        tokens = _tokenize_rich_text("just text")
        assert tokens == [("text", "just text")]

    def test_with_image(self) -> None:
        tokens = _tokenize_rich_text('before <feishu_image key="img_1"/> after')
        assert ("image", "img_1") in tokens

    def test_with_table(self) -> None:
        tag = '<table columns={[{"title":"A","dataIndex":"a"}]} data={[{"a":"1"}]}/>'
        tokens = _tokenize_rich_text(f"before {tag} after")
        assert any(t[0] == "table" for t in tokens)

    def test_mixed_order(self) -> None:
        text = 'text1 <feishu_image key="img_1"/> text2 <feishu_image key="img_2"/> text3'
        tokens = _tokenize_rich_text(text)
        assert len(tokens) == 5


# ---------------------------------------------------------------------------
# _extract_section_blocks
# ---------------------------------------------------------------------------


class TestExtractSectionBlocks:
    def test_no_sections(self) -> None:
        intro, sections = _extract_section_blocks("just intro text")
        assert intro == "just intro text"
        assert sections == []

    def test_with_sections(self) -> None:
        text = "intro\n## First\nbody1\n## Second\nbody2"
        intro, sections = _extract_section_blocks(text)
        assert intro == "intro"
        assert len(sections) == 2
        assert sections[0] == ("First", "body1")
        assert sections[1] == ("Second", "body2")


# ---------------------------------------------------------------------------
# tool_display
# ---------------------------------------------------------------------------


class TestToolDisplay:
    def test_known_tool(self) -> None:
        result = tool_display("web_search", locale="en-US")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_unknown_tool(self) -> None:
        assert tool_display("custom_tool_xyz") == "custom_tool_xyz"


# ---------------------------------------------------------------------------
# _humanize_task_topic_label
# ---------------------------------------------------------------------------


class TestHumanizeTaskTopicLabel:
    def test_basic(self) -> None:
        assert _humanize_task_topic_label("tool.submitted") == "Tool submitted"

    def test_underscore(self) -> None:
        assert _humanize_task_topic_label("task_started") == "Task started"

    def test_empty(self) -> None:
        assert _humanize_task_topic_label("") == ""


# ---------------------------------------------------------------------------
# _task_topic_label
# ---------------------------------------------------------------------------


class TestTaskTopicLabel:
    def test_known_label(self) -> None:
        result = _task_topic_label("tool.submitted", locale="en-US")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_unknown_label(self) -> None:
        result = _task_topic_label("unknown.kind")
        assert result == "Unknown kind"


# ---------------------------------------------------------------------------
# build_thinking_card
# ---------------------------------------------------------------------------


class TestBuildThinkingCard:
    def test_with_hint(self) -> None:
        card = build_thinking_card("Processing...", locale="en-US")
        assert card["schema"] == "2.0"
        assert "*Processing...*" in card["body"]["elements"][0]["content"]

    def test_without_hint(self) -> None:
        card = build_thinking_card(locale="en-US")
        assert card["schema"] == "2.0"
        assert len(card["body"]["elements"]) == 1


# ---------------------------------------------------------------------------
# build_completion_status_card
# ---------------------------------------------------------------------------


class TestBuildCompletionStatusCard:
    def test_without_text(self) -> None:
        card = build_completion_status_card(locale="en-US")
        assert card["header"]["template"] == "green"

    def test_with_text(self) -> None:
        card = build_completion_status_card("All tasks done", locale="en-US")
        assert "All tasks done" in card["body"]["elements"][0]["content"]


# ---------------------------------------------------------------------------
# build_result_card
# ---------------------------------------------------------------------------


class TestBuildResultCard:
    def test_plain_text_card(self) -> None:
        card = build_result_card("Simple text", locale="en-US")
        assert card["schema"] == "2.0"
        assert "header" not in card  # No title → no header

    def test_with_title(self) -> None:
        card = build_result_card("# My Title\n\nBody content", locale="en-US")
        assert card["header"]["title"]["content"] == "My Title"

    def test_with_title_and_sections(self) -> None:
        card = build_result_card(
            "# Title\n\nIntro text\n\n## Section 1\nContent 1\n\n## Section 2\nContent 2",
            locale="en-US",
        )
        assert "header" in card
        # Should have subtitle from intro
        if "subtitle" in card["header"]:
            assert "Intro text" in card["header"]["subtitle"]["content"]


# ---------------------------------------------------------------------------
# build_result_card_with_process
# ---------------------------------------------------------------------------


class TestBuildResultCardWithProcess:
    def test_without_steps(self) -> None:
        card = build_result_card_with_process("Result text", [], locale="en-US")
        # Should behave like build_result_card
        elements = card["body"]["elements"]
        assert not any(e.get("tag") == "collapsible_panel" for e in elements)

    def test_with_steps(self) -> None:
        steps = [
            ToolStep("web_search", "Search", '"test"', "found 5", 1200),
        ]
        card = build_result_card_with_process("Result text", steps, locale="en-US")
        elements = card["body"]["elements"]
        assert any(e.get("tag") == "collapsible_panel" for e in elements)
        assert any(e.get("tag") == "hr" for e in elements)


# ---------------------------------------------------------------------------
# build_task_topic_card edge cases
# ---------------------------------------------------------------------------


class TestBuildTaskTopicCardEdgeCases:
    def test_completed_status_green(self) -> None:
        card = build_task_topic_card(
            {"status": "completed", "current_hint": "Done"},
            locale="en-US",
        )
        assert card["header"]["template"] == "green"

    def test_cancelled_status_red(self) -> None:
        card = build_task_topic_card(
            {"status": "cancelled", "current_hint": "Cancelled"},
            locale="en-US",
        )
        assert card["header"]["template"] == "red"

    def test_running_status_blue(self) -> None:
        card = build_task_topic_card(
            {"status": "running", "current_hint": "Working"},
            locale="en-US",
        )
        assert card["header"]["template"] == "blue"

    def test_invalid_progress_percent(self) -> None:
        card = build_task_topic_card(
            {
                "current_hint": "Working",
                "current_progress_percent": "not_a_number",
                "current_phase": "running",
            },
            locale="en-US",
        )
        assert card is not None

    def test_items_with_progress_percent(self) -> None:
        card = build_task_topic_card(
            {
                "current_hint": "Working",
                "current_phase": "running",
                "items": [
                    {
                        "kind": "tool.submitted",
                        "text": "Searching",
                        "progress_percent": 50,
                        "phase": "running",
                    },
                ],
            },
            locale="en-US",
        )
        assert card is not None

    def test_custom_title(self) -> None:
        card = build_task_topic_card(
            {"current_hint": "Working"},
            title="Custom Title",
            locale="en-US",
        )
        assert card["header"]["title"]["content"] == "Custom Title"

    def test_skips_task_started_for_terminal(self) -> None:
        card = build_task_topic_card(
            {
                "status": "completed",
                "current_hint": "Done",
                "items": [
                    {"kind": "task.started", "text": "Started"},
                    {"kind": "tool.submitted", "text": "Tool ran"},
                ],
            },
            locale="en-US",
        )
        elements = card["body"]["elements"]
        contents = [e.get("content", "") for e in elements]
        # task.started should be skipped for terminal status
        assert not any("Started" in c for c in contents if "Task started" not in c.lower())

    def test_dedup_current_item(self) -> None:
        card = build_task_topic_card(
            {
                "current_hint": "Working",
                "current_phase": "running",
                "current_progress_percent": 50,
                "items": [
                    {"phase": "running", "text": "Working", "progress_percent": 50},
                ],
            },
            locale="en-US",
        )
        # The item matching current state should be skipped
        elements = card["body"]["elements"]
        # Only the current hint element should remain
        assert len(elements) == 1


# ---------------------------------------------------------------------------
# RichCardBuilder internal methods
# ---------------------------------------------------------------------------


class TestRichCardBuilderInternals:
    def test_extract_title_no_match(self) -> None:
        title, body = RichCardBuilder._extract_title("No heading here")
        assert title == ""
        assert body == "No heading here"

    def test_extract_title_with_match(self) -> None:
        title, body = RichCardBuilder._extract_title("# My Title\nBody text")
        assert title == "My Title"
        assert body == "Body text"

    def test_compact_paragraphs_long_chunks(self) -> None:
        builder = RichCardBuilder("x", locale="en-US")
        long_text = ("a" * 250) + "\n\n" + ("b" * 250)
        assert builder._compact_paragraphs(long_text) == long_text


# ---------------------------------------------------------------------------
# _split_with_highlights edge cases
# ---------------------------------------------------------------------------


class TestSplitWithHighlightsEdgeCases:
    def test_no_highlights(self) -> None:
        result = _split_with_highlights("plain text")
        assert result == [("text", "plain text")]

    def test_only_highlight(self) -> None:
        result = _split_with_highlights("<highlight>important</highlight>")
        assert result == [("highlight", "important")]

    def test_empty_string(self) -> None:
        result = _split_with_highlights("")
        assert result == [("text", "")]

    def test_highlight_with_whitespace_only_before(self) -> None:
        result = _split_with_highlights("   <highlight>important</highlight>")
        assert result == [("highlight", "important")]
