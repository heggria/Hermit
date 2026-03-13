from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import structlog

from hermit.builtin.memory.engine import MemoryEngine
from hermit.builtin.memory.types import MemoryEntry
from hermit.kernel.artifacts import ArtifactStore
from hermit.kernel.context import TaskExecutionContext, WorkingStateSnapshot
from hermit.kernel.context_compiler import ContextCompiler
from hermit.kernel.planning import PlanningService
from hermit.kernel.memory_governance import MemoryGovernanceService
from hermit.plugin.base import HookEvent, PluginContext
from hermit.provider.services import StructuredExtractionService, build_provider
from hermit.storage import JsonStore

log = structlog.get_logger()

_MAX_TRANSCRIPT_CHARS = 16000
_MAX_MSG_CHARS = 800
_CHECKPOINT_MIN_CHARS = 300
_CHECKPOINT_MIN_MESSAGES = 6
_CHECKPOINT_MIN_USER_MESSAGES = 2
_GOVERNANCE = MemoryGovernanceService()

_EXPLICIT_MEMORY_RE = re.compile(
    r"(记住|牢记|以后都|今后都|统一使用|统一回复|不要再|必须|务必|偏好|约定|规则|规范|"
    r"always|never|remember this|preference|rule|policy|convention)",
    re.IGNORECASE,
)
_DECISION_SIGNAL_RE = re.compile(
    r"(决定|改为|采用|切换到|标准化|规范为|路径|端口|分支|部署|"
    r"decided|switch to|migrate to|branch\b|port\b|deploy)",
    re.IGNORECASE,
)

_EXTRACTION_PROMPT = """\
你是通用记忆提取助手。从对话中全面提取所有值得长期记忆的信息，不限于技术内容。只输出合法 JSON：
{
  "used_keywords": ["关键词1"],
  "new_memories": [
    {"category": "分类名", "content": "简洁描述"}
  ]
}

## 分类说明
- 用户偏好：沟通习惯、语言偏好、工作风格、审美倾向、常用工具、个人习惯
- 项目约定：项目结构、命名规范、分支策略、部署流程、团队分工、协作规则
- 技术决策：技术选型及理由、架构设计、踩过的坑、性能优化、Bug 修复方案
- 环境与工具：开发环境配置、工具链、API 地址、代理设置、服务端口、安装步骤
- 其他：人物关系、日程习惯、知识见解、任何不属于以上分类但值得记住的信息
- 进行中的任务：明确的待办事项、未完成工作、后续计划

## 提取范围（尽量全面）
- 用户明确表达的偏好、要求、纠正
- 做出的决策及其理由
- 发现的问题及解决方案
- 项目结构、约定、流程的新增或变更
- 具体配置（文件路径、命令、端口、参数名）
- 人物及其职责、项目所属关系
- 反复出现的模式或问题
- 用户提到的日程、计划、习惯
- 学到的经验教训

## 质量要求
- used_keywords：从 <existing_memories> 中找到本次对话涉及的关键词（人名、项目名、技术名词等）
- content 必须简洁自包含，脱离上下文也能理解
- 保留具体细节（路径、命令、参数名），避免泛泛而谈
- 一条记忆只记一件事，不要合并不相关的信息
- 已有记忆中已存在的信息不要重复提取
- 纯粹的闲聊寒暄不需要记忆
- 如无值得记忆的信息，返回空数组"""


def register(ctx: PluginContext) -> None:
    settings = ctx.settings
    if settings is None:
        return

    engine = MemoryEngine(settings.memory_file)
    ctx.add_hook(HookEvent.SYSTEM_PROMPT, lambda: _inject_memory(engine, settings), priority=10)
    ctx.add_hook(
        HookEvent.PRE_RUN,
        lambda prompt, **kwargs: _inject_relevant_memory(engine, settings, prompt, **kwargs),
        priority=15,
    )
    ctx.add_hook(
        HookEvent.POST_RUN,
        lambda result, session_id="", **kwargs: _checkpoint_memories(
            engine,
            settings,
            session_id,
            getattr(result, "messages", []) or [],
        ),
        priority=20,
    )
    ctx.add_hook(
        HookEvent.SESSION_END,
        lambda session_id, messages: _save_memories(engine, settings, session_id, messages),
        priority=90,
    )


