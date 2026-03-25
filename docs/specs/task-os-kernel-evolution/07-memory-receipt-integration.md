# Spec 07: Memory-Receipt Integration

## Goal
Connect memory governance to the receipt/proof chain so memory writes produce governed receipts.

## Current Problem
- Memory governance classifies and scopes memories but doesn't produce receipts
- Memory writes are disconnected from the proof chain
- No rollback support for memory mutations via the receipt system
- Memory promotion lacks receipt evidence

## Deliverables
1. **Receipt generation for memory writes**:
   - When `MemoryGovernance.promote()` creates a MemoryRecord, also issue a ReceiptRecord
   - `action_type: "memory_write"`, `input_refs: [belief_ref]`, `output_refs: [memory_ref]`
   - `rollback_supported: True`, `rollback_strategy: "supersede_or_invalidate"`
2. **Receipt for memory invalidation**:
   - When memory is invalidated, issue receipt with `action_type: "memory_invalidate"`
3. **Wire memory rollback to receipt system**:
   - RollbackService already handles `memory_write` action_type
   - Ensure prestate artifacts are captured before memory promotion
4. **Memory receipts in proof bundles**:
   - Include memory receipts in task proof bundles
5. **Tests** — memory promote with receipt, invalidate with receipt, rollback

## Files to Modify
- `src/hermit/kernel/context/memory/governance.py` (issue receipts)
- `src/hermit/kernel/verification/receipts/receipts.py` (memory receipt support)
- `src/hermit/kernel/verification/rollbacks/rollbacks.py` (verify memory rollback path)
- `tests/` (new test files)

## Constraints
- Memory classification logic unchanged
- Only promoted memories get receipts (volatile beliefs don't)
- Must not slow down memory operations significantly
