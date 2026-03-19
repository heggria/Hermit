from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import structlog

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.suspension.git_worktree import GitWorktreeInspector
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.models.models import ActionRequest

log = structlog.get_logger()


class WitnessCapture:
    """State witness capture and validation for governed tool execution."""

    def __init__(
        self,
        *,
        store: KernelStore,
        artifact_store: ArtifactStore,
        git_worktree: GitWorktreeInspector,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.git_worktree = git_worktree

    def capture(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
        *,
        store_artifact: Any,
    ) -> str:
        payload = self.payload(action_request, attempt_ctx)
        witness_ref = store_artifact(
            payload=payload,
            kind="state.witness",
            attempt_ctx=attempt_ctx,
            metadata={"tool_name": action_request.tool_name},
            event_type="witness.captured",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            payload_summary={
                "tool_name": action_request.tool_name,
                "action_class": action_request.action_class,
            },
        )
        return witness_ref

    def payload(
        self,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> dict[str, Any]:
        workspace_root = Path(attempt_ctx.workspace_root or ".").resolve()
        target_paths = list(action_request.derived.get("target_paths", []))
        files = [self.path_witness(path, workspace_root=workspace_root) for path in target_paths]
        return {
            "action_class": action_request.action_class,
            "tool_name": action_request.tool_name,
            "resource_scopes": list(action_request.resource_scopes),
            "cwd": str(workspace_root),
            "git": self.git_witness(workspace_root),
            "files": files,
            "network_hosts": list(action_request.derived.get("network_hosts", [])),
            "command_preview": action_request.derived.get("command_preview"),
        }

    def path_witness(self, path: str, *, workspace_root: Path) -> dict[str, Any]:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = (workspace_root / candidate).resolve()
        result: dict[str, Any] = {"path": str(candidate)}
        try:
            exists = candidate.exists()
        except OSError as exc:
            return {"path": str(candidate), "error": str(exc), "exists": False}
        result["exists"] = exists
        if not exists:
            return result
        try:
            stat = candidate.stat()
            result["mtime_ns"] = stat.st_mtime_ns
            result["size"] = stat.st_size
            if candidate.is_file():
                result["sha256"] = hashlib.sha256(candidate.read_bytes()).hexdigest()
            else:
                result["kind"] = "directory"
        except OSError as exc:
            result["error"] = str(exc)
        return result

    def git_witness(self, workspace_root: Path) -> dict[str, Any]:
        return self.git_worktree.snapshot(workspace_root).to_witness()

    def validate(
        self,
        witness_ref: str,
        action_request: ActionRequest,
        attempt_ctx: TaskExecutionContext,
    ) -> bool:
        artifact = self.store.get_artifact(witness_ref)
        if artifact is None:
            return False
        try:
            stored = json.loads(self.artifact_store.read_text(artifact.uri))
        except (OSError, json.JSONDecodeError):
            log.warning(
                "witness_validation_read_failed",
                witness_ref=witness_ref,
                step_attempt_id=attempt_ctx.step_attempt_id,
            )
            return False
        current = self.payload(action_request, attempt_ctx)
        valid = stored == current
        self.store.append_event(
            event_type="witness.validated" if valid else "witness.failed",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "state_witness_ref": witness_ref,
                "tool_name": action_request.tool_name,
            },
        )
        return valid
