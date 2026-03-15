from __future__ import annotations

import datetime
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Optional

from hermit.core.session import SessionManager, sanitize_session_messages
from hermit.i18n import resolve_locale, tr
from hermit.kernel.context import TaskExecutionContext
from hermit.kernel.controller import _AUTO_PARENT, TaskController
from hermit.kernel.observation import ObservationService
from hermit.kernel.planning import PlanningService
from hermit.kernel.provider_input import ProviderInputCompiler
from hermit.plugin.base import HookEvent
from hermit.provider.runtime import AgentResult, AgentRuntime, ToolCallback, ToolStartCallback
from hermit.storage import atomic_write

if TYPE_CHECKING:
    from hermit.plugin.manager import PluginManager

CommandHandler = Callable[["AgentRunner", str, str], "DispatchResult"]

_SESSION_TIME_RE = re.compile(r"<session_time>.*?</session_time>\s*", re.DOTALL)
_FEISHU_META_RE = re.compile(r"<feishu_[^>]+>.*?</feishu_[^>]+>\s*", re.DOTALL)


def _strip_internal_markup(text: str) -> str:
    if not text:
        return ""
    cleaned = _SESSION_TIME_RE.sub("", text)
    cleaned = _FEISHU_META_RE.sub("", cleaned)
    cleaned = "\n".join(line for line in cleaned.splitlines() if line.strip())
    return cleaned.strip()


def _result_preview(text: str, *, limit: int = 280) -> str:
    cleaned = _strip_internal_markup(text)
    if not cleaned:
        return ""
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _locale_for_runner(runner: "AgentRunner" | None = None) -> str:
    settings = getattr(getattr(runner, "pm", None), "settings", None)
    return resolve_locale(getattr(settings, "locale", None))


def _t(
    message_key: str,
    *,
    runner: "AgentRunner" | None = None,
    default: str | None = None,
    **kwargs: object,
) -> str:
    return tr(message_key, locale=_locale_for_runner(runner), default=default, **kwargs)


def _resolve_help_text(help_text: str, *, runner: "AgentRunner" | None = None) -> str:
    return tr(help_text, locale=_locale_for_runner(runner), default=help_text)


@dataclass
class DispatchResult:
    """Unified result returned by AgentRunner.dispatch() for both commands and agent replies."""

    text: str
    is_command: bool = False
    should_exit: bool = False
    agent_result: Optional[AgentResult] = None


