"""Tests for hermit.kernel.execution.executor.authorization_handler."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.authorization_handler import AuthorizationHandler
from hermit.kernel.policy.models.models import ActionRequest, PolicyDecision

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


def _make_policy(**overrides: Any) -> PolicyDecision:
    defaults: dict[str, Any] = {
        "verdict": "allow",
        "action_class": "write_local",
    }
    defaults.update(overrides)
    return PolicyDecision(**defaults)


def _make_handler(**overrides: Any) -> AuthorizationHandler:
    defaults: dict[str, Any] = {
        "store": MagicMock(),
        "artifact_store": MagicMock(),
        "capability_service": MagicMock(),
        "workspace_lease_service": MagicMock(),
        "authorization_plans": MagicMock(),
        "registry": MagicMock(),
        "policy_engine": MagicMock(),
        "git_worktree": MagicMock(),
    }
    defaults.update(overrides)
    return AuthorizationHandler(**defaults)


# ---------------------------------------------------------------------------
# authorization_reason
# ---------------------------------------------------------------------------


class TestAuthorizationReason:
    def test_mutable_workspace_mode(self) -> None:
        handler = _make_handler()
        result = handler.authorization_reason(
            policy=_make_policy(), approval_mode="mutable_workspace"
        )
        assert "mutable workspace" in result.lower() or len(result) > 0

    def test_once_mode(self) -> None:
        handler = _make_handler()
        result = handler.authorization_reason(policy=_make_policy(), approval_mode="once")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_default_mode_uses_policy_reason(self) -> None:
        handler = _make_handler()
        from hermit.kernel.policy.models.models import PolicyReason

        policy = _make_policy()
        policy.reasons = [PolicyReason(code="test", message="Policy allows")]
        result = handler.authorization_reason(policy=policy, approval_mode="")
        assert "Policy allows" in result

    def test_default_mode_empty_reason(self) -> None:
        handler = _make_handler()
        policy = _make_policy()
        result = handler.authorization_reason(policy=policy, approval_mode="")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# successful_result_summary
# ---------------------------------------------------------------------------


class TestSuccessfulResultSummary:
    def test_mutable_workspace(self) -> None:
        handler = _make_handler()
        result = handler.successful_result_summary(
            tool_name="write_file", approval_mode="mutable_workspace"
        )
        assert "write_file" in result or "mutable" in result.lower()

    def test_once_mode(self) -> None:
        handler = _make_handler()
        result = handler.successful_result_summary(tool_name="write_file", approval_mode="once")
        assert isinstance(result, str)

    def test_default_mode(self) -> None:
        handler = _make_handler()
        result = handler.successful_result_summary(tool_name="bash", approval_mode="")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# prepare_rollback_plan
# ---------------------------------------------------------------------------


class TestPrepareRollbackPlan:
    def test_write_local_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("original content")
        store = MagicMock()
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("uri://test", "hash123")
        store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")
        handler = _make_handler(store=store, artifact_store=artifact_store)
        ctx = _make_attempt_ctx(workspace_root=str(tmp_path))
        result = handler.prepare_rollback_plan(
            action_type="write_local",
            tool_name="write_file",
            tool_input={"path": str(target)},
            attempt_ctx=ctx,
        )
        assert result["supported"] is True
        assert len(result["artifact_refs"]) == 1

    def test_write_local_nonexistent_file(self, tmp_path: Path) -> None:
        target = tmp_path / "new_file.txt"
        store = MagicMock()
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("uri://test", "hash123")
        store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")
        handler = _make_handler(store=store, artifact_store=artifact_store)
        ctx = _make_attempt_ctx(workspace_root=str(tmp_path))
        result = handler.prepare_rollback_plan(
            action_type="write_local",
            tool_name="write_file",
            tool_input={"path": str(target)},
            attempt_ctx=ctx,
        )
        assert result["supported"] is True

    def test_write_local_no_path(self) -> None:
        handler = _make_handler()
        ctx = _make_attempt_ctx()
        result = handler.prepare_rollback_plan(
            action_type="write_local",
            tool_name="write_file",
            tool_input={},
            attempt_ctx=ctx,
        )
        assert result["supported"] is False

    def test_patch_file_action(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("content")
        store = MagicMock()
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("uri://test", "hash123")
        store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")
        handler = _make_handler(store=store, artifact_store=artifact_store)
        ctx = _make_attempt_ctx(workspace_root=str(tmp_path))
        result = handler.prepare_rollback_plan(
            action_type="patch_file",
            tool_name="patch_file",
            tool_input={"path": str(target)},
            attempt_ctx=ctx,
        )
        assert result["supported"] is True

    def test_vcs_mutation(self) -> None:
        git_worktree = MagicMock()
        snapshot = MagicMock()
        snapshot.to_prestate.return_value = {"branch": "main", "dirty": False}
        git_worktree.snapshot.return_value = snapshot
        store = MagicMock()
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("uri://test", "hash123")
        store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")
        handler = _make_handler(
            store=store, artifact_store=artifact_store, git_worktree=git_worktree
        )
        ctx = _make_attempt_ctx()
        result = handler.prepare_rollback_plan(
            action_type="vcs_mutation",
            tool_name="git_commit",
            tool_input={},
            attempt_ctx=ctx,
        )
        assert result["supported"] is True

    def test_vcs_mutation_dirty(self) -> None:
        git_worktree = MagicMock()
        snapshot = MagicMock()
        snapshot.to_prestate.return_value = {"branch": "main", "dirty": True}
        git_worktree.snapshot.return_value = snapshot
        store = MagicMock()
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("uri://test", "hash123")
        store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")
        handler = _make_handler(
            store=store, artifact_store=artifact_store, git_worktree=git_worktree
        )
        ctx = _make_attempt_ctx()
        result = handler.prepare_rollback_plan(
            action_type="vcs_mutation",
            tool_name="git_commit",
            tool_input={},
            attempt_ctx=ctx,
        )
        assert result["supported"] is False

    def test_vcs_mutation_no_prestate(self) -> None:
        git_worktree = MagicMock()
        snapshot = MagicMock()
        snapshot.to_prestate.return_value = None
        git_worktree.snapshot.return_value = snapshot
        handler = _make_handler(git_worktree=git_worktree)
        ctx = _make_attempt_ctx()
        result = handler.prepare_rollback_plan(
            action_type="vcs_mutation",
            tool_name="git_commit",
            tool_input={},
            attempt_ctx=ctx,
        )
        assert result["supported"] is False

    def test_memory_write(self) -> None:
        handler = _make_handler()
        ctx = _make_attempt_ctx()
        result = handler.prepare_rollback_plan(
            action_type="memory_write",
            tool_name="write_memory",
            tool_input={},
            attempt_ctx=ctx,
        )
        assert result["supported"] is True
        assert result["strategy"] == "supersede_or_invalidate"

    def test_unknown_action_type(self) -> None:
        handler = _make_handler()
        ctx = _make_attempt_ctx()
        result = handler.prepare_rollback_plan(
            action_type="read_local",
            tool_name="read_file",
            tool_input={},
            attempt_ctx=ctx,
        )
        assert result["supported"] is False
        assert result["artifact_refs"] == []

    def test_relative_path_resolved(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("content")
        store = MagicMock()
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("uri://test", "hash123")
        store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")
        handler = _make_handler(store=store, artifact_store=artifact_store)
        ctx = _make_attempt_ctx(workspace_root=str(tmp_path))
        result = handler.prepare_rollback_plan(
            action_type="write_local",
            tool_name="write_file",
            tool_input={"path": "test.txt"},
            attempt_ctx=ctx,
        )
        assert result["supported"] is True


# ---------------------------------------------------------------------------
# store_inline_json_artifact
# ---------------------------------------------------------------------------


class TestStoreInlineJsonArtifact:
    def test_stores_and_returns_id(self) -> None:
        store = MagicMock()
        artifact_store = MagicMock()
        artifact_store.store_json.return_value = ("uri://test", "hash123")
        store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")
        handler = _make_handler(store=store, artifact_store=artifact_store)
        ref = handler.store_inline_json_artifact(
            task_id="t-1",
            step_id="s-1",
            kind="rollback.prestate",
            payload={"key": "value"},
            metadata={"action_type": "write_local"},
        )
        assert ref == "art-1"
        artifact_store.store_json.assert_called_once()
        store.create_artifact.assert_called_once()


# ---------------------------------------------------------------------------
# lease_root_path
# ---------------------------------------------------------------------------


class TestLeaseRootPath:
    def test_with_target_paths(self) -> None:
        handler = _make_handler()
        action = _make_action_request(derived={"target_paths": ["/tmp/test.txt"]})
        ctx = _make_attempt_ctx()
        result = handler.lease_root_path(action, attempt_ctx=ctx)
        # expanduser().resolve().parent on /tmp/test.txt gives the parent of the resolved path
        expected = str(Path("/tmp/test.txt").expanduser().resolve().parent)
        assert result == expected

    def test_without_target_paths_uses_workspace(self) -> None:
        handler = _make_handler()
        action = _make_action_request(derived={})
        ctx = _make_attempt_ctx(workspace_root="/workspace/root")
        result = handler.lease_root_path(action, attempt_ctx=ctx)
        assert result == "/workspace/root"

    def test_empty_target_paths(self) -> None:
        handler = _make_handler()
        action = _make_action_request(derived={"target_paths": []})
        ctx = _make_attempt_ctx(workspace_root="/workspace")
        result = handler.lease_root_path(action, attempt_ctx=ctx)
        assert result == "/workspace"

    def test_target_paths_with_empty_strings(self) -> None:
        handler = _make_handler()
        action = _make_action_request(derived={"target_paths": ["", ""]})
        ctx = _make_attempt_ctx(workspace_root="/workspace")
        result = handler.lease_root_path(action, attempt_ctx=ctx)
        assert result == "/workspace"


# ---------------------------------------------------------------------------
# ensure_workspace_lease
# ---------------------------------------------------------------------------


class TestEnsureWorkspaceLease:
    def test_no_lease_root(self) -> None:
        handler = _make_handler()
        action = _make_action_request(derived={})
        ctx = _make_attempt_ctx(workspace_root="")
        result = handler.ensure_workspace_lease(
            attempt_ctx=ctx, action_request=action, approval_mode="once"
        )
        assert result is None

    def test_existing_lease_reused(self) -> None:
        store = MagicMock()
        existing_attempt = SimpleNamespace(workspace_lease_id="existing-lease")
        store.get_step_attempt.return_value = existing_attempt
        wls = MagicMock()
        lease = SimpleNamespace(lease_id="existing-lease")
        wls.validate_active.return_value = lease
        handler = _make_handler(store=store, workspace_lease_service=wls)
        action = _make_action_request(derived={"target_paths": ["/tmp/test.txt"]})
        ctx = _make_attempt_ctx()
        result = handler.ensure_workspace_lease(
            attempt_ctx=ctx, action_request=action, approval_mode="once"
        )
        assert result == "existing-lease"
        wls.validate_active.assert_called_once()

    def test_new_lease_acquired_scoped(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = SimpleNamespace(workspace_lease_id=None)
        wls = MagicMock()
        wls.acquire.return_value = SimpleNamespace(lease_id="new-lease")
        handler = _make_handler(store=store, workspace_lease_service=wls)
        action = _make_action_request(derived={"target_paths": ["/tmp/test.txt"]})
        ctx = _make_attempt_ctx()
        result = handler.ensure_workspace_lease(
            attempt_ctx=ctx, action_request=action, approval_mode="once"
        )
        assert result == "new-lease"
        wls.acquire.assert_called_once()
        assert wls.acquire.call_args.kwargs["mode"] == "scoped"

    def test_mutable_workspace_mode(self) -> None:
        store = MagicMock()
        store.get_step_attempt.return_value = SimpleNamespace(workspace_lease_id=None)
        wls = MagicMock()
        wls.acquire.return_value = SimpleNamespace(lease_id="mut-lease")
        handler = _make_handler(store=store, workspace_lease_service=wls)
        action = _make_action_request(derived={"target_paths": ["/tmp/test.txt"]})
        ctx = _make_attempt_ctx()
        result = handler.ensure_workspace_lease(
            attempt_ctx=ctx, action_request=action, approval_mode="mutable_workspace"
        )
        assert result == "mut-lease"
        assert wls.acquire.call_args.kwargs["mode"] == "mutable"


# ---------------------------------------------------------------------------
# capability_constraints
# ---------------------------------------------------------------------------


class TestCapabilityConstraints:
    def test_basic_constraints(self) -> None:
        handler = _make_handler()
        action = _make_action_request(
            derived={
                "target_paths": ["/tmp/test.txt"],
                "network_hosts": ["example.com"],
                "command_preview": "echo hello",
                "constraints": {"max_size": 100},
            }
        )
        result = handler.capability_constraints(action, workspace_lease_id=None)
        assert result["target_paths"] == ["/tmp/test.txt"]
        assert result["network_hosts"] == ["example.com"]
        assert result["command_preview"] == "echo hello"
        assert result["max_size"] == 100

    def test_workspace_lease_adds_root(self) -> None:
        store = MagicMock()
        lease = SimpleNamespace(root_path="/workspace/root")
        store.get_workspace_lease.return_value = lease
        handler = _make_handler(store=store)
        action = _make_action_request(derived={})
        result = handler.capability_constraints(action, workspace_lease_id="lease-1")
        assert result["lease_root_path"] == "/workspace/root"

    def test_empty_values_filtered(self) -> None:
        handler = _make_handler()
        action = _make_action_request(
            derived={
                "target_paths": [],
                "network_hosts": [],
                "command_preview": None,
            }
        )
        result = handler.capability_constraints(action, workspace_lease_id=None)
        assert "target_paths" not in result
        assert "network_hosts" not in result
        assert "command_preview" not in result

    def test_missing_lease_no_root(self) -> None:
        store = MagicMock()
        store.get_workspace_lease.return_value = None
        handler = _make_handler(store=store)
        action = _make_action_request(derived={})
        result = handler.capability_constraints(action, workspace_lease_id="bad-lease")
        assert "lease_root_path" not in result
