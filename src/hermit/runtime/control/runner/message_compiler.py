from __future__ import annotations

import datetime
import re
from typing import TYPE_CHECKING, Any

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel.context.injection.provider_input import ProviderInputCompiler
from hermit.kernel.context.models.context import CompiledProviderInput, TaskExecutionContext
from hermit.kernel.task.services.planning import PlanningService
from hermit.runtime.control.lifecycle.session import (
    Session,
    SessionManager,
    sanitize_session_messages,
)

if TYPE_CHECKING:
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.execution.coordination.observation import ObservationService
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.services.controller import TaskController
    from hermit.runtime.capability.registry.manager import PluginManager

_SESSION_TIME_RE = re.compile(r"<session_time>.*?</session_time>\s*", re.DOTALL)
_FEISHU_META_RE = re.compile(r"<feishu_[^>]+>.*?</feishu_[^>]+>\s*", re.DOTALL)


def _strip_internal_markup(text: str) -> str:
    if not text:
        return ""
    cleaned = _SESSION_TIME_RE.sub("", text)
    cleaned = _FEISHU_META_RE.sub("", cleaned)
    return cleaned.strip()


def _locale_for_pm(pm: PluginManager | None = None) -> str:
    settings = getattr(pm, "settings", None)
    return resolve_locale(getattr(settings, "locale", None))


def _t(
    message_key: str,
    *,
    pm: PluginManager | None = None,
    default: str | None = None,
    **kwargs: object,
) -> str:
    return tr(message_key, locale=_locale_for_pm(pm), default=default, **kwargs)


class MessageCompiler:
    """Compiles prompts, session context, and provider input for agent execution.

    Extracted from AgentRunner to keep message compilation logic focused and testable.
    Dependencies are injected via the constructor following the WitnessCapture pattern.
    """

    def __init__(
        self,
        *,
        pm: PluginManager,
        session_manager: SessionManager,
        store: KernelStore,
        task_controller: TaskController,
        observation_service: ObservationService | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self.pm = pm
        self.session_manager = session_manager
        self.store = store
        self.task_controller = task_controller
        self.observation_service = observation_service
        self.artifact_store = artifact_store

    def prepare_prompt_context(
        self,
        session_id: str,
        text: str,
        *,
        source_channel: str,
        runner: Any = None,
        ensure_session_started: Any = None,
    ) -> tuple[Session, str, dict[str, object], str]:
        """Prepare prompt context with system prompt, session messages, and notes.

        Returns a tuple of (session, full_prompt, run_opts, task_goal).

        Parameters
        ----------
        runner:
            The AgentRunner instance, passed through to hooks that expect it.
        ensure_session_started:
            Callable that marks a session as started (fires session-start hooks).
        """
        ensure_conversation = getattr(self.task_controller, "ensure_conversation", None)
        if callable(ensure_conversation):
            ensure_conversation(session_id, source_channel=source_channel)

        session = self.session_manager.get_or_create(session_id)
        sanitized_messages = sanitize_session_messages(session.messages)
        if sanitized_messages != session.messages:
            session.messages = sanitized_messages
            self.session_manager.save(session)

        if callable(ensure_session_started):
            ensure_session_started(session_id)

        prompt, run_opts = self.pm.on_pre_run(
            text,
            session_id=session_id,
            session=session,
            messages=list(session.messages),
            runner=runner,
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

        planning_mode = PlanningService.planning_requested(text)
        if hasattr(self.store, "ensure_conversation"):
            planning = PlanningService(self.store, self.artifact_store)
            planning_mode = planning.pending_for_conversation(session_id) or planning_mode
        if planning_mode:
            run_opts = dict(run_opts)
            run_opts["readonly_only"] = True
            run_opts["planning_mode"] = True
            prompt = prompt + _t("kernel.planner.mode.prompt", pm=self.pm)

        full_prompt = time_ctx + prompt
        task_goal = (
            _strip_internal_markup(text)
            or _strip_internal_markup(full_prompt)
            or text.strip()
            or full_prompt.strip()
        )
        return session, full_prompt, run_opts, task_goal

    def provider_input_compiler(self) -> ProviderInputCompiler:
        """Create a ProviderInputCompiler from the kernel store."""
        if self.store is None or getattr(self.store, "db_path", None) is None:
            raise RuntimeError("compiled_context_unavailable")
        return ProviderInputCompiler(self.store, self.artifact_store)

    def compile_provider_input(
        self,
        *,
        task_ctx: TaskExecutionContext,
        prompt: str,
        raw_text: str,
        session_messages: list[dict[str, Any]] | None = None,
    ) -> CompiledProviderInput:
        """Compile full provider input for a task execution context."""
        try:
            compiler = self.provider_input_compiler()
        except RuntimeError:
            return self.compile_lightweight_input(
                prompt=prompt,
                session_messages=session_messages or [],
            )
        return compiler.compile(task_context=task_ctx, final_prompt=prompt, raw_text=raw_text)

    def compile_lightweight_input(
        self,
        *,
        prompt: str,
        session_messages: list[dict[str, Any]],
        max_recent: int = 20,
    ) -> CompiledProviderInput:
        """Compile lightweight input using only recent session messages."""
        recent = (
            session_messages[-max_recent:]
            if len(session_messages) > max_recent
            else list(session_messages)
        )
        messages = sanitize_session_messages(recent)
        messages.append({"role": "user", "content": prompt})
        return CompiledProviderInput(messages=messages, source_mode="lightweight")

    def append_note_context(
        self, session_id: str, task_id: str, source_channel: str
    ) -> TaskExecutionContext:
        """Build a TaskExecutionContext from the latest step attempt for a task."""
        attempt = next(iter(self.store.list_step_attempts(task_id=task_id, limit=1)), None)
        task = self.store.get_task(task_id)
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
