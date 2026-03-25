# Report: Spec 10 — Durable Execution Enhancements

## Status: COMPLETE

## Summary

Added heartbeat timeout, super-step checkpointing, replay-from capability, and authorization plan revalidation to strengthen durable execution. All enhancements are opt-in and backward-compatible with existing execution flows.

## Deliverables

### 1. Heartbeat Timeout

- **StepNode field**: Added `heartbeat_interval_seconds: float | None = None` to `StepNode` in `dag_builder.py`. Opt-in per step.
- **StepAttemptRecord field**: Added `last_heartbeat_at: float | None = None` to `StepAttemptRecord` in `records.py`.
- **Schema migration**: Added `_ensure_column("step_attempts", "last_heartbeat_at", "REAL")` in `store.py`.
- **Row mapping**: Added `last_heartbeat_at` extraction in `store_records.py` `_step_attempt_from_row()`.
- **Heartbeat storage in context**: During `StepDAGBuilder.materialize()`, if `node.heartbeat_interval_seconds` is set, it is stored in the attempt context for runtime use.
- **`report_heartbeat(step_attempt_id)`**: New method on `KernelDispatchService` in `dispatch.py`. Updates `last_heartbeat_at` in attempt context via `update_step_attempt`.
- **`check_heartbeat_timeouts()`**: New method on `KernelDispatchService`. Scans running/dispatching/executing attempts with `heartbeat_interval_seconds` in context. If `last_heartbeat_at + interval < now`, fails the attempt with `heartbeat_timeout` reason and triggers retry via `retry_step()` or failure propagation.
- **Integration**: `check_heartbeat_timeouts()` is called each poll cycle in `_loop()`.

Files modified:
- `src/hermit/kernel/task/services/dag_builder.py`
- `src/hermit/kernel/task/models/records.py`
- `src/hermit/kernel/ledger/journal/store.py`
- `src/hermit/kernel/ledger/journal/store_records.py`
- `src/hermit/kernel/execution/coordination/dispatch.py`

### 2. Super-Step Checkpointing

- **`compute_super_steps(dag)`**: New static method on `StepDAGBuilder` that groups DAG nodes by topological depth into super-step levels.
- **`_maybe_emit_super_step_checkpoint()`**: New method on `DAGExecutionService`. Computes topological depth for all steps in a task, checks if all peers at the completed step's depth are in a success status, and emits a `checkpoint.super_step` event.
- **`_compute_depth()`**: New static method on `DAGExecutionService` for recursive topological depth calculation with memoization.
- **Integration**: Called at the start of `_handle_success()` in `DAGExecutionService.advance()`.

Event payload:
```json
{
  "super_step_depth": 0,
  "step_ids": ["step-a", "step-b"],
  "completed_by": "step-b"
}
```

Files modified:
- `src/hermit/kernel/task/services/dag_builder.py`
- `src/hermit/kernel/task/services/dag_execution.py`

### 3. Replay-From

- **New module**: `src/hermit/kernel/execution/recovery/replay.py`
- **`replay_events_until(store, task_id, step_id)`**: Returns all journal events for a task up to and including events referencing the target step. Events are ordered by `event_seq ASC`.
- **`replay_from(store, task_id, step_id)`**: Creates a new branched task from the original. Steps upstream of the target are marked as `"skipped"`. The target step and all downstream steps get fresh `"ready"` attempts with replay metadata. A `replay.started` event is emitted.
- **`_collect_upstream(step_id, step_by_id)`**: BFS helper that collects all strict ancestor step IDs.
- **Dependency rewiring**: Original step IDs are mapped to new step IDs so `depends_on` references remain valid in the branched task.
- **Status handling**: Steps whose all dependencies are upstream (skipped) are created with cleared deps and `"ready"` status to avoid `create_step`'s automatic `"waiting"` override.

Files created:
- `src/hermit/kernel/execution/recovery/replay.py`

### 4. Authorization Plan Revalidation

- **`AuthorizationPlanService.revalidate(plan_id, current_policy_version)`**: New method that checks `check_policy_version` in `revalidation_rules`. Compares stored `policy_version` on the step attempt vs current `POLICY_RULES_VERSION`. If they differ, invalidates the plan with `"policy_version_changed"` gap.
- **Executor gate**: In `ToolExecutor`, before governed execution, if `authorization_plan.revalidation_rules.check_policy_version` is `True`, calls `revalidate()`. On drift, supersedes the attempt with `"authorization_plan_policy_revalidation"` reason.

Files modified:
- `src/hermit/kernel/policy/permits/authorization_plans.py`
- `src/hermit/kernel/execution/executor/executor.py`

### 5. Tests

- **File**: `tests/unit/kernel/test_durable_execution.py`
- **22 tests** across 5 test classes, all passing.

| Test Class | Count | Coverage |
|---|---|---|
| `TestHeartbeat` | 6 | Field presence, opt-in, context storage, heartbeat reporting, timeout failure, dispatch methods |
| `TestSuperStepCheckpoint` | 6 | Single node, linear chain, diamond DAG, parallel roots, checkpoint event emission, incomplete peers |
| `TestReplayFrom` | 5 | Events-until, new task creation, original preservation, nonexistent task/step errors |
| `TestAuthorizationPlanRevalidation` | 4 | No rules noop, unchanged noop, changed invalidates, nonexistent plan |
| `TestStepAttemptHeartbeatField` | 1 | Default None |

## Design Decisions

1. **Heartbeat is opt-in**: Only steps with `heartbeat_interval_seconds` set are monitored. Steps without it are unaffected. This preserves backward compatibility.
2. **Replay creates branches, not overwrites**: New events go into a new task, preserving the original event history for audit and debugging.
3. **Super-step depth is computed dynamically**: Rather than storing depth at creation time, it is computed from the DAG structure at checkpoint time. This handles late DAG mutations.
4. **Revalidation is gate-based**: Policy drift supersedes the attempt rather than silently re-executing, ensuring the operator sees what changed.

## Test Results

```
22 passed in 0.84s
```

All 22 new tests pass. No existing kernel tests were broken.
