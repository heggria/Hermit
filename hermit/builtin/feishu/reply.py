"""Feishu message sending / replying helpers using lark-oapi SDK."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_CARD_MAX_BYTES = 28_000  # 30KB limit with safety margin
_SUMMARY_MAX_CHARS = 80
_SUBTITLE_MAX_CHARS = 60
_HR_NEEDS_BLANK = re.compile(r"(?<!\n)\n---")
_FEISHU_IMAGE_TAG = re.compile(r"<feishu_image\s+key=['\"]([^'\"]+)['\"]\s*/?>")
_NOTE_TAG = re.compile(r"<note>(.*?)</note>", re.DOTALL)
_HIGHLIGHT_TAG = re.compile(r"<highlight>(.*?)</highlight>", re.DOTALL)
_SECTION_HEADING_RE = re.compile(r"(?m)^##\s+(.+)$")
_LEADING_HR_RE = re.compile(r"^(?:---\s*\n+)+")
# Matches blocks that look like dense attribute / key-value lists:
# every \n\n-separated chunk is short (no code fences, no long paragraphs).
_DENSE_BLOCK_RE = re.compile(r"```")
_LIST_ITEM_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+\.)\s")
# Matches JSX-like <table columns={[...]} data={[...]}/>
_TABLE_TAG_RE = re.compile(r"<table\s+.*?/>", re.DOTALL)

# Detects any markdown or feishu-extension signal that requires card rendering.
# Plain sentences with no formatting are sent as text messages for a lighter UX.
_MARKDOWN_SIGNAL = re.compile(
    r"""
    \*\*            |  # bold
    ~~              |  # strikethrough
    ^\#+\s          |  # headings (\# escapes # which is a comment char in VERBOSE)
    ^\s*[-*+]\s     |  # unordered list item
    ^\s*\d+\.\s     |  # ordered list item
    ^---\s*$        |  # horizontal rule
    `               |  # inline code or code block
    \[.+?\]\(.+?\)  |  # markdown link
    <at\s           |  # @mention
    <font\s         |  # colored text
    <text_tag\s     |  # colored tag
    <highlight>     |  # highlight block
    <note>          |  # note block
    <table\s        |  # table
    <row>           |  # column layout
    <feishu_image\s |  # Hermit image tag
    <button\s          # button
    """,
    re.VERBOSE | re.MULTILINE,
)


# Tools that are internal housekeeping steps and should NOT be shown to the user
# as work-progress items (e.g. adding an emoji reaction to ack a message).
_SKIP_TOOLS: frozenset[str] = frozenset({
    "feishu_react",            # emoji reaction — internal ack, not a user task
    "image_store_from_feishu", # image pre-processing — internal, not user-visible
})

# Plain-text labels for tool names shown in progress cards.  No emoji.
_TOOL_DISPLAY: dict[str, str] = {
    "web_search":           "搜索",
    "web_fetch":            "读取网页",
    "grok_search":          "深度搜索",
    "read_file":            "读取文件",
    "write_file":           "写入文件",
    "read_hermit_file":  "读取文件",
    "write_hermit_file": "写入文件",
    "list_hermit_files": "列出文件",
    "schedule_create":      "创建任务",
    "schedule_list":        "查看任务",
    "schedule_history":     "执行历史",
    "codex_exec":           "运行 Codex",
}


@dataclass
class ToolStep:
    """Records one completed tool invocation for progress display."""

    name: str
    display: str    # plain-text label, e.g. "深度搜索"
    key_input: str  # key argument extracted for display, e.g. '"Iran situation today"'
    summary: str    # brief result snippet
    elapsed_ms: int


def _extract_key_input(name: str, tool_input: dict) -> str:
    """Extract the most meaningful display string from a tool's input dict."""
    if name in ("web_search", "grok_search"):
        q = tool_input.get("query", "")
        return f'"{q}"' if q else ""
    if name == "web_fetch":
        url = str(tool_input.get("url", ""))
        return url[:60] + "…" if len(url) > 60 else url
    if name in (
        "read_file", "write_file",
        "read_hermit_file", "write_hermit_file", "list_hermit_files",
    ):
        p = str(tool_input.get("path", tool_input.get("filename", "")))
        return ("…" + p[-40:]) if len(p) > 40 else p
    if name == "schedule_create":
        return tool_input.get("title", "")
    # Fallback: first non-empty string value
    for v in tool_input.values():
        if isinstance(v, str) and v.strip():
            val = v.strip()
            return val[:40] + "…" if len(val) > 40 else val
    return ""


def _summarize_result(result: Any) -> str:
    """Produce a short one-line summary of a tool result for display."""
    if isinstance(result, list):
        if result and isinstance(result[0], dict) and result[0].get("type") == "image":
            return "[image result]"
        text = json.dumps(result, ensure_ascii=False)
    elif isinstance(result, dict):
        if result.get("type") == "image":
            return "[image result]"
        text = json.dumps(result, ensure_ascii=False)
    else:
        text = str(result)
    text = text.strip()
    text = re.sub(r"^```[^\n]*\n", "", text)
    text = re.sub(r"\n```$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:80] + "…" if len(text) > 80 else text


def make_tool_step(name: str, tool_input: dict, result: Any, elapsed_ms: int) -> ToolStep:
    """Build a ToolStep from a completed tool invocation."""
    label = _TOOL_DISPLAY.get(name, name)
    return ToolStep(
        name=name,
        display=label,
        key_input=_extract_key_input(name, tool_input),
        summary=_summarize_result(result),
        elapsed_ms=elapsed_ms,
    )


def format_tool_step_text(step: ToolStep) -> str:
    """Format a ToolStep as a compact single-line text (used in collapsible panel)."""
    elapsed_s = step.elapsed_ms / 1000
    parts = [step.display]
    if step.key_input:
        parts.append(f"  {step.key_input}")
    parts.append(f"  {elapsed_s:.1f}s")
    return "".join(parts)


def format_tool_start_hint(name: str, tool_input: dict) -> str:
    """Build a short 'currently running' hint string for on_tool_start callbacks.

    Returns e.g. '正在深度搜索: "Iran situation today..."...'
    Adapter code can pass this directly as the current_hint of build_progress_card.
    """
    label = _TOOL_DISPLAY.get(name, name)
    key_input = _extract_key_input(name, tool_input)
    hint = f"正在{label}"
    if key_input:
        hint += f": {key_input}"
    hint += "..."
    return hint


def _should_use_card(text: str) -> bool:
    """Return True when text contains markdown/extension signals needing card rendering."""
    return bool(_MARKDOWN_SIGNAL.search(text))


def sanitize_for_feishu(text: str) -> str:
    """Lightweight safety net for hard constraints the LLM cannot guarantee.

    The agent is already instructed to output Feishu-compatible Markdown via
    the ``feishu-output-format`` skill.  This function only handles:
    - HR blank-line rule (cheap regex, avoids occasional LLM slips)
    - Card byte-size hard limit (must be enforced deterministically)
    """
    text = _HR_NEEDS_BLANK.sub("\n\n---", text)

    if len(text.encode("utf-8")) > _CARD_MAX_BYTES:
        while len(text.encode("utf-8")) > _CARD_MAX_BYTES - 100:
            text = text[: len(text) - 200]
        text = text.rsplit("\n", 1)[0] + "\n\n---\n*（内容过长，已截断）*"

    return text


def _strip_markdown_for_summary(text: str) -> str:
    """Derive a readable card preview from markdown-heavy content."""
    summary = text.strip()
    summary = re.sub(r"^#+\s*", "", summary)
    summary = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", summary)
    summary = re.sub(r"[*_~`]+", "", summary)
    summary = re.sub(r"</?[^>]+>", "", summary)
    summary = re.sub(r"\s+", " ", summary).strip()
    if len(summary) > _SUMMARY_MAX_CHARS:
        summary = summary[: _SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return summary or "Hermit 回复"


def _shorten(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _markdown_element(
    content: str,
    *,
    text_size: str = "normal",
    margin: str = "0 0 6px 0",
) -> dict[str, Any]:
    return {
        "tag": "markdown",
        "content": content,
        "text_size": text_size,
        "margin": margin,
    }


def _image_element(image_key: str) -> dict[str, Any]:
    return {
        "tag": "img",
        "img_key": image_key,
        "alt": {"tag": "plain_text", "content": "image"},
        "margin": "8px 0",
        "preview": True,
    }


def _extract_balanced_json(text: str, start: int) -> str:
    """Return the shortest balanced JSON array/object starting at ``start``."""
    open_char = text[start]
    close_char = "]" if open_char == "[" else "}"
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _parse_table_tag(tag_str: str) -> tuple[list, list]:
    """Parse a JSX-style ``<table columns={[...]} data={[...]}/>`` tag.

    Returns (columns, data) where *columns* is a list of
    ``{"title": ..., "dataIndex": ...}`` dicts and *data* is a list of row dicts.
    Both lists are empty on parse failure.
    """
    col_m = re.search(r"columns=\{", tag_str)
    data_m = re.search(r"data=\{", tag_str)
    if not col_m or not data_m:
        return [], []
    try:
        col_json = _extract_balanced_json(tag_str, col_m.end())
        data_json = _extract_balanced_json(tag_str, data_m.end())
        return json.loads(col_json), json.loads(data_json)
    except (json.JSONDecodeError, ValueError, IndexError):
        return [], []


def _render_table_as_markdown(columns: list, data: list) -> str:
    """Convert table columns/data to a pipe-style markdown table.

    Feishu card markdown elements reliably support ``| col | col |`` syntax.
    This avoids the need to match Feishu's native table element schema exactly.
    """
    headers = [col.get("title", "") for col in columns]
    keys = [col.get("dataIndex") or col.get("key") or f"col{i}" for i, col in enumerate(columns)]

    def _escape(val: str) -> str:
        return val.replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(_escape(h) for h in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in data:
        cells = [_escape(str(row.get(k, ""))) for k in keys]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _text_tag(text: str, color: str) -> dict[str, Any]:
    return {
        "tag": "text_tag",
        "text": {"tag": "plain_text", "content": text},
        "color": color,
    }


def _split_with_highlights(text: str) -> list[tuple[str, str]]:
    """Split text into alternating ('text', ...) and ('highlight', ...) segments.

    Preserves the in-line position of <highlight> blocks so they are rendered
    at the correct location in the card body (unlike <note> blocks which are
    appended at the bottom).
    """
    segments: list[tuple[str, str]] = []
    pos = 0
    for m in _HIGHLIGHT_TAG.finditer(text):
        before = text[pos : m.start()]
        if before.strip():
            segments.append(("text", before))
        content = m.group(1).strip()
        if content:
            segments.append(("highlight", content))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        segments.append(("text", tail))
    if not segments:
        segments.append(("text", text))
    return segments


def _highlight_element(content: str) -> dict[str, Any]:
    """Render a <highlight> block as a bold-noted callout.

    Feishu Schema 2.0 has no native highlight/callout element, and ``>``
    blockquotes do not render.  We simulate the effect with a **📌 注意**
    prefix so the block stands out visually.
    """
    return {
        "tag": "markdown",
        "content": f"**📌** {content}",
        "text_size": "normal",
        "margin": "6px 0 6px 0",
    }


def _extract_notes(text: str) -> tuple[str, list[str]]:
    """Pull <note>…</note> blocks out of text.

    Schema 2.0 does not support a native ``note`` element, so we render them
    as small italic markdown appended after the main body.

    Returns (text_without_notes, list_of_note_contents).
    """
    notes = [m.group(1).strip() for m in _NOTE_TAG.finditer(text)]
    clean = _NOTE_TAG.sub("", text).rstrip()
    return clean, notes


def _tokenize_rich_text(text: str) -> list[tuple[str, str]]:
    """Split text into ("text"|"image"|"table", value) tokens in source order."""
    # Collect all special-tag matches then sort by position so we process them
    # in the order they appear, regardless of which regex found them first.
    specials: list[tuple[int, str, re.Match]] = []
    for m in _FEISHU_IMAGE_TAG.finditer(text):
        specials.append((m.start(), "image", m))
    for m in _TABLE_TAG_RE.finditer(text):
        specials.append((m.start(), "table", m))
    specials.sort(key=lambda x: x[0])

    tokens: list[tuple[str, str]] = []
    position = 0
    for _, kind, match in specials:
        before = text[position : match.start()]
        if before.strip():
            tokens.append(("text", before))
        if kind == "image":
            tokens.append(("image", match.group(1)))
        else:
            tokens.append(("table", match.group(0)))
        position = match.end()

    tail = text[position:]
    if tail.strip():
        tokens.append(("text", tail))
    return tokens


def _split_on_dividers(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^\s*---\s*$", text)
    blocks: list[tuple[str, str]] = []
    for index, part in enumerate(parts):
        if part.strip():
            blocks.append(("text", part.strip()))
        if index < len(parts) - 1:
            blocks.append(("hr", ""))
    return blocks


def _extract_section_blocks(text: str) -> tuple[str, list[tuple[str, str]]]:
    matches = list(_SECTION_HEADING_RE.finditer(text))
    if not matches:
        return text.strip(), []

    intro = text[: matches[0].start()].strip()
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        heading = match.group(1).strip()
        body = text[start:end].strip()
        sections.append((heading, body))
    return intro, sections


def _header_template(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("error", "failed", "failure", "异常", "错误", "失败")):
        return "red"
    if any(token in lowered for token in ("warning", "warn", "注意", "风险", "谨慎", "提醒")):
        return "orange"
    if any(token in lowered for token in ("success", "done", "完成", "已完成", "成功", "通过")):
        return "green"
    return "blue"


def _header_tags(text: str, section_count: int, image_count: int) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []

    # Count HR-separated non-empty blocks as sections too.
    # e.g. "item1\n\n---\n\nitem2\n\n---\n\nitem3" → hr_block_count=3 → adds 2 to sections
    hr_blocks = [p for p in re.split(r"(?m)^---\s*$", text) if p.strip()]
    total_sections = section_count + max(0, len(hr_blocks) - 1)
    if total_sections >= 2:
        tags.append(_text_tag("结构化", "blue"))

    if image_count > 0:
        tags.append(_text_tag("图文", "violet"))

    # Numbered list (news, rankings, steps) vs bulleted list (feature points)
    if re.search(r"(?m)^\s*\d+\.\s", text):
        tags.append(_text_tag("列表", "indigo"))
    elif re.search(r"(?m)^\s*[-*+]\s", text):
        tags.append(_text_tag("要点", "turquoise"))

    return tags[:3]


class RichCardBuilder:
    """Build richer product-style Feishu cards for structured replies."""

    def __init__(self, text: str) -> None:
        self.adapted = sanitize_for_feishu(text)

    def build(self) -> dict[str, Any]:
        title, body_text = self._extract_title(self.adapted)
        body_elements = self._build_body_elements(body_text)
        summary_source = title or self.adapted
        intro, sections = self._extract_topology(body_text)
        image_count = len(_FEISHU_IMAGE_TAG.findall(body_text))

        card: dict[str, Any] = {
            "schema": "2.0",
            "config": {
                "update_multi": True,
                "summary": {"content": _strip_markdown_for_summary(summary_source)},
            },
            "body": {
                "padding": "8px 12px 12px 12px",
                "elements": body_elements or [_markdown_element("", margin="0")],
            },
        }

        if title:
            header: dict[str, Any] = {
                "title": {"content": title, "tag": "plain_text"},
                "template": _header_template(self.adapted),
                "padding": "12px 16px 8px 16px",
            }
            # Subtitle is only meaningful when there are ## sections: in that case
            # `intro` is a distinct preamble *before* the sections, not the body itself.
            # Without ## sections, intro == full body text → showing it as subtitle
            # would repeat the first paragraph verbatim.
            intro_clean = _LEADING_HR_RE.sub("", intro).strip()
            # Also strip any trailing HRs — the agent sometimes ends an intro
            # paragraph with "---" which would bleed into the subtitle text.
            intro_clean = re.sub(r"(\s*\n---\s*)+$", "", intro_clean).strip()
            if sections and intro_clean:
                subtitle_source = _strip_markdown_for_summary(intro_clean)
                if subtitle_source and subtitle_source != title:
                    header["subtitle"] = {
                        "content": _shorten(subtitle_source, _SUBTITLE_MAX_CHARS),
                        "tag": "plain_text",
                    }
            tags = _header_tags(body_text, len(sections), image_count)
            if tags:
                header["text_tag_list"] = tags
            card["header"] = header

        return card

    @staticmethod
    def _extract_title(text: str) -> tuple[str, str]:
        header_match = re.match(r"^#\s+(.+)\n?", text)
        if not header_match:
            return "", text
        return header_match.group(1).strip(), text[header_match.end():].lstrip("\n")

    @staticmethod
    def _extract_topology(text: str) -> tuple[str, list[tuple[str, str]]]:
        blocks = _tokenize_rich_text(text)
        first_text = next((value for kind, value in blocks if kind == "text" and value.strip()), "")
        return _extract_section_blocks(first_text)

    def _build_body_elements(self, text: str) -> list[dict[str, Any]]:
        text_no_notes, notes = _extract_notes(text)
        elements: list[dict[str, Any]] = []
        for token_type, token_value in _tokenize_rich_text(text_no_notes):
            if token_type == "image":
                elements.append(_image_element(token_value))
                continue
            if token_type == "table":
                columns, data = _parse_table_tag(token_value)
                if columns and data:
                    md_table = _render_table_as_markdown(columns, data)
                    elements.append(_markdown_element(md_table, margin="8px 0"))
                else:
                    # Parse failed: show raw tag in a code block so content isn't lost
                    elements.append(_markdown_element(f"```\n{token_value}\n```"))
                continue
            elements.extend(self._build_text_elements(token_value))
        for note_content in notes:
            elements.append(_markdown_element(
                f"*{note_content}*",
                text_size="small",
                margin="10px 0 0 0",
            ))
        return elements

    @staticmethod
    def _compact_paragraphs(text: str) -> str:
        """Replace double newlines with single newlines when the block is a dense
        attribute/key-value list (no code fences, all chunks are short lines).

        Feishu's markdown renderer turns \\n\\n into a ~20 px paragraph gap,
        which is far too large for compact lists like ``**位置：** value``.
        Collapsing to \\n yields a regular line-break (~6 px).

        IMPORTANT: blocks that contain list items are left untouched.
        In Feishu's renderer a single \\n after a list item causes the next
        line to be indented as a continuation of that bullet.  The \\n\\n is
        required to break out of the list context cleanly.
        """
        if _DENSE_BLOCK_RE.search(text):
            # Code fences need double-newlines to stay intact — leave untouched.
            return text
        if re.search(r"(?m)^\s*(?:[-*+]|\d+\.)\s", text):
            # List items present — do not compact to avoid rendering artefacts.
            return text
        chunks = text.split("\n\n")
        # Only compact if every chunk is short (no multi-sentence paragraphs)
        if all(len(c.strip()) < 200 for c in chunks if c.strip()):
            return "\n".join(chunks)
        return text

    def _build_text_elements(self, text: str) -> list[dict[str, Any]]:
        elements: list[dict[str, Any]] = []
        for block_type, block_value in _split_on_dividers(text):
            if block_type == "hr":
                elements.append({"tag": "hr", "margin": "6px 0"})
                continue
            elements.extend(self._build_block_elements(block_value))
        return elements

    def _build_block_elements(self, block_value: str) -> list[dict[str, Any]]:
        """Process a single text block, rendering <highlight> tags inline."""
        elements: list[dict[str, Any]] = []
        for seg_type, seg_value in _split_with_highlights(block_value):
            if seg_type == "highlight":
                elements.append(_highlight_element(seg_value))
                continue
            intro, sections = _extract_section_blocks(seg_value)
            if intro:
                intro_compacted = self._compact_paragraphs(intro)
                intro_size = "medium" if len(intro_compacted) <= 120 and "\n" not in intro_compacted else "normal"
                elements.append(_markdown_element(intro_compacted, text_size=intro_size, margin="0 0 6px 0"))
            if sections:
                # Track previous content to detect list→heading transitions.
                # When a markdown element ends with a list item, Feishu's renderer
                # absorbs the element's bottom margin into the list's own padding,
                # leaving virtually no gap before the next element.  We compensate
                # by adding explicit top margin on headings that follow list content.
                prev_content: str = intro
                for heading, body in sections:
                    has_list_above = bool(_LIST_ITEM_RE.search(prev_content or ""))
                    heading_margin = "10px 0 2px 0" if has_list_above else "2px 0 2px 0"
                    elements.append(
                        _markdown_element(
                            f"**{heading}**",
                            text_size="heading",
                            margin=heading_margin,
                        )
                    )
                    if body:
                        compacted = self._compact_paragraphs(body)
                        elements.append(_markdown_element(compacted, margin="0 0 6px 0"))
                    prev_content = body
            elif seg_value.strip() and not intro:
                elements.append(_markdown_element(seg_value.strip(), margin="0 0 6px 0"))
        return elements


def send_text_reply(client: Any, message_id: str, text: str) -> bool:
    """Reply to a specific message with plain text."""
    from lark_oapi.api.im.v1 import (
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )

    request = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .msg_type("text")
            .build()
        )
        .build()
    )
    response = client.im.v1.message.reply(request)
    if not response.success():
        log.error("reply failed: code=%s msg=%s", response.code, response.msg)
        return False
    return True


def send_thread_text_reply(client: Any, message_id: str, text: str) -> bool:
    """Reply to a message with plain text, routed into the thread/topic.

    Unlike send_text_reply() which posts to the main chat stream,
    reply_in_thread=True ensures the message only appears inside the
    thread/topic attached to the target message.  Use this for per-tool-call
    progress updates so they don't pollute the main conversation.
    """
    from lark_oapi.api.im.v1 import (
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )

    request = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .msg_type("text")
            .reply_in_thread(True)
            .build()
        )
        .build()
    )
    response = client.im.v1.message.reply(request)
    if not response.success():
        log.error(
            "thread reply failed: code=%s msg=%s", response.code, response.msg
        )
        return False
    return True


def send_at_mention_reply(client: Any, message_id: str, sender_open_id: str) -> bool:
    """Reply with an @ mention to notify the original sender that the task is done.

    Sends a plain-text reply containing only the at-mention tag so the sender
    receives an explicit notification even if they scrolled away.
    """
    at_text = f'<at user_id="{sender_open_id}"></at>'
    return send_text_reply(client, message_id, at_text)


def reply_with_card(client: Any, message_id: str, text: str) -> bool:
    """Reply to a message with a card containing rendered Markdown.

    Falls back to plain text if card delivery fails.
    """
    from lark_oapi.api.im.v1 import (
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )

    card = build_result_card(text)
    request = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .msg_type("interactive")
            .build()
        )
        .build()
    )
    response = client.im.v1.message.reply(request)
    if not response.success():
        log.warning(
            "card reply failed (code=%s), falling back to text", response.code
        )
        return send_text_reply(client, message_id, text)
    return True


