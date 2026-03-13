from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.controller import TaskController
from hermit.kernel.projections import ProjectionService
from hermit.kernel.store import KernelStore
from hermit.kernel.topics import _append_item, _clean_topic_text, build_task_topic


def test_clean_topic_text_strips_metadata_and_blank_lines() -> None:
    raw = """
    <session_time>2026-03-13</session_time>
    <feishu_msg_id>om_1</feishu_msg_id>

    第一行

    第二行
    """

    assert _clean_topic_text(raw) == "第一行\n    第二行"
    assert _clean_topic_text(None) == ""


def test_append_item_deduplicates_adjacent_signatures() -> None:
    items: list[dict[str, Any]] = []

    _append_item(items, {"kind": "tool.progressed", "text": "正在执行", "phase": "running", "progress_percent": 20})
    _append_item(items, {"kind": "tool.progressed", "text": "正在执行", "phase": "running", "progress_percent": 20})
    _append_item(items, {"kind": "tool.progressed", "text": "已完成", "phase": "done", "progress_percent": 100})

    assert items == [
        {"kind": "tool.progressed", "text": "正在执行", "phase": "running", "progress_percent": 20},
        {"kind": "tool.progressed", "text": "已完成", "phase": "done", "progress_percent": 100},
    ]


def test_build_task_topic_covers_approval_denial_and_cancelled_state() -> None:
    topic = build_task_topic(
        [
            {"event_seq": 1, "event_type": "task.created", "payload": {"goal": "先看看日志"}},
            {"event_seq": 2, "event_type": "approval.requested", "payload": {}},
            {"event_seq": 3, "event_type": "approval.denied", "payload": {}},
            {"event_seq": 4, "event_type": "tool.status.changed", "payload": {"status": "paused", "topic_summary": "等待进一步指令"}},
            {"event_seq": 5, "event_type": "task.note.appended", "payload": {"raw_text": "改成只检查最近一天日志"}},
            {"event_seq": 6, "event_type": "task.cancelled", "payload": {}},
        ],
        initial={"current_hint": "Task is running.", "items": [{"kind": "seed", "text": "seed"}]},
    )

    assert topic["status"] == "cancelled"
    assert topic["current_phase"] == "paused"
    assert topic["current_hint"] == "等待进一步指令"
    assert topic["items"][0]["kind"] == "seed"
    assert topic["items"][1]["kind"] == "task.started"
    assert topic["items"][2]["kind"] == "approval.requested"
    assert topic["items"][3]["kind"] == "approval.resolved"
    assert topic["items"][4]["kind"] == "tool.status.changed"
    assert topic["items"][5]["kind"] == "user.note.appended"
    assert topic["items"][-1]["text"] == "Task cancelled."


def test_build_task_topic_keeps_only_last_20_items() -> None:
    events = [
        {"event_seq": index, "event_type": "task.note.appended", "payload": {"raw_text": f"note-{index}"}}
        for index in range(1, 26)
    ]

    topic = build_task_topic(events)

    assert len(topic["items"]) == 20
    assert topic["items"][0]["text"] == "note-6"
    assert topic["items"][-1]["text"] == "note-25"


