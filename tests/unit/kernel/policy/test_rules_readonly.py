from __future__ import annotations

import pytest

from hermit.kernel.policy.guards.rules_readonly import evaluate_readonly_rules
from hermit.kernel.policy.models.models import ActionRequest


@pytest.fixture
def make_request():
    """Factory fixture that creates an ActionRequest with the given action_class."""

    def _make(
        action_class: str,
        *,
        risk_hint: str = "low",
        requires_receipt: bool = False,
    ) -> ActionRequest:
        return ActionRequest(
            request_id="req-test-001",
            action_class=action_class,
            risk_hint=risk_hint,
            requires_receipt=requires_receipt,
        )

    return _make


class TestReadLocalAutoAllow:
    def test_returns_allow_verdict(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("read_local"))
        assert result is not None
        assert len(result) == 1
        assert result[0].verdict == "allow"

    def test_reason_code(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("read_local"))
        assert result is not None
        assert result[0].reasons[0].code == "readonly_tool"

    def test_receipt_propagated(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("read_local", requires_receipt=True))
        assert result is not None
        assert result[0].obligations.require_receipt is True

    def test_receipt_false_by_default(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("read_local"))
        assert result is not None
        assert result[0].obligations.require_receipt is False

    def test_risk_level_from_hint(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("read_local", risk_hint="medium"))
        assert result is not None
        assert result[0].risk_level == "medium"

    def test_risk_level_defaults_to_low(self) -> None:
        req = ActionRequest(request_id="req-test", action_class="read_local", risk_hint="")
        result = evaluate_readonly_rules(req)
        assert result is not None
        assert result[0].risk_level == "low"


class TestNetworkReadAutoAllow:
    def test_returns_allow_verdict(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("network_read"))
        assert result is not None
        assert len(result) == 1
        assert result[0].verdict == "allow"

    def test_reason_code(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("network_read"))
        assert result is not None
        assert result[0].reasons[0].code == "readonly_network"

    def test_receipt_propagated(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("network_read", requires_receipt=True))
        assert result is not None
        assert result[0].obligations.require_receipt is True


class TestDelegateReasoningAutoAllow:
    def test_returns_allow_verdict(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("delegate_reasoning"))
        assert result is not None
        assert len(result) == 1
        assert result[0].verdict == "allow"

    def test_reason_code(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("delegate_reasoning"))
        assert result is not None
        assert result[0].reasons[0].code == "delegate_reasoning"

    def test_receipt_always_false(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("delegate_reasoning", requires_receipt=True))
        assert result is not None
        assert result[0].obligations.require_receipt is False


class TestEphemeralUiMutationAutoAllow:
    def test_returns_allow_verdict(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("ephemeral_ui_mutation"))
        assert result is not None
        assert len(result) == 1
        assert result[0].verdict == "allow"

    def test_reason_code(self, make_request) -> None:
        result = evaluate_readonly_rules(make_request("ephemeral_ui_mutation"))
        assert result is not None
        assert result[0].reasons[0].code == "ephemeral_ui_mutation"

    def test_receipt_always_false(self, make_request) -> None:
        result = evaluate_readonly_rules(
            make_request("ephemeral_ui_mutation", requires_receipt=True)
        )
        assert result is not None
        assert result[0].obligations.require_receipt is False


class TestNonMatchingActionClass:
    @pytest.mark.parametrize(
        "action_class",
        ["write_local", "unknown", "execute", "network_write", "admin", ""],
    )
    def test_returns_none(self, make_request, action_class: str) -> None:
        result = evaluate_readonly_rules(make_request(action_class))
        assert result is None