def send_text_message(client: Any, chat_id: str, text: str) -> Optional[str]:
    """Send a new text message to a chat. Returns message_id or None."""
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
    )

    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    response = client.im.v1.message.create(request)
    if not response.success():
        log.error("send failed: code=%s msg=%s", response.code, response.msg)
        return None
    return getattr(response.data, "message_id", None)


def send_card(client: Any, chat_id: str, card: dict) -> Optional[str]:
    """Send an interactive card message. Returns message_id for later PATCH."""
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
    )

    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        .build()
    )
    response = client.im.v1.message.create(request)
    if not response.success():
        log.error("send card failed: code=%s msg=%s", response.code, response.msg)
        return None
    return getattr(response.data, "message_id", None)


def patch_card(client: Any, message_id: str, card: dict) -> bool:
    """Update an already-sent card message via PATCH (for streaming)."""
    from lark_oapi.api.im.v1 import (
        PatchMessageRequest,
        PatchMessageRequestBody,
    )

    request = (
        PatchMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            PatchMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        .build()
    )
    response = client.im.v1.message.patch(request)
    if not response.success():
        log.error("patch card failed: code=%s msg=%s", response.code, response.msg)
        return False
    return True


def upload_image_path(client: Any, path: Path, image_type: str = "message") -> str:
    """Upload a local image file to Feishu and return image_key."""
    from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

    with path.open("rb") as fh:
        response = client.im.v1.image.create(
            CreateImageRequest.builder()
            .request_body(
                CreateImageRequestBody.builder()
                .image_type(image_type)
                .image(fh)
                .build()
            )
            .build()
        )
    if not response.success() or response.data is None or not response.data.image_key:
        raise RuntimeError(f"Failed to upload image: {response.msg}")
    return str(response.data.image_key)


