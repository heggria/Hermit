"""Tests for kernel/policy/guards/rules.py — main evaluate_rules dispatch and edge cases."""

from __future__ import annotations

from hermit.kernel.policy.guards.rules import (
    POLICY_RULES_VERSION,
    POLICY_STRICTNESS,
    RuleOutcome,
    evaluate_rules,
)
from hermit.kernel.policy.models.models import ActionRequest


class TestPolicyConstants:
    def test_rules_version(self) -> None:
        assert POLICY_RULES_VERSION == "strict-task-first-v2"

    def test_strictness_ordering(self) -> None:
        assert POLICY_STRICTNESS["readonly"] > POLICY_STRICTNESS["supervised"]
        assert POLICY_STRICTNESS["supervised"] > POLICY_STRICTNESS["default"]
        assert POLICY_STRICTNESS["default"] > POLICY_STRICTNESS["autonomous"]


class TestReadonlyProfile:
    def test_readonly_allows_read_local(self) -> None:
        req = ActionRequest(
            request_id="req-1",
            action_class="read_local",
            context={"policy_profile": "readonly"},
        )
        results = evaluate_rules(req)
        # read_local in readonly should be handled by readonly rules evaluator (allow)
        assert len(results) >= 1
        # Should not be denied
        assert results[0].verdict != "deny" or results[0].reasons[0].code != "readonly_profile"

    def test_readonly_denies_write_local(self) -> None:
        req = ActionRequest(
            request_id="req-1",
            action_class="write_local",
            context={"policy_profile": "readonly"},
        )
        results = evaluate_rules(req)
        assert len(results) == 1
        assert results[0].verdict == "deny"
        assert results[0].reasons[0].code == "readonly_profile"

    def test_readonly_denies_execute_command(self) -> None:
        req = ActionRequest(
            request_id="req-1",
            action_class="execute_command",
            context={"policy_profile": "readonly"},
        )
        results = evaluate_rules(req)
        assert results[0].verdict == "deny"


class TestDelegationScopeEnforcement:
    def test_allowed_action_class_passes_through(self) -> None:
        req = ActionRequest(
            request_id="req-1",
            action_class="read_local",
            context={
                "policy_profile": "default",
                "delegation_scope": {
                    "allowed_action_classes": ["read_local", "write_local"],
                },
            },
        )
        results = evaluate_rules(req)
        # read_local is in allowed list, so it should pass to evaluators
        assert (
            results[0].verdict != "deny"
            or results[0].reasons[0].code != "delegation_scope_violation"
        )

    def test_disallowed_action_class_denied(self) -> None:
        req = ActionRequest(
            request_id="req-1",
            action_class="execute_command",
            context={
                "policy_profile": "default",
                "delegation_scope": {
                    "allowed_action_classes": ["read_local"],
                },
            },
        )
        results = evaluate_rules(req)
        assert len(results) == 1
        assert results[0].verdict == "deny"
        assert results[0].reasons[0].code == "delegation_scope_violation"

    def test_empty_allowed_list_means_no_restriction(self) -> None:
        req = ActionRequest(
            request_id="req-1",
            action_class="execute_command",
            context={
                "policy_profile": "default",
                "delegation_scope": {
                    "allowed_action_classes": [],
                },
            },
        )
        results = evaluate_rules(req)
        # Empty allowed list = no restriction, should pass through
        assert not any(
            r.verdict == "deny"
            and any(reason.code == "delegation_scope_violation" for reason in r.reasons)
            for r in results
        )

    def test_delegation_scope_none_no_enforcement(self) -> None:
        req = ActionRequest(
            request_id="req-1",
            action_class="read_local",
            context={"policy_profile": "default"},
        )
        results = evaluate_rules(req)
        # No delegation_scope key, should not trigger deny
        assert not any(
            r.verdict == "deny"
            and any(reason.code == "delegation_scope_violation" for reason in r.reasons)
            for r in results
        )


class TestUnclassifiedMutableAction:
    def test_unknown_action_defaults_to_approval(self) -> None:
        req = ActionRequest(
            request_id="req-1",
            action_class="some_exotic_action",
            context={"policy_profile": "default"},
        )
        results = evaluate_rules(req)
        # Should hit the fallback unclassified mutable action path
        assert any(r.verdict == "approval_required" for r in results)


class TestAutonomousProfile:
    def test_autonomous_read_local_allowed(self) -> None:
        req = ActionRequest(
            request_id="req-1",
            action_class="read_local",
            context={"policy_profile": "autonomous"},
        )
        results = evaluate_rules(req)
        assert len(results) >= 1
        # Autonomous mode should allow or allow_with_receipt
        assert results[0].verdict in ("allow", "allow_with_receipt")


class TestRuleOutcomeDataclass:
    def test_default_fields(self) -> None:
        outcome = RuleOutcome(verdict="allow")
        assert outcome.verdict == "allow"
        assert outcome.reasons == []
        assert outcome.approval_packet is None
        assert outcome.risk_level is None
        assert outcome.action_class_override is None
