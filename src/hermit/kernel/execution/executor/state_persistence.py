from __future__ import annotations

import json
from typing import Any, cast

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.coordination.observation import ObservationTicket
from hermit.kernel.execution.executor.snapshot import RuntimeSnapshotManager
from hermit.kernel.ledger.journal.store import KernelStore

_RUNTIME_SNAPSHOT_KEY = "runtime_snapshot"
_PENDING_EXECUTION_KEY = "pending_observation_execution"
_PENDING_EXECUTION_KIND = "runtime.pending_execution"


def _t(message_key: str, *, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=resolve_locale(), default=default, **kwargs)


class StatePersistence:
    """Suspend / resume state persistence for governed tool execution."""

    def __init__(
        self,
        *,
        store: KernelStore,
        artifact_store: ArtifactStore,
        _snapshot: RuntimeSnapshotManager,
        _store_json_artifact: Any,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self._snapshot = _snapshot
        self._store_json_artifact = _store_json_artifact

    def persist_suspended_state(
        self,
        attempt_ctx: TaskExecutionContext,
        *,
        suspend_kind: str,
        pending_tool_blocks: list[dict[str, Any]],
        tool_result_blocks: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        next_turn: int,
        disable_tools: bool,
        readonly_only: bool,
        note_cursor_event_seq: int = 0,
        observation: ObservationTicket | None = None,
    ) -> None:
        resume_messages_ref = self._store_resume_messages(messages, attempt_ctx=attempt_ctx)
        payload = {
            "suspend_kind": suspend_kind,
            "resume_messages_ref": resume_messages_ref,
            "pending_tool_blocks": pending_tool_blocks,
            "tool_result_blocks": tool_result_blocks,
            "next_turn": next_turn,
            "disable_tools": disable_tools,
            "readonly_only": readonly_only,
            "note_cursor_event_seq": note_cursor_event_seq,
            "observation": observation.to_dict() if observation is not None else None,
        }
        envelope = self._runtime_snapshot_envelope(payload)
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        if attempt_ctx.workspace_root:
            context["workspace_root"] = attempt_ctx.workspace_root
        context["note_cursor_event_seq"] = note_cursor_event_seq
        context[_RUNTIME_SNAPSHOT_KEY] = envelope
        context["phase"] = suspend_kind
        resume_from_ref = self._store_runtime_snapshot_artifact(
            attempt_ctx=attempt_ctx,
            envelope=envelope,
            suspend_kind=suspend_kind,
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            status=suspend_kind,
            context=context,
            resume_from_ref=resume_from_ref,
        )

    def persist_blocked_state(
        self,
        attempt_ctx: TaskExecutionContext,
        *,
        pending_tool_blocks: list[dict[str, Any]],
        tool_result_blocks: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        next_turn: int,
        disable_tools: bool,
        readonly_only: bool,
    ) -> None:
        self.persist_suspended_state(
            attempt_ctx,
            suspend_kind="awaiting_approval",
            pending_tool_blocks=pending_tool_blocks,
            tool_result_blocks=tool_result_blocks,
            messages=messages,
            next_turn=next_turn,
            disable_tools=disable_tools,
            readonly_only=readonly_only,
        )

    def load_suspended_state(self, step_attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            raise KeyError(
                _t(
                    "kernel.executor.error.unknown_step_attempt",
                    default="Unknown step attempt: {step_attempt_id}",
                    step_attempt_id=step_attempt_id,
                )
            )
        envelope = self._load_runtime_snapshot_envelope(attempt)
        if not envelope:
            return {}
        payload = self._runtime_snapshot_payload(envelope)
        if "messages" not in payload:
            resume_messages_ref = str(payload.get("resume_messages_ref", "") or "").strip()
            payload["messages"] = self._load_resume_messages(resume_messages_ref)
        return payload

    def load_blocked_state(self, step_attempt_id: str) -> dict[str, Any]:
        return self.load_suspended_state(step_attempt_id)

    def clear_suspended_state(self, step_attempt_id: str) -> None:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        context = dict(attempt.context)
        context.pop(_RUNTIME_SNAPSHOT_KEY, None)
        context.pop(_PENDING_EXECUTION_KEY, None)
        self.store.update_step_attempt(
            step_attempt_id,
            context=context,
            waiting_reason=None,
            resume_from_ref=None,
        )

    def clear_blocked_state(self, step_attempt_id: str) -> None:
        self.clear_suspended_state(step_attempt_id)

    def _runtime_snapshot_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._snapshot.create_envelope(payload)

    def _runtime_snapshot_payload(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._snapshot.extract_payload(envelope)

    def _store_resume_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        return self._store_json_artifact(
            payload=messages,
            kind="runtime.resume_messages",
            attempt_ctx=attempt_ctx,
            metadata={"message_count": len(messages)},
        )

    def _load_resume_messages(self, resume_messages_ref: str) -> list[dict[str, Any]]:
        return self._snapshot.load_resume_messages(resume_messages_ref)

    def _store_runtime_snapshot_artifact(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        envelope: dict[str, Any],
        suspend_kind: str,
    ) -> str:
        return self._store_json_artifact(
            payload=envelope,
            kind="runtime.snapshot",
            attempt_ctx=attempt_ctx,
            metadata={"suspend_kind": suspend_kind},
        )

    def _store_pending_execution_artifact(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        payload: dict[str, Any],
    ) -> str:
        return self._store_json_artifact(
            payload={
                "schema": "runtime.pending_execution/v1",
                "payload": payload,
            },
            kind=_PENDING_EXECUTION_KIND,
            attempt_ctx=attempt_ctx,
            metadata={
                "status": "observing",
                "tool_name": str(payload.get("tool_name", "") or ""),
            },
        )

    def _load_runtime_snapshot_envelope(self, attempt: Any) -> dict[str, Any]:
        resume_from_ref = str(getattr(attempt, "resume_from_ref", "") or "").strip()
        if resume_from_ref:
            artifact = self.store.get_artifact(resume_from_ref)
            if artifact is not None:
                try:
                    payload: Any = json.loads(self.artifact_store.read_text(artifact.uri))
                except (OSError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, dict):
                    return cast(dict[str, Any], payload)
        context_val: Any = getattr(attempt, "context", {}) or {}
        snapshot_val: Any = cast(dict[str, Any], context_val).get(_RUNTIME_SNAPSHOT_KEY) or {}
        return cast(dict[str, Any], snapshot_val) if isinstance(snapshot_val, dict) else {}

    def _load_json_artifact_payload(self, artifact_ref: str) -> dict[str, Any]:
        artifact = self.store.get_artifact(artifact_ref)
        if artifact is None:
            return {}
        try:
            payload: Any = json.loads(self.artifact_store.read_text(artifact.uri))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        payload_dict = cast(dict[str, Any], payload)
        if payload_dict.get("schema") == "runtime.pending_execution/v1":
            nested: Any = payload_dict.get("payload")
            return cast(dict[str, Any], nested) if isinstance(nested, dict) else {}
        return payload_dict

    def current_note_cursor(self, step_attempt_id: str) -> int:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return 0
        return int(attempt.context.get("note_cursor_event_seq", 0) or 0)

    def consume_appended_notes(
        self, attempt_ctx: TaskExecutionContext
    ) -> tuple[list[dict[str, Any]], int]:
        cursor = self.current_note_cursor(attempt_ctx.step_attempt_id)
        events = self.store.list_events(
            task_id=attempt_ctx.task_id,
            after_event_seq=cursor,
            limit=200,
        )
        note_events = [event for event in events if event["event_type"] == "task.note.appended"]
        if not note_events:
            return cast(list[dict[str, Any]], []), cursor
        latest = int(note_events[-1]["event_seq"])
        messages: list[dict[str, Any]] = []
        for event in note_events:
            payload = cast(dict[str, Any], event.get("payload") or {})
            prompt = str(payload.get("prompt", "") or payload.get("raw_text", "")).strip()
            if not prompt:
                continue
            messages.append(
                {
                    "role": "user",
                    "content": (f"[Task Note Appended]\n{prompt}"),
                }
            )
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        context["note_cursor_event_seq"] = latest
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, context=context)
        return messages, latest

    def _store_pending_execution(
        self, attempt_ctx: TaskExecutionContext, payload: dict[str, Any]
    ) -> None:
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        context = dict(attempt.context) if attempt is not None else {}
        context[_PENDING_EXECUTION_KEY] = payload
        pending_execution_ref = self._store_pending_execution_artifact(
            attempt_ctx=attempt_ctx,
            payload=payload,
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            context=context,
            decision_id=str(payload.get("decision_id", "") or "") or None,
            capability_grant_id=str(payload.get("capability_grant_id", "") or "") or None,
            workspace_lease_id=str(payload.get("workspace_lease_id", "") or "") or None,
            state_witness_ref=str(payload.get("witness_ref", "") or "") or None,
            action_request_ref=str(payload.get("action_request_ref", "") or "") or None,
            policy_result_ref=str(payload.get("policy_result_ref", "") or "") or None,
            approval_packet_ref=str(payload.get("approval_packet_ref", "") or "") or None,
            pending_execution_ref=pending_execution_ref,
            idempotency_key=str(payload.get("idempotency_key", "") or "") or None,
            environment_ref=str(payload.get("environment_ref", "") or "") or None,
        )

    def _load_pending_execution(self, step_attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return {}
        pending_execution_ref = str(getattr(attempt, "pending_execution_ref", "") or "").strip()
        if pending_execution_ref:
            payload = self._load_json_artifact_payload(pending_execution_ref)
            if payload:
                return payload
        payload_raw: Any = attempt.context.get(_PENDING_EXECUTION_KEY) or {}
        return cast(dict[str, Any], payload_raw) if isinstance(payload_raw, dict) else {}

    def _clear_pending_execution(self, step_attempt_id: str) -> None:
        attempt = self.store.get_step_attempt(step_attempt_id)
        if attempt is None:
            return
        context = dict(attempt.context)
        context.pop(_PENDING_EXECUTION_KEY, None)
        self.store.update_step_attempt(
            step_attempt_id,
            context=context,
            pending_execution_ref=None,
        )