def _inject_memory(engine: MemoryEngine, settings: Any | None = None) -> str:
    compiler_result = _compile_context_pack(
        engine,
        settings,
        query="",
        conversation_id=None,
        runner=None,
    )
    if compiler_result is None:
        categories = _knowledge_categories(engine, settings)
        static_categories = _GOVERNANCE.filter_static_categories(categories)
        prompt = engine.summary_prompt(static_categories, limit_per_category=3)
        entry_count = sum(min(3, len(entries)) for entries in static_categories.values() if entries)
        category_count = sum(1 for entries in static_categories.values() if entries)
    else:
        prompt = compiler_result["static_prompt"]
        entry_count = len(compiler_result["pack"].static_memory)
        category_count = len({item["category"] for item in compiler_result["pack"].static_memory})
    if not prompt:
        log.info("memory_injected", categories=0, entries=0)
        return ""
    log.info("memory_injected", categories=category_count, entries=entry_count)
    return f"<memory_context>\n{prompt}\n</memory_context>"


def _inject_relevant_memory(
    engine: MemoryEngine,
    settings: Any | str | None,
    prompt: str | None = None,
    session_id: str | None = None,
    runner: Any | None = None,
    **_: Any,
) -> str:
    # Keep backward compatibility with older helper call sites/tests that pass
    # only `(engine, prompt)`.
    if prompt is None:
        prompt = str(settings or "")
        settings = None
    compiler_result = _compile_context_pack(
        engine,
        settings,
        query=prompt,
        conversation_id=session_id,
        runner=runner,
    )
    if compiler_result is None:
        categories = _knowledge_categories(engine, settings)
        relevant = engine.retrieval_prompt(prompt, categories=categories, limit=5, char_budget=900)
    else:
        relevant = compiler_result["retrieval_prompt"]
    if not relevant:
        return prompt
    return f"<relevant_memory>\n{relevant}\n</relevant_memory>\n\n{prompt}"


def _knowledge_categories(engine: MemoryEngine, settings: Any | None) -> Dict[str, List[MemoryEntry]]:
    if settings is None:
        return engine.load()
    kernel_db_path = getattr(settings, "kernel_db_path", None)
    if not kernel_db_path:
        return engine.load()
    from hermit.kernel.knowledge import MemoryRecordService
    from hermit.kernel.store import KernelStore

    store = KernelStore(Path(kernel_db_path))
    try:
        service = MemoryRecordService(store, mirror_path=Path(settings.memory_file))
        return service.active_categories()
    finally:
        store.close()


