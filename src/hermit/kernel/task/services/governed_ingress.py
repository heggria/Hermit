"""Governed ingress — 3-path routing that wraps the existing IngressRouter.

The spec mandates three distinct paths for incoming messages:

1. **New work** — creates or extends programs/tasks via ``IngressRouter.bind()``.
2. **Status query** — read-only, served entirely from ``StatusProjectionService``.
   Never creates tasks or triggers worker execution.
3. **Control command** — modifies program/team state (pause, resume, budget, etc.)
   via the kernel store's program primitives.

``GovernedIngressService`` is intentionally thin: it classifies intent via
``GovernorService``, then delegates to the appropriate handler.  It does NOT
execute work itself — only decides routing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import structlog

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.program import ACTIVE_PROGRAM_STATES, ProgramState
from hermit.kernel.task.models.records import ConversationRecord, TaskRecord
from hermit.kernel.task.projections.status import StatusProjectionService
from hermit.kernel.task.services.governor import (
    GovernorService,
    IntentClass,
    IntentResolution,
)
from hermit.kernel.task.services.ingress_router import BindingDecision, IngressRouter

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GovernedIngressResult:
    """Structured response from the governed ingress 3-path router.

    Attributes:
        intent_class: One of ``new_work``, ``status_query``, ``control_command``.
        response: Handler-specific payload (status summary, control ack, etc.).
        requires_execution: ``False`` for status queries and control commands;
            ``True`` only when new work needs a worker to pick it up.
        binding_decision: Present only for the ``new_work`` path — the
            ``BindingDecision`` produced by ``IngressRouter.bind()``.
        resolution: The upstream ``IntentResolution`` for traceability.
    """

    intent_class: str
    response: dict[str, Any]
    requires_execution: bool
    binding_decision: BindingDecision | None = None
    resolution: IntentResolution | None = None


# ---------------------------------------------------------------------------
# Control command helpers
# ---------------------------------------------------------------------------

_PAUSE_KEYWORDS = frozenset({"pause", "halt", "stop", "暂停", "停止"})
_RESUME_KEYWORDS = frozenset({"resume", "restart", "恢复", "重启"})
_CANCEL_KEYWORDS = frozenset({"cancel", "取消"})
_PROMOTE_KEYWORDS = frozenset({"promote", "提升", "提高"})
_BUDGET_KEYWORDS = frozenset({"budget", "raise", "预算", "增加"})
_CONCURRENCY_KEYWORDS = frozenset({"lower", "decrease", "concurrency", "降低", "减少", "并发"})
_ESCALATE_KEYWORDS = frozenset({"escalate", "升级"})


def _infer_control_action(raw_input: str) -> str:
    """Best-effort mapping from raw text to a control verb."""
    tokens = set(raw_input.lower().split())
    text_lower = raw_input.lower()
    # Helper: match either space-separated tokens or CJK substrings
    _CJK = re.compile(r"[\u4e00-\u9fff]")

    def _hit(kws: frozenset[str]) -> bool:
        if tokens & kws:
            return True
        return any(kw in text_lower for kw in kws if _CJK.search(kw))

    if _hit(_PAUSE_KEYWORDS):
        return "pause"
    if _hit(_RESUME_KEYWORDS):
        return "resume"
    if _hit(_CANCEL_KEYWORDS):
        return "cancel"
    if _hit(_PROMOTE_KEYWORDS):
        return "promote"
    if _hit(_BUDGET_KEYWORDS):
        return "budget"
    if _hit(_CONCURRENCY_KEYWORDS):
        return "concurrency"
    if _hit(_ESCALATE_KEYWORDS):
        return "escalate"
    return "unknown"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class GovernedIngressService:
    """Unified ingress implementing the spec's 3-path routing.

    Wraps the existing ``IngressRouter`` with governor-level intent
    classification so that status queries are served from read-model
    projections and control commands go through program state transitions —
    neither of which creates new tasks or triggers worker execution.

    Typical usage::

        service = GovernedIngressService(store)
        result = service.process_message(message="show me progress")
        assert result.requires_execution is False
    """

    def __init__(self, store: KernelStore) -> None:
        self.store = store
        self.governor = GovernorService(store)
        self.status_service = StatusProjectionService(store)
        self.ingress_router = IngressRouter(store)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def process_message(
        self,
        *,
        message: str,
        conversation_id: str | None = None,
        context: dict[str, Any] | None = None,
        conversation: ConversationRecord | None = None,
        open_tasks: list[TaskRecord] | None = None,
    ) -> GovernedIngressResult:
        """Process an incoming message through the 3-path router.

        1. Classify intent via ``GovernorService``.
        2. Route to the matching handler.
        3. Return a ``GovernedIngressResult`` — status queries and control
           commands set ``requires_execution=False``.
        """
        resolution = self.governor.classify_intent(message, context=context)
        log.debug(
            "governed_ingress.classified",
            intent_class=str(resolution.intent_class),
            confidence=resolution.confidence,
            message_preview=message[:80] if message else "",
        )

        if resolution.intent_class == IntentClass.status_query:
            response = self.handle_status_query(resolution)
            return GovernedIngressResult(
                intent_class=str(resolution.intent_class),
                response=response,
                requires_execution=False,
                resolution=resolution,
            )

        if resolution.intent_class == IntentClass.control_command:
            response = self.handle_control_command(resolution)
            return GovernedIngressResult(
                intent_class=str(resolution.intent_class),
                response=response,
                requires_execution=False,
                resolution=resolution,
            )

        # Default: new_work path — delegate to the existing IngressRouter.
        binding = self.handle_new_work(
            resolution,
            conversation=conversation,
            open_tasks=open_tasks,
            normalized_text=message,
        )
        return GovernedIngressResult(
            intent_class=str(resolution.intent_class),
            response={
                "action": "bind_task",
                "resolution": binding.resolution,
                "chosen_task_id": binding.chosen_task_id,
                "parent_task_id": binding.parent_task_id,
                "confidence": binding.confidence,
            },
            requires_execution=True,
            binding_decision=binding,
            resolution=resolution,
        )

    # ------------------------------------------------------------------
    # Path handlers
    # ------------------------------------------------------------------

    def handle_status_query(self, resolution: IntentResolution) -> dict[str, Any]:
        """Handle a status query via the read-model path.

        Never creates tasks.  Supports four granularity levels per the spec:
        program, team, task, and attempt.  Falls back to the most-recently
        active program when no explicit target is specified, rather than
        only showing the global approval queue.

        Resolution priority (spec: "如果用户只说'看一下进展'，没说 Program 名怎么办"):
        1. Explicit target from the intent resolution
        2. Most recently active program
        3. Global overview (approval queue)
        """
        result: dict[str, Any] = {
            "handler": "status_query",
            "timestamp": time.time(),
        }

        has_specific_target = any(
            [
                resolution.target_program_id,
                resolution.target_team_id,
                resolution.target_task_id,
                resolution.target_attempt_id,
            ]
        )

        # -- Attempt-level status (most specific) --
        if resolution.target_attempt_id:
            try:
                attempt_proj = self.status_service.get_attempt_status(resolution.target_attempt_id)
                result["attempt_status"] = {
                    "step_attempt_id": attempt_proj.step_attempt_id,
                    "task_id": attempt_proj.task_id,
                    "step_id": attempt_proj.step_id,
                    "attempt_number": attempt_proj.attempt_number,
                    "status": attempt_proj.status,
                    "waiting_reason": attempt_proj.waiting_reason,
                    "has_approval": attempt_proj.has_approval,
                    "has_capability_grant": attempt_proj.has_capability_grant,
                    "started_at": attempt_proj.started_at,
                    "finished_at": attempt_proj.finished_at,
                    "failure_reason": attempt_proj.failure_reason,
                }
                result["formatted_summary"] = self.status_service.format_attempt_summary(
                    attempt_proj
                )
            except KeyError:
                result["error"] = f"Attempt not found: {resolution.target_attempt_id}"

        # -- Program-level status --
        if resolution.target_program_id:
            try:
                projection = self.status_service.get_program_status(resolution.target_program_id)
                result["program_status"] = {
                    "program_id": projection.program_id,
                    "title": projection.title,
                    "overall_state": projection.overall_state,
                    "progress_pct": projection.progress_pct,
                    "current_phase": projection.current_phase,
                    "active_teams": projection.active_teams,
                    "queued_tasks": projection.queued_tasks,
                    "running_attempts": projection.running_attempts,
                    "blocked_items": projection.blocked_items,
                    "awaiting_human": projection.awaiting_human,
                    "latest_summary": projection.latest_summary,
                    "latest_risks": projection.latest_risks,
                    "last_updated_at": projection.last_updated_at,
                }
                result.setdefault(
                    "formatted_summary",
                    self.status_service.format_program_summary(projection),
                )
            except KeyError:
                result["error"] = f"Program not found: {resolution.target_program_id}"

        # -- Team-level status --
        if resolution.target_team_id:
            try:
                team_proj = self.status_service.get_team_status(resolution.target_team_id)
                result["team_status"] = {
                    "team_id": team_proj.team_id,
                    "title": team_proj.title,
                    "state": team_proj.state,
                    "active_workers": team_proj.active_workers,
                    "milestone_progress": team_proj.milestone_progress,
                    "blockers": team_proj.blockers,
                }
                result.setdefault(
                    "formatted_summary",
                    self.status_service.format_team_summary(team_proj),
                )
            except KeyError:
                result["error"] = f"Team not found: {resolution.target_team_id}"

        # -- Task-level status (full projection, not bare store lookup) --
        if resolution.target_task_id:
            try:
                task_proj = self.status_service.get_task_status(resolution.target_task_id)
                result["task_status"] = {
                    "task_id": task_proj.task_id,
                    "title": task_proj.title,
                    "status": task_proj.state,
                    "goal": task_proj.goal,
                    "priority": task_proj.priority,
                    "parent_task_id": task_proj.parent_task_id,
                    "total_steps": task_proj.total_steps,
                    "completed_steps": task_proj.completed_steps,
                    "running_steps": task_proj.running_steps,
                    "blocked_steps": task_proj.blocked_steps,
                    "failed_steps": task_proj.failed_steps,
                    "pending_approvals": task_proj.pending_approvals,
                    "latest_event": task_proj.latest_event,
                    "blockers": task_proj.blockers,
                    "last_updated_at": task_proj.last_updated_at,
                }
                result.setdefault(
                    "formatted_summary",
                    self.status_service.format_task_summary(task_proj),
                )
            except KeyError:
                result["error"] = f"Task not found: {resolution.target_task_id}"

        # -- Fallback: no explicit target --
        # Spec: "当前会话绑定 Program → 最近活跃 Program → 列出候选"
        if not has_specific_target:
            resolved_program_id = self.governor.resolve_program(
                resolution.raw_input,
            )
            if resolved_program_id:
                try:
                    projection = self.status_service.get_program_status(resolved_program_id)
                    result["program_status"] = {
                        "program_id": projection.program_id,
                        "title": projection.title,
                        "overall_state": projection.overall_state,
                        "progress_pct": projection.progress_pct,
                        "current_phase": projection.current_phase,
                        "active_teams": projection.active_teams,
                        "queued_tasks": projection.queued_tasks,
                        "running_attempts": projection.running_attempts,
                        "blocked_items": projection.blocked_items,
                        "awaiting_human": projection.awaiting_human,
                        "latest_summary": projection.latest_summary,
                        "latest_risks": projection.latest_risks,
                        "last_updated_at": projection.last_updated_at,
                    }
                    result["resolved_program_id"] = resolved_program_id
                    result["formatted_summary"] = self.status_service.format_program_summary(
                        projection
                    )
                except KeyError:
                    # Resolved program not found — fall through to approval queue.
                    pass

            # Always include the approval queue in the global overview.
            approval_proj = self.status_service.get_approval_queue()
            result["approval_queue"] = {
                "total_count": approval_proj.total_count,
                "high_priority_count": approval_proj.high_priority_count,
                "pending_approvals": approval_proj.pending_approvals,
            }

        return result

    def handle_control_command(self, resolution: IntentResolution) -> dict[str, Any]:
        """Handle a control command.  May modify program/team state.

        Inspects the resolution's matched keywords and target IDs to
        determine the appropriate control action (pause, resume, cancel).
        """
        action = _infer_control_action(resolution.raw_input)
        result: dict[str, Any] = {
            "handler": "control_command",
            "action": action,
            "timestamp": time.time(),
        }

        target_program_id = resolution.target_program_id
        if target_program_id:
            result.update(self._apply_program_control(target_program_id, action))
        elif resolution.target_task_id:
            result["target_task_id"] = resolution.target_task_id
            result["note"] = (
                "Task-level control commands are routed through "
                "the task controller (not the governor)."
            )
        else:
            result["note"] = "No target program or task specified for control command."

        return result

    def handle_new_work(
        self,
        resolution: IntentResolution,
        *,
        conversation: ConversationRecord | None = None,
        open_tasks: list[TaskRecord] | None = None,
        normalized_text: str = "",
    ) -> BindingDecision:
        """Handle new work via the existing ``IngressRouter.bind()``."""
        return self.ingress_router.bind(
            conversation=conversation,
            open_tasks=open_tasks or [],
            normalized_text=normalized_text or resolution.raw_input,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_program_control(self, program_id: str, action: str) -> dict[str, Any]:
        """Apply a control action to a program, returning an ack dict."""
        program = self.store.get_program(program_id)
        if program is None:
            return {"error": f"Program not found: {program_id}"}

        if action == "pause" and program.status in ACTIVE_PROGRAM_STATES:
            self.store.update_program_status(program_id, ProgramState.paused)
            log.info("governed_ingress.program_paused", program_id=program_id)
            return {
                "program_id": program_id,
                "previous_status": program.status,
                "new_status": str(ProgramState.paused),
                "applied": True,
            }

        if action == "resume" and program.status == ProgramState.paused:
            self.store.update_program_status(program_id, ProgramState.active)
            log.info("governed_ingress.program_resumed", program_id=program_id)
            return {
                "program_id": program_id,
                "previous_status": program.status,
                "new_status": str(ProgramState.active),
                "applied": True,
            }

        if action == "cancel" and program.status in ACTIVE_PROGRAM_STATES:
            self.store.update_program_status(program_id, ProgramState.failed)
            log.info("governed_ingress.program_cancelled", program_id=program_id)
            return {
                "program_id": program_id,
                "previous_status": program.status,
                "new_status": str(ProgramState.failed),
                "applied": True,
            }

        return {
            "program_id": program_id,
            "current_status": program.status,
            "action": action,
            "applied": False,
            "reason": f"Action '{action}' not applicable in state '{program.status}'.",
        }


__all__ = [
    "GovernedIngressResult",
    "GovernedIngressService",
]
