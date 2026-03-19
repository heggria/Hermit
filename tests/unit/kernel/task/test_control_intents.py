"""Tests for parse_control_intent — target 80%+ coverage on control_intents.py."""

from __future__ import annotations

import re

from hermit.kernel.task.state.control_intents import (
    ControlIntent,
    _all_locale_keywords,
    _cached_re,
    _cached_set,
    parse_control_intent,
)

# ── ControlIntent dataclass ──────────────────────────────────────


def test_control_intent_defaults() -> None:
    intent = ControlIntent(action="test")
    assert intent.action == "test"
    assert intent.target_id == ""
    assert intent.reason == ""


def test_control_intent_with_fields() -> None:
    intent = ControlIntent(action="approve_once", target_id="abc", reason="user")
    assert intent.action == "approve_once"
    assert intent.target_id == "abc"
    assert intent.reason == "user"


# ── Empty / whitespace input ────────────────────────────────────


def test_empty_input() -> None:
    assert parse_control_intent("") is None


def test_whitespace_input() -> None:
    assert parse_control_intent("   ") is None


# ── Approve commands ─────────────────────────────────────────────


def test_approve_via_task_command() -> None:
    result = parse_control_intent("/task approve abc123")
    assert result is not None
    assert result.action == "approve_once"
    assert result.target_id == "abc123"


def test_approve_keyword() -> None:
    result = parse_control_intent("approve abc123")
    assert result is not None
    assert result.action == "approve_once"
    assert result.target_id == "abc123"


def test_approve_once_keyword() -> None:
    result = parse_control_intent("approve_once abc123")
    assert result is not None
    assert result.action == "approve_once"
    assert result.target_id == "abc123"


def test_approve_mutable_workspace() -> None:
    result = parse_control_intent("approve_mutable_workspace abc123")
    assert result is not None
    assert result.action == "approve_mutable_workspace"
    assert result.target_id == "abc123"


def test_approve_mutable_workspace_hyphen() -> None:
    result = parse_control_intent("approve-mutable-workspace abc123")
    assert result is not None
    assert result.action == "approve_mutable_workspace"
    assert result.target_id == "abc123"


# ── Deny commands ────────────────────────────────────────────────


def test_deny_via_task_command() -> None:
    result = parse_control_intent("/task deny abc123")
    assert result is not None
    assert result.action == "deny"
    assert result.target_id == "abc123"


def test_deny_with_reason() -> None:
    result = parse_control_intent("/task deny abc123 not safe")
    assert result is not None
    assert result.action == "deny"
    assert result.target_id == "abc123"
    assert result.reason == "not safe"


def test_deny_keyword() -> None:
    result = parse_control_intent("deny abc123")
    assert result is not None
    assert result.action == "deny"
    assert result.target_id == "abc123"


# ── Pending approve (shortcut) ───────────────────────────────────


