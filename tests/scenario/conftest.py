from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import PolicyEngine
from hermit.kernel.policy.approvals.approvals import ApprovalService
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.receipts.receipts import ReceiptService
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec


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


@pytest.fixture
def kernel_runtime(
    tmp_path: Path,
) -> tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, TaskExecutionContext, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    ctx = controller.start_task(
        conversation_id="scenario-chat",
        goal="Governed execution lifecycle test",
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
    return store, artifacts, controller, executor, ctx, workspace
