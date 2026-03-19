from __future__ import annotations

import pytest

from hermit.kernel.policy.guards.rules_governance import evaluate_governance_rules
from hermit.kernel.policy.models.models import ActionRequest


def _make_request(
    action_class: str = "unknown",
    risk_hint: str = "high",
    tool_name: str = "test_tool",
    actor: dict | None = None,
    context: dict | None = None,
) -> ActionRequest:
    return ActionRequest(
        request_id="req-test-001",
        action_class=action_class,
        risk_hint=risk_hint,
        tool_name=tool_name,
        actor=actor or {"kind": "agent", "agent_id": "hermit"},
        context=context or {},
    )


class TestNonGovernanceActions:
    def test_unknown_action_returns_none(self) -> None:
        request = _make_request(action_class="unknown")
        assert evaluate_governance_rules(request) is None

    def test_read_local_action_returns_none(self) -> None:
        request = _make_request(action_class="read_local")
        assert evaluate_governance_rules(request) is None

    def test_shell_action_returns_none(self) -> None:
        request = _make_request(action_class="shell")
        assert evaluate_governance_rules(request) is None


class TestDelegateExecution:
    def test_verdict_is_allow_with_receipt(self) -> None:
        request = _make_request(action_class="delegate_execution")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "allow_with_receipt"

    def test_reason_code(self) -> None:
        request = _make_request(action_class="delegate_execution")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].reasons[0].code == "delegate_execution"

    def test_obligations_require_receipt(self) -> None:
        request = _make_request(action_class="delegate_execution")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].obligations.require_receipt is True

    def test_risk_level_uses_hint(self) -> None:
        request = _make_request(action_class="delegate_execution", risk_hint="low")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].risk_level == "low"

    def test_risk_level_defaults_to_medium(self) -> None:
        request = _make_request(action_class="delegate_execution", risk_hint="")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].risk_level == "medium"


class TestApprovalResolution:
    def test_verdict_is_allow_with_receipt(self) -> None:
        request = _make_request(action_class="approval_resolution")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "allow_with_receipt"

    def test_reason_code(self) -> None:
        request = _make_request(action_class="approval_resolution")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].reasons[0].code == "approval_resolution"

    def test_obligations_require_receipt(self) -> None:
        request = _make_request(action_class="approval_resolution")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].obligations.require_receipt is True

    def test_risk_level_defaults_to_medium(self) -> None:
        request = _make_request(action_class="approval_resolution", risk_hint="")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].risk_level == "medium"


class TestSchedulerMutation:
    def test_verdict_is_allow_with_receipt(self) -> None:
        request = _make_request(action_class="scheduler_mutation")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "allow_with_receipt"

    def test_reason_code(self) -> None:
        request = _make_request(action_class="scheduler_mutation")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].reasons[0].code == "scheduler_mutation"

    def test_obligations_require_receipt(self) -> None:
        request = _make_request(action_class="scheduler_mutation")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].obligations.require_receipt is True

    def test_risk_level_defaults_to_medium(self) -> None:
        request = _make_request(action_class="scheduler_mutation", risk_hint="")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].risk_level == "medium"


class TestRollback:
    def test_verdict_is_allow_with_receipt(self) -> None:
        request = _make_request(action_class="rollback")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "allow_with_receipt"

    def test_reason_code(self) -> None:
        request = _make_request(action_class="rollback")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].reasons[0].code == "rollback"

    def test_obligations_require_receipt(self) -> None:
        request = _make_request(action_class="rollback")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].obligations.require_receipt is True

    def test_risk_level_defaults_to_high(self) -> None:
        request = _make_request(action_class="rollback", risk_hint="")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].risk_level == "high"

    def test_risk_level_uses_hint(self) -> None:
        request = _make_request(action_class="rollback", risk_hint="critical")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].risk_level == "critical"


