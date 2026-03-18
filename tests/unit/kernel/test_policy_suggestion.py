"""Tests for template-confidence-driven policy suggestion.

Verifies that:
1. PolicySuggestion is computed correctly from template confidence
2. High-confidence templates (>=95%) can skip approval
3. Moderate-confidence templates (>=80%) suggest lower risk
4. Critical risk actions never have approval skipped
5. Policy suggestion integrates with rule evaluation
"""

from __future__ import annotations

from hermit.kernel.execution.controller.template_learner import (
    ContractTemplate,
    ContractTemplateLearner,
)
from hermit.kernel.policy.guards.rules import (
    PolicyObligations,
    PolicyReason,
    RuleOutcome,
    _apply_policy_suggestion,
    _apply_task_pattern,
    evaluate_rules,
)
from hermit.kernel.policy.models.models import ActionRequest

# ---------------------------------------------------------------------------
# Unit tests: PolicySuggestion computation
# ---------------------------------------------------------------------------


class TestComputePolicySuggestion:
    def _make_template(
        self,
        *,
        invocation_count: int = 10,
        success_rate: float = 0.95,
    ) -> ContractTemplate:
        return ContractTemplate(
            action_class="write_local",
            tool_name="write_file",
            risk_level="high",
            reversibility_class="reversible",
            source_contract_ref="contract-ref-1",
            invocation_count=invocation_count,
            success_count=int(invocation_count * success_rate),
            failure_count=invocation_count - int(invocation_count * success_rate),
            success_rate=success_rate,
        )

    def test_high_confidence_skip_approval(self) -> None:
        # Use None for store since compute_policy_suggestion doesn't need it
        learner = ContractTemplateLearner.__new__(ContractTemplateLearner)
        template = self._make_template(invocation_count=10, success_rate=0.98)

        suggestion = learner.compute_policy_suggestion(template, risk_level="high")
        assert suggestion is not None
        assert suggestion.skip_approval_eligible is True
        assert suggestion.suggested_risk_level == "medium"

    def test_high_confidence_critical_no_skip(self) -> None:
        learner = ContractTemplateLearner.__new__(ContractTemplateLearner)
        template = self._make_template(invocation_count=10, success_rate=0.98)

        suggestion = learner.compute_policy_suggestion(template, risk_level="critical")
        assert suggestion is not None
        assert suggestion.skip_approval_eligible is False
        assert suggestion.suggested_risk_level == "medium"

    def test_moderate_confidence_risk_downgrade(self) -> None:
        learner = ContractTemplateLearner.__new__(ContractTemplateLearner)
        template = self._make_template(invocation_count=10, success_rate=0.85)

        suggestion = learner.compute_policy_suggestion(template, risk_level="high")
        assert suggestion is not None
        assert suggestion.skip_approval_eligible is False
        assert suggestion.suggested_risk_level == "medium"

    def test_low_confidence_no_suggestion(self) -> None:
        learner = ContractTemplateLearner.__new__(ContractTemplateLearner)
        template = self._make_template(invocation_count=10, success_rate=0.50)

        suggestion = learner.compute_policy_suggestion(template, risk_level="high")
        assert suggestion is None

    def test_insufficient_invocations_no_suggestion(self) -> None:
        learner = ContractTemplateLearner.__new__(ContractTemplateLearner)
        template = self._make_template(invocation_count=3, success_rate=1.0)

        suggestion = learner.compute_policy_suggestion(template, risk_level="high")
        assert suggestion is None


# ---------------------------------------------------------------------------
# Unit tests: policy suggestion application to rule outcomes
# ---------------------------------------------------------------------------


