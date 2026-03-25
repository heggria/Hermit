"""Tests for kernel/verification/proofs/merkle.py — Merkle tree construction and inclusion proofs."""

from __future__ import annotations

from hermit.kernel.ledger.journal.store_support import canonical_json, sha256_hex
from hermit.kernel.verification.proofs.merkle import (
    MISSING_PROOF_FEATURES,
    PROOF_MODE_HASH_CHAINED,
    PROOF_MODE_HASH_ONLY,
    PROOF_MODE_SIGNED,
    PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF,
    build_merkle_inclusion_proofs,
)


class TestConstants:
    def test_proof_mode_hash_only(self) -> None:
        assert PROOF_MODE_HASH_ONLY == "hash_only"

    def test_proof_mode_hash_chained(self) -> None:
        assert PROOF_MODE_HASH_CHAINED == "hash_chained"

    def test_proof_mode_signed(self) -> None:
        assert PROOF_MODE_SIGNED == "signed"

    def test_proof_mode_signed_with_inclusion_proof(self) -> None:
        assert PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF == "signed_with_inclusion_proof"

    def test_missing_proof_features(self) -> None:
        assert MISSING_PROOF_FEATURES == ("signature", "inclusion_proof")


class TestBuildMerkleInclusionProofsEmpty:
    def test_empty_list_returns_none_root(self) -> None:
        result = build_merkle_inclusion_proofs([])
        assert result["root"] is None
        assert result["proofs"] == {}


class TestBuildMerkleInclusionProofsSingleBundle:
    def test_single_bundle_root_equals_leaf_hash(self) -> None:
        bundle = {"receipt_id": "r1", "data": "hello"}
        result = build_merkle_inclusion_proofs([bundle])
        expected_hash = sha256_hex(canonical_json(bundle))
        assert result["root"] == expected_hash

    def test_single_bundle_proof_is_empty_list(self) -> None:
        bundle = {"receipt_id": "r1", "data": "hello"}
        result = build_merkle_inclusion_proofs([bundle])
        assert result["proofs"]["r1"] == []


class TestBuildMerkleInclusionProofsTwoBundles:
    def test_two_bundles_root_is_hash_of_pair(self) -> None:
        b1 = {"receipt_id": "r1", "x": 1}
        b2 = {"receipt_id": "r2", "x": 2}
        result = build_merkle_inclusion_proofs([b1, b2])

        h1 = sha256_hex(canonical_json(b1))
        h2 = sha256_hex(canonical_json(b2))
        expected_root = sha256_hex(canonical_json({"left": h1, "right": h2}))
        assert result["root"] == expected_root

    def test_two_bundles_proofs_contain_sibling(self) -> None:
        b1 = {"receipt_id": "r1", "x": 1}
        b2 = {"receipt_id": "r2", "x": 2}
        result = build_merkle_inclusion_proofs([b1, b2])

        h1 = sha256_hex(canonical_json(b1))
        h2 = sha256_hex(canonical_json(b2))

        # r1 is at index 0 (even), so sibling is on the right
        assert result["proofs"]["r1"] == [{"position": "right", "hash": h2}]
        # r2 is at index 1 (odd), so sibling is on the left
        assert result["proofs"]["r2"] == [{"position": "left", "hash": h1}]


class TestBuildMerkleInclusionProofsThreeBundles:
    """Three bundles: odd count triggers the duplicate-last-node padding."""

    def test_three_bundles_has_valid_root(self) -> None:
        bundles = [
            {"receipt_id": "r1", "v": 1},
            {"receipt_id": "r2", "v": 2},
            {"receipt_id": "r3", "v": 3},
        ]
        result = build_merkle_inclusion_proofs(bundles)
        assert result["root"] is not None
        assert len(result["root"]) == 64  # sha256 hex

    def test_three_bundles_all_receipts_have_proofs(self) -> None:
        bundles = [
            {"receipt_id": "r1", "v": 1},
            {"receipt_id": "r2", "v": 2},
            {"receipt_id": "r3", "v": 3},
        ]
        result = build_merkle_inclusion_proofs(bundles)
        assert set(result["proofs"].keys()) == {"r1", "r2", "r3"}

    def test_three_bundles_proof_depth_is_two(self) -> None:
        bundles = [
            {"receipt_id": "r1", "v": 1},
            {"receipt_id": "r2", "v": 2},
            {"receipt_id": "r3", "v": 3},
        ]
        result = build_merkle_inclusion_proofs(bundles)
        # With 3 leaves → padded to 4 leaves internally → tree depth 2
        for proof in result["proofs"].values():
            assert len(proof) == 2

    def test_odd_padding_duplicates_last_node(self) -> None:
        """The third leaf at index 2 should have its sibling be itself (padding)."""
        bundles = [
            {"receipt_id": "r1", "v": 1},
            {"receipt_id": "r2", "v": 2},
            {"receipt_id": "r3", "v": 3},
        ]
        result = build_merkle_inclusion_proofs(bundles)
        h3 = sha256_hex(canonical_json(bundles[2]))
        # r3 is at index 2 (even), sibling is index 3 which doesn't exist → duplicated
        r3_proof = result["proofs"]["r3"]
        assert r3_proof[0]["hash"] == h3
        assert r3_proof[0]["position"] == "right"


class TestBuildMerkleInclusionProofsFourBundles:
    def test_four_bundles_proof_depth_is_two(self) -> None:
        bundles = [{"receipt_id": f"r{i}", "v": i} for i in range(4)]
        result = build_merkle_inclusion_proofs(bundles)
        for proof in result["proofs"].values():
            assert len(proof) == 2


class TestBuildMerkleInclusionProofsVerification:
    """Verify that inclusion proofs can reconstruct the root."""

    def test_verify_inclusion_proof_two_bundles(self) -> None:
        b1 = {"receipt_id": "r1", "data": "a"}
        b2 = {"receipt_id": "r2", "data": "b"}
        result = build_merkle_inclusion_proofs([b1, b2])
        root = result["root"]

        # Verify r1's proof
        current = sha256_hex(canonical_json(b1))
        for sibling in result["proofs"]["r1"]:
            if sibling["position"] == "right":
                current = sha256_hex(canonical_json({"left": current, "right": sibling["hash"]}))
            else:
                current = sha256_hex(canonical_json({"left": sibling["hash"], "right": current}))
        assert current == root


class TestBuildMerkleInclusionProofsMissingReceiptId:
    def test_bundle_without_receipt_id_uses_empty_string(self) -> None:
        bundle = {"data": "no_id"}
        result = build_merkle_inclusion_proofs([bundle])
        assert "" in result["proofs"]

    def test_bundle_with_none_receipt_id_uses_empty_string(self) -> None:
        bundle = {"receipt_id": None, "data": "none_id"}
        result = build_merkle_inclusion_proofs([bundle])
        assert "" in result["proofs"]
