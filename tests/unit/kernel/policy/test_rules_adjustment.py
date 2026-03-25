"""Tests for the extracted rules_adjustment module.

Verifies that:
1. apply_policy_suggestion adjusts outcomes based on template-confidence suggestions
2. apply_task_pattern annotates and downgrades risk for known-good patterns
3. evaluate_autonomous returns correct outcomes for different action classes
"""

from __future__ import annotations

from hermit.kernel.policy.guards.rules import RuleOutcome
from hermit.kernel.policy.guards.rules_adjustment import (
    apply_policy_suggestion,
    apply_task_pattern,
    evaluate_autonomous,
)
from hermit.kernel.policy.models.models import ActionRequest, PolicyObligations, PolicyReason

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    request_id: str = "req-test",
    tool_name: str = "write_file",
    action_class: str = "write_local",
    context: dict | None = None,
    derived: dict | None = None,
    risk_hint: str = "high",
) -> ActionRequest:
    return ActionRequest(
        request_id=request_id,
        tool_name=tool_name,
        action_class=action_class,
        context=context or {},
        derived=derived or {},
        risk_hint=risk_hint,
    )


def _make_outcome(
    *,
    verdict: str = "approval_required",
    risk_level: str = "high",
    require_receipt: bool = True,
    require_approval: bool = True,
) -> RuleOutcome:
    return RuleOutcome(
        verdict=verdict,
        reasons=[PolicyReason("test", "Test reason")],
        obligations=PolicyObligations(
            require_receipt=require_receipt,
            require_approval=require_approval,
        ),
        risk_level=risk_level,
    )


def _suggestion_context(
    *,
    skip_eligible: bool = True,
    suggested_risk: str = "medium",
    confidence_basis: str = "10 invocations, 98% success",
) -> dict:
    return {
        "policy_suggestion": {
            "template_ref": "tmpl-1",
            "skip_approval_eligible": skip_eligible,
            "suggested_risk_level": suggested_risk,
            "confidence_basis": confidence_basis,
            "reason": "test",
        }
    }


def _pattern_context(*, invocation_count: int = 5, success_rate: float = 0.90) -> dict:
    return {
        "task_pattern": {
            "pattern_fingerprint": "fp-test",
            "step_descriptions": [
                {"action_class": "write_local", "tool_name": "write_file"},
            ],
            "invocation_count": invocation_count,
            "success_rate": success_rate,
        }
    }


# ---------------------------------------------------------------------------
# apply_policy_suggestion
# ---------------------------------------------------------------------------


