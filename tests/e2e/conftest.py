"""Shared fixtures for end-to-end tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.execution.executor.executor import ToolExecutor
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.services.controller import TaskController
    from hermit.runtime.capability.registry.tools import ToolRegistry


def _full_registry(root: Path) -> ToolRegistry:
    """Build a registry with read, write, and bash tools for e2e testing."""
    from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec

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
            name="write_file",
            description="Write a UTF-8 text file inside the workspace.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda payload: _do_write(root, payload),
            action_class="write_local",
            resource_scope_hint=str(root),
            risk_hint="high",
            requires_receipt=True,
            supports_preview=True,
        )
    )
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


def _do_write(root: Path, payload: dict[str, Any]) -> str:
    path = root / str(payload["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(payload["content"]), encoding="utf-8")
    return "ok"


@pytest.fixture
def e2e_runtime(
    tmp_path: Path,
) -> tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path]:
    """Full kernel runtime with read/write/bash tools for e2e scenarios."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.execution.executor.executor import ToolExecutor
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.policy import PolicyEngine
    from hermit.kernel.policy.approvals.approvals import ApprovalService
    from hermit.kernel.task.services.controller import TaskController
    from hermit.kernel.verification.receipts.receipts import ReceiptService

    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    registry = _full_registry(workspace)
    executor = ToolExecutor(
        registry=registry,
        store=store,
        artifact_store=artifacts,
        policy_engine=PolicyEngine(),
        approval_service=ApprovalService(store),
        receipt_service=ReceiptService(store),
        tool_output_limit=2000,
    )
    return store, artifacts, controller, executor, workspace
