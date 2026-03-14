from __future__ import annotations

import json
from pathlib import Path
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
    artifact_uri, artifact_hash = artifacts.store_json({"kind": "context.pack/v3"})
    store.create_artifact(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        kind="context.pack/v3",
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


def test_projection_service_includes_terminal_outcome_summary(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(conversation_id="chat-1", goal="查询北京天气", source_channel="chat", kind="respond")
    controller.finalize_result(
        ctx,
        status="succeeded",
        result_preview="北京今天天气不错：晴到多云，0～12℃。",
        result_text="北京今天天气不错：晴到多云，0～12℃，微风到西南风，无明显降水。",
    )

    payload = ProjectionService(store).rebuild_task(ctx.task_id)

    assert payload["outcome"] is not None
    assert payload["outcome"]["status"] == "completed"
    assert payload["outcome"]["result_text_excerpt"].startswith("北京今天天气不错")
    assert payload["outcome"]["outcome_summary"].startswith("北京今天天气不错")


def test_conversation_projection_strips_internal_tags_from_recent_notes(tmp_path: Path) -> None:
    from hermit.kernel.conversation_projection import ConversationProjectionService

    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    ctx = controller.start_task(conversation_id="oc_1", goal="你好", source_channel="feishu", kind="respond")
    store.append_event(
        event_type="task.note.appended",
        entity_type="task",
        entity_id=ctx.task_id,
        task_id=ctx.task_id,
        actor="user",
        payload={
            "raw_text": (
                "<feishu_msg_id>om_1</feishu_msg_id>\n"
                "<feishu_chat_id>oc_1</feishu_chat_id>\n"
                "加上和 grok 的对比"
            )
        },
    )

    payload = ConversationProjectionService(store).rebuild("oc_1")

    assert payload["recent_notes"] == ["加上和 grok 的对比"]
    assert "<feishu_msg_id>" not in payload["summary"]


def test_conversation_projection_exposes_recent_terminal_continuation_candidates(tmp_path: Path) -> None:
    from hermit.kernel.conversation_projection import ConversationProjectionService

    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    older = controller.start_task(
        conversation_id="oc_weather",
        goal="查询上海天气",
        source_channel="feishu",
        kind="respond",
    )
    controller.finalize_result(
        older,
        status="succeeded",
        result_preview="上海今天多云。",
        result_text="上海今天多云，最高 15℃。",
    )
    latest = controller.start_task(
        conversation_id="oc_weather",
        goal="查询北京天气",
        source_channel="feishu",
        kind="respond",
    )
    controller.finalize_result(
        latest,
        status="succeeded",
        result_preview="北京今天天气不错。",
        result_text="北京今天天气不错：晴到多云，0～12℃。",
    )

    payload = ConversationProjectionService(store).rebuild("oc_weather")

    assert payload["continuation_candidates"][0]["task_id"] == latest.task_id
    assert payload["continuation_candidates"][0]["outcome_summary"].startswith("北京今天天气不错")
    assert payload["continuation_candidates"][1]["task_id"] == older.task_id


def test_conversation_projection_exposes_focus_open_tasks_and_pending_ingresses(tmp_path: Path) -> None:
    from hermit.kernel.conversation_projection import ConversationProjectionService

    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)

    first = controller.start_task(
        conversation_id="oc_focus_projection",
        goal="整理周报",
        source_channel="feishu",
        kind="respond",
    )
    second = controller.enqueue_task(
        conversation_id="oc_focus_projection",
        goal="整理测试计划",
        source_channel="feishu",
        kind="respond",
    )
    store.set_conversation_focus("oc_focus_projection", task_id=first.task_id, reason="manual_test_focus")
    store.create_ingress(
        conversation_id="oc_focus_projection",
        source_channel="feishu",
        raw_text="这个改成 Markdown",
        normalized_text="这个改成 Markdown",
        actor="user",
    )
    decision = controller.decide_ingress(
        conversation_id="oc_focus_projection",
        source_channel="feishu",
        raw_text="补充一点说明",
        prompt="补充一点说明",
    )

    payload = ConversationProjectionService(store).rebuild("oc_focus_projection")

    assert payload["focus_task_id"] == first.task_id
    assert payload["focus_reason"] == "manual_test_focus"
    assert payload["pending_ingress_count"] == 1
    assert len(payload["open_tasks"]) == 2
    assert payload["open_tasks"][0]["task_id"] == second.task_id
    assert any(item["task_id"] == first.task_id and item["is_focus"] for item in payload["open_tasks"])
    assert payload["ingress_metrics"]["total"] >= 2
    assert payload["ingress_metrics"]["resolution_counts"]["append_note"] >= 1
    assert payload["ingress_metrics"]["shadow_disagreement_count"] >= 1
    assert payload["recent_ingresses"][0]["ingress_id"] == decision.ingress_id
    assert payload["recent_ingresses"][0]["shadow_match_actual"] is False


def test_conversation_projection_cache_refreshes_on_focus_and_ingress_updates(tmp_path: Path) -> None:
    from hermit.kernel.conversation_projection import ConversationProjectionService

    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    first = controller.start_task(conversation_id="chat-cache", goal="Inspect first", source_channel="chat", kind="respond")
    second = controller.start_task(conversation_id="chat-cache", goal="Inspect second", source_channel="chat", kind="respond")
    service = ConversationProjectionService(store, ArtifactStore(tmp_path / "artifacts"))

    initial = service.ensure("chat-cache")
    assert initial["focus_task_id"] == second.task_id

    store.set_conversation_focus("chat-cache", task_id=first.task_id, reason="explicit_task_switch")
    ingress = store.create_ingress(
        conversation_id="chat-cache",
        source_channel="chat",
        actor="user",
        raw_text="这个到底指哪一个",
        normalized_text="这个到底指哪一个",
    )
    store.update_ingress(
        ingress.ingress_id,
        status="pending_disambiguation",
        resolution="pending_disambiguation",
        rationale={"reason_codes": ["ambiguous_close_tie"]},
    )

    refreshed = service.ensure("chat-cache")

    assert refreshed["focus_task_id"] == first.task_id
    assert refreshed["focus_reason"] == "explicit_task_switch"
    assert refreshed["pending_ingress_count"] == 1
    assert refreshed["ingress_metrics"]["resolution_counts"]["pending_disambiguation"] >= 1


def test_projection_service_key_input_prefers_first_value() -> None:
    assert ProjectionService._key_input({}) == ""
    assert ProjectionService._key_input({"query": "北京天气", "limit": 3}) == "北京天气"