class TestApplyPolicySuggestion:
    def test_skip_eligible_changes_verdict_to_allow_with_receipt(self) -> None:
        request = _make_request(
            action_class="read_local", context=_suggestion_context(skip_eligible=True)
        )
        outcomes = [_make_outcome(verdict="approval_required", risk_level="high")]

        adjusted = apply_policy_suggestion(request, outcomes)

        assert len(adjusted) == 1
        assert adjusted[0].verdict == "allow_with_receipt"
        assert adjusted[0].obligations.require_approval is False
        assert adjusted[0].obligations.require_receipt is True
        assert adjusted[0].risk_level == "medium"

    def test_skip_eligible_adds_template_confidence_reason(self) -> None:
        request = _make_request(
            action_class="read_local",
            context=_suggestion_context(confidence_basis="20 invocations, 100% success"),
        )
        outcomes = [_make_outcome()]

        adjusted = apply_policy_suggestion(request, outcomes)

        reason_codes = [r.code for r in adjusted[0].reasons]
        assert "template_confidence_skip" in reason_codes

    def test_critical_risk_never_skipped(self) -> None:
        request = _make_request(
            action_class="read_local", context=_suggestion_context(skip_eligible=True)
        )
        outcomes = [_make_outcome(verdict="approval_required", risk_level="critical")]

        adjusted = apply_policy_suggestion(request, outcomes)

        assert adjusted[0].verdict == "approval_required"
        assert adjusted[0].risk_level == "critical"

    def test_risk_downgrade_without_skip(self) -> None:
        request = _make_request(
            context=_suggestion_context(skip_eligible=False, suggested_risk="medium")
        )
        outcomes = [_make_outcome(verdict="approval_required", risk_level="high")]

        adjusted = apply_policy_suggestion(request, outcomes)

        assert adjusted[0].verdict == "approval_required"
        assert adjusted[0].risk_level == "medium"
        reason_codes = [r.code for r in adjusted[0].reasons]
        assert "template_confidence_downgrade" in reason_codes

    def test_no_suggestion_returns_unchanged(self) -> None:
        request = _make_request(context={})
        outcomes = [_make_outcome()]

        adjusted = apply_policy_suggestion(request, outcomes)

        assert adjusted == outcomes

    def test_non_dict_suggestion_returns_unchanged(self) -> None:
        request = _make_request(context={"policy_suggestion": "invalid"})
        outcomes = [_make_outcome()]

        adjusted = apply_policy_suggestion(request, outcomes)

        assert adjusted == outcomes

    def test_non_approval_verdict_unchanged(self) -> None:
        request = _make_request(
            action_class="read_local", context=_suggestion_context(skip_eligible=True)
        )
        outcomes = [_make_outcome(verdict="allow", risk_level="low")]

        adjusted = apply_policy_suggestion(request, outcomes)

        assert adjusted[0].verdict == "allow"

    def test_same_risk_no_skip_returns_unchanged(self) -> None:
        request = _make_request(
            context=_suggestion_context(skip_eligible=False, suggested_risk="high")
        )
        outcomes = [_make_outcome(verdict="approval_required", risk_level="high")]

        adjusted = apply_policy_suggestion(request, outcomes)

        assert adjusted[0].verdict == "approval_required"
        assert adjusted[0].risk_level == "high"
        # No extra reason added since risk is the same
        assert len(adjusted[0].reasons) == 1

    def test_multiple_outcomes_adjusted_independently(self) -> None:
        request = _make_request(
            action_class="read_local", context=_suggestion_context(skip_eligible=True)
        )
        outcomes = [
            _make_outcome(verdict="allow", risk_level="low"),
            _make_outcome(verdict="approval_required", risk_level="high"),
            _make_outcome(verdict="approval_required", risk_level="critical"),
        ]

        adjusted = apply_policy_suggestion(request, outcomes)

        assert len(adjusted) == 3
        assert adjusted[0].verdict == "allow"  # unchanged
        assert adjusted[1].verdict == "allow_with_receipt"  # skipped
        assert adjusted[2].verdict == "approval_required"  # critical, not skipped


# ---------------------------------------------------------------------------
# apply_task_pattern
# ---------------------------------------------------------------------------


