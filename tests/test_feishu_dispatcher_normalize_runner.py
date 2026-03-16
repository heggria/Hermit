# ruff: noqa: F403,F405
from tests.feishu_dispatcher_support import *


def test_normalize_event_extracts_fields() -> None:
    event = _make_event("chat-1", "hello")
    msg = normalize_event(event)

    assert msg.chat_id == "chat-1"
    assert msg.text == "hello"
    assert msg.sender_id == "user-1"
    assert msg.message_type == "text"
    assert msg.chat_type == "p2p"
    assert msg.image_keys == []
    assert msg.reply_to_message_id == ""
    assert msg.quoted_message_id == ""


def test_normalize_event_strips_at_mention_in_group() -> None:
    event = _make_event("chat-g", "@_user_1 how are you", chat_type="group")
    msg = normalize_event(event)

    assert msg.chat_type == "group"
    assert msg.text == "how are you"


def test_normalize_event_handles_plain_text_content() -> None:
    event = {
        "message": {
            "chat_id": "c1",
            "message_id": "m1",
            "content": "plain text",
            "message_type": "text",
        },
        "sender": {"sender_id": {"open_id": "u1"}},
    }
    msg = normalize_event(event)
    assert msg.text == "plain text"


def test_normalize_event_extracts_reply_and_quote_message_ids() -> None:
    event = {
        "message": {
            "chat_id": "c1",
            "message_id": "m1",
            "content": json.dumps({"text": "继续这个"}),
            "message_type": "text",
            "reply_to_message_id": "om_parent",
            "quoted_message_id": "om_quote",
        },
        "sender": {"sender_id": {"open_id": "u1"}},
    }

    msg = normalize_event(event)

    assert msg.reply_to_message_id == "om_parent"
    assert msg.quoted_message_id == "om_quote"


def test_normalize_event_empty_fields() -> None:
    msg = normalize_event({"message": {}, "sender": {}})
    assert msg.chat_id == ""
    assert msg.text == ""
    assert msg.image_keys == []


def test_normalize_event_extracts_image_key() -> None:
    event = {
        "message": {
            "chat_id": "chat-img",
            "message_id": "m-img",
            "content": json.dumps({"image_key": "img_v2_123"}),
            "message_type": "image",
            "chat_type": "p2p",
        },
        "sender": {"sender_id": {"open_id": "u1"}},
    }

    msg = normalize_event(event)

    assert msg.text == ""
    assert msg.message_type == "image"
    assert msg.image_keys == ["img_v2_123"]


def test_normalize_event_collects_nested_image_key_for_post() -> None:
    event = {
        "message": {
            "chat_id": "chat-post",
            "message_id": "m-post",
            "message_type": "post",
            "chat_type": "p2p",
            "content": json.dumps(
                {
                    "zh_cn": {
                        "title": "这是什么",
                        "content": [
                            [
                                {"tag": "at", "user_name": "ZClaw"},
                                {"tag": "text", "text": " 这个是啥"},
                            ],
                            [{"tag": "img", "image_key": "img_nested_1"}],
                        ],
                    }
                }
            ),
        },
        "sender": {"sender_id": {"open_id": "u1"}},
    }

    msg = normalize_event(event)

    assert msg.message_type == "post"
    assert msg.text == "这是什么\n@ZClaw这个是啥"
    assert msg.image_keys == ["img_nested_1"]


def test_runner_creates_session_and_returns_result(tmp_path) -> None:
    runner, _ = _make_runner(tmp_path, answer="reply-1")
    result = runner.handle("chat-x", "hi")

    assert result.text == "reply-1"
    session = runner.session_manager.get_or_create("chat-x")
    assert len(session.messages) == 2


def test_runner_preserves_history_across_messages(tmp_path) -> None:
    runner, client = _make_runner(tmp_path, answer="turn-2")

    runner.handle("chat-y", "first")
    runner.handle("chat-y", "second")

    assert len(client.messages.calls) == 2
    second_call_messages = client.messages.calls[1]["messages"]
    roles = [m["role"] for m in second_call_messages]
    assert roles == ["user"]
    assert "<conversation_projection>" in second_call_messages[0]["content"]
    assert "<context_pack>" in second_call_messages[0]["content"]


def test_runner_isolates_sessions(tmp_path) -> None:
    runner, _ = _make_runner(tmp_path, answer="response")

    runner.handle("chat-a", "msg-a")
    runner.handle("chat-b", "msg-b")

    session_a = runner.session_manager.get_or_create("chat-a")
    session_b = runner.session_manager.get_or_create("chat-b")
    assert session_a.messages != session_b.messages


def test_runner_reset_session(tmp_path) -> None:
    runner, _ = _make_runner(tmp_path, answer="r1")

    runner.handle("s1", "hello")
    session_before = runner.session_manager.get_or_create("s1")
    assert len(session_before.messages) == 2

    runner.reset_session("s1")
    session_after = runner.session_manager.get_or_create("s1")
    assert len(session_after.messages) == 0


def test_runner_close_session(tmp_path) -> None:
    runner, _ = _make_runner(tmp_path, answer="ok")

    runner.handle("s2", "hi")
    runner.close_session("s2")

    fresh = runner.session_manager.get_or_create("s2")
    assert len(fresh.messages) == 0


def test_build_task_topic_card_renders_current_phase_and_recent_milestones() -> None:
    card = build_task_topic_card(
        {
            "status": "running",
            "current_hint": "dev server 已就绪，下一步会继续 smoke test。",
            "current_phase": "ready",
            "current_progress_percent": 100,
            "items": [
                {
                    "kind": "tool.progressed",
                    "text": "Booting dev server",
                    "phase": "starting",
                    "progress_percent": 10,
                },
                {
                    "kind": "task.progress.summarized",
                    "text": "dev server 已就绪，下一步会继续 smoke test。\n服务已经可以访问。",
                    "phase": "ready",
                    "progress_percent": 100,
                },
            ],
        },
        title="Dev Task",
        locale="zh-CN",
    )

    elements = card["body"]["elements"]
    assert elements[0]["content"].startswith("**已就绪 · 100%**")
    assert "下一步会继续 smoke test" in elements[0]["content"]
    assert "服务已经可以访问" in elements[1]["content"]


def test_build_task_topic_card_hides_duplicate_terminal_summary_and_start_item() -> None:
    card = build_task_topic_card(
        {
            "status": "completed",
            "current_hint": "你好！有什么可以帮你的吗？",
            "current_phase": "completed",
            "current_progress_percent": 100,
            "items": [
                {"kind": "task.started", "text": "你好"},
                {
                    "kind": "task.completed",
                    "text": "你好！有什么可以帮你的吗？",
                    "phase": "completed",
                    "progress_percent": 100,
                },
            ],
        },
        title="Greeting",
        locale="zh-CN",
    )

    elements = card["body"]["elements"]
    assert len(elements) == 1
    assert elements[0]["content"].startswith("**已完成 · 100%**")
    assert "有什么可以帮你的吗" in elements[0]["content"]
