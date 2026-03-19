# Spec 10: Durable Execution Enhancements

## Goal
Add heartbeat timeout, super-step checkpointing, and replay-from capability to strengthen durable execution.

## Current Problem
- No heartbeat mechanism — hung steps run until hard deadline
- No checkpoint boundaries between parallel step groups
- No replay-from capability despite having event-sourced journal
- Authorization plan revalidation rules are dead code

## Deliverables
1. **Heartbeat timeout**:
   - Add `heartbeat_interval_seconds` to StepNode (optional)
   - Steps must report progress within interval via `report_heartbeat(step_attempt_id)`
   - Background check: if `last_heartbeat_at + interval < now`, mark attempt as failed with reason "heartbeat_timeout"
   - Create new StepAttempt for retry (not step restart)
2. **Super-step checkpointing**:
   - Group independent parallel steps into "super-steps" (automatic from DAG topology)
   - At super-step boundary (all parallel steps in group complete), emit `checkpoint.super_step` event
   - On recovery, skip completed super-steps entirely
3. **Replay-from**:
   - `replay_from(task_id, step_id)` → read journal events up to step, reconstruct state, re-execute from that point
   - New execution creates new events, does not overwrite existing
   - Useful for debugging and "what if" analysis
4. **Authorization plan revalidation**:
   - Wire `revalidation_rules` on AuthorizationPlanRecord to actual checks
   - Before step execution, if revalidation_rules present, re-evaluate policy
   - If policy changed, re-request approval
5. **Tests** — heartbeat, checkpoint, replay, revalidation

## Files to Modify
- `src/hermit/kernel/task/services/dag_builder.py` (heartbeat, super-steps)
- `src/hermit/kernel/task/services/dag_execution.py` (checkpoint events)
- `src/hermit/kernel/execution/coordination/dispatch.py` (heartbeat check)
- `src/hermit/kernel/execution/executor/executor.py` (revalidation)
- `src/hermit/kernel/policy/permits/authorization_plans.py` (revalidation execution)
- `src/hermit/kernel/ledger/journal/store.py` (replay query)
- `tests/` (new test files)

## Constraints
- Heartbeat is opt-in per step
- Replay creates new branch of events, preserving original history
- Revalidation only triggers if rules specify `check_policy_version: true`
- Must not break existing execution flow
