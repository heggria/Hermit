# Spec 02: DAG Topology Mutation

## Goal
Enable runtime DAG modification — add/remove/rewire steps during execution. Move from static-graph to topology-adaptive execution.

## Current Problem
- DAGs are fixed at materialization time via `start_dag_task()`
- No way to add steps, skip steps, or rewire dependencies after execution begins
- No conditional branching — all nodes execute unconditionally
- No loop/retry-with-different-strategy support

## Deliverables
1. **DAG mutation API** on KernelStore:
   - `add_step(task_id, step_node) -> step_id` — add a new step to running DAG
   - `skip_step(task_id, step_id, reason)` — mark step as skipped (terminal, won't execute)
   - `rewire_dependency(task_id, step_id, new_depends_on)` — change step dependencies
   - Each mutation emits `dag.topology_changed` event with before/after snapshot
2. **Conditional step nodes**: `ConditionalStepNode` with `predicate` field that evaluates upstream outputs
3. **DAG validation on mutation**: Re-run Kahn's algorithm after each mutation to ensure no cycles
4. **Event sourcing**: All mutations are events in the journal, enabling replay
5. **DAGExecutionService updates**: Handle `skipped` status in activation logic
6. **Tests** — topology mutation + conditional branching + validation

## Files to Modify
- `src/hermit/kernel/task/services/dag_builder.py` (mutation methods)
- `src/hermit/kernel/task/services/dag_execution.py` (handle skipped, conditional)
- `src/hermit/kernel/ledger/journal/store_tasks.py` (mutation storage)
- `src/hermit/kernel/task/models/records.py` (StepRecord additions)
- `tests/` (new test files)

## Constraints
- Mutations must be event-sourced (no silent state changes)
- Must not break existing static DAG execution
- Conditional predicates are simple Python expressions evaluated on step output dicts
