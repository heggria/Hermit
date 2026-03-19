from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, cast

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel.task.models.records import TaskRecord
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.task.services.planning import PlanningService
from hermit.runtime.provider_host.execution.runtime import (
    ToolCallback,
    ToolStartCallback,
)

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.capability.registry.manager import PluginManager
    from hermit.runtime.control.runner.runner import AgentRunner, DispatchResult


def _locale_for_runner(runner: AgentRunner | None = None) -> str:
    settings = getattr(getattr(runner, "pm", None), "settings", None)
    return resolve_locale(getattr(settings, "locale", None))


def _t(
    message_key: str,
    *,
    runner: AgentRunner | None = None,
    default: str | None = None,
    **kwargs: object,
) -> str:
    return tr(message_key, locale=_locale_for_runner(runner), default=default, **kwargs)


def _resolve_help_text(help_text: str, *, runner: AgentRunner | None = None) -> str:
    return tr(help_text, locale=_locale_for_runner(runner), default=help_text)


def _result_preview(text: str, *, limit: int = 280) -> str:
    """Produce a short preview of result text for storage."""
    import re

    _SESSION_TIME_RE = re.compile(r"<session_time>.*?</session_time>\s*", re.DOTALL)
    _FEISHU_META_RE = re.compile(r"<feishu_[^>]+>.*?</feishu_[^>]+>\s*", re.DOTALL)
    cleaned = _SESSION_TIME_RE.sub("", text)
    cleaned = _FEISHU_META_RE.sub("", cleaned)
    cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
    cleaned = cleaned.strip()
    if not cleaned:
        return ""
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "\u2026"


