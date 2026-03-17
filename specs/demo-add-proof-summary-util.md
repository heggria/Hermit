---
id: demo-add-proof-summary-util
title: "Add human-readable proof summary formatter"
priority: normal
trust_zone: low
---

## Objective

Add a `format_proof_summary()` utility function to the kernel verification layer that renders a proof bundle into a human-readable markdown summary. This utility will be used by CLI commands and PR body generation.

## Steps

1. **Read** `src/hermit/kernel/verification/proofs/proofs.py` to understand the current ProofService and proof bundle structure.

2. **Create** `src/hermit/kernel/verification/proofs/formatter.py` using `write_file` with the following implementation:

```python
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
    lines.append(f"- **Goal**: {_truncate(task.get('goal', ''), 120)}")
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
    lines.append(f"- **Head hash**: `{chain.get('head_hash', 'none')[:32]}...`")
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


def _truncate(text: str, max_len: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
```

3. **Add tests** — create `tests/unit/kernel/test_proof_formatter.py` using `write_file`:

```python
from __future__ import annotations

from hermit.kernel.verification.proofs.formatter import format_proof_summary


def test_format_proof_summary_basic():
    proof = {
        "task": {
            "task_id": "task_abc123",
            "status": "completed",
            "policy_profile": "autonomous",
            "source_channel": "cli",
            "goal": "Test goal",
        },
        "proof_mode": "hash_chained",
        "projection": {
            "events_processed": 50,
            "step_count": 2,
            "decision_count": 3,
            "capability_grant_count": 3,
            "receipt_count": 2,
            "approval_count": 0,
        },
        "chain_verification": {
            "valid": True,
            "head_hash": "abcdef1234567890" * 4,
            "event_count": 50,
        },
        "proof_coverage": {
            "receipt_bundle_coverage": {
                "bundled_receipts": 2,
                "total_receipts": 2,
            },
            "missing_features": ["signature"],
        },
    }
    result = format_proof_summary(proof)
    assert "# Proof Summary: task_abc123" in result
    assert "**Status**: completed" in result
    assert "**Valid**: yes" in result
    assert "**Receipts**: 2" in result
    assert "signature" in result


def test_format_proof_summary_broken_chain():
    proof = {
        "task": {"task_id": "task_broken", "status": "completed"},
        "projection": {},
        "chain_verification": {"valid": False, "head_hash": "deadbeef", "event_count": 10},
        "proof_coverage": {"receipt_bundle_coverage": {}},
    }
    result = format_proof_summary(proof)
    assert "NO — BROKEN" in result


def test_format_proof_summary_truncates_long_goal():
    proof = {
        "task": {"task_id": "task_long", "status": "completed", "goal": "x" * 200},
        "projection": {},
        "chain_verification": {"valid": True, "head_hash": "abc", "event_count": 1},
        "proof_coverage": {"receipt_bundle_coverage": {}},
    }
    result = format_proof_summary(proof)
    assert "..." in result
```

4. **Run tests** to verify:

```bash
uv run pytest tests/unit/kernel/test_proof_formatter.py -q
```

## Constraints

- Use `write_file` for ALL file creation — never use bash to write files.
- Do not modify any existing files.
- Follow existing kernel code conventions (dataclass, `from __future__ import annotations`).
- Keep the formatter pure — no I/O, no imports beyond stdlib.

## Acceptance Criteria

- [ ] `src/hermit/kernel/verification/proofs/formatter.py` exists with `format_proof_summary()`
- [ ] `tests/unit/kernel/test_proof_formatter.py` exists with 3 tests
- [ ] All tests pass: `uv run pytest tests/unit/kernel/test_proof_formatter.py -q`
- [ ] `make check` passes
