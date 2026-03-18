---
id: proof-anchoring
title: "External proof anchoring with timestamping and hash publication"
priority: normal
trust_zone: low
---

## Goal

Add external proof anchoring: after a task completes, publish its proof chain hash to an external timestamping service (RFC 3161 or a simple append-only log), producing an anchor receipt that proves the proof existed at a specific time. This enables third-party auditability beyond Hermit's local trust boundary.

## Steps

1. Create `src/hermit/kernel/verification/proofs/anchoring.py`:
   - `ProofAnchor` dataclass: proof_hash, anchor_method, anchor_ref, anchored_at, anchor_payload
   - `AnchorService` class:
     - `anchor_proof(task_id, proof_summary, method="local_log")` → ProofAnchor
       - Computes deterministic hash of the proof summary (canonical JSON → SHA-256)
       - Dispatches to anchor method
     - `verify_anchor(anchor)` → AnchorVerification (valid/invalid/unknown)
       - Re-computes hash and checks against stored anchor

2. Implement anchor methods in `src/hermit/kernel/verification/proofs/anchor_methods.py`:
   - `LocalLogAnchor`: Append hash + timestamp to `~/.hermit/proof-anchors.jsonl`
     - Each line: {"proof_hash": "...", "task_id": "...", "anchored_at": "...", "prev_anchor_hash": "..."}
     - Chain anchors together (prev_anchor_hash) for tamper detection
   - `GitNoteAnchor`: Write proof hash as a git note on HEAD commit
     - `git notes --ref=hermit-proofs add -m <hash>`
     - Verify by reading note back
   - Method selection via config: `proof_anchor_method = "local_log" | "git_note"`

3. Integrate into proof export flow:
   - After `ProofService.build_proof_summary()`, auto-anchor if `proof_anchoring_enabled=true`
   - Store anchor reference in the proof summary payload
   - Add `anchor` field to proof export JSON

4. Add anchor verification endpoint to webhook:
   - `POST /proofs/{task_id}/verify-anchor` — re-verify the anchor for a task's proof

5. Write tests in `tests/unit/kernel/test_proof_anchoring.py`:
   - Test local log anchor writes correct JSONL entry
   - Test anchor chain links (prev_anchor_hash)
   - Test verify_anchor returns valid for correct hash
   - Test verify_anchor returns invalid for tampered hash
   - Test git note anchor writes and reads correctly (mock git)
   - Test proof export includes anchor field when enabled

## Constraints

- Anchoring MUST be opt-in (disabled by default)
- Do NOT require external network access — local_log and git_note are offline-capable
- Anchor verification must be deterministic (canonical JSON hashing)
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/verification/proofs/anchoring.py` exists
- [ ] `src/hermit/kernel/verification/proofs/anchor_methods.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_proof_anchoring.py -q` passes with >= 6 tests
- [ ] Local log anchor produces valid chained JSONL entries

## Context

- ProofService: `src/hermit/kernel/verification/proofs/proofs.py`
- Proof export: `ProofService.export_task_proof()`
- Existing hash chain: event_hash / prev_event_hash in ledger events
- Proof modes: hash_only, hash_chained, signed, signed_with_inclusion_proof
