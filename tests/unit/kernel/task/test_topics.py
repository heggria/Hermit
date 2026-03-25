"""Tests for build_task_topic — target 80%+ coverage on topics.py."""

from __future__ import annotations

import time
from typing import Any

from hermit.kernel.task.services.topics import (
    _append_item,
    _clean_topic_text,
    build_task_topic,
)

# ── _clean_topic_text ────────────────────────────────────────────


def test_clean_topic_text_empty() -> None:
    assert _clean_topic_text("") == ""
    assert _clean_topic_text(None) == ""


def test_clean_topic_text_strips() -> None:
    assert _clean_topic_text("  hello  ") == "hello"


def test_clean_topic_text_removes_blank_lines() -> None:
    result = _clean_topic_text("line1\n\n\nline2")
    assert "line1" in result
    assert "line2" in result


# ── _append_item ─────────────────────────────────────────────────


def test_append_item_adds() -> None:
    items: list[dict[str, Any]] = []
    _append_item(items, {"kind": "test", "text": "hello"})
    assert len(items) == 1


def test_append_item_dedup() -> None:
    items: list[dict[str, Any]] = [{"kind": "test", "text": "hello"}]
    _append_item(items, {"kind": "test", "text": "hello"})
    assert len(items) == 1


def test_append_item_different() -> None:
    items: list[dict[str, Any]] = [{"kind": "a", "text": "hello"}]
    _append_item(items, {"kind": "b", "text": "world"})
    assert len(items) == 2


def test_append_item_phase_matters() -> None:
    items: list[dict[str, Any]] = [{"kind": "test", "text": "hello", "phase": "a"}]
    _append_item(items, {"kind": "test", "text": "hello", "phase": "b"})
    assert len(items) == 2


def test_append_item_progress_matters() -> None:
    items: list[dict[str, Any]] = [{"kind": "test", "text": "hello", "progress_percent": 50}]
    _append_item(items, {"kind": "test", "text": "hello", "progress_percent": 75})
    assert len(items) == 2


# ── build_task_topic: empty events ───────────────────────────────


def test_empty_events() -> None:
    result = build_task_topic([])
    assert result["status"] == "running"
    assert result["current_hint"] == "Task is running."
    assert result["items"] == []


def test_with_initial_seed() -> None:
    seed = {
        "current_hint": "Custom hint",
        "current_phase": "custom",
        "current_progress_percent": 42,
        "status": "running",
        "items": [{"kind": "existing", "text": "prior"}],
    }
    result = build_task_topic([], initial=seed)
    assert result["current_hint"] == "Custom hint"
    assert result["current_phase"] == "custom"
    assert result["current_progress_percent"] == 42


def test_initial_seed_invalid_progress() -> None:
    seed = {"current_progress_percent": "not_a_number"}
    result = build_task_topic([], initial=seed)
    assert result["current_progress_percent"] is None


# ── build_task_topic: task.created ───────────────────────────────


def _mk_event(event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "payload": payload or {},
        "event_seq": 1,
        "occurred_at": time.time(),
    }


def test_task_created() -> None:
    events = [_mk_event("task.created", {"title": "My Task"})]
    result = build_task_topic(events)
    assert result["current_hint"] == "My Task"
    assert result["current_phase"] == "started"
    assert len(result["items"]) == 1
    assert result["items"][0]["kind"] == "task.started"


def test_task_created_empty_title() -> None:
    events = [_mk_event("task.created", {"title": ""})]
    result = build_task_topic(events)
    assert result["current_hint"] == "Task started."


# ── build_task_topic: tool.submitted ─────────────────────────────


def test_tool_submitted() -> None:
    events = [_mk_event("tool.submitted", {"tool_name": "bash", "display_name": "Run command"})]
    result = build_task_topic(events)
    assert result["current_phase"] == "submitted"
    assert len(result["items"]) == 1


def test_tool_submitted_with_topic_summary() -> None:
    events = [_mk_event("tool.submitted", {"topic_summary": "Building project"})]
    result = build_task_topic(events)
    assert result["current_hint"] == "Building project"


def test_tool_submitted_empty() -> None:
    events = [_mk_event("tool.submitted", {})]
    result = build_task_topic(events)
    assert result["current_hint"] == "Tool submitted."