class AgentRunner:
    """Unified orchestration layer: session + agent + plugin hooks.

    Both CLI commands and adapter plugins call this instead of
    duplicating the get_session -> run -> save -> hooks flow.
    """

    # Class-level registry for core commands (populated by decorators at import time).
    _core_commands: Dict[str, tuple[CommandHandler, str, bool]] = {}

    @classmethod
    def register_command(
        cls, name: str, help_text: str, cli_only: bool = False
    ) -> Callable[[CommandHandler], CommandHandler]:
        """Decorator to register a core slash command."""

        def decorator(fn: CommandHandler) -> CommandHandler:
            cls._core_commands[name] = (fn, help_text, cli_only)
            return fn

        return decorator

    def __init__(
        self,
        agent: AgentRuntime,
        session_manager: SessionManager,
        plugin_manager: PluginManager,
        serve_mode: bool = False,
        task_controller: TaskController | None = None,
    ) -> None:
        if task_controller is None:
            raise ValueError(
                "AgentRunner requires a TaskController; non-kernel runner mode has been removed."
            )
        self.agent = agent
        self.session_manager = session_manager
        self.pm = plugin_manager
        self.serve_mode = serve_mode
        self.task_controller = task_controller
        self._session_started: set[str] = set()
        self._observation_service: ObservationService | None = None
        # Instance-level copy: core commands + plugin commands added later via add_command()
        self._commands: Dict[str, tuple[CommandHandler, str, bool]] = dict(self._core_commands)
        self._dispatch_service: object | None = None

    def start_background_services(self) -> None:
        if self._observation_service is None:
            self._observation_service = ObservationService(self)
        self._observation_service.start()
        if self._dispatch_service is None:
            from hermit.kernel.dispatch import KernelDispatchService

            worker_count = int(
                getattr(getattr(self.pm, "settings", None), "kernel_dispatch_worker_count", 4) or 4
            )
            self._dispatch_service = KernelDispatchService(self, worker_count=worker_count)
            self._dispatch_service.start()

    def stop_background_services(self) -> None:
        if self._dispatch_service is not None:
            stopper = getattr(self._dispatch_service, "stop", None)
            if callable(stopper):
                stopper()
            self._dispatch_service = None
        if self._observation_service is not None:
            self._observation_service.stop()
            self._observation_service = None

    def add_command(
        self,
        name: str,
        handler: CommandHandler,
        help_text: str,
        cli_only: bool = False,
    ) -> None:
        """Register a command on this runner instance (used by plugins)."""
        self._commands[name] = (handler, help_text, cli_only)

    def wake_dispatcher(self) -> None:
        waker = getattr(self._dispatch_service, "wake", None)
        if callable(waker):
            waker()

    def _ensure_session_started(self, session_id: str) -> None:
        if session_id not in self._session_started:
            self.pm.on_session_start(session_id)
            self._session_started.add(session_id)

    def _prepare_prompt_context(
        self,
        session_id: str,
        text: str,
        *,
        source_channel: str,
    ) -> tuple[object, str, dict[str, object], str]:
        ensure_conversation = getattr(self.task_controller, "ensure_conversation", None)
        if callable(ensure_conversation):
            ensure_conversation(session_id, source_channel=source_channel)
        session = self.session_manager.get_or_create(session_id)
        sanitized_messages = sanitize_session_messages(session.messages)
        if sanitized_messages != session.messages:
            session.messages = sanitized_messages
            self.session_manager.save(session)
        self._ensure_session_started(session_id)
        prompt, run_opts = self.pm.on_pre_run(
            text,
            session_id=session_id,
            session=session,
            messages=list(session.messages),
            runner=self,
        )
        now = datetime.datetime.now()
        if now.tzinfo is None or now.utcoffset() is None:
            now = now.astimezone()
        offset = now.strftime("%z")
        offset_label = f"UTC{offset[:3]}:{offset[3:]}" if offset and len(offset) == 5 else "local"
        timezone_name = getattr(now.tzinfo, "key", None) or offset_label
        time_ctx = (
            f"<session_time>"
            f"current_time={now.isoformat(timespec='seconds')} "
            f"timezone={timezone_name} "
            f"relative_time_base=current_time"
            f"</session_time>\n\n"
        )
        store = getattr(self.task_controller, "store", None)
        planning_mode = PlanningService.planning_requested(text)
        if store is not None and hasattr(store, "ensure_conversation"):
            planning = PlanningService(store, getattr(self.agent, "artifact_store", None))
            planning_mode = planning.pending_for_conversation(session_id) or planning_mode
        if planning_mode:
            run_opts = dict(run_opts)
            run_opts["readonly_only"] = True
            run_opts["planning_mode"] = True
            prompt = prompt + _t("kernel.planner.mode.prompt", runner=self)
        full_prompt = time_ctx + prompt
        task_goal = (
            _strip_internal_markup(text)
            or _strip_internal_markup(full_prompt)
            or text.strip()
            or full_prompt.strip()
        )
        return session, full_prompt, run_opts, task_goal

    def _provider_input_compiler(self) -> ProviderInputCompiler:
        store = getattr(self.task_controller, "store", None)
        if store is None or getattr(store, "db_path", None) is None:
            raise RuntimeError("compiled_context_unavailable")
        return ProviderInputCompiler(store, getattr(self.agent, "artifact_store", None))

    def _compile_provider_input(
        self,
        *,
        task_ctx: TaskExecutionContext,
        prompt: str,
        raw_text: str,
    ):
        try:
            compiler = self._provider_input_compiler()
        except RuntimeError:
            return None
        return compiler.compile(task_context=task_ctx, final_prompt=prompt, raw_text=raw_text)

    def _append_note_context(
        self, session_id: str, task_id: str, source_channel: str
    ) -> TaskExecutionContext:
        attempt = next(
            iter(self.task_controller.store.list_step_attempts(task_id=task_id, limit=1)), None
        )
        task = self.task_controller.store.get_task(task_id)
        return TaskExecutionContext(
            conversation_id=session_id,
            task_id=task_id,
            step_id=attempt.step_id if attempt is not None else "",
            step_attempt_id=attempt.step_attempt_id if attempt is not None else "",
            source_channel=source_channel,
            policy_profile=getattr(task, "policy_profile", "default"),
            workspace_root=str(
                (attempt.context if attempt is not None else {}).get("workspace_root", "") or ""
            ),
            ingress_metadata=dict(
                (attempt.context if attempt is not None else {}).get("ingress_metadata", {}) or {}
            ),
        )

    def _maybe_capture_planning_result(
        self,
        task_ctx: TaskExecutionContext,
        result: AgentResult,
        *,
        readonly_only: bool,
    ) -> bool:
        if not readonly_only:
            return False
        store = getattr(self.task_controller, "store", None)
        if store is None or not hasattr(store, "get_step"):
            return False
        step = store.get_step(task_ctx.step_id)
        if step is None or step.kind != "plan":
            return False
        planning = PlanningService(store, getattr(self.agent, "artifact_store", None))
        plan_ref = planning.capture_plan_result(task_ctx, plan_text=result.text or "")
        self.task_controller.mark_planning_ready(
            task_ctx,
            plan_artifact_ref=plan_ref,
            result_preview=_result_preview(result.text or ""),
            result_text=result.text or "",
        )
        result.execution_status = "planning_ready"
        result.status_managed_by_kernel = True
        return True

    def _run_existing_task(
        self,
        task_ctx: TaskExecutionContext,
        prompt: str,
        *,
        raw_text: str | None = None,
        readonly_only: bool = False,
        disable_tools: bool = False,
        on_tool_call: Optional[ToolCallback] = None,
        on_tool_start: Optional[ToolStartCallback] = None,
    ) -> AgentResult:
        session = self.session_manager.get_or_create(task_ctx.conversation_id)
        compiled_input = self._compile_provider_input(
            task_ctx=task_ctx,
            prompt=prompt,
            raw_text=raw_text or prompt,
        )
        if hasattr(self.task_controller, "update_attempt_phase"):
            self.task_controller.update_attempt_phase(task_ctx.step_attempt_id, phase="executing")
        result = self.agent.run(
            prompt,
            compiled_messages=compiled_input.messages if compiled_input is not None else None,
            message_history=None if compiled_input is not None else list(session.messages),
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
            disable_tools=disable_tools,
            readonly_only=readonly_only,
            task_context=task_ctx,
        )
        session.total_input_tokens += result.input_tokens
        session.total_output_tokens += result.output_tokens
        session.total_cache_read_tokens += result.cache_read_tokens
        session.total_cache_creation_tokens += result.cache_creation_tokens
        session.messages = result.messages
        self.session_manager.save(session)
        if result.suspended or result.blocked:
            if not getattr(result, "status_managed_by_kernel", False):
                self.task_controller.mark_suspended(
                    task_ctx,
                    waiting_kind=str(getattr(result, "waiting_kind", "") or "awaiting_approval"),
                )
        else:
            planning_captured = self._maybe_capture_planning_result(
                task_ctx, result, readonly_only=readonly_only
            )
            if not getattr(result, "status_managed_by_kernel", False) and not planning_captured:
                self.task_controller.finalize_result(
                    task_ctx,
                    status=self._result_status(result),
                    result_preview=_result_preview(result.text or ""),
                    result_text=result.text or "",
                )
            self.pm.on_post_run(
                result, session_id=task_ctx.conversation_id, session=session, runner=self
            )
        return result

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
        parent_task_id: str | None | object = _AUTO_PARENT,
    ):
        source = source_channel or self.task_controller.source_from_session(session_id)
        _session, full_prompt, run_opts, task_goal = self._prepare_prompt_context(
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
            policy_profile="readonly" if run_opts.get("readonly_only", False) else "default",
            workspace_root=str(getattr(self.agent, "workspace_root", "") or ""),
            parent_task_id=parent_task_id,
            requested_by=requested_by,
            ingress_metadata=metadata,
            source_ref=source_ref or None,
        )
        if run_opts.get("planning_mode", False):
            planning = PlanningService(
                self.task_controller.store, getattr(self.agent, "artifact_store", None)
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
        from hermit.kernel.approvals import ApprovalService

        approval = self.task_controller.store.get_approval(approval_id)
        if approval is None:
            return DispatchResult(
                _t("kernel.runner.approval_not_found", runner=self, approval_id=approval_id),
                is_command=True,
            )

        session = self.session_manager.get_or_create(session_id)
        approvals = ApprovalService(self.task_controller.store)
        if action == "deny":
            approvals.deny(approval_id, resolved_by="user", reason=reason)
            text = _t("kernel.runner.approval_denied", runner=self)
            messages = list(session.messages)
            messages.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
            session.messages = messages
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
            runner=self,
            default="Approval granted. The task is queued to resume.",
        )
        messages = list(session.messages)
        messages.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
        session.messages = messages
        self.session_manager.save(session)
        return DispatchResult(text=text, is_command=True)

    def process_claimed_attempt(
        self,
        step_attempt_id: str,
        *,
        on_tool_call: Optional[ToolCallback] = None,
        on_tool_start: Optional[ToolStartCallback] = None,
    ) -> AgentResult:
        task_ctx = self.task_controller.context_for_attempt(step_attempt_id)
        session_id = task_ctx.conversation_id
        self.task_controller.ensure_conversation(session_id, source_channel=task_ctx.source_channel)
        session = self.session_manager.get_or_create(session_id)
        self._ensure_session_started(session_id)

        metadata = dict(task_ctx.ingress_metadata or {})
        execution_mode = str(
            self.task_controller.store.get_step_attempt(step_attempt_id).context.get(
                "execution_mode", "run"
            )  # type: ignore[union-attr]
            if self.task_controller.store.get_step_attempt(step_attempt_id) is not None
            else "run"
        )
        started_at = time.time()
        try:
            if execution_mode == "resume":
                if hasattr(self.task_controller, "update_attempt_phase"):
                    self.task_controller.update_attempt_phase(step_attempt_id, phase="executing")
                result = self.agent.resume(
                    step_attempt_id=step_attempt_id,
                    task_context=task_ctx,
                    on_tool_call=on_tool_call,
                    on_tool_start=on_tool_start,
                )
            else:
                compiled_input = self._compile_provider_input(
                    task_ctx=task_ctx,
                    prompt=str(metadata.get("entry_prompt", "") or ""),
                    raw_text=str(
                        metadata.get("raw_text", "") or str(metadata.get("entry_prompt", "") or "")
                    ),
                )
                if hasattr(self.task_controller, "update_attempt_phase"):
                    self.task_controller.update_attempt_phase(step_attempt_id, phase="executing")
                result = self.agent.run(
                    str(metadata.get("entry_prompt", "") or ""),
                    compiled_messages=compiled_input.messages
                    if compiled_input is not None
                    else None,
                    message_history=None if compiled_input is not None else list(session.messages),
                    on_tool_call=on_tool_call,
                    on_tool_start=on_tool_start,
                    disable_tools=bool(metadata.get("disable_tools", False)),
                    readonly_only=bool(metadata.get("readonly_only", False)),
                    task_context=task_ctx,
                )
        except Exception as exc:
            result = AgentResult(
                text=f"[API Error] {exc}",
                turns=0,
                tool_calls=0,
                messages=list(session.messages),
                task_id=task_ctx.task_id,
                step_id=task_ctx.step_id,
                step_attempt_id=task_ctx.step_attempt_id,
                execution_status="failed",
            )

        session.total_input_tokens += result.input_tokens
        session.total_output_tokens += result.output_tokens
        session.total_cache_read_tokens += result.cache_read_tokens
        session.total_cache_creation_tokens += result.cache_creation_tokens
        session.messages = result.messages
        self.session_manager.save(session)

        if result.suspended or result.blocked:
            if not getattr(result, "status_managed_by_kernel", False):
                self.task_controller.mark_suspended(
                    task_ctx,
                    waiting_kind=str(getattr(result, "waiting_kind", "") or "awaiting_approval"),
                )
            return result

        status = self._result_status(result)
        planning_captured = self._maybe_capture_planning_result(
            task_ctx,
            result,
            readonly_only=bool(metadata.get("readonly_only", False)),
        )
        if not getattr(result, "status_managed_by_kernel", False) and not planning_captured:
            self.task_controller.finalize_result(
                task_ctx,
                status=status,
                result_preview=_result_preview(result.text or ""),
                result_text=result.text or "",
            )
        self.pm.on_post_run(result, session_id=session_id, session=session, runner=self)
        delivery_results = self._emit_async_dispatch_result(task_ctx, result, started_at=started_at)
        self._record_scheduler_execution(
            task_ctx, result, started_at=started_at, delivery_results=delivery_results
        )
        return result

    def _emit_async_dispatch_result(
        self,
        task_ctx: TaskExecutionContext,
        result: AgentResult,
        *,
        started_at: float,
    ) -> list[object]:
        notify = dict(task_ctx.ingress_metadata.get("notify", {}) or {})
        if not notify:
            return []
        success = self._result_status(result) == "succeeded"
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

    def _record_scheduler_execution(
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
        from hermit.builtin.scheduler.models import JobExecutionRecord

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
            success=self._result_status(result) == "succeeded",
            result_text=result.text or "",
            error=None if self._result_status(result) == "succeeded" else (result.text or ""),
        )
        delivery = next(
            (
                item
                for item in (delivery_results or [])
                if isinstance(item, dict) and item.get("channel") == "feishu"
            ),
            None,
        )
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
                {"records": [item.to_dict() for item in history]}, ensure_ascii=False, indent=2
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

    # ------------------------------------------------------------------
    # Public dispatch entry point
    # ------------------------------------------------------------------

    def dispatch(
        self,
        session_id: str,
        text: str,
        on_tool_call: Optional[ToolCallback] = None,
        on_tool_start: Optional[ToolStartCallback] = None,
    ) -> DispatchResult:
        """Route a raw user message: slash commands are handled here; everything
        else is forwarded to the agent.
        """
        stripped = text.strip()
        if self.task_controller is not None:
            resolution = self.task_controller.resolve_text_command(session_id, stripped)
            if resolution is not None:
                action, target_id, reason = resolution
                return self._dispatch_control_action(
                    session_id,
                    action=action,
                    target_id=target_id,
                    reason=reason,
                    on_tool_call=on_tool_call,
                    on_tool_start=on_tool_start,
                )
        if stripped.startswith("/"):
            cmd = stripped.split()[0].lower()
            entry = self._commands.get(cmd)
            if entry:
                handler, _help, _cli = entry
                return handler(self, session_id, stripped)
            return DispatchResult(
                text=_t("kernel.runner.unknown_command", runner=self, cmd=cmd),
                is_command=True,
            )

        agent_result = self.handle(
            session_id,
            text,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
        )
        return DispatchResult(
            text=agent_result.text or "",
            agent_result=agent_result,
        )

    def handle(
        self,
        session_id: str,
        text: str,
        on_tool_call: Optional[ToolCallback] = None,
        on_tool_start: Optional[ToolStartCallback] = None,
    ) -> AgentResult:
        """Process a single user message within a session."""
        source_channel = (
            self.task_controller.source_from_session(session_id) if self.task_controller else "chat"
        )
        session, prompt, run_opts, task_goal = self._prepare_prompt_context(
            session_id,
            text,
            source_channel=source_channel,
        )

        ingress = None
        if self.task_controller is not None and hasattr(self.task_controller, "decide_ingress"):
            ingress = self.task_controller.decide_ingress(
                conversation_id=session_id,
                source_channel=source_channel,
                raw_text=text,
                prompt=prompt,
            )
            if str(getattr(ingress, "resolution", "") or "") == "pending_disambiguation":
                return AgentResult(
                    text=self._pending_disambiguation_text(ingress),
                    turns=0,
                    tool_calls=0,
                    messages=list(session.messages),
                    execution_status="pending_disambiguation",
                    status_managed_by_kernel=True,
                )
            if ingress.mode == "append_note":
                normalized = None
                try:
                    append_ctx = self._append_note_context(
                        session_id, ingress.task_id or "", source_channel
                    )
                    normalized = self._provider_input_compiler().normalize_ingress(
                        task_context=append_ctx,
                        raw_text=text,
                        final_prompt=prompt,
                    )
                except RuntimeError:
                    normalized = None
                if ingress.note_event_seq is None:
                    self.task_controller.append_note(
                        task_id=ingress.task_id or "",
                        source_channel=source_channel,
                        raw_text=text,
                        prompt=prompt,
                        normalized_payload=normalized,
                        ingress_id=getattr(ingress, "ingress_id", None),
                    )
                return AgentResult(
                    text=_t(
                        "kernel.runner.note_appended",
                        runner=self,
                        default="Attached to the current task. It will be applied at the next durable boundary.",
                    ),
                    turns=0,
                    tool_calls=0,
                    messages=list(session.messages),
                    task_id=ingress.task_id,
                    execution_status="note_appended",
                    status_managed_by_kernel=True,
                )
        parent_task_id = (
            getattr(ingress, "parent_task_id", _AUTO_PARENT)
            if ingress is not None
            else _AUTO_PARENT
        )
        ingress_metadata: dict[str, object] = {}
        if ingress is not None:
            ingress_metadata.update(
                {
                    "ingress_id": str(getattr(ingress, "ingress_id", "") or ""),
                    "ingress_intent": str(getattr(ingress, "intent", "") or ""),
                    "ingress_reason": str(getattr(ingress, "reason", "") or ""),
                    "ingress_resolution": str(getattr(ingress, "resolution", "") or ""),
                    "binding_reason_codes": list(getattr(ingress, "reason_codes", []) or []),
                }
            )
            if getattr(ingress, "anchor_task_id", None):
                ingress_metadata["continuation_anchor"] = dict(
                    getattr(ingress, "continuation_anchor", {}) or {}
                )

        task_ctx = None
        if self.task_controller is not None:
            task_kind = "plan" if run_opts.get("readonly_only", False) else "respond"
            task_ctx = self.task_controller.start_task(
                conversation_id=session_id,
                goal=task_goal,
                source_channel=source_channel,
                kind=task_kind,
                policy_profile="readonly" if run_opts.get("readonly_only", False) else "default",
                workspace_root=str(getattr(self.agent, "workspace_root", "") or ""),
                parent_task_id=parent_task_id,
                ingress_metadata=ingress_metadata,
            )
            if run_opts.get("planning_mode", False):
                planning = PlanningService(
                    self.task_controller.store, getattr(self.agent, "artifact_store", None)
                )
                planning.set_pending_for_conversation(session_id, enabled=False)
                planning.enter_planning(task_ctx.task_id)

        compiled_input = (
            self._compile_provider_input(
                task_ctx=task_ctx,
                prompt=prompt,
                raw_text=text,
            )
            if task_ctx is not None
            else None
        )
        if task_ctx is not None and hasattr(self.task_controller, "update_attempt_phase"):
            self.task_controller.update_attempt_phase(task_ctx.step_attempt_id, phase="executing")
        result = self.agent.run(
            prompt,
            compiled_messages=compiled_input.messages if compiled_input is not None else None,
            message_history=None if compiled_input is not None else list(session.messages),
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
            disable_tools=run_opts.get("disable_tools", False),
            readonly_only=run_opts.get("readonly_only", False),
            task_context=task_ctx,
        )

        session.total_input_tokens += result.input_tokens
        session.total_output_tokens += result.output_tokens
        session.total_cache_read_tokens += result.cache_read_tokens
        session.total_cache_creation_tokens += result.cache_creation_tokens

        session.messages = result.messages
        self.session_manager.save(session)
        if self.task_controller is not None and task_ctx is not None:
            if result.suspended or result.blocked:
                if getattr(result, "status_managed_by_kernel", False):
                    return result
                if hasattr(self.task_controller, "mark_suspended"):
                    self.task_controller.mark_suspended(
                        task_ctx,
                        waiting_kind=str(
                            getattr(result, "waiting_kind", "") or "awaiting_approval"
                        ),
                    )
                else:
                    self.task_controller.mark_blocked(task_ctx)
            else:
                planning_captured = self._maybe_capture_planning_result(
                    task_ctx,
                    result,
                    readonly_only=bool(run_opts.get("readonly_only", False)),
                )
                status = self._result_status(result)
                if not getattr(result, "status_managed_by_kernel", False) and not planning_captured:
                    self.task_controller.finalize_result(
                        task_ctx,
                        status=status,
                        result_preview=_result_preview(result.text or ""),
                        result_text=result.text or "",
                    )
        if not (result.suspended or result.blocked):
            self.pm.on_post_run(result, session_id=session_id, session=session, runner=self)
        return result

    @staticmethod
    def _result_status(result: AgentResult) -> str:
        explicit = str(getattr(result, "execution_status", "") or "").strip()
        if explicit:
            return explicit
        text = result.text or ""
        if text.startswith("[Execution Requires Attention]"):
            return "needs_attention"
        if text.startswith("[API Error]") or text.startswith("[Policy Denied]"):
            return "failed"
        return "succeeded"

    def close_session(self, session_id: str) -> None:
        """End a session, fire hooks, and archive."""
        session = self.session_manager.get_or_create(session_id)
        self.pm.on_session_end(session_id, session.messages)
        self.session_manager.close(session_id)
        self._session_started.discard(session_id)

    def resume_attempt(
        self,
        step_attempt_id: str,
        *,
        on_tool_call: Optional[ToolCallback] = None,
        on_tool_start: Optional[ToolStartCallback] = None,
    ) -> AgentResult:
        resume_attempt = getattr(self.task_controller, "resume_attempt", None)
        task_ctx = (
            resume_attempt(step_attempt_id)
            if callable(resume_attempt)
            else self.task_controller.context_for_attempt(step_attempt_id)
        )
        session = self.session_manager.get_or_create(task_ctx.conversation_id)
        if hasattr(self.task_controller, "update_attempt_phase"):
            self.task_controller.update_attempt_phase(task_ctx.step_attempt_id, phase="executing")
        result = self.agent.resume(
            step_attempt_id=step_attempt_id,
            task_context=task_ctx,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
        )
        session.total_input_tokens += result.input_tokens
        session.total_output_tokens += result.output_tokens
        session.total_cache_read_tokens += result.cache_read_tokens
        session.total_cache_creation_tokens += result.cache_creation_tokens
        session.messages = result.messages
        self.session_manager.save(session)
        if result.suspended or result.blocked:
            if not getattr(result, "status_managed_by_kernel", False):
                if hasattr(self.task_controller, "mark_suspended"):
                    self.task_controller.mark_suspended(
                        task_ctx,
                        waiting_kind=str(
                            getattr(result, "waiting_kind", "") or "awaiting_approval"
                        ),
                    )
                else:
                    self.task_controller.mark_blocked(task_ctx)
        else:
            status = self._result_status(result)
            if not getattr(result, "status_managed_by_kernel", False):
                self.task_controller.finalize_result(
                    task_ctx,
                    status=status,
                    result_preview=_result_preview(result.text or ""),
                    result_text=result.text or "",
                )
            self.pm.on_post_run(
                result, session_id=task_ctx.conversation_id, session=session, runner=self
            )
        return result

    def reset_session(self, session_id: str) -> None:
        """Close current session and start a fresh one."""
        self.close_session(session_id)
        self.session_manager.get_or_create(session_id)
        self.pm.on_session_start(session_id)
        self._session_started.add(session_id)

    def _dispatch_control_action(
        self,
        session_id: str,
        *,
        action: str,
        target_id: str,
        reason: str = "",
        on_tool_call: Optional[ToolCallback] = None,
        on_tool_start: Optional[ToolStartCallback] = None,
    ) -> DispatchResult:
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
            self.reset_session(session_id)
            return DispatchResult(_t("kernel.runner.new_session", runner=self), is_command=True)
        if action == "focus_task":
            resolved = self.task_controller.focus_task(session_id, target_id)
            message = _t(
                "kernel.runner.focus_task",
                runner=self,
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
            session = self.session_manager.get_or_create(session_id)
            user_turns = sum(1 for m in session.messages if m.get("role") == "user")
            total = len(session.messages)
            return DispatchResult(
                _t(
                    "kernel.runner.history_summary",
                    runner=self,
                    user_turns=user_turns,
                    total=total,
                ),
                is_command=True,
            )
        if action == "show_help":
            lines = [_t("kernel.runner.help.title", runner=self)]
            for cmd, (_fn, help_text, cli_only) in sorted(self._commands.items()):
                if self.serve_mode and cli_only:
                    continue
                lines.append(f"- `{cmd}` — {_resolve_help_text(help_text, runner=self)}")
            return DispatchResult("\n".join(lines), is_command=True)

        store = getattr(getattr(self, "agent", None), "kernel_store", None)
        if store is None:
            return DispatchResult(
                text=_t("kernel.runner.task_kernel_unavailable", runner=self),
                is_command=True,
            )
        planning = (
            PlanningService(store, getattr(self.agent, "artifact_store", None))
            if hasattr(store, "ensure_conversation")
            else None
        )
        get_latest_task = getattr(store, "get_last_task_for_conversation", None)
        latest_task = get_latest_task(session_id) if callable(get_latest_task) else None
        resolved_target = target_id or (latest_task.task_id if latest_task is not None else "")

        if action == "plan_enter":
            if planning is None:
                return DispatchResult(
                    text=_t("kernel.runner.task_kernel_unavailable", runner=self), is_command=True
                )
            if resolved_target:
                planning.enter_planning(resolved_target)
            else:
                planning.set_pending_for_conversation(session_id, enabled=True)
            plans_dir = getattr(
                getattr(self.agent, "artifact_store", None), "root_dir", "kernel artifact store"
            )
            return DispatchResult(
                _t("kernel.planner.entered", runner=self, plans_hint=plans_dir),
                is_command=True,
            )
        if action == "plan_exit":
            if planning is None:
                return DispatchResult(
                    text=_t("kernel.runner.task_kernel_unavailable", runner=self), is_command=True
                )
            planning.set_pending_for_conversation(session_id, enabled=False)
            if resolved_target:
                planning.exit_planning(resolved_target)
            return DispatchResult(_t("kernel.planner.closed", runner=self), is_command=True)
        if action == "plan_confirm":
            if planning is None:
                return DispatchResult(
                    text=_t("kernel.runner.task_kernel_unavailable", runner=self), is_command=True
                )
            if not resolved_target:
                return DispatchResult(
                    _t("kernel.planner.confirm_missing_plan", runner=self),
                    is_command=True,
                )
            plan_text = planning.load_selected_plan_text(resolved_target)
            plan_ctx = planning.latest_planning_attempt(resolved_target)
            if not plan_text or plan_ctx is None:
                return DispatchResult(
                    _t("kernel.planner.confirm_missing_plan", runner=self),
                    is_command=True,
                )
            planning.confirm_selected_plan(plan_ctx, actor="user")
            latest_attempt = self.task_controller.store.get_step_attempt(plan_ctx.step_attempt_id)
            if (
                latest_attempt is not None
                and str(latest_attempt.status or "") == "awaiting_plan_confirmation"
            ):
                self.task_controller.store.update_step(plan_ctx.step_id, status="succeeded")
                self.task_controller.store.update_step_attempt(
                    plan_ctx.step_attempt_id,
                    status="succeeded",
                    waiting_reason=None,
                )
            execution_ctx = self.task_controller.start_followup_step(
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
                runner=self,
                plan_content=plan_text,
            )
            result = self._run_existing_task(
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

        if action == "task_list":
            payload = [task.__dict__ for task in store.list_tasks(limit=20)]
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "case":
            from hermit.kernel.supervision import SupervisionService

            payload = SupervisionService(store).build_task_case(target_id)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "task_events":
            payload = store.list_events(task_id=target_id, limit=100)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "task_receipts":
            payload = [
                receipt.__dict__ for receipt in store.list_receipts(task_id=target_id, limit=50)
            ]
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "task_proof":
            from hermit.kernel.proofs import ProofService

            payload = ProofService(store).build_proof_summary(target_id)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "task_proof_export":
            from hermit.kernel.proofs import ProofService

            payload = ProofService(store).export_task_proof(target_id)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "rollback":
            from hermit.kernel.rollbacks import RollbackService

            payload = RollbackService(store).execute(target_id)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "projection_rebuild":
            from hermit.kernel.projections import ProjectionService

            payload = ProjectionService(store).rebuild_task(target_id)
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "projection_rebuild_all":
            from hermit.kernel.projections import ProjectionService

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
            grant = store.get_capability_grant(target_id)
            if grant is None:
                return DispatchResult(
                    text=_t("kernel.runner.grant_not_found", runner=self, grant_id=target_id),
                    is_command=True,
                )
            store.update_capability_grant(
                target_id,
                status="revoked",
                revoked_at=time.time(),
            )
            return DispatchResult(
                text=_t("kernel.runner.grant_revoked", runner=self, grant_id=target_id),
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
                for record in store.list_schedule_history(job_id=target_id or None, limit=10)
            ]
            return DispatchResult(
                text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True
            )
        if action == "schedule_enable":
            job = store.update_schedule(target_id, enabled=True)
            message = (
                _t("kernel.runner.schedule.enabled", runner=self, job_id=target_id)
                if job is not None
                else _t("kernel.runner.schedule.not_found", runner=self, job_id=target_id)
            )
            return DispatchResult(text=message, is_command=True)
        if action == "schedule_disable":
            job = store.update_schedule(target_id, enabled=False)
            message = (
                _t("kernel.runner.schedule.disabled", runner=self, job_id=target_id)
                if job is not None
                else _t("kernel.runner.schedule.not_found", runner=self, job_id=target_id)
            )
            return DispatchResult(text=message, is_command=True)
        if action == "schedule_remove":
            deleted = store.delete_schedule(target_id)
            message = (
                _t("kernel.runner.schedule.removed", runner=self, job_id=target_id)
                if deleted
                else _t("kernel.runner.schedule.not_found", runner=self, job_id=target_id)
            )
            return DispatchResult(text=message, is_command=True)
        return DispatchResult(
            text=_t("kernel.runner.unsupported_control_action", runner=self, action=action),
            is_command=True,
        )

    def _pending_disambiguation_text(self, ingress: object) -> str:
        candidates = list(getattr(ingress, "candidates", []) or [])
        if not candidates:
            return _t(
                "kernel.runner.pending_disambiguation.none",
                runner=self,
                default="I couldn't determine which task to continue. Please switch focus first.",
            )
        lines = [
            _t(
                "kernel.runner.pending_disambiguation.intro",
                runner=self,
                default="I couldn't determine which task to continue. Try one of these commands:",
            )
        ]
        for item in candidates[:3]:
            task_id = str(dict(item).get("task_id", "") or "").strip()
            if task_id:
                lines.append(
                    _t(
                        "kernel.runner.pending_disambiguation.item",
                        runner=self,
                        default="- switch task {task_id}",
                        task_id=task_id,
                    )
                )
        return "\n".join(lines)

    def _resolve_approval(
        self,
        session_id: str,
        *,
        action: str,
        approval_id: str,
        reason: str = "",
        on_tool_call: Optional[ToolCallback] = None,
        on_tool_start: Optional[ToolStartCallback] = None,
    ) -> DispatchResult:
        from hermit.kernel.approvals import ApprovalService

        approval = self.task_controller.store.get_approval(approval_id)
        if approval is None:
            return DispatchResult(
                _t("kernel.runner.approval_not_found", runner=self, approval_id=approval_id),
                is_command=True,
            )

        session = self.session_manager.get_or_create(session_id)
        approvals = ApprovalService(self.task_controller.store)
        if action == "deny":
            approvals.deny(approval_id, resolved_by="user", reason=reason)
            text = _t("kernel.runner.approval_denied", runner=self)
            messages = list(session.messages)
            messages.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
            session.messages = messages
            self.session_manager.save(session)
            return DispatchResult(text=text, is_command=True)

        if action == "approve_mutable_workspace":
            approvals.approve_mutable_workspace(approval_id, resolved_by="user")
        else:
            approvals.approve_once(approval_id, resolved_by="user")
        task_ctx = self.task_controller.context_for_attempt(approval.step_attempt_id)
        result = self.agent.resume(
            step_attempt_id=approval.step_attempt_id,
            task_context=task_ctx,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
        )
        session.total_input_tokens += result.input_tokens
        session.total_output_tokens += result.output_tokens
        session.total_cache_read_tokens += result.cache_read_tokens
        session.total_cache_creation_tokens += result.cache_creation_tokens
        session.messages = result.messages
        self.session_manager.save(session)
        if result.suspended or result.blocked:
            if not getattr(result, "status_managed_by_kernel", False):
                if hasattr(self.task_controller, "mark_suspended"):
                    self.task_controller.mark_suspended(
                        task_ctx,
                        waiting_kind=str(
                            getattr(result, "waiting_kind", "") or "awaiting_approval"
                        ),
                    )
                else:
                    self.task_controller.mark_blocked(task_ctx)
        else:
            status = self._result_status(result)
            if not getattr(result, "status_managed_by_kernel", False):
                self.task_controller.finalize_result(
                    task_ctx,
                    status=status,
                    result_preview=_result_preview(result.text or ""),
                    result_text=result.text or "",
                )
            self.pm.on_post_run(result, session_id=session_id, session=session, runner=self)
        return DispatchResult(
            text=result.text or "",
            is_command=False,
            agent_result=result,
        )


# ------------------------------------------------------------------
# Core slash commands (always available, not from plugins)
# ------------------------------------------------------------------


@AgentRunner.register_command("/new", "kernel.runner.command.new.help")
def _cmd_new(runner: AgentRunner, session_id: str, _text: str) -> DispatchResult:
    runner.reset_session(session_id)
    return DispatchResult(_t("kernel.runner.new_session", runner=runner), is_command=True)


@AgentRunner.register_command("/history", "kernel.runner.command.history.help")
def _cmd_history(runner: AgentRunner, session_id: str, _text: str) -> DispatchResult:
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


@AgentRunner.register_command("/quit", "kernel.runner.command.quit.help", cli_only=True)
def _cmd_quit(_runner: AgentRunner, _session_id: str, _text: str) -> DispatchResult:
    return DispatchResult(
        _t("kernel.runner.quit", runner=_runner),
        is_command=True,
        should_exit=True,
    )


@AgentRunner.register_command("/help", "kernel.runner.command.help.help")
def _cmd_help(runner: AgentRunner, _session_id: str, _text: str) -> DispatchResult:
    lines = [_t("kernel.runner.help.title", runner=runner)]
    for cmd, (_fn, help_text, cli_only) in sorted(runner._commands.items()):
        if runner.serve_mode and cli_only:
            continue
        lines.append(f"- `{cmd}` — {_resolve_help_text(help_text, runner=runner)}")
    return DispatchResult("\n".join(lines), is_command=True)


@AgentRunner.register_command("/task", "kernel.runner.command.task.help")
def _cmd_task(runner: AgentRunner, session_id: str, text: str) -> DispatchResult:
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 3 or parts[1] not in {"approve", "deny", "case", "rollback"}:
        return DispatchResult(
            _t("kernel.runner.task_usage", runner=runner),
            is_command=True,
        )
    action = parts[1]
    target_id = parts[2].strip()
    mapped_action = {
        "approve": "approve_once",
        "deny": "deny",
        "case": "case",
        "rollback": "rollback",
    }[action]
    return runner._dispatch_control_action(session_id, action=mapped_action, target_id=target_id)
