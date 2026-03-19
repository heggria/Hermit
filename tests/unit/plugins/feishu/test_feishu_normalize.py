"""Tests for Feishu event normalization (normalize.py)."""

from __future__ import annotations

import json
from typing import Any

from hermit.plugins.builtin.adapters.feishu.normalize import (
    FeishuMessage,
    _collect_image_keys,
    _dedupe_preserve_order,
    _extract_post_text,
    _extract_text,
    normalize_event,
)

# ---------------------------------------------------------------------------
# _collect_image_keys
# ---------------------------------------------------------------------------


def test_collect_image_keys_from_nested_dict() -> None:
    value: dict[str, Any] = {
        "image_key": "img_1",
        "nested": {"image_key": "img_2", "deep": [{"image_key": "img_3"}]},
    }
    keys = _collect_image_keys(value)
    assert keys == ["img_1", "img_2", "img_3"]


def test_collect_image_keys_from_list() -> None:
    value = [{"image_key": "img_a"}, {"other": "no_image"}, {"image_key": "img_b"}]
    keys = _collect_image_keys(value)
    assert keys == ["img_a", "img_b"]


def test_collect_image_keys_returns_empty_for_scalar() -> None:
    assert _collect_image_keys("just a string") == []
    assert _collect_image_keys(42) == []
    assert _collect_image_keys(None) == []


def test_collect_image_keys_skips_empty_image_key() -> None:
    assert _collect_image_keys({"image_key": ""}) == []


def test_collect_image_keys_empty_dict_and_list() -> None:
    assert _collect_image_keys({}) == []
    assert _collect_image_keys([]) == []


# ---------------------------------------------------------------------------
# _dedupe_preserve_order
# ---------------------------------------------------------------------------


def test_dedupe_preserve_order_removes_duplicates() -> None:
    assert _dedupe_preserve_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_dedupe_preserve_order_strips_whitespace_and_skips_empty() -> None:
    assert _dedupe_preserve_order(["  a  ", "", "  ", "a"]) == ["a"]


def test_dedupe_preserve_order_empty_input() -> None:
    assert _dedupe_preserve_order([]) == []


# ---------------------------------------------------------------------------
# _extract_post_text
# ---------------------------------------------------------------------------


def test_extract_post_text_with_title_and_content() -> None:
    parsed: dict[str, Any] = {
        "zh_cn": {
            "title": "Test Title",
            "content": [
                [
                    {"tag": "text", "text": "Hello "},
                    {"tag": "a", "text": "world", "href": "https://example.com"},
                ],
                [
                    {"tag": "at", "user_name": "Alice"},
                    {"tag": "text", "text": " joined"},
                ],
            ],
        }
    }
    result = _extract_post_text(parsed)
    assert "Test Title" in result
    assert "Hello" in result
    assert "world" in result
    assert "@Alice" in result


def test_extract_post_text_at_mention_all() -> None:
    parsed: dict[str, Any] = {
        "en": {
            "title": "",
            "content": [
                [{"tag": "at", "user_id": "all"}],
            ],
        }
    }
    result = _extract_post_text(parsed)
    assert result  # Should include the @all translation


def test_extract_post_text_at_mention_no_name_no_all() -> None:
    parsed: dict[str, Any] = {
        "en": {
            "content": [
                [{"tag": "at", "user_id": "ou_123"}],
            ],
        }
    }
    result = _extract_post_text(parsed)
    # No user_name and not "all" — should not crash
    assert isinstance(result, str)


def test_extract_post_text_with_md_tag() -> None:
    parsed: dict[str, Any] = {
        "en": {
            "content": [
                [{"tag": "md", "text": "markdown content"}],
            ],
        }
    }
    result = _extract_post_text(parsed)
    assert "markdown content" in result


def test_extract_post_text_skips_non_list_paragraphs() -> None:
    parsed: dict[str, Any] = {
        "en": {
            "content": ["not a list paragraph", [{"tag": "text", "text": "valid"}]],
        }
    }
    result = _extract_post_text(parsed)
    assert "valid" in result


def test_extract_post_text_skips_non_dict_nodes() -> None:
    parsed: dict[str, Any] = {
        "en": {
            "content": [["plain string node", {"tag": "text", "text": "valid"}]],
        }
    }
    result = _extract_post_text(parsed)
    assert "valid" in result


def test_extract_post_text_empty_title_and_content() -> None:
    parsed: dict[str, Any] = {"en": {"title": "", "content": []}}
    assert _extract_post_text(parsed) == ""


