from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from hermit.infra.system.i18n import resolve_locale, tr, tr_list_all_locales
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.coordination.data_flow import StepDataFlowService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.constants import _FEISHU_META_RE as _FEISHU_TAG_RE
from hermit.kernel.task.constants import _SESSION_TIME_RE
from hermit.kernel.task.models.records import TaskRecord
from hermit.kernel.task.services.ingress_router import BindingDecision, IngressRouter
from hermit.kernel.task.services.planning import PlanningService
from hermit.kernel.task.state.continuation import (
    has_ambiguous_followup_marker,
    has_continue_marker,
    has_explicit_new_task_marker,
    normalize_text,
    texts_overlap,
)
from hermit.kernel.task.state.control_intents import parse_control_intent
from hermit.kernel.task.state.outcomes import (
    TERMINAL_TASK_STATUSES,
    build_task_outcome,
    clean_runtime_text,
)
from hermit.kernel.task.state.transitions import validate_task_transition

_AUTO_PARENT = object()
AUTO_PARENT = _AUTO_PARENT  # Public alias for use outside this module
_LOW_SIGNAL_RE = re.compile(r"^[\s\?\uff1f!！,，。\.~～…]+$")
_ARTIFACT_REF_RE = re.compile(r"\bartifact_[a-z0-9]{6,}\b", re.IGNORECASE)


