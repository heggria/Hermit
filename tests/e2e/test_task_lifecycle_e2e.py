"""E2E: Task lifecycle — creation, state transitions, finalization, and CLI inspection.

Exercises the full task lifecycle from ingress routing through state transitions
to finalization and post-completion inspection via CLI commands.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController
from hermit.surfaces.cli.main import app


def test_task_creation_execution_and_finalization(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Full task lifecycle: create → execute → finalize → verify terminal state."""
    store, _artifacts, controller, executor, workspace = e2e_runtime

    # 1. Start task
    ctx = controller.start_task(
        conversation_id="e2e-lifecycle",
        goal="Create a summary file",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )
    task = store.get_task(ctx.task_id)
    assert task is not None and task.status == "running"

    # 2. Execute work
    executor.execute(ctx, "write_file", {"path": "summary.txt", "content": "Done.\n"})

    # 3. Finalize
    controller.finalize_result(
        ctx,
        status="succeeded",
        result_preview="Summary created.",
        result_text="The summary file has been created at summary.txt.",
    )

    # 4. Verify terminal state
    completed = store.get_task(ctx.task_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result_preview == "Summary created."


def test_task_pause_and_cancel_transitions(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Task can be paused and then cancelled, with correct state transitions."""
    store, _artifacts, controller, _executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-pause-cancel",
        goal="A pausable task",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )

    # Running → Paused
    controller.pause_task(ctx.task_id)
    paused = store.get_task(ctx.task_id)
    assert paused is not None and paused.status == "paused"

    # Paused → Cancelled
    controller.cancel_task(ctx.task_id)
    cancelled = store.get_task(ctx.task_id)
    assert cancelled is not None and cancelled.status == "cancelled"


def test_task_enqueue_resume_cycle(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Task can be enqueued, suspended, and resumed correctly."""
    store, _artifacts, controller, _executor, workspace = e2e_runtime

    # Enqueue (async-style)
    ctx = controller.enqueue_task(
        conversation_id="e2e-enqueue",
        goal="Background processing",
        source_channel="feishu",
        kind="respond",
        workspace_root=str(workspace),
    )
    task = store.get_task(ctx.task_id)
    assert task is not None and task.status == "queued"

    # Suspend (simulates blocked state)
    controller.mark_suspended(ctx, waiting_kind="awaiting_approval")
    blocked = store.get_task(ctx.task_id)
    assert blocked is not None and blocked.status == "blocked"

    # Resume
    resumed = controller.enqueue_resume(ctx.step_attempt_id)
    assert resumed.step_attempt_id == ctx.step_attempt_id
    task_after = store.get_task(ctx.task_id)
    assert task_after is not None and task_after.status == "queued"


def test_cli_task_list_show_and_receipts(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """CLI task commands correctly display task state, details, and receipts."""
    from hermit.runtime.assembly.config import get_settings

    base_dir = tmp_path / ".hermit"
    monkeypatch.setenv("HERMIT_BASE_DIR", str(base_dir))  # type: ignore[union-attr]
    get_settings.cache_clear()

    store = KernelStore(base_dir / "kernel" / "state.db")
    store.ensure_conversation("e2e-cli", source_channel="chat")
    task = store.create_task(
        conversation_id="e2e-cli",
        title="E2E CLI Task",
        goal="Test CLI inspection",
        source_channel="chat",
    )
    step = store.create_step(task_id=task.task_id, kind="respond")
    attempt = store.create_step_attempt(task_id=task.task_id, step_id=step.step_id)
    decision = store.create_decision(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_type="execution_authorization",
        verdict="allow",
        reason="Policy allowed workspace write.",
        evidence_refs=["artifact_action"],
        action_type="write_local",
    )
    grant = store.create_capability_grant(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        decision_ref=decision.decision_id,
        approval_ref=None,
        policy_ref="policy_e2e",
        issued_to_principal_id="user",
        issued_by_principal_id="kernel",
        workspace_lease_ref=None,
        action_class="write_local",
        resource_scope=["workspace"],
        constraints={},
        idempotency_key="e2e_cli",
        expires_at=None,
    )
    store.create_receipt(
        task_id=task.task_id,
        step_id=step.step_id,
        step_attempt_id=attempt.step_attempt_id,
        action_type="write_local",
        input_refs=["artifact_in"],
        environment_ref="artifact_env",
        policy_result={"decision": "allow"},
        approval_ref=None,
        output_refs=["artifact_out"],
        result_summary="write_file executed successfully",
        result_code="succeeded",
        decision_ref=decision.decision_id,
        capability_grant_ref=grant.grant_id,
        policy_ref="policy_e2e",
    )

    runner = CliRunner()

    # hermit task list
    list_result = runner.invoke(app, ["task", "list"])
    assert list_result.exit_code == 0
    assert task.task_id in list_result.output
    assert "E2E CLI Task" in list_result.output

    # hermit task show
    show_result = runner.invoke(app, ["task", "show", task.task_id])
    assert show_result.exit_code == 0
    assert task.task_id in show_result.output

    # hermit task receipts
    receipts_result = runner.invoke(app, ["task", "receipts", "--task-id", task.task_id])
    assert receipts_result.exit_code == 0
    assert "write_file executed successfully" in receipts_result.output

    # hermit task explain
    explain_result = runner.invoke(app, ["task", "explain", task.task_id])
    assert explain_result.exit_code == 0
    explain_payload = json.loads(explain_result.output)
    assert explain_payload["task"]["task_id"] == task.task_id
    assert explain_payload["operator_answers"]["why_execute"] == "Policy allowed workspace write."

    # hermit task proof
    proof_result = runner.invoke(app, ["task", "proof", task.task_id])
    assert proof_result.exit_code == 0
    proof_payload = json.loads(proof_result.output)
    assert proof_payload["chain_verification"]["valid"] is True
