# ruff: noqa: F401
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.authority.grants import CapabilityGrantError
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.controller.contracts import known_action_classes
from hermit.kernel.execution.coordination.dispatch import KernelDispatchService
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.execution.suspension.git_worktree import (
    GitWorktreeInspector,
    GitWorktreeSnapshot,
)
from hermit.kernel.ledger.journal.store import KernelSchemaError, KernelStore
from hermit.kernel.policy import PolicyEngine
from hermit.kernel.policy.approvals.approval_copy import ApprovalCopyService
from hermit.kernel.policy.approvals.approvals import ApprovalService
from hermit.kernel.policy.models.models import ActionRequest
from hermit.kernel.task.projections.progress_summary import ProgressSummary
from hermit.kernel.task.projections.projections import ProjectionService
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.task.services.topics import build_task_topic
from hermit.kernel.verification.receipts.receipts import ReceiptService
from hermit.kernel.verification.rollbacks.rollbacks import RollbackService
from hermit.plugins.builtin.hooks.scheduler.models import JobExecutionRecord, ScheduledJob
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine
from hermit.runtime.capability.contracts.skills import SkillDefinition
from hermit.runtime.capability.registry.manager import PluginManager
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec
from hermit.runtime.control.lifecycle.session import Session, SessionManager
from hermit.runtime.control.runner.runner import AgentRunner
from hermit.runtime.provider_host.execution.runtime import AgentResult, AgentRuntime
from hermit.runtime.provider_host.shared.contracts import (
    ProviderFeatures,
    ProviderRequest,
    ProviderResponse,
    UsageMetrics,
)


class FakeProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.name = "fake"
        self.features = ProviderFeatures(supports_tool_calling=True)
        self._responses = list(responses)
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return self._responses.pop(0)

    def stream(self, request: ProviderRequest):
        raise NotImplementedError

    def clone(self, *, model: str | None = None, system_prompt: str | None = None) -> FakeProvider:
        return self


class _FakeProgressSummarizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def summarize(self, *, facts: dict[str, Any]) -> ProgressSummary | None:
        self.calls.append(facts)
        progress = dict(facts.get("progress", {}) or {})
        phase = str(progress.get("phase", "") or "running")
        percent = progress.get("progress_percent")
        summary = str(progress.get("summary", "") or "").strip() or "Still working"
        if phase == "ready":
            return ProgressSummary(
                summary=f"{summary}，现在可以继续后续步骤了。",
                detail="下一步会恢复同一个 task 并继续推理。",
                phase=phase,
                progress_percent=percent if isinstance(percent, int) else None,
            )
        return ProgressSummary(
            summary=f"{summary}，正在收敛上下文。",
            detail="还没有看到明确阻塞。",
            phase=phase,
            progress_percent=percent if isinstance(percent, int) else None,
        )


class FakeGitWorktree(GitWorktreeInspector):
    def __init__(self, snapshots: list[dict[str, Any]] | None = None) -> None:
        self._snapshots = [dict(snapshot) for snapshot in (snapshots or [])]
        self._last_snapshot = dict(self._snapshots[-1]) if self._snapshots else {"present": False}
        self.snapshot_calls: list[str] = []
        self.reset_calls: list[dict[str, str]] = []

    def snapshot(self, workspace_root: Path) -> GitWorktreeSnapshot:
        root = workspace_root.resolve()
        self.snapshot_calls.append(str(root))
        if self._snapshots:
            payload = self._snapshots.pop(0)
            self._last_snapshot = dict(payload)
        else:
            payload = dict(self._last_snapshot)
        return GitWorktreeSnapshot(
            repo_path=str(root),
            present=bool(payload.get("present", False)),
            head=str(payload.get("head", "") or ""),
            dirty=bool(payload.get("dirty", False)),
            error=str(payload.get("error", "") or "") or None,
        )

    def hard_reset(self, workspace_root: Path, head: str) -> None:
        self.reset_calls.append({"repo_path": str(workspace_root.resolve()), "head": head})


def _write_registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()

    def write_file(payload: dict[str, Any]) -> str:
        path = root / str(payload["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(payload["content"]), encoding="utf-8")
        return "ok"

    registry.register(
        ToolSpec(
            name="write_file",
            description="Write a UTF-8 text file inside the workspace.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=write_file,
            action_class="write_local",
            resource_scope_hint=str(root),
            risk_hint="high",
            requires_receipt=True,
            supports_preview=True,
        )
    )
    return registry


def _mixed_registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        ToolSpec(
            name="read_file",
            description="Read a UTF-8 text file inside the workspace.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda payload: (root / str(payload["path"])).read_text(encoding="utf-8"),
            readonly=True,
            action_class="read_local",
            resource_scope_hint=str(root),
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        )
    )
    registry.register(
        ToolSpec(
            name="mystery_mutation",
            description="An unclassified mutating tool.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda payload: {"ok": True, "payload": payload},
            action_class="external_mutation",
            risk_hint="high",
            requires_receipt=True,
        )
    )
    return registry


def _network_read_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="grok_search",
            description="Search current information from the network.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda payload: {"ok": True, "payload": payload},
            readonly=True,
            action_class="network_read",
            risk_hint="low",
            requires_receipt=False,
        )
    )
    return registry