def smart_reply(client: Any, message_id: str, text: str) -> bool:
    """Send as card when text has markdown signals, otherwise as plain text.

    Simple conversational replies (no formatting) get plain text for a lighter
    UX; structured responses with markdown or feishu extensions get a card.
    """
    if _should_use_card(text):
        return reply_with_card(client, message_id, text)
    return send_text_reply(client, message_id, text)


def build_thinking_card(hint: str = "Thinking...") -> dict:
    """Build a minimal card that can be PATCH-updated later."""
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": _strip_markdown_for_summary(hint)},
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": f"*{hint}*"},
            ]
        },
    }


def build_result_card(text: str) -> dict:
    """Build a richer product-style card for structured replies."""
    return RichCardBuilder(text).build()


def build_approval_card(
    text: str,
    approval_id: str,
    steps: list[ToolStep] | None = None,
    *,
    title: str | None = None,
    detail: str | None = None,
    command_preview: str | None = None,
) -> dict[str, Any]:
    """Build a dedicated approval card with approve/deny buttons."""
    button_row = {
        "tag": "column_set",
        "horizontal_spacing": "8px",
        "columns": [
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "不通过"},
                        "type": "default",
                        "width": "fill",
                        "behaviors": [
                            {
                                "type": "callback",
                                "value": {
                                    "kind": "approval",
                                    "action": "deny",
                                    "approval_id": approval_id,
                                },
                            }
                        ],
                    }
                ],
            },
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "通过"},
                        "type": "primary_filled",
                        "width": "fill",
                        "behaviors": [
                            {
                                "type": "callback",
                                "value": {
                                    "kind": "approval",
                                    "action": "approve",
                                    "approval_id": approval_id,
                                },
                            }
                        ],
                    }
                ],
            },
        ],
    }
    clean_text = sanitize_for_feishu(text)
    clean_detail = sanitize_for_feishu(detail or "")
    command = (command_preview or "").strip()
    body_elements: list[dict[str, Any]] = [_markdown_element(clean_text, margin="0 0 8px 0")]
    if clean_detail and clean_detail != clean_text:
        body_elements.append(_markdown_element(clean_detail, text_size="small", margin="0 0 8px 0"))
    if command:
        body_elements.append(
            {
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": "查看原始命令",
                    },
                },
                "elements": [
                    _markdown_element(f"```bash\n{sanitize_for_feishu(command)}\n```", text_size="small", margin="4px 0"),
                ],
                "margin": "0 0 8px 0",
            }
        )
    body_elements.extend(
        [
            _markdown_element(f"*Approval ID: `{approval_id}`*", text_size="small", margin="0 0 10px 0"),
            button_row,
        ]
    )
    if steps:
        lines: list[str] = []
        for step in steps:
            elapsed_s = step.elapsed_ms / 1000
            line = f"✅ {step.display}"
            if step.key_input:
                line += f": {step.key_input}"
            line += f" ({elapsed_s:.1f}s)"
            lines.append(line)
        body_elements.extend(
            [
                {"tag": "hr", "margin": "8px 0"},
                {
                    "tag": "collapsible_panel",
                    "expanded": False,
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": f"已完成步骤 ({len(steps)})",
                        },
                    },
                    "elements": [
                        _markdown_element("\n".join(lines), text_size="small", margin="4px 0"),
                    ],
                },
            ]
        )
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": "任务等待审批"},
        },
        "header": {
            "title": {"content": title or "等待审批", "tag": "plain_text"},
            "template": "orange",
        },
        "body": {
            "padding": "8px 12px 12px 12px",
            "elements": body_elements,
        },
    }


