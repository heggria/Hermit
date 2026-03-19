# Spec 03: Verification-Driven Scheduling

## Goal
Make verification results drive scheduling decisions — verifier can block downstream activation, reopen failed branches, or insert new verification steps.

## Current Problem
- Receipts/proofs exist but don't influence scheduling
- DAG activation is purely success/failure driven
- No mechanism for a verifier to say "this step succeeded but output quality is insufficient"
- No auto-insertion of verification gates between steps

## Deliverables
1. **Verification gate** in DAG activation:
   - Before activating a waiting step, check upstream receipt reconciliation status
   - If reconciliation flagged issues, block activation and emit `verification.gate_blocked` event
   - Configurable threshold per step: `verification_required: bool` on StepNode
2. **Verifier-driven reopen**:
   - New edge type `verifies` on StepNode — a verification step that checks another step's output
   - If verification fails, verifier can emit `invalidates` intent → mark verified step as needing retry
   - New StepAttempt created for the invalidated step
3. **First-class edge types** on StepNode:
   - `depends_on` (existing) — data dependency
   - `verifies` (new) — verification relationship
   - `supersedes` (new) — this step replaces another
4. **Receipt signing**: Populate `signature` field on ReceiptRecord using HMAC with HERMIT_PROOF_SIGNING_SECRET
5. **Tests** — verification gates, reopen logic, edge types

## Files to Modify
- `src/hermit/kernel/task/services/dag_builder.py` (edge types)
- `src/hermit/kernel/task/services/dag_execution.py` (verification gate, reopen)
- `src/hermit/kernel/verification/receipts/receipts.py` (signing)
- `src/hermit/kernel/task/models/records.py` (StepRecord edge types)
- `src/hermit/kernel/ledger/journal/store_tasks.py` (new events)
- `tests/` (new test files)

## Constraints
- Verification gates are opt-in per step, not default
- Reopen creates new StepAttempt, does not modify existing records
- Must not break non-verified DAG execution
