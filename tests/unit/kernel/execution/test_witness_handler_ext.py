"""Tests for hermit.kernel.execution.executor.witness_handler."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.witness_handler import WitnessHandler
from hermit.kernel.policy.models.models import ActionRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attempt_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults: dict[str, Any] = {
        "conversation_id": "conv-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "source_channel": "cli",
        "workspace_root": "/tmp/workspace",
    }
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


def _make_action_request(**overrides: Any) -> ActionRequest:
    defaults: dict[str, Any] = {
        "request_id": "req-1",
        "tool_name": "write_file",
        "action_class": "write_local",
        "derived": {},
    }
    defaults.update(overrides)
    return ActionRequest(**defaults)


def _make_handler() -> tuple[WitnessHandler, MagicMock, MagicMock, MagicMock]:
    store = MagicMock()
    artifact_store = MagicMock()
    witness = MagicMock()
    handler = WitnessHandler(store=store, artifact_store=artifact_store, witness=witness)
    return handler, store, artifact_store, witness


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCaptureStateWitness:
    def test_delegates_to_witness(self) -> None:
        handler, _, _, witness = _make_handler()
        witness.capture.return_value = "witness-ref-1"
        action = _make_action_request()
        ctx = _make_attempt_ctx()
        store_artifact = MagicMock()
        ref = handler.capture_state_witness(action, ctx, store_artifact=store_artifact)
        assert ref == "witness-ref-1"
        witness.capture.assert_called_once_with(action, ctx, store_artifact=store_artifact)


class TestStateWitnessPayload:
    def test_delegates_to_witness(self) -> None:
        handler, _, _, witness = _make_handler()
        witness.payload.return_value = {"action_class": "write_local"}
        action = _make_action_request()
        ctx = _make_attempt_ctx()
        result = handler.state_witness_payload(action, ctx)
        assert result["action_class"] == "write_local"
        witness.payload.assert_called_once()


class TestPathWitness:
    def test_delegates_to_witness(self) -> None:
        handler, _, _, witness = _make_handler()
        witness.path_witness.return_value = {"path": "/tmp/file.txt", "exists": True}
        result = handler.path_witness("/tmp/file.txt", workspace_root=Path("/tmp"))
        assert result["exists"] is True
        witness.path_witness.assert_called_once()


class TestGitWitness:
    def test_delegates_to_witness(self) -> None:
        handler, _, _, witness = _make_handler()
        witness.git_witness.return_value = {"branch": "main"}
        result = handler.git_witness(Path("/workspace"))
        assert result["branch"] == "main"
        witness.git_witness.assert_called_once()


class TestValidateStateWitness:
    def test_delegates_to_witness(self) -> None:
        handler, _, _, witness = _make_handler()
        witness.validate.return_value = True
        action = _make_action_request()
        ctx = _make_attempt_ctx()
        result = handler.validate_state_witness("wit-1", action, ctx)
        assert result is True
        witness.validate.assert_called_once()


class TestLoadWitnessPayload:
    def test_empty_ref_returns_empty(self) -> None:
        handler, _, _, _ = _make_handler()
        assert handler.load_witness_payload(None) == {}
        assert handler.load_witness_payload("") == {}

    def test_missing_artifact_returns_empty(self) -> None:
        handler, store, _, _ = _make_handler()
        store.get_artifact.return_value = None
        assert handler.load_witness_payload("ref-missing") == {}

    def test_valid_artifact_loaded(self) -> None:
        handler, store, artifact_store, _ = _make_handler()
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        store.get_artifact.return_value = artifact
        artifact_store.read_text.return_value = json.dumps({"action_class": "write_local"})
        result = handler.load_witness_payload("ref-1")
        assert result["action_class"] == "write_local"

    def test_invalid_json_returns_empty(self) -> None:
        handler, store, artifact_store, _ = _make_handler()
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        store.get_artifact.return_value = artifact
        artifact_store.read_text.return_value = "not json"
        assert handler.load_witness_payload("ref-1") == {}

    def test_os_error_returns_empty(self) -> None:
        handler, store, artifact_store, _ = _make_handler()
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        store.get_artifact.return_value = artifact
        artifact_store.read_text.side_effect = OSError("fail")
        assert handler.load_witness_payload("ref-1") == {}

    def test_non_dict_json_returns_empty(self) -> None:
        handler, store, artifact_store, _ = _make_handler()
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        store.get_artifact.return_value = artifact
        artifact_store.read_text.return_value = json.dumps([1, 2, 3])
        assert handler.load_witness_payload("ref-1") == {}
