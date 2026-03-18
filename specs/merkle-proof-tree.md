---
id: merkle-proof-tree
title: "Merkle tree proof bundles for efficient partial verification"
priority: normal
trust_zone: low
---

## Goal

Replace flat proof bundles with Merkle tree structures, enabling efficient partial verification: verify any single receipt without replaying the entire chain. Each task's proof becomes a Merkle root hash derived from all receipts, decisions, and grants.

## Steps

1. Create `src/hermit/kernel/verification/proofs/merkle.py`:
   - `MerkleTree` class:
     - `__init__(leaves: list[bytes])` — builds tree from leaf hashes
     - `root` property → bytes (Merkle root)
     - `proof_for(leaf_index)` → MerkleProof (sibling hashes)
     - `verify(leaf_hash, proof, root)` → bool (static)
   - `build_tree_from_receipts(receipts, decisions, grants)` → MerkleTree

2. Create `src/hermit/kernel/verification/proofs/merkle_models.py`:
   - `MerkleProof`: leaf_hash, leaf_index, sibling_hashes, tree_size, root_hash
   - `MerkleProofBundle`: task_id, merkle_root, tree_size, created_at, leaves_summary
   - `MerkleVerification`: valid, leaf_index, root_matched, verification_time_ms

3. Integrate into ProofService — add `build_merkle_proof(task_id)` method
4. Add `GET /proofs/{task_id}/merkle/{receipt_index}` endpoint

5. Write tests in `tests/unit/kernel/test_merkle_proof.py`:
   - Test tree construction produces expected root
   - Test proof_for returns valid proof path
   - Test verify succeeds/fails correctly
   - Test tree with single leaf and odd leaves
   - Test build_tree_from_receipts canonical ordering (>= 8 tests)

## Constraints

- Pure Python (hashlib only, no external crypto)
- Canonical JSON with sorted keys
- Power-of-2 padding (duplicate last leaf for odd counts)
- Use `write_file` for ALL file writes

## Acceptance Criteria

- [ ] `src/hermit/kernel/verification/proofs/merkle.py` exists
- [ ] `src/hermit/kernel/verification/proofs/merkle_models.py` exists
- [ ] `uv run pytest tests/unit/kernel/test_merkle_proof.py -q` passes with >= 8 tests

## Context

- ProofService: `src/hermit/kernel/verification/proofs/proofs.py`
- Receipt model: `src/hermit/kernel/verification/receipts/receipts.py`
