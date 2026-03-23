from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.coordination.observation import (
    ObservationPollResult,
    ObservationProgress,
    ObservationTicket,
    normalize_observation_progress,
    normalize_observation_ticket,
)
from hermit.kernel.execution.executor.formatting import (
    compact_progress_text as _compact_progress_text,
)
from hermit.kernel.execution.executor.formatting import (
    format_model_content as _format_model_content,
)
from hermit.kernel.execution.executor.formatting import (
    progress_signature as _progress_signature,
)
from hermit.kernel.execution.executor.formatting import (
    progress_summary_signature as _progress_summary_signature,
)
from hermit.kernel.execution.executor.snapshot import RuntimeSnapshotManager
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import ActionRequest, PolicyDecision
from hermit.kernel.task.projections.progress_summary import (
    ProgressSummaryFormatter,
)
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec

if TYPE_CHECKING:
    from hermit.kernel.execution.executor.executor import ToolExecutionResult

_RUNTIME_SNAPSHOT_KEY = "runtime_snapshot"


def _is_governed_action(tool: ToolSpec, policy: PolicyDecision) -> bool:
    if tool.readonly and policy.verdict == "allow":
        return False
    if policy.action_class in {"read_local", "network_read"} and not policy.requires_receipt:
        return False
    return policy.action_class != "ephemeral_ui_mutation"


