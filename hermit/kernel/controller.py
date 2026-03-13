from __future__ import annotations

import time
from dataclasses import dataclass

from hermit.i18n import resolve_locale, tr
from hermit.kernel.context import TaskExecutionContext
from hermit.kernel.control_intents import parse_control_intent
from hermit.kernel.store import KernelStore

_AUTO_PARENT = object()


@dataclass(frozen=True)
class IngressDecision:
    mode: str
    task_id: str | None = None
    note_event_seq: int | None = None


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
        if task.status in {"running", "blocked"}:
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
        active = self.active_task_for_conversation(conversation_id)
        if active is None:
            return IngressDecision(mode="start")
        note_seq = self.append_note(
            task_id=active.task_id,
            source_channel=source_channel,
            raw_text=raw_text,
            prompt=prompt,
            requested_by=requested_by,
        )
        return IngressDecision(mode="append_note", task_id=active.task_id, note_event_seq=note_seq)

    def append_note(
        self,
        *,
        task_id: str,
        source_channel: str,
        raw_text: str,
        prompt: str,
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
    ) -> None:
        now = time.time()
        self.store.update_step(ctx.step_id, status=status, output_ref=output_ref, finished_at=now)
        self.store.update_step_attempt(ctx.step_attempt_id, status=status, finished_at=now)
        payload = {"result_preview": result_preview} if result_preview else None
        self.store.update_task_status(
            ctx.task_id,
            "completed" if status == "succeeded" else status,
            payload=payload,
        )

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
