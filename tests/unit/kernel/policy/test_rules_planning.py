"""Tests for kernel/policy/guards/rules_planning.py — planning gate rules."""

from __future__ import annotations

import pytest

from hermit.kernel.policy.guards.rules_planning import (
    _PLANNING_GATED_ACTION_CLASSES,
    evaluate_planning_rules,
)
from hermit.kernel.policy.models.models import ActionRequest


def _make_request(
    action_class: str = "write_local",
    planning_required: bool = True,
    selected_plan_ref: str = "",
    risk_hint: str = "high",
) -> ActionRequest:
    return ActionRequest(
        request_id="req-test-001",
        action_class=action_class,
        risk_hint=risk_hint,
        context={
            "planning_required": planning_required,
            "selected_plan_ref": selected_plan_ref,
        },
    )


class TestPlanningNotRequired:
    def test_returns_none_when_planning_not_required(self) -> None:
        result = evaluate_planning_rules(_make_request(planning_required=False))
        assert result is None

    def test_returns_none_when_planning_required_is_falsy(self) -> None:
        req = ActionRequest(
            request_id="req-test",
            action_class="write_local",
            context={"planning_required": 0},
        )
        result = evaluate_planning_rules(req)
        assert result is None


class TestPlanAlreadySelected:
    def test_returns_none_when_plan_ref_present(self) -> None:
        result = evaluate_planning_rules(_make_request(selected_plan_ref="plan://my-plan"))
        assert result is None

    def test_returns_none_when_plan_ref_has_content(self) -> None:
        result = evaluate_planning_rules(_make_request(selected_plan_ref="any-string"))
        assert result is None


class TestNonGatedActionClass:
    @pytest.mark.parametrize(
        "action_class",
        ["read_local", "network_read", "delegate_reasoning", "unknown", "memory_write"],
    )
    def test_non_gated_action_class_returns_none(self, action_class: str) -> None:
        result = evaluate_planning_rules(_make_request(action_class=action_class))
        assert result is None


class TestPlanningGateTriggered:
    @pytest.mark.parametrize("action_class", sorted(_PLANNING_GATED_ACTION_CLASSES))
    def test_gated_action_class_requires_approval(self, action_class: str) -> None:
        result = evaluate_planning_rules(_make_request(action_class=action_class))
        assert result is not None
        assert len(result) == 1
        assert result[0].verdict == "approval_required"

    def test_reason_code(self) -> None:
        result = evaluate_planning_rules(_make_request())
        assert result is not None
        assert result[0].reasons[0].code == "plan_required"

    def test_reason_severity(self) -> None:
        result = evaluate_planning_rules(_make_request())
        assert result is not None
        assert result[0].reasons[0].severity == "warning"

    def test_obligations(self) -> None:
        result = evaluate_planning_rules(_make_request())
        assert result is not None
        assert result[0].obligations.require_receipt is True
        assert result[0].obligations.require_preview is False
        assert result[0].obligations.require_approval is True

    def test_approval_risk_level_from_hint(self) -> None:
        result = evaluate_planning_rules(_make_request(risk_hint="critical"))
        assert result is not None
        assert result[0].obligations.approval_risk_level == "critical"

    def test_approval_risk_level_defaults_to_high(self) -> None:
        result = evaluate_planning_rules(_make_request(risk_hint=""))
        assert result is not None
        assert result[0].obligations.approval_risk_level == "high"

    def test_approval_packet_present(self) -> None:
        result = evaluate_planning_rules(_make_request())
        assert result is not None
        packet = result[0].approval_packet
        assert packet is not None
        assert "title" in packet
        assert "summary" in packet
        assert "risk_level" in packet

    def test_risk_level_from_hint(self) -> None:
        result = evaluate_planning_rules(_make_request(risk_hint="critical"))
        assert result is not None
        assert result[0].risk_level == "critical"

    def test_risk_level_defaults_to_high(self) -> None:
        result = evaluate_planning_rules(_make_request(risk_hint=""))
        assert result is not None
        assert result[0].risk_level == "high"


class TestPlanRefWhitespaceOnly:
    def test_whitespace_plan_ref_treated_as_empty(self) -> None:
        result = evaluate_planning_rules(_make_request(selected_plan_ref="   "))
        assert result is not None
        assert result[0].verdict == "approval_required"
