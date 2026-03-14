from __future__ import annotations

from pathlib import Path

import pytest

from hermit.builtin.feishu.reply import (
    RichCardBuilder,
    ToolStep,
    _extract_balanced_json,
    _extract_key_input,
    _extract_notes,
    _parse_table_tag,
    _render_table_as_markdown,
    _split_with_highlights,
    _summarize_result,
    build_approval_card,
    build_approval_resolution_card,
    build_completion_status_card,
    build_error_card,
    build_progress_card,
    build_result_card,
    build_result_card_with_process,
    build_task_topic_card,
    format_tool_start_hint,
    format_tool_step_text,
    make_tool_step,
    patch_card,
    reply_card_return_id,
    reply_with_card,
    send_at_mention_reply,
    send_card,
    send_text_message,
    send_text_reply,
    send_thread_text_reply,
    smart_reply,
    smart_send_message,
    upload_image_path,
)


def test_reply_helpers_extract_balanced_json_and_tables() -> None:
    tag = (
        '<table columns={[{"title":"Name","dataIndex":"name"},{"title":"Score","dataIndex":"score"}]} '
        'data={[{"name":"Beta|A","score":"10"},{"name":"Gamma","score":"11"}]}/>'
    )

    balanced = _extract_balanced_json(
        'prefix {"outer":{"quoted":"a } still string","inner":[1,2]}} suffix',
        7,
    )
    columns, rows = _parse_table_tag(tag)
    rendered = _render_table_as_markdown(columns, rows)

    assert balanced == '{"outer":{"quoted":"a } still string","inner":[1,2]}}'
    assert columns[0]["title"] == "Name"
    assert rows[0]["name"] == "Beta|A"
    assert "| Name | Score |" in rendered
    assert "Beta\\|A" in rendered


def test_build_result_card_renders_highlights_notes_and_table_fallback() -> None:
    card = build_result_card(
        "# Report\n\nIntro paragraph.\n\n<highlight>Watch this edge case.</highlight>\n"
        "<note>Internal follow-up only.</note>\n\n"
        '<table columns={[{"title":"Name","dataIndex":"name"}]} data={[{"name":"Beta"}]}/>\n'
        "\n---\n\nTail section",
        locale="en-US",
    )
    bad_table_card = build_result_card(
        "Broken table\n\n<table columns={oops} data={[]}/>",
        locale="en-US",
    )

    elements = card["body"]["elements"]
    assert elements[0]["content"] == "Intro paragraph."
    assert elements[1]["content"] == "**📌** Watch this edge case."
    assert "| Name |" in elements[2]["content"]
    assert elements[3]["tag"] == "hr"
    assert elements[-1]["content"] == "*Internal follow-up only.*"
    assert any("```" in element["content"] for element in bad_table_card["body"]["elements"])


def test_reply_progress_and_resolution_cards_preserve_operator_context() -> None:
    step = make_tool_step(
        "grok_search",
        {"query": "latest model release"},
        {"status": "ok", "summary": "done"},
        1530,
        locale="en-US",
    )
    progress = build_progress_card([step], current_hint="Still working", locale="en-US")
    result_card = build_result_card_with_process("All set", [step], locale="en-US")
    approved = build_approval_resolution_card("approve", "appr-1", "Proceed", locale="en-US")
    completed = build_completion_status_card("Finished cleanly", locale="en-US")
    errored = build_error_card("Need retry", locale="en-US")

    assert format_tool_start_hint("grok_search", {"query": "latest model release"}, locale="en-US").startswith(
        "Running Deep search"
    )
    assert format_tool_step_text(step) == 'Deep search  "latest model release"  1.5s'
    assert progress["config"]["summary"]["content"] == "Still working"
    assert progress["body"]["elements"][0]["content"].startswith("**Deep search**")
    assert result_card["body"]["elements"][-2]["tag"] == "hr"
    assert result_card["body"]["elements"][-1]["tag"] == "collapsible_panel"
    assert approved["header"]["template"] == "green"
    assert completed["header"]["template"] == "green"
    assert errored["header"]["template"] == "red"