def test_extract_post_text_with_only_title() -> None:
    parsed: dict[str, Any] = {"en": {"title": "Only Title", "content": []}}
    assert _extract_post_text(parsed) == "Only Title"


def test_extract_post_text_no_nested_dict() -> None:
    # When no value is a dict with 'content' or 'title', payload = parsed itself
    parsed: dict[str, Any] = {
        "content": [[{"tag": "text", "text": "direct"}]],
    }
    result = _extract_post_text(parsed)
    assert "direct" in result


def test_extract_post_text_text_with_whitespace_only_stripped() -> None:
    parsed: dict[str, Any] = {"en": {"content": [[{"tag": "text", "text": "   "}]]}}
    result = _extract_post_text(parsed)
    assert result == ""


def test_extract_post_text_at_tag_with_name_attr() -> None:
    """The 'at' tag may use 'name' instead of 'user_name'."""
    parsed: dict[str, Any] = {"en": {"content": [[{"tag": "at", "name": "Bob"}]]}}
    result = _extract_post_text(parsed)
    assert "@Bob" in result


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------


def test_extract_text_from_text_message() -> None:
    parsed = {"text": "Hello world"}
    text, images = _extract_text(parsed, "text", '{"text":"Hello world"}')
    assert text == "Hello world"
    assert images == []


def test_extract_text_from_image_message() -> None:
    parsed = {"image_key": "img_abc"}
    text, images = _extract_text(parsed, "image", '{"image_key":"img_abc"}')
    assert text == ""
    assert images == ["img_abc"]


def test_extract_text_from_post_message() -> None:
    parsed: dict[str, Any] = {
        "en": {
            "title": "Post Title",
            "content": [[{"tag": "text", "text": "Post body"}]],
        }
    }
    text, _images = _extract_text(parsed, "post", "{}")
    assert "Post Title" in text
    assert "Post body" in text


def test_extract_text_post_empty_returns_empty() -> None:
    parsed: dict[str, Any] = {"en": {"content": []}}
    text, _images = _extract_text(parsed, "post", "{}")
    assert text == ""


def test_extract_text_dict_no_text_key_non_post() -> None:
    parsed: dict[str, Any] = {"other_field": "value"}
    text, _images = _extract_text(parsed, "text", '{"other_field":"value"}')
    assert text == ""


def test_extract_text_string_input() -> None:
    text, images = _extract_text("raw string", "text", "raw string")
    assert text == "raw string"
    assert images == []


def test_extract_text_non_dict_non_string_fallback() -> None:
    text, images = _extract_text(12345, "text", "raw_content")
    assert text == "raw_content"
    assert images == []


def test_extract_text_with_images_in_post() -> None:
    parsed: dict[str, Any] = {
        "en": {
            "content": [
                [
                    {"tag": "text", "text": "See image:"},
                    {"tag": "img", "image_key": "img_in_post"},
                ]
            ]
        }
    }
    _text, images = _extract_text(parsed, "post", "{}")
    assert images == ["img_in_post"]


# ---------------------------------------------------------------------------
# normalize_event
# ---------------------------------------------------------------------------


def test_normalize_event_text_message() -> None:
    event = {
        "message": {
            "chat_id": "oc_123",
            "message_id": "om_456",
            "content": json.dumps({"text": "Hello"}),
            "message_type": "text",
            "chat_type": "p2p",
        },
        "sender": {"sender_id": {"open_id": "ou_789"}},
    }
    msg = normalize_event(event)
    assert msg.chat_id == "oc_123"
    assert msg.message_id == "om_456"
    assert msg.sender_id == "ou_789"
    assert msg.text == "Hello"
    assert msg.message_type == "text"
    assert msg.chat_type == "p2p"
    assert msg.image_keys == []