def test_pending_approve_text() -> None:
    texts = _cached_set("kernel.nlp.control.pending_approve_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first, pending_approval_id="appr_123")
        assert result is not None
        assert result.action == "approve_once"
        assert result.target_id == "appr_123"


def test_pending_approve_no_approval() -> None:
    texts = _cached_set("kernel.nlp.control.pending_approve_texts")
    if texts:
        first = next(iter(texts))
        parse_control_intent(first)
        # Without pending_approval_id, might match another intent or None
        # Just verify no crash


# ── Navigation intents ───────────────────────────────────────────


def test_help_intent() -> None:
    texts = _cached_set("kernel.nlp.control.help_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first)
        assert result is not None
        assert result.action == "show_help"


def test_new_session_intent() -> None:
    texts = _cached_set("kernel.nlp.control.new_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first)
        assert result is not None
        assert result.action == "new_session"


def test_history_intent() -> None:
    texts = _cached_set("kernel.nlp.control.history_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first)
        assert result is not None
        assert result.action == "show_history"


def test_task_list_intent() -> None:
    texts = _cached_set("kernel.nlp.control.task_list_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first)
        assert result is not None
        assert result.action == "task_list"


# ── Task case ────────────────────────────────────────────────────


def test_task_case() -> None:
    result = parse_control_intent("/task case abc123")
    assert result is not None
    assert result.action == "case"
    assert result.target_id == "abc123"


def test_task_case_plain() -> None:
    result = parse_control_intent("task case abc123")
    assert result is not None
    assert result.action == "case"
    assert result.target_id == "abc123"


def test_task_case_latest() -> None:
    texts = _cached_set("kernel.nlp.control.case_latest_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first, latest_task_id="task_latest")
        assert result is not None
        assert result.action == "case"
        assert result.target_id == "task_latest"


# ── Task events ──────────────────────────────────────────────────


def test_task_events() -> None:
    result = parse_control_intent("/task events abc123")
    assert result is not None
    assert result.action == "task_events"
    assert result.target_id == "abc123"


def test_task_events_latest() -> None:
    texts = _cached_set("kernel.nlp.control.events_latest_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first, latest_task_id="task_latest")
        assert result is not None
        assert result.action == "task_events"
        assert result.target_id == "task_latest"


# ── Task receipts ────────────────────────────────────────────────


def test_task_receipts() -> None:
    result = parse_control_intent("/task receipts abc123")
    assert result is not None
    assert result.action == "task_receipts"
    assert result.target_id == "abc123"


def test_task_receipts_latest() -> None:
    texts = _cached_set("kernel.nlp.control.receipts_latest_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first, latest_task_id="task_latest")
        assert result is not None
        assert result.action == "task_receipts"
        assert result.target_id == "task_latest"


# ── Task proof ───────────────────────────────────────────────────


def test_task_proof() -> None:
    result = parse_control_intent("/task proof abc123")
    assert result is not None
    assert result.action == "task_proof"
    assert result.target_id == "abc123"


def test_task_proof_latest() -> None:
    texts = _cached_set("kernel.nlp.control.proof_latest_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first, latest_task_id="task_latest")
        assert result is not None
        assert result.action == "task_proof"
        assert result.target_id == "task_latest"


def test_task_proof_export() -> None:
    result = parse_control_intent("/task proof-export abc123")
    assert result is not None
    assert result.action == "task_proof_export"
    assert result.target_id == "abc123"


def test_task_proof_export_latest() -> None:
    texts = _cached_set("kernel.nlp.control.proof_export_latest_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first, latest_task_id="task_latest")
        assert result is not None
        assert result.action == "task_proof_export"
        assert result.target_id == "task_latest"


# ── Plan intents ─────────────────────────────────────────────────


def test_plan_enter() -> None:
    texts = _cached_set("kernel.nlp.control.plan_enter_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first, latest_task_id="task_latest")
        assert result is not None
        assert result.action == "plan_enter"


def test_plan_confirm() -> None:
    texts = _cached_set("kernel.nlp.control.plan_confirm_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first, latest_task_id="task_latest")
        assert result is not None
        assert result.action == "plan_confirm"
        assert result.target_id == "task_latest"


def test_plan_exit() -> None:
    texts = _cached_set("kernel.nlp.control.plan_exit_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first, latest_task_id="task_latest")
        assert result is not None
        assert result.action == "plan_exit"
        assert result.target_id == "task_latest"


# ── Rollback ─────────────────────────────────────────────────────


def test_rollback() -> None:
    result = parse_control_intent("/task rollback abc123")
    assert result is not None
    assert result.action == "rollback"
    assert result.target_id == "abc123"


def test_rollback_latest() -> None:
    # Use Chinese texts that always go through the keyword path (not the regex path).
    # English texts like "rollback this" match the regex first, extracting "this" as target.
    result = parse_control_intent("回滚这次操作", latest_receipt_id="receipt_latest")
    assert result is not None
    assert result.action == "rollback"
    assert result.target_id == "receipt_latest"


# ── Capability intents ───────────────────────────────────────────


def test_capability_list() -> None:
    texts = _cached_set("kernel.nlp.control.grant_list_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first)
        assert result is not None
        assert result.action == "capability_list"


# ── Schedule intents ─────────────────────────────────────────────


def test_schedule_list() -> None:
    texts = _cached_set("kernel.nlp.control.schedule_list_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first)
        assert result is not None
        assert result.action == "schedule_list"


# ── Projection rebuild ───────────────────────────────────────────


def test_projection_rebuild() -> None:
    texts = _cached_set("kernel.nlp.control.rebuild_projection_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first, latest_task_id="task_latest")
        assert result is not None
        assert result.action == "projection_rebuild"
        assert result.target_id == "task_latest"


def test_projection_rebuild_all() -> None:
    texts = _cached_set("kernel.nlp.control.rebuild_all_projection_texts")
    if texts:
        first = next(iter(texts))
        result = parse_control_intent(first)
        assert result is not None
        assert result.action == "projection_rebuild_all"


# ── Normal text returns None ─────────────────────────────────────


def test_normal_text_returns_none() -> None:
    assert parse_control_intent("please build me a website") is None
    assert parse_control_intent("hello world") is None
    assert parse_control_intent("fix the bug in module X") is None


# ── Task switch ──────────────────────────────────────────────────


def test_task_switch() -> None:
    kw = _all_locale_keywords("kernel.nlp.control.task_switch_keywords")
    if kw:
        first = kw[0]
        result = parse_control_intent(f"{first} task_abc123")
        assert result is not None
        assert result.action == "focus_task"
        assert result.target_id == "task_abc123"
        assert result.reason == "explicit_task_switch"


# ── Cache helpers ────────────────────────────────────────────────


def test_cached_re_returns_same() -> None:
    def builder():
        return re.compile(r"^test$")

    r1 = _cached_re("__test_key__", builder)
    r2 = _cached_re("__test_key__", builder)
    assert r1 is r2


def test_cached_set_returns_frozenset() -> None:
    result = _cached_set("kernel.nlp.control.help_texts")
    assert isinstance(result, frozenset)


def test_all_locale_keywords_returns_list() -> None:
    result = _all_locale_keywords("kernel.nlp.control.help_texts")
    assert isinstance(result, list)
