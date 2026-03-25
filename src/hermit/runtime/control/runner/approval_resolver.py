from __future__ import annotations

from typing import TYPE_CHECKING

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController

if TYPE_CHECKING:
    from hermit.runtime.provider_host.execution.runtime import (
        ToolCallback,
        ToolStartCallback,
    )


from hermit.runtime.control.runner.utils import (
    DispatchResult,
    _t,
    _trim_session_messages,
)


class ApprovalResolver:
    """Resolves approval and disambiguation requests for governed tasks."""

    def __init__(
        self,
        *,
        store: KernelStore,
        task_controller: TaskController,
    ) -> None:
        self.store = store
        self.task_controller = task_controller

    def pending_disambiguation_text(self, ingress: object) -> str:
        candidates = list(getattr(ingress, "candidates", []) or [])
        if not candidates:
            return _t(
                "kernel.runner.pending_disambiguation.none",
                default="I couldn't determine which task to continue. Please switch focus first.",
            )
        lines = [
            _t(
                "kernel.runner.pending_disambiguation.intro",
                default=("I couldn't determine which task to continue. Try one of these commands:"),
            )
        ]
        for item in candidates[:3]:
            task_id = str(dict(item).get("task_id", "") or "").strip()
            if task_id:
                lines.append(
                    _t(
                        "kernel.runner.pending_disambiguation.item",
                        default="- switch task {task_id}",
                        task_id=task_id,
                    )
                )
        return "\n".join(lines)

    def resolve_approval(
        self,
        session_id: str,
        *,
        action: str,
        approval_id: str,
        reason: str = "",
        session_manager: object,
        agent: object,
        pm: object,
        result_status_fn: object,
        runner: object = None,
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
    ) -> object:
        from hermit.kernel.policy.approvals.approvals import ApprovalService
        from hermit.runtime.control.runner.utils import result_preview

        approval = self.store.get_approval(approval_id)
        if approval is None:
            return DispatchResult(
                _t("kernel.runner.approval_not_found", approval_id=approval_id),
                is_command=True,
            )

        session = session_manager.get_or_create(session_id)  # type: ignore[union-attr]
        approvals = ApprovalService(self.store)
        if action == "deny":
            approvals.deny(approval_id, resolved_by="user", reason=reason)
            text = _t("kernel.runner.approval_denied")
            messages = list(session.messages)
            messages.append({"role": "assistant", "content": [{"type": "text", "text": text}]})

            session.messages = _trim_session_messages(messages)
            session_manager.save(session)  # type: ignore[union-attr]
            return DispatchResult(text=text, is_command=True)

        if action == "approve_mutable_workspace":
            approvals.approve_mutable_workspace(approval_id, resolved_by="user")
        else:
            approvals.approve_once(approval_id, resolved_by="user")

        # For async/DAG-dispatched steps, use enqueue_resume so the dispatch
        # loop re-claims and executes the step with proper reconciliation.
        # Direct agent.resume() bypasses reconciliation and can mark steps as
        # succeeded without the approved action actually executing.
        if self._is_async_dispatch(approval.step_attempt_id):
            task_ctx = self.task_controller.enqueue_resume(approval.step_attempt_id)
            if runner is not None and hasattr(runner, "wake_dispatcher"):
                runner.wake_dispatcher()
            text = _t(
                "kernel.runner.approval_enqueued",
                default="Approved. The step has been re-queued for execution.",
            )
            return DispatchResult(text=text, is_command=True)

        task_ctx = self.task_controller.context_for_attempt(approval.step_attempt_id)
        result = agent.resume(  # type: ignore[union-attr]
            step_attempt_id=approval.step_attempt_id,
            task_context=task_ctx,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
        )
        session.total_input_tokens += result.input_tokens
        session.total_output_tokens += result.output_tokens
        session.total_cache_read_tokens += result.cache_read_tokens
        session.total_cache_creation_tokens += result.cache_creation_tokens

        session.messages = _trim_session_messages(result.messages)
        session_manager.save(session)  # type: ignore[union-attr]
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
            status = result_status_fn(result)  # type: ignore[operator]
            if not getattr(result, "status_managed_by_kernel", False):
                self.task_controller.finalize_result(
                    task_ctx,
                    status=status,
                    result_preview=result_preview(result.text or ""),
                    result_text=result.text or "",
                )
            pm.on_post_run(result, session_id=session_id, session=session, runner=None)  # type: ignore[union-attr]
        return DispatchResult(
            text=result.text or "",
            is_command=False,
            agent_result=result,
        )

    def _is_async_dispatch(self, step_attempt_id: str) -> bool:
        """Check if a step attempt was dispatched asynchronously (DAG/MCP)."""
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return False
        ingress = dict((attempt.context or {}).get("ingress_metadata", {}) or {})
        return str(ingress.get("dispatch_mode", "") or "") == "async"
