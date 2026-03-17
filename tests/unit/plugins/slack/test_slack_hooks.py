"""Tests for Slack plugin hooks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from hermit.plugins.builtin.adapters.slack.hooks import (
    _on_dispatch_result,
    _on_post_run,
    register,
)
from hermit.runtime.capability.contracts.base import HookEvent


def test_dispatch_result_no_channel_id():
    result = _on_dispatch_result(source="test", title="t", result_text="r")
    assert result is None


def test_dispatch_result_empty_notify():
    result = _on_dispatch_result(source="test", notify={})
    assert result is None


@patch("hermit.plugins.builtin.adapters.slack.hooks.get_active_adapter")
def test_dispatch_result_adapter_not_active(mock_get):
    mock_get.return_value = None
    result = _on_dispatch_result(
        source="test", title="Job", result_text="done", notify={"slack_channel_id": "C123"}
    )
    assert result is not None
    assert result["status"] == "failure"
    assert result["error"] == "adapter_not_active"


@patch("hermit.plugins.builtin.adapters.slack.hooks.get_active_adapter")
def test_dispatch_result_adapter_no_app(mock_get):
    adapter = MagicMock()
    adapter.app = None
    mock_get.return_value = adapter
    result = _on_dispatch_result(
        source="test", title="Job", result_text="done", notify={"slack_channel_id": "C123"}
    )
    assert result is not None
    assert result["status"] == "failure"
    assert result["error"] == "adapter_not_active"


@patch("hermit.plugins.builtin.adapters.slack.reply.send_message", new_callable=AsyncMock)
@patch("hermit.plugins.builtin.adapters.slack.hooks.get_active_adapter")
def test_dispatch_result_success(mock_get, mock_send):
    adapter = MagicMock()
    adapter.app.client = MagicMock()
    mock_get.return_value = adapter

    result = _on_dispatch_result(
        source="scheduler",
        title="Daily Report",
        result_text="All good",
        success=True,
        notify={"slack_channel_id": "C456"},
        metadata={"job_id": "j1"},
    )
    assert result is not None
    assert result["status"] == "success"
    assert result["target"] == "C456"
    assert result["error"] is None


@patch("hermit.plugins.builtin.adapters.slack.reply.send_message", new_callable=AsyncMock)
@patch("hermit.plugins.builtin.adapters.slack.hooks.get_active_adapter")
def test_dispatch_result_failure_message(mock_get, mock_send):
    adapter = MagicMock()
    adapter.app.client = MagicMock()
    mock_get.return_value = adapter

    result = _on_dispatch_result(
        source="scheduler",
        title="Task",
        result_text="partial output",
        success=False,
        error="timeout",
        notify={"slack_channel_id": "C789"},
    )
    assert result is not None
    assert result["status"] == "success"


@patch("hermit.plugins.builtin.adapters.slack.hooks.get_active_adapter")
def test_dispatch_result_exception(mock_get):
    mock_get.side_effect = RuntimeError("boom")
    result = _on_dispatch_result(source="test", notify={"slack_channel_id": "C123"})
    assert result is not None
    assert result["status"] == "failure"
    assert result["error"] == "exception"


@patch("hermit.plugins.builtin.adapters.slack.hooks.get_active_adapter")
def test_on_post_run_no_adapter(mock_get):
    mock_get.return_value = None
    _on_post_run(result=None, session_id="s1", runner=None)


@patch("hermit.plugins.builtin.adapters.slack.hooks.get_active_adapter")
def test_on_post_run_with_adapter(mock_get):
    mock_get.return_value = MagicMock()
    _on_post_run(result=MagicMock(), session_id="s1", runner=MagicMock())


def test_register_hooks():
    ctx = MagicMock()
    register(ctx)
    assert ctx.add_hook.call_count == 2
    calls = ctx.add_hook.call_args_list
    events = [c[0][0] for c in calls]
    assert HookEvent.POST_RUN in events
    assert HookEvent.DISPATCH_RESULT in events
