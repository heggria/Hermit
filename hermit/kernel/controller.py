from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from hermit.builtin.memory.engine import MemoryEngine
from hermit.i18n import resolve_locale, tr
from hermit.kernel.context import TaskExecutionContext
from hermit.kernel.control_intents import parse_control_intent
from hermit.kernel.planning import PlanningService
from hermit.kernel.store import KernelStore

_AUTO_PARENT = object()
_LOW_SIGNAL_RE = re.compile(r"^[\s\?\uff1f!！,，。\.~～…]+$")
_SESSION_TIME_RE = re.compile(r"<session_time>.*?</session_time>\s*", re.DOTALL)
_FEISHU_TAG_RE = re.compile(r"<feishu_[^>]+>.*?</feishu_[^>]+>\s*", re.DOTALL)
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
    task_id: str | None = None
    note_event_seq: int | None = None
    parent_task_id: str | None | object = _AUTO_PARENT


class TaskController:
    def __init__(self, store: KernelStore) -> None:
        self.store = store

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
    ) -> TaskExecutionContext:
        self.ensure_conversation(conversation_id, source_channel=source_channel)
        parent = self.store.get_last_task_for_conversation(conversation_id)
        if parent_task_id is _AUTO_PARENT:
            resolved_parent = parent.task_id if parent else None
        else:
            resolved_parent = parent_task_id
        task = self.store.create_task(
            conversation_id=conversation_id,
            title=(goal.strip() or self._t("kernel.controller.task.default_title", default="Hermit task"))[:120],
            goal=goal,
            source_channel=source_channel,
            parent_task_id=resolved_parent,
            policy_profile=policy_profile,
            requested_by=requested_by,
        )
        step = self.store.create_step(task_id=task.task_id, kind=kind, status="running")
        attempt_context = {"note_cursor_event_seq": 0}
        if workspace_root:
            attempt_context["workspace_root"] = workspace_root
        attempt = self.store.create_step_attempt(
            task_id=task.task_id,
            step_id=step.step_id,
            status="running",
            context=attempt_context,
        )
        return TaskExecutionContext(
            conversation_id=conversation_id,
            task_id=task.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            source_channel=source_channel,
            workspace_root=workspace_root,
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
        task = self.store.create_task(
            conversation_id=conversation_id,
            title=(goal.strip() or self._t("kernel.controller.task.default_title", default="Hermit task"))[:120],
            goal=goal,
            source_channel=source_channel,
            status="queued",
            parent_task_id=resolved_parent,
            policy_profile=policy_profile,
            requested_by=requested_by,
        )
        step = self.store.create_step(task_id=task.task_id, kind=kind, status="ready")
        attempt_context = {
            "note_cursor_event_seq": 0,
            "ingress_metadata": dict(ingress_metadata or {}),
            "execution_mode": "run",
        }
        if workspace_root:
            attempt_context["workspace_root"] = workspace_root
        attempt = self.store.create_step_attempt(
            task_id=task.task_id,
            step_id=step.step_id,
            status="ready",
            context=attempt_context,
        )
        return TaskExecutionContext(
            conversation_id=conversation_id,
            task_id=task.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            source_channel=source_channel,
            policy_profile=policy_profile,
            workspace_root=workspace_root,
            ingress_metadata=dict(ingress_metadata or {}),
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
            "ingress_metadata": dict(ingress_metadata or {}),
        }
        if workspace_root:
            context["workspace_root"] = workspace_root
        attempt = self.store.create_step_attempt(
            task_id=task_id,
            step_id=step.step_id,
            status=status,
            context=context,
        )
        self.store.update_task_status(task_id, "running" if status == "running" else "queued")
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
        context["execution_mode"] = "resume"
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
    ) -> IngressDecision:
        normalized = self._normalize_ingress_text(raw_text)
        planning = PlanningService(self.store)
        latest = self.store.get_last_task_for_conversation(conversation_id)
        if latest is not None and planning.state_for_task(latest.task_id).planning_mode:
            attempt = next(iter(self.store.list_step_attempts(task_id=latest.task_id, limit=1)), None)
            if attempt is not None and str(attempt.waiting_reason or "") == "awaiting_plan_confirmation":
                return IngressDecision(mode="start", intent="start_new_task", reason="planning_confirmation_gate")
        active = self.active_task_for_conversation(conversation_id)
        if active is None:
            intent = "chat_only" if self._is_chat_only_message(normalized) else "start_new_task"
            return IngressDecision(mode="start", intent=intent, reason="no_active_task", parent_task_id=None)
        if self._is_chat_only_message(normalized):
            return IngressDecision(mode="start", intent="chat_only", reason="chat_only_message", parent_task_id=None)
        if self._is_explicit_new_task_message(normalized):
            return IngressDecision(mode="start", intent="start_new_task", reason="explicit_new_task_marker", parent_task_id=None)
        if self._looks_like_task_followup(normalized, task_id=active.task_id):
            note_event_seq = self.append_note(
                task_id=active.task_id,
                source_channel=source_channel,
                raw_text=raw_text,
                prompt=prompt,
                requested_by=requested_by,
            )
            return IngressDecision(
                mode="append_note",
                intent="continue_task",
                reason="matched_active_task",
                task_id=active.task_id,
                note_event_seq=note_event_seq,
            )
        return IngressDecision(
            mode="start",
            intent="start_new_task",
            reason="conservative_new_task_fallback",
            parent_task_id=None,
        )

    def append_note(
        self,
        *,
        task_id: str,
        source_channel: str,
        raw_text: str,
        prompt: str,
        normalized_payload: dict[str, Any] | None = None,
        requested_by: str | None = "user",
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
            return int(events[-1]["event_seq"])
        recent = self.store.list_events(task_id=task_id, limit=200)
        for event in reversed(recent):
            if event["event_id"] == event_id:
                return int(event["event_seq"])
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
        if result_preview:
            payload["result_preview"] = result_preview
        if result_text:
            payload["result_text"] = result_text
        self.store.update_task_status(ctx.task_id, "blocked", payload=payload)

    def mark_blocked(self, ctx: TaskExecutionContext) -> None:
        self.mark_suspended(ctx, waiting_kind="awaiting_approval")

    def mark_suspended(self, ctx: TaskExecutionContext, *, waiting_kind: str) -> None:
        self.store.update_step(ctx.step_id, status="blocked")
        self.store.update_step_attempt(ctx.step_attempt_id, status=waiting_kind)
        self.store.update_task_status(ctx.task_id, "blocked")

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

    @staticmethod
    def _normalize_ingress_text(text: str) -> str:
        return " ".join(str(text or "").split()).strip()

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

    @staticmethod
    def _sanitize_context_text(text: str) -> str:
        cleaned = _SESSION_TIME_RE.sub("", str(text or ""))
        cleaned = _FEISHU_TAG_RE.sub("", cleaned)
        cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
        return cleaned.strip()
