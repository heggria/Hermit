from __future__ import annotations

import datetime  # noqa: F401 — used by test monkeypatching (runner_module.datetime)
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from hermit.kernel.execution.coordination.observation import ObservationService
from hermit.kernel.task.services.controller import AUTO_PARENT, TaskController
from hermit.runtime.capability.contracts.base import HookEvent
from hermit.runtime.control.lifecycle.budgets import get_runtime_budget
from hermit.runtime.control.lifecycle.session import SessionManager
from hermit.runtime.provider_host.execution.runtime import (
    AgentResult,
    AgentRuntime,
    ToolCallback,
    ToolStartCallback,
)

if TYPE_CHECKING:
    from hermit.kernel.context.injection.provider_input import ProviderInputCompiler
    from hermit.kernel.context.models.context import CompiledProviderInput, TaskExecutionContext
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.capability.registry.manager import PluginManager
    from hermit.runtime.control.lifecycle.session import Session
    from hermit.runtime.control.runner.message_compiler import MessageCompiler

from hermit.runtime.control.runner.utils import (
    DispatchResult,
    _t,
    _trim_session_messages,
    result_preview,
)

CommandHandler = Callable[["AgentRunner", str, str], "DispatchResult"]


def _resolve_help_text(help_text: str, *, runner: AgentRunner | None = None) -> str:
    from hermit.infra.system.i18n import resolve_locale, tr

    settings = getattr(getattr(runner, "pm", None), "settings", None)
    locale = resolve_locale(getattr(settings, "locale", None))
    return tr(help_text, locale=locale, default=help_text)


