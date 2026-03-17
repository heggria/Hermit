"""E2E: Rollback — governed write followed by rollback restores original state.

Exercises the full rollback path: write → receipt → rollback → verify restoration.
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.rollbacks.rollbacks import RollbackService


def test_write_and_rollback_restores_file(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Write a file, then roll back the receipt — original content is restored."""
    store, artifacts, controller, executor, workspace = e2e_runtime

    # Seed original content
    target = workspace / "config.yaml"
    target.write_text("version: 1\n", encoding="utf-8")

    ctx = controller.start_task(
        conversation_id="e2e-rollback",
        goal="Update config then roll back",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    # Write new content
    result = executor.execute(ctx, "write_file", {"path": "config.yaml", "content": "version: 2\n"})
    assert result.blocked is False
    assert result.receipt_id is not None
    assert target.read_text(encoding="utf-8") == "version: 2\n"

    # Get receipt and verify rollback is available
    receipt = store.get_receipt(result.receipt_id)
    assert receipt is not None
    assert receipt.rollback_status == "available"
    assert len(receipt.rollback_artifact_refs) >= 1

    # Execute rollback
    rollback_service = RollbackService(store, artifacts)
    rollback_result = rollback_service.execute(receipt.receipt_id)

    assert rollback_result["status"] == "succeeded"
    assert target.read_text(encoding="utf-8") == "version: 1\n"

    # Verify receipt rollback status updated
    updated_receipt = store.get_receipt(result.receipt_id)
    assert updated_receipt is not None
    assert updated_receipt.rollback_status == "succeeded"


def test_write_new_file_rollback_deletes_it(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Writing a new file and rolling back removes it entirely."""
    store, artifacts, controller, executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-rollback-new",
        goal="Create and rollback new file",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    target = workspace / "temporary.txt"
    assert not target.exists()

    result = executor.execute(
        ctx, "write_file", {"path": "temporary.txt", "content": "ephemeral\n"}
    )
    assert target.exists()
    assert result.receipt_id is not None

    # Rollback
    rollback_service = RollbackService(store, artifacts)
    rollback_result = rollback_service.execute(result.receipt_id)

    assert rollback_result["status"] == "succeeded"
    assert not target.exists()