def build_approval_resolution_card(
    action: str,
    approval_id: str,
    text: str,
) -> dict[str, Any]:
    """Build a terminal approval-state card with buttons removed."""
    action_key = action.strip().lower()
    if action_key == "approve":
        title = "已通过"
        template = "green"
    elif action_key == "deny":
        title = "未通过"
        template = "red"
    else:
        title = "已处理"
        template = "grey"
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": title},
        },
        "header": {
            "title": {"content": title, "tag": "plain_text"},
            "template": template,
        },
        "body": {
            "padding": "8px 12px 12px 12px",
            "elements": [
                _markdown_element(sanitize_for_feishu(text), margin="0 0 8px 0"),
                _markdown_element(f"*Approval ID: `{approval_id}`*", text_size="small", margin="0"),
            ],
        },
    }


def build_error_card(hint: str = "处理出错，请稍后重试") -> dict:
    """Build an error-state card for patching when the agent fails."""
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": "❌ 处理失败"},
        },
        "header": {
            "title": {"content": "❌ 处理失败", "tag": "plain_text"},
            "template": "red",
        },
        "body": {
            "padding": "8px 12px 12px 12px",
            "elements": [_markdown_element(hint)],
        },
    }


def reply_card_return_id(client: Any, message_id: str, card: dict) -> Optional[str]:
    """Reply to a message with a card and return the new reply's message_id.

    Returns None on failure so callers can safely skip subsequent PATCH calls.
    Unlike reply_with_card() which only returns bool, this variant exposes the
    reply message_id needed to PATCH the card with progress updates.
    """
    from lark_oapi.api.im.v1 import (
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )

    request = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .msg_type("interactive")
            .build()
        )
        .build()
    )
    response = client.im.v1.message.reply(request)
    if not response.success():
        log.warning(
            "reply_card_return_id failed: code=%s msg=%s", response.code, response.msg
        )
        return None
    return getattr(response.data, "message_id", None)


