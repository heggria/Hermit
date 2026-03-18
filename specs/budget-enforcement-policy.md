---
id: budget-enforcement-policy
title: "Token and cost budgets enforced at kernel policy level"
priority: normal
trust_zone: low
---

## Goal

Implement kernel-level budget enforcement: each task gets a token/cost budget tracked through receipts and enforced before capability grant issuance. Budget exhaustion blocks further execution.

## Steps

1. Create `src/hermit/kernel/policy/budgets/enforcer.py`:
   - `BudgetEnforcer` class:
     - `assign_budget(task_id, policy_profile, store)` → TaskBudget
     - `check_budget(task_id, estimated_cost, store)` → BudgetCheck
     - `record_usage(task_id, step_attempt_id, tokens_used, cost, store)` → None
     - `request_extension(task_id, additional_tokens, reason)` → ExtensionResult

2. Create `src/hermit/kernel/policy/budgets/models.py`:
   - `TaskBudget`, `BudgetCheck`, `BudgetUsage`, `ExtensionResult`

3. Integrate into PolicyEngine.evaluate() — check budget before standard rules
4. Include budget in proof summary

5. Write tests in `tests/unit/kernel/test_budget_enforcement.py` (>= 8 tests)

## Constraints

- Budget limits are ENFORCED, not advisory
- Extension ALWAYS requires approval
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/policy/budgets/enforcer.py` exists
- [ ] `src/hermit/kernel/policy/budgets/models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_budget_enforcement.py -q` passes with >= 8 tests

## Context

- PolicyEngine: `src/hermit/kernel/policy/evaluators/engine.py`
- ReceiptService: `src/hermit/kernel/verification/receipts/receipts.py`
