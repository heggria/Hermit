# Report 09: Communication Budget & Monotonicity Guard

## Status: COMPLETE

## Summary

Implemented kernel-level budget tracking and monotonicity classification as specified in `09-budget-monotonicity-guard.md`. All changes are additive and do not alter existing policy evaluation flow.

## Deliverables

### 1. Task-level budget tracking

- Added `budget_tokens_used: int = 0` and `budget_tokens_limit: int | None = None` to `TaskRecord`
- Added `update_task_budget()` method to `KernelTaskStoreMixin`
- Added budget columns to schema DDL and migration (`_migrate_budget_v17`)
- Schema version bumped from 16 to 17

### 2. Monotonicity classification

- Added `monotonicity_class: str = "compensatable_mutation"` to `StepNode` (frozen dataclass)
- Valid values: `readonly`, `additive`, `compensatable_mutation`, `irreversible_mutation`
- Default is `compensatable_mutation` per spec constraint

### 3. Communication budget policy guard

- New module: `src/hermit/kernel/policy/guards/rules_budget.py`
- `evaluate_monotonicity_guard()`: returns `allow` (skip coordination) for `readonly`/`additive` steps, `None` for mutation steps
  - `readonly` skips both receipt and approval
  - `additive` skips approval but still requires receipt
- `evaluate_communication_budget_guard()`: evaluates token budget
  - Denies when `budget_tokens_used >= budget_tokens_limit`
  - Warns when communication-to-budget ratio exceeds configurable threshold (default 30%)
  - Returns `None` when no limit set (opt-in enforcement)

### 4. Budget tracking in executor

- Added budget increment logic at the end of `ToolExecutor.execute()`
- Token cost = `len(str(tool_input)) + len(str(raw_result))`
- When limit exceeded: emits `budget.exceeded` event and sets task status to `budget_exceeded`
- Only activates when `budget_tokens_limit` is set (opt-in)

## Files Modified

| File | Change |
|------|--------|
| `src/hermit/kernel/task/models/records.py` | Added `budget_tokens_used`, `budget_tokens_limit` to `TaskRecord` |
| `src/hermit/kernel/task/services/dag_builder.py` | Added `monotonicity_class` to `StepNode` |
| `src/hermit/kernel/policy/guards/rules_budget.py` | **New file** -- monotonicity guard + communication budget guard |
| `src/hermit/kernel/execution/executor/executor.py` | Budget tracking after step execution |
| `src/hermit/kernel/ledger/journal/store.py` | Schema v17 migration, budget columns in DDL |
| `src/hermit/kernel/ledger/journal/store_records.py` | `_task_from_row` reads budget columns |
| `src/hermit/kernel/ledger/journal/store_tasks.py` | `update_task_budget()` method |
| `tests/unit/kernel/test_budget_monotonicity_guard.py` | **New file** -- 34 tests |
| `tests/unit/kernel/test_competition_store.py` | Schema version assertion updated |
| `tests/unit/kernel/test_kernel_store_tasks_support.py` | Schema version assertions updated |

## Test Results

- **34 new tests**, all passing
- **100% coverage** on `rules_budget.py`
- **2292 total kernel tests** passing (0 failures)
- Test categories:
  - `TestTaskRecordBudgetFields` (2 tests): default and explicit budget fields
  - `TestStepNodeMonotonicity` (5 tests): monotonicity class on StepNode and DAG
  - `TestMonotonicityGuard` (7 tests): all monotonicity class values
  - `TestCommunicationBudgetGuard` (10 tests): budget exceeded, warnings, custom ratios
  - `TestBudgetTrackingStore` (4 tests): store CRUD for budget fields
  - `TestModuleConstants` (4 tests): constant validity
  - `TestSchemaMigration` (2 tests): column presence and idempotency

## Constraints Verified

- Budget enforcement is opt-in (`budget_tokens_limit = None` means unlimited)
- Monotonicity defaults to `compensatable_mutation`
- Existing policy evaluation flow unchanged (guards are standalone functions, not wired into `evaluate_rules`)
