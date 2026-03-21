"""End-to-end tests for the approval / parking / resume lifecycle.

These tests exercise state consistency across the full kernel using a real
KernelStore backed by SQLite (via tmp_path), verifying that approval-gated
task flows maintain correct state at every transition point.
"""

from __future__ import annotations

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kernel(tmp_path):
    """Return (store, controller) backed by a real SQLite database."""
    store = KernelStore(tmp_path / "kernel" / "state.db")
    controller = TaskController(store)
    return store, controller


def _start_task(controller, *, conversation_id="conv_approval", goal="test approval lifecycle"):
    """Create a task with a running step + attempt via the controller."""
    return controller.start_task(
        conversation_id=conversation_id,
        goal=goal,
        source_channel="cli",
        kind="respond",
    )


# ---------------------------------------------------------------------------
# Test 5: approval → park → approve → resume → complete
# ---------------------------------------------------------------------------


def test_approval_park_approve_resume_complete(tmp_path) -> None:
    store, controller = _make_kernel(tmp_path)

    # 1. Create task + step + attempt (running)
    ctx = _start_task(controller)
    task = store.get_task(ctx.task_id)
    assert task is not None and task.status == "running"

    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert attempt is not None and attempt.status == "running"

    # 2. Store an approval request
    approval = store.create_approval(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        approval_type="operator_confirmation",
        requested_action={"tool": "write_file", "path": "/tmp/test.txt"},
        request_packet_ref=None,
    )
    assert approval.status == "pending"

    # 3. Park the attempt (awaiting_approval)
    controller.mark_suspended(ctx, waiting_kind="awaiting_approval")

    parked_attempt = store.get_step_attempt(ctx.step_attempt_id)
    parked_task = store.get_task(ctx.task_id)
    parked_step = store.get_step(ctx.step_id)

    assert parked_attempt is not None and parked_attempt.status == "awaiting_approval"
    assert parked_task is not None and parked_task.status == "blocked"
    assert parked_step is not None and parked_step.status == "blocked"

    # 4. Approve the request
    store.resolve_approval(
        approval.approval_id,
        status="approved",
        resolved_by="operator",
        resolution={"approved": True, "reason": "looks safe"},
    )
    resolved = store.get_approval(approval.approval_id)
    assert resolved is not None and resolved.status == "approved"
    assert resolved.resolved_at is not None

    # 5. Create a decision recording the approval
    decision = store.create_decision(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        decision_type="approval_resolution",
        verdict="approved",
        reason="operator approved the action",
        approval_ref=approval.approval_id,
    )
    assert decision.verdict == "approved"

    # 6. Simulate resume: create a new attempt on the same step
    new_attempt = store.create_step_attempt(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        status="ready",
        context={"execution_mode": "resume", "phase": "executing"},
    )
    store.update_step(ctx.step_id, status="ready")
    store.update_task_status(ctx.task_id, "running")

    # 7. Verify state consistency
    original = store.get_step_attempt(ctx.step_attempt_id)
    resumed = store.get_step_attempt(new_attempt.step_attempt_id)
    final_task = store.get_task(ctx.task_id)

    assert original is not None and original.status == "awaiting_approval"
    assert resumed is not None and resumed.status == "ready"
    assert final_task is not None and final_task.status == "running"

    # Verify the approval event trail
    events = store.list_events(task_id=ctx.task_id)
    approval_events = [e for e in events if "approval" in e["event_type"]]
    assert len(approval_events) >= 2  # requested + approved
    event_types = [e["event_type"] for e in approval_events]
    assert "approval.requested" in event_types
    assert "approval.approved" in event_types


# ---------------------------------------------------------------------------
# Test 6: approval → park → deny → fail
# ---------------------------------------------------------------------------


def test_approval_park_deny_fails_task(tmp_path) -> None:
    store, controller = _make_kernel(tmp_path)

    # 1. Create task + step + attempt
    ctx = _start_task(controller, goal="test denial flow")

    # 2. Store approval request and park
    approval = store.create_approval(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        approval_type="operator_confirmation",
        requested_action={"tool": "bash", "command": "rm -rf /"},
        request_packet_ref=None,
    )
    controller.mark_suspended(ctx, waiting_kind="awaiting_approval")

    parked = store.get_step_attempt(ctx.step_attempt_id)
    assert parked is not None and parked.status == "awaiting_approval"

    # 3. Deny the approval
    store.resolve_approval(
        approval.approval_id,
        status="denied",
        resolved_by="operator",
        resolution={"approved": False, "reason": "too dangerous"},
    )
    denied = store.get_approval(approval.approval_id)
    assert denied is not None and denied.status == "denied"

    # 4. Record the denial decision
    decision = store.create_decision(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        decision_type="approval_resolution",
        verdict="denied",
        reason="operator denied the action",
        approval_ref=approval.approval_id,
    )
    assert decision.verdict == "denied"

    # 5. Fail the attempt and step
    store.update_step_attempt(ctx.step_attempt_id, status="failed")
    store.update_step(ctx.step_id, status="failed")
    store.update_task_status(ctx.task_id, "failed")

    # 6. Verify terminal state
    failed_attempt = store.get_step_attempt(ctx.step_attempt_id)
    failed_step = store.get_step(ctx.step_id)
    failed_task = store.get_task(ctx.task_id)

    assert failed_attempt is not None and failed_attempt.status == "failed"
    assert failed_step is not None and failed_step.status == "failed"
    assert failed_task is not None and failed_task.status == "failed"

    # Verify denial is recorded in events
    events = store.list_events(task_id=ctx.task_id)
    denial_events = [e for e in events if e["event_type"] == "approval.denied"]
    assert len(denial_events) == 1

    decision_events = [e for e in events if e["event_type"] == "decision.recorded"]
    assert any(e["payload"]["verdict"] == "denied" for e in decision_events)