def test_reply_helpers_split_highlights_and_notes_without_losing_plain_text() -> None:
    segments = _split_with_highlights("before <highlight>important</highlight> after")
    clean_text, notes = _extract_notes("Main body<note>remember me</note>")

    assert segments == [("text", "before "), ("highlight", "important"), ("text", " after")]
    assert clean_text == "Main body"
    assert notes == ["remember me"]


def test_reply_helper_branches_cover_key_input_summaries_and_compaction() -> None:
    long_url = "https://example.com/" + "a" * 80
    long_path = "/tmp/" + "b" * 60

    assert _extract_key_input("web_fetch", {"url": long_url}).endswith("…")
    assert _extract_key_input("write_file", {"path": long_path}).startswith("…")
    assert _extract_key_input("schedule_create", {"title": "Nightly digest"}) == "Nightly digest"
    assert _extract_key_input("custom_tool", {"note": "x" * 50}).endswith("…")
    assert _summarize_result([{"type": "image"}], locale="en-US") == "[image result]"
    assert _summarize_result({"type": "image"}, locale="en-US") == "[image result]"

    builder = RichCardBuilder("Body", locale="en-US")
    assert builder._compact_paragraphs("a\n\nb") == "a\nb"
    assert builder._compact_paragraphs("- item\n\nnext") == "- item\n\nnext"
    assert builder._compact_paragraphs("```py\nprint(1)\n```\n\nTail") == "```py\nprint(1)\n```\n\nTail"

    block_elements = builder._build_block_elements("1. item\n## Next\nBody")
    heading = next(element for element in block_elements if element.get("content") == "**Next**")
    assert heading["margin"] == "10px 0 2px 0"


def test_build_approval_and_task_topic_cards_cover_rich_branches() -> None:
    steps = [
        ToolStep(name="grok_search", display="Deep search", key_input='"query"', summary="ok", elapsed_ms=1234),
        ToolStep(name="read_file", display="Read file", key_input="", summary="ok", elapsed_ms=250),
    ]
    card = build_approval_card(
        "Need approval",
        "approval-1",
        steps,
        title="Confirm action",
        detail="Detailed why",
        sections=[
            {"title": "Impact", "items": ["Writes files", "Calls API"]},
            type("Section", (), {"title": "Scope", "items": ("workspace",)})(),
        ],
        command_preview="git status",
        target_path="/tmp/demo.txt",
        workspace_root="/tmp",
        grant_scope_dir="/tmp/project",
        locale="en-US",
    )
    denied = build_approval_resolution_card("deny", "approval-1", "Handled", locale="en-US")
    processed = build_approval_resolution_card("processed", "approval-1", "Handled", locale="en-US")
    topic = build_task_topic_card(
        {
            "current_hint": "Still working",
            "current_phase": "running",
            "current_progress_percent": "50",
            "status": "failed",
            "items": [
                {"phase": "planning", "text": "Planned", "progress_percent": 10},
                {"kind": "tool", "text": "Executed"},
            ],
        },
        title="Task topic",
        locale="en-US",
    )

    elements = card["body"]["elements"]
    assert card["header"]["title"]["content"] == "Confirm action"
    assert any(element["tag"] == "collapsible_panel" for element in elements)
    assert any("Impact" in element.get("content", "") for element in elements if element["tag"] == "markdown")
    assert any("/tmp/demo.txt" in element.get("content", "") for element in elements if element["tag"] == "markdown")
    assert denied["header"]["template"] == "red"
    assert processed["header"]["template"] == "grey"
    assert topic["header"]["template"] == "red"
    assert topic["body"]["elements"][0]["content"].startswith("**Running")


class _Resp:
    def __init__(self, *, ok: bool = True, code: int = 0, msg: str = "ok", data=None) -> None:
        self._ok = ok
        self.code = code
        self.msg = msg
        self.data = data

    def success(self) -> bool:
        return self._ok


