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
# Startup validation
# ---------------------------------------------------------------------------
# Catch structural problems as early as possible — at import time — so that
# a typo in a profile name or a missing required key surfaces immediately
# rather than silently producing wrong behaviour at distant call-sites.

_REQUIRED_KEYS = frozenset({"id", "label", "profiles"})
_KNOWN_PROFILES = frozenset(PROFILE_LABELS)

for _row in CLAIM_ROWS:
    _missing = _REQUIRED_KEYS - _row.keys()
    if _missing:
        raise ValueError(f"claim_manifest: row {_row!r} is missing required key(s): {_missing}")
    _unknown = frozenset(_row["profiles"]) - _KNOWN_PROFILES
    if _unknown:
        raise ValueError(
            f"claim_manifest: claim '{_row['id']}' references unknown profile(s): {_unknown}. "
            f"Known profiles are: {set(_KNOWN_PROFILES)}"
        )

del _row, _missing, _unknown  # keep module namespace tidy


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def get_claim(claim_id: str) -> dict[str, Any]:
    """Return the claim row for *claim_id*.

    Raises ``KeyError`` if no row with that id exists, so callers get a clear
    error instead of silently receiving ``None`` and propagating it downstream.
    """
    for row in CLAIM_ROWS:
        if row["id"] == claim_id:
            return row
    raise KeyError(f"claim_manifest: no claim with id '{claim_id}'")


def claims_for_profile(profile: str) -> list[dict[str, Any]]:
    """Return all claim rows that include *profile* in their profiles list.

    Raises ``KeyError`` for unknown profile names so callers catch typos early.
    """
    if profile not in PROFILE_LABELS:
        raise KeyError(
            f"claim_manifest: unknown profile '{profile}'. "
            f"Known profiles are: {set(PROFILE_LABELS)}"
        )
    return [row for row in CLAIM_ROWS if profile in row["profiles"]]


__all__ = [
    "CLAIM_ROWS",
    "PROFILE_LABELS",
    "claims_for_profile",
    "get_claim",
]
