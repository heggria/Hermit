# Test Quality Review — Task OS Kernel Evolution

**Reviewer**: Claude Sonnet 4.6 (code-reviewer agent)
**Date**: 2026-03-19
**Scope**: All new test files introduced by the task-os-kernel-evolution spec series

---

## Summary

All 384 tests across the 11 reviewed files pass. The overall quality is high: tests are well-structured, cover the happy path thoroughly, verify event emission, and enforce error contracts. A small number of issues are catalogued below, ranging from one tautological assertion to repeated private-API access patterns that couple tests to implementation internals.

---

## Test Run Results

| File | Collected | Result |
|---|---|---|
| `tests/unit/kernel/test_formal_state_machine.py` | 107 | PASS |
| `tests/unit/kernel/test_dag_mutation.py` | 31 | PASS |
| `tests/integration/kernel/test_dag_mutation_integration.py` | 7 | PASS |
| `tests/unit/kernel/test_verification_driven_scheduling.py` | 23 | PASS |
| `tests/unit/kernel/authority/test_workspace_lifecycle.py` | 42 | PASS |
| `tests/unit/kernel/test_observation_durability.py` | 27 | PASS |
| `tests/unit/kernel/test_approval_orchestration.py` | 32 | PASS |
| `tests/unit/kernel/test_blackboard.py` | 46 | PASS |
| `tests/unit/kernel/test_budget_monotonicity_guard.py` | 34 | PASS |
| `tests/unit/kernel/test_durable_execution.py` | 22 | PASS |
| `tests/unit/kernel/test_memory_receipt_integration.py` | 13 | PASS |
| **Total** | **384** | **384 passed, 0 failed** |

---

## Issues Found

### CRITICAL — None

No security-sensitive issues, bare excepts, swallowed exceptions, or SQL injection paths were found in the test suite.

---

### HIGH — Tautological Assertion (Always Passes)

**File**: `tests/unit/kernel/test_memory_receipt_integration.py:397`

```python
assert receipt.receipt_bundle_ref is not None or True  # bundle may be optional
```

This assertion is always `True` regardless of the value of `receipt.receipt_bundle_ref`. The comment acknowledges ambiguity about whether `receipt_bundle_ref` is actually required. The result is a test that passes even when the feature is completely absent. Either:
- Assert the field is present: `assert receipt.receipt_bundle_ref is not None`
- Or remove the assertion and add a clarifying comment if this path is intentionally untested

**Class**: `TestReceiptProofBundle.test_memory_receipt_has_proof_bundle`

---

### MEDIUM — Private API Access in Unit Tests

**File**: `tests/unit/kernel/authority/test_workspace_lifecycle.py:432,530,563,593,597`

Several tests directly access `svc._queue` and call `svc._process_queue(...)`:

```python
queue = svc._queue["ws-1"]
result = svc._process_queue("ws-1")
entries = svc._queue.get("ws-1", [])
```

This couples the tests to the internal in-memory queue representation. If `WorkspaceLeaseService` changes its internal queue structure (e.g., to a deque or an external store), these tests will break even if the observable behavior is unchanged. The `TestWorkspaceLeaseServiceReleaseWithQueue.test_process_queue_*` tests in particular are testing implementation internals rather than the public contract.

The FIFO ordering test (`test_queue_fifo_order`) introspects `svc._queue["ws-1"]` directly. If FIFO is a behavioral guarantee, it should be observable through the public `queue_position()` method or through the ordering of leases that get granted.

**Recommendation**: Extract the `_process_queue` tests to test observable outcomes (i.e., which lease gets granted after release) rather than internal queue state.

---

### MEDIUM — Private API Access for Schema and Migration Testing

**File**: `tests/unit/kernel/test_budget_monotonicity_guard.py:358,404,413,416`

Three tests use `store._get_conn()` and `store._migrate_budget_v17()`:

```python
store._get_conn().execute(
    "UPDATE tasks SET budget_tokens_limit = ? WHERE task_id = ?", ...)

store._migrate_budget_v17()
cols = {row[1] for row in store._get_conn()
    .execute("PRAGMA table_info(tasks)").fetchall()}
```

The use of `store._get_conn()` for raw SQL is understandable given that `create_task` does not expose `budget_tokens_limit` as a parameter yet. The comment in the test acknowledges this: `# Set budget limit directly since create_task doesn't expose it yet`. This is an acceptable temporary measure, but the comment should include a TODO to remove it once the public API is extended.

Calling `store._migrate_budget_v17()` directly is reasonable for the idempotency test, since there is no other way to trigger a specific migration. Mark with a `# noqa: SLF001` if this triggers a linter, or document the intent explicitly.

---

### MEDIUM — Unused Variable Assignments in Test Setup

**File**: `tests/unit/kernel/test_verification_driven_scheduling.py:180,238`

