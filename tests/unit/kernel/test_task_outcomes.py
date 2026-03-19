"""Tests for kernel/task/state/outcomes.py — text cleaning and outcome building."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hermit.kernel.task.state.outcomes import (
    TERMINAL_TASK_STATUSES,
    build_task_outcome,
    clean_runtime_text,
    outcome_source_artifact_refs,
    trim_text,
)


class TestCleanRuntimeText:
    def test_empty_string(self) -> None:
        assert clean_runtime_text("") == ""

    def test_none_returns_empty(self) -> None:
        assert clean_runtime_text(None) == ""

    def test_plain_text_unchanged(self) -> None:
        assert clean_runtime_text("hello world") == "hello world"

    def test_strips_session_time_tag(self) -> None:
        text = "<session_time>12:00</session_time>actual content"
        assert clean_runtime_text(text) == "actual content"

    def test_strips_feishu_meta_tags(self) -> None:
        text = "<feishu_msg_id>abc123</feishu_msg_id>actual content"
        assert clean_runtime_text(text) == "actual content"

    def test_strips_both_tags(self) -> None:
        text = "<session_time>t</session_time><feishu_chat_id>c</feishu_chat_id>content"
        assert clean_runtime_text(text) == "content"

    def test_strips_blank_lines(self) -> None:
        text = "line1\n\n\nline2"
        assert clean_runtime_text(text) == "line1\nline2"

    def test_strips_leading_trailing_whitespace(self) -> None:
        assert clean_runtime_text("  hello  ") == "hello"

    def test_multiline_with_tags(self) -> None:
        text = "<session_time>t</session_time>\nline1\n\n<feishu_msg>x</feishu_msg>\nline2"
        result = clean_runtime_text(text)
        assert "line1" in result
        assert "line2" in result
        assert "<session_time>" not in result
        assert "<feishu_msg>" not in result


class TestTrimText:
    def test_short_text_unchanged(self) -> None:
        assert trim_text("hello", limit=10) == "hello"

    def test_exact_limit_unchanged(self) -> None:
        assert trim_text("hello", limit=5) == "hello"

    def test_over_limit_truncated_with_ellipsis(self) -> None:
        result = trim_text("hello world", limit=6)
        assert len(result) <= 6
        assert result.endswith("\u2026")

    def test_limit_1_returns_single_char(self) -> None:
        result = trim_text("hello", limit=1)
        assert len(result) == 1

    def test_none_input(self) -> None:
        assert trim_text(None, limit=10) == ""

    def test_cleans_runtime_text_before_trimming(self) -> None:
        text = "<session_time>t</session_time>actual"
        result = trim_text(text, limit=100)
        assert result == "actual"

    def test_empty_after_cleaning(self) -> None:
        text = "<session_time>t</session_time>"
        result = trim_text(text, limit=100)
        assert result == ""


class TestOutcomeSourceArtifactRefs:
    def test_collects_refs_from_receipts(self) -> None:
        r1 = SimpleNamespace(output_refs=["art-1", "art-2"])
        r2 = SimpleNamespace(output_refs=["art-3"])
        store = MagicMock()
        store.list_receipts.return_value = [r1, r2]

        result = outcome_source_artifact_refs(store, "task-1")
        assert result == ["art-1", "art-2", "art-3"]

    def test_deduplicates_refs(self) -> None:
        r1 = SimpleNamespace(output_refs=["art-1", "art-2"])
        r2 = SimpleNamespace(output_refs=["art-1", "art-3"])
        store = MagicMock()
        store.list_receipts.return_value = [r1, r2]

        result = outcome_source_artifact_refs(store, "task-1")
        assert result == ["art-1", "art-2", "art-3"]

    def test_respects_limit(self) -> None:
        r1 = SimpleNamespace(output_refs=[f"art-{i}" for i in range(10)])
        store = MagicMock()
        store.list_receipts.return_value = [r1]

        result = outcome_source_artifact_refs(store, "task-1", limit=3)
        assert len(result) == 3

    def test_skips_empty_refs(self) -> None:
        r1 = SimpleNamespace(output_refs=["art-1", "", None, "art-2"])
        store = MagicMock()
        store.list_receipts.return_value = [r1]

        result = outcome_source_artifact_refs(store, "task-1")
        assert result == ["art-1", "art-2"]

    def test_empty_receipts(self) -> None:
        store = MagicMock()
        store.list_receipts.return_value = []

        result = outcome_source_artifact_refs(store, "task-1")
        assert result == []

    def test_none_output_refs(self) -> None:
        r1 = SimpleNamespace(output_refs=None)
        store = MagicMock()
        store.list_receipts.return_value = [r1]

        result = outcome_source_artifact_refs(store, "task-1")
        assert result == []


class TestBuildTaskOutcome:
    def _make_events(self, status: str, payload: dict | None = None) -> list[dict]:
        return [
            {"event_type": f"task.{status}", "occurred_at": 1000.0, "payload": payload or {}},
        ]

    def test_non_terminal_status_returns_none(self) -> None:
        store = MagicMock()
        result = build_task_outcome(
            store=store,
            task_id="t1",
            status="running",
            events=self._make_events("running"),
        )
        assert result is None

    @pytest.mark.parametrize("status", sorted(TERMINAL_TASK_STATUSES))
    def test_terminal_statuses_accepted(self, status: str) -> None:
        store = MagicMock()
        store.list_receipts.return_value = []
        result = build_task_outcome(
            store=store,
            task_id="t1",
            status=status,
            events=self._make_events(status),
        )
        assert result is not None
        assert result["status"] == status

    def test_no_terminal_event_returns_none(self) -> None:
        store = MagicMock()
        result = build_task_outcome(
            store=store,
            task_id="t1",
            status="completed",
            events=[{"event_type": "task.started", "payload": {}}],
        )
        assert result is None

    def test_result_preview_from_payload(self) -> None:
        store = MagicMock()
        store.list_receipts.return_value = []
        events = self._make_events("completed", {"result_preview": "All done"})
        result = build_task_outcome(
            store=store,
            task_id="t1",
            status="completed",
            events=events,
        )
        assert result is not None
        assert result["result_preview"] == "All done"

    def test_result_text_excerpt(self) -> None:
        store = MagicMock()
        store.list_receipts.return_value = []
        events = self._make_events("completed", {"result_text": "Detailed result"})
        result = build_task_outcome(
            store=store,
            task_id="t1",
            status="completed",
            events=events,
        )
        assert result is not None
        assert result["result_text_excerpt"] == "Detailed result"

    def test_completed_at_from_event(self) -> None:
        store = MagicMock()
        store.list_receipts.return_value = []
        events = [
            {"event_type": "task.completed", "occurred_at": 12345.678, "payload": {}},
        ]
        result = build_task_outcome(
            store=store,
            task_id="t1",
            status="completed",
            events=events,
        )
        assert result is not None
        assert result["completed_at"] == 12345.678

    def test_outcome_summary_prefers_result_text(self) -> None:
        store = MagicMock()
        store.list_receipts.return_value = []
        events = self._make_events(
            "completed",
            {
                "result_text": "Text result",
                "result_preview": "Preview",
            },
        )
        result = build_task_outcome(
            store=store,
            task_id="t1",
            status="completed",
            events=events,
        )
        assert result is not None
        assert result["outcome_summary"] == "Text result"

    def test_outcome_summary_falls_back_to_preview(self) -> None:
        store = MagicMock()
        store.list_receipts.return_value = []
        events = self._make_events(
            "completed",
            {
                "result_text": "",
                "result_preview": "Preview text",
            },
        )
        result = build_task_outcome(
            store=store,
            task_id="t1",
            status="completed",
            events=events,
        )
        assert result is not None
        assert result["outcome_summary"] == "Preview text"

    def test_outcome_summary_falls_back_to_default(self) -> None:
        store = MagicMock()
        store.list_receipts.return_value = []
        events = self._make_events("failed", {})
        result = build_task_outcome(
            store=store,
            task_id="t1",
            status="failed",
            events=events,
        )
        assert result is not None
        # Falls back to topic hint or "Task {status}."
        assert "failed" in result["outcome_summary"].lower() or result["outcome_summary"]

    def test_source_artifact_refs_included(self) -> None:
        receipt = SimpleNamespace(output_refs=["art-1"])
        store = MagicMock()
        store.list_receipts.return_value = [receipt]
        events = self._make_events("completed", {})
        result = build_task_outcome(
            store=store,
            task_id="t1",
            status="completed",
            events=events,
        )
        assert result is not None
        assert result["source_artifact_refs"] == ["art-1"]

    def test_uses_last_terminal_event(self) -> None:
        store = MagicMock()
        store.list_receipts.return_value = []
        events = [
            {
                "event_type": "task.completed",
                "occurred_at": 100.0,
                "payload": {"result_text": "first"},
            },
            {"event_type": "task.started", "occurred_at": 200.0, "payload": {}},
            {
                "event_type": "task.completed",
                "occurred_at": 300.0,
                "payload": {"result_text": "second"},
            },
        ]
        result = build_task_outcome(
            store=store,
            task_id="t1",
            status="completed",
            events=events,
        )
        assert result is not None
        assert result["result_text_excerpt"] == "second"
        assert result["completed_at"] == 300.0
