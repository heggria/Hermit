from __future__ import annotations

import importlib
from typing import Any, Callable

from hermit.kernel.claim_manifest import CLAIM_ROWS, PROFILE_LABELS
from hermit.kernel.proofs import ProofService, proof_capabilities
from hermit.kernel.receipts import ReceiptService
from hermit.kernel.store import KernelStore
from hermit.kernel.store_tasks import KernelTaskStoreMixin


def _implemented(_caps: dict[str, Any]) -> str:
    return "implemented"


def _bool_status(value: bool) -> str:
    return "implemented" if value else "partial"


def _derive_row_status(row_id: str, caps: dict[str, Any]) -> str:
    approvals = importlib.import_module("hermit.kernel.approvals")
    context_compiler = importlib.import_module("hermit.kernel.context_compiler")
    executor = importlib.import_module("hermit.kernel.executor")
    provider_input = importlib.import_module("hermit.kernel.provider_input")
    memory_governance = importlib.import_module("hermit.kernel.memory_governance")
    capability_service = importlib.import_module("hermit.capabilities.service")
    workspace_service = importlib.import_module("hermit.workspaces.service")
    evaluators: dict[str, Callable[[dict[str, Any]], str]] = {
        "ingress_task_first": lambda _caps: _bool_status(
            hasattr(KernelTaskStoreMixin, "create_task")
            and hasattr(KernelTaskStoreMixin, "create_ingress")
        ),
        "event_backed_truth": lambda _caps: _bool_status(
            hasattr(KernelStore, "list_events") and hasattr(KernelStore, "_compute_event_hash")
        ),
        "no_tool_bypass": lambda _caps: _bool_status(
            hasattr(executor.ToolExecutor, "execute")
            and hasattr(provider_input.ProviderInputCompiler, "compile")
        ),
        "scoped_authority": lambda _caps: _bool_status(
            hasattr(approvals.ApprovalService, "request")
            and hasattr(capability_service.CapabilityGrantService, "issue")
            and hasattr(workspace_service.WorkspaceLeaseService, "acquire")
        ),
        "receipts": lambda _caps: _bool_status(
            hasattr(ReceiptService, "issue") and hasattr(ProofService, "ensure_receipt_bundle")
        ),
        "uncertain_outcome": lambda _caps: _bool_status(
            hasattr(executor.ToolExecutor, "_handle_uncertain_outcome")
            and hasattr(executor.ToolExecutor, "_handle_observation_submission")
        ),
        "durable_reentry": lambda _caps: _bool_status(
            hasattr(executor.ToolExecutor, "_supersede_attempt_for_witness_drift")
            and hasattr(executor.ToolExecutor, "persist_suspended_state")
        ),
        "artifact_context": lambda _caps: _bool_status(
            hasattr(context_compiler.ContextCompiler, "compile")
            and hasattr(provider_input.ProviderInputCompiler, "_store_context_pack")
        ),
        "memory_evidence": lambda _caps: _bool_status(
            hasattr(memory_governance.MemoryGovernanceService, "inspect_claim")
        ),
        "proof_export": lambda _caps: _bool_status(
            hasattr(ProofService, "export_task_proof")
            and hasattr(ProofService, "build_proof_summary")
        ),
        "signed_proofs": lambda caps: (
            "implemented" if caps["signing_configured"] else "conditional"
        ),
    }
    return evaluators.get(row_id, _implemented)(caps)


def repository_claim_status() -> dict[str, Any]:
    proof_caps = proof_capabilities()
    rows: list[dict[str, Any]] = []
    blockers_by_profile: dict[str, list[str]] = {profile: [] for profile in PROFILE_LABELS}
    for row in CLAIM_ROWS:
        computed = dict(row)
        status = _derive_row_status(str(row["id"]), proof_caps)
        computed["status"] = status
        rows.append(computed)
        for profile in row.get("profiles", []):
            if status != "implemented":
                blockers_by_profile[str(profile)].append(str(row["id"]))

    profiles = {
        profile: {
            "claimable": not blockers_by_profile[profile],
            "label": PROFILE_LABELS[profile],
            "blockers": list(blockers_by_profile[profile]),
        }
        for profile in PROFILE_LABELS
    }
    repo_blockers = sorted({blocker for items in blockers_by_profile.values() for blocker in items})
    return {
        "rows": rows,
        "profiles": profiles,
        "claimable_profiles": [
            payload["label"] for payload in profiles.values() if payload["claimable"]
        ],
        "blockers": repo_blockers,
        "conditional_capabilities": {
            "signing_configured": proof_caps["signing_configured"],
            "strong_signed_proofs_available": proof_caps["strong_signed_proofs_available"],
            "baseline_verifiable_available": proof_caps["baseline_verifiable_available"],
        },
    }


def task_claim_status(
    store: KernelStore, task_id: str, *, proof_summary: dict[str, Any]
) -> dict[str, Any]:
    repo = repository_claim_status()
    coverage = dict(proof_summary.get("proof_coverage", {}) or {})
    chain = dict(proof_summary.get("chain_verification", {}) or {})
    receipt_bundle = dict(coverage.get("receipt_bundle_coverage", {}) or {})
    signature_coverage = dict(coverage.get("signature_coverage", {}) or {})
    inclusion_coverage = dict(coverage.get("inclusion_proof_coverage", {}) or {})
    verifiable_ready = bool(chain.get("valid")) and (
        int(receipt_bundle.get("bundled_receipts", 0) or 0)
        == int(receipt_bundle.get("total_receipts", 0) or 0)
    )
    strong_mode = proof_summary.get("strongest_export_mode") == "signed_with_inclusion_proof"
    strongest_ready = (
        verifiable_ready
        and strong_mode
        and (
            int(signature_coverage.get("signed_receipts", 0) or 0)
            == int(signature_coverage.get("total_receipts", 0) or 0)
        )
        and (
            int(inclusion_coverage.get("proved_receipts", 0) or 0)
            == int(inclusion_coverage.get("total_receipts", 0) or 0)
        )
    )
    return {
        "task_id": task_id,
        "repository": repo,
        "task_gate": {
            "chain_valid": bool(chain.get("valid")),
            "verifiable_ready": verifiable_ready,
            "strong_verifiable_ready": strongest_ready,
            "proof_mode": proof_summary.get("proof_mode"),
            "strongest_export_mode": proof_summary.get("strongest_export_mode"),
        },
    }


__all__ = ["repository_claim_status", "task_claim_status"]
