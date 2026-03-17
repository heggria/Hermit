from __future__ import annotations

from hermit.kernel.verification.proofs.formatter import (
    format_proof_summary,
    format_receipt_table,
)


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


def test_format_receipt_table():
    receipts = [
        {
            "receipt_id": "receipt_abc123def456",
            "action_type": "write_local",
            "result_code": "succeeded",
            "rollback_supported": True,
            "rollback_status": "not_requested",
        },
        {
            "receipt_id": "receipt_xyz789012345",
            "action_type": "execute_command",
            "result_code": "succeeded",
            "rollback_supported": False,
            "rollback_status": "not_requested",
        },
    ]
    result = format_receipt_table(receipts)
    assert "write_local" in result
    assert "execute_command" in result
    assert "| yes |" in result
    assert "| no |" in result
