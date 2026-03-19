from __future__ import annotations

import datetime
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from hermit.infra.storage import atomic_write
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.task.services.controller import AUTO_PARENT, TaskController
from hermit.kernel.task.services.planning import PlanningService
from hermit.runtime.capability.contracts.base import HookEvent
from hermit.runtime.control.lifecycle.session import SessionManager
from hermit.runtime.control.runner.utils import result_status
from hermit.runtime.provider_host.execution.runtime import AgentResult

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.capability.registry.manager import PluginManager
    from hermit.runtime.control.runner.runner import AgentRunner, DispatchResult


class AsyncDispatcher:
    """Async dispatch helpers extracted from AgentRunner.

    Delegates back to the runner for session preparation, result status
    evaluation, and other runner-internal helpers — following the same
    delegation pattern used by ``WitnessCapture``.
    """

    def __init__(
        self,
        *,
        runner: AgentRunner,
        store: KernelStore,
        task_controller: TaskController,
        session_manager: SessionManager,
        pm: PluginManager,
    ) -> None:
        self._runner = runner
        self.store = store
        self.task_controller = task_controller
        self.session_manager = session_manager
        self.pm = pm

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def wake_dispatcher(self) -> None:
        self._runner.wake_dispatcher()

    def enqueue_ingress(
        self,
        session_id: str,
        text: str,
        *,
        source_channel: str | None = None,
        notify: dict[str, object] | None = None,
        source_ref: str = "",
        ingress_metadata: dict[str, object] | None = None,
        requested_by: str | None = "user",
        parent_task_id: str | None | object = AUTO_PARENT,
    ):
        source = source_channel or self.task_controller.source_from_session(session_id)
        _session, full_prompt, run_opts, task_goal = self._runner._prepare_prompt_context(
            session_id,
            text,
            source_channel=source,
        )
        task_kind = "plan" if run_opts.get("readonly_only", False) else "respond"
        metadata = dict(ingress_metadata or {})
        metadata.update(
            {
                "dispatch_mode": "async",
                "entry_prompt": full_prompt,
                "raw_text": text,
                "notify": dict(notify or {}),
                "source_ref": source_ref,
                "disable_tools": bool(run_opts.get("disable_tools", False)),
                "readonly_only": bool(run_opts.get("readonly_only", False)),
            }
        )
        ctx = self.task_controller.enqueue_task(
            conversation_id=session_id,
            goal=task_goal,
            source_channel=source,
            kind=task_kind,
            policy_profile=(
                "readonly"
                if run_opts.get("readonly_only", False)
                else str(
                    metadata.get("policy_profile") or run_opts.get("policy_profile") or "default"
                )
            ),
            workspace_root=str(getattr(self._runner.agent, "workspace_root", "") or ""),
            parent_task_id=parent_task_id,
            requested_by=requested_by,
            ingress_metadata=metadata,
            source_ref=source_ref or None,
        )
        if run_opts.get("planning_mode", False):
            planning = PlanningService(
                self.task_controller.store, getattr(self._runner.agent, "artifact_store", None)
            )
            planning.set_pending_for_conversation(session_id, enabled=False)
            planning.enter_planning(ctx.task_id)
        self.wake_dispatcher()
        return ctx

    def enqueue_approval_resume(
        self,
        session_id: str,
        *,
        action: str,
        approval_id: str,
        reason: str = "",
    ) -> DispatchResult:
        from hermit.kernel.policy.approvals.approvals import ApprovalService
        from hermit.runtime.control.runner.runner import DispatchResult, _t

        approval = self.task_controller.store.get_approval(approval_id)
        if approval is None:
            return DispatchResult(
                _t(
                    "kernel.runner.approval_not_found",
                    runner=self._runner,
                    approval_id=approval_id,
                ),
                is_command=True,
            )

        session = self.session_manager.get_or_create(session_id)
        approvals = ApprovalService(self.task_controller.store)
        if action == "deny":
            approvals.deny(approval_id, resolved_by="user", reason=reason)
            text = _t("kernel.runner.approval_denied", runner=self._runner)
            messages = list(session.messages)
            messages.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
            from hermit.runtime.control.runner.runner import _trim_session_messages

            session.messages = _trim_session_messages(messages)
            self.session_manager.save(session)
            return DispatchResult(text=text, is_command=True)

        if action == "approve_mutable_workspace":
            approvals.approve_mutable_workspace(approval_id, resolved_by="user")
        else:
            approvals.approve_once(approval_id, resolved_by="user")
        self.task_controller.enqueue_resume(approval.step_attempt_id)
        self.wake_dispatcher()
        text = _t(
            "kernel.runner.approval_queued",
            runner=self._runner,
            default="Approval granted. The task is queued to resume.",
        )
        messages = list(session.messages)
        messages.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
        from hermit.runtime.control.runner.runner import _trim_session_messages

        session.messages = _trim_session_messages(messages)
        self.session_manager.save(session)
        return DispatchResult(text=text, is_command=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def emit_async_dispatch_result(
        self,
        task_ctx: TaskExecutionContext,
        result: AgentResult,
        *,
        started_at: float,
    ) -> list[object]:
        notify = dict(task_ctx.ingress_metadata.get("notify", {}) or {})
        if not notify:
            return []
        success = result_status(result) == "succeeded"
        return self.pm.hooks.fire(
            HookEvent.DISPATCH_RESULT,
            source=str(task_ctx.ingress_metadata.get("source_ref", "") or task_ctx.source_channel),
            title=str(
                task_ctx.ingress_metadata.get("title", "")
                or task_ctx.ingress_metadata.get("schedule_job_name", "")
                or task_ctx.task_id
            ),
            result_text=result.text or "",
            success=success,
            error=None if success else (result.text or ""),
            notify=notify,
            metadata={
                "task_id": task_ctx.task_id,
                "step_attempt_id": task_ctx.step_attempt_id,
                "job_id": str(task_ctx.ingress_metadata.get("schedule_job_id", "") or ""),
                "job_name": str(task_ctx.ingress_metadata.get("schedule_job_name", "") or ""),
                "started_at": started_at,
                "source_channel": task_ctx.source_channel,
            },
            settings=getattr(self.pm, "settings", None),
        )

    def record_scheduler_execution(
        self,
        task_ctx: TaskExecutionContext,
        result: AgentResult,
        *,
        started_at: float,
        delivery_results: list[object] | None = None,
    ) -> None:
        job_id = str(task_ctx.ingress_metadata.get("schedule_job_id", "") or "")
        if not job_id:
            return
        from hermit.plugins.builtin.hooks.scheduler.models import JobExecutionRecord

        settings = getattr(self.pm, "settings", None)
        if settings is None:
            return
        notify = dict(task_ctx.ingress_metadata.get("notify", {}) or {})
        job_name = str(task_ctx.ingress_metadata.get("schedule_job_name", "") or "") or job_id
        finished_at = time.time()
        record = JobExecutionRecord(
            job_id=job_id,
            job_name=job_name,
            started_at=started_at,
            finished_at=finished_at,
            success=result_status(result) == "succeeded",
            result_text=result.text or "",
            error=(None if result_status(result) == "succeeded" else (result.text or "")),
        )
        delivery: dict[str, Any] | None = None
        for _item in delivery_results or []:
            if isinstance(_item, dict) and cast(dict[str, Any], _item).get("channel") == "feishu":
                delivery = cast(dict[str, Any], _item)
                break
        if delivery is not None:
            record.delivery_status = str(delivery.get("status", "") or "") or None
            record.delivery_channel = str(delivery.get("channel", "") or "") or None
            record.delivery_mode = str(delivery.get("mode", "") or "") or None
            record.delivery_target = str(delivery.get("target", "") or "") or None
            record.delivery_message_id = str(delivery.get("message_id", "") or "") or None
            record.delivery_error = str(delivery.get("error", "") or "") or None
        elif notify.get("feishu_chat_id"):
            record.delivery_status = "failure"
            record.delivery_channel = "feishu"
            record.delivery_mode = str(notify.get("delivery_mode", "") or "") or None
            record.delivery_target = str(notify.get("feishu_chat_id", "") or "") or None
            record.delivery_error = "dispatch_result hook returned no feishu delivery result"
        self.task_controller.store.append_schedule_history(record)
        history = self.task_controller.store.list_schedule_history(limit=200)
        history_path = Path(settings.base_dir) / "schedules" / "history.json"
        logs_dir = Path(settings.base_dir) / "schedules" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            history_path,
            json.dumps(
                {"records": [item.to_dict() for item in history]},
                ensure_ascii=False,
                indent=2,
            ),
        )
        timestamp = datetime.datetime.fromtimestamp(record.started_at).strftime("%Y%m%d_%H%M%S")
        log_name = f"{timestamp}_{record.job_id}.log"
        lines = [
            f"Job: {record.job_name} ({record.job_id})",
            f"Started: {datetime.datetime.fromtimestamp(record.started_at).isoformat()}",
            f"Finished: {datetime.datetime.fromtimestamp(record.finished_at).isoformat()}",
            f"Duration: {record.finished_at - record.started_at:.1f}s",
            f"Success: {record.success}",
        ]
        if record.error:
            lines.append(f"Error: {record.error}")
        lines.extend(["", "--- Result ---", record.result_text])
        atomic_write(logs_dir / log_name, "\n".join(lines))