def _compile_context_pack(
    engine: MemoryEngine,
    settings: Any | None,
    *,
    query: str,
    conversation_id: str | None,
    runner: Any | None,
) -> dict[str, Any] | None:
    if settings is None:
        return None
    kernel_db_path = getattr(settings, "kernel_db_path", None)
    if not kernel_db_path:
        return None
    from hermit.kernel.store import KernelStore

    artifact_store = None
    kernel_artifacts_dir = getattr(settings, "kernel_artifacts_dir", None)
    if kernel_artifacts_dir:
        artifact_store = ArtifactStore(Path(kernel_artifacts_dir))
    store = KernelStore(Path(kernel_db_path))
    try:
        task_id = ""
        if runner is not None and conversation_id and getattr(runner, "task_controller", None) is not None:
            active_task = runner.task_controller.active_task_for_conversation(conversation_id)
            if active_task is not None:
                task_id = active_task.task_id
        workspace_root = str(Path(settings.memory_file).parent)
        ctx = TaskExecutionContext(
            conversation_id=conversation_id or "memory-system",
            task_id=task_id,
            step_id="context_pack",
            step_attempt_id="context_pack",
            source_channel="memory",
            workspace_root=workspace_root,
        )
        compiler = ContextCompiler(_GOVERNANCE, artifact_store)
        planning = PlanningService(store, artifact_store)
        planning_state = planning.state_for_task(task_id) if task_id else None
        pack = compiler.compile(
            context=ctx,
            working_state=WorkingStateSnapshot(
                goal_summary=query[:400],
                planning_mode=bool(planning_state.planning_mode) if planning_state else False,
                candidate_plan_refs=list(planning_state.candidate_plan_refs) if planning_state else [],
                selected_plan_ref=str(planning_state.selected_plan_ref or "") if planning_state else "",
                plan_status=str(planning_state.plan_status or "none") if planning_state else "none",
            ),
            beliefs=store.list_beliefs(status="active", limit=200),
            memories=store.list_memory_records(status="active", conversation_id=conversation_id, limit=500),
            query=query,
        )
        if artifact_store is not None and pack.artifact_uri is not None:
            artifact = store.create_artifact(
                task_id=task_id or None,
                step_id=None,
                kind="context.pack/v1",
                uri=pack.artifact_uri,
                content_hash=str(pack.artifact_hash or pack.pack_hash),
                producer="memory_hook",
                retention_class="audit",
                trust_tier="derived",
                metadata={"pack_hash": pack.pack_hash, "conversation_id": conversation_id or ""},
            )
            if task_id:
                store.append_event(
                    event_type="context.pack.compiled",
                    entity_type="task",
                    entity_id=task_id,
                    task_id=task_id,
                    actor="kernel",
                    payload={"artifact_ref": artifact.artifact_id, "pack_hash": pack.pack_hash},
                )
        return {
            "pack": pack,
            "static_prompt": compiler.render_static_prompt(pack),
            "retrieval_prompt": compiler.render_retrieval_prompt(pack),
        }
    finally:
        store.close()

def _save_memories(
    engine: MemoryEngine,
    settings: Any,
    session_id: str,
    messages: List[Dict[str, Any]],
) -> None:
    if not messages:
        log.info("memory_save_skipped", session_id=session_id, reason="no_messages")
        return
    if not settings.has_auth:
        log.info("memory_save_skipped", session_id=session_id, reason="no_auth")
        return
    try:
        _extract_and_save(engine, settings, messages, session_id=session_id)
    except Exception:
        log.exception("memory_save_failed", session_id=session_id)
    finally:
        _clear_session_progress(settings.session_state_file, session_id)


def _checkpoint_memories(
    engine: MemoryEngine,
    settings: Any,
    session_id: str,
    messages: List[Dict[str, Any]],
) -> None:
    if not session_id:
        log.info("memory_checkpoint_skipped", reason="missing_session_id")
        return
    if session_id == "cli-oneshot":
        log.info("memory_checkpoint_skipped", session_id=session_id, reason="cli_oneshot")
        return
    if not messages:
        log.info("memory_checkpoint_skipped", session_id=session_id, reason="no_messages")
        return
    if not settings.has_auth:
        log.info("memory_checkpoint_skipped", session_id=session_id, reason="no_auth")
        return

    delta, processed = _pending_messages(settings.session_state_file, session_id, messages)
    if not delta:
        log.info("memory_checkpoint_skipped", session_id=session_id, reason="no_pending_delta")
        return

    should_checkpoint, reason = _should_checkpoint(delta)
    if not should_checkpoint:
        log.info("memory_checkpoint_skipped", session_id=session_id, reason=reason, pending_messages=len(delta))
        return

    try:
        extraction = _extract_memory_payload(engine, settings, delta, max_tokens=1024)
    except Exception:
        log.exception("memory_checkpoint_failed", session_id=session_id, reason=reason)
        return

    new_entries = extraction["new_entries"]
    if not new_entries:
        log.info(
            "memory_checkpoint_no_entries",
            session_id=session_id,
            reason=reason,
            pending_messages=len(delta),
        )
        return

    if not _promote_memories_via_kernel(
        engine,
        settings,
        session_id=session_id,
        messages=delta,
        used_keywords=set(extraction["used_keywords"]),
        new_entries=new_entries,
        mode="checkpoint",
    ):
        log.info("memory_checkpoint_skipped", session_id=session_id, reason="kernel_promotion_unavailable")
        return
    _mark_messages_processed(settings.session_state_file, session_id, len(messages))
    log.info(
        "memory_checkpoint_saved",
        session_id=session_id,
        reason=reason,
        new=len(new_entries),
        processed_before=processed,
        processed_after=len(messages),
    )


