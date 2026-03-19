# Report 03: Verification-Driven Scheduling

## Summary

Implemented verification-driven scheduling for the Hermit kernel, making verification results drive scheduling decisions. Verifiers can now block downstream activation, reopen failed branches, and receipts are HMAC-signed.

## Deliverables Completed

### 1. Verification Gate in DAG Activation

- Added `verification_required: bool` field to `StepNode` and `StepRecord`
- Before activating a waiting step, the DAG execution service checks upstream receipt reconciliation status
- If any upstream receipt has `reconciliation_required=True`, the step is blocked with status `verification_blocked` and a `verification.gate_blocked` event is emitted
- Verification gates are opt-in per step (not default), preserving backward compatibility

### 2. Verifier-Driven Reopen

- Added `verifies: list[str]` edge type on `StepNode` and `StepRecord`
- When a verifier step fails, the DAG execution service automatically reopens the verified step by creating a new `StepAttempt` via `retry_step()`
- Original completed attempts are not modified (immutable records)
- `verification.step_invalidated` event is emitted on reopen

### 3. First-Class Edge Types

- `depends_on` (existing) -- data dependency
- `verifies` (new) -- verification relationship; verifier runs after verified step; failure triggers reopen
- `supersedes` (new) -- replacement relationship; superseder runs after superseded step

All edge types are validated during DAG construction:
- References to non-existent keys raise `ValueError`
- Cycles across any edge type are detected via Kahn's algorithm
- Edge types contribute to topological ordering

### 4. Receipt HMAC Signing

- `ReceiptService._compute_signature()` computes HMAC-SHA256 using `HERMIT_PROOF_SIGNING_SECRET` env var
- Signature covers: `receipt_id:task_id:step_id:action_type:result_code`
- Signature is computed after receipt creation and persisted via `update_receipt_signature()`
- No signature is generated when the signing secret is not configured (graceful degradation)

### 5. Schema Migration

- Schema version bumped (integrated with parallel changes at v15+)
- Migration `_migrate_verification_v14` adds three new columns to `steps` table:
  - `verification_required INTEGER NOT NULL DEFAULT 0`
  - `verifies_json TEXT NOT NULL DEFAULT '[]'`
  - `supersedes_json TEXT NOT NULL DEFAULT '[]'`
- `_step_from_row` updated with safe defaults for older schema compatibility

## Files Modified

| File | Change |
|------|--------|
| `src/hermit/kernel/task/services/dag_builder.py` | Added `verification_required`, `verifies`, `supersedes` to `StepNode`; validation of new edge types; cycle detection; materialization passes new fields |
| `src/hermit/kernel/task/services/dag_execution.py` | Verification gate check in `_handle_success`; `reopen_verified_step` method; verifier failure triggers reopen in `advance()` |
| `src/hermit/kernel/verification/receipts/receipts.py` | `_compute_signature` static method (HMAC-SHA256); signature computation and persistence in `issue()` |
| `src/hermit/kernel/task/models/records.py` | Added `verification_required`, `verifies`, `supersedes` fields to `StepRecord` |
| `src/hermit/kernel/ledger/journal/store.py` | Schema migration `_migrate_verification_v14`; new columns in `steps` DDL |
| `src/hermit/kernel/ledger/journal/store_records.py` | Updated `_step_from_row` to read new columns |
| `src/hermit/kernel/ledger/journal/store_tasks.py` | Updated `create_step` to accept and persist new fields |
| `src/hermit/kernel/ledger/events/store_ledger.py` | Added `list_receipts_for_step` and `update_receipt_signature` methods |

## Files Created

| File | Purpose |
|------|---------|
| `tests/unit/kernel/test_verification_driven_scheduling.py` | 23 tests across 6 test classes |

## Test Results

- **23 new tests**: All passing
- **2103 existing kernel tests**: All passing (0 regressions)
- **2 pre-existing failures**: `test_schema_version_bumped_to_11` and `test_kernel_store_accepts_schema_version_5_for_additive_migration` (assert outdated schema version "13"; unrelated to this change)

### Test Coverage by Feature

| Test Class | Tests | Feature |
|------------|-------|---------|
| `TestEdgeTypes` | 8 | Edge type validation, cycle detection, persistence |
| `TestVerificationGate` | 3 | Gate blocking, passing, opt-in behavior |
| `TestVerifierReopen` | 3 | New attempt creation, event emission, immutability |
| `TestReceiptSigning` | 5 | HMAC computation, determinism, absence handling |
| `TestBackwardCompatibility` | 3 | Non-verified DAG unchanged, defaults, failure propagation |
| `TestListReceiptsForStep` | 1 | Step-scoped receipt queries |

## Constraints Satisfied

- Verification gates are opt-in per step (`verification_required=False` default)
- Reopen creates new `StepAttempt`, does not modify existing records
- Non-verified DAG execution is unchanged (23+ backward compatibility assertions)