def test_reply_senders_cover_card_text_and_upload_paths(tmp_path: Path) -> None:
    class Client:
        def __init__(self) -> None:
            self.replies: list[object] = []
            self.creates: list[object] = []
            self.patches: list[object] = []
            self.images: list[object] = []
            self.im = type(
                "IM",
                (),
                {
                    "v1": type(
                        "V1",
                        (),
                        {
                            "message": type(
                                "MessageAPI",
                                (),
                                {
                                    "reply": self.reply,
                                    "create": self.create,
                                    "patch": self.patch,
                                },
                            )(),
                            "image": type("ImageAPI", (), {"create": self.create_image})(),
                        },
                    )()
                },
            )()

        def reply(self, request):
            self.replies.append(request)
            return _Resp(ok=True, data=type("Data", (), {"message_id": "reply-1"})())

        def create(self, request):
            self.creates.append(request)
            return _Resp(ok=True, data=type("Data", (), {"message_id": "msg-1"})())

        def patch(self, request):
            self.patches.append(request)
            return _Resp(ok=True)

        def create_image(self, request):
            self.images.append(request)
            return _Resp(ok=True, data=type("Data", (), {"image_key": "img-1"})())

    client = Client()
    image_path = tmp_path / "demo.png"
    image_path.write_bytes(b"image-bytes")

    assert send_text_reply(client, "om_1", "hello") is True
    assert send_thread_text_reply(client, "om_1", "thread") is True
    assert send_at_mention_reply(client, "om_1", "ou_123") is True
    assert reply_with_card(client, "om_1", "# Hello") is True
    assert send_text_message(client, "oc_1", "hi") == "msg-1"
    assert send_card(client, "oc_1", {"schema": "2.0"}) == "msg-1"
    assert patch_card(client, "om_1", {"schema": "2.0"}) is True
    assert reply_card_return_id(client, "om_1", {"schema": "2.0"}) == "reply-1"
    assert upload_image_path(client, image_path) == "img-1"
    assert smart_reply(client, "om_1", "plain text") is True
    assert smart_reply(client, "om_1", "# markdown") is True
    assert smart_send_message(client, "oc_1", "plain text") == "msg-1"
    assert smart_send_message(client, "oc_1", "# markdown") == "msg-1"

    assert len(client.replies) >= 5
    assert len(client.creates) >= 4
    assert len(client.patches) == 1
    assert len(client.images) == 1


def test_reply_senders_cover_failure_fallbacks(monkeypatch) -> None:
    class Client:
        def __init__(self) -> None:
            self.replied = 0
            self.created = 0
            self.patched = 0
            self.im = type(
                "IM",
                (),
                {
                    "v1": type(
                        "V1",
                        (),
                        {
                            "message": type(
                                "MessageAPI",
                                (),
                                {
                                    "reply": self.reply,
                                    "create": self.create,
                                    "patch": self.patch,
                                },
                            )(),
                            "image": type("ImageAPI", (), {"create": self.create_image})(),
                        },
                    )()
                },
            )()

        def reply(self, request):
            self.replied += 1
            return _Resp(ok=False, code=500, msg="reply failed")

        def create(self, request):
            self.created += 1
            return _Resp(ok=False, code=500, msg="create failed")

        def patch(self, request):
            self.patched += 1
            return _Resp(ok=False, code=500, msg="patch failed")

        def create_image(self, request):
            return _Resp(ok=False, msg="upload failed", data=None)

    client = Client()

    assert send_text_reply(client, "om_1", "hello") is False
    assert send_thread_text_reply(client, "om_1", "thread") is False
    assert reply_with_card(client, "om_1", "# title") is False
    assert send_text_message(client, "oc_1", "hello") is None
    assert send_card(client, "oc_1", {"schema": "2.0"}) is None
    assert patch_card(client, "om_1", {"schema": "2.0"}) is False
    assert reply_card_return_id(client, "om_1", {"schema": "2.0"}) is None
    with pytest.raises(RuntimeError, match="Failed to upload image"):
        upload_image_path(client, Path(__file__))
