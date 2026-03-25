from __future__ import annotations

import datetime
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from hermit.infra.storage import atomic_write
from hermit.kernel.context.injection.provider_input import ProviderInputCompiler
from hermit.kernel.context.models.context import CompiledProviderInput, TaskExecutionContext
from hermit.kernel.execution.coordination.observation import ObservationService
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.task.services.planning import PlanningService
from hermit.runtime.capability.contracts.base import HookEvent
from hermit.runtime.control.lifecycle.budgets import get_runtime_budget
from hermit.runtime.control.lifecycle.session import (
    SessionManager,
    sanitize_session_messages,
)
from hermit.runtime.provider_host.execution.runtime import (
    AgentResult,
    AgentRuntime,
    ToolCallback,
    ToolStartCallback,
)

if TYPE_CHECKING:
    from hermit.runtime.capability.registry.manager import PluginManager


from hermit.runtime.control.runner.utils import (
    result_preview,
    result_status,
)


def _enrich_prompt_with_dag_metadata(prompt: str, dag_meta: dict[str, Any]) -> str:
    """Append DAG node metadata to a prompt so the agent receives full context.

    DAG step metadata (file path, description, goal, constraints) is stored in
    ``ingress_metadata["dag_node_metadata"]`` but was previously never surfaced
    to the agent.  This helper folds it into the prompt text.
    """
    if not dag_meta:
        return prompt

    extra_parts: list[str] = []
    if dag_meta.get("description"):
        extra_parts.append(f"Description: {dag_meta['description']}")
    if dag_meta.get("file"):
        extra_parts.append(f"Target file: {dag_meta['file']}")
    if dag_meta.get("goal"):
        extra_parts.append(f"Goal: {dag_meta['goal']}")
    if dag_meta.get("target"):
        extra_parts.append(f"Target: {dag_meta['target']}")
    if dag_meta.get("constraints"):
        constraints = dag_meta["constraints"]
        if isinstance(constraints, list):
            extra_parts.append(f"Constraints: {'; '.join(str(c) for c in constraints)}")
        else:
            extra_parts.append(f"Constraints: {constraints}")

    # Team role context — surface before the generic catch-all.
    team_role = dag_meta.get("team_role")
    if team_role:
        extra_parts.append(f"Your role in the team: {team_role}")
        role_config = dag_meta.get("role_config")
        if isinstance(role_config, dict) and role_config.get("instruction"):
            extra_parts.append(f"Role instructions: {role_config['instruction']}")

    # Surface any remaining keys that aren't already handled above.
    _HANDLED = {
        "description",
        "file",
        "goal",
        "target",
        "constraints",
        "team_role",
        "team_id",
        "role_count",
        "role_config",
    }
    for key, value in dag_meta.items():
        if key not in _HANDLED and value:
            extra_parts.append(f"{key}: {value}")

    if not extra_parts:
        return prompt

    enrichment = "\n".join(extra_parts)
    workspace_guard = (
        "All file operations must stay within the workspace root. "
        "Do not write to /tmp/ or any directory outside the project."
    )
    return f"{prompt}\n\n{enrichment}\n\n{workspace_guard}"