class ControlActionDispatcher:
    """Handles control action dispatch, extracted from AgentRunner.

    Follows the delegation pattern used by WitnessCapture: the runner
    instantiates this class with the required dependencies and calls
    ``dispatch`` instead of inlining the logic.
    """

    def __init__(
        self,
        *,
        runner: AgentRunner,
        task_controller: TaskController,
        pm: PluginManager,
    ) -> None:
        self._runner = runner
        self._task_controller = task_controller
        self._pm = pm

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def dispatch(
        self,
        session_id: str,
        *,
        action: str,
        target_id: str,
        reason: str = "",
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
    ) -> DispatchResult:
        from hermit.runtime.control.runner.runner import DispatchResult

        runner = self._runner

        if action in {"approve_once", "approve_mutable_workspace", "deny"}:
            return self._resolve_approval(
                session_id,
                action=action,
                approval_id=target_id,
                reason=reason,
                on_tool_call=on_tool_call,
                on_tool_start=on_tool_start,
            )
        if action == "new_session":
            runner.reset_session(session_id)
            return DispatchResult(_t("kernel.runner.new_session", runner=runner), is_command=True)
        if action == "focus_task":
            resolved = self._task_controller.focus_task(session_id, target_id)
            message = _t(
                "kernel.runner.focus_task",
                runner=runner,
                default=f"Focused task switched to {target_id}.",
                task_id=target_id,
            )
            if resolved is not None and getattr(resolved, "note_event_seq", None):
                message = (
                    f"{message}\n"
                    "The pending message was attached to this task and will be applied at the next durable boundary."
                )
            return DispatchResult(
                text=message,
                is_command=True,
            )
        if action == "show_history":
            session = runner.session_manager.get_or_create(session_id)
            user_turns = sum(1 for m in session.messages if m.get("role") == "user")
            total = len(session.messages)
            return DispatchResult(
                _t(
                    "kernel.runner.history_summary",
                    runner=runner,
                    user_turns=user_turns,
                    total=total,
                ),
                is_command=True,
            )
        if action == "show_help":
            lines = [_t("kernel.runner.help.title", runner=runner)]
            for cmd, (_fn, help_text, cli_only) in sorted(runner._commands.items()):
                if runner.serve_mode and cli_only:
                    continue
                lines.append(f"- `{cmd}` \u2014 {_resolve_help_text(help_text, runner=runner)}")
            return DispatchResult("\n".join(lines), is_command=True)

        store = getattr(getattr(runner, "agent", None), "kernel_store", None)
        if store is None:
            return DispatchResult(
                text=_t("kernel.runner.task_kernel_unavailable", runner=runner),
                is_command=True,
            )
        planning = self._build_planning(store)
        get_latest_task = getattr(store, "get_last_task_for_conversation", None)
        latest_task: TaskRecord | None = cast(
            TaskRecord | None,
            get_latest_task(session_id) if callable(get_latest_task) else None,
        )
        resolved_target = target_id or (latest_task.task_id if latest_task is not None else "")

        return self._dispatch_kernel_action(
            session_id,
            action=action,
            store=store,
            planning=planning,
            resolved_target=resolved_target,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
        )

    # ------------------------------------------------------------------
    # Kernel-level actions (require store)
    # ------------------------------------------------------------------

    def _dispatch_kernel_action(
        self,
        session_id: str,
        *,
        action: str,
        store: KernelStore,
        planning: PlanningService | None,
        resolved_target: str,
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
    ) -> DispatchResult:
        from hermit.runtime.control.runner.runner import DispatchResult

        runner = self._runner

        if action == "plan_enter":
            return self._plan_enter(planning, resolved_target, session_id)

        if action == "plan_exit":
            return self._plan_exit(planning, resolved_target, session_id)

        if action == "plan_confirm":
            return self._plan_confirm(
                planning,
                resolved_target,
                on_tool_call=on_tool_call,
                on_tool_start=on_tool_start,
            )

        if action == "task_list":
            payload = [task.__dict__ for task in store.list_tasks(limit=20)]
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "case":
            from hermit.kernel.execution.controller.supervision import SupervisionService

            payload = SupervisionService(store).build_task_case(resolved_target)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "task_events":
            payload = store.list_events(task_id=resolved_target, limit=100)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "task_receipts":
            payload = [
                receipt.__dict__
                for receipt in store.list_receipts(task_id=resolved_target, limit=50)
            ]
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "task_proof":
            from hermit.kernel.verification.proofs.proofs import ProofService

            payload = ProofService(store).build_proof_summary(resolved_target)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "task_proof_export":
            from hermit.kernel.verification.proofs.proofs import ProofService

            payload = ProofService(store).export_task_proof(resolved_target)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "rollback":
            from hermit.kernel.verification.rollbacks.rollbacks import RollbackService

            payload = RollbackService(store).execute(resolved_target)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "projection_rebuild":
            from hermit.kernel.task.projections.projections import ProjectionService

            payload = ProjectionService(store).rebuild_task(resolved_target)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "projection_rebuild_all":
            from hermit.kernel.task.projections.projections import ProjectionService

            payload = ProjectionService(store).rebuild_all()
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "capability_list":
            payload = [grant.__dict__ for grant in store.list_capability_grants(limit=50)]
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "capability_revoke":
            grant = store.get_capability_grant(resolved_target)
            if grant is None:
                return DispatchResult(
                    text=_t(
                        "kernel.runner.grant_not_found", runner=runner, grant_id=resolved_target
                    ),
                    is_command=True,
                )
            store.update_capability_grant(
                resolved_target,
                status="revoked",
                revoked_at=time.time(),
            )
            return DispatchResult(
                text=_t("kernel.runner.grant_revoked", runner=runner, grant_id=resolved_target),
                is_command=True,
            )
        if action == "schedule_list":
            payload = [job.to_dict() for job in store.list_schedules()]
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "schedule_history":
            payload = [
                record.to_dict()
                for record in store.list_schedule_history(job_id=resolved_target or None, limit=10)
            ]
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "schedule_enable":
            job = store.update_schedule(resolved_target, enabled=True)
            message = (
                _t("kernel.runner.schedule.enabled", runner=runner, job_id=resolved_target)
                if job is not None
                else _t("kernel.runner.schedule.not_found", runner=runner, job_id=resolved_target)
            )
            return DispatchResult(text=message, is_command=True)
        if action == "schedule_disable":
            job = store.update_schedule(resolved_target, enabled=False)
            message = (
                _t("kernel.runner.schedule.disabled", runner=runner, job_id=resolved_target)
                if job is not None
                else _t("kernel.runner.schedule.not_found", runner=runner, job_id=resolved_target)
            )
            return DispatchResult(text=message, is_command=True)
        if action == "schedule_remove":
            deleted = store.delete_schedule(resolved_target)
            message = (
                _t("kernel.runner.schedule.removed", runner=runner, job_id=resolved_target)
                if deleted
                else _t("kernel.runner.schedule.not_found", runner=runner, job_id=resolved_target)
            )
            return DispatchResult(text=message, is_command=True)
        return DispatchResult(
            text=_t("kernel.runner.unsupported_control_action", runner=runner, action=action),
            is_command=True,
        )

    # ------------------------------------------------------------------
    # Planning helpers
    # ------------------------------------------------------------------

    def _build_planning(self, store: KernelStore) -> PlanningService | None:
        runner = self._runner
        if hasattr(store, "ensure_conversation"):
            return PlanningService(store, getattr(runner.agent, "artifact_store", None))
        return None

    def _plan_enter(
        self,
        planning: PlanningService | None,
        resolved_target: str,
        session_id: str,
    ) -> DispatchResult:
        from hermit.runtime.control.runner.runner import DispatchResult

        runner = self._runner
        if planning is None:
            return DispatchResult(
                text=_t("kernel.runner.task_kernel_unavailable", runner=runner), is_command=True
            )
        if resolved_target:
            planning.enter_planning(resolved_target)
        else:
            planning.set_pending_for_conversation(session_id, enabled=True)
        plans_dir = getattr(
            getattr(runner.agent, "artifact_store", None), "root_dir", "kernel artifact store"
        )
        return DispatchResult(
            _t("kernel.planner.entered", runner=runner, plans_hint=plans_dir),
            is_command=True,
        )

    def _plan_exit(
        self,
        planning: PlanningService | None,
        resolved_target: str,
        session_id: str,
    ) -> DispatchResult:
        from hermit.runtime.control.runner.runner import DispatchResult

        runner = self._runner
        if planning is None:
            return DispatchResult(
                text=_t("kernel.runner.task_kernel_unavailable", runner=runner), is_command=True
            )
        planning.set_pending_for_conversation(session_id, enabled=False)
        if resolved_target:
            planning.exit_planning(resolved_target)
        return DispatchResult(_t("kernel.planner.closed", runner=runner), is_command=True)

    def _plan_confirm(
        self,
        planning: PlanningService | None,
        resolved_target: str,
        *,
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
    ) -> DispatchResult:
        from hermit.runtime.control.runner.runner import DispatchResult

        runner = self._runner
        if planning is None:
            return DispatchResult(
                text=_t("kernel.runner.task_kernel_unavailable", runner=runner), is_command=True
            )
        if not resolved_target:
            return DispatchResult(
                _t("kernel.planner.confirm_missing_plan", runner=runner),
                is_command=True,
            )
        plan_text = planning.load_selected_plan_text(resolved_target)
        plan_ctx = planning.latest_planning_attempt(resolved_target)
        if not plan_text or plan_ctx is None:
            return DispatchResult(
                _t("kernel.planner.confirm_missing_plan", runner=runner),
                is_command=True,
            )
        planning.confirm_selected_plan(plan_ctx, actor="user")
        latest_attempt = self._task_controller.store.get_step_attempt(plan_ctx.step_attempt_id)
        if (
            latest_attempt is not None
            and str(latest_attempt.status or "") == "awaiting_plan_confirmation"
        ):
            self._task_controller.store.update_step(plan_ctx.step_id, status="succeeded")
            self._task_controller.store.update_step_attempt(
                plan_ctx.step_attempt_id,
                status="succeeded",
                waiting_reason=None,
            )
        execution_ctx = self._task_controller.start_followup_step(
            task_id=resolved_target,
            kind="respond",
            status="running",
            workspace_root=plan_ctx.workspace_root,
            ingress_metadata={
                "selected_plan_ref": planning.state_for_task(resolved_target).selected_plan_ref
                or "",
                "plan_status": "executing",
                "planning_required": True,
            },
        )
        execution_prompt = _t(
            "kernel.planner.execution_prompt",
            runner=runner,
            plan_content=plan_text,
        )
        result = runner._run_existing_task(
            execution_ctx,
            execution_prompt,
            raw_text=execution_prompt,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
        )
        return DispatchResult(
            text=result.text or "",
            is_command=False,
            agent_result=result,
        )

    # ------------------------------------------------------------------
    # Approval resolution
    # ------------------------------------------------------------------

    def _resolve_approval(
        self,
        session_id: str,
        *,
        action: str,
        approval_id: str,
        reason: str = "",
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
    ) -> DispatchResult:
        from hermit.kernel.policy.approvals.approvals import ApprovalService
        from hermit.runtime.control.runner.runner import DispatchResult

        runner = self._runner

        approval = self._task_controller.store.get_approval(approval_id)
        if approval is None:
            return DispatchResult(
                _t("kernel.runner.approval_not_found", runner=runner, approval_id=approval_id),
                is_command=True,
            )

        session = runner.session_manager.get_or_create(session_id)
        approvals = ApprovalService(self._task_controller.store)
        if action == "deny":
            approvals.deny(approval_id, resolved_by="user", reason=reason)
            text = _t("kernel.runner.approval_denied", runner=runner)
            messages = list(session.messages)
            messages.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
            from hermit.runtime.control.runner.runner import _trim_session_messages

            session.messages = _trim_session_messages(
                messages,
                max_messages=runner._max_session_messages(),
            )
            runner.session_manager.save(session)
            return DispatchResult(text=text, is_command=True)

        if action == "approve_mutable_workspace":
            approvals.approve_mutable_workspace(approval_id, resolved_by="user")
        else:
            approvals.approve_once(approval_id, resolved_by="user")

        # For async/DAG-dispatched steps, enqueue_resume so the dispatch loop
        # re-claims and executes the step with proper reconciliation.
        # Calling agent.resume() directly here bypasses reconciliation and
        # can mark steps as succeeded without the approved action executing.
        if self._is_async_dispatch(approval.step_attempt_id):
            self._task_controller.enqueue_resume(approval.step_attempt_id)
            runner.wake_dispatcher()
            text = _t(
                "kernel.runner.approval_enqueued",
                runner=runner,
                default="Approved. The step has been re-queued for execution.",
            )
            return DispatchResult(text=text, is_command=True)

        task_ctx = self._task_controller.context_for_attempt(approval.step_attempt_id)
        result = runner.agent.resume(
            step_attempt_id=approval.step_attempt_id,
            task_context=task_ctx,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
        )
        session.total_input_tokens += result.input_tokens
        session.total_output_tokens += result.output_tokens
        session.total_cache_read_tokens += result.cache_read_tokens
        session.total_cache_creation_tokens += result.cache_creation_tokens
        from hermit.runtime.control.runner.runner import _trim_session_messages

        session.messages = _trim_session_messages(
            result.messages,
            max_messages=runner._max_session_messages(),
        )
        runner.session_manager.save(session)
        if result.suspended or result.blocked:
            if not getattr(result, "status_managed_by_kernel", False):
                if hasattr(self._task_controller, "mark_suspended"):
                    self._task_controller.mark_suspended(
                        task_ctx,
                        waiting_kind=str(
                            getattr(result, "waiting_kind", "") or "awaiting_approval"
                        ),
                    )
                else:
                    self._task_controller.mark_blocked(task_ctx)
        else:
            status = runner._result_status(result)
            if not getattr(result, "status_managed_by_kernel", False):
                self._task_controller.finalize_result(
                    task_ctx,
                    status=status,
                    result_preview=_result_preview(result.text or ""),
                    result_text=result.text or "",
                )
            self._pm.on_post_run(result, session_id=session_id, session=session, runner=runner)
        return DispatchResult(
            text=result.text or "",
            is_command=False,
            agent_result=result,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_async_dispatch(self, step_attempt_id: str) -> bool:
        """Return True if the step attempt was dispatched asynchronously (DAG/MCP)."""
        attempt = self._task_controller.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return False
        ingress = dict((attempt.context or {}).get("ingress_metadata", {}) or {})
        return str(ingress.get("dispatch_mode", "") or "") == "async"
