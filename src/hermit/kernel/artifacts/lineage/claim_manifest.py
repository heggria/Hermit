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


__all__ = ["CLAIM_ROWS", "PROFILE_LABELS"]
