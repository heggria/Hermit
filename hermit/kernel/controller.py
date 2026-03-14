from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from hermit.builtin.memory.engine import MemoryEngine
from hermit.i18n import resolve_locale, tr
from hermit.kernel.context import TaskExecutionContext
from hermit.kernel.control_intents import parse_control_intent
from hermit.kernel.ingress_router import BindingDecision, IngressRouter
from hermit.kernel.outcomes import TERMINAL_TASK_STATUSES, build_task_outcome, clean_runtime_text
from hermit.kernel.planning import PlanningService
from hermit.kernel.store import KernelStore

_AUTO_PARENT = object()
_LOW_SIGNAL_RE = re.compile(r"^[\s\?\uff1f!！,，。\.~～…]+$")
_SESSION_TIME_RE = re.compile(r"<session_time>.*?</session_time>\s*", re.DOTALL)
_FEISHU_TAG_RE = re.compile(r"<feishu_[^>]+>.*?</feishu_[^>]+>\s*", re.DOTALL)
_ARTIFACT_REF_RE = re.compile(r"\bartifact_[a-z0-9]{6,}\b", re.IGNORECASE)
_GREETING_TEXTS = {
    "hi",
    "hello",
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "在吗",
    "有人吗",
    "早上好",
    "上午好",
    "中午好",
    "下午好",
    "晚上好",
}
_EXPLICIT_NEW_TASK_MARKERS = (
    "新任务",
    "新开一个",
    "新开个",
    "另一个",
    "重新开始",
    "从头开始",
    "换个话题",
    "顺便问下",
    "顺便再问",
)
_CONTINUE_MARKERS = (
    "继续",
    "接着",
    "然后",
    "补充",
    "补充一点",
    "补充说明",
    "说明",
    "加上",
    "再加",
    "改成",
    "改为",
    "extra note",
    "follow up",
    "放到",
    "发到",
    "写到",
    "去掉",
    "删掉",
    "保留",
    "就按",
    "按照",
)
_AMBIGUOUS_FOLLOWUP_MARKERS = (
    "这个",
    "那个",
    "这份",
    "这条",
    "上面",
    "上一条",
    "刚才",
)


@dataclass(frozen=True)
class IngressDecision:
    mode: str
    intent: str = ""
    reason: str = ""
    resolution: str = ""
    ingress_id: str | None = None
    task_id: str | None = None
    note_event_seq: int | None = None
    continuation_anchor: dict[str, Any] | None = None
    anchor_task_id: str | None = None
    anchor_kind: str | None = None
    anchor_reason: str | None = None
    confidence: float = 0.0
    margin: float = 0.0
    reason_codes: list[str] | None = None
    candidates: list[dict[str, Any]] | None = None
    parent_task_id: str | None | object = _AUTO_PARENT


