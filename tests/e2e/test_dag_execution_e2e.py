"""E2E: DAG execution topology — diamond lifecycle, failure cascade, join strategies, and proof.

Exercises the full DAG execution path: task creation with step DAGs, dependency-aware
scheduling, parallel execution, join barriers, failure propagation, batch approvals,
workspace lease mutual exclusion, and DAG proof bundle generation.
"""

from __future__ import annotations

import time
from pathlib import Path

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.coordination.data_flow import StepDataFlowService
from hermit.kernel.execution.coordination.join_barrier import (
    JoinBarrierService,
    JoinStrategy,
)
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.approvals.approvals import ApprovalService
from hermit.kernel.task.services.controller import TaskController
from hermit.kernel.task.services.dag_builder import StepNode
from hermit.kernel.verification.proofs.dag_proof import DAGProofService
from hermit.kernel.verification.proofs.proofs import ProofService


def test_diamond_dag_full_governed_lifecycle(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Diamond DAG: create → parallel execute → join → finalize → proof.

    A (research) → {B (frontend), C (backend)} → D (review)

    Validates:
    - start_dag_task creates correct step states (root=ready, rest=waiting)
    - Dependency activation after step completion
    - Parallel steps become ready simultaneously
    - Join barrier blocks until all deps satisfied
    - Task remains running until all steps complete
    - DAG proof bundle is generated with correct topology
    """
    store, artifacts, controller, executor, workspace = e2e_runtime

    # 1. Create DAG task
    nodes = [
        StepNode(key="research", kind="research", title="Research requirements"),
        StepNode(key="frontend", kind="code", title="Build frontend", depends_on=["research"]),
        StepNode(key="backend", kind="code", title="Build backend", depends_on=["research"]),
        StepNode(
            key="review", kind="review", title="Code review", depends_on=["frontend", "backend"]
        ),
    ]
    ctx, dag, key_map, _root_ctxs = controller.start_dag_task(
        conversation_id="e2e-dag-diamond",
        goal="Build full-stack feature with review",
        source_channel="chat",
        nodes=nodes,
        workspace_root=str(workspace),
    )

    # Verify initial state
    assert dag.roots == ["research"]
    assert dag.leaves == ["review"]
    assert len(key_map) == 4
    assert store.get_step(key_map["research"]).status == "ready"
    assert store.get_step(key_map["frontend"]).status == "waiting"
    assert store.get_step(key_map["backend"]).status == "waiting"
    assert store.get_step(key_map["review"]).status == "waiting"

    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "queued"

    # 2. Claim should only return the root step
    store.update_task_status(ctx.task_id, "running")
    claim = store.claim_next_ready_step_attempt()
    assert claim is not None
    assert claim.step_id == key_map["research"]
    assert store.claim_next_ready_step_attempt() is None  # no other ready step

    # 3. Complete research step → frontend and backend activate
    store.update_step(key_map["research"], status="succeeded", finished_at=time.time())
    store.update_step_attempt(claim.step_attempt_id, status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(ctx.task_id, key_map["research"])
    assert set(activated) == {key_map["frontend"], key_map["backend"]}
    assert store.get_step(key_map["frontend"]).status == "ready"
    assert store.get_step(key_map["backend"]).status == "ready"
    assert store.get_step(key_map["review"]).status == "waiting"

    # 4. Both parallel steps are claimable
    claim_1 = store.claim_next_ready_step_attempt()
    claim_2 = store.claim_next_ready_step_attempt()
    assert claim_1 is not None and claim_2 is not None
    claimed_step_ids = {claim_1.step_id, claim_2.step_id}
    assert claimed_step_ids == {key_map["frontend"], key_map["backend"]}
    assert store.claim_next_ready_step_attempt() is None

    # 5. Complete frontend — review still waiting (backend not done)
    store.update_step(key_map["frontend"], status="succeeded", finished_at=time.time())
    store.update_step_attempt(claim_1.step_attempt_id, status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(ctx.task_id, key_map["frontend"])
    assert key_map["review"] not in activated  # blocked on backend

    # Task still has non-terminal steps
    assert store.has_non_terminal_steps(ctx.task_id) is True

    # 6. Complete backend — review activates
    store.update_step(key_map["backend"], status="succeeded", finished_at=time.time())
    store.update_step_attempt(claim_2.step_attempt_id, status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(ctx.task_id, key_map["backend"])
    assert key_map["review"] in activated
    assert store.get_step(key_map["review"]).status == "ready"

    # 7. Claim and complete review
    claim_review = store.claim_next_ready_step_attempt()
    assert claim_review is not None
    assert claim_review.step_id == key_map["review"]

    # Execute a governed write in the review step
    review_ctx = ctx  # reuse for simplicity (same task)
    executor.execute(review_ctx, "write_file", {"path": "review.md", "content": "LGTM\n"})

    store.update_step(key_map["review"], status="succeeded", finished_at=time.time())
    store.update_step_attempt(
        claim_review.step_attempt_id, status="succeeded", finished_at=time.time()
    )

    # 8. All steps terminal — task can complete
    assert store.has_non_terminal_steps(ctx.task_id) is False

    # 9. Verify event audit trail has DAG-specific events
    events = store.list_events(task_id=ctx.task_id, limit=200)
    event_types = [e["event_type"] for e in events]
    assert "step.dependency_satisfied" in event_types

    dep_events = [e for e in events if e["event_type"] == "step.dependency_satisfied"]
    assert len(dep_events) >= 3  # frontend, backend, review activations

    # 10. Generate DAG proof bundle
    proof_service = ProofService(store, artifacts)
    dag_proof_service = DAGProofService(store, proof_service)
    bundle = dag_proof_service.generate(ctx.task_id)
    assert bundle.task_id == ctx.task_id
    assert len(bundle.root_step_ids) >= 1
    assert len(bundle.leaf_step_ids) >= 1
    assert len(bundle.join_events) >= 1


def test_dag_failure_cascade_all_required(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """When a step fails under all_required strategy, downstream steps cascade-fail.

    A → {B, C} → D
    If B fails, D should cascade-fail. C continues independently.
    """
    store, _artifacts, controller, _executor, _workspace = e2e_runtime

    nodes = [
        StepNode(key="a", kind="execute", title="Init"),
        StepNode(key="b", kind="execute", title="Branch B", depends_on=["a"]),
        StepNode(key="c", kind="execute", title="Branch C", depends_on=["a"]),
        StepNode(key="d", kind="execute", title="Join", depends_on=["b", "c"]),
    ]
    ctx, _dag, key_map, _root_ctxs = controller.start_dag_task(
        conversation_id="e2e-dag-fail-cascade",
        goal="Test failure cascade",
        source_channel="chat",
        nodes=nodes,
    )

    # A succeeds → B, C activate
    store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
    store.activate_waiting_dependents(ctx.task_id, key_map["a"])
    assert store.get_step(key_map["b"]).status == "ready"
    assert store.get_step(key_map["c"]).status == "ready"

    # B fails → D cascade-fails
    store.update_step(key_map["b"], status="failed", finished_at=time.time())
    cascaded = store.propagate_step_failure(ctx.task_id, key_map["b"])
    assert key_map["d"] in cascaded
    assert store.get_step(key_map["d"]).status == "failed"

    # C remains ready (independent branch)
    assert store.get_step(key_map["c"]).status == "ready"

    # Verify via JoinBarrierService
    barrier = JoinBarrierService(store)
    result = barrier.evaluate(ctx.task_id, key_map["d"])
    assert result.strategy == JoinStrategy.ALL_REQUIRED
    assert result.failed >= 1


def test_dag_any_sufficient_join_strategy(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """any_sufficient: one success activates the join node even if others are pending."""
    store, _artifacts, controller, _executor, _workspace = e2e_runtime

    nodes = [
        StepNode(key="a", kind="execute", title="Source A"),
        StepNode(key="b", kind="execute", title="Source B"),
        StepNode(
            key="merge",
            kind="execute",
            title="Merge",
            depends_on=["a", "b"],
            join_strategy="any_sufficient",
        ),
    ]
    ctx, _dag, key_map, _root_ctxs = controller.start_dag_task(
        conversation_id="e2e-dag-any-sufficient",
        goal="Test any_sufficient join",
        source_channel="chat",
        nodes=nodes,
    )

    # A succeeds → merge activates (any_sufficient)
    store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(ctx.task_id, key_map["a"])
    assert key_map["merge"] in activated
    assert store.get_step(key_map["merge"]).status == "ready"


def test_dag_best_effort_join_strategy(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """best_effort: join activates once all deps are terminal, regardless of success/failure."""
    store, _artifacts, controller, _executor, _workspace = e2e_runtime

    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B"),
        StepNode(
            key="summary",
            kind="execute",
            title="Summary",
            depends_on=["a", "b"],
            join_strategy="best_effort",
        ),
    ]
    ctx, _dag, key_map, _root_ctxs = controller.start_dag_task(
        conversation_id="e2e-dag-best-effort",
        goal="Test best_effort join",
        source_channel="chat",
        nodes=nodes,
    )

    # A fails — summary still waiting (B not terminal)
    store.update_step(key_map["a"], status="failed", finished_at=time.time())
    activated = store.activate_waiting_dependents(ctx.task_id, key_map["a"])
    assert key_map["summary"] not in activated

    # B succeeds — all terminal → summary activates
    store.update_step(key_map["b"], status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(ctx.task_id, key_map["b"])
    assert key_map["summary"] in activated


def test_dag_finalize_result_integrates_with_controller(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """controller.finalize_result() auto-activates dependents and protects task completion."""
    store, _artifacts, controller, _executor, workspace = e2e_runtime

    nodes = [
        StepNode(key="a", kind="respond", title="First step"),
        StepNode(key="b", kind="respond", title="Second step", depends_on=["a"]),
    ]
    ctx, _dag, key_map, _root_ctxs = controller.start_dag_task(
        conversation_id="e2e-dag-finalize",
        goal="Test finalize integration",
        source_channel="chat",
        nodes=nodes,
    )
    store.update_task_status(ctx.task_id, "running")

    # Claim step A
    attempt_a = store.claim_next_ready_step_attempt()
    assert attempt_a is not None
    assert attempt_a.step_id == key_map["a"]

    # Build ctx for step A
    from hermit.kernel.context.models.context import TaskExecutionContext

    ctx_a = TaskExecutionContext(
        conversation_id="e2e-dag-finalize",
        task_id=ctx.task_id,
        step_id=key_map["a"],
        step_attempt_id=attempt_a.step_attempt_id,
        source_channel="chat",
        policy_profile="default",
        workspace_root=str(workspace),
    )

    # finalize_result for A should activate B and keep task running
    controller.finalize_result(ctx_a, status="succeeded", result_text="Step A done")

    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "running"  # not completed — B still waiting/ready
    assert store.get_step(key_map["b"]).status == "ready"

    # Claim and finalize step B
    attempt_b = store.claim_next_ready_step_attempt()
    assert attempt_b is not None
    assert attempt_b.step_id == key_map["b"]

    ctx_b = TaskExecutionContext(
        conversation_id="e2e-dag-finalize",
        task_id=ctx.task_id,
        step_id=key_map["b"],
        step_attempt_id=attempt_b.step_attempt_id,
        source_channel="chat",
        policy_profile="default",
        workspace_root=str(workspace),
    )
    controller.finalize_result(ctx_b, status="succeeded", result_text="Step B done")

    # Now task should be completed
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "completed"


def test_dag_batch_approval_for_parallel_steps(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Batch approvals: create correlated approvals for parallel DAG steps and approve as batch."""
    store, _artifacts, controller, _executor, _workspace = e2e_runtime

    nodes = [
        StepNode(key="root", kind="execute", title="Root"),
        StepNode(key="w1", kind="execute", title="Worker 1", depends_on=["root"]),
        StepNode(key="w2", kind="execute", title="Worker 2", depends_on=["root"]),
    ]
    ctx, _dag, key_map, _root_ctxs = controller.start_dag_task(
        conversation_id="e2e-dag-batch-approval",
        goal="Test batch approvals",
        source_channel="chat",
        nodes=nodes,
    )

    # Activate workers
    store.update_step(key_map["root"], status="succeeded", finished_at=time.time())
    store.activate_waiting_dependents(ctx.task_id, key_map["root"])

    # Create step attempts for workers
    att_w1 = store.create_step_attempt(task_id=ctx.task_id, step_id=key_map["w1"], status="running")
    att_w2 = store.create_step_attempt(task_id=ctx.task_id, step_id=key_map["w2"], status="running")

    # Request batch approval
    approval_service = ApprovalService(store)
    approval_ids = approval_service.request_batch(
        task_id=ctx.task_id,
        approval_requests=[
            {
                "step_id": key_map["w1"],
                "step_attempt_id": att_w1.step_attempt_id,
                "approval_type": "tool_use",
                "requested_action": {"tool": "write_file"},
                "request_packet_ref": None,
            },
            {
                "step_id": key_map["w2"],
                "step_attempt_id": att_w2.step_attempt_id,
                "approval_type": "tool_use",
                "requested_action": {"tool": "bash"},
                "request_packet_ref": None,
            },
        ],
        batch_reason="parallel worker approvals",
    )
    assert len(approval_ids) == 2

    # Find batch_id
    approval = store.get_approval(approval_ids[0])
    batch_id = approval.resolution.get("batch_id")
    assert batch_id is not None

    # Batch approve
    approved = approval_service.approve_batch(batch_id, resolved_by="operator")
    assert len(approved) == 2


def test_dag_data_flow_input_bindings(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """input_bindings resolve upstream step output_ref into downstream context."""
    store, _artifacts, controller, _executor, _workspace = e2e_runtime

    nodes = [
        StepNode(key="producer", kind="execute", title="Produce data"),
        StepNode(
            key="consumer",
            kind="execute",
            title="Consume data",
            depends_on=["producer"],
            input_bindings={"data_ref": "producer.output_ref"},
        ),
    ]
    ctx, _dag, key_map, _root_ctxs = controller.start_dag_task(
        conversation_id="e2e-dag-data-flow",
        goal="Test data flow",
        source_channel="chat",
        nodes=nodes,
    )

    # Simulate producer completing with an output artifact
    store.update_step(
        key_map["producer"],
        status="succeeded",
        output_ref="artifact_abc123",
        finished_at=time.time(),
    )
    store.activate_waiting_dependents(ctx.task_id, key_map["producer"])

    # Resolve bindings for consumer
    # Need key→step_id mapping where keys are the original StepNode keys
    # The input_bindings reference "producer" key, but step depends_on uses step_ids
    # We stored the step_id for "producer" key — use that mapping
    key_to_sid = {"producer": key_map["producer"], "consumer": key_map["consumer"]}
    data_flow = StepDataFlowService(store)
    resolved = data_flow.resolve_inputs(ctx.task_id, key_map["consumer"], key_to_sid)
    assert resolved == {"data_ref": "artifact_abc123"}

    # Inject into step attempt
    consumer_attempts = store.list_step_attempts(step_id=key_map["consumer"], limit=1)
    assert len(consumer_attempts) >= 1
    data_flow.inject_resolved_inputs(consumer_attempts[0].step_attempt_id, resolved)

    updated = store.get_step_attempt(consumer_attempts[0].step_attempt_id)
    assert updated is not None
    assert updated.context.get("resolved_inputs") == {"data_ref": "artifact_abc123"}


def test_dag_complex_topology(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Complex DAG: A → {B, C}, B → D, C → {D, E}, D → F.

    Verifies correct activation order and that multi-parent joins work.
    """
    store, _artifacts, controller, _executor, _workspace = e2e_runtime

    nodes = [
        StepNode(key="a", kind="execute", title="A"),
        StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
        StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
        StepNode(key="d", kind="execute", title="D", depends_on=["b", "c"]),
        StepNode(key="e", kind="execute", title="E", depends_on=["c"]),
        StepNode(key="f", kind="execute", title="F", depends_on=["d"]),
    ]
    ctx, _dag, key_map, _root_ctxs = controller.start_dag_task(
        conversation_id="e2e-dag-complex",
        goal="Test complex DAG topology",
        source_channel="chat",
        nodes=nodes,
    )

    # Only A is ready
    assert store.get_step(key_map["a"]).status == "ready"
    for k in ["b", "c", "d", "e", "f"]:
        assert store.get_step(key_map[k]).status == "waiting"

    # A → succeeded → B, C activate
    store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(ctx.task_id, key_map["a"])
    assert set(activated) == {key_map["b"], key_map["c"]}

    # B → succeeded → D still waiting (needs C too)
    store.update_step(key_map["b"], status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(ctx.task_id, key_map["b"])
    assert key_map["d"] not in activated

    # C → succeeded → D and E activate
    store.update_step(key_map["c"], status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(ctx.task_id, key_map["c"])
    assert key_map["d"] in activated
    assert key_map["e"] in activated

    # D → succeeded → F activates
    store.update_step(key_map["d"], status="succeeded", finished_at=time.time())
    activated = store.activate_waiting_dependents(ctx.task_id, key_map["d"])
    assert key_map["f"] in activated

    # E → succeeded
    store.update_step(key_map["e"], status="succeeded", finished_at=time.time())

    # F → succeeded → all terminal
    store.update_step(key_map["f"], status="succeeded", finished_at=time.time())
    assert not store.has_non_terminal_steps(ctx.task_id)


def test_backward_compat_linear_task_unaffected(
    e2e_runtime: tuple[KernelStore, ArtifactStore, TaskController, ToolExecutor, Path],
) -> None:
    """Existing linear tasks (no depends_on) continue to work identically."""
    store, _artifacts, controller, _executor, workspace = e2e_runtime

    ctx = controller.start_task(
        conversation_id="e2e-dag-compat",
        goal="Normal linear task",
        source_channel="chat",
        kind="respond",
        workspace_root=str(workspace),
    )
    step = store.get_step(ctx.step_id)
    assert step is not None
    assert step.depends_on == []
    assert step.join_strategy == "all_required"
    assert step.input_bindings == {}
    assert step.status == "running"

    controller.finalize_result(ctx, status="succeeded", result_text="Done")
    task = store.get_task(ctx.task_id)
    assert task is not None
    assert task.status == "completed"