def test_normalize_event_group_strips_at_mentions() -> None:
    event = {
        "message": {
            "chat_id": "oc_group",
            "message_id": "om_1",
            "content": json.dumps({"text": "@_user_12345 do something"}),
            "message_type": "text",
            "chat_type": "group",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert msg.text == "do something"
    assert msg.chat_type == "group"


def test_normalize_event_group_strips_multiple_at_mentions() -> None:
    event = {
        "message": {
            "chat_id": "oc_group",
            "message_id": "om_1",
            "content": json.dumps({"text": "@_user_111 @_user_222 hello team"}),
            "message_type": "text",
            "chat_type": "group",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert msg.text == "hello team"


def test_normalize_event_image_message() -> None:
    event = {
        "message": {
            "chat_id": "oc_1",
            "message_id": "om_2",
            "content": json.dumps({"image_key": "img_abc"}),
            "message_type": "image",
            "chat_type": "p2p",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert msg.text == ""
    assert msg.image_keys == ["img_abc"]


def test_normalize_event_invalid_json_content() -> None:
    event = {
        "message": {
            "chat_id": "oc_1",
            "message_id": "om_3",
            "content": "not json {{{",
            "message_type": "text",
            "chat_type": "p2p",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert msg.text == "not json {{{"


def test_normalize_event_non_string_content() -> None:
    event = {
        "message": {
            "chat_id": "oc_1",
            "message_id": "om_4",
            "content": 12345,
            "message_type": "text",
            "chat_type": "p2p",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert msg.text == "12345"


def test_normalize_event_missing_fields_default() -> None:
    event: dict[str, Any] = {"message": {}, "sender": {}}
    msg = normalize_event(event)
    assert msg.chat_id == ""
    assert msg.message_id == ""
    assert msg.sender_id == ""
    assert msg.message_type == "text"
    assert msg.chat_type == "p2p"


def test_normalize_event_reply_to_message_id() -> None:
    event = {
        "message": {
            "chat_id": "oc_1",
            "message_id": "om_5",
            "content": json.dumps({"text": "reply"}),
            "message_type": "text",
            "chat_type": "p2p",
            "reply_to_message_id": "om_parent",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert msg.reply_to_message_id == "om_parent"


def test_normalize_event_parent_id_fallback() -> None:
    event = {
        "message": {
            "chat_id": "oc_1",
            "message_id": "om_6",
            "content": json.dumps({"text": "reply"}),
            "message_type": "text",
            "chat_type": "p2p",
            "parent_id": "om_parent2",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert msg.reply_to_message_id == "om_parent2"


def test_normalize_event_quoted_message_id() -> None:
    event = {
        "message": {
            "chat_id": "oc_1",
            "message_id": "om_7",
            "content": json.dumps({"text": "quoting"}),
            "message_type": "text",
            "chat_type": "p2p",
            "quoted_message_id": "om_quoted",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert msg.quoted_message_id == "om_quoted"


def test_normalize_event_root_id_fallback_for_quoted() -> None:
    event = {
        "message": {
            "chat_id": "oc_1",
            "message_id": "om_8",
            "content": json.dumps({"text": "quoting"}),
            "message_type": "text",
            "chat_type": "p2p",
            "root_id": "om_root",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert msg.quoted_message_id == "om_root"


def test_normalize_event_reply_in_thread_from_message_id_fallback() -> None:
    event = {
        "message": {
            "chat_id": "oc_1",
            "message_id": "om_9",
            "content": json.dumps({"text": "thread"}),
            "message_type": "text",
            "chat_type": "p2p",
            "reply_in_thread_from_message_id": "om_thread",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert msg.reply_to_message_id == "om_thread"


def test_normalize_event_upper_message_id_fallback_for_quoted() -> None:
    event = {
        "message": {
            "chat_id": "oc_1",
            "message_id": "om_10",
            "content": json.dumps({"text": "upper"}),
            "message_type": "text",
            "chat_type": "p2p",
            "upper_message_id": "om_upper",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert msg.quoted_message_id == "om_upper"


def test_normalize_event_post_message() -> None:
    post_content: dict[str, Any] = {
        "en": {
            "title": "My Post",
            "content": [[{"tag": "text", "text": "Rich text body"}]],
        }
    }
    event = {
        "message": {
            "chat_id": "oc_1",
            "message_id": "om_post",
            "content": json.dumps(post_content),
            "message_type": "post",
            "chat_type": "p2p",
        },
        "sender": {"sender_id": {"open_id": "ou_1"}},
    }
    msg = normalize_event(event)
    assert "My Post" in msg.text
    assert "Rich text body" in msg.text


def test_feishu_message_dataclass() -> None:
    msg = FeishuMessage(
        chat_id="oc_1",
        message_id="om_1",
        sender_id="ou_1",
        text="hello",
        message_type="text",
        chat_type="p2p",
        image_keys=["img_1"],
    )
    assert msg.reply_to_message_id == ""
    assert msg.quoted_message_id == ""
    assert msg.image_keys == ["img_1"]
