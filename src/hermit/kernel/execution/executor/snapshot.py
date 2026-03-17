from __future__ import annotations

import json
import time
from typing import Any, cast

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.errors import SnapshotError
from hermit.kernel.ledger.journal.store import KernelStore

_RUNTIME_SNAPSHOT_KEY = "runtime_snapshot"
_RUNTIME_SNAPSHOT_SCHEMA_VERSION = 2
_RUNTIME_SNAPSHOT_TTL_SECONDS = 24 * 60 * 60
_RUNTIME_SNAPSHOT_MAX_BYTES = 256 * 1024
_RUNTIME_SNAPSHOT_V1_ALLOWED_KEYS = {
    "messages",
    "pending_tool_blocks",
    "tool_result_blocks",
    "next_turn",
    "disable_tools",
    "readonly_only",
}
_RUNTIME_SNAPSHOT_V2_ALLOWED_KEYS = {
    "suspend_kind",
    "resume_messages_ref",
    "pending_tool_blocks",
    "tool_result_blocks",
    "next_turn",
    "disable_tools",
    "readonly_only",
    "note_cursor_event_seq",
    "observation",
}
_RUNTIME_SNAPSHOT_V3_ALLOWED_KEYS = {
    "suspend_kind",
    "resume_messages_ref",
    "pending_tool_blocks",
    "tool_result_blocks",
    "next_turn",
    "disable_tools",
    "readonly_only",
    "note_cursor_event_seq",
    "observation",
}


def _t(message_key: str, *, default: str | None = None, **kwargs: object) -> str:
    return tr(message_key, locale=resolve_locale(), default=default, **kwargs)


class RuntimeSnapshotManager:
    """Manages runtime snapshot envelope creation, validation, and resume message storage."""

    def __init__(
        self,
        *,
        store: KernelStore,
        artifact_store: ArtifactStore,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store

    def create_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        unknown = set(payload) - _RUNTIME_SNAPSHOT_V3_ALLOWED_KEYS
        if unknown:
            raise SnapshotError(
                "unsupported_keys",
                _t(
                    "kernel.executor.error.unsupported_working_state_keys",
                    default="Unsupported working-state keys: {keys}",
                    keys=sorted(unknown),
                ),
            )
        envelope = {
            "schema_version": _RUNTIME_SNAPSHOT_SCHEMA_VERSION,
            "kind": _RUNTIME_SNAPSHOT_KEY,
            "expires_at": time.time() + _RUNTIME_SNAPSHOT_TTL_SECONDS,
            "payload": payload,
        }
        encoded = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        if len(encoded) > _RUNTIME_SNAPSHOT_MAX_BYTES:
            raise SnapshotError(
                "too_large",
                _t(
                    "kernel.executor.error.snapshot_too_large",
                    default="Runtime snapshot exceeds working-state size limit",
                ),
            )
        return envelope

    def extract_payload(self, envelope: dict[str, Any]) -> dict[str, Any]:
        schema_version = int(envelope.get("schema_version", 0))
        if schema_version not in {1, 2, _RUNTIME_SNAPSHOT_SCHEMA_VERSION}:
            raise SnapshotError(
                "unsupported_schema",
                _t(
                    "kernel.executor.error.unsupported_snapshot_schema",
                    default="Unsupported runtime snapshot schema version",
                ),
            )
        if str(envelope.get("kind", "")) != _RUNTIME_SNAPSHOT_KEY:
            raise SnapshotError(
                "invalid_kind",
                _t(
                    "kernel.executor.error.invalid_snapshot_kind",
                    default="Invalid runtime snapshot kind",
                ),
            )
        expires_at = float(envelope.get("expires_at", 0) or 0)
        if expires_at and expires_at < time.time():
            raise SnapshotError(
                "expired",
                _t(
                    "kernel.executor.error.snapshot_expired",
                    default="Runtime snapshot expired",
                ),
            )
        payload = dict(envelope.get("payload", {}))
        allowed_keys = (
            _RUNTIME_SNAPSHOT_V1_ALLOWED_KEYS
            if schema_version == 1
            else _RUNTIME_SNAPSHOT_V2_ALLOWED_KEYS
            if schema_version == 2
            else _RUNTIME_SNAPSHOT_V3_ALLOWED_KEYS
        )
        unknown = set(payload) - allowed_keys
        if unknown:
            raise SnapshotError(
                "unsupported_keys",
                _t(
                    "kernel.executor.error.snapshot_contains_unsupported_keys",
                    default="Runtime snapshot contains unsupported keys: {keys}",
                    keys=sorted(unknown),
                ),
            )
        encoded = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        if len(encoded) > _RUNTIME_SNAPSHOT_MAX_BYTES:
            raise SnapshotError(
                "too_large",
                _t(
                    "kernel.executor.error.snapshot_too_large",
                    default="Runtime snapshot exceeds working-state size limit",
                ),
            )
        return payload

    def store_resume_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        attempt_ctx: TaskExecutionContext,
        store_artifact: Any,
    ) -> str:
        return store_artifact(
            payload=messages,
            kind="runtime.resume_messages",
            attempt_ctx=attempt_ctx,
            metadata={"message_count": len(messages)},
        )

    def load_resume_messages(self, resume_messages_ref: str) -> list[dict[str, Any]]:
        artifact = self.store.get_artifact(resume_messages_ref)
        if artifact is None:
            raise SnapshotError(
                "unknown_artifact",
                _t(
                    "kernel.executor.error.unknown_resume_messages_artifact",
                    default="Unknown resume messages artifact: {resume_messages_ref}",
                    resume_messages_ref=resume_messages_ref,
                ),
            )
        payload: Any = json.loads(self.artifact_store.read_text(artifact.uri))
        if not isinstance(payload, list):
            raise SnapshotError(
                "invalid_format",
                _t(
                    "kernel.executor.error.resume_messages_not_list",
                    default="Runtime resume messages artifact is not a list",
                ),
            )
        return [
            cast(dict[str, Any], message)
            for message in cast(list[Any], payload)
            if isinstance(message, dict)
        ]

    def store_snapshot_artifact(
        self,
        envelope: dict[str, Any],
        *,
        attempt_ctx: TaskExecutionContext,
        store_artifact: Any,
    ) -> str:
        return store_artifact(
            payload=envelope,
            kind="runtime.snapshot",
            attempt_ctx=attempt_ctx,
            metadata={"schema_version": envelope.get("schema_version")},
        )

    def load_snapshot_envelope(self, snapshot_ref: str) -> dict[str, Any] | None:
        artifact = self.store.get_artifact(snapshot_ref)
        if artifact is None:
            return None
        try:
            raw: Any = json.loads(self.artifact_store.read_text(artifact.uri))
        except (OSError, json.JSONDecodeError):
            return None
        return cast(dict[str, Any], raw) if isinstance(raw, dict) else None
