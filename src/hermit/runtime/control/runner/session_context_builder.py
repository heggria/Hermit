from __future__ import annotations

import re
from typing import TYPE_CHECKING

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.task.services.planning import PlanningService
from hermit.runtime.control.lifecycle.session import SessionManager
from hermit.runtime.provider_host.execution.runtime import AgentResult

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.runtime.capability.registry.manager import PluginManager

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
    return cleaned[: limit - 1].rstrip() + "\u2026"


class SessionContextBuilder:
    """Extracted session lifecycle helpers delegated from AgentRunner."""

    def __init__(
        self,
        *,
        session_manager: SessionManager,
        pm: PluginManager,
        store: KernelStore,
        planning_service: PlanningService,
    ) -> None:
        self.session_manager = session_manager
        self.pm = pm
        self.store = store
        self.planning_service = planning_service
        self._session_started: set[str] = set()

    def max_session_messages(self) -> int:
        settings = getattr(self.pm, "settings", None)
        return int(getattr(settings, "max_session_messages", 100) or 100)

    def ensure_session_started(self, session_id: str) -> None:
        if session_id not in self._session_started:
            self.pm.on_session_start(session_id)
            self._session_started.add(session_id)

    def maybe_capture_planning_result(
        self,
        task_ctx: TaskExecutionContext,
        result: AgentResult,
        *,
        readonly_only: bool,
        task_controller: object,
    ) -> bool:
        if not readonly_only:
            return False
        step = self.store.get_step(task_ctx.step_id)
        if step is None or step.kind != "plan":
            return False
        artifact_store = getattr(task_controller, "artifact_store", None)
        if artifact_store is None:
            artifact_store = getattr(self.store, "artifact_store", None)
        planning = PlanningService(self.store, artifact_store)
        plan_ref = planning.capture_plan_result(task_ctx, plan_text=result.text or "")
        mark = getattr(task_controller, "mark_planning_ready", None)
        if callable(mark):
            mark(
                task_ctx,
                plan_artifact_ref=plan_ref,
                result_preview=_result_preview(result.text or ""),
                result_text=result.text or "",
            )
        result.execution_status = "planning_ready"
        result.status_managed_by_kernel = True
        return True