```python
step_a = store.get_step(key_map["a"])
# step_a is never used after assignment in these two test methods
attempt_a = store.list_step_attempts(step_id=key_map["a"], limit=1)[0]
...
```

In `TestVerificationGate.test_gate_blocks_on_reconciliation_required` (line 180) and `test_gate_passes_when_no_issues` (line 238), `step_a` is assigned but never referenced again. The test proceeds to use `attempt_a` and `key_map["a"]` directly. This is a minor clarity issue — remove the unused variable to reduce noise.

---

### MEDIUM — Missing Error-Path Coverage in Three Files

**Files**:
- `tests/unit/kernel/test_observation_durability.py` — 0 `pytest.raises` calls
- `tests/unit/kernel/test_approval_orchestration.py` — 0 `pytest.raises` calls
- `tests/unit/kernel/test_memory_receipt_integration.py` — 0 `pytest.raises` calls

`test_approval_orchestration.py` tests policy resolution, timeout service, and delegation checks thoroughly on the happy path, but none of the 32 tests exercise what happens when `resolve_approval` raises, or when the store is unavailable. The `ApprovalTimeoutService.check_expired` method iterates across approvals that may raise — there is no test for partial-failure resilience.

`test_observation_durability.py` exercises lifecycle operations (create, update, resolve, timeout, list) but does not assert what happens when `create_observation_ticket` is called with a non-existent `step_attempt_id` or when `resolve_observation` is called on an already-resolved ticket.

`test_memory_receipt_integration.py` tests `_issue_memory_invalidate_receipt("nonexistent-id")` (line 290) to confirm it returns `None`, which is good. But there is no test for what happens when the `artifact_store` is unavailable during `promote_from_belief`.

---

### MEDIUM — Implementation Testing via `hasattr` Check

**File**: `tests/unit/kernel/test_durable_execution.py:148-152`

```python
def test_dispatch_heartbeat_methods_exist(self) -> None:
    from hermit.kernel.execution.coordination.dispatch import KernelDispatchService
    assert hasattr(KernelDispatchService, "report_heartbeat")
    assert hasattr(KernelDispatchService, "check_heartbeat_timeouts")
```

This test verifies that methods exist on a class, which is testing interface shape rather than behavior. A method named `report_heartbeat` that silently does nothing would pass this test. The behavior of `report_heartbeat` and `check_heartbeat_timeouts` is tested indirectly in `TestHeartbeat.test_heartbeat_timeout_marks_failed` (which simulates what the dispatch method does), but there is no test that calls the methods themselves on a live service. Consider replacing the `hasattr` check with a behavioral test that calls `KernelDispatchService.report_heartbeat(...)` on a minimal mock runner.

---

### LOW — Repeated `_make_task` Helper Across Five Files

**Files**:
- `tests/unit/kernel/test_dag_mutation.py`
- `tests/integration/kernel/test_dag_mutation_integration.py`
- `tests/unit/kernel/test_verification_driven_scheduling.py`
- `tests/unit/kernel/test_durable_execution.py`
- `tests/unit/kernel/test_budget_monotonicity_guard.py`

All five files define an identical module-level `_make_task(store: KernelStore) -> str` function with the same body. This is not an isolation problem (each file is self-contained), but a maintainability concern — if `KernelStore.create_task` changes its required parameters, all five copies must be updated.

Consider adding a shared `conftest.py` fixture in `tests/unit/kernel/` that provides a `make_task` function, or a `task_in_store` fixture.

---

### LOW — `_mk_task` Missing Return Type Annotation

**File**: `tests/unit/kernel/test_observation_durability.py:22`

```python
def _mk_task(store: KernelStore, **kwargs: Any):
```

The return type annotation is missing. All other helper functions in the reviewed files are fully typed. Should be:

```python
def _mk_task(store: KernelStore, **kwargs: Any) -> TaskRecord:
```

---

### LOW — `task_id` Fixture Skips `ensure_conversation`

**File**: `tests/unit/kernel/test_blackboard.py:28-40`

The `task_id` fixture calls `store.create_task(conversation_id="conv_test", ...)` without first calling `store.ensure_conversation("conv_test", ...)`. The `tasks` table stores `conversation_id` as a plain `TEXT` column without a foreign key to `conversations`, so this works at runtime. However, it creates an orphaned conversation reference that is inconsistent with the production write path, which always calls `ensure_conversation` first.

This is unlikely to cause test failures but could mask problems if FK enforcement is ever tightened. The pattern used in `test_dag_mutation.py` — calling `store.ensure_conversation("conv_1", source_channel="test")` before `store.create_task(...)` — is the correct pattern.

---

## Positive Findings

The following quality signals are notably strong across the reviewed files:

**Isolation**: Every test that touches the database uses either `tmp_path / "state.db"` (file-scoped temporary path, function-isolated by pytest) or `KernelStore(Path(":memory:"))` (in-memory). No shared mutable state leaks between tests.