class AgentRunner:
    """Unified orchestration layer: session + agent + plugin hooks.

    Both CLI commands and adapter plugins call this instead of
    duplicating the get_session -> run -> save -> hooks flow.

    Heavy logic is delegated to extracted handler modules:
    - MessageCompiler: prompt context preparation, provider input compilation
    - SessionContextBuilder: session lifecycle helpers
    - RunnerTaskExecutor: task execution through the agent loop
    - AsyncDispatcher: async ingress, approval resume, dispatch results
    - ControlActionDispatcher: control action dispatch
    """

    # Class-level registry for core commands (populated by decorators at import time).
    _core_commands: dict[str, tuple[CommandHandler, str, bool]] = {}

    @classmethod
    def register_command(
        cls, name: str, help_text: str, cli_only: bool = False
    ) -> Callable[[CommandHandler], CommandHandler]:
        """Decorator to register a core slash command."""

        def decorator(fn: CommandHandler) -> CommandHandler:
            cls._core_commands[name] = (fn, help_text, cli_only)
            return fn

        return decorator

    @classmethod
    def core_command_specs(cls) -> dict[str, tuple[CommandHandler, str, bool]]:
        return dict(cls._core_commands)

    @property
    def command_specs(self) -> dict[str, tuple[CommandHandler, str, bool]]:
        """Public accessor for the instance command registry."""
        return self._commands

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
        self._commands: dict[str, tuple[CommandHandler, str, bool]] = dict(self._core_commands)
        self._dispatch_service: object | None = None

    # ------------------------------------------------------------------
    # Background services
    # ------------------------------------------------------------------

    def start_background_services(self) -> None:
        if self._observation_service is None:
            self._observation_service = ObservationService(self)
        self._observation_service.start()
        if self._dispatch_service is None:
            dispatch_mode = (
                (
                    os.environ.get("HERMIT_DISPATCH_MODE")
                    or str(
                        getattr(
                            getattr(self.pm, "settings", None),
                            "kernel_dispatch_mode",
                            "pool",
                        )
                        or "pool"
                    )
                )
                .strip()
                .lower()
            )

            if dispatch_mode not in ("flat", "pool"):
                import structlog as _slog

                _slog.get_logger().warning(
                    "invalid_dispatch_mode",
                    dispatch_mode=dispatch_mode,
                    defaulting_to="pool",
                )
                dispatch_mode = "pool"

            if dispatch_mode == "flat":
                from hermit.kernel.execution.coordination.dispatch import KernelDispatchService

                worker_count = int(
                    getattr(
                        getattr(self.pm, "settings", None),
                        "kernel_dispatch_worker_count",
                        4,
                    )
                    or 4
                )
                self._dispatch_service = KernelDispatchService(self, worker_count=worker_count)
            else:
                from hermit.kernel.execution.coordination.pool_dispatch import (
                    PoolAwareDispatchService,
                )

                self._dispatch_service = PoolAwareDispatchService(self)

            self._register_kind_handlers(self._dispatch_service)
            self._dispatch_service.start()

        # Register competition candidate completion listener.
        self._setup_competition_hook()

    def stop_background_services(self) -> None:
        if self._dispatch_service is not None:
            stopper = getattr(self._dispatch_service, "stop", None)
            if callable(stopper):
                stopper()
            self._dispatch_service = None
        if self._observation_service is not None:
            self._observation_service.stop()
            self._observation_service = None

    def _setup_competition_hook(self) -> None:
        """Register DISPATCH_RESULT hook for competition candidate completion."""
        try:
            from hermit.kernel.execution.competition.service import CompetitionService
            from hermit.kernel.execution.competition.workspace import (
                CompetitionWorkspaceManager,
            )

            store = getattr(self.agent, "kernel_store", None)
            tc = self.task_controller
            workspace_root = getattr(self.agent, "workspace_root", None)

            if store is None or tc is None:
                return

            ws_mgr = CompetitionWorkspaceManager(Path(workspace_root)) if workspace_root else None
            competition_service = CompetitionService(
                store=store,
                task_controller=tc,
                workspace_manager=ws_mgr,
            )
            self.pm.hooks.add(
                HookEvent.DISPATCH_RESULT,
                competition_service.on_dispatch_result,
                priority=15,
            )
        except Exception:
            import structlog as _slog

            _slog.get_logger().debug("competition_hook_unavailable", exc_info=True)

    def _register_kind_handlers(self, dispatch_service: object) -> None:
        """Register custom step kind handlers on the dispatch service."""
        try:
            from hermit.plugins.builtin.hooks.memory.hooks_promotion import (
                create_memory_promotion_handler,
            )

            dispatch_service.register_kind_handler(
                "memory_promotion",
                create_memory_promotion_handler(self),
            )
        except Exception as exc:
            import structlog as _slog

            _slog.get_logger().warning(
                "memory_promotion_handler_unavailable",
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Command registration
    # ------------------------------------------------------------------

    def add_command(
        self,
        name: str,
        handler: CommandHandler,
        help_text: str,
        cli_only: bool = False,
    ) -> None:
        """Register a command on this runner instance (used by plugins)."""
        self._commands[name] = (handler, help_text, cli_only)

    # ------------------------------------------------------------------
    # Session helpers (thin wrappers)
    # ------------------------------------------------------------------

    def _max_session_messages(self) -> int:
        settings = getattr(self.pm, "settings", None)
        return int(getattr(settings, "max_session_messages", 100) or 100)

    def wake_dispatcher(self) -> None:
        waker = getattr(self._dispatch_service, "wake", None)
        if callable(waker):
            waker()

    def _ensure_session_started(self, session_id: str) -> None:
        if session_id not in self._session_started:
            self.pm.on_session_start(session_id, runner=self)
            self._session_started.add(session_id)

    def close_session(self, session_id: str) -> None:
        """End a session, fire hooks, and archive."""
        session = self.session_manager.get_or_create(session_id)
        self.pm.on_session_end(session_id, session.messages)
        self.session_manager.close(session_id)
        self._session_started.discard(session_id)

    def reset_session(self, session_id: str) -> None:
        """Close current session and start a fresh one."""
        self.close_session(session_id)
        self.session_manager.get_or_create(session_id)
        self.pm.on_session_start(session_id, runner=self)
        self._session_started.add(session_id)

    # ------------------------------------------------------------------
    # Delegation to extracted modules (lazy imports to avoid circular deps)
    # ------------------------------------------------------------------

    def _get_store(self) -> KernelStore | None:
        """Retrieve the kernel store from the task controller, or None."""
        return getattr(self.task_controller, "store", None)

    def _make_compiler(self) -> MessageCompiler:
        """Create a MessageCompiler wired to the current runner state."""
        from hermit.runtime.control.runner.message_compiler import MessageCompiler

        return MessageCompiler(
            pm=self.pm,
            session_manager=self.session_manager,
            store=self._get_store(),
            task_controller=self.task_controller,
            observation_service=self._observation_service,
            artifact_store=getattr(self.agent, "artifact_store", None),
        )

    def _prepare_prompt_context(
        self, session_id: str, text: str, *, source_channel: str
    ) -> tuple[Session, str, dict[str, object], str]:
        return self._make_compiler().prepare_prompt_context(
            session_id,
            text,
            source_channel=source_channel,
            runner=self,
            ensure_session_started=self._ensure_session_started,
        )

    def _provider_input_compiler(self) -> ProviderInputCompiler:
        return self._make_compiler().provider_input_compiler()

    def _compile_provider_input(
        self,
        *,
        task_ctx: TaskExecutionContext,
        prompt: str,
        raw_text: str,
        session_messages: list[dict[str, object]] | None = None,
    ) -> CompiledProviderInput:
        return self._make_compiler().compile_provider_input(
            task_ctx=task_ctx,
            prompt=prompt,
            raw_text=raw_text,
            session_messages=session_messages,
        )

    def _append_note_context(
        self, session_id: str, task_id: str, source_channel: str
    ) -> TaskExecutionContext:
        return self._make_compiler().append_note_context(session_id, task_id, source_channel)

    def _run_existing_task(self, task_ctx, prompt, **kwargs):
        from hermit.runtime.control.runner.task_executor import RunnerTaskExecutor

        executor = RunnerTaskExecutor(
            session_manager=self.session_manager,
            store=self._get_store(),
            task_controller=self.task_controller,
            pm=self.pm,
            runtime=self.agent,
            observation_service=self._observation_service,
        )
        return executor.run_existing_task(task_ctx, prompt, runner=self, **kwargs)

    def _maybe_capture_planning_result(self, task_ctx, result, *, readonly_only):
        store = self._get_store()
        if store is None or not hasattr(store, "get_step"):
            return False
        from hermit.kernel.task.services.planning import PlanningService
        from hermit.runtime.control.runner.session_context_builder import SessionContextBuilder

        builder = SessionContextBuilder(
            session_manager=self.session_manager,
            pm=self.pm,
            store=store,
            planning_service=PlanningService(store, getattr(self.agent, "artifact_store", None)),
        )
        return builder.maybe_capture_planning_result(
            task_ctx,
            result,
            readonly_only=readonly_only,
            task_controller=self.task_controller,
        )

    def _emit_async_dispatch_result(self, task_ctx, result, *, started_at):
        from hermit.runtime.control.runner.async_dispatcher import AsyncDispatcher

        dispatcher = AsyncDispatcher(
            runner=self,
            store=self._get_store(),
            task_controller=self.task_controller,
            session_manager=self.session_manager,
            pm=self.pm,
        )
        return dispatcher.emit_async_dispatch_result(task_ctx, result, started_at=started_at)

    def _record_scheduler_execution(self, task_ctx, result, *, started_at, delivery_results=None):
        from hermit.runtime.control.runner.async_dispatcher import AsyncDispatcher

        dispatcher = AsyncDispatcher(
            runner=self,
            store=self._get_store(),
            task_controller=self.task_controller,
            session_manager=self.session_manager,
            pm=self.pm,
        )
        return dispatcher.record_scheduler_execution(
            task_ctx, result, started_at=started_at, delivery_results=delivery_results
        )

    def _pending_disambiguation_text(self, ingress: object) -> str:
        from hermit.runtime.control.runner.approval_resolver import ApprovalResolver

        store = self._get_store()
        if store is None:
            return "I couldn't determine which task to continue. Please switch focus first."
        resolver = ApprovalResolver(
            store=store,
            task_controller=self.task_controller,
        )
        return resolver.pending_disambiguation_text(ingress)

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
        from hermit.runtime.control.runner.approval_resolver import ApprovalResolver

        store = self._get_store()
        if store is None:
            return DispatchResult(
                _t("kernel.runner.approval_not_found", runner=self, approval_id=approval_id),
                is_command=True,
            )
        resolver = ApprovalResolver(store=store, task_controller=self.task_controller)
        return resolver.resolve_approval(
            session_id,
            action=action,
            approval_id=approval_id,
            reason=reason,
            session_manager=self.session_manager,
            agent=self.agent,
            pm=self.pm,
            result_status_fn=self._result_status,
            runner=self,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
        )

    # ------------------------------------------------------------------
    # Public async ingress & approval methods (delegate to AsyncDispatcher)
    # ------------------------------------------------------------------

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
        from hermit.runtime.control.runner.async_dispatcher import AsyncDispatcher

        dispatcher = AsyncDispatcher(
            runner=self,
            store=self.task_controller.store,
            task_controller=self.task_controller,
            session_manager=self.session_manager,
            pm=self.pm,
        )
        return dispatcher.enqueue_ingress(
            session_id,
            text,
            source_channel=source_channel,
            notify=notify,
            source_ref=source_ref,
            ingress_metadata=ingress_metadata,
            requested_by=requested_by,
            parent_task_id=parent_task_id,
        )

    def enqueue_approval_resume(
        self,
        session_id: str,
        *,
        action: str,
        approval_id: str,
        reason: str = "",
    ) -> DispatchResult:
        from hermit.runtime.control.runner.async_dispatcher import AsyncDispatcher

        dispatcher = AsyncDispatcher(
            runner=self,
            store=self.task_controller.store,
            task_controller=self.task_controller,
            session_manager=self.session_manager,
            pm=self.pm,
        )
        return dispatcher.enqueue_approval_resume(
            session_id, action=action, approval_id=approval_id, reason=reason
        )

    def process_claimed_attempt(
        self,
        step_attempt_id: str,
        *,
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
    ) -> AgentResult:
        from hermit.runtime.control.runner.task_executor import RunnerTaskExecutor

        executor = RunnerTaskExecutor(
            session_manager=self.session_manager,
            store=self.task_controller.store,
            task_controller=self.task_controller,
            pm=self.pm,
            runtime=self.agent,
            observation_service=self._observation_service,
        )
        return executor.process_claimed_attempt(
            step_attempt_id,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
            ensure_session_started=self._ensure_session_started,
            runner=self,
        )

    def resume_attempt(
        self,
        step_attempt_id: str,
        *,
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
    ) -> AgentResult:
        from typing import cast

        from hermit.kernel.context.models.context import TaskExecutionContext

        _resume_attempt_fn = getattr(self.task_controller, "resume_attempt", None)
        task_ctx: TaskExecutionContext = cast(
            TaskExecutionContext,
            _resume_attempt_fn(step_attempt_id)
            if callable(_resume_attempt_fn)
            else self.task_controller.context_for_attempt(step_attempt_id),
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
        session.messages = _trim_session_messages(
            result.messages,
            max_messages=self._max_session_messages(),
        )
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
                    result_preview=result_preview(result.text or ""),
                    result_text=result.text or "",
                )
            self.pm.on_post_run(
                result, session_id=task_ctx.conversation_id, session=session, runner=self
            )
        return result

    # ------------------------------------------------------------------
    # Control action dispatch (delegate to ControlActionDispatcher)
    # ------------------------------------------------------------------

    def dispatch_control_action(
        self,
        session_id: str,
        *,
        action: str,
        target_id: str,
        reason: str = "",
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
    ) -> DispatchResult:
        from hermit.runtime.control.runner.control_actions import ControlActionDispatcher

        dispatcher = ControlActionDispatcher(
            runner=self,
            task_controller=self.task_controller,
            pm=self.pm,
        )
        return dispatcher.dispatch(
            session_id,
            action=action,
            target_id=target_id,
            reason=reason,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
        )

    # ------------------------------------------------------------------
    # Public dispatch entry point
    # ------------------------------------------------------------------

    def dispatch(
        self,
        session_id: str,
        text: str,
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
    ) -> DispatchResult:
        """Route a raw user message: slash commands are handled here; everything
        else is forwarded to the agent.
        """
        stripped = text.strip()
        resolution = self.task_controller.resolve_text_command(session_id, stripped)
        if resolution is not None:
            action, target_id, reason = resolution
            return self.dispatch_control_action(
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
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
        run_opts: dict[str, object] | None = None,
    ) -> AgentResult:
        """Process a single user message within a session."""
        from hermit.kernel.task.services.planning import PlanningService

        source_channel = self.task_controller.source_from_session(session_id)
        session, prompt, prepared_run_opts, task_goal = self._prepare_prompt_context(
            session_id,
            text,
            source_channel=source_channel,
        )
        if run_opts:
            prepared_run_opts.update(run_opts)
        run_opts = prepared_run_opts

        ingress = None
        skip_ingress = session_id == "cli-oneshot" or run_opts.get("skip_ingress", False)
        if not skip_ingress and hasattr(self.task_controller, "decide_ingress"):
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
            getattr(ingress, "parent_task_id", AUTO_PARENT) if ingress is not None else AUTO_PARENT
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

        task_kind = "plan" if run_opts.get("readonly_only", False) else "respond"
        task_ctx = self.task_controller.start_task(
            conversation_id=session_id,
            goal=task_goal,
            source_channel=source_channel,
            kind=task_kind,
            policy_profile=(
                "readonly"
                if run_opts.get("readonly_only", False)
                else str(
                    ingress_metadata.get("policy_profile")
                    or run_opts.get("policy_profile")
                    or "default"
                )
            ),
            workspace_root=str(getattr(self.agent, "workspace_root", "") or ""),
            parent_task_id=parent_task_id,
            ingress_metadata=ingress_metadata,
        )
        # Set tool execution deadline from runtime budget.
        _budget = get_runtime_budget()
        task_ctx.deadline = time.time() + _budget.tool_hard_deadline
        # Fire SUBTASK_SPAWN when a child task is created under a parent.
        store = self._get_store()
        if store is not None:
            task_record = store.get_task(task_ctx.task_id)
            if task_record is not None and task_record.parent_task_id:
                self.pm.hooks.fire(
                    HookEvent.SUBTASK_SPAWN,
                    parent_task_id=task_record.parent_task_id,
                    task_id=task_ctx.task_id,
                    store=store,
                )
        if run_opts.get("planning_mode", False):
            planning = PlanningService(
                self.task_controller.store, getattr(self.agent, "artifact_store", None)
            )
            planning.set_pending_for_conversation(session_id, enabled=False)
            planning.enter_planning(task_ctx.task_id)

        compiled_input = self._compile_provider_input(
            task_ctx=task_ctx,
            prompt=prompt,
            raw_text=text,
            session_messages=list(session.messages),
        )
        if hasattr(self.task_controller, "update_attempt_phase"):
            self.task_controller.update_attempt_phase(task_ctx.step_attempt_id, phase="executing")
        try:
            result = self.agent.run(
                prompt,
                compiled_messages=compiled_input.messages,
                on_tool_call=on_tool_call,
                on_tool_start=on_tool_start,
                disable_tools=bool(run_opts.get("disable_tools", False)),
                readonly_only=bool(run_opts.get("readonly_only", False)),
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

        session.messages = _trim_session_messages(
            result.messages,
            max_messages=self._max_session_messages(),
        )
        self.session_manager.save(session)
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
                    result_preview=result_preview(result.text or ""),
                    result_text=result.text or "",
                )
        if not (result.suspended or result.blocked):
            self.pm.on_post_run(result, session_id=session_id, session=session, runner=self)
        return result

    @staticmethod
    def _result_status(result: AgentResult) -> str:
        from hermit.runtime.control.runner.utils import result_status

        return result_status(result)


# ------------------------------------------------------------------
# Core slash commands (always available, not from plugins)
# ------------------------------------------------------------------


@AgentRunner.register_command("/new", "kernel.runner.command.new.help")
def _cmd_new(runner: AgentRunner, session_id: str, _text: str) -> DispatchResult:  # pyright: ignore[reportUnusedFunction]
    runner.reset_session(session_id)
    return DispatchResult(_t("kernel.runner.new_session", runner=runner), is_command=True)


@AgentRunner.register_command("/history", "kernel.runner.command.history.help")
def _cmd_history(runner: AgentRunner, session_id: str, _text: str) -> DispatchResult:  # pyright: ignore[reportUnusedFunction]
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
def _cmd_quit(_runner: AgentRunner, _session_id: str, _text: str) -> DispatchResult:  # pyright: ignore[reportUnusedFunction]
    return DispatchResult(
        _t("kernel.runner.quit", runner=_runner),
        is_command=True,
        should_exit=True,
    )


@AgentRunner.register_command("/help", "kernel.runner.command.help.help")
def _cmd_help(runner: AgentRunner, _session_id: str, _text: str) -> DispatchResult:  # pyright: ignore[reportUnusedFunction]
    lines = [_t("kernel.runner.help.title", runner=runner)]
    for cmd, (_fn, help_text, cli_only) in sorted(runner.command_specs.items()):
        if runner.serve_mode and cli_only:
            continue
        lines.append(f"- `{cmd}` \u2014 {_resolve_help_text(help_text, runner=runner)}")
    return DispatchResult("\n".join(lines), is_command=True)


@AgentRunner.register_command("/task", "kernel.runner.command.task.help")
def _cmd_task(runner: AgentRunner, session_id: str, text: str) -> DispatchResult:  # pyright: ignore[reportUnusedFunction]
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
    return runner.dispatch_control_action(session_id, action=mapped_action, target_id=target_id)
