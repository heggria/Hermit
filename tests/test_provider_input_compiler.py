from __future__ import annotations

import json
from pathlib import Path

from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.context import TaskExecutionContext
from hermit.kernel.controller import TaskController
from hermit.kernel.provider_input import ProviderInputCompiler
from hermit.kernel.store import KernelStore


def _make_task(store: KernelStore, conversation_id: str = "chat-1") -> TaskExecutionContext:
    store.ensure_conversation(conversation_id, source_channel="chat")
    task = store.create_task(
        conversation_id=conversation_id,
        title="Inspect payload",
        goal="Inspect payload",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond", status="running")
    attempt = store.create_step_attempt(
        task_id=task.task_id, step_id=step.step_id, status="running", context={}
    )
    return TaskExecutionContext(
        conversation_id=conversation_id,
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        source_channel="chat",
    )


def test_normalize_ingress_artifactizes_code_blocks(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    compiler = ProviderInputCompiler(store, ArtifactStore(tmp_path / "artifacts"))
    ctx = _make_task(store)

    normalized = compiler.normalize_ingress(
        task_context=ctx,
        raw_text="Please inspect\n```py\nprint('hi')\n```",
        final_prompt="Please inspect\n```py\nprint('hi')\n```",
    )

    assert normalized["ingress_artifact_refs"]
    assert "code_block" in normalized["detected_payload_kinds"]
    artifact = store.get_artifact(normalized["ingress_artifact_refs"][0])
    assert artifact is not None
    assert artifact.kind == "ingress.payload/v1"


def test_compile_builds_context_pack_and_projection_refs(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    compiler = ProviderInputCompiler(store, ArtifactStore(tmp_path / "artifacts"))
    ctx = _make_task(store)
    store.set_conversation_focus(ctx.conversation_id, task_id=ctx.task_id, reason="manual_focus")
    ingress = store.create_ingress(
        conversation_id=ctx.conversation_id,
        source_channel="chat",
        raw_text="请按这条回复继续",
        normalized_text="请按这条回复继续",
        actor="user",
        prompt_ref="请按这条回复继续",
        reply_to_ref="om_root",
        quoted_message_ref="om_quote",
    )
    store.update_ingress(
        ingress.ingress_id,
        status="bound",
        resolution="append_note",
        chosen_task_id=ctx.task_id,
        confidence=0.97,
        margin=0.91,
    )
    store.append_event(
        event_type="task.note.appended",
        entity_type="task",
        entity_id=ctx.task_id,
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        actor="user",
        payload={
            "raw_text": "Follow the architecture note.",
            "inline_excerpt": "Follow the architecture note.",
        },
    )

    compiled = compiler.compile(
        task_context=ctx,
        final_prompt="Check the repo and report back.",
        raw_text="Check the repo and report back.",
    )

    assert compiled.source_mode == "compiled"
    assert compiled.context_pack_ref
    assert compiled.session_projection_ref
    assert "<conversation_projection>" in compiled.messages[0]["content"]
    assert "<context_pack>" in compiled.messages[0]["content"]
    assert "focus_summary={" in compiled.messages[0]["content"]
    assert "bound_ingress_deltas=[" in compiled.messages[0]["content"]

    pack_artifact = store.get_artifact(compiled.context_pack_ref)
    projection_artifact = store.get_artifact(compiled.session_projection_ref)
    assert pack_artifact is not None and pack_artifact.kind == "context.pack/v3"
    assert (
        projection_artifact is not None and projection_artifact.kind == "conversation.projection/v2"
    )
    payload = json.loads(Path(pack_artifact.uri).read_text(encoding="utf-8"))
    assert payload["focus_summary"]["task_id"] == ctx.task_id
    assert payload["focus_summary"]["reason"] == "manual_focus"
    assert payload["bound_ingress_deltas"][0]["reply_to_ref"] == "om_root"
    assert payload["bound_ingress_deltas"][0]["quoted_message_ref"] == "om_quote"


def test_compile_carries_forward_terminal_outcome_into_context_pack(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    compiler = ProviderInputCompiler(store, artifacts)
    controller = TaskController(store)

    previous = controller.start_task(
        conversation_id="oc_weather",
        goal="查询北京天气",
        source_channel="feishu",
        kind="respond",
    )
    controller.finalize_result(
        previous,
        status="succeeded",
        result_preview="北京今天天气不错：晴到多云，0～12℃。",
        result_text="北京今天天气不错：晴到多云，0～12℃，微风到西南风，无明显降水。",
    )
    decision = controller.decide_ingress(
        conversation_id="oc_weather",
        source_channel="feishu",
        raw_text="你说一下你刚才查的北京天气是怎么样的",
        prompt="你说一下你刚才查的北京天气是怎么样的",
    )
    assert decision.continuation_anchor is not None

    followup = controller.start_task(
        conversation_id="oc_weather",
        goal="你说一下你刚才查的北京天气是怎么样的",
        source_channel="feishu",
        kind="respond",
        parent_task_id=decision.parent_task_id,
        ingress_metadata={"continuation_anchor": dict(decision.continuation_anchor)},
    )
    followup.ingress_metadata = {}

    compiled = compiler.compile(
        task_context=followup,
        final_prompt="你说一下你刚才查的北京天气是怎么样的",
        raw_text="你说一下你刚才查的北京天气是怎么样的",
    )

    assert "carry_forward={" in compiled.messages[0]["content"]
    assert "<continuation_guidance>" in compiled.messages[0]["content"]
    assert "北京今天天气不错" in compiled.messages[0]["content"]

    pack_artifact = store.get_artifact(compiled.context_pack_ref or "")
    assert pack_artifact is not None
    payload = json.loads(Path(pack_artifact.uri).read_text(encoding="utf-8"))
    assert payload["carry_forward"]["anchor_task_id"] == previous.task_id
    assert payload["carry_forward"]["anchor_goal"] == "查询北京天气"
    assert payload["carry_forward"]["anchor_user_request"] == "查询北京天气"
    assert payload["carry_forward"]["outcome_summary"].startswith("北京今天天气不错")
    assert payload["continuation_guidance"]["mode"] == "anchor_correction"
    assert payload["working_state"]["recent_results"] == []
    projection = store.build_task_projection(followup.task_id)
    assert projection["task"]["continuation_anchor"]["anchor_task_id"] == previous.task_id


def test_compile_adds_anchor_correction_guidance_for_short_corrective_request(
    tmp_path: Path,
) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    compiler = ProviderInputCompiler(store, artifacts)
    controller = TaskController(store)

    previous = controller.start_task(
        conversation_id="oc_anchor_fix",
        goal="你目前能给自己接入 chorme-devtools mcp 吗",
        source_channel="feishu",
        kind="respond",
    )
    controller.finalize_result(
        previous,
        status="succeeded",
        result_preview="可以接，但目前还没接上。",
        result_text="可以接，但目前还没接上，需要把 chrome-devtools MCP 配进当前环境。",
    )
    followup = controller.start_task(
        conversation_id="oc_anchor_fix",
        goal="我的意思是你改你自己",
        source_channel="feishu",
        kind="respond",
        ingress_metadata={
            "continuation_anchor": controller._continuation_anchor(
                previous.task_id, selection_reason="test"
            )
        },
    )
    followup.ingress_metadata = {}

    compiled = compiler.compile(
        task_context=followup,
        final_prompt="我的意思是你改你自己",
        raw_text="我的意思是你改你自己",
    )

    assert (
        "Prefer interpreting it as a clarification or correction of the anchor task"
        in compiled.messages[0]["content"]
    )
    pack_artifact = store.get_artifact(compiled.context_pack_ref or "")
    assert pack_artifact is not None
    payload = json.loads(Path(pack_artifact.uri).read_text(encoding="utf-8"))
    assert payload["continuation_guidance"]["mode"] == "anchor_correction"
    assert payload["continuation_guidance"]["is_corrective_request"] is True


def test_compile_allows_explicit_and_strong_topic_shift_with_anchor(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    compiler = ProviderInputCompiler(store, artifacts)
    controller = TaskController(store)

    previous = controller.start_task(
        conversation_id="oc_anchor_shift",
        goal="整理 MCP 接入方案",
        source_channel="feishu",
        kind="respond",
    )
    controller.finalize_result(
        previous,
        status="succeeded",
        result_preview="MCP 接入方案已整理。",
        result_text="MCP 接入方案已整理，包含 Cursor 和本地环境配置。",
    )

    explicit_followup = controller.start_task(
        conversation_id="oc_anchor_shift",
        goal="换个话题，帮我查北京天气",
        source_channel="feishu",
        kind="respond",
        ingress_metadata={
            "continuation_anchor": controller._continuation_anchor(
                previous.task_id, selection_reason="test"
            )
        },
    )
    explicit_followup.ingress_metadata = {}
    explicit = compiler.compile(
        task_context=explicit_followup,
        final_prompt="换个话题，帮我查北京天气",
        raw_text="换个话题，帮我查北京天气",
    )
    explicit_pack = store.get_artifact(explicit.context_pack_ref or "")
    assert explicit_pack is not None
    explicit_payload = json.loads(Path(explicit_pack.uri).read_text(encoding="utf-8"))
    assert explicit_payload["continuation_guidance"]["mode"] == "explicit_topic_shift"

    strong_followup = controller.start_task(
        conversation_id="oc_anchor_shift",
        goal="帮我查北京天气",
        source_channel="feishu",
        kind="respond",
        ingress_metadata={
            "continuation_anchor": controller._continuation_anchor(
                previous.task_id, selection_reason="test"
            )
        },
    )
    strong_followup.ingress_metadata = {}
    strong = compiler.compile(
        task_context=strong_followup,
        final_prompt="帮我查北京天气",
        raw_text="帮我查北京天气",
    )
    strong_pack = store.get_artifact(strong.context_pack_ref or "")
    assert strong_pack is not None
    strong_payload = json.loads(Path(strong_pack.uri).read_text(encoding="utf-8"))
    assert strong_payload["continuation_guidance"]["mode"] == "strong_topic_shift"


def test_normalize_ingress_uses_user_text_not_injected_memory(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    compiler = ProviderInputCompiler(store, ArtifactStore(tmp_path / "artifacts"))
    ctx = _make_task(store)

    normalized = compiler.normalize_ingress(
        task_context=ctx,
        raw_text=(
            "<feishu_msg_id>om_1</feishu_msg_id>\n<feishu_chat_id>oc_1</feishu_chat_id>\n你好"
        ),
        final_prompt=(
            "<session_time>session_started_at=2026-03-13 17:36:20 message_sent_at=2026-03-13 17:36:20</session_time>\n\n"
            "<relevant_memory>\n用户要求制作模型对比文档。\n</relevant_memory>\n\n"
            "<feishu_msg_id>om_1</feishu_msg_id>\n"
            "<feishu_chat_id>oc_1</feishu_chat_id>\n"
            "你好"
        ),
    )

    assert normalized["inline_excerpt"] == "你好"
    assert normalized["normalized_prompt"] == "你好"
