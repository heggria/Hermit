# Report 02: DAG Topology Mutation

## Status: Completed

## Summary

Implemented runtime DAG modification capabilities -- add, skip, and rewire steps during execution. The kernel now supports topology-adaptive DAG execution with conditional branching, full event sourcing, and cycle detection on every mutation.

## Deliverables Implemented

### 1. DAG Mutation API on StepDAGBuilder

Three mutation methods added to `StepDAGBuilder`:

- **`add_step(task_id, node, ...)`** -- Adds a new step to a running DAG. Resolves dependency keys to step_ids, validates no duplicate keys and no unknown dependencies, delegates cycle detection to `_check_dag_cycles` in the store, creates the step and its initial attempt, and emits a `dag.topology_changed` event.

- **`skip_step(task_id, step_key, reason)`** -- Marks a step as skipped (terminal) by node key. Delegates to `KernelStore.skip_step()` which sets step and attempt statuses, emits an event, and activates downstream dependents.

- **`rewire_dependency(task_id, step_key, new_depends_on_keys)`** -- Changes a step's dependencies at runtime. Resolves keys to step_ids, validates no cycles via Kahn's algorithm on the proposed graph, and delegates to `KernelStore.update_step_depends_on()`.

### 2. Store-Level Mutation Methods (KernelTaskStoreMixin)

- **`skip_step(task_id, step_id, reason)`** -- Atomically updates step status to `skipped`, closes waiting/ready step_attempts, emits `dag.topology_changed` event, and calls `activate_waiting_dependents()` to propagate through the graph.

- **`update_step_depends_on(step_id, task_id, new_depends_on)`** -- Updates `depends_on_json` column, recalculates step status (ready if no deps or all deps terminal-success, waiting otherwise), syncs step_attempt status, and emits `dag.topology_changed` event with before/after snapshot.

### 3. Conditional Step Nodes

- Added `predicate: str | None = None` field to `StepNode` dataclass.
- Added `ConditionalStepNode` subclass (inherits predicate from StepNode).
- Predicates are stored in step_attempt context metadata during materialization.
- `StepDAGBuilder.evaluate_predicate()` -- static method that evaluates a Python expression against upstream output values using restricted `eval()`. Returns False on error or empty predicate.

### 4. DAG Validation on Mutation

- `_validate_no_cycles_for_rewire()` builds the full adjacency graph with proposed changes and runs Kahn's algorithm before committing.
- `add_step()` uses existing `_check_dag_cycles()` in the store via `create_step()`.
- Duplicate key detection on `add_step()`.
- Unknown dependency detection on both `add_step()` and `rewire_dependency()`.

### 5. Event Sourcing

All mutations emit `dag.topology_changed` events with structured payloads:
- `add_step`: `{"mutation": "add_step", "node_key": ..., "depends_on": ..., "step_id": ...}`
- `skip_step`: `{"mutation": "skip_step", "step_id": ..., "reason": ...}`
- `rewire_dependency`: `{"mutation": "rewire_dependency", "step_id": ..., "old_depends_on": ..., "new_depends_on": ..., "new_status": ...}`

### 6. DAGExecutionService Updates

- Added `_evaluate_conditional_steps()` method that checks activated steps for predicates in their context metadata. Steps whose predicates evaluate to False are automatically skipped via `store.skip_step()`.
- Integrated into `_handle_success()` after `activate_waiting_dependents()` and before verification gate checks.

## Files Modified

- `src/hermit/kernel/task/services/dag_builder.py` -- Added `predicate` field, `ConditionalStepNode`, mutation methods (`add_step`, `skip_step`, `rewire_dependency`, `_validate_no_cycles_for_rewire`, `evaluate_predicate`)
- `src/hermit/kernel/task/services/dag_execution.py` -- Added `_evaluate_conditional_steps()`, integrated into `_handle_success()`
- `src/hermit/kernel/ledger/journal/store_tasks.py` -- Added `skip_step()`, `update_step_depends_on()`

## Files Created

- `tests/unit/kernel/test_dag_mutation.py` -- 31 unit tests
- `tests/integration/kernel/test_dag_mutation_integration.py` -- 7 integration tests

## Test Results

- **38 new tests**: 31 unit + 7 integration, all passing
- **37 existing DAG tests**: all passing (no regressions)
- **Total**: 75 DAG-related tests passing

### Test Coverage by Feature

| Feature | Tests |
|---------|-------|
| add_step | 8 (duplicate key, unknown dep, cycle, event, attempt creation, predicate, no-deps ready, with-deps waiting) |
| skip_step | 5 (marks skipped, activates downstream, emits event, nonexistent raises, closes attempts) |
| rewire_dependency | 7 (changes deps, empty deps ready, cycle detection, emits event, nonexistent raises, unknown dep raises, updates attempts) |
| Conditional predicate | 6 (true, false, complex, error, none, metadata storage, ConditionalStepNode) |
| DAG execution with skipped | 2 (downstream activation, task status) |
| Backward compatibility | 2 (static DAG, build_and_materialize signature) |
| Integration lifecycle | 7 (add+execute, skip middle, rewire+execute, event audit trail, multiple mutations, conditional skip, skip chain) |

## Constraints Verified

- All mutations are event-sourced via `dag.topology_changed` events
- Existing static DAG execution is fully backward compatible
- Conditional predicates use restricted eval (no builtins) for safety
- Cycle detection runs on every mutation that modifies the graph topology
