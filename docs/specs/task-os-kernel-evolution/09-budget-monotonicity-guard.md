# Spec 09: Communication Budget & Monotonicity Guard

## Goal
Add kernel-level policy guards for communication budget and coordination tax avoidance.

## Current Problem
- No kernel-level resource budget enforcement (only runtime ExecutionBudget)
- All tasks get equal coordination overhead regardless of complexity
- No mechanism to classify tasks as monotonic (read-only/additive) vs non-monotonic (mutating)
- No token budget tracking at task level

## Deliverables
1. **Task-level budget tracking**:
   - Add `budget_tokens_used`, `budget_tokens_limit` to TaskRecord
   - After each step execution, increment `budget_tokens_used`
   - When limit exceeded, emit `budget.exceeded` event and block task
2. **Monotonicity classification**:
   - Add `monotonicity_class` to StepNode: `readonly`, `additive`, `compensatable_mutation`, `irreversible_mutation`
   - Policy guard: only `compensatable_mutation` and `irreversible_mutation` require approval/locking
   - `readonly` and `additive` steps skip coordination overhead
3. **Communication budget policy guard**:
   - New policy rule: `communication_budget_guard`
   - Evaluates token cost of inter-step communication
   - Emits warning when communication exceeds configurable ratio of total budget
4. **Tests** — budget tracking, monotonicity classification, budget guard

## Files to Modify
- `src/hermit/kernel/task/models/records.py` (budget fields)
- `src/hermit/kernel/policy/guards/rules.py` (new guards)
- `src/hermit/kernel/task/services/dag_builder.py` (monotonicity on StepNode)
- `src/hermit/kernel/execution/executor/executor.py` (budget tracking)
- `tests/` (new test files)

## Constraints
- Budget enforcement is opt-in (no budget = unlimited)
- Monotonicity defaults to `compensatable_mutation` if not specified
- Must not change existing policy evaluation flow
