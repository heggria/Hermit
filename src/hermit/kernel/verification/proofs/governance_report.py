from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .formatter import _truncate


@dataclass
class GovernanceEvents:
    """Classified governance events extracted from a proof bundle."""

    denied: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    allowed_with_receipt: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    rollback_capable: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    boundary_violations_prevented: int = 0
    total_governed_actions: int = 0


def extract_governance_events(proof: dict[str, Any]) -> GovernanceEvents:
    """Extract and classify governance events from a proof bundle dict."""
    events = GovernanceEvents()

    # Classify receipt bundles by result_code
    receipt_bundles = proof.get("receipt_bundles", [])
    for bundle in receipt_bundles:
        result_code = str(bundle.get("result_code", ""))
        events.total_governed_actions += 1

        if result_code in ("denied", "dispatch_denied"):
            events.denied.append(bundle)
            events.boundary_violations_prevented += 1
        elif result_code == "succeeded":
            events.allowed_with_receipt.append(bundle)

        if bundle.get("rollback_supported"):
            events.rollback_capable.append(bundle)

    # Also check decision_refs for deny decisions that terminated before receipt issuance.
    # These are policy denials that never produced a receipt bundle — count them as prevented
    # boundary violations. We track them via decision_refs count minus receipt-linked decisions.
    decision_refs = proof.get("decision_refs", [])
    receipt_decision_refs = {
        str(bundle.get("decision_ref", ""))
        for bundle in receipt_bundles
        if bundle.get("decision_ref")
    }
    unlinked_decision_count = sum(
        1 for ref in decision_refs if ref and ref not in receipt_decision_refs
    )
    events.boundary_violations_prevented += unlinked_decision_count
    events.total_governed_actions += unlinked_decision_count

    # Check authorization_plans for policy-blocked actions (denials that occur before any
    # receipt is issued, captured as blocked auth plans with "policy_denied" in gaps).
    auth_plans = proof.get("authorization_plans", [])
    for plan in auth_plans:
        if str(plan.get("status", "")) == "blocked" and "policy_denied" in list(
            plan.get("current_gaps", [])
        ):
            action_classes = list(plan.get("requested_action_classes", []))
            action_type = action_classes[0] if action_classes else "unknown"
            synthetic = {
                "action_type": action_type,
                "result_code": "policy_denied",
                "risk_level": "critical",
                "authorization_plan_id": plan.get("authorization_plan_id"),
                "contract_ref": plan.get("contract_ref"),
            }
            events.denied.append(synthetic)
            events.boundary_violations_prevented += 1
            events.total_governed_actions += 1

    # Check execution_contracts for abandoned contracts (actions denied before execution).
    contract_refs_from_auth = {
        str(plan.get("contract_ref", "")) for plan in auth_plans if plan.get("contract_ref")
    }
    contracts = proof.get("execution_contracts", [])
    for contract in contracts:
        contract_id = str(contract.get("contract_id", ""))
        if (
            str(contract.get("status", "")) == "abandoned"
            and contract_id not in contract_refs_from_auth
        ):
            synthetic = {
                "action_type": str(contract.get("objective", "unknown")),
                "result_code": "contract_abandoned",
                "risk_level": "high",
                "contract_id": contract_id,
            }
            events.denied.append(synthetic)
            events.boundary_violations_prevented += 1
            events.total_governed_actions += 1

    return events


def format_governance_assurance_report(proof: dict[str, Any]) -> str:
    """Render a proof bundle dict into a human-readable governance assurance report."""
    events = extract_governance_events(proof)
    chain = proof.get("chain_verification", {})
    coverage = proof.get("proof_coverage", {})
    lines: list[str] = []

    # Header
    task_id = proof.get("task_id", "unknown")
    lines.append(f"# Governance Assurance Report: {task_id}")
    lines.append("")

    # Executive Summary
    lines.append("## Executive Summary")
    lines.append(f"- **Task ID**: {task_id}")
    lines.append(f"- **Status**: {proof.get('status', 'unknown')}")
    lines.append(f"- **Proof Mode**: {proof.get('proof_mode', 'unknown')}")
    lines.append(f"- **Total Governed Actions**: {events.total_governed_actions}")
    lines.append(f"- **Denied**: {len(events.denied)}")
    lines.append(f"- **Allowed (with receipt)**: {len(events.allowed_with_receipt)}")
    lines.append(f"- **Rollback Capable**: {len(events.rollback_capable)}")
    lines.append(f"- **Boundary Violations Prevented**: {events.boundary_violations_prevented}")
    lines.append("")

    # Boundary Enforcement
    lines.append("## Boundary Enforcement")
    if events.denied:
        lines.append("")
        lines.append("| Action | Result | Risk |")
        lines.append("|--------|--------|------|")
        for bundle in events.denied:
            action = _truncate(str(bundle.get("action_type", "?")), 40)
            result = str(bundle.get("result_code", "?"))
            risk = str(bundle.get("risk_level", "unknown"))
            # Try to extract reason from policy_result if available
            if not risk or risk == "unknown":
                risk = "high"
            lines.append(f"| {action} | {result} | {risk} |")
        lines.append("")
    else:
        lines.append("No boundary violations detected — clean execution.")
        lines.append("")

    # Authorized Executions
    lines.append("## Authorized Executions")
    if events.allowed_with_receipt:
        lines.append("")
        lines.append("| Receipt | Action | Rollback | Status |")
        lines.append("|---------|--------|----------|--------|")
        for bundle in events.allowed_with_receipt:
            rid = str(bundle.get("receipt_id", "?"))[:20]
            action = str(bundle.get("action_type", "?"))
            rb = "yes" if bundle.get("rollback_supported") else "no"
            status = str(bundle.get("rollback_status", "n/a"))
            lines.append(f"| `{rid}` | {action} | {rb} | {status} |")
        lines.append("")
    else:
        lines.append("No authorized executions recorded.")
        lines.append("")

    # Chain Integrity
    lines.append("## Chain Integrity")
    valid = chain.get("valid", False)
    lines.append(f"- **Valid**: {'yes' if valid else 'NO — BROKEN'}")
    head = str(chain.get("head_hash", "none") or "none")
    lines.append(f"- **Head Hash**: `{head[:32]}{'...' if len(head) > 32 else ''}`")
    lines.append(f"- **Event Count**: {chain.get('event_count', 0)}")
    lines.append("")

    # Coverage Assessment
    lines.append("## Coverage Assessment")
    bundle_cov = coverage.get("receipt_bundle_coverage", {})
    lines.append(
        f"- **Receipt Bundles**: {bundle_cov.get('bundled_receipts', 0)}"
        f"/{bundle_cov.get('total_receipts', 0)}"
    )
    missing = coverage.get("missing_features", [])
    if missing:
        lines.append(f"- **Missing Features**: {', '.join(missing)}")
    lines.append("")

    # Verdict
    lines.append("## Verdict")
    if not valid:
        verdict = "INTEGRITY COMPROMISED"
    elif events.denied:
        verdict = "GOVERNANCE ENFORCED"
    else:
        verdict = "CLEAN EXECUTION"
    lines.append(f"**{verdict}**")
    lines.append("")

    return "\n".join(lines)
