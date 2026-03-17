"""Hypothesis property-based tests for policy engine core logic."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from hermit.kernel.policy.guards.merge import _PRIORITY, merge_outcomes
from hermit.kernel.policy.guards.rules import RuleOutcome, evaluate_rules
from hermit.kernel.policy.models.models import (
    ActionRequest,
    PolicyObligations,
    PolicyReason,
)

_VERDICTS = list(_PRIORITY.keys())

_reason_strategy = st.builds(
    PolicyReason,
    code=st.text(min_size=1, max_size=20),
    message=st.text(min_size=1, max_size=80),
    severity=st.sampled_from(["info", "warning", "error"]),
)

_obligations_strategy = st.builds(
    PolicyObligations,
    require_receipt=st.booleans(),
    require_preview=st.booleans(),
    require_approval=st.booleans(),
    require_evidence=st.booleans(),
    approval_risk_level=st.sampled_from([None, "low", "medium", "high", "critical"]),
)

_outcome_strategy = st.builds(
    RuleOutcome,
    verdict=st.sampled_from(_VERDICTS),
    reasons=st.lists(_reason_strategy, min_size=0, max_size=3),
    obligations=_obligations_strategy,
    normalized_constraints=st.just({}),
    approval_packet=st.none(),
    risk_level=st.sampled_from([None, "low", "medium", "high", "critical"]),
)


# ---------------------------------------------------------------------------
# merge_outcomes properties
# ---------------------------------------------------------------------------


@given(outcomes=st.lists(_outcome_strategy, min_size=1, max_size=8))
@settings(max_examples=200)
def test_merge_verdict_equals_highest_priority(outcomes: list[RuleOutcome]) -> None:
    decision = merge_outcomes(outcomes, action_class="write_local", default_risk="high")
    expected_verdict = max(outcomes, key=lambda o: _PRIORITY.get(o.verdict, 0)).verdict
    assert decision.verdict == expected_verdict


@given(outcomes=st.lists(_outcome_strategy, min_size=1, max_size=8))
@settings(max_examples=200)
def test_merge_accumulates_all_reasons(outcomes: list[RuleOutcome]) -> None:
    decision = merge_outcomes(outcomes, action_class="write_local", default_risk="high")
    total_reasons = sum(len(o.reasons) for o in outcomes)
    assert len(decision.reasons) == total_reasons


@given(outcomes=st.lists(_outcome_strategy, min_size=1, max_size=8))
@settings(max_examples=200)
def test_merge_obligations_use_or_semantics(outcomes: list[RuleOutcome]) -> None:
    decision = merge_outcomes(outcomes, action_class="write_local", default_risk="high")
    assert decision.obligations.require_receipt == any(
        o.obligations.require_receipt for o in outcomes
    )
    assert decision.obligations.require_preview == any(
        o.obligations.require_preview for o in outcomes
    )
    assert decision.obligations.require_approval == any(
        o.obligations.require_approval for o in outcomes
    )
    assert decision.obligations.require_evidence == any(
        o.obligations.require_evidence for o in outcomes
    )


# ---------------------------------------------------------------------------
# evaluate_rules properties
# ---------------------------------------------------------------------------


def _make_request(action_class: str, **overrides: object) -> ActionRequest:
    defaults: dict[str, object] = {
        "request_id": "prop-test",
        "tool_name": "test_tool",
        "action_class": action_class,
        "risk_hint": "high",
        "context": {},
        "derived": {},
        "actor": {"kind": "agent", "agent_id": "hermit"},
    }
    defaults.update(overrides)
    return ActionRequest(**defaults)  # type: ignore[arg-type]


@given(st.data())
@settings(max_examples=50)
def test_read_local_always_allowed(data: st.DataObject) -> None:
    profile = data.draw(st.sampled_from(["default", "readonly", "admin", ""]))
    request = _make_request("read_local", context={"policy_profile": profile})
    outcomes = evaluate_rules(request)
    assert len(outcomes) >= 1
    assert outcomes[0].verdict == "allow"


@given(
    action_class=st.sampled_from(
        [
            "write_local",
            "patch_file",
            "execute_command",
            "network_write",
            "external_mutation",
        ]
    )
)
@settings(max_examples=50)
def test_readonly_profile_denies_non_read(action_class: str) -> None:
    request = _make_request(action_class, context={"policy_profile": "readonly"})
    outcomes = evaluate_rules(request)
    assert len(outcomes) >= 1
    assert outcomes[0].verdict == "deny"


@given(
    action_class=st.sampled_from(
        [
            "read_local",
            "network_read",
            "write_local",
            "execute_command",
            "network_write",
            "external_mutation",
            "memory_write",
            "vcs_mutation",
            "delegate_reasoning",
            "approval_resolution",
            "scheduler_mutation",
            "rollback",
            "ephemeral_ui_mutation",
        ]
    )
)
@settings(max_examples=100)
def test_evaluate_rules_returns_non_empty(action_class: str) -> None:
    request = _make_request(action_class)
    outcomes = evaluate_rules(request)
    assert len(outcomes) >= 1


# ---------------------------------------------------------------------------
# autonomous profile tests
# ---------------------------------------------------------------------------


def test_autonomous_read_local_allowed() -> None:
    request = _make_request("read_local", context={"policy_profile": "autonomous"})
    outcomes = evaluate_rules(request)
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "allow"
    assert outcomes[0].reasons[0].code == "autonomous_read"


def test_autonomous_safe_actions_allowed() -> None:
    for action_class in ("network_read", "delegate_reasoning", "ephemeral_ui_mutation"):
        request = _make_request(action_class, context={"policy_profile": "autonomous"})
        outcomes = evaluate_rules(request)
        assert len(outcomes) == 1
        assert outcomes[0].verdict == "allow"
        assert outcomes[0].reasons[0].code == "autonomous_passthrough"


def test_autonomous_dangerous_shell_denied() -> None:
    request = _make_request(
        "execute_command",
        context={"policy_profile": "autonomous"},
        derived={"command_flags": {"sudo": True}},
    )
    outcomes = evaluate_rules(request)
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "deny"
    assert outcomes[0].reasons[0].code == "dangerous_shell"


def test_autonomous_curl_pipe_sh_denied() -> None:
    request = _make_request(
        "execute_command",
        context={"policy_profile": "autonomous"},
        derived={"command_flags": {"curl_pipe_sh": True}},
    )
    outcomes = evaluate_rules(request)
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "deny"


def test_autonomous_protected_path_denied() -> None:
    request = _make_request(
        "write_local",
        context={"policy_profile": "autonomous"},
        derived={"sensitive_paths": ["/etc/passwd"], "outside_workspace": True},
    )
    outcomes = evaluate_rules(request)
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "deny"
    assert outcomes[0].reasons[0].code == "protected_path"


def test_autonomous_default_action_allowed_with_receipt() -> None:
    request = _make_request("write_local", context={"policy_profile": "autonomous"})
    outcomes = evaluate_rules(request)
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "allow_with_receipt"
    assert outcomes[0].obligations.require_receipt is True
    assert outcomes[0].obligations.require_approval is False
