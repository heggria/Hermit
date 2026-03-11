"""Tests for DISPATCH_RESULT event — payload contract and feishu routing."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from hermit.plugin.base import HookEvent
from hermit.plugin.hooks import HooksEngine

# ---------------------------------------------------------------------------
# Payload contract
# ---------------------------------------------------------------------------

class TestDispatchResultEvent:
    def test_event_exists(self) -> None:
        assert hasattr(HookEvent, "DISPATCH_RESULT")
        assert HookEvent.DISPATCH_RESULT.value == "dispatch_result"

    def test_schedule_result_removed(self) -> None:
        assert not hasattr(HookEvent, "SCHEDULE_RESULT")

    def test_handler_receives_all_fields(self) -> None:
        hooks = HooksEngine()
        received: list[dict[str, Any]] = []

        def handler(
            *,
            source: str,
            title: str,
            result_text: str,
            success: bool,
            error: str | None,
            notify: dict,
            metadata: dict,
            **kw: Any,
        ) -> None:
            received.append(locals())

        hooks.register(str(HookEvent.DISPATCH_RESULT), handler)
        hooks.fire(
            HookEvent.DISPATCH_RESULT,
            source="scheduler",
            title="Morning Report",
            result_text="All good.",
            success=True,
            error=None,
            notify={"feishu_chat_id": "oc_abc"},
            metadata={"job_id": "j1"},
        )

        assert len(received) == 1
        ev = received[0]
        assert ev["source"] == "scheduler"
        assert ev["title"] == "Morning Report"
        assert ev["result_text"] == "All good."
        assert ev["success"] is True
        assert ev["error"] is None
        assert ev["notify"] == {"feishu_chat_id": "oc_abc"}
        assert ev["metadata"] == {"job_id": "j1"}

    def test_multiple_consumers_decouple(self) -> None:
        """Two independent consumers both receive the same event."""
        hooks = HooksEngine()
        feishu_calls: list[str] = []
        slack_calls: list[str] = []

        hooks.register(
            str(HookEvent.DISPATCH_RESULT),
            lambda *, notify, **kw: feishu_calls.append(notify.get("feishu_chat_id", "")),
        )
        hooks.register(
            str(HookEvent.DISPATCH_RESULT),
            lambda *, notify, **kw: slack_calls.append(notify.get("slack_channel", "")),
        )

        hooks.fire(
            HookEvent.DISPATCH_RESULT,
            source="webhook/github",
            title="PR #42",
            result_text="LGTM",
            success=True,
            error=None,
            notify={"feishu_chat_id": "oc_xyz", "slack_channel": "#eng"},
            metadata={},
        )

        assert feishu_calls == ["oc_xyz"]
        assert slack_calls == ["#eng"]


# ---------------------------------------------------------------------------
# Feishu handler routing
# ---------------------------------------------------------------------------

class TestFeishuDispatchResultHandler:
    def _make_handler(self):
        from hermit.builtin.feishu.hooks import _on_dispatch_result
        return _on_dispatch_result

    def test_skips_when_no_feishu_chat_id(self) -> None:
        handler = self._make_handler()
        # Should not raise — just return silently
        handler(
            source="scheduler",
            title="Test",
            result_text="hello",
            success=True,
            error=None,
            notify={},
        )

    def test_sends_to_feishu_chat_id(self) -> None:
        handler = self._make_handler()

        with (
            patch("hermit.builtin.feishu.hooks.build_lark_client") as mock_client,
            patch("hermit.builtin.feishu.reply._should_use_card", return_value=False),
            patch("hermit.builtin.feishu.reply.send_text_message") as mock_send,
        ):
            mock_client.return_value = MagicMock()
            handler(
                source="scheduler",
                title="Daily",
                result_text="Done.",
                success=True,
                error=None,
                notify={"feishu_chat_id": "oc_test"},
            )
            mock_send.assert_called_once()
            _, chat_id, _ = mock_send.call_args[0]
            assert chat_id == "oc_test"

    def test_failed_result_includes_error_in_text(self) -> None:
        handler = self._make_handler()
        sent_texts: list[str] = []

        def capture_send(client, chat_id, text):
            sent_texts.append(text)

        with (
            patch("hermit.builtin.feishu.hooks.build_lark_client") as mock_client,
            patch("hermit.builtin.feishu.reply._should_use_card", return_value=False),
            patch("hermit.builtin.feishu.reply.send_text_message", side_effect=capture_send),
        ):
            mock_client.return_value = MagicMock()
            handler(
                source="scheduler",
                title="Nightly",
                result_text="partial output",
                success=False,
                error="timeout after 60s",
                notify={"feishu_chat_id": "oc_test"},
            )

        assert sent_texts
        assert "failed" in sent_texts[0]
        assert "timeout after 60s" in sent_texts[0]
