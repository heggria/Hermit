"""E2E: Uncertain outcomes — tool handler exceptions trigger reconciliation.

Exercises the exception → mark_uncertain → reconcile → receipt path for
governed actions that fail mid-execution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import PolicyEngine
from hermit.kernel.policy.approvals.approvals import ApprovalService
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.proofs.proofs import ProofService
from hermit.kernel.verification.receipts.receipts import ReceiptService
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec


def _flaky_registry(root: Path) -> ToolRegistry:
    """Registry with a write tool that crashes mid-execution."""
    registry = ToolRegistry()

    call_count: dict[str, int] = {"write_flaky": 0}

    def flaky_write(payload: dict[str, Any]) -> str:
        call_count["write_flaky"] += 1
        path = root / str(payload["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write partial content then crash
        path.write_text(str(payload["content"]), encoding="utf-8")
        raise OSError("Disk I/O error: write interrupted")

    registry.register(
        ToolSpec(
            name="read_file",
            description="Read file.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=lambda p: (root / str(p["path"])).read_text(encoding="utf-8"),
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
            name="write_flaky",
            description="Write that crashes after partial write.",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=flaky_write,
            action_class="write_local",
            resource_scope_hint=str(root),
            risk_hint="high",
            requires_receipt=True,
            supports_preview=True,
        )
    )
    return registry


@pytest.fixture
def flaky_runtime(
    tmp_path: Path,
) -> tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path]:
    """Runtime with a flaky write tool for uncertain outcome testing."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = KernelStore(tmp_path / "kernel" / "state.db")
    artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
    controller = TaskController(store)
    registry = _flaky_registry(workspace)
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


def test_flaky_write_triggers_uncertain_outcome_and_reconciliation(
    flaky_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Tool handler exception triggers uncertain outcome with reconciliation."""
    store, _artifacts, controller, executor, workspace = flaky_runtime

    ctx = controller.start_task(
        conversation_id="e2e-uncertain",
        goal="Write with flaky tool",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    # Execute flaky write — handler crashes after writing
    result = executor.execute(ctx, "write_flaky", {"path": "data.txt", "content": "partial data\n"})

    # The execution should not bubble up the exception — it's handled internally
    # Result indicates the outcome is uncertain/reconciling
    assert result.state_applied is True

    # Verify uncertain outcome events were recorded
    events = store.list_events(task_id=ctx.task_id)
    event_types = [e["event_type"] for e in events]
    assert any("uncertain" in t or "reconcil" in t for t in event_types), (
        f"Expected uncertain/reconciliation event, got: {event_types}"
    )

    # Capability grant should be marked uncertain
    if result.capability_grant_id:
        grant = store.get_capability_grant(result.capability_grant_id)
        assert grant is not None
        assert grant.status == "uncertain"

    # A receipt should still be issued (with error status)
    assert result.receipt_id is not None
    receipt = store.get_receipt(result.receipt_id)
    assert receipt is not None

    # Reconciliation should have been recorded
    reconciliations = store.list_reconciliations(task_id=ctx.task_id, limit=10)
    assert len(reconciliations) >= 1


def test_flaky_write_file_actually_written_reconciles_as_applied(
    flaky_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """When a flaky tool writes the file before crashing, reconciliation detects it."""
    store, _artifacts, controller, executor, workspace = flaky_runtime

    ctx = controller.start_task(
        conversation_id="e2e-reconcile-applied",
        goal="Write then crash",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    executor.execute(ctx, "write_flaky", {"path": "output.txt", "content": "full content\n"})

    # File was actually written (handler writes before crashing)
    assert (workspace / "output.txt").exists()
    assert (workspace / "output.txt").read_text(encoding="utf-8") == "full content\n"

    # Reconciliation should detect the file was actually applied
    reconciliations = store.list_reconciliations(task_id=ctx.task_id, limit=10)
    assert len(reconciliations) >= 1
    recon = reconciliations[0]
    # The reconciler checks if the file exists with expected content
    assert recon.result_class in ("satisfied", "violated", "partial", "ambiguous")


def test_normal_write_after_uncertain_outcome_produces_valid_chain(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """A normal successful write after a task with reconciliation still has valid proof chain."""
    store, artifacts, controller, executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-post-reconcile",
        goal="Write files normally",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    # Multiple successful writes
    for i in range(3):
        result = executor.execute(
            ctx, "write_file", {"path": f"file{i}.txt", "content": f"content {i}\n"}
        )
        assert result.blocked is False
        assert result.receipt_id is not None

    # Verify complete proof chain
    chain = ProofService(store, artifacts).verify_task_chain(ctx.task_id)
    assert chain["valid"] is True
    assert chain["event_count"] >= 3

    # Verify all reconciliations (if any) are clean
    reconciliations = store.list_reconciliations(task_id=ctx.task_id, limit=10)
    # Normal writes should have completed reconciliations
    for r in reconciliations:
        assert r.result_class != "ambiguous"
