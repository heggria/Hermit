from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, Optional

from hermit.core.session import SessionManager, sanitize_session_messages
from hermit.i18n import resolve_locale, tr
from hermit.kernel.controller import TaskController
from hermit.kernel.observation import ObservationService
from hermit.provider.runtime import AgentResult, AgentRuntime, ToolCallback, ToolStartCallback

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
            raise ValueError("AgentRunner requires a TaskController; non-kernel runner mode has been removed.")
        self.agent = agent
        self.session_manager = session_manager
        self.pm = plugin_manager
        self.serve_mode = serve_mode
        self.task_controller = task_controller
        self._session_started: set[str] = set()
        self._observation_service: ObservationService | None = None
        # Instance-level copy: core commands + plugin commands added later via add_command()
        self._commands: Dict[str, tuple[CommandHandler, str, bool]] = dict(self._core_commands)

    def start_background_services(self) -> None:
        if self._observation_service is None:
            self._observation_service = ObservationService(self)
        self._observation_service.start()

    def stop_background_services(self) -> None:
        if self._observation_service is not None:
            self._observation_service.stop()

    def add_command(
        self, name: str, handler: CommandHandler, help_text: str, cli_only: bool = False,
    ) -> None:
        """Register a command on this runner instance (used by plugins)."""
        self._commands[name] = (handler, help_text, cli_only)

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
            session_id, text,
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
        session = self.session_manager.get_or_create(session_id)
        sanitized_messages = sanitize_session_messages(session.messages)
        if sanitized_messages != session.messages:
            session.messages = sanitized_messages
            self.session_manager.save(session)
        source_channel = self.task_controller.source_from_session(session_id) if self.task_controller else "chat"

        if session_id not in self._session_started:
            self.pm.on_session_start(session_id)
            self._session_started.add(session_id)

        prompt, run_opts = self.pm.on_pre_run(
            text, session_id=session_id, session=session, messages=list(session.messages),
            runner=self,
        )

        now = datetime.datetime.now()
        session_started = datetime.datetime.fromtimestamp(session.created_at)
        time_ctx = (
            f"<session_time>"
            f"session_started_at={session_started.strftime('%Y-%m-%d %H:%M:%S')} "
            f"message_sent_at={now.strftime('%Y-%m-%d %H:%M:%S')}"
            f"</session_time>\n\n"
        )
        prompt = time_ctx + prompt
        task_goal = _strip_internal_markup(text) or _strip_internal_markup(prompt) or text.strip() or prompt.strip()

        if self.task_controller is not None and hasattr(self.task_controller, "decide_ingress"):
            ingress = self.task_controller.decide_ingress(
                conversation_id=session_id,
                source_channel=source_channel,
                raw_text=text,
                prompt=prompt,
            )
            if ingress.mode == "append_note":
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
            )

        result = self.agent.run(
            prompt,
            message_history=list(session.messages),
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
            disable_tools=run_opts.get("disable_tools", False),
            readonly_only=run_opts.get("readonly_only", False),
            task_context=task_ctx,
        )

        session.total_input_tokens      += result.input_tokens
        session.total_output_tokens     += result.output_tokens
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
                        waiting_kind=str(getattr(result, "waiting_kind", "") or "awaiting_approval"),
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
        task_ctx = resume_attempt(step_attempt_id) if callable(resume_attempt) else self.task_controller.context_for_attempt(step_attempt_id)
        session = self.session_manager.get_or_create(task_ctx.conversation_id)
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
                        waiting_kind=str(getattr(result, "waiting_kind", "") or "awaiting_approval"),
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
                )
            self.pm.on_post_run(result, session_id=task_ctx.conversation_id, session=session, runner=self)
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
        if action in {"approve_once", "approve_always_directory", "deny"}:
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

        if action == "task_list":
            payload = [task.__dict__ for task in store.list_tasks(limit=20)]
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
        if action == "case":
            from hermit.kernel.supervision import SupervisionService

            payload = SupervisionService(store).build_task_case(target_id)
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
        if action == "task_events":
            payload = store.list_events(task_id=target_id, limit=100)
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
        if action == "task_receipts":
            payload = [receipt.__dict__ for receipt in store.list_receipts(task_id=target_id, limit=50)]
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
        if action == "task_proof":
            from hermit.kernel.proofs import ProofService

            payload = ProofService(store).build_proof_summary(target_id)
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
        if action == "task_proof_export":
            from hermit.kernel.proofs import ProofService

            payload = ProofService(store).export_task_proof(target_id)
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
        if action == "rollback":
            from hermit.kernel.rollbacks import RollbackService

            payload = RollbackService(store).execute(target_id)
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
        if action == "projection_rebuild":
            from hermit.kernel.projections import ProjectionService

            payload = ProjectionService(store).rebuild_task(target_id)
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
        if action == "projection_rebuild_all":
            from hermit.kernel.projections import ProjectionService

            payload = ProjectionService(store).rebuild_all()
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
        if action == "grant_list":
            payload = [
                grant.__dict__
                for grant in store.list_path_grants(
                    subject_kind="conversation",
                    subject_ref=session_id,
                    limit=50,
                )
            ]
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
        if action == "grant_revoke":
            grant = store.get_path_grant(target_id)
            if grant is None:
                return DispatchResult(
                    text=_t("kernel.runner.grant_not_found", runner=self, grant_id=target_id),
                    is_command=True,
                )
            store.update_path_grant(
                target_id,
                status="revoked",
                actor="user",
                event_type="grant.revoked",
                payload={"status": "revoked"},
            )
            return DispatchResult(
                text=_t("kernel.runner.grant_revoked", runner=self, grant_id=target_id),
                is_command=True,
            )
        if action == "schedule_list":
            payload = [job.to_dict() for job in store.list_schedules()]
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
        if action == "schedule_history":
            payload = [
                record.to_dict() for record in store.list_schedule_history(job_id=target_id or None, limit=10)
            ]
            return DispatchResult(text=json.dumps(payload, ensure_ascii=False, indent=2), is_command=True)
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
        approval = self.task_controller.store.get_approval(approval_id)
        if approval is None:
            return DispatchResult(
                _t("kernel.runner.approval_not_found", runner=self, approval_id=approval_id),
                is_command=True,
            )

        session = self.session_manager.get_or_create(session_id)
        if action == "deny":
            self.task_controller.store.resolve_approval(
                approval_id,
                status="denied",
                resolved_by="user",
                resolution={"status": "denied", "mode": "denied", "reason": reason},
            )
            text = _t("kernel.runner.approval_denied", runner=self)
            messages = list(session.messages)
            messages.append({"role": "assistant", "content": [{"type": "text", "text": text}]})
            session.messages = messages
            self.session_manager.save(session)
            return DispatchResult(text=text, is_command=True)

        if action == "approve_always_directory":
            self.task_controller.store.resolve_approval(
                approval_id,
                status="granted",
                resolved_by="user",
                resolution={"status": "granted", "mode": "always_directory"},
            )
        else:
            self.task_controller.store.resolve_approval(
                approval_id,
                status="granted",
                resolved_by="user",
                resolution={"status": "granted", "mode": "once"},
            )
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
                        waiting_kind=str(getattr(result, "waiting_kind", "") or "awaiting_approval"),
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
    mapped_action = {"approve": "approve_once", "deny": "deny", "case": "case", "rollback": "rollback"}[action]
    return runner._dispatch_control_action(session_id, action=mapped_action, target_id=target_id)
