from __future__ import annotations

from hermit.kernel.policy.guards.rules import evaluate_rules
from hermit.kernel.policy.models.models import ActionRequest


def _make_request(action_class: str, **kwargs: object) -> ActionRequest:
    return ActionRequest(
        request_id="test-req-001",
        tool_name=kwargs.get("tool_name", "delegate_test"),  # type: ignore[arg-type]
        action_class=action_class,
        risk_hint=kwargs.get("risk_hint", "medium"),  # type: ignore[arg-type]
        requires_receipt=kwargs.get("requires_receipt", False),  # type: ignore[arg-type]
    )


def test_delegate_execution_requires_decision() -> None:
    """delegate_execution produces a decision (verdict is allow_with_receipt)."""
    request = _make_request("delegate_execution")
    outcomes = evaluate_rules(request)
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "allow_with_receipt"
    assert any(r.code == "delegate_execution" for r in outcomes[0].reasons)


def test_delegate_execution_requires_receipt() -> None:
    """delegate_execution requires a receipt."""
    request = _make_request("delegate_execution")
    outcomes = evaluate_rules(request)
    assert outcomes[0].obligations.require_receipt is True


def test_delegate_execution_no_approval_needed() -> None:
    """delegate_execution does not require approval."""
    request = _make_request("delegate_execution")
    outcomes = evaluate_rules(request)
    assert outcomes[0].obligations.require_approval is False


def test_delegate_reasoning_unchanged() -> None:
    """delegate_reasoning remains auto-allowed without receipt."""
    request = _make_request("delegate_reasoning", risk_hint="low")
    outcomes = evaluate_rules(request)
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "allow"
    assert outcomes[0].obligations.require_receipt is False
    assert outcomes[0].obligations.require_approval is False
