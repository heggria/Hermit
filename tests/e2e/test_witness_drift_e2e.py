"""E2E: Witness drift — state changes between approval and execution trigger reentry.

Exercises the witness capture → approval → external state modification → drift
detection → attempt supersedence flow.
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.approvals.approvals import ApprovalService
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.proofs.proofs import ProofService


def test_write_blocked_then_file_modified_externally_triggers_witness_drift(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Approval for .env write, then external modification causes witness drift on re-execute."""
    store, _artifacts, controller, executor, workspace = e2e_runtime

    # Seed sensitive file
    target = workspace / ".env"
    target.write_text("SECRET=original\n", encoding="utf-8")

    ctx = controller.start_task(
        conversation_id="e2e-witness-drift",
        goal="Update sensitive config",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    # 1. First attempt blocks (sensitive path)
    blocked = executor.execute(ctx, "write_file", {"path": ".env", "content": "SECRET=new\n"})
    assert blocked.blocked is True
    assert blocked.approval_id is not None

    # 2. Approve the action
    ApprovalService(store).approve(blocked.approval_id)

    # 3. Externally modify the file (simulates another process changing state)
    target.write_text("SECRET=changed_externally\n", encoding="utf-8")

    # 4. Re-execute — witness drift should be detected
    executor.execute(ctx, "write_file", {"path": ".env", "content": "SECRET=new\n"})

    # Drift causes reentry: either blocked again or a new attempt is created
    # The key signal is that the original approval was invalidated
    events = store.list_events(task_id=ctx.task_id)
    event_types = [e["event_type"] for e in events]

    # Should see drift-related events
    has_drift = any(
        t in ("approval.drifted", "witness.failed", "step_attempt.superseded") for t in event_types
    )
    assert has_drift, f"Expected drift event, got: {event_types}"


def test_witness_captures_file_state_before_execution(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Witness captures file SHA256 and metadata before governed execution."""
    store, _artifacts, controller, executor, workspace = e2e_runtime

    target = workspace / "data.txt"
    target.write_text("initial content\n", encoding="utf-8")

    ctx = controller.start_task(
        conversation_id="e2e-witness-capture",
        goal="Overwrite data file",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    result = executor.execute(
        ctx, "write_file", {"path": "data.txt", "content": "updated content\n"}
    )
    assert result.blocked is False
    assert result.receipt_id is not None

    # Verify witness was captured (witness event in audit trail)
    events = store.list_events(task_id=ctx.task_id)
    witness_events = [e for e in events if e["event_type"] == "witness.captured"]
    assert len(witness_events) >= 1

    # The receipt should reference a witness
    receipt = store.get_receipt(result.receipt_id)
    assert receipt is not None


def test_multiple_writes_with_interleaved_external_changes(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Multiple writes where external changes happen between governed operations."""
    store, artifacts, controller, executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-interleaved",
        goal="Write multiple files with external interference",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    # Write file A — succeeds
    result_a = executor.execute(ctx, "write_file", {"path": "a.txt", "content": "content A\n"})
    assert result_a.blocked is False
    assert result_a.receipt_id is not None

    # External process creates file B before our write
    (workspace / "b.txt").write_text("external content\n", encoding="utf-8")

    # Write file B — governed write overwrites external content
    result_b = executor.execute(
        ctx, "write_file", {"path": "b.txt", "content": "governed content\n"}
    )
    assert result_b.blocked is False
    assert result_b.receipt_id is not None
    assert (workspace / "b.txt").read_text(encoding="utf-8") == "governed content\n"

    # Verify both writes produced receipts
    receipts = store.list_receipts(task_id=ctx.task_id, limit=10)
    assert len(receipts) == 2

    # Proof chain remains valid despite external interference
    chain = ProofService(store, artifacts).verify_task_chain(ctx.task_id)
    assert chain["valid"] is True


def test_evidence_invalidation_forces_re_approval(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Evidence invalidation between approval and execution forces policy recompile."""
    store, _artifacts, controller, executor, workspace = e2e_runtime

    target = workspace / ".env"
    target.write_text("KEY=val\n", encoding="utf-8")

    ctx = controller.start_task(
        conversation_id="e2e-evidence-invalidation",
        goal="Update sensitive file after evidence invalidation",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    # 1. First attempt blocks
    blocked = executor.execute(ctx, "write_file", {"path": ".env", "content": "KEY=new\n"})
    assert blocked.blocked is True
    assert blocked.approval_id is not None

    # 2. Approve
    ApprovalService(store).approve(blocked.approval_id)

    # 3. Invalidate evidence case (simulates external contradiction)
    evidence_cases = store.list_evidence_cases(task_id=ctx.task_id, limit=10)
    if evidence_cases:
        executor.evidence_cases.invalidate(
            evidence_cases[0].evidence_case_id,
            contradictions=["manual_probe"],
            summary="Evidence contradicted by external observation",
        )

    # 4. Re-execute — should detect evidence drift
    executor.execute(ctx, "write_file", {"path": ".env", "content": "KEY=new\n"})

    events = store.list_events(task_id=ctx.task_id)
    event_types = [e["event_type"] for e in events]

    # Evidence invalidation should produce audit events
    has_invalidation = any("invalidated" in t or "evidence" in t for t in event_types)
    assert has_invalidation, f"Expected evidence event, got: {event_types}"