def build_progress_card(steps: list[ToolStep], current_hint: str = "正在处理...") -> dict:
    """Build a Manus-style progress card, PATCH-updated after each tool call.

    Each completed step gets its own markdown block:

        **深度搜索** · 19.2s
        "Iran situation today March 10 2026..."
        找到 12 条结果，涵盖战争动态、制裁更新...

    Followed by an italic current-state hint line.
    """
    elements: list[dict] = []
    for step in steps:
        elapsed_s = step.elapsed_ms / 1000
        header = f"**{step.display}** · {elapsed_s:.1f}s"
        lines = [header]
        if step.key_input:
            lines.append(step.key_input)
        elements.append(_markdown_element("\n".join(lines), margin="0 0 10px 0"))
    elements.append(_markdown_element(f"*{current_hint}*", margin="4px 0 0 0"))
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": current_hint},
        },
        "header": {
            "title": {"content": "正在处理", "tag": "plain_text"},
            "template": "blue",
        },
        "body": {
            "padding": "8px 12px 12px 12px",
            "elements": elements,
        },
    }


def build_result_card_with_process(text: str, steps: list[ToolStep]) -> dict:
    """Build the final result card, appending a collapsible work-process panel.

    When *steps* is empty the function behaves identically to build_result_card().
    When *steps* is non-empty a collapsible_panel is appended after a divider so
    the user can expand it to review exactly what the agent did.
    """
    card = build_result_card(text)
    if not steps:
        return card

    lines: list[str] = []
    for step in steps:
        elapsed_s = step.elapsed_ms / 1000
        line = f"✅ {step.display}"
        if step.key_input:
            line += f": {step.key_input}"
        line += f" ({elapsed_s:.1f}s)"
        lines.append(line)

    collapsible: dict[str, Any] = {
        "tag": "collapsible_panel",
        "expanded": False,
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"工作过程 ({len(steps)} 步骤)",
            },
        },
        "elements": [
            _markdown_element("\n".join(lines), text_size="small", margin="4px 0"),
        ],
    }

    body = card.setdefault("body", {})
    elements: list[dict] = body.setdefault("elements", [])
    if elements:
        elements.append({"tag": "hr", "margin": "8px 0"})
    elements.append(collapsible)
    return card
