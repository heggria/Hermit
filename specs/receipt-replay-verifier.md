---
id: receipt-replay-verifier
title: "Deterministic replay verification from proof bundles"
priority: normal
trust_zone: low
---

## Goal

Build a receipt replay verifier that reconstructs the execution trace from a proof bundle and verifies that the recorded sequence of policy decisions, capability grants, and receipts forms a valid governed execution. Enables offline auditing without access to the live kernel.

## Steps

1. Create `src/hermit/kernel/verification/replay/verifier.py`:
   - `ReplayVerifier` class:
     - `verify_bundle(proof_json)` → ReplayReport
     - `check_invariants(timeline)` → list[InvariantViolation]
       - INV-1: Every receipt has a preceding decision
       - INV-2: Every receipt has a preceding capability grant
       - INV-3: Every grant has a decision_ref to existing decision
       - INV-4: No grant consumed after expiry
       - INV-5: Hash chain unbroken
       - INV-6: Rollback receipts reference existing forward receipt
       - INV-7: No overlapping grants for same step_attempt_id
     - `reconstruct_timeline(events)` → list[TimelineEntry]

2. Create `src/hermit/kernel/verification/replay/models.py`:
   - `ReplayReport`: task_id, verified_at, total_events, violations, overall_verdict
   - `InvariantViolation`: invariant_id, description, event_refs, severity
   - `TimelineEntry`: event_type, event_id, timestamp, linked_refs

3. Add CLI command `hermit task verify <proof-file>`
4. Add batch verification `verify_all(proof_dir)`

5. Write tests in `tests/unit/kernel/test_replay_verifier.py` (>= 8 tests covering all invariants)

## Constraints

- Must work OFFLINE — no KernelStore access
- Do NOT import runtime modules — standalone verifier
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/verification/replay/verifier.py` exists
- [ ] `src/hermit/kernel/verification/replay/models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_replay_verifier.py -q` passes with >= 8 tests

## Context

- ProofService export: `src/hermit/kernel/verification/proofs/proofs.py`
- Event hash chain: event_hash, prev_event_hash fields
- CapabilityGrant: expires_at, consumed_at, status fields