def test_projection_service_verify_ensure_and_rebuild_all(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    first = controller.start_task(conversation_id="chat-1", goal="A", source_channel="chat", kind="respond")
    second = controller.start_task(conversation_id="chat-2", goal="B", source_channel="chat", kind="respond")
    service = ProjectionService(store)

    missing = service.verify_projection(first.task_id)
    rebuilt = service.rebuild_all()
    valid = service.verify_projection(first.task_id)
    ensured = service.ensure_task_projection(first.task_id)

    store.upsert_projection_cache(
        second.task_id,
        schema_version="old",
        event_head_hash="stale",
        payload={"task": {"task_id": second.task_id}},
    )
    stale = service.verify_projection(second.task_id)

    assert missing["valid"] is False and missing["reason"] == "missing"
    assert rebuilt["count"] == 2
    assert set(rebuilt["rebuilt_tasks"]) == {first.task_id, second.task_id}
    assert valid["valid"] is True and valid["reason"] == "ok"
    assert ensured["task"]["task_id"] == first.task_id
    assert stale["valid"] is False and stale["reason"] == "stale"


def test_projection_service_rebuild_task_raises_for_missing_task(tmp_path: Path) -> None:
    service = ProjectionService(KernelStore(tmp_path / "kernel" / "state.db"))

    with pytest.raises(KeyError):
        service.rebuild_task("task-missing")


def test_projection_service_tool_input_and_history_handle_missing_invalid_and_valid_artifacts(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(conversation_id="chat-1", goal="A", source_channel="chat", kind="respond")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    service = ProjectionService(store)

    missing = service._tool_input_from_event(None)

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{bad", encoding="utf-8")
    invalid_artifact = store.create_artifact(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        kind="action_request",
        uri=str(invalid_path),
        content_hash="hash-invalid",
        producer="test",
        metadata={},
    )
    invalid = service._tool_input_from_event(invalid_artifact.artifact_id)

    uri, content_hash = artifacts.store_json({"tool_input": {"query": "北京天气", "limit": 3}})
    valid_artifact = store.create_artifact(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        kind="action_request",
        uri=uri,
        content_hash=content_hash,
        producer="test",
        metadata={},
    )
    store.append_event(
        event_type="action.requested",
        entity_type="step_attempt",
        entity_id=ctx.step_attempt_id,
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        actor="kernel",
        payload={"tool_name": "grok_search", "artifact_ref": valid_artifact.artifact_id},
    )
    store.append_event(
        event_type="action.requested",
        entity_type="step_attempt",
        entity_id=ctx.step_attempt_id,
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        actor="kernel",
        payload={"tool_name": "", "artifact_ref": valid_artifact.artifact_id},
    )

    valid = service._tool_input_from_event(valid_artifact.artifact_id)
    history = service._tool_history_from_events(store.list_events(task_id=ctx.task_id, limit=20))

    assert missing == {}
    assert invalid == {}
    assert valid == {"query": "北京天气", "limit": 3}
    assert history == [
        {
            "event_seq": history[0]["event_seq"],
            "tool_name": "grok_search",
            "tool_input": {"limit": 3, "query": "北京天气"},
            "key_input": json.dumps(3, ensure_ascii=False),
            "occurred_at": history[0]["occurred_at"],
        }
    ]


def test_projection_service_ensure_uses_cached_payload_when_valid(monkeypatch, tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    service = ProjectionService(store)
    cached_payload = {"task": {"task_id": "task-1"}}

    monkeypatch.setattr(service, "verify_projection", lambda task_id: {"valid": True})
    monkeypatch.setattr(store, "get_projection_cache", lambda task_id: {"payload": cached_payload})
    monkeypatch.setattr(service, "rebuild_task", lambda task_id: (_ for _ in ()).throw(AssertionError("should not rebuild")))

    assert service.ensure_task_projection("task-1") == cached_payload


def test_projection_service_context_pack_and_rollbacks_are_included(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(conversation_id="chat-1", goal="A", source_channel="chat", kind="respond")
    artifact_uri, artifact_hash = artifacts.store_json({"kind": "context.pack/v1"})
    store.create_artifact(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        kind="context.pack/v1",
        uri=artifact_uri,
        content_hash=artifact_hash,
        producer="test",
        metadata={},
    )
    receipt = store.create_receipt(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        action_type="write_file",
        input_refs=[],
        environment_ref=None,
        policy_result={},
        approval_ref=None,
        output_refs=[],
        result_summary="ok",
    )
    store.create_rollback(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        receipt_ref=receipt.receipt_id,
        action_type="write_file",
        strategy="restore_file",
    )

    payload = ProjectionService(store).rebuild_task(ctx.task_id)

    assert payload["latest_context_pack_ref"] is not None
    assert payload["rollbacks"][0]["receipt_ref"] == receipt.receipt_id


def test_projection_service_key_input_prefers_first_value() -> None:
    assert ProjectionService._key_input({}) == ""
    assert ProjectionService._key_input({"query": "北京天气", "limit": 3}) == "北京天气"