def _extract_and_save(
    engine: MemoryEngine,
    settings: Any,
    messages: List[Dict[str, Any]],
    *,
    session_id: str = "",
) -> None:
    log.info("memory_extraction_started", mode="session_end", message_count=len(messages))
    extraction = _extract_memory_payload(engine, settings, messages, max_tokens=2048)
    used_keywords = extraction["used_keywords"]
    new_entries = extraction["new_entries"]

    if not new_entries and not used_keywords:
        log.info("memory_nothing_to_save")
        return

    if _promote_memories_via_kernel(
        engine,
        settings,
        session_id=session_id,
        messages=messages,
        used_keywords=used_keywords,
        new_entries=new_entries,
        mode="session_end",
    ):
        log.info("memory_promoted", mode="session_end", new=len(new_entries), keywords=len(used_keywords))
        return
    log.info("memory_save_skipped", session_id=session_id, reason="kernel_promotion_unavailable")


def _promote_memories_via_kernel(
    engine: MemoryEngine,
    settings: Any,
    *,
    session_id: str,
    messages: List[Dict[str, Any]],
    used_keywords: Set[str],
    new_entries: List[MemoryEntry],
    mode: str,
) -> bool:
    kernel_db_path = getattr(settings, "kernel_db_path", None)
    kernel_artifacts_dir = getattr(settings, "kernel_artifacts_dir", None)
    if not kernel_db_path or not kernel_artifacts_dir or not new_entries:
        return False

    from hermit.kernel.artifacts import ArtifactStore
    from hermit.kernel.context import capture_execution_environment
    from hermit.kernel.controller import TaskController
    from hermit.kernel.decisions import DecisionService
    from hermit.kernel.knowledge import BeliefService, MemoryRecordService
    from hermit.kernel.permits import CapabilityGrantError, ExecutionPermitService
    from hermit.kernel.policy import ActionRequest, PolicyEngine
    from hermit.kernel.receipts import ReceiptService
    from hermit.kernel.store import KernelStore

    store = KernelStore(Path(kernel_db_path))
    try:
        artifact_store = ArtifactStore(Path(kernel_artifacts_dir))
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id=session_id or f"memory-{mode}",
            goal=f"Promote durable memory ({mode})",
            source_channel=controller.source_from_session(session_id or "memory"),
            kind="memory_promotion",
            policy_profile="memory",
            workspace_root=str(Path(settings.memory_file).parent),
        )
        policy_engine = PolicyEngine()
        decision_service = DecisionService(store)
        belief_service = BeliefService(store)
        memory_service = MemoryRecordService(store, mirror_path=Path(settings.memory_file))
        permit_service = ExecutionPermitService(store)
        receipt_service = ReceiptService(store, artifact_store)
        request_id = f"memreq_{uuid.uuid4().hex[:12]}"

        transcript = _format_transcript(messages)
        transcript_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="memory_evidence.transcript",
            payload={"mode": mode, "transcript": transcript},
            metadata={"mode": mode},
            event_type="memory.evidence.captured",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        extraction_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="memory_evidence.extraction",
            payload={
                "mode": mode,
                "used_keywords": sorted(used_keywords),
                "new_entries": [_memory_entry_payload(entry) for entry in new_entries],
            },
            metadata={"mode": mode, "entry_count": len(new_entries)},
            event_type="memory.extraction.recorded",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        action_request = ActionRequest(
            request_id=request_id,
            idempotency_key=request_id,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            conversation_id=ctx.conversation_id,
            tool_name="memory_promotion",
            tool_input={
                "mode": mode,
                "entry_count": len(new_entries),
                "entries": [_memory_entry_payload(entry) for entry in new_entries],
            },
            action_class="memory_write",
            resource_scopes=["memory_store"],
            risk_hint="medium",
            requires_receipt=True,
            actor={"kind": "kernel", "agent_id": "memory"},
            context={
                "policy_profile": "memory",
                "source_ingress": "memory_hook",
                "workspace_root": str(Path(settings.memory_file).parent),
                "evidence_refs": [transcript_ref, extraction_ref],
                "mode": mode,
            },
            derived={"categories": sorted({entry.category for entry in new_entries})},
        )
        action_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="action_request",
            payload=action_request.to_dict(),
            metadata={"mode": mode, "tool_name": action_request.tool_name},
            event_type="action.requested",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        policy = policy_engine.evaluate(action_request)
        policy_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="policy_evaluation",
            payload={
                "tool_name": action_request.tool_name,
                "action_class": action_request.action_class,
                "risk_band": policy.risk_level,
                "verdict": policy.verdict,
                "reason": policy.reason,
                "reasons": [reason.to_dict() for reason in policy.reasons],
                "obligations": policy.obligations.to_dict(),
                "policy_profile": "memory",
            },
            metadata={"mode": mode, "tool_name": action_request.tool_name},
            event_type="policy.evaluated",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        if policy.verdict == "deny" or policy.obligations.require_approval:
            controller.finalize_result(ctx, status="failed")
            return False

        decision_id = decision_service.record(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_type="memory_promotion",
            verdict=policy.verdict,
            reason=policy.reason or "Evidence-bound durable memory promotion allowed.",
            evidence_refs=[transcript_ref, extraction_ref, action_ref, policy_ref],
            policy_ref=policy_ref,
            action_type="memory_write",
            decided_by="memory_hook",
        )
        permit_id = permit_service.issue(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            decision_ref=decision_id,
            approval_ref=None,
            policy_ref=policy_ref,
            action_class="memory_write",
            resource_scope=["memory_store"],
            idempotency_key=request_id,
            constraints={
                "mode": mode,
                "entry_count": len(new_entries),
                "categories": sorted({entry.category for entry in new_entries}),
            },
        )
        store.update_step_attempt(
            ctx.step_attempt_id,
            status="dispatching",
            decision_id=decision_id,
            permit_id=permit_id,
        )
        store.update_step(ctx.step_id, status="dispatching")
        try:
            permit_service.enforce(
                permit_id,
                action_class="memory_write",
                resource_scope=["memory_store"],
                constraints={
                    "mode": mode,
                    "entry_count": len(new_entries),
                    "categories": sorted({entry.category for entry in new_entries}),
                },
            )
        except CapabilityGrantError as exc:
            store.append_event(
                event_type="dispatch.denied",
                entity_type="execution_permit",
                entity_id=permit_id,
                task_id=ctx.task_id,
                step_id=ctx.step_id,
                actor="kernel",
                payload={
                    "permit_ref": permit_id,
                    "decision_ref": decision_id,
                    "error_code": exc.code,
                    "error": str(exc),
                    "tool_name": action_request.tool_name,
                },
            )
            store.update_step_attempt(
                ctx.step_attempt_id,
                status="failed",
                waiting_reason=str(exc),
                decision_id=decision_id,
                permit_id=permit_id,
            )
            store.update_step(ctx.step_id, status="failed")
            controller.finalize_result(ctx, status="failed")
            return False

        promoted_beliefs = []
        promoted_memories = []
        for entry in new_entries:
            belief = belief_service.record(
                task_id=ctx.task_id,
                conversation_id=ctx.conversation_id,
                scope_kind="conversation",
                scope_ref=ctx.conversation_id,
                category=entry.category,
                content=entry.content,
                confidence=entry.confidence,
                evidence_refs=[transcript_ref, extraction_ref, action_ref],
                supersedes=list(entry.supersedes),
            )
            memory = memory_service.promote_from_belief(
                belief=belief,
                conversation_id=ctx.conversation_id,
                workspace_root=str(Path(settings.memory_file).parent),
            )
            promoted_beliefs.append(belief.belief_id)
            promoted_memories.append(memory.memory_id)
        memory_service.render_mirror(Path(settings.memory_file))

        rollback_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="rollback.memory_targets",
            payload={"belief_ids": promoted_beliefs, "memory_ids": promoted_memories},
            metadata={"mode": mode, "entry_count": len(promoted_memories)},
            event_type="memory.rollback_captured",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )

        output_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="memory_promotion_result",
            payload={
                "mode": mode,
                "used_keywords": sorted(used_keywords),
                "new_entries": [_memory_entry_payload(entry) for entry in new_entries],
            },
            metadata={"mode": mode, "entry_count": len(new_entries)},
            event_type="memory.promoted",
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        env_ref = _store_memory_artifact(
            store,
            artifact_store,
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            kind="environment",
            payload=capture_execution_environment(cwd=Path(settings.memory_file).parent),
            metadata={"mode": mode},
            entity_type="step_attempt",
            event_type=None,
            entity_id=ctx.step_attempt_id,
            task_context=ctx,
        )
        receipt_service.issue(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            action_type="memory_write",
            input_refs=[transcript_ref, extraction_ref],
            environment_ref=env_ref,
            policy_result=policy.to_dict(),
            approval_ref=None,
            output_refs=[output_ref],
            result_summary=f"Promoted {len(new_entries)} durable memory entries via {mode}.",
            result_code="succeeded",
            decision_ref=decision_id,
            permit_ref=permit_id,
            policy_ref=policy_ref,
            idempotency_key=request_id,
            rollback_supported=True,
            rollback_strategy="supersede_or_invalidate",
            rollback_artifact_refs=[rollback_ref],
        )
        permit_service.consume(permit_id)
        controller.finalize_result(ctx, status="succeeded")
        return True
    finally:
        store.close()


