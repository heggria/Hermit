# Report: Spec 07 — Memory-Receipt Integration

## Status: COMPLETE

## Changes

### `src/hermit/kernel/context/memory/knowledge.py`
- Added optional `receipt_service` and `artifact_store` parameters to `MemoryRecordService.__init__`
- `promote_from_belief()` now issues a `memory_write` receipt after creating a new memory record:
  - Captures prestate (belief IDs + superseded memory IDs) as a rollback artifact
  - Receipt includes `rollback_supported=True` and `rollback_strategy="supersede_or_invalidate"`
  - Input refs: belief ID; Output refs: new memory ID
- `invalidate()` now issues a `memory_invalidate` receipt before invalidating:
  - Receipt includes `rollback_supported=False` (invalidation is informational)
  - Input refs: memory ID being invalidated
- Both methods are no-ops when `receipt_service` or `artifact_store` is None (backwards compatible)

### `tests/unit/kernel/test_memory_receipt_integration.py` (NEW — 13 tests)
- **TestMemoryWriteReceipt** (5 tests): promote issues receipt, captures prestate artifact, no receipt without service, duplicate skips receipt, superseded records in prestate
- **TestMemoryInvalidateReceipt** (3 tests): invalidate issues receipt, no receipt without service, nonexistent memory skips receipt
- **TestMemoryRollbackIntegration** (1 test): memory_write receipt can be rolled back via RollbackService
- **TestBackwardsCompatibility** (3 tests): promote/invalidate without services, receipt_service only (no artifact_store)
- **TestReceiptProofBundle** (1 test): memory receipt has proof bundle ref

## Verification
- All 13 new tests pass
- Ruff clean
- Backwards compatible: existing code without receipt_service continues to work unchanged
- Rollback path verified: `memory_write` receipts work with existing `RollbackService._apply_rollback()` which already handles the `memory_write` + `supersede_or_invalidate` strategy

## Architecture Notes
- Receipt issuance is opt-in via dependency injection — only callers that provide `receipt_service` and `artifact_store` get receipts
- Prestate artifacts use `kind="prestate.memory_write"` for clear identification
- The existing rollback path in `rollbacks.py:225` already handles `memory_write` action type — no changes needed there