class TestApplyTaskPattern:
    def test_high_confidence_pattern_downgrades_high_to_medium(self) -> None:
        request = _make_request(context=_pattern_context(invocation_count=5, success_rate=0.90))
        outcomes = [_make_outcome(verdict="approval_required", risk_level="high")]

        adjusted = apply_task_pattern(request, outcomes)

        assert adjusted[0].risk_level == "medium"
        assert any(r.code == "task_pattern_match" for r in adjusted[0].reasons)

    def test_critical_risk_not_downgraded_but_annotated(self) -> None:
        request = _make_request(context=_pattern_context(invocation_count=10, success_rate=0.95))
        outcomes = [_make_outcome(verdict="approval_required", risk_level="critical")]

        adjusted = apply_task_pattern(request, outcomes)

        assert adjusted[0].risk_level == "critical"
        assert any(r.code == "task_pattern_match" for r in adjusted[0].reasons)

    def test_no_pattern_returns_unchanged(self) -> None:
        request = _make_request(context={})
        outcomes = [_make_outcome()]

        adjusted = apply_task_pattern(request, outcomes)

        assert adjusted == outcomes

    def test_non_dict_pattern_returns_unchanged(self) -> None:
        request = _make_request(context={"task_pattern": 42})
        outcomes = [_make_outcome()]

        adjusted = apply_task_pattern(request, outcomes)

        assert adjusted == outcomes

    def test_low_invocation_count_returns_unchanged(self) -> None:
        request = _make_request(context=_pattern_context(invocation_count=2, success_rate=1.0))
        outcomes = [_make_outcome()]

        adjusted = apply_task_pattern(request, outcomes)

        assert adjusted == outcomes

    def test_low_success_rate_returns_unchanged(self) -> None:
        request = _make_request(context=_pattern_context(invocation_count=10, success_rate=0.50))
        outcomes = [_make_outcome()]

        adjusted = apply_task_pattern(request, outcomes)

        assert adjusted == outcomes

    def test_allow_verdict_annotated_not_downgraded(self) -> None:
        request = _make_request(context=_pattern_context(invocation_count=5, success_rate=0.90))
        outcomes = [_make_outcome(verdict="allow", risk_level="low")]

        adjusted = apply_task_pattern(request, outcomes)

        assert adjusted[0].verdict == "allow"
        assert adjusted[0].risk_level == "low"
        assert any(r.code == "task_pattern_match" for r in adjusted[0].reasons)

    def test_medium_risk_not_downgraded_further(self) -> None:
        request = _make_request(context=_pattern_context(invocation_count=5, success_rate=0.90))
        outcomes = [_make_outcome(verdict="approval_required", risk_level="medium")]

        adjusted = apply_task_pattern(request, outcomes)

        # medium stays medium (only high -> medium)
        assert adjusted[0].risk_level == "medium"

    def test_threshold_boundary_invocation_exactly_3(self) -> None:
        request = _make_request(context=_pattern_context(invocation_count=3, success_rate=0.90))
        outcomes = [_make_outcome(verdict="approval_required", risk_level="high")]

        adjusted = apply_task_pattern(request, outcomes)

        assert adjusted[0].risk_level == "medium"
        assert any(r.code == "task_pattern_match" for r in adjusted[0].reasons)

    def test_threshold_boundary_success_rate_exactly_085(self) -> None:
        request = _make_request(context=_pattern_context(invocation_count=5, success_rate=0.85))
        outcomes = [_make_outcome(verdict="approval_required", risk_level="high")]

        adjusted = apply_task_pattern(request, outcomes)

        assert adjusted[0].risk_level == "medium"
        assert any(r.code == "task_pattern_match" for r in adjusted[0].reasons)


# ---------------------------------------------------------------------------
# evaluate_autonomous
# ---------------------------------------------------------------------------