class ObservationHandler:
    """Observation lifecycle: submission, polling, progress tracking, and finalization."""

    def __init__(
        self,
        *,
        store: KernelStore,
        registry: ToolRegistry,
        policy_engine: Any,
        receipt_service: Any,
        decision_service: Any,
        capability_service: Any,
        reconciliations: Any,
        _snapshot: RuntimeSnapshotManager,
        progress_summarizer: ProgressSummaryFormatter | None,
        progress_summary_keepalive_seconds: float,
        tool_output_limit: int,
        executor: Any,
    ) -> None:
        self.store = store
        self.registry = registry
        self.policy_engine = policy_engine
        self.receipt_service = receipt_service
        self.decision_service = decision_service
        self.capability_service = capability_service
        self.reconciliations = reconciliations
        self._snapshot = _snapshot
        self.progress_summarizer = progress_summarizer
        self.progress_summary_keepalive_seconds = max(
            float(progress_summary_keepalive_seconds or 0.0), 0.0
        )
        self.tool_output_limit = tool_output_limit
        self._executor = executor

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    def handle_observation_submission(
        self,
        *,
        tool: ToolSpec,
        tool_name: str,
        tool_input: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
        observation: ObservationTicket,
        policy: PolicyDecision,
        policy_ref: str | None,
        decision_id: str | None,
        capability_grant_id: str | None,
        workspace_lease_id: str | None,
        approval_ref: str | None,
        witness_ref: str | None,
        action_request: ActionRequest,
        action_request_ref: str | None,
        approval_packet_ref: str | None,
        environment_ref: str | None,
        approval_mode: str,
        rollback_plan: dict[str, Any],
    ) -> ToolExecutionResult:
        from hermit.kernel.execution.executor.executor import ToolExecutionResult

        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        self._executor._store_pending_execution(
            attempt_ctx,
            {
                "tool_name": tool_name,
                "tool_input": dict(tool_input),
                "action_type": action_type,
                "policy": policy.to_dict(),
                "policy_ref": policy_ref,
                "decision_id": decision_id,
                "capability_grant_id": capability_grant_id,
                "workspace_lease_id": workspace_lease_id,
                "approval_ref": approval_ref,
                "action_request_ref": action_request_ref,
                "witness_ref": witness_ref,
                "idempotency_key": action_request.idempotency_key,
                "policy_result_ref": policy_ref,
                "approval_mode": approval_mode,
                "approval_packet_ref": approval_packet_ref,
                "environment_ref": environment_ref,
                "rollback_plan": rollback_plan,
            },
        )
        self._executor._set_attempt_phase(attempt_ctx, "observing", reason="observation_submitted")
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status="observing",
            status_reason=observation.topic_summary,
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            state_witness_ref=witness_ref,
            action_request_ref=action_request_ref,
            policy_result_ref=policy_ref,
            approval_packet_ref=approval_packet_ref,
            environment_ref=environment_ref,
        )
        self.store.update_step(attempt_ctx.step_id, status="blocked")
        self.store.update_task_status(attempt_ctx.task_id, "blocked")
        self.store.append_event(
            event_type="tool.submitted",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "tool_name": tool_name,
                "observer_kind": observation.observer_kind,
                "job_id": observation.job_id,
                "status_ref": observation.status_ref,
                "display_name": observation.display_name or tool_name,
                "topic_summary": observation.topic_summary,
                "poll_after_seconds": observation.poll_after_seconds,
                "ready_return": observation.ready_return,
            },
        )
        return ToolExecutionResult(
            model_content=observation.topic_summary,
            raw_result={"job_id": observation.job_id, "status_ref": observation.status_ref},
            blocked=True,
            suspended=True,
            waiting_kind="observing",
            observation=observation,
            approval_id=approval_ref,
            policy_decision=policy,
            decision_id=decision_id,
            capability_grant_id=capability_grant_id,
            workspace_lease_id=workspace_lease_id,
            policy_ref=policy_ref,
            witness_ref=witness_ref,
            result_code="observation_submitted",
            execution_status="observing",
            state_applied=True,
        )

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_tool_call_observation(self, ticket: ObservationTicket) -> dict[str, Any]:
        tool_name = ticket.status_tool_name or ticket.tool_name
        if not tool_name:
            return {
                "status": "failed",
                "topic_summary": "Observation ticket is missing a status tool name.",
                "result": {"error": "missing status tool"},
                "is_error": True,
            }
        tool = self.registry.get(tool_name)
        payload = dict(ticket.status_tool_input or {})
        payload.setdefault("job_id", ticket.job_id)
        payload.setdefault("status_ref", ticket.status_ref)
        result = tool.handler(payload)
        nested = normalize_observation_ticket(result)
        if nested is not None:
            return {
                "status": "observing",
                "topic_summary": nested.topic_summary,
                "poll_after_seconds": nested.poll_after_seconds,
                "progress": nested.progress,
            }
        return result

    def _poll_ticket(self, ticket: ObservationTicket) -> dict[str, Any]:
        if ticket.observer_kind == "local_process":
            sandbox = getattr(getattr(self.registry, "_tools", {}).get("bash"), "handler", None)
            sandbox_self = getattr(sandbox, "_sandbox", None) or getattr(sandbox, "__self__", None)
            if sandbox_self is None or not hasattr(sandbox_self, "poll"):
                return {
                    "status": "failed",
                    "topic_summary": (f"Observation handler unavailable for job {ticket.job_id}."),
                    "result": {"error": "local process observer unavailable"},
                    "is_error": True,
                }
            return sandbox_self.poll(ticket.job_id)
        if ticket.observer_kind == "tool_call":
            return self._poll_tool_call_observation(ticket)
        return {
            "status": "failed",
            "topic_summary": f"Unsupported observer kind: {ticket.observer_kind}",
            "result": {"error": f"unsupported observer kind: {ticket.observer_kind}"},
            "is_error": True,
        }

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    def finalize_observation(
        self,
        attempt_ctx: TaskExecutionContext,
        *,
        terminal_status: str,
        raw_result: Any,
        is_error: bool,
        summary: str,
        model_content_override: Any = None,
    ) -> dict[str, Any]:
        pending = self._executor._load_pending_execution(attempt_ctx.step_attempt_id)
        if not pending:
            model_content = (
                model_content_override
                if model_content_override is not None
                else _format_model_content(raw_result, self.tool_output_limit)
            )
            return {
                "raw_result": raw_result,
                "model_content": model_content,
                "is_error": is_error,
                "result_code": terminal_status,
            }
        tool_name = str(pending.get("tool_name", ""))
        tool_input = dict(pending.get("tool_input", {}) or {})
        tool = self.registry.get(tool_name)
        policy = PolicyDecision.from_dict(dict(pending.get("policy", {}) or {}))
        policy_ref = str(pending.get("policy_ref", "") or "") or None
        decision_ref = str(pending.get("decision_id", "") or "") or None
        capability_grant_ref = str(pending.get("capability_grant_id", "") or "") or None
        workspace_lease_ref = str(pending.get("workspace_lease_id", "") or "") or None
        approval_ref = str(pending.get("approval_ref", "") or "") or None
        witness_ref = str(pending.get("witness_ref", "") or "") or None
        action_request_ref = str(pending.get("action_request_ref", "") or "") or None
        policy_result_ref = str(pending.get("policy_result_ref", "") or "") or None
        environment_ref = str(pending.get("environment_ref", "") or "") or None
        approval_mode = str(pending.get("approval_mode", "") or "")
        rollback_plan = dict(pending.get("rollback_plan", {}) or {})

        result_code = (
            terminal_status
            if terminal_status in {"failed", "timeout", "cancelled"}
            else "succeeded"
        )
        action_type = tool.action_class or self.policy_engine.infer_action_class(tool)
        governed = _is_governed_action(tool, policy)
        model_content = (
            model_content_override
            if model_content_override is not None
            else _format_model_content(raw_result, self.tool_output_limit)
        )
        self._executor._set_attempt_phase(attempt_ctx, "settling", reason="observation_finalized")
        receipt_id = None
        if policy.requires_receipt:
            if capability_grant_ref and result_code == "succeeded":
                self.capability_service.consume(capability_grant_ref)
            contract, _evidence_case, authorization_plan = self._executor._load_contract_bundle(
                attempt_ctx
            )
            receipt_id = self._executor._issue_receipt(
                tool=tool,
                tool_name=tool_name,
                tool_input=tool_input,
                raw_result=raw_result,
                attempt_ctx=attempt_ctx,
                approval_ref=approval_ref,
                policy=policy,
                policy_ref=policy_ref,
                decision_ref=decision_ref,
                capability_grant_ref=capability_grant_ref,
                workspace_lease_ref=workspace_lease_ref,
                action_request_ref=action_request_ref,
                policy_result_ref=policy_result_ref,
                witness_ref=witness_ref,
                environment_ref=environment_ref,
                result_code=result_code,
                idempotency_key=str(pending.get("idempotency_key", "") or "") or None,
                result_summary=summary
                if result_code != "succeeded"
                else self._executor._successful_result_summary(
                    tool_name=tool_name,
                    approval_mode=approval_mode,
                ),
                output_kind="tool_error" if is_error else "tool_output",
                rollback_supported=bool(rollback_plan.get("supported", False)),
                rollback_strategy=str(rollback_plan.get("strategy", "") or "") or None,
                rollback_artifact_refs=list(rollback_plan.get("artifact_refs", []) or []),
                contract_ref=getattr(contract, "contract_id", None),
                authorization_plan_ref=getattr(authorization_plan, "authorization_plan_id", None),
                observed_effect_summary=summary,
                reconciliation_required=governed,
            )
            if governed:
                action_request = self.policy_engine.build_action_request(
                    tool, tool_input, attempt_ctx=attempt_ctx
                )
                self._executor._record_reconciliation(
                    attempt_ctx=attempt_ctx,
                    receipt_id=receipt_id,
                    action_type=action_type,
                    tool_input=tool_input,
                    observables=dict(action_request.derived),
                    witness_ref=witness_ref,
                    result_code_hint=result_code,
                    authorized_effect_summary=self._executor._authorized_effect_summary(
                        action_request=action_request,
                        contract=contract,
                    ),
                )
        self._executor._clear_pending_execution(attempt_ctx.step_attempt_id)
        # C10: Resolve any active observation tickets for this attempt so they
        # do not remain orphaned in the "active" state after finalization.
        try:
            self.store.resolve_observations_for_attempt(
                attempt_ctx.step_attempt_id, status=result_code
            )
        except Exception:
            pass
        return {
            "raw_result": raw_result,
            "model_content": model_content if not is_error else f"Error: {summary}",
            "is_error": is_error,
            "result_code": result_code,
        }

    # ------------------------------------------------------------------
    # Progress summary
    # ------------------------------------------------------------------

    def _progress_summary_facts(
        self,
        *,
        task_id: str,
        step_attempt_id: str,
        ticket: ObservationTicket,
        status: str,
        progress: ObservationProgress | None,
    ) -> dict[str, Any]:
        task = self.store.get_task(task_id)
        events = self.store.list_events(task_id=task_id, limit=500)[-80:]
        relevant_types = {
            "task.created",
            "task.note.appended",
            "tool.submitted",
            "tool.progressed",
            "tool.status.changed",
            "approval.requested",
            "approval.granted",
            "approval.denied",
            "approval.consumed",
        }
        recent_events: list[dict[str, Any]] = []
        for event in events:
            if event["event_type"] not in relevant_types:
                continue
            payload = dict(event.get("payload", {}) or {})
            text = ""
            if event["event_type"] == "task.note.appended":
                text = str(payload.get("raw_text", "") or payload.get("prompt", "")).strip()
            else:
                text = (
                    str(payload.get("summary", "") or "")
                    or str(payload.get("topic_summary", "") or "")
                    or str(payload.get("detail", "") or "")
                    or str(payload.get("status", "") or "")
                ).strip()
            phase = str(payload.get("phase", "") or "").strip()
            recent_events.append(
                {
                    "event_type": event["event_type"],
                    "text": _compact_progress_text(text, limit=180),
                    "phase": phase or None,
                    "progress_percent": payload.get("progress_percent"),
                }
            )
        latest_progress = progress or normalize_observation_progress(ticket.progress)
        return {
            "task": {
                "title": _compact_progress_text(getattr(task, "title", ""), limit=120),
                "goal": _compact_progress_text(getattr(task, "goal", ""), limit=600),
                "status": status,
                "source_channel": (getattr(task, "source_channel", "") if task is not None else ""),
            },
            "attempt": {
                "step_attempt_id": step_attempt_id,
                "tool_name": ticket.tool_name,
                "display_name": ticket.display_name or ticket.tool_name or "observed task",
                "topic_summary": ticket.topic_summary,
                "observer_kind": ticket.observer_kind,
            },
            "progress": latest_progress.to_dict() if latest_progress is not None else None,
            "recent_events": recent_events[-8:],
        }

    def _maybe_emit_progress_summary(
        self,
        *,
        step_attempt_id: str,
        task_id: str | None,
        step_id: str | None,
        ticket: ObservationTicket,
        status: str,
        progress: ObservationProgress | None,
        progress_changed: bool,
        now: float,
    ) -> None:
        if self.progress_summarizer is None or not task_id:
            return
        keepalive_due = (
            status == "observing"
            and self.progress_summary_keepalive_seconds > 0
            and ticket.last_progress_summary_at is not None
            and (now - ticket.last_progress_summary_at) >= self.progress_summary_keepalive_seconds
        )
        if not progress_changed and not keepalive_due:
            return
        try:
            summary = self.progress_summarizer.summarize(
                facts=self._progress_summary_facts(
                    task_id=task_id,
                    step_attempt_id=step_attempt_id,
                    ticket=ticket,
                    status=status,
                    progress=progress,
                )
            )
        except Exception:
            return
        if summary is None or not summary.summary.strip():
            return
        if not summary.phase:
            if progress is not None and progress.phase:
                summary.phase = progress.phase
            elif status:
                summary.phase = status
        if summary.progress_percent is None and progress is not None:
            summary.progress_percent = progress.progress_percent
        previous_signature = _progress_summary_signature(ticket.progress_summary)
        current_signature = summary.signature()
        ticket.last_progress_summary_at = now
        if current_signature == previous_signature:
            return
        ticket.progress_summary = summary.to_dict()
        self.store.append_event(
            event_type="task.progress.summarized",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            step_id=step_id,
            actor="kernel",
            payload={
                **summary.to_dict(),
                "job_id": ticket.job_id,
                "status": status,
            },
        )

    # ------------------------------------------------------------------
    # Poll observation (main entry point for polling loop)
    # ------------------------------------------------------------------

    def poll_observation(
        self, step_attempt_id: str, *, now: float | None = None
    ) -> ObservationPollResult | None:
        payload = self._executor.load_suspended_state(step_attempt_id)
        if str(payload.get("suspend_kind", "")) != "observing":
            return None
        observation_data: Any = payload.get("observation")
        if not isinstance(observation_data, dict):
            return None
        ticket = ObservationTicket.from_dict(cast(dict[str, Any], observation_data))
        current = time.time() if now is None else now
        if ticket.next_poll_at and current < ticket.next_poll_at:
            return ObservationPollResult(ticket=ticket, should_resume=False)

        status_payload = self._poll_ticket(ticket)
        status = str(status_payload.get("status", "observing") or "observing")
        progress = normalize_observation_progress(status_payload.get("progress"))
        summary = str(
            status_payload.get("topic_summary", ticket.topic_summary) or ticket.topic_summary
        )
        if progress is not None and progress.summary:
            summary = progress.summary
        task_attempt = self.store.get_step_attempt(step_attempt_id)
        task_id = task_attempt.task_id if task_attempt else None
        step_id = task_attempt.step_id if task_attempt else None

        previous_progress_sig = _progress_signature(ticket.progress)
        current_progress_sig = progress.signature() if progress is not None else None
        if progress is not None:
            ticket.progress = progress.to_dict()
            ticket.topic_summary = progress.summary or summary
            if current_progress_sig != previous_progress_sig:
                self.store.append_event(
                    event_type="tool.progressed",
                    entity_type="step_attempt",
                    entity_id=step_attempt_id,
                    task_id=task_id,
                    step_id=step_id,
                    actor="kernel",
                    payload={
                        "job_id": ticket.job_id,
                        "phase": progress.phase,
                        "summary": progress.summary,
                        "detail": progress.detail,
                        "progress_percent": progress.progress_percent,
                        "ready": bool(progress.ready),
                    },
                )
        else:
            ticket.topic_summary = summary
            progress = normalize_observation_progress(ticket.progress)

        if status != ticket.last_status or summary != ticket.last_status_summary:
            self.store.append_event(
                event_type="tool.status.changed",
                entity_type="step_attempt",
                entity_id=step_attempt_id,
                task_id=task_id,
                step_id=step_id,
                actor="kernel",
                payload={
                    "job_id": ticket.job_id,
                    "status": status,
                    "topic_summary": summary,
                },
            )
        ticket.last_status = status
        ticket.last_status_summary = summary
        self._maybe_emit_progress_summary(
            step_attempt_id=step_attempt_id,
            task_id=task_id,
            step_id=step_id,
            ticket=ticket,
            status=status,
            progress=progress,
            progress_changed=current_progress_sig != previous_progress_sig,
            now=current,
        )

        if (
            status == "observing"
            and ticket.ready_return
            and progress is not None
            and progress.ready
        ):
            attempt_ctx = self._attempt_context_from_snapshot(step_attempt_id)
            ready_result = status_payload.get("result")
            if ready_result is None:
                ready_result = {
                    "job_id": ticket.job_id,
                    "status_ref": ticket.status_ref,
                    "ready": True,
                }
            final = self.finalize_observation(
                attempt_ctx,
                terminal_status="completed",
                raw_result=ready_result,
                is_error=False,
                summary=ticket.topic_summary,
                model_content_override=ticket.topic_summary,
            )
            ticket.terminal_status = "completed"
            ticket.final_result = final["raw_result"]
            ticket.final_model_content = final["model_content"]
            ticket.final_is_error = bool(final["is_error"])
            payload["observation"] = ticket.to_dict()
            self._update_runtime_snapshot(step_attempt_id, payload)
            return ObservationPollResult(ticket=ticket, should_resume=True)

        if status == "observing":
            ticket.poll_after_seconds = float(
                status_payload.get("poll_after_seconds", ticket.poll_after_seconds)
                or ticket.poll_after_seconds
            )
            ticket.schedule_next_poll(now=current)
            payload["observation"] = ticket.to_dict()
            self._update_runtime_snapshot(step_attempt_id, payload)
            return ObservationPollResult(ticket=ticket, should_resume=False)

        attempt_ctx = self._attempt_context_from_snapshot(step_attempt_id)
        final = self.finalize_observation(
            attempt_ctx,
            terminal_status=status,
            raw_result=status_payload.get("result"),
            is_error=bool(status_payload.get("is_error", False) or status != "completed"),
            summary=summary,
        )
        ticket.terminal_status = status
        ticket.final_result = final["raw_result"]
        ticket.final_model_content = final["model_content"]
        ticket.final_is_error = bool(final["is_error"])
        payload["observation"] = ticket.to_dict()
        self._update_runtime_snapshot(step_attempt_id, payload)
        return ObservationPollResult(ticket=ticket, should_resume=True)

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------

    def _attempt_context_from_snapshot(self, step_attempt_id: str) -> TaskExecutionContext:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            raise KeyError(f"Unknown step attempt: {step_attempt_id}")
        task = self.store.get_task(attempt.task_id)
        if task is None:
            raise KeyError(f"Unknown task for step attempt: {step_attempt_id}")
        return TaskExecutionContext(
            conversation_id=task.conversation_id,
            task_id=task.task_id,
            step_id=attempt.step_id,
            step_attempt_id=attempt.step_attempt_id,
            source_channel=task.source_channel,
            policy_profile=task.policy_profile,
            workspace_root=str(attempt.context.get("workspace_root", "") or ""),
        )

    def _update_runtime_snapshot(self, step_attempt_id: str, payload: dict[str, Any]) -> None:
        snapshot_payload = dict(payload)
        snapshot_payload.pop("messages", None)
        envelope = self._executor._runtime_snapshot_envelope(snapshot_payload)
        attempt = self.store.get_step_attempt(step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        if "note_cursor_event_seq" in snapshot_payload:
            context["note_cursor_event_seq"] = int(
                snapshot_payload.get("note_cursor_event_seq", 0) or 0
            )
        context[_RUNTIME_SNAPSHOT_KEY] = envelope
        if attempt is None:
            return
        resume_from_ref = self._executor._store_runtime_snapshot_artifact(
            attempt_ctx=self._attempt_context_from_snapshot(step_attempt_id),
            envelope=envelope,
            suspend_kind=str(
                snapshot_payload.get("suspend_kind", attempt.status or "suspended") or "suspended"
            ),
        )
        self.store.update_step_attempt(
            step_attempt_id,
            context=context,
            resume_from_ref=resume_from_ref,
        )
