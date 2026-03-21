from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.context.models.context import TaskExecutionContext
    from hermit.kernel.execution.executor.executor import ToolExecutor
    from hermit.kernel.ledger.journal.store import KernelStore
    from hermit.kernel.task.services.controller import TaskController
    from hermit.runtime.capability.registry.tools import ToolRegistry


def _write_registry(root: Path) -> ToolRegistry:
    from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec

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


@pytest.fixture(scope="module")
def _kernel_base(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[KernelStore, ArtifactStore, Path]:
    """Module-scoped heavy objects: KernelStore (SQLite) and ArtifactStore.

    These are expensive to initialize (schema migration, WAL setup) and can
    be safely shared across tests that create their own tasks.
    """
    from hermit.kernel.artifacts.models.artifacts import ArtifactStore
    from hermit.kernel.ledger.journal.store import KernelStore

    base = tmp_path_factory.mktemp("scenario")
    store = KernelStore(base / "kernel" / "state.db")
    artifacts = ArtifactStore(base / "kernel" / "artifacts")
    workspace = base / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return store, artifacts, workspace


@pytest.fixture
def kernel_runtime(
    _kernel_base: tuple[KernelStore, ArtifactStore, Path],
) -> tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, TaskExecutionContext, Path]:
    store, artifacts, workspace = _kernel_base

    from hermit.kernel.execution.executor.executor import ToolExecutor
    from hermit.kernel.policy import PolicyEngine
    from hermit.kernel.policy.approvals.approvals import ApprovalService
    from hermit.kernel.task.services.controller import TaskController
    from hermit.kernel.verification.receipts.receipts import ReceiptService

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
