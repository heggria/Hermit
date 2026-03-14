from __future__ import annotations

from pathlib import Path

from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.context import TaskExecutionContext
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
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id, status="running", context={})
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
    store.append_event(
        event_type="task.note.appended",
        entity_type="task",
        entity_id=ctx.task_id,
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        actor="user",
        payload={"raw_text": "Follow the architecture note.", "inline_excerpt": "Follow the architecture note."},
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

    pack_artifact = store.get_artifact(compiled.context_pack_ref)
    projection_artifact = store.get_artifact(compiled.session_projection_ref)
    assert pack_artifact is not None and pack_artifact.kind == "context.pack/v2"
    assert projection_artifact is not None and projection_artifact.kind == "conversation.projection/v1"


def test_normalize_ingress_uses_user_text_not_injected_memory(tmp_path: Path) -> None:
    store = KernelStore(tmp_path / "kernel" / "state.db")
    compiler = ProviderInputCompiler(store, ArtifactStore(tmp_path / "artifacts"))
    ctx = _make_task(store)

    normalized = compiler.normalize_ingress(
        task_context=ctx,
        raw_text=(
            "<feishu_msg_id>om_1</feishu_msg_id>\n"
            "<feishu_chat_id>oc_1</feishu_chat_id>\n"
            "你好"
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