def _store_memory_artifact(
    store: Any,
    artifact_store: Any,
    *,
    task_id: str,
    step_id: str,
    kind: str,
    payload: Any,
    metadata: Dict[str, Any],
    task_context: Any,
    event_type: str | None,
    entity_id: str,
    entity_type: str = "step_attempt",
) -> str:
    uri, content_hash = artifact_store.store_json(payload)
    artifact = store.create_artifact(
        task_id=task_id,
        step_id=step_id,
        kind=kind,
        uri=uri,
        content_hash=content_hash,
        producer="memory_hook",
        retention_class="audit",
        trust_tier="observed",
        metadata=metadata,
    )
    if event_type:
        store.append_event(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            task_id=task_context.task_id,
            step_id=task_context.step_id,
            actor="kernel",
            payload={"artifact_ref": artifact.artifact_id, **metadata},
        )
    return artifact.artifact_id


def _memory_entry_payload(entry: MemoryEntry) -> Dict[str, Any]:
    return {
        "category": entry.category,
        "content": entry.content,
        "score": entry.score,
        "locked": entry.locked,
        "confidence": entry.confidence,
    }


def _extract_memory_payload(
    engine: MemoryEngine,
    settings: Any,
    messages: List[Dict[str, Any]],
    *,
    max_tokens: int,
) -> Dict[str, Any]:
    transcript = _format_transcript(messages)
    if len(transcript.strip()) < 20:
        log.info("memory_extraction_empty", reason="short_transcript", transcript_chars=len(transcript))
        return {"used_keywords": set(), "new_entries": []}

    existing = _knowledge_categories(engine, settings)
    existing_text = engine.summary_prompt(existing)
    user_content = (
        f"<existing_memories>\n{existing_text}\n</existing_memories>\n\n"
        f"<conversation>\n{transcript}\n</conversation>"
    )

    log.info(
        "memory_extraction_started",
        mode="checkpoint" if max_tokens <= 1024 else "session_end",
        message_count=len(messages),
        transcript_chars=len(transcript),
        existing_chars=len(existing_text),
        max_tokens=max_tokens,
    )
    provider = build_provider(settings, model=settings.model)
    service = StructuredExtractionService(provider, model=settings.model)
    data = service.extract_json(
        system_prompt=_EXTRACTION_PROMPT,
        user_content=user_content,
        max_tokens=max_tokens,
    )
    if not data:
        log.info("memory_extraction_empty", reason="no_provider_data", transcript_chars=len(transcript))
        return {"used_keywords": set(), "new_entries": []}

    used_keywords: Set[str] = set(data.get("used_keywords", []))
    new_entries: List[MemoryEntry] = []
    for item in data.get("new_memories", []):
        content = item.get("content", "").strip()
        if content:
            new_entries.append(MemoryEntry(
                category=item.get("category", "其他"),
                content=content,
                confidence=_infer_confidence(content),
            ))
    log.info(
        "memory_extraction_result",
        used_keywords=len(used_keywords),
        new_entries=len(new_entries),
        categories=len({entry.category for entry in new_entries}),
    )
    return {"used_keywords": used_keywords, "new_entries": new_entries}