# ---------------------------------------------------------------------------
# Test 7: multiple tasks — one parked, others continue independently
# ---------------------------------------------------------------------------


def test_multiple_tasks_one_parked_others_continue(tmp_path) -> None:
    store, controller = _make_kernel(tmp_path)

    # 1. Create three independent tasks
    ctx1 = _start_task(controller, conversation_id="conv_multi_1", goal="task that will be parked")
    ctx2 = _start_task(controller, conversation_id="conv_multi_2", goal="task that proceeds")
    ctx3 = _start_task(
        controller, conversation_id="conv_multi_3", goal="another task that proceeds"
    )

    # Verify all three are running
    for ctx in [ctx1, ctx2, ctx3]:
        t = store.get_task(ctx.task_id)
        assert t is not None and t.status == "running"

    # 2. Park task 1 with an approval request
    approval = store.create_approval(
        task_id=ctx1.task_id,
        step_id=ctx1.step_id,
        step_attempt_id=ctx1.step_attempt_id,
        approval_type="operator_confirmation",
        requested_action={"tool": "write_file"},
        request_packet_ref=None,
    )
    controller.mark_suspended(ctx1, waiting_kind="awaiting_approval")

    # 3. Complete tasks 2 and 3
    controller.finalize_result(
        ctx2,
        status="succeeded",
        result_preview="task 2 done",
        result_text="task 2 completed successfully",
    )
    controller.finalize_result(
        ctx3,
        status="succeeded",
        result_preview="task 3 done",
        result_text="task 3 completed successfully",
    )

    # 4. Verify state: task 1 blocked, tasks 2 & 3 completed
    task1 = store.get_task(ctx1.task_id)
    task2 = store.get_task(ctx2.task_id)
    task3 = store.get_task(ctx3.task_id)

    assert task1 is not None and task1.status == "blocked"
    assert task2 is not None and task2.status == "completed"
    assert task3 is not None and task3.status == "completed"

    # 5. Verify task 1's approval is still pending
    pending_approval = store.get_approval(approval.approval_id)
    assert pending_approval is not None and pending_approval.status == "pending"

    # 6. Verify the attempt on task 1 is still parked
    attempt1 = store.get_step_attempt(ctx1.step_attempt_id)
    assert attempt1 is not None and attempt1.status == "awaiting_approval"

    # 7. Verify tasks 2 and 3 have no pending approvals
    approvals_t2 = store.list_approvals(task_id=ctx2.task_id, status="pending")
    approvals_t3 = store.list_approvals(task_id=ctx3.task_id, status="pending")
    assert len(approvals_t2) == 0
    assert len(approvals_t3) == 0


# ---------------------------------------------------------------------------
# Test 8: low-risk autonomous task proceeds without approval
# ---------------------------------------------------------------------------


def test_low_risk_auto_proceeds_without_approval(tmp_path) -> None:
    store, controller = _make_kernel(tmp_path)

    # 1. Create task with autonomous policy profile
    ctx = controller.start_task(
        conversation_id="conv_auto",
        goal="low-risk autonomous task",
        source_channel="cli",
        kind="respond",
        policy_profile="autonomous",
    )

    task = store.get_task(ctx.task_id)
    assert task is not None and task.status == "running"
    assert task.policy_profile == "autonomous"

    # 2. Verify the attempt is running directly — no parking needed
    attempt = store.get_step_attempt(ctx.step_attempt_id)
    assert attempt is not None and attempt.status == "running"

    # 3. Simulate a low-risk action proceeding without approval:
    #    record a decision that auto-approved based on policy
    decision = store.create_decision(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        decision_type="policy_auto_approve",
        verdict="approved",
        reason="autonomous profile, low-risk action auto-approved by policy engine",
        risk_level="low",
    )
    assert decision.verdict == "approved"
    assert decision.risk_level == "low"

    # 4. Grant capability directly (no approval record needed)
    grant = store.create_capability_grant(
        task_id=ctx.task_id,
        step_id=ctx.step_id,
        step_attempt_id=ctx.step_attempt_id,
        decision_ref=decision.decision_id,
        approval_ref=None,
        policy_ref=None,
        action_class="read_local",
        resource_scope=["/tmp/workspace"],
        constraints={"max_size_bytes": 10_000_000},
        idempotency_key=None,
        expires_at=None,
    )
    assert grant.status == "issued"
    assert grant.approval_ref is None  # no approval needed

    # 5. Complete the task
    controller.finalize_result(
        ctx,
        status="succeeded",
        result_preview="auto task done",
        result_text="low-risk task completed without approval",
    )

    # 6. Verify: no approval records exist for this task
    approvals = store.list_approvals(task_id=ctx.task_id)
    assert len(approvals) == 0

    # Verify: the capability grant is recorded
    grants = store.list_capability_grants(task_id=ctx.task_id)
    assert len(grants) == 1
    assert grants[0].action_class == "read_local"

    # Verify: the decision records the auto-approval
    decisions = store.list_decisions(task_id=ctx.task_id)
    assert len(decisions) == 1
    assert decisions[0].decision_type == "policy_auto_approve"
    assert decisions[0].risk_level == "low"

    # Verify: task completed successfully
    final_task = store.get_task(ctx.task_id)
    assert final_task is not None and final_task.status == "completed"