class TestApplyPolicySuggestion:
    def test_skip_approval_adjusts_verdict(self) -> None:
        request = ActionRequest(
            request_id="req-1",
            tool_name="write_file",
            action_class="write_local",
            context={
                "policy_suggestion": {
                    "template_ref": "tmpl-1",
                    "skip_approval_eligible": True,
                    "suggested_risk_level": "medium",
                    "confidence_basis": "10 invocations, 98% success",
                    "reason": "test",
                },
            },
        )
        outcomes = [
            RuleOutcome(
                verdict="approval_required",
                reasons=[PolicyReason("test", "Test reason")],
                obligations=PolicyObligations(require_receipt=True, require_approval=True),
                risk_level="high",
            )
        ]

        adjusted = _apply_policy_suggestion(request, outcomes)
        assert len(adjusted) == 1
        assert adjusted[0].verdict == "allow_with_receipt"
        assert adjusted[0].obligations.require_approval is False
        assert adjusted[0].risk_level == "medium"

    def test_critical_risk_never_skipped(self) -> None:
        request = ActionRequest(
            request_id="req-2",
            tool_name="bash",
            action_class="execute_command",
            context={
                "policy_suggestion": {
                    "template_ref": "tmpl-1",
                    "skip_approval_eligible": True,
                    "suggested_risk_level": "high",
                    "confidence_basis": "50 invocations, 99% success",
                    "reason": "test",
                },
            },
        )
        outcomes = [
            RuleOutcome(
                verdict="approval_required",
                reasons=[PolicyReason("test", "Test reason")],
                obligations=PolicyObligations(require_receipt=True, require_approval=True),
                risk_level="critical",
            )
        ]

        adjusted = _apply_policy_suggestion(request, outcomes)
        assert len(adjusted) == 1
        assert adjusted[0].verdict == "approval_required"
        assert adjusted[0].risk_level == "critical"

    def test_risk_downgrade_without_skip(self) -> None:
        request = ActionRequest(
            request_id="req-3",
            tool_name="write_file",
            action_class="write_local",
            context={
                "policy_suggestion": {
                    "template_ref": "tmpl-1",
                    "skip_approval_eligible": False,
                    "suggested_risk_level": "medium",
                    "confidence_basis": "10 invocations, 85% success",
                    "reason": "test",
                },
            },
        )
        outcomes = [
            RuleOutcome(
                verdict="approval_required",
                reasons=[PolicyReason("test", "Test reason")],
                obligations=PolicyObligations(require_receipt=True, require_approval=True),
                risk_level="high",
            )
        ]

        adjusted = _apply_policy_suggestion(request, outcomes)
        assert len(adjusted) == 1
        assert adjusted[0].verdict == "approval_required"
        assert adjusted[0].risk_level == "medium"

    def test_no_suggestion_no_change(self) -> None:
        request = ActionRequest(
            request_id="req-4",
            tool_name="write_file",
            action_class="write_local",
            context={},
        )
        outcomes = [
            RuleOutcome(
                verdict="approval_required",
                reasons=[PolicyReason("test", "Test reason")],
                obligations=PolicyObligations(require_receipt=True, require_approval=True),
                risk_level="high",
            )
        ]

        adjusted = _apply_policy_suggestion(request, outcomes)
        assert adjusted == outcomes

    def test_non_approval_verdicts_unchanged(self) -> None:
        request = ActionRequest(
            request_id="req-5",
            tool_name="write_file",
            action_class="write_local",
            context={
                "policy_suggestion": {
                    "template_ref": "tmpl-1",
                    "skip_approval_eligible": True,
                    "suggested_risk_level": "low",
                    "confidence_basis": "test",
                    "reason": "test",
                },
            },
        )
        outcomes = [
            RuleOutcome(
                verdict="allow",
                reasons=[PolicyReason("test", "Test reason")],
                risk_level="low",
            )
        ]

        adjusted = _apply_policy_suggestion(request, outcomes)
        assert len(adjusted) == 1
        assert adjusted[0].verdict == "allow"

    def test_suggestion_integrates_with_evaluate_rules(self) -> None:
        """Full integration: evaluate_rules + policy suggestion."""
        request = ActionRequest(
            request_id="req-int",
            tool_name="write_file",
            action_class="write_local",
            risk_hint="high",
            supports_preview=False,
            derived={"target_paths": ["/workspace/file.txt"]},
            context={
                "policy_suggestion": {
                    "template_ref": "tmpl-1",
                    "skip_approval_eligible": True,
                    "suggested_risk_level": "medium",
                    "confidence_basis": "20 invocations, 100% success",
                    "reason": "High confidence",
                },
            },
        )

        outcomes = evaluate_rules(request)
        # Should have been adjusted from approval_required → allow_with_receipt
        approval_outcomes = [o for o in outcomes if o.verdict == "approval_required"]
        allow_outcomes = [o for o in outcomes if o.verdict == "allow_with_receipt"]
        assert len(approval_outcomes) == 0
        assert len(allow_outcomes) >= 1


