"""Tests for action_classes NOT covered by any specific guard.

The audit of kernel/policy/guards/ found these action_classes declared in
ToolSpec registrations but NOT explicitly handled by any guard rule file:

- spec_generation    (readonly=True, risk_hint="low")
- task_decomposition (readonly=True, risk_hint="low")
- patrol_execution   (readonly=False, risk_hint="medium")
- unknown            (fallback for tools that don't declare action_class)

All of them fall through every evaluator in the guard chain and land on the
"unknown_mutation" fallback path in evaluate_rules(), which returns
verdict="approval_required".

This test module verifies that behaviour and documents the gap so that if
guards are later added for these classes, the tests can be updated.
"""

from __future__ import annotations

import pytest

from hermit.kernel.policy.guards.rules import evaluate_rules
from hermit.kernel.policy.guards.rules_readonly import evaluate_readonly_rules
from hermit.kernel.policy.models.models import ActionRequest


@pytest.fixture
def make_request():
    """Factory fixture: creates an ActionRequest with sensible defaults."""

    def _make(
        action_class: str,
        *,
        risk_hint: str | None = None,
        requires_receipt: bool = False,
        policy_profile: str = "default",
    ) -> ActionRequest:
        return ActionRequest(
            request_id="req-gap-test-001",
            action_class=action_class,
            risk_hint=risk_hint,
            requires_receipt=requires_receipt,
            context={
                "policy_profile": policy_profile,
                "cwd": "/tmp/test",
                "repo_root": "/tmp/test",
                "workspace_root": "/tmp/test",
            },
        )

    return _make


# ──────────────────────────────────────────────────────────────────────
# 1. Verify these action_classes are NOT handled by the readonly guard
# ──────────────────────────────────────────────────────────────────────


class TestReadonlyGuardDoesNotCover:
    @pytest.mark.parametrize(
        "action_class",
        ["spec_generation", "task_decomposition", "patrol_execution", "unknown"],
    )
    def test_readonly_guard_returns_none(self, make_request, action_class: str) -> None:
        """The readonly guard should return None (no match) for these classes."""
        result = evaluate_readonly_rules(make_request(action_class))
        assert result is None, f"readonly guard unexpectedly matched action_class={action_class!r}"


# ──────────────────────────────────────────────────────────────────────
# 2. Verify these action_classes fall through to the unknown_mutation
#    fallback under the "default" policy profile.
# ──────────────────────────────────────────────────────────────────────


class TestFallbackToUnknownMutation:
    @pytest.mark.parametrize(
        "action_class,risk",
        [
            ("spec_generation", "low"),
            ("task_decomposition", "low"),
            ("patrol_execution", "medium"),
            ("unknown", None),
        ],
    )
    def test_uncovered_action_class_requires_approval(
        self, make_request, action_class: str, risk: str | None
    ) -> None:
        """Uncovered action_classes must hit the fallback and require approval."""
        request = make_request(action_class, risk_hint=risk)
        outcomes = evaluate_rules(request)

        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert outcome.verdict == "approval_required", (
            f"Expected approval_required for {action_class!r}, got {outcome.verdict!r}"
        )
        assert outcome.reasons[0].code == "unknown_mutation"
        assert outcome.obligations.require_approval is True
        assert outcome.obligations.require_receipt is True

    @pytest.mark.parametrize(
        "action_class,risk,expected_risk",
        [
            ("spec_generation", "low", "low"),
            ("task_decomposition", "low", "low"),
            ("patrol_execution", "medium", "medium"),
            ("unknown", None, "high"),  # fallback defaults to "high" when no hint
        ],
    )
    def test_risk_level_propagated(
        self, make_request, action_class: str, risk: str | None, expected_risk: str
    ) -> None:
        """The fallback should use the risk_hint if present, else default to 'high'."""
        request = make_request(action_class, risk_hint=risk)
        outcomes = evaluate_rules(request)
        assert outcomes[0].risk_level == expected_risk


# ──────────────────────────────────────────────────────────────────────
# 3. Verify the autonomous profile still auto-approves these
# ──────────────────────────────────────────────────────────────────────


class TestAutonomousProfileHandlesUncovered:
    @pytest.mark.parametrize(
        "action_class",
        ["spec_generation", "task_decomposition", "patrol_execution", "unknown"],
    )
    def test_autonomous_allows_with_receipt(self, make_request, action_class: str) -> None:
        """Under autonomous profile, uncovered classes should be allowed (with receipt)."""
        request = make_request(action_class, policy_profile="autonomous")
        outcomes = evaluate_rules(request)

        assert len(outcomes) == 1
        outcome = outcomes[0]
        # Autonomous should auto-approve; verdict depends on implementation
        # but should NOT be "deny".
        assert outcome.verdict in {"allow", "allow_with_receipt"}, (
            f"Autonomous should not deny {action_class!r}, got {outcome.verdict!r}"
        )


# ──────────────────────────────────────────────────────────────────────
# 4. Verify the readonly profile denies these (they aren't read_local)
# ──────────────────────────────────────────────────────────────────────


class TestReadonlyProfileDeniesUncovered:
    @pytest.mark.parametrize(
        "action_class",
        ["spec_generation", "task_decomposition", "patrol_execution", "unknown"],
    )
    def test_readonly_profile_denies(self, make_request, action_class: str) -> None:
        """Readonly profile should deny anything that isn't read_local."""
        request = make_request(action_class, policy_profile="readonly")
        outcomes = evaluate_rules(request)

        assert len(outcomes) == 1
        assert outcomes[0].verdict == "deny"
