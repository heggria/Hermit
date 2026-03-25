"""Extended coverage tests for hermit.kernel.execution.executor.witness.WitnessCapture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.witness import WitnessCapture
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


def _make_witness() -> tuple[WitnessCapture, MagicMock, MagicMock, MagicMock]:
    store = MagicMock()
    artifact_store = MagicMock()
    git_worktree = MagicMock()
    git_worktree.snapshot.return_value = MagicMock(
        to_witness=MagicMock(return_value={"branch": "main", "sha": "abc123"})
    )
    witness = WitnessCapture(store=store, artifact_store=artifact_store, git_worktree=git_worktree)
    return witness, store, artifact_store, git_worktree


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


class TestCapture:
    def test_calls_store_artifact(self) -> None:
        witness, _, _, _ = _make_witness()
        store_artifact = MagicMock(return_value="witness-ref-1")
        action = _make_action_request()
        ctx = _make_attempt_ctx()
        ref = witness.capture(action, ctx, store_artifact=store_artifact)
        assert ref == "witness-ref-1"
        store_artifact.assert_called_once()
        call_kwargs = store_artifact.call_args.kwargs
        assert call_kwargs["kind"] == "state.witness"
        assert call_kwargs["event_type"] == "witness.captured"

    def test_payload_included_in_artifact(self) -> None:
        witness, _, _, _ = _make_witness()
        store_artifact = MagicMock(return_value="wit-1")
        action = _make_action_request(
            derived={"target_paths": ["/tmp/test.txt"], "network_hosts": ["example.com"]}
        )
        ctx = _make_attempt_ctx()
        witness.capture(action, ctx, store_artifact=store_artifact)
        call_kwargs = store_artifact.call_args.kwargs
        payload = call_kwargs["payload"]
        assert payload["tool_name"] == "write_file"
        assert "files" in payload


# ---------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------


class TestPayload:
    def test_basic_payload(self) -> None:
        witness, _, _, _ = _make_witness()
        action = _make_action_request()
        ctx = _make_attempt_ctx(workspace_root="/tmp/workspace")
        payload = witness.payload(action, ctx)
        assert payload["action_class"] == "write_local"
        assert payload["tool_name"] == "write_file"
        assert payload["git"]["branch"] == "main"

    def test_payload_with_target_paths(self) -> None:
        witness, _, _, _ = _make_witness()
        action = _make_action_request(derived={"target_paths": ["/tmp/test.txt"]})
        ctx = _make_attempt_ctx()
        payload = witness.payload(action, ctx)
        assert len(payload["files"]) == 1

    def test_payload_with_network_hosts(self) -> None:
        witness, _, _, _ = _make_witness()
        action = _make_action_request(
            derived={"network_hosts": ["example.com"], "command_preview": "curl example.com"}
        )
        ctx = _make_attempt_ctx()
        payload = witness.payload(action, ctx)
        assert payload["network_hosts"] == ["example.com"]
        assert payload["command_preview"] == "curl example.com"


# ---------------------------------------------------------------------------
# path_witness
# ---------------------------------------------------------------------------


class TestPathWitness:
    def test_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("hello")
        witness, _, _, _ = _make_witness()
        result = witness.path_witness(str(target), workspace_root=tmp_path)
        assert result["exists"] is True
        assert result["size"] == 5
        assert "sha256" in result
        expected = hashlib.sha256(b"hello").hexdigest()
        assert result["sha256"] == expected

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        witness, _, _, _ = _make_witness()
        result = witness.path_witness(str(tmp_path / "missing.txt"), workspace_root=tmp_path)
        assert result["exists"] is False

    def test_directory(self, tmp_path: Path) -> None:
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        witness, _, _, _ = _make_witness()
        result = witness.path_witness(str(subdir), workspace_root=tmp_path)
        assert result["exists"] is True
        assert result.get("kind") == "directory"

    def test_relative_path(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("data")
        witness, _, _, _ = _make_witness()
        result = witness.path_witness("test.txt", workspace_root=tmp_path)
        assert result["exists"] is True


# ---------------------------------------------------------------------------
# git_witness
# ---------------------------------------------------------------------------


class TestGitWitness:
    def test_delegates_to_git_worktree(self) -> None:
        witness, _, _, git_worktree = _make_witness()
        result = witness.git_witness(Path("/workspace"))
        assert result["branch"] == "main"
        git_worktree.snapshot.assert_called_once()


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_missing_artifact_returns_false(self) -> None:
        witness, store, _, _ = _make_witness()
        store.get_artifact.return_value = None
        action = _make_action_request()
        ctx = _make_attempt_ctx()
        assert witness.validate("ref-1", action, ctx) is False

    def test_matching_state_returns_true(self) -> None:
        witness, store, artifact_store, _ = _make_witness()
        action = _make_action_request()
        ctx = _make_attempt_ctx()
        # Get the current payload
        current_payload = witness.payload(action, ctx)
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        store.get_artifact.return_value = artifact
        artifact_store.read_text.return_value = json.dumps(current_payload)
        assert witness.validate("ref-1", action, ctx) is True
        # Should emit witness.validated event
        event_calls = [
            c
            for c in store.append_event.call_args_list
            if c.kwargs.get("event_type") == "witness.validated"
        ]
        assert len(event_calls) == 1

    def test_mismatched_state_returns_false(self) -> None:
        witness, store, artifact_store, _ = _make_witness()
        action = _make_action_request()
        ctx = _make_attempt_ctx()
        stored_payload = {"action_class": "different_class", "tool_name": "other"}
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        store.get_artifact.return_value = artifact
        artifact_store.read_text.return_value = json.dumps(stored_payload)
        assert witness.validate("ref-1", action, ctx) is False
        event_calls = [
            c
            for c in store.append_event.call_args_list
            if c.kwargs.get("event_type") == "witness.failed"
        ]
        assert len(event_calls) == 1

    def test_invalid_json_returns_false(self) -> None:
        witness, store, artifact_store, _ = _make_witness()
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        store.get_artifact.return_value = artifact
        artifact_store.read_text.return_value = "not json"
        action = _make_action_request()
        ctx = _make_attempt_ctx()
        assert witness.validate("ref-1", action, ctx) is False

    def test_os_error_returns_false(self) -> None:
        witness, store, artifact_store, _ = _make_witness()
        artifact = SimpleNamespace(uri="file:///tmp/witness.json")
        store.get_artifact.return_value = artifact
        artifact_store.read_text.side_effect = OSError("fail")
        action = _make_action_request()
        ctx = _make_attempt_ctx()
        assert witness.validate("ref-1", action, ctx) is False