def _consolidate_category_entries(category: str, entries: List[MemoryEntry]) -> List[MemoryEntry]:
    consolidated: List[MemoryEntry] = []
    for entry in sorted(
        entries,
        key=lambda item: (item.updated_at, item.created_at, item.score, item.confidence),
        reverse=True,
    ):
        merged = False
        for existing in consolidated:
            if not _should_merge_entries(existing, entry):
                continue
            existing.score = max(existing.score, entry.score)
            existing.confidence = max(existing.confidence, entry.confidence)
            existing.updated_at = max(existing.updated_at, entry.updated_at, entry.created_at)
            if entry.content != existing.content and entry.content not in existing.supersedes:
                existing.supersedes.append(entry.content)
            for value in entry.supersedes:
                if value not in existing.supersedes:
                    existing.supersedes.append(value)
            merged = True
            break
        if not merged:
            consolidated.append(entry)
    return consolidated


def _should_merge_entries(left: MemoryEntry, right: MemoryEntry) -> bool:
    if left.category != right.category:
        return False
    if MemoryEngine._is_duplicate([left], right.content):
        return True
    return MemoryEngine._shares_topic(left.content, right.content)


def _infer_confidence(content: str) -> float:
    strong_signal = ("必须", "务必", "统一", "默认", "固定", "不要", "采用", "改为")
    if any(signal in content for signal in strong_signal):
        return 0.8
    if len(content) >= 20:
        return 0.65
    return 0.55