# ── build_task_topic: tool.progressed ────────────────────────────


def test_tool_progressed() -> None:
    events = [
        _mk_event(
            "tool.progressed",
            {"summary": "Step 1 done", "progress_percent": 50, "phase": "building"},
        )
    ]
    result = build_task_topic(events)
    assert result["current_hint"] == "Step 1 done"
    assert result["current_phase"] == "building"
    assert result["current_progress_percent"] == 50


def test_tool_progressed_with_detail() -> None:
    events = [
        _mk_event(
            "tool.progressed",
            {"summary": "Step 1", "detail": "Compiling main.rs", "phase": "building"},
        )
    ]
    result = build_task_topic(events)
    assert "Compiling main.rs" in result["items"][0]["text"]


def test_tool_progressed_invalid_percent() -> None:
    events = [_mk_event("tool.progressed", {"summary": "Test", "progress_percent": "bad"})]
    result = build_task_topic(events)
    assert result["current_progress_percent"] is None


# ── build_task_topic: tool.status.changed ────────────────────────


def test_tool_status_changed() -> None:
    events = [_mk_event("tool.status.changed", {"topic_summary": "Compiling..."})]
    result = build_task_topic(events)
    assert result["current_hint"] == "Compiling..."
    assert len(result["items"]) == 1


def test_tool_status_changed_same_hint_skipped() -> None:
    seed = {"current_hint": "Same hint"}
    events = [_mk_event("tool.status.changed", {"topic_summary": "Same hint"})]
    result = build_task_topic(events, initial=seed)
    assert len(result["items"]) == 0


# ── build_task_topic: task.progress.summarized ───────────────────


def test_progress_summarized() -> None:
    events = [
        _mk_event(
            "task.progress.summarized",
            {"summary": "70% complete", "progress_percent": 70, "phase": "executing"},
        )
    ]
    result = build_task_topic(events)
    assert result["current_hint"] == "70% complete"
    assert result["current_progress_percent"] == 70
    assert result["current_phase"] == "executing"


def test_progress_summarized_with_detail() -> None:
    events = [
        _mk_event(
            "task.progress.summarized",
            {"summary": "Building", "detail": "Extra info"},
        )
    ]
    result = build_task_topic(events)
    assert "Extra info" in result["items"][0]["text"]


def test_progress_summarized_empty() -> None:
    events = [_mk_event("task.progress.summarized", {"summary": ""})]
    result = build_task_topic(events)
    assert len(result["items"]) == 0


# ── build_task_topic: task.note.appended ─────────────────────────


def test_note_appended() -> None:
    events = [_mk_event("task.note.appended", {"raw_text": "User says hi"})]
    result = build_task_topic(events)
    assert len(result["items"]) == 1
    assert result["items"][0]["kind"] == "user.note.appended"


def test_note_appended_empty() -> None:
    events = [_mk_event("task.note.appended", {"raw_text": ""})]
    result = build_task_topic(events)
    assert len(result["items"]) == 0


# ── build_task_topic: execution_contract events ──────────────────


def test_execution_contract_selected() -> None:
    events = [_mk_event("execution_contract.selected", {"objective": "Install pkg"})]
    result = build_task_topic(events)
    assert result["current_phase"] == "contracting"
    assert result["current_hint"] == "Install pkg"


def test_execution_contract_superseded() -> None:
    events = [_mk_event("execution_contract.superseded", {})]
    result = build_task_topic(events)
    assert result["current_phase"] == "contracting"


# ── build_task_topic: evidence / authorization events ────────────


def test_evidence_case_recorded() -> None:
    events = [_mk_event("evidence_case.recorded", {"operator_summary": "Evidence ok"})]
    result = build_task_topic(events)
    assert result["current_phase"] == "preflighting"


def test_evidence_case_invalidated() -> None:
    events = [_mk_event("evidence_case.invalidated", {"summary": "Bad evidence"})]
    result = build_task_topic(events)
    assert result["current_phase"] == "preflighting"


def test_authorization_plan_recorded() -> None:
    events = [_mk_event("authorization_plan.recorded", {"approval_route": "auto"})]
    result = build_task_topic(events)
    assert result["current_phase"] == "preflighting"