class RunnerTaskExecutor:
    """Executes tasks through the agent loop on behalf of AgentRunner.

    Extracted from AgentRunner to keep the runner focused on orchestration
    while this class handles the details of running and resuming tasks.

    Follows the delegation pattern used by WitnessCapture: accepts explicit
    dependencies in __init__ and exposes focused methods without inheriting
    from or holding a back-reference to the full runner.
    """

    def __init__(
        self,
        *,
        session_manager: SessionManager,
        store: Any,
        task_controller: TaskController,
        pm: PluginManager,
        runtime: AgentRuntime,
        observation_service: ObservationService | None = None,
    ) -> None:
        self.session_manager = session_manager
        self.store = store
        self.task_controller = task_controller
        self.pm = pm
        self.runtime = runtime
        self.observation_service = observation_service

    # ------------------------------------------------------------------
    # Helpers (private)
    # ------------------------------------------------------------------

    def _max_session_messages(self) -> int:
        settings = getattr(self.pm, "settings", None)
        return int(getattr(settings, "max_session_messages", 100) or 100)

    def _provider_input_compiler(self) -> ProviderInputCompiler:
        if self.store is None or getattr(self.store, "db_path", None) is None:
            raise RuntimeError("compiled_context_unavailable")
        return ProviderInputCompiler(self.store, getattr(self.runtime, "artifact_store", None))

    def _compile_provider_input(
        self,
        *,
        task_ctx: TaskExecutionContext,
        prompt: str,
        raw_text: str,
        session_messages: list[dict[str, Any]] | None = None,
    ) -> CompiledProviderInput:
        try:
            compiler = self._provider_input_compiler()
        except RuntimeError:
            return self._compile_lightweight_input(
                prompt=prompt,
                session_messages=session_messages or [],
            )
        return compiler.compile(task_context=task_ctx, final_prompt=prompt, raw_text=raw_text)

    def _compile_lightweight_input(
        self,
        *,
        prompt: str,
        session_messages: list[dict[str, Any]],
        max_recent: int = 20,
    ) -> CompiledProviderInput:
        recent = (
            session_messages[-max_recent:]
            if len(session_messages) > max_recent
            else list(session_messages)
        )
        messages = sanitize_session_messages(recent)
        messages.append({"role": "user", "content": prompt})
        return CompiledProviderInput(messages=messages, source_mode="lightweight")

    @staticmethod
    def _result_status(result: AgentResult) -> str:
        return result_status(result)

    @staticmethod
    def _result_preview(text: str, *, limit: int = 280) -> str:
        return result_preview(text, limit=limit)

    def _maybe_capture_planning_result(
        self,
        task_ctx: TaskExecutionContext,
        result: AgentResult,
        *,
        readonly_only: bool,
    ) -> bool:
        if not readonly_only:
            return False
        if self.store is None or not hasattr(self.store, "get_step"):
            return False
        step = self.store.get_step(task_ctx.step_id)
        if step is None or step.kind != "plan":
            return False
        planning = PlanningService(self.store, getattr(self.runtime, "artifact_store", None))
        plan_ref = planning.capture_plan_result(task_ctx, plan_text=result.text or "")
        self.task_controller.mark_planning_ready(
            task_ctx,
            plan_artifact_ref=plan_ref,
            result_preview=self._result_preview(result.text or ""),
            result_text=result.text or "",
        )
        result.execution_status = "planning_ready"
        result.status_managed_by_kernel = True
        return True

    @staticmethod
    def _trim_session_messages(
        messages: list[dict[str, Any]], *, max_messages: int = 100
    ) -> list[dict[str, Any]]:
        if len(messages) <= max_messages:
            return list(messages)
        first_msg = messages[0] if messages else None
        has_system_first = first_msg is not None and first_msg.get("role") == "system"
        if has_system_first:
            tail = messages[-(max_messages - 1) :]
            trimmed = [first_msg, *tail]
        else:
            trimmed = messages[-max_messages:]
        return sanitize_session_messages(trimmed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_existing_task(
        self,
        task_ctx: TaskExecutionContext,
        prompt: str,
        *,
        raw_text: str | None = None,
        readonly_only: bool = False,
        disable_tools: bool = False,
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
        runner: Any = None,
    ) -> AgentResult:
        """Run an existing task through the agent loop.

        Args:
            task_ctx: The execution context for the task.
            prompt: The compiled prompt to send.
            raw_text: Original user text (defaults to *prompt*).
            readonly_only: If True, run in read-only / planning mode.
            disable_tools: If True, disable tool use for this run.
            on_tool_call: Optional callback invoked on each tool call.
            on_tool_start: Optional callback invoked when a tool starts.
            runner: Back-reference to the AgentRunner (passed through to
                hooks that expect it).
        """
        session = self.session_manager.get_or_create(task_ctx.conversation_id)
        compiled_input = self._compile_provider_input(
            task_ctx=task_ctx,
            prompt=prompt,
            raw_text=raw_text or prompt,
            session_messages=list(session.messages),
        )
        if hasattr(self.task_controller, "update_attempt_phase"):
            self.task_controller.update_attempt_phase(task_ctx.step_attempt_id, phase="executing")
        result = self.runtime.run(
            prompt,
            compiled_messages=compiled_input.messages,
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
        session.messages = self._trim_session_messages(
            result.messages,
            max_messages=self._max_session_messages(),
        )
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
                    result_preview=self._result_preview(result.text or ""),
                    result_text=result.text or "",
                )
            self.pm.on_post_run(
                result,
                session_id=task_ctx.conversation_id,
                session=session,
                runner=runner,
            )
        return result

    def process_claimed_attempt(
        self,
        step_attempt_id: str,
        *,
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
        ensure_session_started: Any | None = None,
        runner: Any = None,
    ) -> AgentResult:
        """Process a claimed step attempt through the agent loop.

        This is the main entry point used by the dispatch service to execute
        tasks that have been claimed from the work queue.

        Args:
            step_attempt_id: The step attempt to process.
            on_tool_call: Optional callback invoked on each tool call.
            on_tool_start: Optional callback invoked when a tool starts.
            ensure_session_started: Callable to fire session-start hooks
                (``runner._ensure_session_started``).
            runner: Back-reference to the AgentRunner (passed through to
                hooks and notification helpers).
        """
        task_ctx = self.task_controller.context_for_attempt(step_attempt_id)
        # Set tool execution deadline from runtime budget.
        budget = get_runtime_budget()
        task_ctx.deadline = time.time() + budget.tool_hard_deadline
        session_id = task_ctx.conversation_id
        self.task_controller.ensure_conversation(session_id, source_channel=task_ctx.source_channel)
        session = self.session_manager.get_or_create(session_id)
        if ensure_session_started is not None:
            ensure_session_started(session_id)

        metadata = dict(task_ctx.ingress_metadata or {})
        _step_attempt = self.task_controller.store.get_step_attempt(step_attempt_id)
        execution_mode = str(
            (_step_attempt.context or {}).get("execution_mode", "run")
            if _step_attempt is not None
            else "run"
        )
        started_at = time.time()
        try:
            if execution_mode == "resume":
                if hasattr(self.task_controller, "update_attempt_phase"):
                    self.task_controller.update_attempt_phase(step_attempt_id, phase="executing")
                result = self.runtime.resume(
                    step_attempt_id=step_attempt_id,
                    task_context=task_ctx,
                    on_tool_call=on_tool_call,
                    on_tool_start=on_tool_start,
                )
            else:
                # Build prompt, enriching with DAG node metadata when present.
                dag_meta = dict(metadata.get("dag_node_metadata", {}) or {})
                prompt = _enrich_prompt_with_dag_metadata(
                    str(metadata.get("entry_prompt", "") or ""),
                    dag_meta,
                )
                raw_text = _enrich_prompt_with_dag_metadata(
                    str(
                        metadata.get("raw_text", "") or str(metadata.get("entry_prompt", "") or "")
                    ),
                    dag_meta,
                )
                compiled_input = self._compile_provider_input(
                    task_ctx=task_ctx,
                    prompt=prompt,
                    raw_text=raw_text,
                    session_messages=list(session.messages),
                )
                if hasattr(self.task_controller, "update_attempt_phase"):
                    self.task_controller.update_attempt_phase(step_attempt_id, phase="executing")
                result = self.runtime.run(
                    prompt,
                    compiled_messages=compiled_input.messages,
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
        session.messages = self._trim_session_messages(
            result.messages,
            max_messages=self._max_session_messages(),
        )
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
                result_preview=self._result_preview(result.text or ""),
                result_text=result.text or "",
            )
        self.pm.on_post_run(result, session_id=session_id, session=session, runner=runner)
        delivery_results = self._emit_async_dispatch_result(task_ctx, result, started_at=started_at)
        self._record_scheduler_execution(
            task_ctx, result, started_at=started_at, delivery_results=delivery_results
        )
        return result

    # ------------------------------------------------------------------
    # Async dispatch / scheduler helpers (used by process_claimed_attempt)
    # ------------------------------------------------------------------

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
            success=self._result_status(result) == "succeeded",
            result_text=result.text or "",
            error=(None if self._result_status(result) == "succeeded" else (result.text or "")),
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
