---
id: recursive-rollback
title: "Recursive rollback with transitive dependency tracking"
priority: normal
trust_zone: low
---

## Goal

Extend the rollback system to support recursive (transitive) rollbacks: when rolling back an action, automatically identify downstream actions that depended on its output and offer to rollback the entire chain. This prevents inconsistent state where a root action is reverted but its dependents remain.

## Steps

1. Create `src/hermit/kernel/verification/rollbacks/dependency_tracker.py`:
   - `RollbackDependencyTracker` class:
     - `trace_dependents(receipt_id, store)` → list of DependentReceipt
       - Starting from the receipt, find all receipts whose input_refs overlap with this receipt's output_refs
       - Recursively trace downstream (BFS/DFS with cycle detection)
       - Return ordered list (leaf → root) for safe rollback ordering
     - `build_rollback_plan(receipt_id, store)` → RollbackPlan
       - Calls trace_dependents() to find the chain
       - Checks rollback_supported for each receipt in chain
       - Marks unsupported ones as "manual_review_required"
       - Returns ordered plan with estimated impact

2. Create `src/hermit/kernel/verification/rollbacks/rollback_models.py`:
   - `DependentReceipt`: receipt_id, tool_name, dependency_type (output_input/artifact_chain), depth
   - `RollbackPlan`: root_receipt_id, chain (ordered list of DependentReceipt), unsupported_count, requires_approval, estimated_impact_summary
   - `RollbackPlanExecution`: plan, executed_rollbacks (list of receipt_ids), failed_rollbacks, skipped_rollbacks

3. Extend RollbackService:
   - Add `execute_recursive(receipt_id, store, approved_plan=None)` method:
     - If no approved_plan, build one and return it for operator review
     - If approved_plan provided, execute rollbacks in dependency order (leaf-first)
     - Each individual rollback uses existing `execute()` method
     - Record the recursive rollback as a single decision event with all receipt_ids
   - Add `preview_recursive(receipt_id, store)` method → returns plan without executing

4. Add rollback plan webhook endpoint:
   - `POST /receipts/{receipt_id}/rollback-plan` — preview recursive rollback plan
   - `POST /receipts/{receipt_id}/rollback-recursive` — execute after approval

5. Write tests in `tests/unit/kernel/test_recursive_rollback.py`:
   - Test trace_dependents finds direct dependents (output_ref → input_ref match)
   - Test trace_dependents handles transitive chains (A → B → C)
   - Test cycle detection prevents infinite loops
   - Test rollback plan orders leaf-first
   - Test unsupported receipts are marked for manual review
   - Test execute_recursive rolls back in correct order
   - Test preview_recursive returns plan without side effects

## Constraints

- Do NOT auto-execute recursive rollbacks — always require operator approval via plan
- Rollback order MUST be leaf-first to avoid orphaned state
- Cycle detection is required (corrupted data could create loops)
- Use `write_file` for ALL file writes
- Do NOT modify the existing `RollbackService.execute()` method signature

## Acceptance Criteria

- [ ] `src/hermit/kernel/verification/rollbacks/dependency_tracker.py` exists
- [ ] `src/hermit/kernel/verification/rollbacks/rollback_models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_recursive_rollback.py -q` passes with >= 7 tests
- [ ] Dependency tracker correctly traces transitive receipt chains

## Context

- RollbackService: `src/hermit/kernel/verification/rollbacks/rollbacks.py`
- Receipt model: ReceiptRecord has input_refs, output_refs (artifact references)
- ProofService: `src/hermit/kernel/verification/proofs/proofs.py`
- Existing rollback strategies: file_restore, git_revert_or_reset, supersede_or_invalidate
