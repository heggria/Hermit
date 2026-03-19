"""Merkle-tree helpers for SCITT-style receipt inclusion proofs.

This module is intentionally free of KernelStore / database dependencies so it
can be imported, tested, and reused without any I/O setup.

Public API
----------
build_merkle_inclusion_proofs(receipt_bundles) -> {"root": str|None, "proofs": dict}
"""

from __future__ import annotations

from typing import Any

from hermit.kernel.ledger.journal.store_support import canonical_json as _canonical_json
from hermit.kernel.ledger.journal.store_support import sha256_hex as _sha256_hex

# ---------------------------------------------------------------------------
# Proof-mode constants (single source of truth; re-exported from proofs.py)
# ---------------------------------------------------------------------------

PROOF_MODE_HASH_ONLY: str = "hash_only"
PROOF_MODE_HASH_CHAINED: str = "hash_chained"
PROOF_MODE_SIGNED: str = "signed"
PROOF_MODE_SIGNED_WITH_INCLUSION_PROOF: str = "signed_with_inclusion_proof"
MISSING_PROOF_FEATURES: tuple[str, ...] = ("signature", "inclusion_proof")


# ---------------------------------------------------------------------------
# Merkle tree
# ---------------------------------------------------------------------------


def build_merkle_inclusion_proofs(
    receipt_bundles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a binary Merkle tree over *receipt_bundles* and return inclusion proofs.

    Each bundle is hashed with ``sha256_hex(canonical_json(bundle))``.
    Odd-length levels duplicate the last node (standard RFC-style padding).

    Returns
    -------
    dict with:
        ``root``   – hex Merkle root, or ``None`` if *receipt_bundles* is empty.
        ``proofs`` – mapping of ``receipt_id`` → list of sibling dicts
                     ``{"position": "left"|"right", "hash": str}``.
    """
    if not receipt_bundles:
        return {"root": None, "proofs": {}}

    leaves = [
        {
            "receipt_id": str(bundle.get("receipt_id", "") or ""),
            "hash": _sha256_hex(_canonical_json(bundle)),
        }
        for bundle in receipt_bundles
    ]

    # Build the level list bottom-up (level 0 = leaf hashes).
    levels: list[list[str]] = [[leaf["hash"] for leaf in leaves]]
    while len(levels[-1]) > 1:
        current = levels[-1]
        next_level: list[str] = []
        for index in range(0, len(current), 2):
            left = current[index]
            right = current[index + 1] if index + 1 < len(current) else current[index]
            next_level.append(_sha256_hex(_canonical_json({"left": left, "right": right})))
        levels.append(next_level)

    # Derive inclusion proof (sibling path) for each leaf.
    proofs: dict[str, list[dict[str, Any]]] = {}
    for leaf_index, leaf in enumerate(leaves):
        siblings: list[dict[str, Any]] = []
        index = leaf_index
        for level in levels[:-1]:
            sibling_index = index + 1 if index % 2 == 0 else index - 1
            sibling_hash = level[sibling_index] if sibling_index < len(level) else level[index]
            siblings.append(
                {
                    "position": "right" if index % 2 == 0 else "left",
                    "hash": sibling_hash,
                }
            )
            index //= 2
        proofs[leaf["receipt_id"]] = siblings

    return {"root": levels[-1][0], "proofs": proofs}