class TestMemoryWriteEvidenceBound:
    """Memory write by kernel actor with evidence_refs: allow_with_receipt."""

    def test_verdict_is_allow_with_receipt(self) -> None:
        request = _make_request(
            action_class="memory_write",
            actor={"kind": "kernel"},
            context={"evidence_refs": ["ref-001"]},
        )
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "allow_with_receipt"

    def test_reason_code(self) -> None:
        request = _make_request(
            action_class="memory_write",
            actor={"kind": "kernel"},
            context={"evidence_refs": ["ref-001"]},
        )
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].reasons[0].code == "memory_write_evidence_bound"

    def test_obligations_require_receipt_and_evidence(self) -> None:
        request = _make_request(
            action_class="memory_write",
            actor={"kind": "kernel"},
            context={"evidence_refs": ["ref-001"]},
        )
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].obligations.require_receipt is True
        assert outcomes[0].obligations.require_evidence is True
        assert outcomes[0].obligations.require_approval is False

    def test_risk_level_defaults_to_medium(self) -> None:
        request = _make_request(
            action_class="memory_write",
            risk_hint="",
            actor={"kind": "kernel"},
            context={"evidence_refs": ["ref-001"]},
        )
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].risk_level == "medium"


class TestMemoryWriteRequiresApproval:
    """Memory write without kernel+evidence: approval_required."""

    def test_non_kernel_actor_requires_approval(self) -> None:
        request = _make_request(
            action_class="memory_write",
            actor={"kind": "agent", "agent_id": "hermit"},
        )
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].verdict == "approval_required"

    def test_kernel_without_evidence_requires_approval(self) -> None:
        request = _make_request(
            action_class="memory_write",
            actor={"kind": "kernel"},
            context={},
        )
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].verdict == "approval_required"

    def test_kernel_with_empty_evidence_requires_approval(self) -> None:
        request = _make_request(
            action_class="memory_write",
            actor={"kind": "kernel"},
            context={"evidence_refs": []},
        )
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].verdict == "approval_required"

    def test_reason_severity_is_warning(self) -> None:
        request = _make_request(action_class="memory_write")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].reasons[0].severity == "warning"

    def test_reason_code(self) -> None:
        request = _make_request(action_class="memory_write")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].reasons[0].code == "memory_write"

    def test_obligations_require_all(self) -> None:
        request = _make_request(action_class="memory_write")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        obligations = outcomes[0].obligations
        assert obligations.require_receipt is True
        assert obligations.require_approval is True
        assert obligations.require_evidence is True

    def test_approval_risk_level_defaults_to_high(self) -> None:
        request = _make_request(action_class="memory_write", risk_hint="")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].obligations.approval_risk_level == "high"

    def test_approval_packet_present(self) -> None:
        request = _make_request(action_class="memory_write", tool_name="write_memory")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        packet = outcomes[0].approval_packet
        assert packet is not None
        assert "write_memory" in packet["title"]
        assert packet["risk_level"] == "high"

    def test_risk_level_defaults_to_high(self) -> None:
        request = _make_request(action_class="memory_write", risk_hint="")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].risk_level == "high"

    def test_risk_level_uses_hint(self) -> None:
        request = _make_request(action_class="memory_write", risk_hint="critical")
        outcomes = evaluate_governance_rules(request)
        assert outcomes is not None
        assert outcomes[0].risk_level == "critical"


@pytest.mark.parametrize(
    "action_class",
    [
        "delegate_execution",
        "approval_resolution",
        "scheduler_mutation",
        "rollback",
        "memory_write",
    ],
)
def test_all_governance_actions_return_outcomes(action_class: str) -> None:
    request = _make_request(action_class=action_class)
    outcomes = evaluate_governance_rules(request)
    assert outcomes is not None
    assert len(outcomes) >= 1


@pytest.mark.parametrize(
    "action_class",
    [
        "delegate_execution",
        "approval_resolution",
        "scheduler_mutation",
        "rollback",
        "memory_write",
    ],
)
def test_all_governance_actions_require_receipt(action_class: str) -> None:
    request = _make_request(action_class=action_class)
    outcomes = evaluate_governance_rules(request)
    assert outcomes is not None
    assert outcomes[0].obligations.require_receipt is True