**Event sourcing coverage**: Tests consistently assert not just that state changed, but that the corresponding event was emitted (`dag.topology_changed`, `workspace.lease_extended`, `observation.created`, etc.). This is a strong behavioral signal rather than implementation testing.

**Error contract completeness**: `test_dag_mutation.py`, `test_formal_state_machine.py`, `test_blackboard.py`, and `test_workspace_lifecycle.py` all exercise the error paths (`pytest.raises`) alongside the success paths. The state machine tests in particular enumerate both valid and invalid transitions exhaustively.

**Backward-compatibility sections**: Seven of the eleven files include explicit backward-compatibility classes or tests (`TestBackwardCompatibility`, `TestExistingDAGUnbroken`, `TestBackwardsCompatibility`). This directly validates that new kernel features do not regress existing behavior.

**Parametrize usage**: `test_formal_state_machine.py` uses `@pytest.mark.parametrize` extensively for transition tables (21 valid task transitions, 10 invalid, 27 valid attempt transitions, 9 invalid), which gives exhaustive coverage without test bloat.

**Fixture-based mocking in workspace tests**: `test_workspace_lifecycle.py` uses `MagicMock` and `SimpleNamespace` cleanly with `_make_service()` and `_make_lease()` helpers rather than patching global state, making failure diagnosis straightforward.

**Integration test design**: The 7 integration tests in `test_dag_mutation_integration.py` are genuinely end-to-end (create task, build DAG, mutate, execute, verify terminal status), not just unit tests moved to an integration file.

**Restart recovery testing**: `test_observation_durability.py` simulates a restart by opening a second `KernelStore` on the same database path and verifying that active tickets are recovered. This is a high-value behavioral test that catches real-world restart-recovery bugs.

---

## Coverage Assessment by Feature

| Spec | File | Happy Path | Error Paths | Edge Cases | Events | Backward Compat |
|---|---|---|---|---|---|---|
| 01 Formal State Machine | test_formal_state_machine.py | Strong | Strong | Strong (self-transitions, bogus states) | N/A | Strong |
| 02 DAG Mutation | test_dag_mutation.py | Strong | Strong | Moderate | Strong | Strong |
| 02 DAG Mutation (integration) | test_dag_mutation_integration.py | Strong | None | Moderate | Strong | N/A |
| 03 Verification Scheduling | test_verification_driven_scheduling.py | Strong | Moderate | Moderate | Strong | Strong |
| 04 Workspace Lifecycle | test_workspace_lifecycle.py | Strong | Strong | Strong (FIFO, expired leases) | Strong | N/A |
| 05 Observation Durability | test_observation_durability.py | Strong | **Weak** | Moderate (restart recovery) | Strong | N/A |
| 06 Approval Orchestration | test_approval_orchestration.py | Strong | **Weak** | Moderate | Strong | N/A |
| 08 Typed Blackboard | test_blackboard.py | Strong | Strong | Strong (boundaries, task scoping) | Strong | N/A |
| 09 Budget Monotonicity Guard | test_budget_monotonicity_guard.py | Strong | Moderate | Strong (boundary ratios, disjointness) | N/A | N/A |
| 10 Durable Execution | test_durable_execution.py | Strong | Moderate | Moderate | Strong | N/A |
| 07 Memory-Receipt Integration | test_memory_receipt_integration.py | Strong | **Weak** | Moderate | N/A | Strong |

---

## Verdict

**Approve with warnings (MEDIUM issues, no CRITICAL or HIGH blocking issues)**

The test suite is production-quality overall. The 384 tests all pass, isolation is enforced, event emission is verified, and backward compatibility is explicitly validated. The one tautological assertion in `test_memory_receipt_integration.py` is the most actionable fix. The `_process_queue` and `_queue` internal access patterns are the next priority to address.

### Action Items (Priority Order)

1. Fix the tautological `assert receipt.receipt_bundle_ref is not None or True` — decide if the field is required and assert accordingly.
2. Remove unused `step_a` variable assignments at lines 180 and 238 of `test_verification_driven_scheduling.py`.
3. Add error-path tests to `test_observation_durability.py`, `test_approval_orchestration.py`, and `test_memory_receipt_integration.py`.
4. Replace the `hasattr` existence test in `test_durable_execution.py` with a behavioral call test.
5. Refactor `test_workspace_lifecycle.py` queue tests to use observable behavior rather than `svc._queue` and `svc._process_queue`.
6. Extract `_make_task` into a shared conftest fixture to eliminate the five-way duplication.
7. Add return type annotation to `_mk_task` in `test_observation_durability.py`.
8. Add `store.ensure_conversation(...)` before `store.create_task(...)` in the `task_id` fixture of `test_blackboard.py`.
