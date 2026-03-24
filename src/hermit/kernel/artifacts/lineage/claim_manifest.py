from __future__ import annotations

from typing import Any

CLAIM_ROWS: list[dict[str, Any]] = [
    {
        "id": "ingress_task_first",
        "label": "Every ingress is task-first and durable",
        "profiles": ["core", "governed", "verifiable"],
    },
    {
        "id": "event_backed_truth",
        "label": "Durable truth is event-backed and append-only",
        "profiles": ["core", "governed", "verifiable"],
    },
    {
        "id": "no_tool_bypass",
        "label": "No direct model-to-tool execution bypass",
        "profiles": ["core", "governed", "verifiable"],
    },
    {
        "id": "scoped_authority",
        "label": "Effectful execution uses scoped authority and approval packets",
        "profiles": ["governed", "verifiable"],
    },
    {
        "id": "receipts",
        "label": "Important actions emit receipts",
        "profiles": ["governed", "verifiable"],
    },
    {
        "id": "uncertain_outcome",
        "label": "Uncertain outcomes re-enter via observation or reconciliation",
        "profiles": ["governed", "verifiable"],
    },
    {
        "id": "durable_reentry",
        "label": "Input drift / witness drift / approval drift use durable re-entry",
        "profiles": ["governed", "verifiable"],
    },
    {
        "id": "artifact_context",
        "label": "Artifact-native context is the default runtime path",
        "profiles": ["core", "governed", "verifiable"],
    },
    {
        "id": "memory_evidence",
        "label": "Memory writes are evidence-bound and kernel-backed",
        "profiles": ["core", "governed", "verifiable"],
    },
    {
        "id": "proof_export",
        "label": "Verifiable profile exposes proof coverage and exportable bundles",
        "profiles": ["verifiable"],
    },
    {
        "id": "signed_proofs",
        "label": "Strong signed proofs and inclusion proofs are available when signing is configured",
        "profiles": [],
        "conditional": True,
    },
    {
        "id": "reconciliation_coverage",
        "label": "All consequential action types produce durable reconciliation records",
        "profiles": ["governed", "verifiable"],
    },
    {
        "id": "proof_chain_complete",
        "label": "Proof export reconstructs full contract/evidence/authority/receipt/reconciliation chains",
        "profiles": ["verifiable"],
    },
    {
        "id": "retry_stale_guard",
        "label": "Contract-sensitive retries invalidate stale contract, approval, evidence, and witness state",
        "profiles": ["governed", "verifiable"],
    },
]

PROFILE_LABELS = {
    "core": "Hermit Kernel v0.3 Core",
    "governed": "Hermit Kernel v0.3 Core + Governed",
    "verifiable": "Hermit Kernel v0.3 Core + Governed + Verifiable",
}

# ---------------------------------------------------------------------------
# Module-level integrity guard
# ---------------------------------------------------------------------------
# Catch malformed entries (missing required keys or duplicate IDs) at import
# time rather than silently propagating bad data to callers.
_REQUIRED_KEYS = {"id", "label", "profiles"}
_seen_ids: set[str] = set()
for _row in CLAIM_ROWS:
    _missing = _REQUIRED_KEYS - _row.keys()
    if _missing:
        raise ValueError(f"claim_manifest: row is missing required keys {_missing!r}: {_row!r}")
    if _row["id"] in _seen_ids:
        raise ValueError(f"claim_manifest: duplicate claim id {_row['id']!r}")
    _seen_ids.add(_row["id"])
del _seen_ids, _row  # keep module namespace clean


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_claims_for_profile(profile: str) -> list[dict[str, Any]]:
    """Return all non-conditional claims that apply to *profile*.

    Args:
        profile: One of the keys in :data:`PROFILE_LABELS` (``"core"``,
            ``"governed"``, or ``"verifiable"``).

    Returns:
        A list of claim dicts whose ``profiles`` list includes *profile* and
        whose ``conditional`` flag is not set to ``True``.

    Raises:
        ValueError: If *profile* is not a recognised profile key.
    """
    if profile not in PROFILE_LABELS:
        raise ValueError(
            f"Unknown profile {profile!r}. Valid options are: {sorted(PROFILE_LABELS)}"
        )
    return [
        row
        for row in CLAIM_ROWS
        if profile in row["profiles"] and not row.get("conditional", False)
    ]


def get_claim_by_id(claim_id: str) -> dict[str, Any]:
    """Look up a single claim by its ``id`` field.

    Args:
        claim_id: The ``id`` value of the desired claim row.

    Returns:
        The matching claim dict.

    Raises:
        KeyError: If no claim with the given *claim_id* exists.
    """
    for row in CLAIM_ROWS:
        if row["id"] == claim_id:
            return row
    raise KeyError(f"No claim found with id {claim_id!r}")


__all__ = [
    "CLAIM_ROWS",
    "PROFILE_LABELS",
    "get_claim_by_id",
    "get_claims_for_profile",
]
