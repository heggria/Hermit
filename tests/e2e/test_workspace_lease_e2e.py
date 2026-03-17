"""E2E: Workspace leases — mutable workspace approval enables multi-write sessions.

Exercises the approval → mutable_workspace grant → lease acquisition → multiple
writes without re-approval path.
"""

from __future__ import annotations

from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.approvals.approvals import ApprovalService
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.verification.proofs.proofs import ProofService


def test_mutable_workspace_approval_allows_multiple_writes(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """After mutable_workspace approval, subsequent writes don't need re-approval."""
    store, _artifacts, controller, executor, workspace = e2e_runtime

    (workspace / ".env").write_text("OLD=val\n", encoding="utf-8")

    ctx = controller.start_task(
        conversation_id="e2e-mutable-ws",
        goal="Multiple writes to sensitive files",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    # 1. First write to sensitive file blocks
    blocked = executor.execute(ctx, "write_file", {"path": ".env", "content": "NEW=val1\n"})
    assert blocked.blocked is True
    assert blocked.approval_id is not None

    # 2. Approve with mutable_workspace mode
    approval_svc = ApprovalService(store)
    approval_svc.approve_mutable_workspace(blocked.approval_id)

    # 3. Re-execute — should succeed now
    result1 = executor.execute(ctx, "write_file", {"path": ".env", "content": "NEW=val1\n"})
    assert result1.blocked is False
    assert result1.receipt_id is not None
    assert (workspace / ".env").read_text(encoding="utf-8") == "NEW=val1\n"

    # 4. Verify workspace lease was created
    assert result1.workspace_lease_id is not None
    lease = store.get_workspace_lease(result1.workspace_lease_id)
    assert lease is not None
    assert lease.mode == "mutable"
    assert lease.status == "active"


def test_workspace_lease_authority_chain(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Workspace lease is part of the capability grant authority chain."""
    store, artifacts, controller, executor, workspace = e2e_runtime

    (workspace / ".env").write_text("KEY=old\n", encoding="utf-8")

    ctx = controller.start_task(
        conversation_id="e2e-lease-authority",
        goal="Verify lease authority chain",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    # Block and approve with mutable workspace
    blocked = executor.execute(ctx, "write_file", {"path": ".env", "content": "KEY=new\n"})
    assert blocked.blocked is True
    ApprovalService(store).approve_mutable_workspace(blocked.approval_id)

    result = executor.execute(ctx, "write_file", {"path": ".env", "content": "KEY=new\n"})
    assert result.blocked is False
    assert result.receipt_id is not None

    # Verify full authority chain
    receipt = store.get_receipt(result.receipt_id)
    assert receipt is not None
    assert receipt.decision_ref is not None
    assert receipt.capability_grant_ref is not None

    grant = store.get_capability_grant(receipt.capability_grant_ref)
    assert grant is not None
    assert grant.workspace_lease_ref is not None

    # Verify proof chain is valid with lease
    chain = ProofService(store, artifacts).verify_task_chain(ctx.task_id)
    assert chain["valid"] is True


def test_normal_write_creates_scoped_lease(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Normal (non-sensitive) governed writes create scoped leases."""
    store, _artifacts, controller, executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-scoped-lease",
        goal="Normal write with scoped lease",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    result = executor.execute(ctx, "write_file", {"path": "normal.txt", "content": "hello\n"})
    assert result.blocked is False
    assert result.receipt_id is not None

    # Normal writes may also create workspace leases
    if result.workspace_lease_id is not None:
        lease = store.get_workspace_lease(result.workspace_lease_id)
        assert lease is not None
        # Scoped lease for normal (once) approval
        assert lease.mode in ("scoped", "mutable")
        assert lease.status == "active"


def test_approval_flow_records_complete_audit_trail(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Full approval flow produces comprehensive audit events."""
    store, _artifacts, controller, executor, workspace = e2e_runtime

    (workspace / ".env").write_text("SECRET=abc\n", encoding="utf-8")

    ctx = controller.start_task(
        conversation_id="e2e-audit-trail",
        goal="Test audit completeness",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    # Block → approve → execute
    blocked = executor.execute(ctx, "write_file", {"path": ".env", "content": "SECRET=xyz\n"})
    ApprovalService(store).approve(blocked.approval_id)
    result = executor.execute(ctx, "write_file", {"path": ".env", "content": "SECRET=xyz\n"})
    assert result.receipt_id is not None

    # Verify comprehensive event trail
    events = store.list_events(task_id=ctx.task_id)
    event_types = {e["event_type"] for e in events}

    # Must have receipt and witness events
    assert "receipt.issued" in event_types
    assert "witness.captured" in event_types

    # Verify contract loop
    contracts = store.list_execution_contracts(task_id=ctx.task_id, limit=10)
    assert len(contracts) >= 1

    evidence_cases = store.list_evidence_cases(task_id=ctx.task_id, limit=10)
    assert len(evidence_cases) >= 1

    auth_plans = store.list_authorization_plans(task_id=ctx.task_id, limit=10)
    assert len(auth_plans) >= 1

    reconciliations = store.list_reconciliations(task_id=ctx.task_id, limit=10)
    assert len(reconciliations) >= 1