def _greeting_texts() -> set[str]:
    return set(tr_list_all_locales("kernel.nlp.greeting_texts"))


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
    def __init__(
        self,
        store: KernelStore,
        workspace_lease_service: Any | None = None,
    ) -> None:
        self.store = store
        self.ingress_router = IngressRouter(store)
        self._workspace_lease_service = workspace_lease_service

    def _t(self, message_key: str, *, default: str | None = None, **kwargs: object) -> str:
        return tr(message_key, locale=resolve_locale(), default=default, **kwargs)

    def _runtime_snapshot_payload(self, attempt: Any) -> dict[str, Any]:
        resume_from_ref = str(getattr(attempt, "resume_from_ref", "") or "").strip()
        if resume_from_ref:
            artifact = self.store.get_artifact(resume_from_ref)
            if artifact is not None:
                try:
                    snapshot: Any = json.loads(Path(str(artifact.uri)).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    snapshot = {}
                if isinstance(snapshot, dict):
                    payload: Any = cast(dict[str, Any], snapshot).get("payload")
                    if isinstance(payload, dict):
                        return dict(cast(dict[str, Any], payload))
        context = dict(getattr(attempt, "context", {}) or {})
        snapshot_raw: Any = cast(dict[str, Any], context).get("runtime_snapshot") or {}
        snapshot_dict = cast(dict[str, Any], snapshot_raw) if isinstance(snapshot_raw, dict) else {}
        payload_raw: Any = snapshot_dict.get("payload")
        return dict(cast(dict[str, Any], payload_raw)) if isinstance(payload_raw, dict) else {}

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

    def ensure_conversation(
        self, conversation_id: str, *, source_channel: str | None = None
    ) -> None:
        self.store.ensure_conversation(
            conversation_id,
            source_channel=source_channel or self.source_from_session(conversation_id),
        )

    def latest_task(self, conversation_id: str) -> TaskRecord | None:
        return self.store.get_last_task_for_conversation(conversation_id)

    def active_task_for_conversation(self, conversation_id: str) -> TaskRecord | None:
        focus_task_id = self.store.ensure_valid_focus(conversation_id)
        task = self.store.get_task(focus_task_id) if focus_task_id else None
        if task is None:
            task = self.store.get_last_task_for_conversation(conversation_id)
        if task is None:
            return None
        if task.status == "blocked":
            attempt = next(iter(self.store.list_step_attempts(task_id=task.task_id, limit=1)), None)
            if (
                attempt is not None
                and str(attempt.status_reason or "") == "awaiting_plan_confirmation"
            ):
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
        acceptance_criteria: list[str] | None = None,
    ) -> TaskExecutionContext:
        self.ensure_conversation(conversation_id, source_channel=source_channel)
        parent = self.store.get_last_task_for_conversation(conversation_id)
        if parent_task_id is _AUTO_PARENT:
            resolved_parent: str | None = parent.task_id if parent else None
        else:
            resolved_parent = str(parent_task_id) if parent_task_id is not None else None
        metadata = dict(ingress_metadata or {})
        task = self.store.create_task(
            conversation_id=conversation_id,
            title=(
                goal.strip()
                or self._t("kernel.controller.task.default_title", default="Hermit task")
            )[:120],
            goal=goal,
            source_channel=source_channel,
            parent_task_id=resolved_parent,
            policy_profile=policy_profile,
            requested_by=requested_by,
            continuation_anchor=dict(metadata.get("continuation_anchor", {}) or {}) or None,
            acceptance_criteria=acceptance_criteria,
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
            queue_priority=self._ingress_queue_priority(
                source_channel=source_channel, requested_by=requested_by, metadata=metadata
            ),
        )
        self._bind_ingress_on_task_creation(
            conversation_id=conversation_id,
            task_id=task.task_id,
            parent_task_id=resolved_parent,
            ingress_metadata=metadata,
        )
        self._set_focus(
            conversation_id=conversation_id, task_id=task.task_id, reason="task_started"
        )
        return TaskExecutionContext(
            conversation_id=conversation_id,
            task_id=task.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            source_channel=source_channel,
            actor_principal_id=(task.requested_by_principal_id or requested_by or "principal_user"),
            policy_profile=policy_profile,
            workspace_root=workspace_root,
            ingress_metadata=metadata,
        )

    def start_dag_task(
        self,
        *,
        conversation_id: str,
        goal: str,
        source_channel: str,
        nodes: list[Any],
        policy_profile: str = "default",
        workspace_root: str = "",
        requested_by: str | None = None,
        ingress_metadata: dict[str, Any] | None = None,
        acceptance_criteria: list[str] | None = None,
        team_id: str | None = None,
    ) -> tuple[TaskExecutionContext, Any, dict[str, str], list[TaskExecutionContext]]:
        """Create a task with a DAG of steps.

        Returns (ctx_for_first_root, DAGDefinition, key→step_id mapping, all_root_contexts).

        Fix 6: all_root_contexts contains one TaskExecutionContext per root node,
        enabling callers to dispatch multiple roots in parallel for multi-root DAGs.
        """
        from hermit.kernel.task.services.dag_builder import StepDAGBuilder

        self.ensure_conversation(conversation_id, source_channel=source_channel)
        metadata = dict(ingress_metadata or {})
        task = self.store.create_task(
            conversation_id=conversation_id,
            title=(goal.strip() or "DAG task")[:120],
            goal=goal,
            source_channel=source_channel,
            policy_profile=policy_profile,
            requested_by=requested_by,
            acceptance_criteria=acceptance_criteria,
            team_id=team_id,
        )
        builder = StepDAGBuilder(self.store)
        dag, key_to_step_id = builder.build_and_materialize(
            task.task_id,
            nodes,
            ingress_metadata=metadata,
            workspace_root=workspace_root,
        )
        self.store.update_task_status(task.task_id, "queued")

        # Fix 6: build contexts for ALL roots, not just the first one.
        all_root_contexts: list[TaskExecutionContext] = []
        for root_key in dag.roots:
            root_step_id = key_to_step_id[root_key]
            root_attempts = self.store.list_step_attempts(step_id=root_step_id, limit=1)
            root_attempt = root_attempts[0] if root_attempts else None
            all_root_contexts.append(
                TaskExecutionContext(
                    conversation_id=conversation_id,
                    task_id=task.task_id,
                    step_id=root_step_id,
                    step_attempt_id=root_attempt.step_attempt_id if root_attempt else "",
                    source_channel=source_channel,
                    actor_principal_id=(
                        task.requested_by_principal_id or requested_by or "principal_user"
                    ),
                    policy_profile=policy_profile,
                    workspace_root=workspace_root,
                    ingress_metadata=metadata,
                )
            )

        ctx = all_root_contexts[0]
        self._set_focus(
            conversation_id=conversation_id, task_id=task.task_id, reason="dag_task_started"
        )
        return ctx, dag, key_to_step_id, all_root_contexts

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
        team_id: str | None = None,
    ) -> TaskExecutionContext:
        self.store.ensure_conversation(
            conversation_id,
            source_channel=source_channel,
            source_ref=source_ref,
        )
        parent = self.store.get_last_task_for_conversation(conversation_id)
        if parent_task_id is _AUTO_PARENT:
            resolved_parent: str | None = parent.task_id if parent else None
        else:
            resolved_parent = str(parent_task_id) if parent_task_id is not None else None
        metadata = dict(ingress_metadata or {})
        task = self.store.create_task(
            conversation_id=conversation_id,
            title=(
                goal.strip()
                or self._t("kernel.controller.task.default_title", default="Hermit task")
            )[:120],
            goal=goal,
            source_channel=source_channel,
            status="queued",
            parent_task_id=resolved_parent,
            policy_profile=policy_profile,
            requested_by=requested_by,
            continuation_anchor=dict(metadata.get("continuation_anchor", {}) or {}) or None,
            team_id=team_id,
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
            queue_priority=self._ingress_queue_priority(
                source_channel=source_channel, requested_by=requested_by, metadata=metadata
            ),
        )
        self._bind_ingress_on_task_creation(
            conversation_id=conversation_id,
            task_id=task.task_id,
            parent_task_id=resolved_parent,
            ingress_metadata=metadata,
        )
        self._set_focus(
            conversation_id=conversation_id, task_id=task.task_id, reason="task_enqueued"
        )
        return TaskExecutionContext(
            conversation_id=conversation_id,
            task_id=task.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            source_channel=source_channel,
            actor_principal_id=(task.requested_by_principal_id or requested_by or "principal_user"),
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
            actor_principal_id=(
                task.requested_by_principal_id or task.owner_principal_id or "principal_user"
            ),
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
            actor_principal_id=(
                task.requested_by_principal_id or task.owner_principal_id or "principal_user"
            ),
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
            str(attempt.status_reason or "") == "awaiting_approval"
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
            successor_context["reentry_required"] = True
            successor_context["reentry_boundary"] = "policy_recompile"
            successor_context["reentry_reason"] = "input_dirty"
            successor_context["reentry_requested_at"] = time.time()
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
                status_reason="input_changed_reenter_policy",
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
                actor_principal_id=(
                    task.requested_by_principal_id or task.owner_principal_id or "principal_user"
                ),
                policy_profile=task.policy_profile,
                workspace_root=str(successor_context.get("workspace_root", "") or ""),
                ingress_metadata=dict(successor_context.get("ingress_metadata", {}) or {}),
            )
        context["execution_mode"] = "resume"
        context["phase"] = "authorized_pre_exec"
        context["reentry_required"] = False
        context["reentry_resolved_at"] = time.time()
        self.store.update_step(attempt.step_id, status="ready", finished_at=None)
        self.store.update_step_attempt(
            step_attempt_id,
            status="ready",
            context=context,
            status_reason=None,
            finished_at=None,
        )
        self.store.update_task_status(task.task_id, "queued")
        return TaskExecutionContext(
            conversation_id=task.conversation_id,
            task_id=task.task_id,
            step_id=attempt.step_id,
            step_attempt_id=step_attempt_id,
            source_channel=task.source_channel,
            actor_principal_id=(
                task.requested_by_principal_id or task.owner_principal_id or "principal_user"
            ),
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
        open_tasks = self.store.list_open_tasks_for_conversation(
            conversation_id=conversation_id, limit=10
        )
        pending_approval = self.store.get_latest_pending_approval(conversation_id)
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

        _branch_primaries = tr_list_all_locales("kernel.nlp.continuation.branch_primary")
        if self._is_explicit_new_task_message(normalized) and not any(
            kw in normalized for kw in _branch_primaries
        ):
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
            pending_approval_task_id=pending_approval.task_id
            if pending_approval is not None
            else None,
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
            self._set_focus(
                conversation_id=conversation_id,
                task_id=binding.chosen_task_id,
                reason="ingress_bound",
            )
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

        anchor = self.resolve_continuation_target(
            conversation_id=conversation_id, raw_text=normalized
        )
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
            attempt = next(
                iter(self.store.list_step_attempts(task_id=latest.task_id, limit=1)), None
            )
            if (
                attempt is not None
                and str(attempt.status_reason or "") == "awaiting_plan_confirmation"
            ):
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
        has_ambiguous_marker = has_ambiguous_followup_marker(cleaned)

        for task in self._terminal_continuation_tasks(conversation_id):
            candidate_texts = self._continuation_candidate_texts(task.task_id)
            if not candidate_texts:
                continue
            overlap = any(texts_overlap(cleaned, text) for text in candidate_texts)
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
        self._try_upgrade_to_steering(
            task_id=task_id, raw_text=raw_text, source_channel=source_channel
        )
        self._mark_attempt_input_dirty(
            task_id=task_id, ingress_id=ingress_id, note_event_seq=None, emit_event=True
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
            self._mark_attempt_input_dirty(
                task_id=task_id,
                ingress_id=ingress_id,
                note_event_seq=int(events[-1]["event_seq"]),
                emit_event=False,
            )
            self._set_focus(
                conversation_id=task.conversation_id, task_id=task_id, reason="note_appended"
            )
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
                self._set_focus(
                    conversation_id=task.conversation_id, task_id=task_id, reason="note_appended"
                )
                return int(event["event_seq"])
        self._set_focus(
            conversation_id=task.conversation_id, task_id=task_id, reason="note_appended"
        )
        return 0

    def _try_upgrade_to_steering(self, *, task_id: str, raw_text: str, source_channel: str) -> None:
        """If raw_text starts with /steer, create a SteeringDirective."""
        stripped = raw_text.strip()
        if not stripped.startswith("/steer"):
            return
        body = stripped[len("/steer") :].strip()
        steering_type = "scope"
        if body.startswith("--type "):
            parts = body[len("--type ") :].split(None, 1)
            if len(parts) == 2:
                steering_type, body = parts[0], parts[1]
            elif len(parts) == 1:
                steering_type = parts[0]
                body = ""
        from hermit.kernel.signals.models import SteeringDirective
        from hermit.kernel.signals.steering import SteeringProtocol

        directive = SteeringDirective(
            task_id=task_id,
            steering_type=steering_type,
            directive=body,
            issued_by="user" if source_channel != "cli" else "operator",
        )
        protocol = SteeringProtocol(self.store)
        protocol.issue(directive)

    def _apply_acknowledged_steerings(self, task_id: str) -> None:
        """Auto-apply acknowledged steerings when task is finalized."""
        if not hasattr(self.store, "active_steerings_for_task"):
            return
        directives = self.store.active_steerings_for_task(task_id)
        for d in directives:
            if d.disposition == "acknowledged":
                self.store.update_steering_disposition(
                    d.directive_id, "applied", applied_at=time.time()
                )

    def finalize_result(
        self,
        ctx: TaskExecutionContext,
        *,
        status: str,
        output_ref: str | None = None,
        result_preview: str | None = None,
        result_text: str | None = None,
    ) -> None:
        # Atomic CAS guard: prevent double-finalization from concurrent workers.
        # try_finalize_step_attempt() does a conditional UPDATE that only succeeds
        # if the attempt is not already in a terminal state, eliminating the
        # TOCTOU window that caused duplicate DAG activations.
        now = time.time()
        if not self.store.try_finalize_step_attempt(
            ctx.step_attempt_id, status=status, finished_at=now
        ):
            # Step already finalized (e.g. by reconciliation executor), but we
            # may still have a result_text from the LLM's post-tool-call
            # response.  Append it to the task event so the WebUI can display
            # the answer.
            if result_text:
                task = self.store.get_task(ctx.task_id)
                if task is not None and task.status in ("completed", "succeeded"):
                    self.store.append_event(
                        event_type="task.result_text_attached",
                        entity_type="task",
                        entity_id=ctx.task_id,
                        task_id=ctx.task_id,
                        actor="kernel",
                        payload={
                            "result_text": result_text,
                            "result_preview": result_preview or result_text[:200],
                        },
                    )
            return
        self.store.update_step(ctx.step_id, status=status, output_ref=output_ref, finished_at=now)
        payload: dict[str, Any] | None = None
        if result_preview or result_text:
            payload = {}
            if result_preview:
                payload["result_preview"] = result_preview
            if result_text:
                payload["result_text"] = result_text
        self._apply_acknowledged_steerings(ctx.task_id)

        if status in ("succeeded", "completed", "skipped"):
            activated_step_ids = self.store.activate_waiting_dependents(ctx.task_id, ctx.step_id)
            # Fix 2: auto-inject input_bindings for newly activated steps.
            # Fix 3: load key_to_step_id from DB via node_key so symbolic bindings
            #        resolve even when the in-memory mapping is not available
            #        (e.g. across process restarts or in a separate worker).
            if activated_step_ids:
                _data_flow = StepDataFlowService(self.store)
                key_to_step_id = self.store.get_key_to_step_id(ctx.task_id)
                for activated_step_id in activated_step_ids:
                    activated_attempts = self.store.list_step_attempts(
                        step_id=activated_step_id, status="ready", limit=1
                    )
                    if activated_attempts:
                        resolved = _data_flow.resolve_inputs(
                            ctx.task_id, activated_step_id, key_to_step_id=key_to_step_id
                        )
                        if resolved:
                            _data_flow.inject_resolved_inputs(
                                activated_attempts[0].step_attempt_id, resolved
                            )
        elif status in ("failed", "needs_attention"):
            # Fix 1: max_attempts retry — use retry_step() for atomic attempt increment
            #        instead of raw _get_conn() calls.
            # needs_attention (dispatch denial, uncertain outcome) is treated as
            # failure for DAG propagation so the task fails fast instead of
            # hanging until the staleness guard intervenes.
            step = self.store.get_step(ctx.step_id)
            if step is not None and step.attempt < step.max_attempts:
                self.store.retry_step(ctx.task_id, ctx.step_id)
            else:
                self.store.propagate_step_failure(ctx.task_id, ctx.step_id)

        if self.store.has_non_terminal_steps(ctx.task_id):
            task_status = "running"
        else:
            task_status = "completed" if status in ("succeeded", "completed", "skipped") else status
        # Release workspace leases when task reaches terminal state
        if task_status in TERMINAL_TASK_STATUSES and self._workspace_lease_service is not None:
            try:
                released = self._workspace_lease_service.release_all_for_task(ctx.task_id)
                if released:
                    self.store.append_event(
                        event_type="workspace.task_terminal_cleanup",
                        entity_type="task",
                        entity_id=ctx.task_id,
                        task_id=ctx.task_id,
                        actor="kernel",
                        payload={
                            "released_lease_ids": released,
                            "task_status": task_status,
                        },
                    )
            except Exception:
                import structlog

                structlog.get_logger().warning(
                    "finalize_result_lease_release_failed",
                    task_id=ctx.task_id,
                    exc_info=True,
                )

        self.store.update_task_status(ctx.task_id, task_status, payload=payload)
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
            status_reason="awaiting_plan_confirmation",
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
        self._set_focus(
            conversation_id=ctx.conversation_id, task_id=ctx.task_id, reason="planning_ready"
        )

    def mark_blocked(self, ctx: TaskExecutionContext) -> None:
        self.mark_suspended(ctx, waiting_kind="awaiting_approval")

    def mark_suspended(self, ctx: TaskExecutionContext, *, waiting_kind: str) -> None:
        task = self.store.get_task(ctx.task_id)
        if task is not None and not validate_task_transition(task.status, "blocked"):
            import structlog

            structlog.get_logger().warning(
                "invalid_task_transition",
                task_id=ctx.task_id,
                current=task.status,
                target="blocked",
                caller="mark_suspended",
            )
        self.store.update_step(ctx.step_id, status="blocked")
        self.store.update_step_attempt(ctx.step_attempt_id, status=waiting_kind)
        self.store.update_task_status(ctx.task_id, "blocked")
        self.update_attempt_phase(ctx.step_attempt_id, phase=waiting_kind)
        self._set_focus(
            conversation_id=ctx.conversation_id, task_id=ctx.task_id, reason=waiting_kind
        )

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
        if not validate_task_transition(task.status, "paused"):
            import structlog

            structlog.get_logger().warning(
                "invalid_task_transition",
                task_id=task_id,
                current=task.status,
                target="paused",
                caller="pause_task",
            )
        self.store.update_task_status(task_id, "paused")
        self._refresh_focus_after_task_status(task.conversation_id, task_id)

    def cancel_task(self, task_id: str, *, _cascaded_from: str | None = None) -> list[str]:
        """Cancel a task and recursively cascade cancellation to all descendants.

        When *_cascaded_from* is set this is an internal recursive call triggered
        by a parent cancellation — a ``task.cascade_cancelled`` audit event is
        emitted instead of a plain cancellation.

        Returns the list of task IDs that were cascade-cancelled (excludes the
        root task itself and any children already in a terminal state).
        """
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_task",
                    default="Unknown task: {task_id}",
                    task_id=task_id,
                )
            )
        if not validate_task_transition(task.status, "cancelled"):
            import structlog

            structlog.get_logger().warning(
                "invalid_task_transition",
                task_id=task_id,
                current=task.status,
                target="cancelled",
                caller="cancel_task",
            )

        # ── Depth-first cascade: cancel all non-terminal descendants first ──
        cascade_cancelled: list[str] = []
        children = self.store.list_child_tasks(parent_task_id=task_id)
        for child in children:
            if child.status in TERMINAL_TASK_STATUSES:
                continue
            # Recurse into grandchildren before cancelling the child itself.
            grandchild_ids = self.cancel_task(child.task_id, _cascaded_from=task_id)
            cascade_cancelled.extend(grandchild_ids)
            cascade_cancelled.append(child.task_id)

        # ── Release workspace leases for *this* task ──
        if self._workspace_lease_service is not None:
            try:
                released = self._workspace_lease_service.release_all_for_task(task_id)
                if released:
                    self.store.append_event(
                        event_type="workspace.task_terminal_cleanup",
                        entity_type="task",
                        entity_id=task_id,
                        task_id=task_id,
                        actor="kernel",
                        payload={
                            "released_lease_ids": released,
                            "task_status": "cancelled",
                        },
                    )
            except Exception:
                import structlog

                structlog.get_logger().warning(
                    "cancel_task_lease_release_failed",
                    task_id=task_id,
                    exc_info=True,
                )

        # ── Cancel the task itself ──
        # C11: Resolve any active observation tickets for this task before
        # marking it as cancelled so they do not remain orphaned.
        try:
            self.store.resolve_observations_for_task(task_id, status="cancelled")
        except Exception:
            import structlog

            structlog.get_logger().warning(
                "cancel_task_observation_cleanup_failed",
                task_id=task_id,
                exc_info=True,
            )

        # Note: update_task_status will call _cascade_cancel_children internally,
        # but all children are already terminal so that will be a no-op.
        self.store.update_task_status(task_id, "cancelled")

        # ── Emit cascade audit event when this cancellation was triggered by a parent ──
        if _cascaded_from is not None:
            self.store.append_event(
                event_type="task.cascade_cancelled",
                entity_type="task",
                entity_id=task_id,
                task_id=task_id,
                actor="kernel",
                payload={
                    "cascaded_from": _cascaded_from,
                    "task_status": "cancelled",
                    "child_cascade_count": len(cascade_cancelled),
                },
            )

        self._refresh_focus_after_task_status(task.conversation_id, task_id)
        return cascade_cancelled

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
        self._set_focus(
            conversation_id=conversation_id, task_id=task_id, reason="explicit_task_switch"
        )
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
        self.store.update_task_priority(task_id, priority=priority)

    def resume_attempt(self, step_attempt_id: str) -> TaskExecutionContext:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            raise KeyError(
                self._t(
                    "kernel.controller.error.unknown_step_attempt",
                    default="Unknown step attempt: {step_attempt_id}",
                    step_attempt_id=step_attempt_id,
                )
            )
        context = dict(attempt.context or {})
        if bool(context.get("input_dirty")) and (
            str(attempt.status_reason or "") == "awaiting_approval"
            or str(attempt.status or "") == "awaiting_approval"
            or str(context.get("phase", "") or "") == "awaiting_approval"
        ):
            return self.enqueue_resume(step_attempt_id)
        if bool(context.get("recovery_required")):
            payload = self._runtime_snapshot_payload(attempt)
            context["execution_mode"] = "resume"
            context["phase"] = (
                "observing"
                if str(payload.get("suspend_kind", "") or "") == "observing"
                else "authorized_pre_exec"
            )
            context["reentry_required"] = False
            context["recovery_required"] = False
            context["reentry_resolved_at"] = time.time()
            self.store.update_step_attempt(
                step_attempt_id,
                context=context,
                status_reason="reentry_resumed",
            )
            self.store.append_event(
                event_type="step_attempt.reentry_resumed",
                entity_type="step_attempt",
                entity_id=step_attempt_id,
                task_id=attempt.task_id,
                step_id=attempt.step_id,
                actor="kernel",
                payload={
                    "step_attempt_id": step_attempt_id,
                    "reentry_reason": context.get("reentry_reason") or "worker_interrupted",
                    "reentry_boundary": context.get("reentry_boundary") or "observation_resolution",
                    "phase": context["phase"],
                },
            )
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
            iter(
                self.store.list_ingresses(
                    conversation_id=conversation_id, status="pending_disambiguation", limit=1
                )
            ),
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
            requested_by=requested_by or pending.actor_principal_id or "user",
            ingress_id=pending.ingress_id,
        )
        self._set_focus(
            conversation_id=conversation_id, task_id=task_id, reason="pending_ingress_resolved"
        )
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
        self._set_focus(
            conversation_id=conversation_id, task_id=task_id, reason="ingress_created_task"
        )

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
                payload={
                    "focus_task_id": task_id,
                    "reason": reason,
                    "previous_focus_task_id": previous_task_id,
                },
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
        self._set_focus(
            conversation_id=conversation_id, task_id=next_focus, reason="focus_task_terminal"
        )

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
                if item.status
                in {"ready", "running", "awaiting_approval", "observing", "policy_pending"}
            ),
            None,
        )
        if attempt is None:
            return
        context = dict(attempt.context or {})
        was_dirty = bool(context.get("input_dirty"))
        context["input_dirty"] = True
        context["latest_bound_ingress_id"] = ingress_id or str(
            context.get("latest_bound_ingress_id", "") or ""
        )
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
        return normalize_text(text)

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
        greetings = _greeting_texts()
        if lowered in greetings or cleaned in greetings:
            return True
        return bool(_LOW_SIGNAL_RE.match(cleaned))

    @classmethod
    def _is_explicit_new_task_message(cls, text: str) -> bool:
        cleaned = cls._normalize_ingress_text(text)
        return has_explicit_new_task_marker(cleaned)

    @classmethod
    def _has_continue_marker(cls, text: str) -> bool:
        cleaned = cls._normalize_ingress_text(text)
        return has_continue_marker(cleaned)

    def _looks_like_task_followup(self, text: str, *, task_id: str) -> bool:
        cleaned = self._normalize_ingress_text(text)
        if not cleaned:
            return False
        if self._has_continue_marker(cleaned):
            return True
        if has_ambiguous_followup_marker(cleaned):
            return True
        context_texts = self._task_context_texts(task_id)
        return any(texts_overlap(cleaned, context_text) for context_text in context_texts)

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
            step_kinds = {
                str(step.get("kind") or "") for step in projection.get("steps", {}).values()
            }
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
            note_text = clean_runtime_text(
                payload.get("raw_text") or payload.get("inline_excerpt") or ""
            )
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
        outcome = (
            build_task_outcome(
                store=self.store,
                task_id=task_id,
                status=str(task.status or ""),
                events=events,
            )
            or {}
        )
        return {
            "anchor_task_id": task_id,
            "anchor_kind": "completed_outcome",
            "selection_reason": selection_reason,
            "anchor_title": str(task.title or ""),
            "anchor_goal": str(task.goal or ""),
            "anchor_user_request": str(task.title or task.goal or ""),
            "outcome_status": str(outcome.get("status", task.status) or task.status),
            "outcome_summary": str(outcome.get("outcome_summary", "") or ""),
            "source_artifact_refs": list(outcome.get("source_artifact_refs", []) or []),
        }

    @staticmethod
    def _texts_overlap(
        text: str, candidate_text: str, *, query_tokens: set[str] | None = None
    ) -> bool:
        return texts_overlap(text, candidate_text)

    @staticmethod
    def _sanitize_context_text(text: str) -> str:
        cleaned = _SESSION_TIME_RE.sub("", str(text or ""))
        cleaned = _FEISHU_TAG_RE.sub("", cleaned)
        cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
        return cleaned.strip()