class TaskController:
    def __init__(self, store: KernelStore) -> None:
        self.store = store
        self.ingress_router = IngressRouter(store)

    def _t(self, message_key: str, *, default: str | None = None, **kwargs: object) -> str:
        return tr(message_key, locale=resolve_locale(), default=default, **kwargs)

    def source_from_session(self, session_id: str) -> str:
        if session_id.startswith("webhook-"):
            return "webhook"
        if session_id.startswith("schedule-"):
            return "scheduler"
        if session_id.startswith("cli"):
            return "cli"
        if ":" in session_id or session_id.startswith("oc_"):
            return "feishu"
        return "chat"

    def ensure_conversation(self, conversation_id: str, *, source_channel: str | None = None) -> None:
        self.store.ensure_conversation(
            conversation_id,
            source_channel=source_channel or self.source_from_session(conversation_id),
        )

    def latest_task(self, conversation_id: str) -> object | None:
        return self.store.get_last_task_for_conversation(conversation_id)

    def active_task_for_conversation(self, conversation_id: str):
        focus_task_id = self.store.ensure_valid_focus(conversation_id)
        task = self.store.get_task(focus_task_id) if focus_task_id else None
        if task is None:
            task = self.store.get_last_task_for_conversation(conversation_id)
        if task is None:
            return None
        if task.status == "blocked":
            attempt = next(iter(self.store.list_step_attempts(task_id=task.task_id, limit=1)), None)
            if attempt is not None and str(attempt.waiting_reason or "") == "awaiting_plan_confirmation":
                return None
        if task.status in {"queued", "running", "blocked"}:
            return task
        return None

    def start_task(
        self,
        *,
        conversation_id: str,
        goal: str,
        source_channel: str,
        kind: str,
        policy_profile: str = "default",
        workspace_root: str = "",
        parent_task_id: str | None | object = _AUTO_PARENT,
        requested_by: str | None = None,
        ingress_metadata: dict[str, Any] | None = None,
    ) -> TaskExecutionContext:
        self.ensure_conversation(conversation_id, source_channel=source_channel)
        parent = self.store.get_last_task_for_conversation(conversation_id)
        if parent_task_id is _AUTO_PARENT:
            resolved_parent = parent.task_id if parent else None
        else:
            resolved_parent = parent_task_id
        metadata = dict(ingress_metadata or {})
        task = self.store.create_task(
            conversation_id=conversation_id,
            title=(goal.strip() or self._t("kernel.controller.task.default_title", default="Hermit task"))[:120],
            goal=goal,
            source_channel=source_channel,
            parent_task_id=resolved_parent,
            policy_profile=policy_profile,
            requested_by=requested_by,
            continuation_anchor=dict(metadata.get("continuation_anchor", {}) or {}) or None,
        )
        step = self.store.create_step(task_id=task.task_id, kind=kind, status="running")
        attempt_context = {
            "note_cursor_event_seq": 0,
            "execution_mode": "run",
            "phase": "planning",
            "input_dirty": False,
            "ingress_metadata": metadata,
        }
        if workspace_root:
            attempt_context["workspace_root"] = workspace_root
        attempt = self.store.create_step_attempt(
            task_id=task.task_id,
            step_id=step.step_id,
            status="running",
            context=attempt_context,
            queue_priority=self._ingress_queue_priority(source_channel=source_channel, requested_by=requested_by, metadata=metadata),
        )
        self._bind_ingress_on_task_creation(
            conversation_id=conversation_id,
            task_id=task.task_id,
            parent_task_id=resolved_parent,
            ingress_metadata=metadata,
        )
        self._set_focus(conversation_id=conversation_id, task_id=task.task_id, reason="task_started")
        return TaskExecutionContext(
            conversation_id=conversation_id,
            task_id=task.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            source_channel=source_channel,
            policy_profile=policy_profile,
            workspace_root=workspace_root,
            ingress_metadata=metadata,
        )

    def enqueue_task(
        self,
        *,
        conversation_id: str,
        goal: str,
        source_channel: str,
        kind: str,
        policy_profile: str = "default",
        workspace_root: str = "",
        parent_task_id: str | None | object = _AUTO_PARENT,
        requested_by: str | None = None,
        ingress_metadata: dict[str, Any] | None = None,
        source_ref: str | None = None,
    ) -> TaskExecutionContext:
        self.store.ensure_conversation(
            conversation_id,
            source_channel=source_channel,
            source_ref=source_ref,
        )
        parent = self.store.get_last_task_for_conversation(conversation_id)
        if parent_task_id is _AUTO_PARENT:
            resolved_parent = parent.task_id if parent else None
        else:
            resolved_parent = parent_task_id
        metadata = dict(ingress_metadata or {})
        task = self.store.create_task(
            conversation_id=conversation_id,
            title=(goal.strip() or self._t("kernel.controller.task.default_title", default="Hermit task"))[:120],
            goal=goal,
            source_channel=source_channel,
            status="queued",
            parent_task_id=resolved_parent,
            policy_profile=policy_profile,
            requested_by=requested_by,
            continuation_anchor=dict(metadata.get("continuation_anchor", {}) or {}) or None,
        )
        step = self.store.create_step(task_id=task.task_id, kind=kind, status="ready")
        attempt_context = {
            "note_cursor_event_seq": 0,
            "ingress_metadata": metadata,
            "execution_mode": "run",
            "phase": "planning",
            "input_dirty": False,
        }
        if workspace_root:
            attempt_context["workspace_root"] = workspace_root
        attempt = self.store.create_step_attempt(
            task_id=task.task_id,
            step_id=step.step_id,
            status="ready",
            context=attempt_context,
            queue_priority=self._ingress_queue_priority(source_channel=source_channel, requested_by=requested_by, metadata=metadata),
        )
        self._bind_ingress_on_task_creation(
            conversation_id=conversation_id,
            task_id=task.task_id,
            parent_task_id=resolved_parent,
            ingress_metadata=metadata,
        )
        self._set_focus(conversation_id=conversation_id, task_id=task.task_id, reason="task_enqueued")
        return TaskExecutionContext(
            conversation_id=conversation_id,
            task_id=task.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            source_channel=source_channel,
            policy_profile=policy_profile,
            workspace_root=workspace_root,
            ingress_metadata=metadata,
        )

    def start_followup_step(
        self,
        *,
        task_id: str,
        kind: str,
        status: str = "running",
        workspace_root: str = "",
        ingress_metadata: dict[str, Any] | None = None,
    ) -> TaskExecutionContext:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_task",
                    default="Unknown task: {task_id}",
                    task_id=task_id,
                )
            )
        step = self.store.create_step(task_id=task_id, kind=kind, status=status)
        context = {
            "note_cursor_event_seq": 0,
            "execution_mode": "run",
            "phase": "planning",
            "input_dirty": False,
            "ingress_metadata": dict(ingress_metadata or {}),
        }
        if workspace_root:
            context["workspace_root"] = workspace_root
        attempt = self.store.create_step_attempt(
            task_id=task_id,
            step_id=step.step_id,
            status=status,
            context=context,
            queue_priority=self._ingress_queue_priority(
                source_channel=task.source_channel,
                requested_by=None,
                metadata=dict(ingress_metadata or {}),
            ),
        )
        self.store.update_task_status(task_id, "running" if status == "running" else "queued")
        self._set_focus(
            conversation_id=task.conversation_id,
            task_id=task_id,
            reason="followup_step_started" if status == "running" else "followup_step_enqueued",
        )
        return TaskExecutionContext(
            conversation_id=task.conversation_id,
            task_id=task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            source_channel=task.source_channel,
            policy_profile=task.policy_profile,
            workspace_root=workspace_root,
            ingress_metadata=dict(ingress_metadata or {}),
        )

    def context_for_attempt(self, step_attempt_id: str) -> TaskExecutionContext:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_step_attempt",
                    default="Unknown step attempt: {step_attempt_id}",
                    step_attempt_id=step_attempt_id,
                )
            )
        task = self.store.get_task(attempt.task_id)
        if task is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_task_for_step_attempt",
                    default="Unknown task for step attempt: {step_attempt_id}",
                    step_attempt_id=step_attempt_id,
                )
            )
        return TaskExecutionContext(
            conversation_id=task.conversation_id,
            task_id=task.task_id,
            step_id=attempt.step_id,
            step_attempt_id=step_attempt_id,
            source_channel=task.source_channel,
            policy_profile=task.policy_profile,
            workspace_root=str(attempt.context.get("workspace_root", "") or ""),
            ingress_metadata=dict(attempt.context.get("ingress_metadata", {}) or {}),
        )

    def enqueue_resume(self, step_attempt_id: str) -> TaskExecutionContext:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_step_attempt",
                    default="Unknown step attempt: {step_attempt_id}",
                    step_attempt_id=step_attempt_id,
                )
            )
        task = self.store.get_task(attempt.task_id)
        if task is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_task_for_step_attempt",
                    default="Unknown task for step attempt: {step_attempt_id}",
                    step_attempt_id=step_attempt_id,
                )
            )
        context = dict(attempt.context or {})
        if bool(context.get("input_dirty")) and (
            str(attempt.waiting_reason or "") == "awaiting_approval"
            or str(attempt.status or "") == "awaiting_approval"
            or str(context.get("phase", "") or "") == "awaiting_approval"
        ):
            successor_context = dict(context)
            successor_context["execution_mode"] = "run"
            successor_context["phase"] = "planning"
            successor_context["input_dirty"] = False
            successor_context["supersedes_step_attempt_id"] = step_attempt_id
            successor_context["reentered_via"] = "input_dirty_approval"
            successor_context["recompile_required"] = True
            successor = self.store.create_step_attempt(
                task_id=attempt.task_id,
                step_id=attempt.step_id,
                attempt=int(attempt.attempt or 1) + 1,
                status="ready",
                context=successor_context,
                queue_priority=attempt.queue_priority,
            )
            superseded_context = dict(context)
            superseded_context["phase"] = "awaiting_approval"
            self.store.update_step(attempt.step_id, status="ready", finished_at=None)
            self.store.update_step_attempt(
                step_attempt_id,
                status="superseded",
                context=superseded_context,
                waiting_reason="input_changed_reenter_policy",
                superseded_by_step_attempt_id=successor.step_attempt_id,
                finished_at=time.time(),
            )
            self.store.append_event(
                event_type="step_attempt.superseded",
                entity_type="step_attempt",
                entity_id=step_attempt_id,
                task_id=attempt.task_id,
                step_id=attempt.step_id,
                actor="kernel",
                payload={
                    "step_attempt_id": step_attempt_id,
                    "superseded_by_step_attempt_id": successor.step_attempt_id,
                    "reason": "input_dirty_reenter_policy",
                    "approval_id": attempt.approval_id,
                },
            )
            self.store.update_task_status(task.task_id, "queued")
            return TaskExecutionContext(
                conversation_id=task.conversation_id,
                task_id=task.task_id,
                step_id=attempt.step_id,
                step_attempt_id=successor.step_attempt_id,
                source_channel=task.source_channel,
                policy_profile=task.policy_profile,
                workspace_root=str(successor_context.get("workspace_root", "") or ""),
                ingress_metadata=dict(successor_context.get("ingress_metadata", {}) or {}),
            )
        context["execution_mode"] = "resume"
        context["phase"] = "authorized_pre_exec"
        self.store.update_step(attempt.step_id, status="ready", finished_at=None)
        self.store.update_step_attempt(
            step_attempt_id,
            status="ready",
            context=context,
            waiting_reason=None,
            finished_at=None,
        )
        self.store.update_task_status(task.task_id, "queued")
        return TaskExecutionContext(
            conversation_id=task.conversation_id,
            task_id=task.task_id,
            step_id=attempt.step_id,
            step_attempt_id=step_attempt_id,
            source_channel=task.source_channel,
            policy_profile=task.policy_profile,
            workspace_root=str(context.get("workspace_root", "") or ""),
            ingress_metadata=dict(context.get("ingress_metadata", {}) or {}),
        )

    def decide_ingress(
        self,
        *,
        conversation_id: str,
        source_channel: str,
        raw_text: str,
        prompt: str,
        requested_by: str | None = "user",
        explicit_task_ref: str | None = None,
        reply_to_task_id: str | None = None,
        reply_to_ref: str | None = None,
        quoted_message_ref: str | None = None,
    ) -> IngressDecision:
        normalized = self._normalize_ingress_text(raw_text)
        self.ensure_conversation(conversation_id, source_channel=source_channel)
        ingress = self.store.create_ingress(
            conversation_id=conversation_id,
            source_channel=source_channel,
            raw_text=raw_text,
            normalized_text=normalized,
            actor=requested_by,
            prompt_ref=prompt,
            explicit_task_ref=explicit_task_ref,
            reply_to_ref=reply_to_ref,
            quoted_message_ref=quoted_message_ref,
            referenced_artifact_refs=self._extract_artifact_refs(raw_text, prompt),
        )
        planning = PlanningService(self.store)
        latest = self.store.get_last_task_for_conversation(conversation_id)
        conversation = self.store.get_conversation(conversation_id)
        open_tasks = self.store.list_open_tasks_for_conversation(conversation_id=conversation_id, limit=10)
        pending_approval = self.store.get_latest_pending_approval(conversation_id)
        shadow_binding = self._legacy_shadow_binding(
            normalized_text=normalized,
            open_tasks=open_tasks,
            explicit_task_ref=explicit_task_ref,
            reply_to_task_id=reply_to_task_id,
            pending_approval_task_id=pending_approval.task_id if pending_approval is not None else None,
        )
        if self._is_chat_only_message(normalized):
            self.store.update_ingress(
                ingress.ingress_id,
                status="bound",
                resolution="chat_only",
                rationale=self._augment_ingress_rationale(
                    {"reason_codes": ["chat_only_message"]},
                    resolution="chat_only",
                    chosen_task_id=None,
                    parent_task_id=None,
                    confidence=None,
                    margin=None,
                    reason_codes=["chat_only_message"],
                    shadow_binding=shadow_binding,
                ),
            )
            return IngressDecision(
                mode="start",
                intent="chat_only",
                reason="chat_only_message",
                resolution="chat_only",
                ingress_id=ingress.ingress_id,
                parent_task_id=None,
            )

        if self._is_explicit_new_task_message(normalized) and "顺便" not in normalized:
            self.store.update_ingress(
                ingress.ingress_id,
                status="bound",
                resolution="start_new_root",
                rationale=self._augment_ingress_rationale(
                    {"reason_codes": ["explicit_new_task_marker"]},
                    resolution="start_new_root",
                    chosen_task_id=None,
                    parent_task_id=None,
                    confidence=None,
                    margin=None,
                    reason_codes=["explicit_new_task_marker"],
                    shadow_binding=shadow_binding,
                ),
            )
            return IngressDecision(
                mode="start",
                intent="start_new_task",
                reason="explicit_new_task_marker",
                resolution="start_new_root",
                ingress_id=ingress.ingress_id,
                parent_task_id=None,
            )

        binding = self.ingress_router.bind(
            conversation=conversation,
            open_tasks=open_tasks,
            normalized_text=normalized,
            explicit_task_ref=explicit_task_ref,
            reply_to_task_id=reply_to_task_id,
            pending_approval_task_id=pending_approval.task_id if pending_approval is not None else None,
        )

        if binding.resolution == "append_note" and binding.chosen_task_id:
            self.store.update_ingress(
                ingress.ingress_id,
                status="bound",
                resolution="append_note",
                chosen_task_id=binding.chosen_task_id,
                confidence=binding.confidence,
                margin=binding.margin,
                rationale=self._augment_ingress_rationale(
                    {
                        "reason_codes": list(binding.reason_codes),
                        "candidates": self._serialize_binding_candidates(binding),
                    },
                    resolution="append_note",
                    chosen_task_id=binding.chosen_task_id,
                    parent_task_id=None,
                    confidence=binding.confidence,
                    margin=binding.margin,
                    reason_codes=list(binding.reason_codes),
                    shadow_binding=shadow_binding,
                    candidates=self._serialize_binding_candidates(binding),
                ),
            )
            note_event_seq = self.append_note(
                task_id=binding.chosen_task_id,
                source_channel=source_channel,
                raw_text=raw_text,
                prompt=prompt,
                requested_by=requested_by,
                ingress_id=ingress.ingress_id,
            )
            self._set_focus(conversation_id=conversation_id, task_id=binding.chosen_task_id, reason="ingress_bound")
            return self._binding_to_ingress_decision(
                ingress_id=ingress.ingress_id,
                binding=binding,
                mode="append_note",
                intent="continue_task",
                reason="matched_open_task",
                note_event_seq=note_event_seq,
            )

        if binding.resolution == "pending_disambiguation":
            self.store.update_ingress(
                ingress.ingress_id,
                status="pending_disambiguation",
                resolution="pending_disambiguation",
                confidence=binding.confidence,
                margin=binding.margin,
                rationale=self._augment_ingress_rationale(
                    {
                        "reason_codes": list(binding.reason_codes),
                        "candidates": self._serialize_binding_candidates(binding),
                    },
                    resolution="pending_disambiguation",
                    chosen_task_id=None,
                    parent_task_id=None,
                    confidence=binding.confidence,
                    margin=binding.margin,
                    reason_codes=list(binding.reason_codes),
                    shadow_binding=shadow_binding,
                    candidates=self._serialize_binding_candidates(binding),
                ),
            )
            return self._binding_to_ingress_decision(
                ingress_id=ingress.ingress_id,
                binding=binding,
                mode="start",
                intent="start_new_task",
                reason="pending_disambiguation",
            )

        if binding.resolution == "fork_child":
            self.store.update_ingress(
                ingress.ingress_id,
                status="bound",
                resolution="fork_child",
                parent_task_id=binding.parent_task_id,
                confidence=binding.confidence,
                margin=binding.margin,
                rationale=self._augment_ingress_rationale(
                    {
                        "reason_codes": list(binding.reason_codes),
                        "candidates": self._serialize_binding_candidates(binding),
                    },
                    resolution="fork_child",
                    chosen_task_id=None,
                    parent_task_id=binding.parent_task_id,
                    confidence=binding.confidence,
                    margin=binding.margin,
                    reason_codes=list(binding.reason_codes),
                    shadow_binding=shadow_binding,
                    candidates=self._serialize_binding_candidates(binding),
                ),
            )
            return self._binding_to_ingress_decision(
                ingress_id=ingress.ingress_id,
                binding=binding,
                mode="start",
                intent="start_new_task",
                reason="fork_child",
            )

        anchor = self.resolve_continuation_target(conversation_id=conversation_id, raw_text=normalized)
        if anchor is not None:
            self.store.update_ingress(
                ingress.ingress_id,
                status="bound",
                resolution="start_new_root",
                confidence=binding.confidence,
                margin=binding.margin,
                rationale=self._augment_ingress_rationale(
                    {
                        "reason_codes": ["matched_terminal_task"],
                        "anchor_task_id": anchor.get("anchor_task_id"),
                    },
                    resolution="start_new_root",
                    chosen_task_id=None,
                    parent_task_id=None,
                    confidence=binding.confidence,
                    margin=binding.margin,
                    reason_codes=["matched_terminal_task"],
                    shadow_binding=shadow_binding,
                ),
            )
            return IngressDecision(
                mode="start",
                intent="continue_task",
                reason="matched_terminal_task",
                resolution="start_new_root",
                ingress_id=ingress.ingress_id,
                continuation_anchor=anchor,
                anchor_task_id=anchor["anchor_task_id"],
                anchor_kind=anchor["anchor_kind"],
                anchor_reason=anchor["selection_reason"],
                confidence=binding.confidence,
                margin=binding.margin,
                candidates=self._serialize_binding_candidates(binding),
                parent_task_id=None,
            )
        if latest is not None and planning.state_for_task(latest.task_id).planning_mode:
            attempt = next(iter(self.store.list_step_attempts(task_id=latest.task_id, limit=1)), None)
            if attempt is not None and str(attempt.waiting_reason or "") == "awaiting_plan_confirmation":
                self.store.update_ingress(
                    ingress.ingress_id,
                    status="bound",
                    resolution="start_new_root",
                    rationale=self._augment_ingress_rationale(
                        {"reason_codes": ["planning_confirmation_gate"]},
                        resolution="start_new_root",
                        chosen_task_id=None,
                        parent_task_id=None,
                        confidence=None,
                        margin=None,
                        reason_codes=["planning_confirmation_gate"],
                        shadow_binding=shadow_binding,
                    ),
                )
                return IngressDecision(
                    mode="start",
                    intent="start_new_task",
                    reason="planning_confirmation_gate",
                    resolution="start_new_root",
                    ingress_id=ingress.ingress_id,
                    parent_task_id=None,
                )
        self.store.update_ingress(
            ingress.ingress_id,
            status="bound",
            resolution="start_new_root",
            confidence=binding.confidence,
            margin=binding.margin,
            rationale=self._augment_ingress_rationale(
                {
                    "reason_codes": list(binding.reason_codes),
                    "candidates": self._serialize_binding_candidates(binding),
                },
                resolution="start_new_root",
                chosen_task_id=None,
                parent_task_id=None,
                confidence=binding.confidence,
                margin=binding.margin,
                reason_codes=list(binding.reason_codes),
                shadow_binding=shadow_binding,
                candidates=self._serialize_binding_candidates(binding),
            ),
        )
        return IngressDecision(
            mode="start",
            intent="start_new_task",
            reason="conservative_new_task_fallback" if open_tasks else "no_active_task",
            resolution="start_new_root",
            ingress_id=ingress.ingress_id,
            confidence=binding.confidence,
            margin=binding.margin,
            candidates=self._serialize_binding_candidates(binding),
            parent_task_id=None,
        )

    def resolve_continuation_target(
        self,
        *,
        conversation_id: str,
        raw_text: str,
    ) -> dict[str, Any] | None:
        cleaned = self._normalize_ingress_text(raw_text)
        if not cleaned:
            return None
        has_explicit_marker = self._has_continue_marker(cleaned)
        has_ambiguous_marker = any(marker in cleaned for marker in _AMBIGUOUS_FOLLOWUP_MARKERS)
        query_tokens = {token for token in MemoryEngine._topic_tokens(cleaned) if len(token) >= 2}

        for task in self._terminal_continuation_tasks(conversation_id):
            candidate_texts = self._continuation_candidate_texts(task.task_id)
            if not candidate_texts:
                continue
            overlap = any(self._texts_overlap(cleaned, text, query_tokens=query_tokens) for text in candidate_texts)
            if not overlap:
                continue
            selection_reason = (
                "terminal_followup_marker_topic_overlap"
                if has_explicit_marker or has_ambiguous_marker
                else "terminal_topic_overlap"
            )
            return self._continuation_anchor(task.task_id, selection_reason=selection_reason)
        return None

    def append_note(
        self,
        *,
        task_id: str,
        source_channel: str,
        raw_text: str,
        prompt: str,
        normalized_payload: dict[str, Any] | None = None,
        requested_by: str | None = "user",
        ingress_id: str | None = None,
    ) -> int:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_task",
                    default="Unknown task: {task_id}",
                    task_id=task_id,
                )
            )
        self._mark_attempt_input_dirty(task_id=task_id, ingress_id=ingress_id, note_event_seq=None, emit_event=True)
        event_id = self.store.append_event(
            event_type="task.note.appended",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            actor=requested_by or "user",
            payload={
                "status": task.status,
                "source_channel": source_channel,
                "raw_text": raw_text,
                "prompt": prompt,
                **dict(normalized_payload or {}),
                "requested_by": requested_by,
                "appended_at": time.time(),
            },
        )
        events = self.store.list_events(task_id=task_id, limit=1)
        if events and events[-1]["event_id"] == event_id:
            self._mark_attempt_input_dirty(
                task_id=task_id,
                ingress_id=ingress_id,
                note_event_seq=int(events[-1]["event_seq"]),
                emit_event=False,
            )
            self._set_focus(conversation_id=task.conversation_id, task_id=task_id, reason="note_appended")
            return int(events[-1]["event_seq"])
        recent = self.store.list_events(task_id=task_id, limit=200)
        for event in reversed(recent):
            if event["event_id"] == event_id:
                self._mark_attempt_input_dirty(
                    task_id=task_id,
                    ingress_id=ingress_id,
                    note_event_seq=int(event["event_seq"]),
                    emit_event=False,
                )
                self._set_focus(conversation_id=task.conversation_id, task_id=task_id, reason="note_appended")
                return int(event["event_seq"])
        self._set_focus(conversation_id=task.conversation_id, task_id=task_id, reason="note_appended")
        return 0

    def finalize_result(
        self,
        ctx: TaskExecutionContext,
        *,
        status: str,
        output_ref: str | None = None,
        result_preview: str | None = None,
        result_text: str | None = None,
    ) -> None:
        now = time.time()
        self.store.update_step(ctx.step_id, status=status, output_ref=output_ref, finished_at=now)
        self.store.update_step_attempt(ctx.step_attempt_id, status=status, finished_at=now)
        payload: dict[str, Any] | None = None
        if result_preview or result_text:
            payload = {}
            if result_preview:
                payload["result_preview"] = result_preview
            if result_text:
                payload["result_text"] = result_text
        self.store.update_task_status(
            ctx.task_id,
            "completed" if status == "succeeded" else status,
            payload=payload,
        )
        self._refresh_focus_after_task_status(ctx.conversation_id, ctx.task_id)

    def mark_planning_ready(
        self,
        ctx: TaskExecutionContext,
        *,
        plan_artifact_ref: str | None,
        result_preview: str | None = None,
        result_text: str | None = None,
    ) -> None:
        now = time.time()
        self.store.update_step(
            ctx.step_id,
            status="blocked",
            output_ref=plan_artifact_ref,
            finished_at=now,
        )
        self.store.update_step_attempt(
            ctx.step_attempt_id,
            status="awaiting_plan_confirmation",
            waiting_reason="awaiting_plan_confirmation",
            finished_at=now,
        )
        payload: dict[str, Any] = {
            "planning_mode": True,
            "selected_plan_ref": plan_artifact_ref,
        }
        self.update_attempt_phase(ctx.step_attempt_id, phase="planning")
        if result_preview:
            payload["result_preview"] = result_preview
        if result_text:
            payload["result_text"] = result_text
        self.store.update_task_status(ctx.task_id, "blocked", payload=payload)
        self._set_focus(conversation_id=ctx.conversation_id, task_id=ctx.task_id, reason="planning_ready")

    def mark_blocked(self, ctx: TaskExecutionContext) -> None:
        self.mark_suspended(ctx, waiting_kind="awaiting_approval")

    def mark_suspended(self, ctx: TaskExecutionContext, *, waiting_kind: str) -> None:
        self.store.update_step(ctx.step_id, status="blocked")
        self.store.update_step_attempt(ctx.step_attempt_id, status=waiting_kind)
        self.store.update_task_status(ctx.task_id, "blocked")
        self.update_attempt_phase(ctx.step_attempt_id, phase=waiting_kind)
        self._set_focus(conversation_id=ctx.conversation_id, task_id=ctx.task_id, reason=waiting_kind)

    def pause_task(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_task",
                    default="Unknown task: {task_id}",
                    task_id=task_id,
                )
            )
        self.store.update_task_status(task_id, "paused")
        self._refresh_focus_after_task_status(task.conversation_id, task_id)

    def cancel_task(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_task",
                    default="Unknown task: {task_id}",
                    task_id=task_id,
                )
            )
        self.store.update_task_status(task_id, "cancelled")
        self._refresh_focus_after_task_status(task.conversation_id, task_id)

    def focus_task(self, conversation_id: str, task_id: str) -> IngressDecision | None:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_task",
                    default="Unknown task: {task_id}",
                    task_id=task_id,
                )
            )
        if task.conversation_id != conversation_id:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_task",
                    default="Unknown task: {task_id}",
                    task_id=task_id,
                )
            )
        self._set_focus(conversation_id=conversation_id, task_id=task_id, reason="explicit_task_switch")
        return self._resolve_pending_disambiguation(
            conversation_id=conversation_id,
            task_id=task_id,
            requested_by="user",
        )

    def reprioritize_task(self, task_id: str, *, priority: str) -> None:
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_task",
                    default="Unknown task: {task_id}",
                    task_id=task_id,
                )
            )
        with self.store._lock, self.store._conn:
            self.store._conn.execute(
                "UPDATE tasks SET priority = ?, updated_at = ? WHERE task_id = ?",
                (priority, time.time(), task_id),
            )
            self.store._append_event_tx(
                event_id=self.store._id("event"),
                event_type="task.reprioritized",
                entity_type="task",
                entity_id=task_id,
                task_id=task_id,
                actor="user",
                payload={"priority": priority},
            )

    def resume_attempt(self, step_attempt_id: str) -> TaskExecutionContext:
        return self.context_for_attempt(step_attempt_id)

    def resolve_text_command(self, conversation_id: str, text: str) -> tuple[str, str, str] | None:
        pending = self.store.get_latest_pending_approval(conversation_id)
        latest_task = self.store.get_last_task_for_conversation(conversation_id)
        latest_receipts = (
            self.store.list_receipts(task_id=latest_task.task_id, limit=1)
            if latest_task is not None
            else []
        )
        intent = parse_control_intent(
            text,
            pending_approval_id=pending.approval_id if pending is not None else None,
            latest_task_id=latest_task.task_id if latest_task is not None else None,
            latest_receipt_id=latest_receipts[0].receipt_id if latest_receipts else None,
        )
        if intent is None:
            return None
        return (intent.action, intent.target_id, intent.reason)

    def _resolve_pending_disambiguation(
        self,
        *,
        conversation_id: str,
        task_id: str,
        requested_by: str | None,
    ) -> IngressDecision | None:
        pending = next(
            iter(self.store.list_ingresses(conversation_id=conversation_id, status="pending_disambiguation", limit=1)),
            None,
        )
        if pending is None:
            return None
        rationale = dict(pending.rationale or {})
        reason_codes = ["user_disambiguated_focus_task"]
        for code in list(rationale.get("reason_codes", []) or []):
            normalized = str(code or "").strip()
            if normalized and normalized not in reason_codes:
                reason_codes.append(normalized)
        binding = BindingDecision(
            resolution="append_note",
            chosen_task_id=task_id,
            parent_task_id=None,
            confidence=1.0,
            margin=1.0,
            candidates=[],
            reason_codes=reason_codes,
        )
        self.store.update_ingress(
            pending.ingress_id,
            status="bound",
            resolution="append_note",
            chosen_task_id=task_id,
            confidence=1.0,
            margin=1.0,
            rationale={
                "reason_codes": reason_codes,
                "resolved_by": "explicit_task_switch",
                "previous_rationale": rationale,
            },
        )
        note_event_seq = self.append_note(
            task_id=task_id,
            source_channel=pending.source_channel,
            raw_text=pending.raw_text,
            prompt=str(pending.prompt_ref or pending.raw_text or ""),
            requested_by=requested_by or pending.actor or "user",
            ingress_id=pending.ingress_id,
        )
        self._set_focus(conversation_id=conversation_id, task_id=task_id, reason="pending_ingress_resolved")
        return self._binding_to_ingress_decision(
            ingress_id=pending.ingress_id,
            binding=binding,
            mode="append_note",
            intent="continue_task",
            reason="pending_disambiguation_resolved",
            note_event_seq=note_event_seq,
        )

    def _binding_to_ingress_decision(
        self,
        *,
        ingress_id: str,
        binding: BindingDecision,
        mode: str,
        intent: str,
        reason: str,
        note_event_seq: int | None = None,
    ) -> IngressDecision:
        return IngressDecision(
            mode=mode,
            intent=intent,
            reason=reason,
            resolution=binding.resolution,
            ingress_id=ingress_id,
            task_id=binding.chosen_task_id,
            note_event_seq=note_event_seq,
            confidence=binding.confidence,
            margin=binding.margin,
            reason_codes=list(binding.reason_codes),
            candidates=self._serialize_binding_candidates(binding),
            parent_task_id=binding.parent_task_id if binding.parent_task_id is not None else None,
        )

    def _legacy_shadow_binding(
        self,
        *,
        normalized_text: str,
        open_tasks: list[Any],
        explicit_task_ref: str | None,
        reply_to_task_id: str | None,
        pending_approval_task_id: str | None,
    ) -> BindingDecision:
        cleaned = self._normalize_ingress_text(normalized_text)
        if self._is_chat_only_message(cleaned):
            return BindingDecision(
                resolution="chat_only",
                confidence=0.95,
                margin=0.95,
                reason_codes=["legacy_chat_only_message"],
            )
        if self._is_explicit_new_task_message(cleaned):
            return BindingDecision(
                resolution="start_new_root",
                confidence=0.95,
                margin=0.95,
                reason_codes=["legacy_explicit_new_task_marker"],
            )
        if explicit_task_ref:
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=explicit_task_ref,
                confidence=1.0,
                margin=1.0,
                reason_codes=["legacy_explicit_task_ref"],
            )
        if reply_to_task_id:
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=reply_to_task_id,
                confidence=1.0,
                margin=1.0,
                reason_codes=["legacy_reply_target"],
            )
        if pending_approval_task_id and self.ingress_router._looks_like_approval_followup(cleaned):
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=pending_approval_task_id,
                confidence=0.98,
                margin=0.98,
                reason_codes=["legacy_pending_approval_correlation"],
            )
        latest_open = open_tasks[0] if open_tasks else None
        if latest_open is None:
            return BindingDecision(
                resolution="start_new_root",
                confidence=0.2,
                margin=0.2,
                reason_codes=["legacy_no_open_tasks"],
            )
        if self._looks_like_task_followup(cleaned, task_id=latest_open.task_id):
            return BindingDecision(
                resolution="append_note",
                chosen_task_id=latest_open.task_id,
                confidence=0.85,
                margin=0.85,
                reason_codes=["legacy_latest_open_followup"],
            )
        return BindingDecision(
            resolution="start_new_root",
            confidence=0.4,
            margin=0.4,
            reason_codes=["legacy_latest_open_no_match"],
        )

    @staticmethod
    def _binding_snapshot(
        *,
        resolution: str,
        chosen_task_id: str | None,
        parent_task_id: str | None,
        confidence: float | None,
        margin: float | None,
        reason_codes: list[str] | None,
        candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "resolution": resolution,
            "chosen_task_id": chosen_task_id,
            "parent_task_id": parent_task_id,
            "confidence": confidence,
            "margin": margin,
            "reason_codes": list(reason_codes or []),
        }
        if candidates:
            payload["candidates"] = list(candidates)
        return payload

    def _augment_ingress_rationale(
        self,
        base: dict[str, Any],
        *,
        resolution: str,
        chosen_task_id: str | None,
        parent_task_id: str | None,
        confidence: float | None,
        margin: float | None,
        reason_codes: list[str] | None,
        shadow_binding: BindingDecision | None,
        candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        rationale = dict(base or {})
        actual_binding = self._binding_snapshot(
            resolution=resolution,
            chosen_task_id=chosen_task_id,
            parent_task_id=parent_task_id,
            confidence=confidence,
            margin=margin,
            reason_codes=reason_codes,
            candidates=candidates,
        )
        rationale["actual_binding"] = actual_binding
        if shadow_binding is not None:
            shadow_payload = self._binding_snapshot(
                resolution=shadow_binding.resolution,
                chosen_task_id=shadow_binding.chosen_task_id,
                parent_task_id=shadow_binding.parent_task_id,
                confidence=shadow_binding.confidence,
                margin=shadow_binding.margin,
                reason_codes=list(shadow_binding.reason_codes),
                candidates=self._serialize_binding_candidates(shadow_binding),
            )
            shadow_payload["match_actual"] = (
                shadow_payload["resolution"] == actual_binding["resolution"]
                and shadow_payload["chosen_task_id"] == actual_binding["chosen_task_id"]
                and shadow_payload["parent_task_id"] == actual_binding["parent_task_id"]
            )
            rationale["shadow_binding"] = shadow_payload
        return rationale

    @staticmethod
    def _serialize_binding_candidates(binding: BindingDecision) -> list[dict[str, Any]]:
        return [
            {
                "task_id": item.task_id,
                "score": item.score,
                "reason_codes": list(item.reason_codes),
            }
            for item in binding.candidates
        ]

    def _bind_ingress_on_task_creation(
        self,
        *,
        conversation_id: str,
        task_id: str,
        parent_task_id: str | None,
        ingress_metadata: dict[str, Any],
    ) -> None:
        ingress_id = str(ingress_metadata.get("ingress_id", "") or "")
        if not ingress_id:
            return
        resolution = str(ingress_metadata.get("ingress_resolution", "") or "") or "start_new_root"
        self.store.update_ingress(
            ingress_id,
            status="bound",
            resolution=resolution,
            chosen_task_id=task_id,
            parent_task_id=parent_task_id,
            rationale={
                "reason_codes": list(ingress_metadata.get("binding_reason_codes", []) or []),
                "created_task_id": task_id,
            },
        )
        self._set_focus(conversation_id=conversation_id, task_id=task_id, reason="ingress_created_task")

    def _set_focus(self, *, conversation_id: str, task_id: str | None, reason: str) -> None:
        previous = self.store.get_conversation(conversation_id)
        previous_task_id = previous.focus_task_id if previous is not None else None
        previous_reason = str(previous.focus_reason or "") if previous is not None else ""
        if (
            task_id
            and previous_task_id == task_id
            and reason in {"ingress_bound", "note_appended"}
            and previous_reason
        ):
            return
        self.store.set_conversation_focus(conversation_id, task_id=task_id, reason=reason)
        if previous_task_id == task_id:
            return
        if task_id:
            self.store.append_event(
                event_type="conversation.focus.set",
                entity_type="conversation",
                entity_id=conversation_id,
                task_id=None,
                actor="kernel",
                payload={"focus_task_id": task_id, "reason": reason, "previous_focus_task_id": previous_task_id},
            )
        else:
            self.store.append_event(
                event_type="conversation.focus.cleared",
                entity_type="conversation",
                entity_id=conversation_id,
                task_id=None,
                actor="kernel",
                payload={"reason": reason, "previous_focus_task_id": previous_task_id},
            )

    def _refresh_focus_after_task_status(self, conversation_id: str, task_id: str) -> None:
        conversation = self.store.get_conversation(conversation_id)
        if conversation is None or conversation.focus_task_id != task_id:
            return
        next_focus = self.store.ensure_valid_focus(conversation_id)
        if next_focus == task_id:
            return
        self._set_focus(conversation_id=conversation_id, task_id=next_focus, reason="focus_task_terminal")

    def _mark_attempt_input_dirty(
        self,
        *,
        task_id: str,
        ingress_id: str | None,
        note_event_seq: int | None,
        emit_event: bool,
    ) -> None:
        attempt = next(
            (
                item
                for item in self.store.list_step_attempts(task_id=task_id, limit=20)
                if item.status in {"ready", "running", "awaiting_approval", "observing", "policy_pending"}
            ),
            None,
        )
        if attempt is None:
            return
        context = dict(attempt.context or {})
        was_dirty = bool(context.get("input_dirty"))
        context["input_dirty"] = True
        context["latest_bound_ingress_id"] = ingress_id or str(context.get("latest_bound_ingress_id", "") or "")
        if note_event_seq is not None:
            context["latest_note_event_seq"] = int(note_event_seq)
        context["input_dirty_at"] = time.time()
        self.store.update_step_attempt(attempt.step_attempt_id, context=context)
        if emit_event and not was_dirty:
            self.store.append_event(
                event_type="step_attempt.input_dirty",
                entity_type="step_attempt",
                entity_id=attempt.step_attempt_id,
                task_id=attempt.task_id,
                step_id=attempt.step_id,
                actor="kernel",
                payload={
                    "step_attempt_id": attempt.step_attempt_id,
                    "phase": str(context.get("phase", "") or ""),
                    "latest_bound_ingress_id": context.get("latest_bound_ingress_id"),
                    "latest_note_event_seq": context.get("latest_note_event_seq"),
                },
            )

    def update_attempt_phase(self, step_attempt_id: str, *, phase: str) -> None:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        context = dict(attempt.context or {})
        previous = str(context.get("phase", "") or "")
        if previous == phase:
            return
        context["phase"] = phase
        self.store.update_step_attempt(step_attempt_id, context=context)
        self.store.append_event(
            event_type="step_attempt.phase_changed",
            entity_type="step_attempt",
            entity_id=step_attempt_id,
            task_id=attempt.task_id,
            step_id=attempt.step_id,
            actor="kernel",
            payload={
                "step_attempt_id": step_attempt_id,
                "previous_phase": previous,
                "phase": phase,
            },
        )

    @staticmethod
    def _ingress_queue_priority(
        *,
        source_channel: str,
        requested_by: str | None,
        metadata: dict[str, Any],
    ) -> int:
        if str(metadata.get("resume_kind", "") or "") == "approval":
            return 90
        if source_channel in {"chat", "feishu", "cli"} or requested_by:
            return 100
        if source_channel in {"scheduler", "webhook"}:
            return 10
        return 0

    @staticmethod
    def _normalize_ingress_text(text: str) -> str:
        return " ".join(str(text or "").split()).strip()

    @staticmethod
    def _extract_artifact_refs(*values: str | None) -> list[str]:
        refs: list[str] = []
        for value in values:
            for match in _ARTIFACT_REF_RE.findall(str(value or "")):
                artifact_id = str(match or "").strip().lower()
                if artifact_id and artifact_id not in refs:
                    refs.append(artifact_id)
        return refs

    @classmethod
    def _is_chat_only_message(cls, text: str) -> bool:
        cleaned = cls._normalize_ingress_text(text)
        lowered = cleaned.lower()
        if not cleaned:
            return True
        if lowered in _GREETING_TEXTS or cleaned in _GREETING_TEXTS:
            return True
        return bool(_LOW_SIGNAL_RE.match(cleaned))

    @classmethod
    def _is_explicit_new_task_message(cls, text: str) -> bool:
        cleaned = cls._normalize_ingress_text(text)
        return any(marker in cleaned for marker in _EXPLICIT_NEW_TASK_MARKERS)

    @classmethod
    def _has_continue_marker(cls, text: str) -> bool:
        cleaned = cls._normalize_ingress_text(text)
        return any(marker in cleaned for marker in _CONTINUE_MARKERS)

    def _looks_like_task_followup(self, text: str, *, task_id: str) -> bool:
        cleaned = self._normalize_ingress_text(text)
        if not cleaned:
            return False
        if self._has_continue_marker(cleaned):
            return True
        if any(marker in cleaned for marker in _AMBIGUOUS_FOLLOWUP_MARKERS):
            return True
        context_texts = self._task_context_texts(task_id)
        query_tokens = {token for token in MemoryEngine._topic_tokens(cleaned) if len(token) >= 2}
        for context_text in context_texts:
            if not context_text:
                continue
            if MemoryEngine._shares_topic(context_text, cleaned):
                return True
            context_tokens = {token for token in MemoryEngine._topic_tokens(context_text) if len(token) >= 2}
            if query_tokens & context_tokens:
                return True
            if any(token in context_text for token in query_tokens):
                return True
            if any(token in cleaned for token in context_tokens if len(token) >= 4):
                return True
        return False

    def _task_context_texts(self, task_id: str) -> list[str]:
        task = self.store.get_task(task_id)
        texts: list[str] = []
        if task is not None:
            texts.extend([str(task.title or ""), str(task.goal or "")])
        for event in reversed(self.store.list_events(task_id=task_id, limit=50)):
            if event["event_type"] != "task.note.appended":
                continue
            payload = dict(event["payload"] or {})
            note_text = self._sanitize_context_text(
                str(payload.get("raw_text") or payload.get("inline_excerpt") or "")
            )
            if note_text:
                texts.append(note_text)
            if len(texts) >= 6:
                break
        return texts

    def _terminal_continuation_tasks(self, conversation_id: str) -> list[Any]:
        tasks = self.store.list_tasks(conversation_id=conversation_id, limit=20)
        candidates: list[Any] = []
        for task in tasks:
            if task.status not in TERMINAL_TASK_STATUSES:
                continue
            projection = self.store.build_task_projection(task.task_id)
            step_kinds = {str(step.get("kind") or "") for step in projection.get("steps", {}).values()}
            if step_kinds and not (step_kinds & {"respond", "plan"}):
                continue
            candidates.append(task)
            if len(candidates) >= 5:
                break
        return candidates

    def _continuation_candidate_texts(self, task_id: str) -> list[str]:
        task = self.store.get_task(task_id)
        if task is None:
            return []
        texts: list[str] = [str(task.title or ""), str(task.goal or "")]
        for event in reversed(self.store.list_events(task_id=task_id, limit=50)):
            if event["event_type"] != "task.note.appended":
                continue
            payload = dict(event["payload"] or {})
            note_text = clean_runtime_text(payload.get("raw_text") or payload.get("inline_excerpt") or "")
            if note_text:
                texts.append(note_text)
            if len(texts) >= 6:
                break
        anchor = self._continuation_anchor(task_id, selection_reason="")
        outcome_summary = str(anchor.get("outcome_summary", "") or "")
        if outcome_summary:
            texts.append(outcome_summary)
        return [text for text in texts if text]

    def _continuation_anchor(self, task_id: str, *, selection_reason: str) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        if task is None:
            return {}
        events = self.store.list_events(task_id=task_id, limit=500)
        outcome = build_task_outcome(
            store=self.store,
            task_id=task_id,
            status=str(task.status or ""),
            events=events,
        ) or {}
        return {
            "anchor_task_id": task_id,
            "anchor_kind": "completed_outcome",
            "selection_reason": selection_reason,
            "anchor_title": str(task.title or ""),
            "outcome_status": str(outcome.get("status", task.status) or task.status),
            "outcome_summary": str(outcome.get("outcome_summary", "") or ""),
            "source_artifact_refs": list(outcome.get("source_artifact_refs", []) or []),
        }

    @staticmethod
    def _texts_overlap(text: str, candidate_text: str, *, query_tokens: set[str]) -> bool:
        if not candidate_text:
            return False
        if MemoryEngine._shares_topic(candidate_text, text):
            return True
        candidate_tokens = {token for token in MemoryEngine._topic_tokens(candidate_text) if len(token) >= 2}
        if query_tokens & candidate_tokens:
            return True
        if any(token in candidate_text for token in query_tokens):
            return True
        return any(token in text for token in candidate_tokens if len(token) >= 4)

    @staticmethod
    def _sanitize_context_text(text: str) -> str:
        cleaned = _SESSION_TIME_RE.sub("", str(text or ""))
        cleaned = _FEISHU_TAG_RE.sub("", cleaned)
        cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
        return cleaned.strip()
