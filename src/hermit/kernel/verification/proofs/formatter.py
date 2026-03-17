from __future__ import annotations

from typing import Any


def format_proof_summary(proof: dict[str, Any]) -> str:
    """Render a proof bundle dict into a human-readable markdown summary."""
    lines: list[str] = []
    task = proof.get("task", {})
    proj = proof.get("projection", {})
    chain = proof.get("chain_verification", {})
    coverage = proof.get("proof_coverage", {})

    # Header
    lines.append(f"# Proof Summary: {task.get('task_id', 'unknown')}")
    lines.append("")

    # Task overview
    lines.append("## Task")
    lines.append(f"- **Status**: {task.get('status', 'unknown')}")
    lines.append(f"- **Policy**: {task.get('policy_profile', 'default')}")
    lines.append(f"- **Channel**: {task.get('source_channel', 'unknown')}")
    goal = task.get("goal", "")
    lines.append(f"- **Goal**: {_truncate(goal, 120)}")
    lines.append("")

    # Governance stats
    lines.append("## Governance")
    lines.append(f"- **Events**: {proj.get('events_processed', 0)}")
    lines.append(f"- **Steps**: {proj.get('step_count', 0)}")
    lines.append(f"- **Decisions**: {proj.get('decision_count', 0)}")
    lines.append(f"- **Capability grants**: {proj.get('capability_grant_count', 0)}")
    lines.append(f"- **Receipts**: {proj.get('receipt_count', 0)}")
    lines.append(f"- **Approvals**: {proj.get('approval_count', 0)}")
    lines.append("")

    # Chain integrity
    lines.append("## Chain Integrity")
    valid = chain.get("valid", False)
    lines.append(f"- **Valid**: {'yes' if valid else 'NO — BROKEN'}")
    lines.append(f"- **Proof mode**: {proof.get('proof_mode', 'unknown')}")
    head = chain.get("head_hash", "none") or "none"
    lines.append(f"- **Head hash**: `{head[:32]}...`")
    lines.append(f"- **Event count**: {chain.get('event_count', 0)}")
    lines.append("")

    # Coverage
    bundle_cov = coverage.get("receipt_bundle_coverage", {})
    lines.append("## Coverage")
    lines.append(
        f"- **Receipt bundles**: {bundle_cov.get('bundled_receipts', 0)}"
        f"/{bundle_cov.get('total_receipts', 0)}"
    )
    missing = coverage.get("missing_features", [])
    if missing:
        lines.append(f"- **Missing features**: {', '.join(missing)}")
    lines.append("")

    return "\n".join(lines)


def format_receipt_table(receipts: list[dict[str, Any]]) -> str:
    """Render a list of receipt dicts into a markdown table."""
    lines = [
        "| Receipt | Action | Result | Rollback | Status |",
        "|---------|--------|--------|----------|--------|",
    ]
    for r in receipts:
        rid = r.get("receipt_id", "?")[:20]
        action = r.get("action_type", "?")
        result = r.get("result_code", "?")
        rb = "yes" if r.get("rollback_supported") else "no"
        status = r.get("rollback_status", "n/a")
        lines.append(f"| `{rid}` | {action} | {result} | {rb} | {status} |")
    return "\n".join(lines)


def _truncate(text: str, max_len: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
