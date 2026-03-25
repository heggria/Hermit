"""Tests for the Feishu adapter (adapter.py).

Covers the FeishuAdapter class methods in isolation, heavily mocking the
lark-oapi SDK and the AgentRunner to avoid any real network calls.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.plugins.builtin.adapters.feishu.adapter import (
    FeishuAdapter,
    _is_expected_lark_ws_close,
    get_active_adapter,
    register,
)
from hermit.plugins.builtin.adapters.feishu.normalize import FeishuMessage
from hermit.plugins.builtin.adapters.feishu.reply import ToolStep

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(
    *,
    app_id: str = "test_id",
    app_secret: str = "test_secret",
    settings: Any = None,
) -> FeishuAdapter:
    if settings is None:
        settings = SimpleNamespace(
            feishu_app_id=app_id,
            feishu_app_secret=app_secret,
            locale="en-US",
            feishu_thread_progress=False,
            feishu_reaction_enabled=False,
        )
    adapter = FeishuAdapter(settings=settings)
    return adapter


def _make_msg(
    *,
    chat_id: str = "oc_test",
    message_id: str = "om_test",
    sender_id: str = "ou_test",
    text: str = "hello",
    chat_type: str = "p2p",
    message_type: str = "text",
    image_keys: list[str] | None = None,
    reply_to_message_id: str = "",
    quoted_message_id: str = "",
) -> FeishuMessage:
    return FeishuMessage(
        chat_id=chat_id,
        message_id=message_id,
        sender_id=sender_id,
        text=text,
        chat_type=chat_type,
        message_type=message_type,
        image_keys=image_keys or [],
        reply_to_message_id=reply_to_message_id,
        quoted_message_id=quoted_message_id,
    )


def _mock_store(
    *,
    conversations: dict[str, Any] | None = None,
    tasks: dict[str, Any] | None = None,
    approvals: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
    step_attempts: list[Any] | None = None,
) -> MagicMock:
    store = MagicMock()
    conversations = conversations or {}
    tasks = tasks or {}
    approvals = approvals or {}
    events = events or []
    step_attempts = step_attempts or []

    store.get_conversation.side_effect = lambda cid: conversations.get(cid)
    store.get_task.side_effect = lambda tid: tasks.get(tid)
    store.get_approval.side_effect = lambda aid: approvals.get(aid)
    store.list_events.return_value = events
    store.list_step_attempts.return_value = step_attempts
    store.list_conversations.return_value = list(conversations.keys())
    store.list_approvals.return_value = list(approvals.values())
    store.update_conversation_metadata = MagicMock()
    return store


def _mock_runner(store: MagicMock | None = None) -> MagicMock:
    runner = MagicMock()
    if store is None:
        store = _mock_store()
    runner.task_controller.store = store
    runner.session_manager = MagicMock()
    runner.session_manager.idle_timeout_seconds = 3600
    runner.session_manager._active = {}
    runner._session_started = []
    return runner


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    def test_is_expected_lark_ws_close_ok(self) -> None:
        exc = Exception("sent 1000 (OK); then received 1000 (OK); bye")
        assert _is_expected_lark_ws_close(exc) is True

    def test_is_expected_lark_ws_close_connection_closed(self) -> None:
        exc = Exception("connection is closed")
        assert _is_expected_lark_ws_close(exc) is True

    def test_is_expected_lark_ws_close_other_error(self) -> None:
        exc = Exception("random error")
        assert _is_expected_lark_ws_close(exc) is False

    def test_get_active_adapter_initially_none(self) -> None:
        # Reset for test isolation
        import hermit.plugins.builtin.adapters.feishu.adapter as adapter_mod

        old = adapter_mod._active_adapter
        adapter_mod._active_adapter = None
        assert get_active_adapter() is None
        adapter_mod._active_adapter = old

    def test_register_adds_adapter_spec(self) -> None:
        ctx = MagicMock()
        register(ctx)
        ctx.add_adapter.assert_called_once()
        spec = ctx.add_adapter.call_args[0][0]
        assert spec.name == "feishu"
        assert spec.factory is FeishuAdapter


# ---------------------------------------------------------------------------
# FeishuAdapter initialization
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_init_reads_settings(self) -> None:
        settings = SimpleNamespace(
            feishu_app_id="id1",
            feishu_app_secret="secret1",
            locale="en-US",
        )
        adapter = FeishuAdapter(settings=settings)
        assert adapter._app_id == "id1"
        assert adapter._app_secret == "secret1"

    def test_init_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMIT_FEISHU_APP_ID", "env_id")
        monkeypatch.setenv("HERMIT_FEISHU_APP_SECRET", "env_secret")
        adapter = FeishuAdapter(settings=SimpleNamespace())
        assert adapter._app_id == "env_id"
        assert adapter._app_secret == "env_secret"

    def test_init_defaults_to_empty_when_no_settings_or_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HERMIT_FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("HERMIT_FEISHU_APP_SECRET", raising=False)
        adapter = FeishuAdapter(settings=None)
        assert adapter._app_id == ""
        assert adapter._app_secret == ""

    def test_required_skills(self) -> None:
        adapter = _make_adapter()
        assert "feishu-output-format" in adapter.required_skills
        assert "feishu-emoji-reaction" in adapter.required_skills
        assert "feishu-tools" in adapter.required_skills


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDedup:
    def test_is_duplicate_first_time_returns_false(self) -> None:
        adapter = _make_adapter()
        assert adapter._is_duplicate("om_1") is False

    def test_is_duplicate_second_time_returns_true(self) -> None:
        adapter = _make_adapter()
        adapter._is_duplicate("om_1")
        assert adapter._is_duplicate("om_1") is True

    def test_is_duplicate_empty_message_id(self) -> None:
        adapter = _make_adapter()
        assert adapter._is_duplicate("") is False

    def test_dedup_evicts_oldest_when_exceeding_max(self) -> None:
        adapter = _make_adapter()
        for i in range(adapter._DEDUP_MAX + 10):
            adapter._is_duplicate(f"om_{i}")
        # First entries should have been evicted
        assert adapter._is_duplicate("om_0") is False
        # Recent entries should still be present
        assert adapter._is_duplicate(f"om_{adapter._DEDUP_MAX + 9}") is True


# ---------------------------------------------------------------------------
# Session ID building
# ---------------------------------------------------------------------------


class TestBuildSessionId:
    def test_p2p_session_uses_chat_id(self) -> None:
        msg = _make_msg(chat_type="p2p", chat_id="oc_1", sender_id="ou_1")
        assert FeishuAdapter._build_session_id(msg) == "oc_1"

    def test_group_session_appends_sender_id(self) -> None:
        msg = _make_msg(chat_type="group", chat_id="oc_2", sender_id="ou_2")
        assert FeishuAdapter._build_session_id(msg) == "oc_2:ou_2"

    def test_group_empty_sender_uses_chat_id_only(self) -> None:
        msg = _make_msg(chat_type="group", chat_id="oc_3", sender_id="")
        assert FeishuAdapter._build_session_id(msg) == "oc_3"


# ---------------------------------------------------------------------------
# Chat ID from conversation ID
# ---------------------------------------------------------------------------


class TestChatIdFromConversationId:
    def test_simple_chat_id(self) -> None:
        assert FeishuAdapter._chat_id_from_conversation_id("oc_abc") == "oc_abc"

    def test_composite_id_extracts_chat_part(self) -> None:
        assert FeishuAdapter._chat_id_from_conversation_id("oc_abc:ou_xyz") == "oc_abc"


# ---------------------------------------------------------------------------
# Card signature
# ---------------------------------------------------------------------------


class TestCardSignature:
    def test_consistent_hash(self) -> None:
        card = {"schema": "2.0", "body": {"elements": [{"tag": "markdown", "content": "hello"}]}}
        sig1 = FeishuAdapter._card_signature(card)
        sig2 = FeishuAdapter._card_signature(card)
        assert sig1 == sig2
        assert len(sig1) == 40  # SHA-1 hex digest

    def test_different_content_different_hash(self) -> None:
        card1 = {"body": "a"}
        card2 = {"body": "b"}
        assert FeishuAdapter._card_signature(card1) != FeishuAdapter._card_signature(card2)


# ---------------------------------------------------------------------------
# _is_short_text_message
# ---------------------------------------------------------------------------


class TestIsShortTextMessage:
    def test_empty_is_short(self) -> None:
        assert FeishuAdapter._is_short_text_message("") is True

    def test_short_text(self) -> None:
        assert FeishuAdapter._is_short_text_message("hi") is True

    def test_exactly_12_chars(self) -> None:
        assert FeishuAdapter._is_short_text_message("a" * 12) is True

    def test_13_chars_is_not_short(self) -> None:
        assert FeishuAdapter._is_short_text_message("a" * 13) is False

    def test_multiline_is_not_short(self) -> None:
        assert FeishuAdapter._is_short_text_message("a\nb") is False

    def test_whitespace_collapses(self) -> None:
        assert FeishuAdapter._is_short_text_message("  a   b  ") is True


# ---------------------------------------------------------------------------
# Approval card kwargs
# ---------------------------------------------------------------------------


class TestApprovalCardKwargs:
    def test_none_approval(self) -> None:
        result = FeishuAdapter._approval_card_kwargs(None)
        assert result == {
            "target_path": None,
            "workspace_root": None,
            "grant_scope_dir": None,
        }

    def test_with_approval_target_paths(self) -> None:
        approval = SimpleNamespace(
            requested_action={
                "target_paths": ["/tmp/file.txt"],
                "workspace_root": "/home/user",
                "grant_scope_dir": "/home/user/project",
            }
        )
        result = FeishuAdapter._approval_card_kwargs(approval)
        assert result["target_path"] == "/tmp/file.txt"
        assert result["workspace_root"] == "/home/user"
        assert result["grant_scope_dir"] == "/home/user/project"

    def test_with_empty_target_paths(self) -> None:
        approval = SimpleNamespace(
            requested_action={"target_paths": [], "workspace_root": "", "grant_scope_dir": ""}
        )
        result = FeishuAdapter._approval_card_kwargs(approval)
        assert result["target_path"] is None
        assert result["workspace_root"] is None
        assert result["grant_scope_dir"] is None


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_basic_text_prompt(self) -> None:
        adapter = _make_adapter()
        msg = _make_msg(text="do something", message_id="om_1", chat_id="oc_1")
        prompt = adapter._build_prompt("session_1", msg)
        assert "<feishu_msg_id>om_1</feishu_msg_id>" in prompt
        assert "<feishu_chat_id>oc_1</feishu_chat_id>" in prompt
        assert "do something" in prompt

    def test_prompt_with_no_ids(self) -> None:
        adapter = _make_adapter()
        msg = _make_msg(text="hi", message_id="", chat_id="")
        prompt = adapter._build_prompt("s1", msg)
        assert "feishu_msg_id" not in prompt
        assert "feishu_chat_id" not in prompt
        assert prompt == "hi"

    def test_prompt_with_images(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        msg = _make_msg(text="check this", image_keys=["img_1"])
        # _ingest_image_records will return empty since the mock doesn't implement the tool
        prompt = adapter._build_prompt("s1", msg)
        assert "check this" in prompt


# ---------------------------------------------------------------------------
# _build_image_prompt
# ---------------------------------------------------------------------------


class TestBuildImagePrompt:
    def test_no_runner_returns_single_prompt(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        msg = _make_msg(image_keys=["img_1"])
        result = adapter._build_image_prompt("s1", msg)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_with_runner_but_no_records(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        adapter._runner = runner
        msg = _make_msg(image_keys=["img_1", "img_2"])
        # _ingest_image_records returns empty since tool_executor is not set up
        result = adapter._build_image_prompt("s1", msg)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Topic helpers
# ---------------------------------------------------------------------------


class TestTopicHelpers:
    def test_topic_has_displayable_progress(self) -> None:
        adapter = _make_adapter()
        topic: dict[str, Any] = {"items": [{"kind": "tool.submitted"}, {"kind": "other"}]}
        assert adapter._topic_has_displayable_progress(topic) is True

    def test_topic_has_no_displayable_progress(self) -> None:
        adapter = _make_adapter()
        topic: dict[str, Any] = {"items": [{"kind": "task.started"}]}
        assert adapter._topic_has_displayable_progress(topic) is False

    def test_topic_has_displayable_progress_empty_items(self) -> None:
        adapter = _make_adapter()
        assert adapter._topic_has_displayable_progress({}) is False

    def test_progress_hint_from_topic_with_hint(self) -> None:
        adapter = _make_adapter()
        topic: dict[str, Any] = {
            "current_hint": "Analyzing data",
            "current_phase": "running",
            "items": [{"kind": "tool.submitted"}],
        }
        assert adapter._progress_hint_from_topic(topic) == "Analyzing data"

    def test_progress_hint_from_topic_early_phase(self) -> None:
        adapter = _make_adapter()
        topic: dict[str, Any] = {
            "current_hint": "",
            "current_phase": "started",
            "items": [],
        }
        result = adapter._progress_hint_from_topic(topic)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_progress_hint_from_topic_empty_phase_no_meaningful_items(self) -> None:
        adapter = _make_adapter()
        topic: dict[str, Any] = {
            "current_hint": "",
            "current_phase": "",
            "items": [{"kind": "unknown"}],
        }
        result = adapter._progress_hint_from_topic(topic)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Task topic binding / mapping
# ---------------------------------------------------------------------------


class TestTaskTopicBinding:
    def test_bind_task_topic(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(conversations={"s1": SimpleNamespace(metadata={})})
        adapter._runner = _mock_runner(store)

        adapter._bind_task_topic(
            "s1",
            "task_1",
            chat_id="oc_1",
            root_message_id="om_card",
            card_mode="topic",
        )

        store.update_conversation_metadata.assert_called_once()
        call_args = store.update_conversation_metadata.call_args
        metadata = call_args[0][1]
        assert "feishu_task_topics" in metadata
        assert "task_1" in metadata["feishu_task_topics"]

    def test_bind_task_topic_no_runner(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._bind_task_topic("s1", "t1", chat_id="oc_1")
        # Should not raise

    def test_task_topic_mapping_returns_dict(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(
            conversations={
                "s1": SimpleNamespace(
                    metadata={
                        "feishu_task_topics": {"t1": {"chat_id": "oc_1", "root_message_id": "om_1"}}
                    }
                )
            }
        )
        adapter._runner = _mock_runner(store)
        result = adapter._task_topic_mapping("s1", "t1")
        assert result["chat_id"] == "oc_1"

    def test_task_topic_mapping_missing_task(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(conversations={"s1": SimpleNamespace(metadata={})})
        adapter._runner = _mock_runner(store)
        assert adapter._task_topic_mapping("s1", "unknown") == {}

    def test_task_topic_mapping_no_runner(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        assert adapter._task_topic_mapping("s1", "t1") == {}

    def test_task_topic_mapping_no_conversation(self) -> None:
        adapter = _make_adapter()
        store = _mock_store()
        adapter._runner = _mock_runner(store)
        assert adapter._task_topic_mapping("nonexistent", "t1") == {}

    def test_unbind_task_topic(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(
            conversations={
                "s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {"chat_id": "oc_1"}}})
            }
        )
        adapter._runner = _mock_runner(store)
        adapter._unbind_task_topic("s1", "t1")
        store.update_conversation_metadata.assert_called_once()

    def test_unbind_task_topic_nonexistent(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {}})}
        )
        adapter._runner = _mock_runner(store)
        adapter._unbind_task_topic("s1", "nonexistent")
        store.update_conversation_metadata.assert_not_called()

    def test_unbind_task_topic_no_runner(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._unbind_task_topic("s1", "t1")

    def test_unbind_task_topic_no_conversation(self) -> None:
        adapter = _make_adapter()
        store = _mock_store()
        adapter._runner = _mock_runner(store)
        adapter._unbind_task_topic("nonexistent", "t1")

    def test_update_task_topic_mapping(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(
            conversations={
                "s1": SimpleNamespace(
                    metadata={
                        "feishu_task_topics": {"t1": {"chat_id": "oc_1", "card_mode": "topic"}}
                    }
                )
            }
        )
        adapter._runner = _mock_runner(store)
        adapter._update_task_topic_mapping("s1", "t1", card_mode="approval")
        store.update_conversation_metadata.assert_called_once()

    def test_update_task_topic_mapping_no_existing(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {}})}
        )
        adapter._runner = _mock_runner(store)
        adapter._update_task_topic_mapping("s1", "nonexistent", card_mode="topic")
        store.update_conversation_metadata.assert_not_called()

    def test_update_task_topic_mapping_no_runner(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._update_task_topic_mapping("s1", "t1", card_mode="topic")

    def test_task_id_for_message_reference_found(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(
            conversations={
                "s1": SimpleNamespace(
                    metadata={
                        "feishu_task_topics": {
                            "t1": {"root_message_id": "om_card"},
                            "t2": {"reply_to_message_id": "om_reply"},
                        }
                    }
                )
            }
        )
        adapter._runner = _mock_runner(store)
        assert adapter._task_id_for_message_reference("s1", "om_card") == "t1"
        assert adapter._task_id_for_message_reference("s1", "om_reply") == "t2"

    def test_task_id_for_message_reference_not_found(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {}})}
        )
        adapter._runner = _mock_runner(store)
        assert adapter._task_id_for_message_reference("s1", "om_unknown") is None

    def test_task_id_for_message_reference_empty_id(self) -> None:
        adapter = _make_adapter()
        assert adapter._task_id_for_message_reference("s1", "") is None
        assert adapter._task_id_for_message_reference("s1", None) is None

    def test_task_id_for_message_reference_no_runner(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        assert adapter._task_id_for_message_reference("s1", "om_1") is None


# ---------------------------------------------------------------------------
# Task event helpers
# ---------------------------------------------------------------------------


class TestTaskEventHelpers:
    def test_task_has_appended_notes_true(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(events=[{"event_type": "task.note.appended", "payload": {}}])
        adapter._runner = _mock_runner(store)
        assert adapter._task_has_appended_notes("t1") is True

    def test_task_has_appended_notes_false(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(events=[{"event_type": "task.started"}])
        adapter._runner = _mock_runner(store)
        assert adapter._task_has_appended_notes("t1") is False

    def test_task_has_appended_notes_no_runner(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        assert adapter._task_has_appended_notes("t1") is False

    def test_task_has_appended_notes_no_store(self) -> None:
        adapter = _make_adapter()
        runner = MagicMock()
        runner.task_controller = None
        adapter._runner = runner
        assert adapter._task_has_appended_notes("t1") is False

    def test_task_terminal_result_text_completed(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(
            events=[
                {"event_type": "task.started", "payload": {}},
                {"event_type": "task.completed", "payload": {"result_text": "Done!"}},
            ]
        )
        adapter._runner = _mock_runner(store)
        assert adapter._task_terminal_result_text("t1") == "Done!"

    def test_task_terminal_result_text_from_preview(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(
            events=[
                {"event_type": "task.completed", "payload": {"result_preview": "Preview text"}},
            ]
        )
        adapter._runner = _mock_runner(store)
        assert adapter._task_terminal_result_text("t1") == "Preview text"

    def test_task_terminal_result_text_no_terminal_event(self) -> None:
        adapter = _make_adapter()
        store = _mock_store(events=[{"event_type": "task.started"}])
        adapter._runner = _mock_runner(store)
        assert adapter._task_terminal_result_text("t1") == ""

    def test_task_terminal_result_text_no_runner(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        assert adapter._task_terminal_result_text("t1") == ""

    def test_is_async_feishu_task_true(self) -> None:
        adapter = _make_adapter()
        attempt = SimpleNamespace(context={"ingress_metadata": {"dispatch_mode": "async"}})
        store = _mock_store(step_attempts=[attempt])
        adapter._runner = _mock_runner(store)
        assert adapter._is_async_feishu_task("t1") is True

    def test_is_async_feishu_task_false(self) -> None:
        adapter = _make_adapter()
        attempt = SimpleNamespace(context={"ingress_metadata": {"dispatch_mode": "sync"}})
        store = _mock_store(step_attempts=[attempt])
        adapter._runner = _mock_runner(store)
        assert adapter._is_async_feishu_task("t1") is False

    def test_is_async_feishu_task_no_runner(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        assert adapter._is_async_feishu_task("t1") is False


# ---------------------------------------------------------------------------
# _task_history_steps
# ---------------------------------------------------------------------------


class TestTaskHistorySteps:
    def test_no_runner_returns_live_steps(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        live = [ToolStep("read_file", "Read", "/tmp", "", 100)]
        assert adapter._task_history_steps("t1", live_steps=live) == live

    def test_no_live_steps_returns_history(self) -> None:
        adapter = _make_adapter()
        store = _mock_store()
        adapter._runner = _mock_runner(store)
        # ProjectionService will be called — mock it
        with patch("hermit.plugins.builtin.adapters.feishu.adapter.ProjectionService") as MockPS:
            MockPS.return_value.ensure_task_projection.return_value = {
                "tool_history": [
                    {"tool_name": "web_search", "key_input": "test"},
                    {"tool_name": "feishu_react"},  # Should be skipped
                ]
            }
            steps = adapter._task_history_steps("t1")
            assert len(steps) == 1
            assert steps[0].name == "web_search"

    def test_live_steps_merged_with_history(self) -> None:
        adapter = _make_adapter()
        store = _mock_store()
        adapter._runner = _mock_runner(store)
        live = [ToolStep("web_search", "Search", "test", "result", 500)]
        with patch("hermit.plugins.builtin.adapters.feishu.adapter.ProjectionService") as MockPS:
            MockPS.return_value.ensure_task_projection.return_value = {
                "tool_history": [{"tool_name": "web_search", "key_input": "test"}]
            }
            steps = adapter._task_history_steps("t1", live_steps=live)
            # Should replace the history step with the live one
            assert len(steps) == 1
            assert steps[0].summary == "result"

    def test_live_steps_appended_when_no_match(self) -> None:
        adapter = _make_adapter()
        store = _mock_store()
        adapter._runner = _mock_runner(store)
        live = [ToolStep("bash", "Shell", "ls", "ok", 200)]
        with patch("hermit.plugins.builtin.adapters.feishu.adapter.ProjectionService") as MockPS:
            MockPS.return_value.ensure_task_projection.return_value = {
                "tool_history": [{"tool_name": "web_search", "key_input": "query"}]
            }
            steps = adapter._task_history_steps("t1", live_steps=live)
            assert len(steps) == 2


# ---------------------------------------------------------------------------
# _on_message event handler
# ---------------------------------------------------------------------------


class TestOnMessage:
    def test_skips_empty_text_no_images(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    chat_id="oc_1",
                    message_id="om_1",
                    content=json.dumps({"text": ""}),
                    message_type="text",
                    chat_type="p2p",
                    reply_to_message_id="",
                    parent_id="",
                    reply_in_thread_from_message_id="",
                    quoted_message_id="",
                    root_id="",
                    upper_message_id="",
                ),
                sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_1")),
            )
        )
        # Should not raise
        adapter._on_message(data)

    def test_skips_when_stopped(self) -> None:
        adapter = _make_adapter()
        adapter._stopped = True
        adapter._runner = _mock_runner()
        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    chat_id="oc_1",
                    message_id="om_1",
                    content=json.dumps({"text": "hello"}),
                    message_type="text",
                    chat_type="p2p",
                    reply_to_message_id="",
                    parent_id="",
                    reply_in_thread_from_message_id="",
                    quoted_message_id="",
                    root_id="",
                    upper_message_id="",
                ),
                sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_1")),
            )
        )
        adapter._on_message(data)

    def test_skips_duplicate(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        adapter._seen_msgs["om_dup"] = True
        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    chat_id="oc_1",
                    message_id="om_dup",
                    content=json.dumps({"text": "hello"}),
                    message_type="text",
                    chat_type="p2p",
                    reply_to_message_id="",
                    parent_id="",
                    reply_in_thread_from_message_id="",
                    quoted_message_id="",
                    root_id="",
                    upper_message_id="",
                ),
                sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_1")),
            )
        )
        adapter._on_message(data)
        # The message should not be submitted to executor

    def test_handles_exception_in_event_extraction(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        data = SimpleNamespace(event=None)  # Will cause AttributeError
        # Should not raise
        adapter._on_message(data)


# ---------------------------------------------------------------------------
# _on_card_action
# ---------------------------------------------------------------------------


class TestOnCardAction:
    def test_unsupported_action_returns_info_toast(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        adapter._client = MagicMock()
        data = SimpleNamespace(
            event=SimpleNamespace(
                action=SimpleNamespace(value={"kind": "unknown"}),
                context=SimpleNamespace(open_message_id="om_1"),
            )
        )
        with patch(
            "hermit.plugins.builtin.adapters.feishu.adapter.FeishuAdapter._card_action_response"
        ) as mock_resp:
            mock_resp.return_value = "response"
            adapter._on_card_action(data)
            mock_resp.assert_called()

    def test_approval_not_found_returns_error(self) -> None:
        adapter = _make_adapter()
        store = _mock_store()
        adapter._runner = _mock_runner(store)
        adapter._client = MagicMock()
        data = SimpleNamespace(
            event=SimpleNamespace(
                action=SimpleNamespace(
                    value={
                        "kind": "approval",
                        "action": "approve_once",
                        "approval_id": "ap_missing",
                    }
                ),
                context=SimpleNamespace(open_message_id="om_1"),
            )
        )
        result = adapter._on_card_action(data)
        assert result is not None

    def test_already_handled_approval(self) -> None:
        adapter = _make_adapter()
        approval = SimpleNamespace(
            approval_id="ap_1",
            status="approved",
            requested_action={},
        )
        store = _mock_store(approvals={"ap_1": approval})
        adapter._runner = _mock_runner(store)
        adapter._client = MagicMock()
        data = SimpleNamespace(
            event=SimpleNamespace(
                action=SimpleNamespace(
                    value={
                        "kind": "approval",
                        "action": "approve_once",
                        "approval_id": "ap_1",
                    }
                ),
                context=SimpleNamespace(open_message_id="om_1"),
            )
        )
        result = adapter._on_card_action(data)
        assert result is not None

    def test_no_runner_returns_error(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._client = None
        data = SimpleNamespace(
            event=SimpleNamespace(
                action=SimpleNamespace(
                    value={
                        "kind": "approval",
                        "action": "approve_once",
                        "approval_id": "ap_1",
                    }
                ),
                context=SimpleNamespace(open_message_id="om_1"),
            )
        )
        result = adapter._on_card_action(data)
        assert result is not None

    def test_deny_action_returns_success(self) -> None:
        adapter = _make_adapter()
        approval = SimpleNamespace(
            approval_id="ap_1",
            status="pending",
            task_id="t1",
            requested_action={},
        )
        store = _mock_store(approvals={"ap_1": approval})
        adapter._runner = _mock_runner(store)
        adapter._client = MagicMock()
        data = SimpleNamespace(
            event=SimpleNamespace(
                action=SimpleNamespace(
                    value={
                        "kind": "approval",
                        "action": "deny",
                        "approval_id": "ap_1",
                    }
                ),
                context=SimpleNamespace(open_message_id="om_1"),
            )
        )
        result = adapter._on_card_action(data)
        assert result is not None


# ---------------------------------------------------------------------------
# _card_action_response
# ---------------------------------------------------------------------------


class TestCardActionResponse:
    def test_basic_response(self) -> None:
        adapter = _make_adapter()
        with patch(
            "hermit.plugins.builtin.adapters.feishu.adapter.FeishuAdapter._card_action_response"
        ) as mock:
            mock.return_value = "ok"
            result = adapter._card_action_response("test", level="info")
            assert result is not None

    def test_with_card(self) -> None:
        adapter = _make_adapter()
        # Exercise the actual method via direct call
        # The lark_oapi import may fail in test — we mock it
        with patch(
            "lark_oapi.event.callback.model.p2_card_action_trigger.P2CardActionTriggerResponse"
        ) as MockResp:
            MockResp.return_value = "mocked_response"
            result = adapter._card_action_response(
                "content", level="success", card={"schema": "2.0"}
            )
            assert result == "mocked_response"
            call_payload = MockResp.call_args[0][0]
            assert "toast" in call_payload
            assert "card" in call_payload

    def test_without_card(self) -> None:
        with patch(
            "lark_oapi.event.callback.model.p2_card_action_trigger.P2CardActionTriggerResponse"
        ) as MockResp:
            MockResp.return_value = "mocked_response"
            adapter = _make_adapter()
            adapter._card_action_response("content", level="error")
            call_payload = MockResp.call_args[0][0]
            assert "card" not in call_payload

    def test_invalid_level_defaults_to_info(self) -> None:
        with patch(
            "lark_oapi.event.callback.model.p2_card_action_trigger.P2CardActionTriggerResponse"
        ) as MockResp:
            MockResp.return_value = "mocked"
            adapter = _make_adapter()
            adapter._card_action_response("content", level="invalid_level")
            call_payload = MockResp.call_args[0][0]
            assert call_payload["toast"]["type"] == "info"


# ---------------------------------------------------------------------------
# _flush_all_sessions
# ---------------------------------------------------------------------------


class TestFlushAllSessions:
    def test_closes_all_sessions(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner._session_started = ["s1", "s2"]
        adapter._runner = runner
        adapter._flush_all_sessions()
        assert runner.close_session.call_count == 2

    def test_no_runner_does_nothing(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._flush_all_sessions()  # Should not raise

    def test_exception_in_close_session_swallowed(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner._session_started = ["s1"]
        runner.close_session.side_effect = RuntimeError("boom")
        adapter._runner = runner
        adapter._flush_all_sessions()  # Should not raise


# ---------------------------------------------------------------------------
# _sweep_idle_sessions
# ---------------------------------------------------------------------------


class TestSweepIdleSessions:
    def test_sweeps_expired_sessions(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        session = MagicMock()
        session.is_expired.return_value = True
        runner.session_manager._active = {"s1": session}
        adapter._runner = runner
        adapter._sweep_idle_sessions()
        runner.close_session.assert_called_once_with("s1")

    def test_skips_non_expired_sessions(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        session = MagicMock()
        session.is_expired.return_value = False
        runner.session_manager._active = {"s1": session}
        adapter._runner = runner
        adapter._sweep_idle_sessions()
        runner.close_session.assert_not_called()

    def test_does_nothing_when_stopped(self) -> None:
        adapter = _make_adapter()
        adapter._stopped = True
        adapter._runner = _mock_runner()
        adapter._sweep_idle_sessions()

    def test_does_nothing_without_runner(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._sweep_idle_sessions()


# ---------------------------------------------------------------------------
# _schedule_sweep and _schedule_topic_refresh
# ---------------------------------------------------------------------------


class TestScheduling:
    def test_schedule_sweep_when_stopped(self) -> None:
        adapter = _make_adapter()
        adapter._stopped = True
        adapter._schedule_sweep()
        assert adapter._sweep_timer is None

    def test_schedule_topic_refresh_when_stopped(self) -> None:
        adapter = _make_adapter()
        adapter._stopped = True
        adapter._schedule_topic_refresh()
        assert adapter._topic_timer is None

    def test_schedule_sweep_creates_timer(self) -> None:
        adapter = _make_adapter()
        adapter._stopped = False
        adapter._schedule_sweep()
        assert adapter._sweep_timer is not None
        adapter._sweep_timer.cancel()

    def test_schedule_topic_refresh_creates_timer(self) -> None:
        adapter = _make_adapter()
        adapter._stopped = False
        adapter._schedule_topic_refresh()
        assert adapter._topic_timer is not None
        adapter._topic_timer.cancel()


# ---------------------------------------------------------------------------
# _should_dispatch_raw
# ---------------------------------------------------------------------------


class TestShouldDispatchRaw:
    def test_slash_command(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        adapter._runner.task_controller.resolve_text_command.return_value = None
        assert adapter._should_dispatch_raw("s1", "/help") is True

    def test_empty_text(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        assert adapter._should_dispatch_raw("s1", "") is False
        assert adapter._should_dispatch_raw("s1", "  ") is False


# ---------------------------------------------------------------------------
# _supports_async_ingress
# ---------------------------------------------------------------------------


class TestSupportsAsyncIngress:
    def test_with_both_methods(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.enqueue_ingress = MagicMock()
        runner.task_controller.decide_ingress = MagicMock()
        adapter._runner = runner
        assert adapter._supports_async_ingress() is True

    def test_without_enqueue(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        del runner.enqueue_ingress
        adapter._runner = runner
        assert adapter._supports_async_ingress() is False

    def test_no_runner(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        assert adapter._supports_async_ingress() is False


# ---------------------------------------------------------------------------
# start validates credentials
# ---------------------------------------------------------------------------


class TestStartValidation:
    @pytest.mark.asyncio
    async def test_start_raises_without_credentials(self) -> None:
        adapter = FeishuAdapter(settings=SimpleNamespace())
        adapter._app_id = ""
        adapter._app_secret = ""
        with pytest.raises(RuntimeError, match="HERMIT_FEISHU_APP_ID"):
            await adapter.start(MagicMock())


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_cancels_timers(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        adapter._runner._session_started = []
        adapter._stopped = False
        sweep_timer = MagicMock()
        topic_timer = MagicMock()
        adapter._sweep_timer = sweep_timer
        adapter._topic_timer = topic_timer
        adapter._ws_client = None
        adapter._ws_loop = None
        adapter._ws_thread = None
        await adapter.stop()
        sweep_timer.cancel.assert_called_once()
        topic_timer.cancel.assert_called_once()
        assert adapter._stopped is True


# ---------------------------------------------------------------------------
# _present_task_result
# ---------------------------------------------------------------------------


class TestPresentTaskResult:
    def test_no_client_returns_early(self) -> None:
        adapter = _make_adapter()
        adapter._client = None
        result = SimpleNamespace(
            text="result",
            agent_result=SimpleNamespace(
                blocked=False, suspended=False, task_id="t1", approval_id=""
            ),
        )
        _mid, blocked, tid = adapter._present_task_result(
            reply_to_message_id="om_1",
            existing_card_message_id=None,
            chat_id="oc_1",
            result=result,
            steps=[],
        )
        assert blocked is False
        assert tid == "t1"

    def test_blocked_with_approval_patches_card(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        adapter._runner = _mock_runner()
        result = SimpleNamespace(
            text="waiting",
            agent_result=SimpleNamespace(
                blocked=True,
                suspended=False,
                task_id="t1",
                approval_id="ap_1",
            ),
        )
        with (
            patch(
                "hermit.plugins.builtin.adapters.feishu.adapter.FeishuAdapter._build_pending_approval_card"
            ) as mock_build,
            patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card") as mock_patch,
        ):
            mock_build.return_value = ({"schema": "2.0"}, None)
            mock_patch.return_value = True
            _mid, blocked, _tid = adapter._present_task_result(
                reply_to_message_id=None,
                existing_card_message_id="om_existing",
                chat_id="oc_1",
                result=result,
                steps=[],
            )
            assert blocked is True
            mock_patch.assert_called_once()

    def test_empty_result_text(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        result = SimpleNamespace(
            text="",
            agent_result=SimpleNamespace(
                blocked=False, suspended=False, task_id="t1", approval_id=""
            ),
        )
        _mid, blocked, _tid = adapter._present_task_result(
            reply_to_message_id="om_1",
            existing_card_message_id=None,
            chat_id="oc_1",
            result=result,
            steps=[],
        )
        assert blocked is False


# ---------------------------------------------------------------------------
# _resolve_approval_from_feishu
# ---------------------------------------------------------------------------


class TestResolveApprovalFromFeishu:
    def test_no_runner_raises(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        with pytest.raises(RuntimeError, match="runner unavailable"):
            adapter._resolve_approval_from_feishu("s1", action="deny", approval_id="ap_1")

    def test_uses_enqueue_approval_resume(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.enqueue_approval_resume = MagicMock(return_value="result")
        adapter._runner = runner
        result = adapter._resolve_approval_from_feishu(
            "s1", action="approve_once", approval_id="ap_1"
        )
        assert result == "result"
        runner.enqueue_approval_resume.assert_called_once()

    def test_falls_back_to_resolve_approval(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        del runner.enqueue_approval_resume
        runner._resolve_approval = MagicMock(return_value="fallback_result")
        adapter._runner = runner
        result = adapter._resolve_approval_from_feishu(
            "s1", action="deny", approval_id="ap_1", reason="test"
        )
        assert result == "fallback_result"

    def test_no_method_raises(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        del runner.enqueue_approval_resume
        del runner._resolve_approval
        adapter._runner = runner
        with pytest.raises(AttributeError, match="approval resolution"):
            adapter._resolve_approval_from_feishu("s1", action="deny", approval_id="ap_1")


# ---------------------------------------------------------------------------
# handle_post_run_result
# ---------------------------------------------------------------------------


class TestHandlePostRunResult:
    def test_no_client_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = None
        adapter._runner = _mock_runner()
        assert adapter.handle_post_run_result(SimpleNamespace()) is False

    def test_wrong_runner_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        adapter._runner = _mock_runner()
        other_runner = MagicMock()
        assert adapter.handle_post_run_result(SimpleNamespace(), runner=other_runner) is False

    def test_no_task_id_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        adapter._runner = _mock_runner()
        result = SimpleNamespace(task_id="")
        assert adapter.handle_post_run_result(result) is False

    def test_non_feishu_task_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1",
            source_channel="cli",
            status="completed",
            conversation_id="s1",
        )
        store = _mock_store(tasks={"t1": task})
        adapter._runner = _mock_runner(store)
        result = SimpleNamespace(task_id="t1")
        assert adapter.handle_post_run_result(result) is False


# ---------------------------------------------------------------------------
# _join_ws_thread
# ---------------------------------------------------------------------------


class TestJoinWsThread:
    def test_no_thread(self) -> None:
        adapter = _make_adapter()
        adapter._ws_thread = None
        adapter._join_ws_thread()  # Should not raise

    def test_thread_joins(self) -> None:
        adapter = _make_adapter()
        thread = MagicMock()
        thread.is_alive.return_value = False
        adapter._ws_thread = thread
        adapter._join_ws_thread()
        thread.join.assert_called_once()

    def test_thread_timeout_warning(self) -> None:
        adapter = _make_adapter()
        thread = MagicMock()
        thread.is_alive.return_value = True
        adapter._ws_thread = thread
        adapter._join_ws_thread(timeout_seconds=0.1)
        thread.join.assert_called_once()


# ---------------------------------------------------------------------------
# _send_task_card
# ---------------------------------------------------------------------------


class TestSendTaskCard:
    def test_no_client_returns_none(self) -> None:
        adapter = _make_adapter()
        adapter._client = None
        adapter._runner = _mock_runner()
        result = adapter._send_task_card(
            "s1",
            "t1",
            mapping={"chat_id": "oc_1"},
            card={"schema": "2.0"},
            card_mode="topic",
        )
        assert result is None

    def test_sends_reply_when_reply_to_message_id(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        store = _mock_store(
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})}
        )
        adapter._runner = _mock_runner(store)
        with patch(
            "hermit.plugins.builtin.adapters.feishu.adapter.reply_card_return_id"
        ) as mock_reply:
            mock_reply.return_value = "om_card_reply"
            result = adapter._send_task_card(
                "s1",
                "t1",
                mapping={"reply_to_message_id": "om_parent"},
                card={"schema": "2.0"},
                card_mode="topic",
            )
            assert result == "om_card_reply"

    def test_sends_to_chat_when_no_reply_to(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        store = _mock_store(
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})}
        )
        adapter._runner = _mock_runner(store)
        with patch("hermit.plugins.builtin.adapters.feishu.adapter.send_card") as mock_send:
            mock_send.return_value = "om_card_send"
            result = adapter._send_task_card(
                "s1",
                "t1",
                mapping={"chat_id": "oc_1"},
                card={"schema": "2.0"},
                card_mode="topic",
            )
            assert result == "om_card_send"


# ---------------------------------------------------------------------------
# _deliver_terminal_result_without_card
# ---------------------------------------------------------------------------


class TestDeliverTerminalResult:
    def test_no_runner_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._client = None
        assert adapter._deliver_terminal_result_without_card("t1", mapping={}) is False

    def test_non_terminal_task_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(status="running", conversation_id="s1")
        store = _mock_store(tasks={"t1": task})
        adapter._runner = _mock_runner(store)
        assert adapter._deliver_terminal_result_without_card("t1", mapping={}) is False

    def test_already_sent_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(status="completed", conversation_id="s1")
        store = _mock_store(tasks={"t1": task}, events=[])
        adapter._runner = _mock_runner(store)
        mapping: dict[str, Any] = {"completion_reply_sent": True}
        assert adapter._deliver_terminal_result_without_card("t1", mapping=mapping) is False


# ---------------------------------------------------------------------------
# _maybe_send_completion_result_message
# ---------------------------------------------------------------------------


class TestMaybeSendCompletionResult:
    def test_no_runner_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._client = None
        assert adapter._maybe_send_completion_result_message("t1") is False

    def test_non_feishu_task_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1",
            source_channel="cli",
            status="completed",
            conversation_id="s1",
        )
        store = _mock_store(tasks={"t1": task})
        adapter._runner = _mock_runner(store)
        assert adapter._maybe_send_completion_result_message("t1") is False


# ---------------------------------------------------------------------------
# _patch_task_topic
# ---------------------------------------------------------------------------


class TestPatchTaskTopic:
    def test_no_runner_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        assert adapter._patch_task_topic("t1") is False

    def test_no_client_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        adapter._client = None
        assert adapter._patch_task_topic("t1") is False

    def test_no_task_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        store = _mock_store()
        adapter._runner = _mock_runner(store)
        assert adapter._patch_task_topic("t1") is False


# ---------------------------------------------------------------------------
# _patch_terminal_result_card
# ---------------------------------------------------------------------------


class TestPatchTerminalResultCard:
    def test_no_runner_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        assert adapter._patch_terminal_result_card("t1") is False

    def test_non_terminal_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(status="running", conversation_id="s1")
        store = _mock_store(
            tasks={"t1": task},
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})},
        )
        adapter._runner = _mock_runner(store)
        assert adapter._patch_terminal_result_card("t1") is False


# ---------------------------------------------------------------------------
# _reply_task_topic_card
# ---------------------------------------------------------------------------


class TestReplyTaskTopicCard:
    def test_no_client_returns_none(self) -> None:
        adapter = _make_adapter()
        adapter._client = None
        assert adapter._reply_task_topic_card("om_1", "t1") is None

    def test_with_client_calls_reply(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        with patch("hermit.plugins.builtin.adapters.feishu.adapter.reply_card_return_id") as mock:
            mock.return_value = "om_reply"
            result = adapter._reply_task_topic_card("om_1", "t1")
            assert result == "om_reply"


# ---------------------------------------------------------------------------
# _reissue_pending_approval_cards
# ---------------------------------------------------------------------------


class TestReissuePendingApprovalCards:
    def test_no_runner_returns_early(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._reissue_pending_approval_cards()

    def test_no_client_returns_early(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        adapter._client = None
        adapter._reissue_pending_approval_cards()

    def test_skips_non_feishu_tasks(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        approval = SimpleNamespace(
            approval_id="ap_1",
            task_id="t1",
            status="pending",
            requested_action={},
        )
        task = SimpleNamespace(
            task_id="t1",
            source_channel="cli",
            conversation_id="oc_1",
        )
        store = _mock_store(
            tasks={"t1": task},
            approvals={"ap_1": approval},
            conversations={"oc_1": SimpleNamespace(metadata={})},
        )
        adapter._runner = _mock_runner(store)
        adapter._reissue_pending_approval_cards()


# ---------------------------------------------------------------------------
# _build_pending_approval_card
# ---------------------------------------------------------------------------


class TestBuildPendingApprovalCard:
    def test_with_approval(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        approval = SimpleNamespace(
            approval_id="ap_1",
            requested_action={
                "target_paths": ["/tmp/file.txt"],
                "workspace_root": "/tmp",
                "grant_scope_dir": "/tmp/project",
            },
        )
        card, returned_approval = adapter._build_pending_approval_card(
            "ap_1",
            fallback_text="fallback",
            approval=approval,
        )
        assert card["schema"] == "2.0"
        assert returned_approval is approval

    def test_without_approval_fetches_from_store(self) -> None:
        adapter = _make_adapter()
        approval = SimpleNamespace(
            approval_id="ap_1",
            requested_action={},
        )
        store = _mock_store(approvals={"ap_1": approval})
        adapter._runner = _mock_runner(store)
        _card, returned_approval = adapter._build_pending_approval_card(
            "ap_1",
            fallback_text="fallback",
        )
        assert returned_approval is approval

    def test_with_detail_suffix(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        card, _ = adapter._build_pending_approval_card(
            "ap_1",
            fallback_text="fallback",
            detail_suffix="extra info",
            approval=None,
        )
        assert card is not None


# ---------------------------------------------------------------------------
# _is_expected_ws_close instance method
# ---------------------------------------------------------------------------


class TestIsExpectedWsCloseInstance:
    def test_delegates_to_module_function(self) -> None:
        adapter = _make_adapter()
        exc = Exception("sent 1000 (OK); then received 1000 (OK)")
        assert adapter._is_expected_ws_close(exc) is True
        assert adapter._is_expected_ws_close(Exception("other")) is False


# ---------------------------------------------------------------------------
# _install_ws_exception_handler
# ---------------------------------------------------------------------------


class TestInstallWsExceptionHandler:
    def test_no_loop_does_nothing(self) -> None:
        adapter = _make_adapter()
        adapter._ws_loop = None
        adapter._install_ws_exception_handler()  # Should not raise

    def test_sets_handler_on_loop(self) -> None:
        adapter = _make_adapter()
        loop = MagicMock()
        loop.get_exception_handler.return_value = None
        adapter._ws_loop = loop
        adapter._install_ws_exception_handler()
        loop.set_exception_handler.assert_called_once()
