from __future__ import annotations

from hermit.kernel.verification.proofs.governance_report import (
    GovernanceEvents,
    extract_governance_events,
    format_governance_assurance_report,
)


def _base_proof(**overrides):
    proof = {
        "task_id": "task_gov_test",
        "status": "verified",
        "proof_mode": "hash_chained",
        "receipt_bundles": [],
        "decision_refs": [],
        "chain_verification": {
            "valid": True,
            "head_hash": "abcdef1234567890" * 4,
            "event_count": 10,
        },
        "proof_coverage": {
            "receipt_bundle_coverage": {
                "bundled_receipts": 0,
                "total_receipts": 0,
            },
            "missing_features": ["signature"],
        },
    }
    proof.update(overrides)
    return proof


def _denied_bundle(action_type="read_local", result_code="denied", **kwargs):
    bundle = {
        "receipt_id": "receipt_denied_001",
        "action_type": action_type,
        "result_code": result_code,
        "rollback_supported": False,
        "rollback_status": "n/a",
        "risk_level": "critical",
    }
    bundle.update(kwargs)
    return bundle


def _allowed_bundle(action_type="write_local", rollback_supported=True, **kwargs):
    bundle = {
        "receipt_id": "receipt_allowed_001",
        "action_type": action_type,
        "result_code": "succeeded",
        "rollback_supported": rollback_supported,
        "rollback_status": "not_requested",
        "risk_level": "medium",
    }
    bundle.update(kwargs)
    return bundle


def test_governance_report_with_denials():
    proof = _base_proof(
        receipt_bundles=[
            _denied_bundle(action_type="read_local", receipt_id="r1"),
            _denied_bundle(
                action_type="execute_command",
                result_code="dispatch_denied",
                receipt_id="r2",
            ),
            _denied_bundle(action_type="execute_command", receipt_id="r3"),
            _allowed_bundle(receipt_id="r4"),
        ],
    )
    result = format_governance_assurance_report(proof)
    assert "GOVERNANCE ENFORCED" in result
    assert "Denied**: 3" in result
    assert "Allowed (with receipt)**: 1" in result
    assert "Boundary Violations Prevented**: 3" in result
    assert "read_local" in result
    assert "execute_command" in result


def test_governance_report_all_allowed():
    proof = _base_proof(
        receipt_bundles=[
            _allowed_bundle(receipt_id="r1"),
            _allowed_bundle(action_type="execute_command", receipt_id="r2"),
        ],
    )
    result = format_governance_assurance_report(proof)
    assert "CLEAN EXECUTION" in result
    assert "Denied**: 0" in result
    assert "No boundary violations detected" in result


def test_governance_report_mixed():
    proof = _base_proof(
        receipt_bundles=[
            _denied_bundle(receipt_id="r1"),
            _allowed_bundle(receipt_id="r2"),
            _allowed_bundle(
                action_type="execute_command",
                rollback_supported=False,
                receipt_id="r3",
            ),
        ],
    )
    result = format_governance_assurance_report(proof)
    assert "GOVERNANCE ENFORCED" in result
    assert "Denied**: 1" in result
    assert "Allowed (with receipt)**: 2" in result
    assert "Rollback Capable**: 1" in result


def test_governance_report_empty_proof():
    proof = _base_proof()
    result = format_governance_assurance_report(proof)
    assert "CLEAN EXECUTION" in result
    assert "Total Governed Actions**: 0" in result
    assert "No boundary violations detected" in result
    assert "No authorized executions recorded" in result


def test_governance_report_chain_integrity_broken():
    proof = _base_proof(
        chain_verification={
            "valid": False,
            "head_hash": "deadbeef",
            "event_count": 5,
        },
    )
    result = format_governance_assurance_report(proof)
    assert "INTEGRITY COMPROMISED" in result
    assert "NO — BROKEN" in result


def test_extract_governance_events_classifies_correctly():
    proof = _base_proof(
        receipt_bundles=[
            _denied_bundle(action_type="read_local", receipt_id="r1"),
            _denied_bundle(
                action_type="execute_command",
                result_code="dispatch_denied",
                receipt_id="r2",
                decision_ref="dec_1",
            ),
            _allowed_bundle(
                action_type="write_local",
                rollback_supported=True,
                receipt_id="r3",
                decision_ref="dec_2",
            ),
            _allowed_bundle(
                action_type="execute_command",
                rollback_supported=False,
                receipt_id="r4",
            ),
        ],
        decision_refs=["dec_1", "dec_2", "dec_orphan"],
    )
    events = extract_governance_events(proof)
    assert isinstance(events, GovernanceEvents)
    assert len(events.denied) == 2
    assert len(events.allowed_with_receipt) == 2
    assert len(events.rollback_capable) == 1
    # 2 denied bundles + 1 unlinked decision
    assert events.boundary_violations_prevented == 3
    # 4 bundles + 1 unlinked decision
    assert events.total_governed_actions == 5


def test_extract_governance_events_detects_blocked_auth_plans():
    """Auth plans with status=blocked and policy_denied gap are counted as denials."""
    proof = _base_proof(
        authorization_plans=[
            {
                "authorization_plan_id": "auth_plan_001",
                "status": "blocked",
                "current_gaps": ["policy_denied"],
                "requested_action_classes": ["execute_command"],
                "contract_ref": "contract_001",
            },
            {
                "authorization_plan_id": "auth_plan_002",
                "status": "active",
                "current_gaps": [],
                "requested_action_classes": ["write_local"],
                "contract_ref": "contract_002",
            },
        ],
    )
    events = extract_governance_events(proof)
    assert len(events.denied) == 1
    assert events.denied[0]["result_code"] == "policy_denied"
    assert events.denied[0]["action_type"] == "execute_command"
    assert events.boundary_violations_prevented == 1
    assert events.total_governed_actions == 1


def test_extract_governance_events_detects_abandoned_contracts():
    """Abandoned contracts not linked to blocked auth plans are counted as denials."""
    proof = _base_proof(
        execution_contracts=[
            {
                "contract_id": "contract_orphan",
                "status": "abandoned",
                "objective": "bash: sudo rm -rf /",
            },
        ],
        authorization_plans=[],
    )
    events = extract_governance_events(proof)
    assert len(events.denied) == 1
    assert events.denied[0]["result_code"] == "contract_abandoned"
    assert events.boundary_violations_prevented == 1


def test_governance_report_with_auth_plan_denials():
    """Integration: auth plan denials appear in the formatted report."""
    proof = _base_proof(
        authorization_plans=[
            {
                "authorization_plan_id": "auth_plan_001",
                "status": "blocked",
                "current_gaps": ["policy_denied"],
                "requested_action_classes": ["execute_command"],
                "contract_ref": "contract_001",
            },
        ],
        execution_contracts=[
            {
                "contract_id": "contract_001",
                "status": "abandoned",
                "objective": "bash: sudo cat /etc/shadow",
            },
        ],
    )
    result = format_governance_assurance_report(proof)
    assert "GOVERNANCE ENFORCED" in result
    assert "policy_denied" in result
    assert "execute_command" in result