class TestEvaluateAutonomous:
    def test_read_local_auto_allowed(self) -> None:
        request = _make_request(action_class="read_local", tool_name="read_file")

        outcomes = evaluate_autonomous(request)

        assert len(outcomes) == 1
        assert outcomes[0].verdict == "allow"
        assert outcomes[0].risk_level == "low"
        assert any(r.code == "autonomous_read" for r in outcomes[0].reasons)

    def test_network_read_auto_allowed(self) -> None:
        request = _make_request(action_class="network_read", tool_name="web_fetch")

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].verdict == "allow"
        assert outcomes[0].risk_level == "low"

    def test_delegate_reasoning_auto_allowed(self) -> None:
        request = _make_request(action_class="delegate_reasoning", tool_name="subagent")

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].verdict == "allow"
        assert any(r.code == "autonomous_passthrough" for r in outcomes[0].reasons)

    def test_ephemeral_ui_mutation_auto_allowed(self) -> None:
        request = _make_request(action_class="ephemeral_ui_mutation", tool_name="ui_tool")

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].verdict == "allow"

    def test_execute_command_sudo_denied(self) -> None:
        request = _make_request(
            action_class="execute_command",
            tool_name="bash",
            derived={"command_flags": {"sudo": True}},
        )

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].verdict == "deny"
        assert outcomes[0].risk_level == "critical"
        assert any(r.code == "dangerous_shell" for r in outcomes[0].reasons)

    def test_execute_command_curl_pipe_sh_denied(self) -> None:
        request = _make_request(
            action_class="execute_command",
            tool_name="bash",
            derived={"command_flags": {"curl_pipe_sh": True}},
        )

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].verdict == "deny"
        assert outcomes[0].risk_level == "critical"

    def test_execute_command_safe_falls_through(self) -> None:
        request = _make_request(
            action_class="execute_command",
            tool_name="bash",
            derived={"command_flags": {}},
        )

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].verdict == "allow_with_receipt"
        assert any(r.code == "autonomous_auto_approve" for r in outcomes[0].reasons)

    def test_write_local_sensitive_outside_workspace_denied(self) -> None:
        request = _make_request(
            action_class="write_local",
            tool_name="write_file",
            derived={
                "sensitive_paths": ["/etc/passwd"],
                "outside_workspace": True,
            },
        )

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].verdict == "deny"
        assert outcomes[0].risk_level == "critical"
        assert any(r.code == "protected_path" for r in outcomes[0].reasons)

    def test_patch_file_sensitive_outside_workspace_denied(self) -> None:
        request = _make_request(
            action_class="patch_file",
            tool_name="patch",
            derived={
                "sensitive_paths": ["/etc/hosts"],
                "outside_workspace": True,
            },
        )

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].verdict == "deny"
        assert outcomes[0].risk_level == "critical"

    def test_write_local_sensitive_inside_workspace_allowed(self) -> None:
        request = _make_request(
            action_class="write_local",
            tool_name="write_file",
            derived={
                "sensitive_paths": ["/workspace/.env"],
                "outside_workspace": False,
            },
        )

        outcomes = evaluate_autonomous(request)

        # Not denied because inside workspace
        assert outcomes[0].verdict == "allow_with_receipt"

    def test_kernel_self_modification_requires_approval(self) -> None:
        request = _make_request(
            action_class="write_local",
            tool_name="write_file",
            derived={
                "kernel_paths": ["/src/hermit/kernel/policy/guards/rules.py"],
            },
        )

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].verdict == "approval_required"
        assert outcomes[0].risk_level == "critical"
        assert outcomes[0].obligations.require_approval is True
        assert outcomes[0].obligations.require_evidence is True
        assert any(r.code == "kernel_self_modification" for r in outcomes[0].reasons)
        assert outcomes[0].approval_packet is not None
        assert "kernel" in outcomes[0].approval_packet["title"].lower()

    def test_patch_file_kernel_paths_requires_approval(self) -> None:
        request = _make_request(
            action_class="patch_file",
            tool_name="patch",
            derived={
                "kernel_paths": ["/src/hermit/kernel/task/models/records.py"],
            },
        )

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].verdict == "approval_required"
        assert outcomes[0].risk_level == "critical"

    def test_default_action_allowed_with_receipt(self) -> None:
        request = _make_request(
            action_class="write_local",
            tool_name="write_file",
            risk_hint="medium",
        )

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].verdict == "allow_with_receipt"
        assert outcomes[0].obligations.require_receipt is True
        assert outcomes[0].obligations.require_approval is False
        assert outcomes[0].risk_level == "medium"

    def test_default_action_uses_risk_hint(self) -> None:
        request = _make_request(
            action_class="unknown_action",
            tool_name="some_tool",
            risk_hint="low",
        )

        outcomes = evaluate_autonomous(request)

        assert outcomes[0].risk_level == "low"

    def test_sensitive_paths_without_outside_workspace_not_denied(self) -> None:
        """Sensitive paths alone are not enough to deny -- outside_workspace must also be true."""
        request = _make_request(
            action_class="write_local",
            tool_name="write_file",
            derived={"sensitive_paths": ["/etc/shadow"]},
        )

        outcomes = evaluate_autonomous(request)

        # outside_workspace defaults to falsy, so not denied
        assert outcomes[0].verdict != "deny"
