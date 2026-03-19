"""Extended tests for adapter.py covering _process_message, _dispatch_message_sync_compat,
_handle_approval_action, _refresh_task_topics, handle_post_run_result, and related methods.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.plugins.builtin.adapters.feishu.adapter import FeishuAdapter
from hermit.plugins.builtin.adapters.feishu.normalize import FeishuMessage
from hermit.plugins.builtin.adapters.feishu.reply import ToolStep

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(**kwargs: Any) -> FeishuAdapter:
    settings = SimpleNamespace(
        feishu_app_id=kwargs.get("app_id", "test_id"),
        feishu_app_secret=kwargs.get("app_secret", "test_secret"),
        locale="en-US",
        feishu_thread_progress=kwargs.get("feishu_thread_progress", False),
        feishu_reaction_enabled=False,
    )
    return FeishuAdapter(settings=settings)


def _make_msg(**kwargs: Any) -> FeishuMessage:
    defaults = {
        "chat_id": "oc_test",
        "message_id": "om_test",
        "sender_id": "ou_test",
        "text": "hello",
        "chat_type": "p2p",
        "message_type": "text",
        "image_keys": [],
        "reply_to_message_id": "",
        "quoted_message_id": "",
    }
    defaults.update(kwargs)
    return FeishuMessage(**defaults)


def _mock_store(**kwargs: Any) -> MagicMock:
    store = MagicMock()
    conversations = kwargs.get("conversations", {})
    tasks = kwargs.get("tasks", {})
    approvals = kwargs.get("approvals", {})
    events = kwargs.get("events", [])

    store.get_conversation.side_effect = lambda cid: conversations.get(cid)
    store.get_task.side_effect = lambda tid: tasks.get(tid)
    store.get_approval.side_effect = lambda aid: approvals.get(aid)
    store.list_events.return_value = events
    store.list_step_attempts.return_value = kwargs.get("step_attempts", [])
    store.list_conversations.return_value = list(conversations.keys())
    store.list_approvals.return_value = list(approvals.values())
    store.update_conversation_metadata = MagicMock()
    store.get_last_task_for_conversation = MagicMock(return_value=None)
    return store


def _mock_runner(store: MagicMock | None = None) -> MagicMock:
    runner = MagicMock()
    if store is None:
        store = _mock_store()
    runner.task_controller = MagicMock()
    runner.task_controller.store = store
    runner.task_controller.resolve_text_command.return_value = None
    runner.session_manager = MagicMock()
    runner.session_manager.idle_timeout_seconds = 3600
    runner.session_manager._active = {}
    runner._session_started = []
    return runner


# ---------------------------------------------------------------------------
# _process_message
# ---------------------------------------------------------------------------


class TestProcessMessage:
    def test_no_runner_returns_immediately(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._process_message(_make_msg())

    def test_dispatches_slash_command_raw(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.task_controller.resolve_text_command.return_value = None
        runner.dispatch.return_value = SimpleNamespace(text="ok")
        adapter._runner = runner
        adapter._client = MagicMock()
        msg = _make_msg(text="/help")

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.smart_reply") as mock_reply,
        ):
            adapter._process_message(msg)
            mock_reply.assert_called_once()

    def test_dispatches_approval_command(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.task_controller.resolve_text_command.return_value = (
            "approve_once",
            "ap_1",
            "",
        )
        adapter._runner = runner
        adapter._client = MagicMock()
        msg = _make_msg(text="approve ap_1")

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.smart_reply"),
            patch.object(adapter, "_resolve_approval_from_feishu") as mock_resolve,
        ):
            mock_resolve.return_value = SimpleNamespace(text="approved")
            adapter._process_message(msg)
            mock_resolve.assert_called_once()

    def test_dispatches_raw_exception_sends_error(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.task_controller.resolve_text_command.side_effect = [
            None,
            None,
        ]
        runner.dispatch.side_effect = RuntimeError("agent error")
        adapter._runner = runner
        adapter._client = MagicMock()
        msg = _make_msg(text="/crash")

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_text_reply") as mock_send,
        ):
            adapter._process_message(msg)
            mock_send.assert_called_once()

    def test_async_ingress_path(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.enqueue_ingress = MagicMock(return_value=SimpleNamespace(task_id="t1"))
        ingress = SimpleNamespace(
            resolution="new_task",
            mode="task",
            intent="task",
            ingress_id="ing_1",
            reason="new",
            reason_codes=[],
            anchor_task_id=None,
            parent_task_id=None,
        )
        runner.task_controller.decide_ingress.return_value = ingress
        adapter._runner = runner
        adapter._client = MagicMock()
        msg = _make_msg(text="do a complex task that is longer than 12 chars")

        with patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"):
            adapter._process_message(msg)
            runner.enqueue_ingress.assert_called_once()

    def test_pending_disambiguation_replies(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.enqueue_ingress = MagicMock()
        ingress = SimpleNamespace(
            resolution="pending_disambiguation",
            mode="task",
            intent="task",
        )
        runner.task_controller.decide_ingress.return_value = ingress
        adapter._runner = runner
        adapter._client = MagicMock()
        msg = _make_msg(text="do something longer than twelve")

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.smart_reply") as mock_reply,
        ):
            adapter._process_message(msg)
            mock_reply.assert_called_once()

    def test_append_note_mode_patches_topic(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.enqueue_ingress = MagicMock()
        ingress = SimpleNamespace(
            resolution="appended",
            mode="append_note",
            intent="note",
            task_id="t1",
        )
        runner.task_controller.decide_ingress.return_value = ingress
        adapter._runner = runner
        adapter._client = MagicMock()
        msg = _make_msg(text="append note longer than twelve")

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_done"),
            patch.object(adapter, "_patch_task_topic") as mock_patch,
        ):
            adapter._process_message(msg)
            mock_patch.assert_called_once_with("t1")

    def test_chat_only_intent_dispatches_sync(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.enqueue_ingress = MagicMock()
        ingress = SimpleNamespace(
            resolution="new_task",
            mode="task",
            intent="chat_only",
            ingress_id="",
            reason="",
            reason_codes=[],
            anchor_task_id=None,
        )
        runner.task_controller.decide_ingress.return_value = ingress
        adapter._runner = runner
        adapter._client = MagicMock()
        msg = _make_msg(text="just chat with longer text here")

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"),
            patch.object(adapter, "_dispatch_message_sync_compat") as mock_dispatch,
        ):
            adapter._process_message(msg)
            mock_dispatch.assert_called_once()
            assert mock_dispatch.call_args[1].get("enable_progress_card") is False

    def test_short_text_dispatches_sync(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.enqueue_ingress = MagicMock()
        ingress = SimpleNamespace(
            resolution="new_task",
            mode="task",
            intent="task",
            ingress_id="",
            reason="",
            reason_codes=[],
            anchor_task_id=None,
        )
        runner.task_controller.decide_ingress.return_value = ingress
        adapter._runner = runner
        adapter._client = MagicMock()
        msg = _make_msg(text="hi")

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"),
            patch.object(adapter, "_dispatch_message_sync_compat") as mock_dispatch,
        ):
            adapter._process_message(msg)
            mock_dispatch.assert_called_once()

    def test_sync_compat_fallback_no_async_ingress(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        # Remove enqueue_ingress to simulate no async support
        del runner.enqueue_ingress
        ingress = SimpleNamespace(
            resolution="new_task",
            mode="task",
            intent="task",
        )
        runner.task_controller.decide_ingress.return_value = ingress
        adapter._runner = runner
        adapter._client = MagicMock()
        msg = _make_msg(text="do a complex task that is longer than 12 chars")

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"),
            patch.object(adapter, "_dispatch_message_sync_compat") as mock_dispatch,
        ):
            adapter._process_message(msg)
            mock_dispatch.assert_called_once()

    def test_async_ingress_failure_sends_error(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.enqueue_ingress = MagicMock(side_effect=RuntimeError("enqueue fail"))
        ingress = SimpleNamespace(
            resolution="new_task",
            mode="task",
            intent="task",
            ingress_id="",
            reason="",
            reason_codes=[],
            anchor_task_id=None,
            parent_task_id=None,
        )
        runner.task_controller.decide_ingress.return_value = ingress
        adapter._runner = runner
        adapter._client = MagicMock()
        msg = _make_msg(text="do a complex task that is longer than 12 chars")

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_text_reply") as mock_reply,
        ):
            adapter._process_message(msg)
            mock_reply.assert_called_once()

    def test_schedule_keyword_adds_reaction(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.enqueue_ingress = MagicMock(return_value=SimpleNamespace(task_id="t1"))
        ingress = SimpleNamespace(
            resolution="new_task",
            mode="task",
            intent="task",
            ingress_id="",
            reason="",
            reason_codes=[],
            anchor_task_id=None,
            parent_task_id=None,
        )
        runner.task_controller.decide_ingress.return_value = ingress
        adapter._runner = runner
        adapter._client = MagicMock()
        # "schedule" is a schedule keyword
        msg = _make_msg(text="schedule a meeting tomorrow afternoon please")

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.add_reaction") as mock_reaction,
        ):
            adapter._process_message(msg)
            mock_reaction.assert_called_once()

    def test_reply_to_ref_lookup(self) -> None:
        adapter = _make_adapter()
        conv = SimpleNamespace(
            metadata={"feishu_task_topics": {"t1": {"root_message_id": "om_parent_card"}}}
        )
        store = _mock_store(conversations={"oc_test": conv})
        runner = _mock_runner(store)
        runner.enqueue_ingress = MagicMock(return_value=SimpleNamespace(task_id="t2"))
        ingress = SimpleNamespace(
            resolution="new_task",
            mode="task",
            intent="task",
            ingress_id="",
            reason="",
            reason_codes=[],
            anchor_task_id=None,
            parent_task_id=None,
        )
        runner.task_controller.decide_ingress.return_value = ingress
        adapter._runner = runner
        adapter._client = MagicMock()
        msg = _make_msg(
            text="follow up on that longer than twelve",
            reply_to_message_id="om_parent_card",
        )

        with patch("hermit.plugins.builtin.adapters.feishu.adapter.send_ack"):
            adapter._process_message(msg)
            # Should have passed reply_to_task_id to decide_ingress
            call_kwargs = runner.task_controller.decide_ingress.call_args[1]
            assert call_kwargs.get("reply_to_task_id") == "t1"


# ---------------------------------------------------------------------------
# _dispatch_message_sync_compat
# ---------------------------------------------------------------------------


class TestDispatchMessageSyncCompat:
    def test_no_runner_returns(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._dispatch_message_sync_compat(
            session_id="s1",
            msg=_make_msg(),
            dispatch_text="hello",
        )

    def test_basic_dispatch_with_text_result(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.dispatch.return_value = SimpleNamespace(
            text="response",
            agent_result=SimpleNamespace(
                blocked=False, suspended=False, task_id="t1", approval_id=""
            ),
        )
        adapter._runner = runner
        adapter._client = MagicMock()

        with patch("hermit.plugins.builtin.adapters.feishu.adapter.smart_reply") as mock_reply:
            adapter._dispatch_message_sync_compat(
                session_id="s1",
                msg=_make_msg(),
                dispatch_text="hello",
            )
            mock_reply.assert_called_once()

    def test_dispatch_exception_sends_error(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.dispatch.side_effect = RuntimeError("agent crashed")
        adapter._runner = runner
        adapter._client = MagicMock()

        with patch("hermit.plugins.builtin.adapters.feishu.adapter.send_text_reply") as mock_send:
            adapter._dispatch_message_sync_compat(
                session_id="s1",
                msg=_make_msg(),
                dispatch_text="hello",
            )
            mock_send.assert_called_once()

    def test_note_appended_patches_topic(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.dispatch.return_value = SimpleNamespace(
            text="noted",
            agent_result=SimpleNamespace(
                blocked=False,
                suspended=False,
                task_id="t1",
                approval_id="",
                execution_status="note_appended",
            ),
        )
        adapter._runner = runner
        adapter._client = MagicMock()

        with patch.object(adapter, "_patch_task_topic") as mock_patch:
            adapter._dispatch_message_sync_compat(
                session_id="s1",
                msg=_make_msg(),
                dispatch_text="note",
            )
            mock_patch.assert_called_once_with("t1")

    def test_blocked_result_binds_topic(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        store = _mock_store(
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {}})}
        )
        runner.task_controller.store = store
        runner.dispatch.return_value = SimpleNamespace(
            text="waiting",
            agent_result=SimpleNamespace(
                blocked=True,
                suspended=False,
                task_id="t1",
                approval_id="ap_1",
                execution_status="blocked",
            ),
        )
        adapter._runner = runner
        adapter._client = MagicMock()

        with (
            patch.object(adapter, "_present_task_result") as mock_present,
            patch.object(adapter, "_bind_task_topic") as mock_bind,
        ):
            mock_present.return_value = ("om_card", True, "t1")
            adapter._dispatch_message_sync_compat(
                session_id="s1",
                msg=_make_msg(),
                dispatch_text="hello",
            )
            mock_bind.assert_called_once()

    def test_with_progress_enabled(self) -> None:
        adapter = _make_adapter(feishu_thread_progress=True)
        runner = _mock_runner()
        runner.dispatch.return_value = SimpleNamespace(
            text="result",
            agent_result=SimpleNamespace(
                blocked=False,
                suspended=False,
                task_id="t1",
                approval_id="",
                execution_status="completed",
            ),
        )
        adapter._runner = runner
        adapter._client = MagicMock()

        with (
            patch(
                "hermit.plugins.builtin.adapters.feishu.adapter.reply_card_return_id"
            ) as mock_reply,
            patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card"),
            patch.object(adapter, "_present_task_result") as mock_present,
        ):
            mock_reply.return_value = "om_progress"
            mock_present.return_value = ("om_progress", False, "t1")
            adapter._dispatch_message_sync_compat(
                session_id="s1",
                msg=_make_msg(),
                dispatch_text="hello",
                enable_progress_card=True,
            )
            mock_reply.assert_called_once()

    def test_completed_task_with_notes_unbinds(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        store = _mock_store(
            events=[{"event_type": "task.note.appended", "payload": {}}],
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})},
        )
        runner.task_controller.store = store
        runner.dispatch.return_value = SimpleNamespace(
            text="done",
            agent_result=SimpleNamespace(
                blocked=False,
                suspended=False,
                task_id="t1",
                approval_id="",
                execution_status="completed",
            ),
        )
        adapter._runner = runner
        adapter._client = MagicMock()

        with (
            patch.object(adapter, "_present_task_result") as mock_present,
            patch.object(adapter, "_unbind_task_topic") as mock_unbind,
        ):
            mock_present.return_value = (None, False, "t1")
            adapter._dispatch_message_sync_compat(
                session_id="s1",
                msg=_make_msg(),
                dispatch_text="hello",
            )
            mock_unbind.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_approval_action
# ---------------------------------------------------------------------------


class TestHandleApprovalAction:
    def test_no_runner_returns(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._client = None
        adapter._handle_approval_action("ap_1", "approve_once", "om_1")

    def test_no_store_returns(self) -> None:
        adapter = _make_adapter()
        runner = MagicMock()
        runner.task_controller = None
        adapter._runner = runner
        adapter._client = MagicMock()
        adapter._handle_approval_action("ap_1", "approve_once", "om_1")

    def test_approval_not_found_patches_error_card(self) -> None:
        adapter = _make_adapter()
        store = _mock_store()
        adapter._runner = _mock_runner(store)
        adapter._client = MagicMock()

        with patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card") as mock_patch:
            adapter._handle_approval_action("ap_missing", "approve_once", "om_1")
            mock_patch.assert_called_once()

    def test_task_not_found_patches_error_card(self) -> None:
        adapter = _make_adapter()
        approval = SimpleNamespace(
            approval_id="ap_1",
            task_id="t_missing",
            status="pending",
            requested_action={},
        )
        store = _mock_store(approvals={"ap_1": approval})
        adapter._runner = _mock_runner(store)
        adapter._client = MagicMock()

        with patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card") as mock_patch:
            adapter._handle_approval_action("ap_1", "approve_once", "om_1")
            mock_patch.assert_called_once()

    def test_deny_action_resolves_and_patches(self) -> None:
        adapter = _make_adapter()
        approval = SimpleNamespace(
            approval_id="ap_1",
            task_id="t1",
            status="pending",
            requested_action={},
        )
        task = SimpleNamespace(
            task_id="t1",
            conversation_id="s1",
            source_channel="feishu",
        )
        store = _mock_store(
            approvals={"ap_1": approval},
            tasks={"t1": task},
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})},
        )
        adapter._runner = _mock_runner(store)
        adapter._client = MagicMock()

        with (
            patch.object(adapter, "_resolve_approval_from_feishu") as mock_resolve,
            patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card"),
        ):
            mock_resolve.return_value = SimpleNamespace(text="denied")
            adapter._handle_approval_action("ap_1", "deny", "om_1")
            mock_resolve.assert_called_once()

    def test_approve_with_enqueue_resume(self) -> None:
        adapter = _make_adapter()
        approval = SimpleNamespace(
            approval_id="ap_1",
            task_id="t1",
            status="pending",
            requested_action={},
        )
        task = SimpleNamespace(
            task_id="t1",
            conversation_id="s1",
            source_channel="feishu",
        )
        store = _mock_store(
            approvals={"ap_1": approval},
            tasks={"t1": task},
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})},
        )
        runner = _mock_runner(store)
        runner.enqueue_approval_resume = MagicMock()
        adapter._runner = runner
        adapter._client = MagicMock()

        with (
            patch.object(adapter, "_resolve_approval_from_feishu") as mock_resolve,
            patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card"),
            patch.object(adapter, "_patch_task_topic"),
        ):
            mock_resolve.return_value = SimpleNamespace(text="approved")
            adapter._handle_approval_action("ap_1", "approve_once", "om_1")
            mock_resolve.assert_called_once()

    def test_approve_fallback_presents_result(self) -> None:
        adapter = _make_adapter()
        approval = SimpleNamespace(
            approval_id="ap_1",
            task_id="t1",
            status="pending",
            requested_action={},
        )
        task = SimpleNamespace(
            task_id="t1",
            conversation_id="s1",
            source_channel="feishu",
        )
        store = _mock_store(
            approvals={"ap_1": approval},
            tasks={"t1": task},
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})},
        )
        runner = _mock_runner(store)
        del runner.enqueue_approval_resume
        adapter._runner = runner
        adapter._client = MagicMock()

        with (
            patch.object(adapter, "_resolve_approval_from_feishu") as mock_resolve,
            patch.object(adapter, "_present_task_result") as mock_present,
        ):
            mock_resolve.return_value = SimpleNamespace(
                text="result",
                agent_result=SimpleNamespace(
                    blocked=False,
                    suspended=False,
                    task_id="t1",
                    approval_id="",
                ),
            )
            mock_present.return_value = (None, False, "t1")
            adapter._handle_approval_action("ap_1", "approve_once", "om_1")

    def test_exception_patches_error_card(self) -> None:
        adapter = _make_adapter()
        approval = SimpleNamespace(
            approval_id="ap_1",
            task_id="t1",
            status="pending",
            requested_action={},
        )
        task = SimpleNamespace(
            task_id="t1",
            conversation_id="s1",
            source_channel="feishu",
        )
        store = _mock_store(
            approvals={"ap_1": approval},
            tasks={"t1": task},
            conversations={"s1": SimpleNamespace(metadata={})},
        )
        adapter._runner = _mock_runner(store)
        adapter._client = MagicMock()

        with (
            patch.object(
                adapter, "_resolve_approval_from_feishu", side_effect=RuntimeError("boom")
            ),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card") as mock_patch,
        ):
            adapter._handle_approval_action("ap_1", "deny", "om_1")
            mock_patch.assert_called()

    def test_approval_not_found_no_message_id(self) -> None:
        adapter = _make_adapter()
        store = _mock_store()
        adapter._runner = _mock_runner(store)
        adapter._client = MagicMock()
        # Empty message_id - should not patch
        with patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card") as mock_patch:
            adapter._handle_approval_action("ap_missing", "approve_once", "")
            mock_patch.assert_not_called()

    def test_blocked_result_updates_topic(self) -> None:
        adapter = _make_adapter()
        approval = SimpleNamespace(
            approval_id="ap_1", task_id="t1", status="pending", requested_action={}
        )
        task = SimpleNamespace(task_id="t1", conversation_id="s1", source_channel="feishu")
        store = _mock_store(
            approvals={"ap_1": approval},
            tasks={"t1": task},
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})},
        )
        runner = _mock_runner(store)
        del runner.enqueue_approval_resume
        adapter._runner = runner
        adapter._client = MagicMock()

        with (
            patch.object(adapter, "_resolve_approval_from_feishu") as mock_resolve,
            patch.object(adapter, "_present_task_result") as mock_present,
            patch.object(adapter, "_update_task_topic_mapping") as mock_update,
        ):
            mock_resolve.return_value = SimpleNamespace(
                text="result",
                agent_result=SimpleNamespace(
                    blocked=True, suspended=False, task_id="t1", approval_id="ap_2"
                ),
            )
            mock_present.return_value = ("om_card", True, "t1")
            adapter._handle_approval_action("ap_1", "approve_once", "om_1")
            mock_update.assert_called()


# ---------------------------------------------------------------------------
# handle_post_run_result
# ---------------------------------------------------------------------------


class TestHandlePostRunResultExtended:
    def test_finds_task_from_session_id(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1",
            source_channel="feishu",
            status="completed",
            conversation_id="s1",
        )
        store = _mock_store(
            tasks={"t1": task},
            conversations={
                "s1": SimpleNamespace(
                    metadata={"feishu_task_topics": {"t1": {"root_message_id": "om_card"}}}
                )
            },
            events=[{"event_type": "task.completed", "payload": {"result_text": "done"}}],
            step_attempts=[
                SimpleNamespace(context={"ingress_metadata": {"dispatch_mode": "async"}})
            ],
        )
        store.get_last_task_for_conversation.return_value = SimpleNamespace(task_id="t1")
        adapter._runner = _mock_runner(store)

        with patch.object(adapter, "_patch_terminal_result_card") as mock_patch:
            mock_patch.return_value = True
            adapter.handle_post_run_result(SimpleNamespace(task_id=""), session_id="s1")
            # Should look up the task via session_id
            store.get_last_task_for_conversation.assert_called_with("s1")

    def test_non_async_task_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1",
            source_channel="feishu",
            status="completed",
            conversation_id="s1",
        )
        store = _mock_store(
            tasks={"t1": task},
            step_attempts=[
                SimpleNamespace(context={"ingress_metadata": {"dispatch_mode": "sync"}})
            ],
        )
        adapter._runner = _mock_runner(store)
        result = adapter.handle_post_run_result(SimpleNamespace(task_id="t1"))
        assert result is False

    def test_terminal_without_card_delivers_text(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1",
            source_channel="feishu",
            status="completed",
            conversation_id="s1",
        )
        store = _mock_store(
            tasks={"t1": task},
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})},
            step_attempts=[
                SimpleNamespace(context={"ingress_metadata": {"dispatch_mode": "async"}})
            ],
        )
        adapter._runner = _mock_runner(store)

        with patch.object(adapter, "_deliver_terminal_result_without_card") as mock_deliver:
            mock_deliver.return_value = True
            adapter.handle_post_run_result(SimpleNamespace(task_id="t1"))
            mock_deliver.assert_called()


# ---------------------------------------------------------------------------
# _present_task_result extended
# ---------------------------------------------------------------------------


class TestPresentTaskResultExtended:
    def test_result_text_with_existing_card_patches(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        adapter._runner = _mock_runner()
        result = SimpleNamespace(
            text="all done",
            agent_result=SimpleNamespace(
                blocked=False, suspended=False, task_id="t1", approval_id=""
            ),
        )

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card") as mock_patch,
            patch("hermit.plugins.builtin.adapters.feishu.adapter.build_result_card_with_process"),
            patch.object(adapter, "_task_history_steps", return_value=[]),
        ):
            mid, _blocked, _tid = adapter._present_task_result(
                reply_to_message_id=None,
                existing_card_message_id="om_card",
                chat_id="oc_1",
                result=result,
                steps=[],
            )
            mock_patch.assert_called_once()
            assert mid == "om_card"

    def test_result_text_with_reply_to_sends_smart_reply(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        result = SimpleNamespace(
            text="simple answer",
            agent_result=SimpleNamespace(
                blocked=False, suspended=False, task_id="t1", approval_id=""
            ),
        )

        with patch("hermit.plugins.builtin.adapters.feishu.adapter.smart_reply") as mock_reply:
            _mid, _blocked, _tid = adapter._present_task_result(
                reply_to_message_id="om_parent",
                existing_card_message_id=None,
                chat_id="oc_1",
                result=result,
                steps=[],
            )
            mock_reply.assert_called_once()

    def test_result_text_without_reply_sends_to_chat(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        result = SimpleNamespace(
            text="proactive message",
            agent_result=SimpleNamespace(
                blocked=False, suspended=False, task_id="t1", approval_id=""
            ),
        )

        with patch(
            "hermit.plugins.builtin.adapters.feishu.adapter.smart_send_message"
        ) as mock_send:
            _mid, _blocked, _tid = adapter._present_task_result(
                reply_to_message_id=None,
                existing_card_message_id=None,
                chat_id="oc_1",
                result=result,
                steps=[],
            )
            mock_send.assert_called_once()

    def test_blocked_without_existing_card_creates_reply(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        adapter._runner = _mock_runner()
        result = SimpleNamespace(
            text="need approval",
            agent_result=SimpleNamespace(
                blocked=True, suspended=False, task_id="t1", approval_id="ap_1"
            ),
        )

        with (
            patch.object(adapter, "_build_pending_approval_card") as mock_build,
            patch(
                "hermit.plugins.builtin.adapters.feishu.adapter.reply_card_return_id"
            ) as mock_reply,
        ):
            mock_build.return_value = ({"schema": "2.0"}, None)
            mock_reply.return_value = "om_new_card"
            mid, blocked, _tid = adapter._present_task_result(
                reply_to_message_id="om_parent",
                existing_card_message_id=None,
                chat_id="oc_1",
                result=result,
                steps=[],
            )
            assert mid == "om_new_card"
            assert blocked is True


# ---------------------------------------------------------------------------
# _patch_terminal_result_card
# ---------------------------------------------------------------------------


class TestPatchTerminalResultCardExtended:
    def test_patches_completed_task_with_result(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(status="completed", conversation_id="s1")
        store = _mock_store(
            tasks={"t1": task},
            conversations={
                "s1": SimpleNamespace(
                    metadata={
                        "feishu_task_topics": {
                            "t1": {"root_message_id": "om_card", "topic_signature": "old"}
                        }
                    }
                )
            },
            events=[{"event_type": "task.completed", "payload": {"result_text": "done"}}],
        )
        adapter._runner = _mock_runner(store)

        with (
            patch(
                "hermit.plugins.builtin.adapters.feishu.adapter.build_result_card_with_process"
            ) as mock_build,
            patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card") as mock_patch,
            patch("hermit.plugins.builtin.adapters.feishu.adapter.ProjectionService") as MockPS,
        ):
            MockPS.return_value.ensure_task_projection.return_value = {"tool_history": []}
            mock_build.return_value = {"schema": "2.0", "body": {}}
            mock_patch.return_value = True
            result = adapter._patch_terminal_result_card("t1", message_id="om_card")
            assert result is True

    def test_same_signature_skips_patch(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(status="completed", conversation_id="s1")
        # Pre-compute signature for the completion card
        from hermit.plugins.builtin.adapters.feishu.reply import build_completion_status_card

        card = build_completion_status_card(locale="en-US")
        sig = FeishuAdapter._card_signature(card)

        store = _mock_store(
            tasks={"t1": task},
            conversations={
                "s1": SimpleNamespace(
                    metadata={
                        "feishu_task_topics": {
                            "t1": {"root_message_id": "om_card", "topic_signature": sig}
                        }
                    }
                )
            },
            events=[],
        )
        adapter._runner = _mock_runner(store)

        result = adapter._patch_terminal_result_card("t1", message_id="om_card")
        assert result is False

    def test_no_root_message_id_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(status="completed", conversation_id="s1")
        store = _mock_store(
            tasks={"t1": task},
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})},
        )
        adapter._runner = _mock_runner(store)
        result = adapter._patch_terminal_result_card("t1")
        assert result is False


# ---------------------------------------------------------------------------
# _maybe_send_completion_result_message extended
# ---------------------------------------------------------------------------


class TestMaybeSendCompletionExtended:
    def test_sends_when_conditions_met(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", source_channel="feishu", status="completed", conversation_id="s1"
        )
        store = _mock_store(
            tasks={"t1": task},
            events=[{"event_type": "task.note.appended", "payload": {}}],
            conversations={
                "s1": SimpleNamespace(
                    metadata={
                        "feishu_task_topics": {
                            "t1": {"chat_id": "oc_1", "completion_reply_sent": False}
                        }
                    }
                )
            },
        )
        # Also need terminal result text
        store.list_events.return_value = [
            {"event_type": "task.note.appended", "payload": {}},
            {"event_type": "task.completed", "payload": {"result_text": "All done"}},
        ]
        adapter._runner = _mock_runner(store)

        with patch(
            "hermit.plugins.builtin.adapters.feishu.adapter.smart_send_message"
        ) as mock_send:
            mock_send.return_value = "om_sent"
            result = adapter._maybe_send_completion_result_message("t1")
            assert result is True


# ---------------------------------------------------------------------------
# _patch_task_topic extended
# ---------------------------------------------------------------------------


class TestPatchTaskTopicExtended:
    def test_patches_when_signature_differs(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(status="running", conversation_id="s1")
        store = _mock_store(
            tasks={"t1": task},
            conversations={
                "s1": SimpleNamespace(
                    metadata={
                        "feishu_task_topics": {
                            "t1": {"root_message_id": "om_card", "topic_signature": "old_sig"}
                        }
                    }
                )
            },
        )
        adapter._runner = _mock_runner(store)

        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.ProjectionService") as MockPS,
            patch(
                "hermit.plugins.builtin.adapters.feishu.adapter.build_progress_card"
            ) as mock_build,
            patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card") as mock_patch,
        ):
            MockPS.return_value.ensure_task_projection.return_value = {"topic": {}}
            mock_build.return_value = {"schema": "2.0", "new": True}
            mock_patch.return_value = True
            result = adapter._patch_task_topic("t1")
            assert result is True


# ---------------------------------------------------------------------------
# _deliver_terminal_result_without_card extended
# ---------------------------------------------------------------------------


class TestDeliverTerminalResultExtended:
    def test_delivers_via_reply_to_message(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", status="completed", conversation_id="s1", source_channel="feishu"
        )
        store = _mock_store(
            tasks={"t1": task},
            conversations={
                "s1": SimpleNamespace(
                    metadata={
                        "feishu_task_topics": {
                            "t1": {
                                "reply_to_message_id": "om_reply",
                                "completion_reply_sent": False,
                            }
                        }
                    }
                )
            },
            events=[{"event_type": "task.completed", "payload": {"result_text": "Done"}}],
        )
        adapter._runner = _mock_runner(store)

        mapping = {"reply_to_message_id": "om_reply", "completion_reply_sent": False}
        with patch("hermit.plugins.builtin.adapters.feishu.adapter.smart_reply") as mock_reply:
            mock_reply.return_value = True
            result = adapter._deliver_terminal_result_without_card("t1", mapping=mapping)
            assert result is True

    def test_fallback_to_chat_when_reply_fails(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", status="completed", conversation_id="s1", source_channel="feishu"
        )
        store = _mock_store(
            tasks={"t1": task},
            conversations={
                "s1": SimpleNamespace(
                    metadata={"feishu_task_topics": {"t1": {"completion_reply_sent": False}}}
                )
            },
            events=[{"event_type": "task.completed", "payload": {"result_text": "Done"}}],
        )
        adapter._runner = _mock_runner(store)

        mapping = {
            "reply_to_message_id": "om_reply",
            "chat_id": "oc_1",
            "completion_reply_sent": False,
        }
        with (
            patch("hermit.plugins.builtin.adapters.feishu.adapter.smart_reply") as mock_reply,
            patch("hermit.plugins.builtin.adapters.feishu.adapter.smart_send_message") as mock_send,
        ):
            mock_reply.return_value = False
            mock_send.return_value = "om_sent"
            result = adapter._deliver_terminal_result_without_card("t1", mapping=mapping)
            assert result is True

    def test_delivers_via_chat_id_only(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", status="completed", conversation_id="s1", source_channel="feishu"
        )
        store = _mock_store(
            tasks={"t1": task},
            conversations={
                "s1": SimpleNamespace(
                    metadata={"feishu_task_topics": {"t1": {"completion_reply_sent": False}}}
                )
            },
            events=[{"event_type": "task.completed", "payload": {"result_text": "Done"}}],
        )
        adapter._runner = _mock_runner(store)

        mapping = {"chat_id": "oc_1", "completion_reply_sent": False}
        with patch(
            "hermit.plugins.builtin.adapters.feishu.adapter.smart_send_message"
        ) as mock_send:
            mock_send.return_value = "om_sent"
            result = adapter._deliver_terminal_result_without_card("t1", mapping=mapping)
            assert result is True

    def test_no_chat_id_or_reply_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", status="completed", conversation_id="s1", source_channel="feishu"
        )
        store = _mock_store(
            tasks={"t1": task},
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})},
            events=[{"event_type": "task.completed", "payload": {"result_text": "Done"}}],
        )
        adapter._runner = _mock_runner(store)

        # Mock _chat_id_from_conversation_id to return "" so there's truly no chat_id
        with patch.object(adapter, "_chat_id_from_conversation_id", return_value=""):
            mapping: dict[str, Any] = {"completion_reply_sent": False}
            result = adapter._deliver_terminal_result_without_card("t1", mapping=mapping)
            assert result is False

    def test_no_result_text_returns_false(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", status="completed", conversation_id="s1", source_channel="feishu"
        )
        store = _mock_store(
            tasks={"t1": task},
            conversations={"s1": SimpleNamespace(metadata={"feishu_task_topics": {"t1": {}}})},
            events=[{"event_type": "task.completed", "payload": {}}],
        )
        adapter._runner = _mock_runner(store)

        mapping = {"chat_id": "oc_1", "completion_reply_sent": False}
        result = adapter._deliver_terminal_result_without_card("t1", mapping=mapping)
        assert result is False


# ---------------------------------------------------------------------------
# _reissue_pending_approval_cards extended
# ---------------------------------------------------------------------------


class TestReissuePendingApprovalCardsExtended:
    def test_sends_card_for_feishu_task(self) -> None:
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
            source_channel="feishu",
            conversation_id="oc_test_chat",
        )
        store = _mock_store(
            approvals={"ap_1": approval},
            tasks={"t1": task},
            conversations={"oc_test_chat": SimpleNamespace(metadata={})},
        )
        adapter._runner = _mock_runner(store)

        with (
            patch.object(adapter, "_build_pending_approval_card") as mock_build,
            patch("hermit.plugins.builtin.adapters.feishu.adapter.send_card") as mock_send,
        ):
            mock_build.return_value = ({"schema": "2.0"}, approval)
            mock_send.return_value = "om_reissued"
            adapter._reissue_pending_approval_cards()
            mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# _refresh_task_topics
# ---------------------------------------------------------------------------


class TestRefreshTaskTopics:
    def test_no_runner_returns(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        adapter._client = MagicMock()
        adapter._refresh_task_topics()  # should not raise

    def test_no_client_returns(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        adapter._client = None
        adapter._refresh_task_topics()

    def test_stopped_returns(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        adapter._client = MagicMock()
        adapter._stopped = True
        adapter._refresh_task_topics()

    def test_no_store_returns(self) -> None:
        adapter = _make_adapter()
        adapter._runner = MagicMock()
        adapter._runner.task_controller = None
        adapter._client = MagicMock()
        adapter._refresh_task_topics()

    def test_skips_non_dict_mapping(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        conv = SimpleNamespace(metadata={"feishu_task_topics": {"t1": "not_a_dict"}})
        store = _mock_store(conversations={"s1": conv})
        store.list_conversations.return_value = ["s1"]
        adapter._runner = _mock_runner(store)
        with patch.object(adapter, "_schedule_topic_refresh"):
            adapter._refresh_task_topics()
        store.update_conversation_metadata.assert_called()

    def test_skips_non_feishu_task(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(task_id="t1", source_channel="cli", status="running")
        conv = SimpleNamespace(metadata={"feishu_task_topics": {"t1": {"card_mode": "topic"}}})
        store = _mock_store(tasks={"t1": task}, conversations={"s1": conv})
        store.list_conversations.return_value = ["s1"]
        adapter._runner = _mock_runner(store)
        with patch.object(adapter, "_schedule_topic_refresh"):
            adapter._refresh_task_topics()
        store.update_conversation_metadata.assert_called()

    def test_approval_card_mode_resolved_approval_reverts_to_topic(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", source_channel="feishu", status="running", conversation_id="s1"
        )
        resolved_approval = SimpleNamespace(approval_id="ap_1", status="approved")
        conv = SimpleNamespace(
            metadata={
                "feishu_task_topics": {
                    "t1": {
                        "card_mode": "approval",
                        "root_message_id": "om_card",
                        "approval_id": "ap_1",
                    }
                }
            }
        )
        store = _mock_store(
            tasks={"t1": task},
            conversations={"s1": conv},
            approvals={"ap_1": resolved_approval},
        )
        store.list_conversations.return_value = ["s1"]
        adapter._runner = _mock_runner(store)
        with (
            patch.object(adapter, "_schedule_topic_refresh"),
            patch.object(adapter, "_patch_task_topic") as mock_patch,
        ):
            adapter._refresh_task_topics()
            mock_patch.assert_called_with("t1", message_id="om_card")

    def test_approval_card_mode_terminal_patches_result(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", source_channel="feishu", status="completed", conversation_id="s1"
        )
        conv = SimpleNamespace(
            metadata={
                "feishu_task_topics": {
                    "t1": {
                        "card_mode": "approval",
                        "root_message_id": "om_card",
                        "approval_id": "",
                    }
                }
            }
        )
        store = _mock_store(tasks={"t1": task}, conversations={"s1": conv})
        store.list_conversations.return_value = ["s1"]
        adapter._runner = _mock_runner(store)
        with (
            patch.object(adapter, "_schedule_topic_refresh"),
            patch.object(adapter, "_patch_terminal_result_card") as mock_patch,
        ):
            adapter._refresh_task_topics()
            mock_patch.assert_called_with("t1", message_id="om_card")

    def test_approval_card_mode_no_root_message_id_removes(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", source_channel="feishu", status="running", conversation_id="s1"
        )
        conv = SimpleNamespace(
            metadata={
                "feishu_task_topics": {"t1": {"card_mode": "approval", "root_message_id": ""}}
            }
        )
        store = _mock_store(tasks={"t1": task}, conversations={"s1": conv})
        store.list_conversations.return_value = ["s1"]
        adapter._runner = _mock_runner(store)
        with patch.object(adapter, "_schedule_topic_refresh"):
            adapter._refresh_task_topics()
        store.update_conversation_metadata.assert_called()

    def test_topic_mode_blocked_with_pending_approval(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", source_channel="feishu", status="blocked", conversation_id="s1"
        )
        approval = SimpleNamespace(approval_id="ap_1", status="pending", requested_action={})
        conv = SimpleNamespace(
            metadata={
                "feishu_task_topics": {"t1": {"card_mode": "topic", "root_message_id": "om_card"}}
            }
        )
        store = _mock_store(
            tasks={"t1": task},
            conversations={"s1": conv},
            approvals={"ap_1": approval},
        )
        store.list_conversations.return_value = ["s1"]
        store.list_approvals.return_value = [approval]
        adapter._runner = _mock_runner(store)
        with (
            patch.object(adapter, "_schedule_topic_refresh"),
            patch.object(
                adapter, "_build_pending_approval_card", return_value=({"schema": "2.0"}, approval)
            ),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card") as mock_patch,
        ):
            adapter._refresh_task_topics()
            mock_patch.assert_called()

    def test_topic_mode_blocked_pending_no_message_sends_card(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", source_channel="feishu", status="blocked", conversation_id="s1"
        )
        approval = SimpleNamespace(approval_id="ap_1", status="pending", requested_action={})
        conv = SimpleNamespace(metadata={"feishu_task_topics": {"t1": {"card_mode": "topic"}}})
        store = _mock_store(
            tasks={"t1": task},
            conversations={"s1": conv},
            approvals={"ap_1": approval},
        )
        store.list_conversations.return_value = ["s1"]
        store.list_approvals.return_value = [approval]
        adapter._runner = _mock_runner(store)
        with (
            patch.object(adapter, "_schedule_topic_refresh"),
            patch.object(
                adapter, "_build_pending_approval_card", return_value=({"schema": "2.0"}, approval)
            ),
            patch.object(adapter, "_send_task_card", return_value="om_new") as mock_send,
        ):
            adapter._refresh_task_topics()
            mock_send.assert_called()

    def test_topic_mode_no_message_terminal_delivers_text(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", source_channel="feishu", status="completed", conversation_id="s1"
        )
        conv = SimpleNamespace(metadata={"feishu_task_topics": {"t1": {"card_mode": "topic"}}})
        store = _mock_store(tasks={"t1": task}, conversations={"s1": conv})
        store.list_conversations.return_value = ["s1"]
        adapter._runner = _mock_runner(store)
        with (
            patch.object(adapter, "_schedule_topic_refresh"),
            patch.object(adapter, "_deliver_terminal_result_without_card") as mock_deliver,
        ):
            adapter._refresh_task_topics()
            mock_deliver.assert_called()

    def test_topic_mode_no_message_running_creates_card(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", source_channel="feishu", status="running", conversation_id="s1"
        )
        conv = SimpleNamespace(metadata={"feishu_task_topics": {"t1": {"card_mode": "topic"}}})
        store = _mock_store(tasks={"t1": task}, conversations={"s1": conv})
        store.list_conversations.return_value = ["s1"]
        adapter._runner = _mock_runner(store)
        with (
            patch.object(adapter, "_schedule_topic_refresh"),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.ProjectionService") as MockPS,
            patch.object(adapter, "_topic_has_displayable_progress", return_value=True),
            patch.object(adapter, "_task_history_steps", return_value=[]),
            patch.object(adapter, "_progress_hint_from_topic", return_value="thinking"),
            patch(
                "hermit.plugins.builtin.adapters.feishu.adapter.build_progress_card",
                return_value={"schema": "2.0"},
            ),
            patch.object(adapter, "_send_task_card", return_value="om_new") as mock_send,
        ):
            MockPS.return_value.ensure_task_projection.return_value = {"topic": {}}
            adapter._refresh_task_topics()
            mock_send.assert_called()

    def test_topic_mode_no_message_running_no_progress_skips(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", source_channel="feishu", status="running", conversation_id="s1"
        )
        conv = SimpleNamespace(metadata={"feishu_task_topics": {"t1": {"card_mode": "topic"}}})
        store = _mock_store(tasks={"t1": task}, conversations={"s1": conv})
        store.list_conversations.return_value = ["s1"]
        adapter._runner = _mock_runner(store)
        with (
            patch.object(adapter, "_schedule_topic_refresh"),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.ProjectionService") as MockPS,
            patch.object(adapter, "_topic_has_displayable_progress", return_value=False),
        ):
            MockPS.return_value.ensure_task_projection.return_value = {"topic": {}}
            adapter._refresh_task_topics()
        store.update_conversation_metadata.assert_not_called()

    def test_topic_mode_with_message_terminal_patches(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", source_channel="feishu", status="completed", conversation_id="s1"
        )
        conv = SimpleNamespace(
            metadata={
                "feishu_task_topics": {"t1": {"card_mode": "topic", "root_message_id": "om_card"}}
            }
        )
        store = _mock_store(tasks={"t1": task}, conversations={"s1": conv})
        store.list_conversations.return_value = ["s1"]
        adapter._runner = _mock_runner(store)
        with (
            patch.object(adapter, "_schedule_topic_refresh"),
            patch.object(adapter, "_patch_terminal_result_card") as mock_patch,
        ):
            adapter._refresh_task_topics()
            mock_patch.assert_called_with("t1", message_id="om_card")

    def test_topic_mode_with_message_running_patches_topic(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        task = SimpleNamespace(
            task_id="t1", source_channel="feishu", status="running", conversation_id="s1"
        )
        conv = SimpleNamespace(
            metadata={
                "feishu_task_topics": {"t1": {"card_mode": "topic", "root_message_id": "om_card"}}
            }
        )
        store = _mock_store(tasks={"t1": task}, conversations={"s1": conv})
        store.list_conversations.return_value = ["s1"]
        adapter._runner = _mock_runner(store)
        with (
            patch.object(adapter, "_schedule_topic_refresh"),
            patch.object(adapter, "_patch_task_topic") as mock_patch,
        ):
            adapter._refresh_task_topics()
            mock_patch.assert_called_with("t1", message_id="om_card")

    def test_exception_still_reschedules(self) -> None:
        adapter = _make_adapter()
        adapter._client = MagicMock()
        store = _mock_store()
        store.list_conversations.side_effect = RuntimeError("db error")
        runner = _mock_runner(store)
        adapter._runner = runner
        with patch.object(adapter, "_schedule_topic_refresh") as mock_sched:
            with pytest.raises(RuntimeError, match="db error"):
                adapter._refresh_task_topics()
            mock_sched.assert_called()


# ---------------------------------------------------------------------------
# _ingest_image_record
# ---------------------------------------------------------------------------


class TestIngestImageRecord:
    def test_no_runner_returns_none(self) -> None:
        adapter = _make_adapter()
        adapter._runner = None
        result = adapter._ingest_image_record(session_id="s1", message_id="m1", image_key="img_1")
        assert result is None

    def test_no_tool_executor_returns_none(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        runner.agent = None
        adapter._runner = runner
        result = adapter._ingest_image_record(session_id="s1", message_id="m1", image_key="img_1")
        assert result is None

    def test_key_error_returns_none(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        tool_executor = MagicMock()
        tool_executor.execute.side_effect = KeyError("tool not found")
        runner.agent = SimpleNamespace(
            tool_executor=tool_executor,
            workspace_root="/tmp",
        )
        runner.task_controller.start_task.return_value = SimpleNamespace(task_id="t1")
        adapter._runner = runner
        result = adapter._ingest_image_record(session_id="s1", message_id="m1", image_key="img_1")
        assert result is None
        runner.task_controller.finalize_result.assert_called()

    def test_generic_exception_returns_none(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        tool_executor = MagicMock()
        tool_executor.execute.side_effect = RuntimeError("fail")
        runner.agent = SimpleNamespace(
            tool_executor=tool_executor,
            workspace_root="/tmp",
        )
        runner.task_controller.start_task.return_value = SimpleNamespace(task_id="t1")
        adapter._runner = runner
        result = adapter._ingest_image_record(session_id="s1", message_id="m1", image_key="img_1")
        assert result is None

    def test_blocked_result_returns_none(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        exec_result = SimpleNamespace(
            blocked=True,
            approval_id="ap_1",
            execution_status="blocked",
            result_code=None,
            raw_result=None,
        )
        tool_executor = MagicMock()
        tool_executor.execute.return_value = exec_result
        runner.agent = SimpleNamespace(
            tool_executor=tool_executor,
            workspace_root="/tmp",
        )
        runner.task_controller.start_task.return_value = SimpleNamespace(task_id="t1")
        adapter._runner = runner
        with patch("hermit.kernel.policy.approvals.approvals.ApprovalService"):
            result = adapter._ingest_image_record(
                session_id="s1", message_id="m1", image_key="img_1"
            )
        assert result is None

    def test_success_returns_dict(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        exec_result = SimpleNamespace(
            blocked=False,
            approval_id="",
            execution_status="succeeded",
            result_code="succeeded",
            raw_result={"image_id": "img_stored_1", "summary": "A cat"},
        )
        tool_executor = MagicMock()
        tool_executor.execute.return_value = exec_result
        runner.agent = SimpleNamespace(
            tool_executor=tool_executor,
            workspace_root="/tmp",
        )
        runner.task_controller.start_task.return_value = SimpleNamespace(task_id="t1")
        adapter._runner = runner
        result = adapter._ingest_image_record(session_id="s1", message_id="m1", image_key="img_1")
        assert result == {"image_id": "img_stored_1", "summary": "A cat"}

    def test_non_dict_result_returns_none(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        exec_result = SimpleNamespace(
            blocked=False,
            approval_id="",
            execution_status="succeeded",
            result_code="succeeded",
            raw_result="not a dict",
        )
        tool_executor = MagicMock()
        tool_executor.execute.return_value = exec_result
        runner.agent = SimpleNamespace(
            tool_executor=tool_executor,
            workspace_root="/tmp",
        )
        runner.task_controller.start_task.return_value = SimpleNamespace(task_id="t1")
        adapter._runner = runner
        result = adapter._ingest_image_record(session_id="s1", message_id="m1", image_key="img_1")
        assert result is None

    def test_failed_execution_returns_none(self) -> None:
        adapter = _make_adapter()
        runner = _mock_runner()
        exec_result = SimpleNamespace(
            blocked=False,
            approval_id="",
            execution_status="failed",
            result_code="failed",
            raw_result=None,
        )
        tool_executor = MagicMock()
        tool_executor.execute.return_value = exec_result
        runner.agent = SimpleNamespace(
            tool_executor=tool_executor,
            workspace_root="/tmp",
        )
        runner.task_controller.start_task.return_value = SimpleNamespace(task_id="t1")
        adapter._runner = runner
        result = adapter._ingest_image_record(session_id="s1", message_id="m1", image_key="img_1")
        assert result is None


# ---------------------------------------------------------------------------
# _ingest_image_records
# ---------------------------------------------------------------------------


class TestIngestImageRecords:
    def test_aggregates_results(self) -> None:
        adapter = _make_adapter()
        msg = _make_msg(image_keys=["img_a", "img_b", "img_c"])
        with patch.object(
            adapter,
            "_ingest_image_record",
            side_effect=[
                {"image_id": "a"},
                None,
                {"image_id": "c"},
            ],
        ):
            records = adapter._ingest_image_records("s1", msg)
        assert len(records) == 2
        assert records[0]["image_id"] == "a"
        assert records[1]["image_id"] == "c"


# ---------------------------------------------------------------------------
# _build_image_prompt
# ---------------------------------------------------------------------------


class TestBuildImagePromptExtended:
    def test_formats_records(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        msg = _make_msg(image_keys=["img_1", "img_2"])
        records = [
            {"image_id": "img_1", "summary": "Cat photo", "tags": ["animal", "pet"]},
            {"image_id": "img_2", "summary": "", "tags": []},
        ]
        with patch.object(adapter, "_ingest_image_records", return_value=records):
            result = adapter._build_image_prompt("s1", msg)
        assert "img_1" in result
        assert "Cat photo" in result
        assert "img_2" in result


# ---------------------------------------------------------------------------
# _dispatch_message_sync_compat with progress card callbacks
# ---------------------------------------------------------------------------


class TestSyncCompatProgressCallbacks:
    def test_progress_enabled_creates_card(self) -> None:
        adapter = _make_adapter(feishu_thread_progress=True)
        adapter._client = MagicMock()
        runner = _mock_runner()
        runner.dispatch.return_value = SimpleNamespace(
            text="result",
            agent_result=SimpleNamespace(
                blocked=False, suspended=False, task_id="", approval_id=""
            ),
        )
        adapter._runner = runner

        with (
            patch(
                "hermit.plugins.builtin.adapters.feishu.adapter.reply_card_return_id",
                return_value="om_progress_card",
            ),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.build_progress_card"),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.smart_reply"),
            patch.object(adapter, "_present_task_result", return_value=(None, False, "")),
        ):
            adapter._dispatch_message_sync_compat(
                session_id="s1",
                msg=_make_msg(),
                dispatch_text="do something",
                enable_progress_card=True,
            )
            runner.dispatch.assert_called_once()
            call_kwargs = runner.dispatch.call_args[1]
            assert call_kwargs.get("on_tool_start") is not None
            assert call_kwargs.get("on_tool_call") is not None

    def test_on_tool_start_and_on_tool_call_callbacks(self) -> None:
        """Exercise the inner callback closures to cover lines 1569-1615."""
        adapter = _make_adapter(feishu_thread_progress=True)
        adapter._client = MagicMock()
        runner = _mock_runner()

        captured_callbacks: dict[str, Any] = {}

        def fake_dispatch(**kwargs: Any) -> SimpleNamespace:
            captured_callbacks["on_tool_start"] = kwargs.get("on_tool_start")
            captured_callbacks["on_tool_call"] = kwargs.get("on_tool_call")
            # Simulate tool calls by invoking callbacks
            if captured_callbacks["on_tool_start"]:
                captured_callbacks["on_tool_start"]("bash", {"command": "ls"})
                # Call with a schedule reaction tool
                captured_callbacks["on_tool_start"]("hermit_schedule_create", {})
                # Call read_skill with scheduler
                captured_callbacks["on_tool_start"]("read_skill", {"name": "scheduler"})
            if captured_callbacks["on_tool_call"]:
                captured_callbacks["on_tool_call"]("bash", {"command": "ls"}, "output")
            return SimpleNamespace(
                text="done",
                agent_result=SimpleNamespace(
                    blocked=False, suspended=False, task_id="", approval_id=""
                ),
            )

        runner.dispatch.side_effect = fake_dispatch
        adapter._runner = runner

        with (
            patch(
                "hermit.plugins.builtin.adapters.feishu.adapter.reply_card_return_id",
                return_value="om_progress_card",
            ),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.build_progress_card"),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.patch_card"),
            patch(
                "hermit.plugins.builtin.adapters.feishu.adapter.format_tool_start_hint",
                return_value="Running bash...",
            ),
            patch(
                "hermit.plugins.builtin.adapters.feishu.adapter.make_tool_step",
                return_value=ToolStep(
                    name="bash", display="Bash", key_input="ls", summary="", elapsed_ms=0
                ),
            ),
            patch("hermit.plugins.builtin.adapters.feishu.adapter.add_reaction"),
            patch.object(adapter, "_present_task_result", return_value=(None, False, "")),
        ):
            adapter._dispatch_message_sync_compat(
                session_id="s1",
                msg=_make_msg(),
                dispatch_text="do something",
                enable_progress_card=True,
            )
        assert captured_callbacks.get("on_tool_start") is not None
        assert captured_callbacks.get("on_tool_call") is not None


# ---------------------------------------------------------------------------
# stop method
# ---------------------------------------------------------------------------


class TestStopExtended:
    async def test_stop_cancels_timers(self) -> None:
        adapter = _make_adapter()
        adapter._runner = _mock_runner()
        adapter._client = MagicMock()
        sweep_timer = MagicMock()
        topic_timer = MagicMock()
        adapter._sweep_timer = sweep_timer
        adapter._topic_timer = topic_timer
        adapter._ws_client = None
        adapter._ws_loop = None
        adapter._ws_thread = None

        with patch.object(adapter, "_flush_all_sessions"):
            await adapter.stop()

        sweep_timer.cancel.assert_called_once()
        topic_timer.cancel.assert_called_once()
        assert adapter._stopped is True