# ---------------------------------------------------------------------------
# Unit tests: _apply_task_pattern
# ---------------------------------------------------------------------------


def _make_pattern_context(*, invocation_count: int = 5, success_rate: float = 0.9) -> dict:
    return {
        "task_pattern": {
            "pattern_fingerprint": "fp-test",
            "step_descriptions": [
                {"action_class": "write_local", "tool_name": "write_file"},
                {"action_class": "execute_command", "tool_name": "bash"},
            ],
            "invocation_count": invocation_count,
            "success_rate": success_rate,
        }
    }


class TestApplyTaskPattern:
    def test_high_confidence_pattern_downgrades_risk(self) -> None:
        request = ActionRequest(
            request_id="req-pat-1",
            tool_name="write_file",
            action_class="write_local",
            context=_make_pattern_context(invocation_count=5, success_rate=0.90),
        )
        outcomes = [
            RuleOutcome(
                verdict="approval_required",
                reasons=[PolicyReason("test", "Test")],
                obligations=PolicyObligations(require_approval=True),
                risk_level="high",
            )
        ]

        adjusted = _apply_task_pattern(request, outcomes)
        assert len(adjusted) == 1
        assert adjusted[0].risk_level == "medium"
        assert any(r.code == "task_pattern_match" for r in adjusted[0].reasons)

    def test_critical_risk_not_downgraded(self) -> None:
        request = ActionRequest(
            request_id="req-pat-2",
            tool_name="write_file",
            action_class="write_local",
            context=_make_pattern_context(invocation_count=10, success_rate=0.95),
        )
        outcomes = [
            RuleOutcome(
                verdict="approval_required",
                reasons=[PolicyReason("test", "Test")],
                obligations=PolicyObligations(require_approval=True),
                risk_level="critical",
            )
        ]

        adjusted = _apply_task_pattern(request, outcomes)
        assert len(adjusted) == 1
        assert adjusted[0].risk_level == "critical"
        # Still annotated
        assert any(r.code == "task_pattern_match" for r in adjusted[0].reasons)

    def test_no_pattern_unchanged(self) -> None:
        request = ActionRequest(
            request_id="req-pat-3",
            tool_name="write_file",
            action_class="write_local",
            context={},
        )
        outcomes = [
            RuleOutcome(
                verdict="approval_required",
                reasons=[PolicyReason("test", "Test")],
                risk_level="high",
            )
        ]

        adjusted = _apply_task_pattern(request, outcomes)
        assert adjusted == outcomes

    def test_low_invocation_count_unchanged(self) -> None:
        request = ActionRequest(
            request_id="req-pat-4",
            tool_name="write_file",
            action_class="write_local",
            context=_make_pattern_context(invocation_count=2, success_rate=1.0),
        )
        outcomes = [
            RuleOutcome(
                verdict="approval_required",
                reasons=[PolicyReason("test", "Test")],
                risk_level="high",
            )
        ]

        adjusted = _apply_task_pattern(request, outcomes)
        assert adjusted == outcomes

    def test_low_success_rate_unchanged(self) -> None:
        request = ActionRequest(
            request_id="req-pat-5",
            tool_name="write_file",
            action_class="write_local",
            context=_make_pattern_context(invocation_count=10, success_rate=0.5),
        )
        outcomes = [
            RuleOutcome(
                verdict="approval_required",
                reasons=[PolicyReason("test", "Test")],
                risk_level="high",
            )
        ]

        adjusted = _apply_task_pattern(request, outcomes)
        assert adjusted == outcomes

    def test_allow_verdict_annotated_not_downgraded(self) -> None:
        request = ActionRequest(
            request_id="req-pat-6",
            tool_name="read_file",
            action_class="read_local",
            context=_make_pattern_context(invocation_count=5, success_rate=0.90),
        )
        outcomes = [
            RuleOutcome(
                verdict="allow",
                reasons=[PolicyReason("test", "Test")],
                risk_level="low",
            )
        ]

        adjusted = _apply_task_pattern(request, outcomes)
        assert len(adjusted) == 1
        assert adjusted[0].verdict == "allow"
        assert adjusted[0].risk_level == "low"
        assert any(r.code == "task_pattern_match" for r in adjusted[0].reasons)
