"""Tests for the StatePersistence module."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.state_persistence import (
    _PENDING_EXECUTION_KEY,
    _RUNTIME_SNAPSHOT_KEY,
    StatePersistence,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attempt_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults: dict[str, Any] = {
        "conversation_id": "conv_1",
        "task_id": "task_1",
        "step_id": "step_1",
        "step_attempt_id": "attempt_1",
        "source_channel": "cli",
    }
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


@dataclass
class FakeAttempt:
    context: dict[str, Any] = field(default_factory=dict)
    resume_from_ref: str | None = None
    pending_execution_ref: str | None = None


def _make_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 2, "kind": "runtime_snapshot", "payload": payload}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def artifact_store() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def snapshot() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def store_json_artifact() -> MagicMock:
    return MagicMock(return_value="artifact://ref/1")


@pytest.fixture()
def persistence(
    store: MagicMock,
    artifact_store: MagicMock,
    snapshot: MagicMock,
    store_json_artifact: MagicMock,
) -> StatePersistence:
    return StatePersistence(
        store=store,
        artifact_store=artifact_store,
        _snapshot=snapshot,
        _store_json_artifact=store_json_artifact,
    )


# ---------------------------------------------------------------------------
# persist_suspended_state
# ---------------------------------------------------------------------------


class TestPersistSuspendedState:
    def test_stores_envelope_and_updates_attempt(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        snapshot: MagicMock,
        store_json_artifact: MagicMock,
    ) -> None:
        envelope = _make_envelope({"suspend_kind": "awaiting_observation"})
        snapshot.create_envelope.return_value = envelope
        store_json_artifact.return_value = "artifact://snapshot/1"
        store.get_step_attempt.return_value = FakeAttempt(context={"existing": "val"})

        ctx = _make_attempt_ctx()
        persistence.persist_suspended_state(
            ctx,
            suspend_kind="awaiting_observation",
            pending_tool_blocks=[{"type": "tool_use", "id": "t1"}],
            tool_result_blocks=[],
            messages=[{"role": "assistant", "content": "hello"}],
            next_turn=3,
            disable_tools=False,
            readonly_only=True,
        )

        snapshot.create_envelope.assert_called_once()
        store.update_step_attempt.assert_called_once()
        call_kwargs = store.update_step_attempt.call_args[1]
        assert call_kwargs["status"] == "awaiting_observation"
        assert call_kwargs["resume_from_ref"] == "artifact://snapshot/1"
        context = call_kwargs["context"]
        assert context[_RUNTIME_SNAPSHOT_KEY] == envelope
        assert context["phase"] == "awaiting_observation"
        assert context["existing"] == "val"

    def test_preserves_workspace_root(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        snapshot: MagicMock,
    ) -> None:
        snapshot.create_envelope.return_value = _make_envelope({})
        store.get_step_attempt.return_value = FakeAttempt(context={})

        ctx = _make_attempt_ctx(workspace_root="/home/user/project")
        persistence.persist_suspended_state(
            ctx,
            suspend_kind="suspended",
            pending_tool_blocks=[],
            tool_result_blocks=[],
            messages=[],
            next_turn=0,
            disable_tools=False,
            readonly_only=False,
        )

        context = store.update_step_attempt.call_args[1]["context"]
        assert context["workspace_root"] == "/home/user/project"

    def test_stores_note_cursor_event_seq(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        snapshot: MagicMock,
    ) -> None:
        snapshot.create_envelope.return_value = _make_envelope({})
        store.get_step_attempt.return_value = FakeAttempt(context={})

        ctx = _make_attempt_ctx()
        persistence.persist_suspended_state(
            ctx,
            suspend_kind="suspended",
            pending_tool_blocks=[],
            tool_result_blocks=[],
            messages=[],
            next_turn=0,
            disable_tools=False,
            readonly_only=False,
            note_cursor_event_seq=42,
        )

        context = store.update_step_attempt.call_args[1]["context"]
        assert context["note_cursor_event_seq"] == 42

    def test_serializes_observation_ticket(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        snapshot: MagicMock,
    ) -> None:
        snapshot.create_envelope.return_value = _make_envelope({})
        store.get_step_attempt.return_value = FakeAttempt(context={})

        observation = MagicMock()
        observation.to_dict.return_value = {"observer_kind": "ci", "job_id": "j1"}

        ctx = _make_attempt_ctx()
        persistence.persist_suspended_state(
            ctx,
            suspend_kind="awaiting_observation",
            pending_tool_blocks=[],
            tool_result_blocks=[],
            messages=[],
            next_turn=1,
            disable_tools=False,
            readonly_only=False,
            observation=observation,
        )

        payload = snapshot.create_envelope.call_args[0][0]
        assert payload["observation"] == {"observer_kind": "ci", "job_id": "j1"}

    def test_handles_none_attempt(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        snapshot: MagicMock,
    ) -> None:
        snapshot.create_envelope.return_value = _make_envelope({})
        store.get_step_attempt.return_value = None

        ctx = _make_attempt_ctx()
        persistence.persist_suspended_state(
            ctx,
            suspend_kind="suspended",
            pending_tool_blocks=[],
            tool_result_blocks=[],
            messages=[],
            next_turn=0,
            disable_tools=False,
            readonly_only=False,
        )

        store.update_step_attempt.assert_called_once()
        context = store.update_step_attempt.call_args[1]["context"]
        assert _RUNTIME_SNAPSHOT_KEY in context

    def test_stores_resume_messages_via_artifact(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        snapshot: MagicMock,
        store_json_artifact: MagicMock,
    ) -> None:
        snapshot.create_envelope.return_value = _make_envelope({})
        store.get_step_attempt.return_value = FakeAttempt(context={})
        store_json_artifact.return_value = "artifact://messages/1"

        messages = [{"role": "user", "content": "hi"}]
        ctx = _make_attempt_ctx()
        persistence.persist_suspended_state(
            ctx,
            suspend_kind="suspended",
            pending_tool_blocks=[],
            tool_result_blocks=[],
            messages=messages,
            next_turn=0,
            disable_tools=False,
            readonly_only=False,
        )

        # First call is for resume messages, second is for snapshot artifact
        first_call = store_json_artifact.call_args_list[0]
        assert first_call[1]["kind"] == "runtime.resume_messages"
        assert first_call[1]["payload"] == messages


# ---------------------------------------------------------------------------
# persist_blocked_state
# ---------------------------------------------------------------------------


class TestPersistBlockedState:
    def test_delegates_to_persist_suspended_with_awaiting_approval(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        snapshot: MagicMock,
    ) -> None:
        snapshot.create_envelope.return_value = _make_envelope({})
        store.get_step_attempt.return_value = FakeAttempt(context={})

        ctx = _make_attempt_ctx()
        persistence.persist_blocked_state(
            ctx,
            pending_tool_blocks=[{"type": "tool_use"}],
            tool_result_blocks=[],
            messages=[],
            next_turn=2,
            disable_tools=True,
            readonly_only=False,
        )

        call_kwargs = store.update_step_attempt.call_args[1]
        assert call_kwargs["status"] == "awaiting_approval"


# ---------------------------------------------------------------------------
# load_suspended_state
# ---------------------------------------------------------------------------


class TestLoadSuspendedState:
    def test_raises_key_error_for_unknown_attempt(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = None
        with pytest.raises(KeyError):
            persistence.load_suspended_state("unknown_id")

    def test_returns_empty_dict_when_no_envelope(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        # Attempt has no resume_from_ref and no snapshot in context,
        # so _load_runtime_snapshot_envelope returns {} naturally.
        attempt = FakeAttempt(context={}, resume_from_ref=None)
        store.get_step_attempt.return_value = attempt

        result = persistence.load_suspended_state("attempt_1")
        assert result == {}

    def test_loads_payload_from_envelope(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        snapshot: MagicMock,
    ) -> None:
        payload = {
            "suspend_kind": "awaiting_observation",
            "resume_messages_ref": "artifact://msg/1",
            "pending_tool_blocks": [{"id": "t1"}],
            "tool_result_blocks": [],
            "next_turn": 5,
            "disable_tools": False,
            "readonly_only": True,
        }
        envelope = _make_envelope(payload)
        attempt = FakeAttempt(
            context={_RUNTIME_SNAPSHOT_KEY: envelope},
            resume_from_ref=None,
        )
        store.get_step_attempt.return_value = attempt
        snapshot.extract_payload.return_value = dict(payload)
        snapshot.load_resume_messages.return_value = [{"role": "user", "content": "msg"}]

        result = persistence.load_suspended_state("attempt_1")

        assert result["suspend_kind"] == "awaiting_observation"
        assert result["next_turn"] == 5
        assert result["messages"] == [{"role": "user", "content": "msg"}]

    def test_loads_payload_with_inline_messages(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        snapshot: MagicMock,
    ) -> None:
        payload_with_messages = {
            "suspend_kind": "suspended",
            "messages": [{"role": "assistant", "content": "inline"}],
            "pending_tool_blocks": [],
            "tool_result_blocks": [],
            "next_turn": 1,
        }
        envelope = _make_envelope(payload_with_messages)
        attempt = FakeAttempt(context={_RUNTIME_SNAPSHOT_KEY: envelope})
        store.get_step_attempt.return_value = attempt
        snapshot.extract_payload.return_value = dict(payload_with_messages)

        result = persistence.load_suspended_state("attempt_1")

        # When messages are already present, resume_messages_ref should not be loaded
        snapshot.load_resume_messages.assert_not_called()
        assert result["messages"] == [{"role": "assistant", "content": "inline"}]

    def test_loads_from_resume_from_ref_artifact(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        artifact_store: MagicMock,
        snapshot: MagicMock,
    ) -> None:
        payload = {
            "suspend_kind": "suspended",
            "resume_messages_ref": "",
            "pending_tool_blocks": [],
            "tool_result_blocks": [],
            "next_turn": 0,
        }
        envelope = _make_envelope(payload)
        fake_artifact = SimpleNamespace(uri="file:///tmp/snapshot.json")
        store.get_step_attempt.return_value = FakeAttempt(
            context={},
            resume_from_ref="artifact://snap/1",
        )
        store.get_artifact.return_value = fake_artifact
        artifact_store.read_text.return_value = json.dumps(envelope)
        snapshot.extract_payload.return_value = dict(payload)
        snapshot.load_resume_messages.return_value = []

        result = persistence.load_suspended_state("attempt_1")

        store.get_artifact.assert_called_with("artifact://snap/1")
        assert result["suspend_kind"] == "suspended"


# ---------------------------------------------------------------------------
# load_blocked_state
# ---------------------------------------------------------------------------


class TestLoadBlockedState:
    def test_delegates_to_load_suspended_state(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        snapshot: MagicMock,
    ) -> None:
        payload = {"suspend_kind": "awaiting_approval", "messages": []}
        envelope = _make_envelope(payload)
        attempt = FakeAttempt(context={_RUNTIME_SNAPSHOT_KEY: envelope})
        store.get_step_attempt.return_value = attempt
        snapshot.extract_payload.return_value = dict(payload)

        result = persistence.load_blocked_state("attempt_1")

        assert result["suspend_kind"] == "awaiting_approval"


# ---------------------------------------------------------------------------
# clear_suspended_state
# ---------------------------------------------------------------------------


class TestClearSuspendedState:
    def test_clears_snapshot_and_pending_keys(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = FakeAttempt(
            context={
                _RUNTIME_SNAPSHOT_KEY: {"some": "data"},
                _PENDING_EXECUTION_KEY: {"pending": "exec"},
                "other_key": "preserved",
            }
        )

        persistence.clear_suspended_state("attempt_1")

        call_kwargs = store.update_step_attempt.call_args[1]
        context = call_kwargs["context"]
        assert _RUNTIME_SNAPSHOT_KEY not in context
        assert _PENDING_EXECUTION_KEY not in context
        assert context["other_key"] == "preserved"
        assert call_kwargs["status_reason"] is None
        assert call_kwargs["resume_from_ref"] is None

    def test_noop_for_unknown_attempt(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = None
        persistence.clear_suspended_state("unknown_id")
        store.update_step_attempt.assert_not_called()


# ---------------------------------------------------------------------------
# clear_blocked_state
# ---------------------------------------------------------------------------


class TestClearBlockedState:
    def test_delegates_to_clear_suspended_state(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = FakeAttempt(context={_RUNTIME_SNAPSHOT_KEY: {}})

        persistence.clear_blocked_state("attempt_1")

        store.update_step_attempt.assert_called_once()
        context = store.update_step_attempt.call_args[1]["context"]
        assert _RUNTIME_SNAPSHOT_KEY not in context


# ---------------------------------------------------------------------------
# current_note_cursor
# ---------------------------------------------------------------------------


class TestCurrentNoteCursor:
    def test_returns_cursor_from_context(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = FakeAttempt(context={"note_cursor_event_seq": 15})
        assert persistence.current_note_cursor("attempt_1") == 15

    def test_returns_zero_for_unknown_attempt(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = None
        assert persistence.current_note_cursor("attempt_1") == 0

    def test_returns_zero_when_key_missing(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = FakeAttempt(context={})
        assert persistence.current_note_cursor("attempt_1") == 0


# ---------------------------------------------------------------------------
# consume_appended_notes
# ---------------------------------------------------------------------------


class TestConsumeAppendedNotes:
    def test_returns_empty_when_no_note_events(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = FakeAttempt(context={})
        store.list_events.return_value = []

        messages, cursor = persistence.consume_appended_notes(_make_attempt_ctx())

        assert messages == []
        assert cursor == 0

    def test_collects_note_events_into_messages(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = FakeAttempt(context={"note_cursor_event_seq": 0})
        store.list_events.return_value = [
            {
                "event_type": "task.note.appended",
                "event_seq": 5,
                "payload": {"prompt": "Please focus on tests"},
            },
            {
                "event_type": "task.note.appended",
                "event_seq": 8,
                "payload": {"prompt": "Also check coverage"},
            },
            {
                "event_type": "step.started",
                "event_seq": 6,
                "payload": {},
            },
        ]

        messages, cursor = persistence.consume_appended_notes(_make_attempt_ctx())

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert "Please focus on tests" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert "Also check coverage" in messages[1]["content"]
        assert cursor == 8

    def test_updates_note_cursor_in_context(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = FakeAttempt(context={})
        store.list_events.return_value = [
            {
                "event_type": "task.note.appended",
                "event_seq": 10,
                "payload": {"prompt": "note text"},
            },
        ]

        persistence.consume_appended_notes(_make_attempt_ctx())

        update_call = store.update_step_attempt.call_args
        assert update_call[1]["context"]["note_cursor_event_seq"] == 10

    def test_skips_empty_prompts(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = FakeAttempt(context={})
        store.list_events.return_value = [
            {
                "event_type": "task.note.appended",
                "event_seq": 3,
                "payload": {"prompt": ""},
            },
            {
                "event_type": "task.note.appended",
                "event_seq": 4,
                "payload": {"raw_text": "  "},
            },
        ]

        messages, cursor = persistence.consume_appended_notes(_make_attempt_ctx())

        assert messages == []
        assert cursor == 4

    def test_uses_raw_text_fallback(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = FakeAttempt(context={})
        store.list_events.return_value = [
            {
                "event_type": "task.note.appended",
                "event_seq": 7,
                "payload": {"raw_text": "Fallback text"},
            },
        ]

        messages, _cursor = persistence.consume_appended_notes(_make_attempt_ctx())

        assert len(messages) == 1
        assert "Fallback text" in messages[0]["content"]


# ---------------------------------------------------------------------------
# _load_runtime_snapshot_envelope
# ---------------------------------------------------------------------------


class TestLoadRuntimeSnapshotEnvelope:
    def test_loads_from_artifact_ref(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        artifact_store: MagicMock,
    ) -> None:
        envelope = _make_envelope({"suspend_kind": "suspended"})
        fake_artifact = SimpleNamespace(uri="file:///tmp/envelope.json")
        store.get_artifact.return_value = fake_artifact
        artifact_store.read_text.return_value = json.dumps(envelope)

        attempt = FakeAttempt(resume_from_ref="artifact://env/1", context={})
        result = persistence._load_runtime_snapshot_envelope(attempt)

        assert result == envelope

    def test_falls_back_to_context_snapshot(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        inline_snapshot = {"schema_version": 2, "kind": "runtime_snapshot", "payload": {}}
        attempt = FakeAttempt(
            resume_from_ref="",
            context={_RUNTIME_SNAPSHOT_KEY: inline_snapshot},
        )

        result = persistence._load_runtime_snapshot_envelope(attempt)

        assert result == inline_snapshot

    def test_returns_empty_dict_when_artifact_read_fails(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        artifact_store: MagicMock,
    ) -> None:
        fake_artifact = SimpleNamespace(uri="file:///tmp/bad.json")
        store.get_artifact.return_value = fake_artifact
        artifact_store.read_text.side_effect = OSError("read failed")

        attempt = FakeAttempt(resume_from_ref="artifact://bad/1", context={})
        result = persistence._load_runtime_snapshot_envelope(attempt)

        assert result == {}

    def test_returns_empty_dict_when_artifact_not_found(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_artifact.return_value = None

        attempt = FakeAttempt(resume_from_ref="artifact://missing/1", context={})
        result = persistence._load_runtime_snapshot_envelope(attempt)

        assert result == {}

    def test_returns_empty_dict_when_json_invalid(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        artifact_store: MagicMock,
    ) -> None:
        fake_artifact = SimpleNamespace(uri="file:///tmp/bad.json")
        store.get_artifact.return_value = fake_artifact
        artifact_store.read_text.return_value = "not json"

        attempt = FakeAttempt(resume_from_ref="artifact://bad/1", context={})
        result = persistence._load_runtime_snapshot_envelope(attempt)

        assert result == {}


# ---------------------------------------------------------------------------
# _store_pending_execution / _load_pending_execution / _clear_pending_execution
# ---------------------------------------------------------------------------


class TestPendingExecution:
    def test_store_pending_execution(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        store_json_artifact: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = FakeAttempt(context={})
        store_json_artifact.return_value = "artifact://pending/1"

        payload = {
            "tool_name": "bash",
            "decision_id": "dec_1",
            "capability_grant_id": "grant_1",
        }
        ctx = _make_attempt_ctx()
        persistence._store_pending_execution(ctx, payload)

        store.update_step_attempt.assert_called_once()
        call_kwargs = store.update_step_attempt.call_args[1]
        assert call_kwargs["context"][_PENDING_EXECUTION_KEY] == payload
        assert call_kwargs["pending_execution_ref"] == "artifact://pending/1"
        assert call_kwargs["decision_id"] == "dec_1"
        assert call_kwargs["capability_grant_id"] == "grant_1"

    def test_load_pending_execution_from_artifact(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        artifact_store: MagicMock,
    ) -> None:
        inner = {"tool_name": "bash", "args": {"cmd": "ls"}}
        artifact_data = {
            "schema": "runtime.pending_execution/v1",
            "payload": inner,
        }
        fake_artifact = SimpleNamespace(uri="file:///tmp/pending.json")
        attempt = FakeAttempt(context={})
        attempt.pending_execution_ref = "artifact://pending/1"
        store.get_step_attempt.return_value = attempt
        store.get_artifact.return_value = fake_artifact
        artifact_store.read_text.return_value = json.dumps(artifact_data)

        result = persistence._load_pending_execution("attempt_1")

        assert result["tool_name"] == "bash"
        assert result["args"] == {"cmd": "ls"}

    def test_load_pending_execution_falls_back_to_context(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        inline_payload = {"tool_name": "read_file"}
        attempt = FakeAttempt(
            context={_PENDING_EXECUTION_KEY: inline_payload},
        )
        attempt.pending_execution_ref = ""
        store.get_step_attempt.return_value = attempt

        result = persistence._load_pending_execution("attempt_1")

        assert result["tool_name"] == "read_file"

    def test_load_pending_execution_returns_empty_for_unknown(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = None
        result = persistence._load_pending_execution("unknown_id")
        assert result == {}

    def test_clear_pending_execution(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = FakeAttempt(
            context={_PENDING_EXECUTION_KEY: {"tool": "bash"}, "keep": True}
        )

        persistence._clear_pending_execution("attempt_1")

        call_kwargs = store.update_step_attempt.call_args[1]
        assert _PENDING_EXECUTION_KEY not in call_kwargs["context"]
        assert call_kwargs["context"]["keep"] is True
        assert call_kwargs["pending_execution_ref"] is None

    def test_clear_pending_execution_noop_for_unknown(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_step_attempt.return_value = None
        persistence._clear_pending_execution("unknown_id")
        store.update_step_attempt.assert_not_called()


# ---------------------------------------------------------------------------
# _load_json_artifact_payload
# ---------------------------------------------------------------------------


class TestLoadJsonArtifactPayload:
    def test_loads_plain_dict(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        artifact_store: MagicMock,
    ) -> None:
        data = {"key": "value"}
        fake_artifact = SimpleNamespace(uri="file:///tmp/plain.json")
        store.get_artifact.return_value = fake_artifact
        artifact_store.read_text.return_value = json.dumps(data)

        result = persistence._load_json_artifact_payload("artifact://plain/1")

        assert result == {"key": "value"}

    def test_unwraps_pending_execution_schema(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        artifact_store: MagicMock,
    ) -> None:
        data = {
            "schema": "runtime.pending_execution/v1",
            "payload": {"tool_name": "write_file"},
        }
        fake_artifact = SimpleNamespace(uri="file:///tmp/pe.json")
        store.get_artifact.return_value = fake_artifact
        artifact_store.read_text.return_value = json.dumps(data)

        result = persistence._load_json_artifact_payload("artifact://pe/1")

        assert result == {"tool_name": "write_file"}

    def test_returns_empty_for_missing_artifact(
        self,
        persistence: StatePersistence,
        store: MagicMock,
    ) -> None:
        store.get_artifact.return_value = None
        assert persistence._load_json_artifact_payload("artifact://missing") == {}

    def test_returns_empty_on_json_error(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        artifact_store: MagicMock,
    ) -> None:
        fake_artifact = SimpleNamespace(uri="file:///tmp/bad.json")
        store.get_artifact.return_value = fake_artifact
        artifact_store.read_text.return_value = "{{invalid"

        assert persistence._load_json_artifact_payload("artifact://bad") == {}

    def test_returns_empty_on_os_error(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        artifact_store: MagicMock,
    ) -> None:
        fake_artifact = SimpleNamespace(uri="file:///tmp/gone.json")
        store.get_artifact.return_value = fake_artifact
        artifact_store.read_text.side_effect = OSError("gone")

        assert persistence._load_json_artifact_payload("artifact://gone") == {}

    def test_returns_empty_for_non_dict_payload(
        self,
        persistence: StatePersistence,
        store: MagicMock,
        artifact_store: MagicMock,
    ) -> None:
        fake_artifact = SimpleNamespace(uri="file:///tmp/list.json")
        store.get_artifact.return_value = fake_artifact
        artifact_store.read_text.return_value = json.dumps([1, 2, 3])

        assert persistence._load_json_artifact_payload("artifact://list") == {}