def _should_checkpoint(messages: List[Dict[str, Any]]) -> Tuple[bool, str]:
    user_text = _collect_role_text(messages, "user")
    assistant_text = _collect_role_text(messages, "assistant")
    transcript = _format_transcript(messages)
    meaningful_count = sum(1 for msg in messages if _message_text(msg).strip())
    user_count = sum(1 for msg in messages if msg.get("role") == "user" and _message_text(msg).strip())

    if _EXPLICIT_MEMORY_RE.search(user_text):
        return True, "explicit_memory_signal"
    if _DECISION_SIGNAL_RE.search(user_text) or _DECISION_SIGNAL_RE.search(assistant_text):
        return True, "decision_signal"
    if len(transcript) >= _CHECKPOINT_MIN_CHARS and user_count >= _CHECKPOINT_MIN_USER_MESSAGES:
        return True, "conversation_batch"
    if meaningful_count >= _CHECKPOINT_MIN_MESSAGES:
        return True, "message_batch"
    return False, "below_threshold"


def _pending_messages(
    state_file: Path,
    session_id: str,
    messages: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    data = _read_state(state_file)
    sessions = data.get("sessions", {})
    meta = sessions.get(session_id, {}) if isinstance(sessions, dict) else {}
    processed = int(meta.get("processed_messages", 0))
    if processed < 0:
        processed = 0
    return messages[processed:], processed


def _mark_messages_processed(state_file: Path, session_id: str, count: int) -> None:
    store = JsonStore(state_file, default={"session_index": 0, "sessions": {}}, cross_process=True)
    with store.update() as data:
        sessions = data.setdefault("sessions", {})
        if not isinstance(sessions, dict):
            sessions = {}
            data["sessions"] = sessions
        meta = sessions.get(session_id)
        if not isinstance(meta, dict):
            meta = {}
            sessions[session_id] = meta
        meta["processed_messages"] = max(0, int(count))


def _clear_session_progress(state_file: Path, session_id: str) -> None:
    if not session_id:
        return
    store = JsonStore(state_file, default={"session_index": 0, "sessions": {}}, cross_process=True)
    with store.update() as data:
        sessions = data.get("sessions", {})
        if isinstance(sessions, dict):
            sessions.pop(session_id, None)


def _read_state(state_file: Path) -> Dict[str, Any]:
    return JsonStore(
        state_file,
        default={"session_index": 0, "sessions": {}},
        cross_process=True,
    ).read()


def _format_transcript(messages: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    total = 0
    for msg in messages:
        role = msg.get("role", "unknown")
        text = _message_text(msg)

        if not text.strip():
            continue

        label = {"user": "User", "assistant": "Assistant"}.get(role, role)
        entry = f"[{label}] {text}"
        total += len(entry)
        if total > _MAX_TRANSCRIPT_CHARS:
            lines.append("[... conversation truncated ...]")
            break
        lines.append(entry)

    return "\n\n".join(lines)


def _message_text(msg: Dict[str, Any]) -> str:
    content = msg.get("content", "")
    if isinstance(content, str):
        return content[:_MAX_MSG_CHARS]
    if not isinstance(content, list):
        return str(content)[:_MAX_MSG_CHARS] if content else ""

    parts: List[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text":
            parts.append(block.get("text", "")[:_MAX_MSG_CHARS])
        elif btype == "tool_use":
            inp = json.dumps(block.get("input", {}), ensure_ascii=False)[:120]
            parts.append(f"[Tool: {block.get('name', '')}({inp})]")
        elif btype == "tool_result":
            parts.append(f"[Tool Result: {str(block.get('content', ''))[:200]}]")
    return "\n".join(parts).strip()


def _collect_role_text(messages: List[Dict[str, Any]], role: str) -> str:
    return "\n".join(
        _message_text(msg) for msg in messages if msg.get("role") == role
    ).strip()


def _parse_json(text: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        for suffix in ("}", "]}", "]}"):
            try:
                return json.loads(cleaned + suffix)
            except json.JSONDecodeError:
                continue
        log.warning("memory_json_parse_failed", text=text[:200])
        return None


def _bump_session_index(state_file: Path) -> int:
    """Atomically increment session_index and return the new value.

    Uses JsonStore.update() to eliminate the read-modify-write TOCTOU race:
    the lock is held for the entire read → increment → write sequence.
    """
    store = JsonStore(
        state_file,
        default={"session_index": 0, "sessions": {}},
        cross_process=True,
    )
    try:
        with store.update() as data:
            idx = data.get("session_index", 0) + 1
            data["session_index"] = idx
        return idx
    except Exception:
        log.warning("session_state_update_failed")
        return 1