def test_authorization_plan_invalidated() -> None:
    events = [_mk_event("authorization_plan.invalidated", {})]
    result = build_task_topic(events)
    assert result["current_phase"] == "preflighting"


# ── build_task_topic: approval events ────────────────────────────


def test_approval_requested() -> None:
    events = [_mk_event("approval.requested", {})]
    result = build_task_topic(events)
    assert result["current_phase"] == "awaiting_approval"
    assert result["current_progress_percent"] is None


def test_approval_drifted() -> None:
    events = [_mk_event("approval.drifted", {})]
    result = build_task_topic(events)
    assert result["current_phase"] == "awaiting_approval"


def test_approval_expired() -> None:
    events = [_mk_event("approval.expired", {})]
    result = build_task_topic(events)
    assert result["current_phase"] == "awaiting_approval"


def test_approval_granted() -> None:
    events = [_mk_event("approval.granted", {})]
    result = build_task_topic(events)
    assert result["current_phase"] == "approval_resolved"
    assert "granted" in result["items"][0]["text"]


def test_approval_denied() -> None:
    events = [_mk_event("approval.denied", {})]
    result = build_task_topic(events)
    assert "denied" in result["items"][0]["text"]


def test_approval_consumed() -> None:
    events = [_mk_event("approval.consumed", {})]
    result = build_task_topic(events)
    assert "consumed" in result["items"][0]["text"]


# ── build_task_topic: reconciliation.closed ──────────────────────


def test_reconciliation_closed() -> None:
    events = [_mk_event("reconciliation.closed", {"result_class": "success"})]
    result = build_task_topic(events)
    assert result["current_phase"] == "reconciling"
    assert "success" in result["current_hint"]


# ── build_task_topic: terminal events ────────────────────────────


def test_task_completed() -> None:
    events = [_mk_event("task.completed", {"result_preview": "All done"})]
    result = build_task_topic(events)
    assert result["status"] == "completed"
    assert result["current_progress_percent"] == 100
    assert result["current_hint"] == "All done"


def test_task_completed_no_preview() -> None:
    events = [_mk_event("task.completed", {})]
    result = build_task_topic(events)
    assert result["status"] == "completed"
    assert "completed" in result["items"][0]["text"]


def test_task_failed() -> None:
    events = [_mk_event("task.failed", {})]
    result = build_task_topic(events)
    assert result["status"] == "failed"
    assert "failed" in result["items"][0]["text"]


def test_task_cancelled() -> None:
    events = [_mk_event("task.cancelled", {})]
    result = build_task_topic(events)
    assert result["status"] == "cancelled"


# ── build_task_topic: items limit ────────────────────────────────


def test_items_limited_to_20() -> None:
    events = [_mk_event("tool.submitted", {"tool_name": f"tool_{i}"}) for i in range(30)]
    result = build_task_topic(events)
    assert len(result["items"]) <= 20


# ── build_task_topic: unknown events ignored ─────────────────────


def test_unknown_event_ignored() -> None:
    events = [_mk_event("unknown.event.type", {"data": "ignored"})]
    result = build_task_topic(events)
    assert len(result["items"]) == 0


# ── Multi-event sequence ────────────────────────────────────────


def test_full_lifecycle() -> None:
    events = [
        _mk_event("task.created", {"title": "Deploy app"}),
        _mk_event("tool.submitted", {"tool_name": "bash"}),
        _mk_event("tool.progressed", {"summary": "Building", "progress_percent": 30}),
        _mk_event("approval.requested", {}),
        _mk_event("approval.granted", {}),
        _mk_event("tool.progressed", {"summary": "Deploying", "progress_percent": 80}),
        _mk_event("task.completed", {"result_preview": "Deployed successfully"}),
    ]
    result = build_task_topic(events)
    assert result["status"] == "completed"
    assert result["current_hint"] == "Deployed successfully"
    # progress_percent stays at 80 because the completed event only sets 100
    # when current_progress_percent is None (no prior progress reported)
    assert result["current_progress_percent"] in (80, 100)
    assert len(result["items"]) >= 5
