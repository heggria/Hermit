"""Tests for hermit.kernel.execution.executor.snapshot."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.errors import SnapshotError
from hermit.kernel.execution.executor.snapshot import (
    _RUNTIME_SNAPSHOT_KEY,
    _RUNTIME_SNAPSHOT_MAX_BYTES,
    _RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    _RUNTIME_SNAPSHOT_V3_ALLOWED_KEYS,
    RuntimeSnapshotManager,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager() -> RuntimeSnapshotManager:
    store = MagicMock()
    artifact_store = MagicMock()
    return RuntimeSnapshotManager(store=store, artifact_store=artifact_store)


def _make_attempt_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults: dict[str, Any] = {
        "conversation_id": "conv-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "source_channel": "cli",
    }
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


# ---------------------------------------------------------------------------
# create_envelope
# ---------------------------------------------------------------------------


class TestCreateEnvelope:
    def test_valid_payload(self) -> None:
        mgr = _make_manager()
        payload = {"suspend_kind": "observing", "next_turn": 5}
        envelope = mgr.create_envelope(payload)
        assert envelope["schema_version"] == _RUNTIME_SNAPSHOT_SCHEMA_VERSION
        assert envelope["kind"] == _RUNTIME_SNAPSHOT_KEY
        assert envelope["payload"] == payload
        assert "expires_at" in envelope

    def test_unsupported_keys_raise_error(self) -> None:
        mgr = _make_manager()
        payload = {"suspend_kind": "observing", "bad_key": "nope"}
        with pytest.raises(SnapshotError) as exc_info:
            mgr.create_envelope(payload)
        assert exc_info.value.code == "unsupported_keys"

    def test_all_v3_keys_accepted(self) -> None:
        mgr = _make_manager()
        payload = {key: None for key in _RUNTIME_SNAPSHOT_V3_ALLOWED_KEYS}
        envelope = mgr.create_envelope(payload)
        assert envelope["payload"] == payload

    def test_too_large_raises_error(self) -> None:
        mgr = _make_manager()
        payload = {"suspend_kind": "x" * (_RUNTIME_SNAPSHOT_MAX_BYTES + 1)}
        with pytest.raises(SnapshotError) as exc_info:
            mgr.create_envelope(payload)
        assert exc_info.value.code == "too_large"

    def test_expires_at_is_future(self) -> None:
        mgr = _make_manager()
        before = time.time()
        envelope = mgr.create_envelope({"suspend_kind": "test"})
        assert envelope["expires_at"] > before


# ---------------------------------------------------------------------------
# extract_payload
# ---------------------------------------------------------------------------


class TestExtractPayload:
    def _valid_envelope(self, **overrides: Any) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "schema_version": _RUNTIME_SNAPSHOT_SCHEMA_VERSION,
            "kind": _RUNTIME_SNAPSHOT_KEY,
            "expires_at": time.time() + 3600,
            "payload": {"suspend_kind": "observing"},
        }
        envelope.update(overrides)
        return envelope

    def test_valid_envelope_v3(self) -> None:
        mgr = _make_manager()
        envelope = self._valid_envelope()
        payload = mgr.extract_payload(envelope)
        assert payload == {"suspend_kind": "observing"}

    def test_valid_envelope_v1(self) -> None:
        mgr = _make_manager()
        envelope = self._valid_envelope(
            schema_version=1,
            payload={"messages": [], "next_turn": 1},
        )
        payload = mgr.extract_payload(envelope)
        assert "messages" in payload

    def test_valid_envelope_v2(self) -> None:
        mgr = _make_manager()
        envelope = self._valid_envelope(
            schema_version=2,
            payload={"suspend_kind": "test", "next_turn": 1},
        )
        payload = mgr.extract_payload(envelope)
        assert payload["suspend_kind"] == "test"

    def test_unsupported_schema_version(self) -> None:
        mgr = _make_manager()
        envelope = self._valid_envelope(schema_version=99)
        with pytest.raises(SnapshotError) as exc_info:
            mgr.extract_payload(envelope)
        assert exc_info.value.code == "unsupported_schema"

    def test_invalid_kind(self) -> None:
        mgr = _make_manager()
        envelope = self._valid_envelope(kind="wrong_kind")
        with pytest.raises(SnapshotError) as exc_info:
            mgr.extract_payload(envelope)
        assert exc_info.value.code == "invalid_kind"

    def test_expired_snapshot(self) -> None:
        mgr = _make_manager()
        envelope = self._valid_envelope(expires_at=time.time() - 3600)
        with pytest.raises(SnapshotError) as exc_info:
            mgr.extract_payload(envelope)
        assert exc_info.value.code == "expired"

    def test_zero_expires_at_is_valid(self) -> None:
        mgr = _make_manager()
        envelope = self._valid_envelope(expires_at=0)
        # Should NOT raise expired
        payload = mgr.extract_payload(envelope)
        assert payload is not None

    def test_unsupported_payload_keys(self) -> None:
        mgr = _make_manager()
        envelope = self._valid_envelope(payload={"suspend_kind": "ok", "bad_key": "nope"})
        with pytest.raises(SnapshotError) as exc_info:
            mgr.extract_payload(envelope)
        assert exc_info.value.code == "unsupported_keys"

    def test_v1_disallows_v2_keys(self) -> None:
        mgr = _make_manager()
        envelope = self._valid_envelope(
            schema_version=1,
            payload={"messages": [], "observation": {}},
        )
        with pytest.raises(SnapshotError) as exc_info:
            mgr.extract_payload(envelope)
        assert exc_info.value.code == "unsupported_keys"

    def test_too_large_payload(self) -> None:
        mgr = _make_manager()
        envelope = self._valid_envelope(
            payload={"suspend_kind": "x" * (_RUNTIME_SNAPSHOT_MAX_BYTES + 1)}
        )
        with pytest.raises(SnapshotError) as exc_info:
            mgr.extract_payload(envelope)
        assert exc_info.value.code == "too_large"


# ---------------------------------------------------------------------------
# store_resume_messages / load_resume_messages
# ---------------------------------------------------------------------------


class TestResumeMessages:
    def test_store_resume_messages(self) -> None:
        mgr = _make_manager()
        ctx = _make_attempt_ctx()
        store_artifact = MagicMock(return_value="art-1")
        messages = [{"role": "user", "content": "hello"}]
        ref = mgr.store_resume_messages(messages, attempt_ctx=ctx, store_artifact=store_artifact)
        assert ref == "art-1"
        store_artifact.assert_called_once()
        call_kwargs = store_artifact.call_args.kwargs
        assert call_kwargs["kind"] == "runtime.resume_messages"
        assert call_kwargs["metadata"]["message_count"] == 1

    def test_load_resume_messages_success(self) -> None:
        mgr = _make_manager()
        messages = [{"role": "user", "content": "hello"}]
        artifact = SimpleNamespace(uri="file:///tmp/test.json")
        mgr.store.get_artifact.return_value = artifact
        mgr.artifact_store.read_text.return_value = json.dumps(messages)
        result = mgr.load_resume_messages("ref-1")
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_load_resume_messages_unknown_artifact(self) -> None:
        mgr = _make_manager()
        mgr.store.get_artifact.return_value = None
        with pytest.raises(SnapshotError) as exc_info:
            mgr.load_resume_messages("ref-missing")
        assert exc_info.value.code == "unknown_artifact"

    def test_load_resume_messages_not_list(self) -> None:
        mgr = _make_manager()
        artifact = SimpleNamespace(uri="file:///tmp/test.json")
        mgr.store.get_artifact.return_value = artifact
        mgr.artifact_store.read_text.return_value = json.dumps({"not": "list"})
        with pytest.raises(SnapshotError) as exc_info:
            mgr.load_resume_messages("ref-1")
        assert exc_info.value.code == "invalid_format"

    def test_load_resume_messages_filters_non_dicts(self) -> None:
        mgr = _make_manager()
        artifact = SimpleNamespace(uri="file:///tmp/test.json")
        mgr.store.get_artifact.return_value = artifact
        mgr.artifact_store.read_text.return_value = json.dumps(
            [{"role": "user"}, "not_a_dict", 42, {"role": "assistant"}]
        )
        result = mgr.load_resume_messages("ref-1")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# store_snapshot_artifact / load_snapshot_envelope
# ---------------------------------------------------------------------------


class TestSnapshotArtifact:
    def test_store_snapshot_artifact(self) -> None:
        mgr = _make_manager()
        ctx = _make_attempt_ctx()
        store_artifact = MagicMock(return_value="snap-1")
        envelope = {"schema_version": 2, "payload": {}}
        ref = mgr.store_snapshot_artifact(envelope, attempt_ctx=ctx, store_artifact=store_artifact)
        assert ref == "snap-1"
        call_kwargs = store_artifact.call_args.kwargs
        assert call_kwargs["kind"] == "runtime.snapshot"

    def test_load_snapshot_envelope_success(self) -> None:
        mgr = _make_manager()
        envelope = {"schema_version": 2, "kind": "runtime_snapshot", "payload": {}}
        artifact = SimpleNamespace(uri="file:///tmp/snap.json")
        mgr.store.get_artifact.return_value = artifact
        mgr.artifact_store.read_text.return_value = json.dumps(envelope)
        result = mgr.load_snapshot_envelope("snap-1")
        assert result is not None
        assert result["schema_version"] == 2

    def test_load_snapshot_envelope_missing_artifact(self) -> None:
        mgr = _make_manager()
        mgr.store.get_artifact.return_value = None
        result = mgr.load_snapshot_envelope("snap-missing")
        assert result is None

    def test_load_snapshot_envelope_invalid_json(self) -> None:
        mgr = _make_manager()
        artifact = SimpleNamespace(uri="file:///tmp/snap.json")
        mgr.store.get_artifact.return_value = artifact
        mgr.artifact_store.read_text.return_value = "not json"
        result = mgr.load_snapshot_envelope("snap-1")
        assert result is None

    def test_load_snapshot_envelope_os_error(self) -> None:
        mgr = _make_manager()
        artifact = SimpleNamespace(uri="file:///tmp/snap.json")
        mgr.store.get_artifact.return_value = artifact
        mgr.artifact_store.read_text.side_effect = OSError("read fail")
        result = mgr.load_snapshot_envelope("snap-1")
        assert result is None

    def test_load_snapshot_envelope_non_dict(self) -> None:
        mgr = _make_manager()
        artifact = SimpleNamespace(uri="file:///tmp/snap.json")
        mgr.store.get_artifact.return_value = artifact
        mgr.artifact_store.read_text.return_value = json.dumps([1, 2, 3])
        result = mgr.load_snapshot_envelope("snap-1")
        assert result is None