def _bash_registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="bash",
            description="Run shell command.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda payload: {"stdout": str(payload.get("command", ""))},
            action_class="execute_command",
            resource_scope_hint=str(root),
            risk_hint="critical",
            requires_receipt=True,
            supports_preview=True,
        )
    )
    return registry


def _attachment_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="image_store_from_feishu",
            description="Store an incoming Feishu image.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda payload: {"ok": True, "payload": payload},
            action_class="attachment_ingest",
            risk_hint="high",
            requires_receipt=True,
        )
    )
    return registry


def _observation_registry(status_responses: list[dict[str, Any]]) -> ToolRegistry:
    registry = ToolRegistry()

    def observe_start(_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "_hermit_observation": {
                "observer_kind": "tool_call",
                "job_id": "job-1",
                "status_ref": "job-1",
                "poll_after_seconds": 0.0,
                "cancel_supported": False,
                "resume_token": "job-1",
                "topic_summary": "Observation submitted.",
                "display_name": "Observed Search",
                "tool_name": "observe_start",
                "status_tool_name": "observe_status",
                "ready_return": True,
            }
        }

    def observe_status(_payload: dict[str, Any]) -> dict[str, Any]:
        return status_responses.pop(0)

    registry.register(
        ToolSpec(
            name="observe_start",
            description="Submit a long-running observed task.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=observe_start,
            readonly=True,
            action_class="network_read",
            risk_hint="low",
            requires_receipt=False,
            idempotent=True,
        )
    )
    registry.register(
        ToolSpec(
            name="observe_status",
            description="Poll observed task status.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=observe_status,
            readonly=True,
            action_class="network_read",
            risk_hint="low",
            requires_receipt=False,
            idempotent=True,
        )
    )
    return registry


def _kernel_runtime(
    tmp_path: Path,
) -> tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, TaskExecutionContext]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="chat-kernel",
        goal="Update a file",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )
    executor = ToolExecutor(
        registry=_write_registry(workspace),
        store=store,
        artifact_store=artifacts,
        policy_engine=PolicyEngine(),
        approval_service=ApprovalService(store),
        receipt_service=ReceiptService(store),
        tool_output_limit=2000,
    )
    return store, artifacts, controller, executor, ctx


class _RunnerSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self.saved = 0

    def get_or_create(self, session_id: str) -> Session:
        return self._sessions.setdefault(session_id, Session(session_id=session_id))

    def save(self, session: Session) -> None:
        self._sessions[session.session_id] = session
        self.saved += 1

    def close(self, session_id: str) -> Session | None:
        return self._sessions.pop(session_id, None)


class _RunnerPluginManager:
    def __init__(self, tmp_path: Path) -> None:
        self.settings = SimpleNamespace(
            locale="en-US", base_dir=tmp_path, kernel_dispatch_worker_count=2
        )
        self.hooks = HooksEngine()
        self.started: list[str] = []
        self.ended: list[str] = []
        self.post_run: list[str] = []

    def on_session_start(self, session_id: str) -> None:
        self.started.append(session_id)

    def on_session_end(self, session_id: str, _messages: list[dict[str, Any]]) -> None:
        self.ended.append(session_id)

    def on_pre_run(self, prompt: str, **_kwargs: Any) -> tuple[str, dict[str, Any]]:
        return prompt, {}

    def on_post_run(self, result: AgentResult, **_kwargs: Any) -> None:
        self.post_run.append(result.text)


class _AsyncAgent:
    def __init__(self) -> None:
        self.workspace_root = "/tmp/workspace"
        self.run_result = AgentResult(text="done", turns=1, tool_calls=0, messages=[])
        self.resume_result = AgentResult(text="resumed", turns=1, tool_calls=0, messages=[])
        self.run_calls: list[dict[str, Any]] = []
        self.resume_calls: list[dict[str, Any]] = []
        self.raise_on_run: Exception | None = None

    def run(self, prompt: str, **kwargs: Any) -> AgentResult:
        self.run_calls.append({"prompt": prompt, **kwargs})
        if self.raise_on_run is not None:
            raise self.raise_on_run
        return self.run_result

    def resume(self, **kwargs: Any) -> AgentResult:
        self.resume_calls.append(kwargs)
        return self.resume_result


__all__ = [name for name in globals() if not name.startswith("__")]
