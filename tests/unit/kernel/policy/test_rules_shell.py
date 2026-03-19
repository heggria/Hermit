from __future__ import annotations

import pytest

from hermit.kernel.policy.guards.rules_shell import evaluate_shell_rules
from hermit.kernel.policy.models.models import ActionRequest


def _make_request(
    action_class: str = "execute_command",
    command_flags: dict | None = None,
    tool_name: str = "bash",
    risk_hint: str = "high",
) -> ActionRequest:
    derived: dict = {}
    if command_flags is not None:
        derived["command_flags"] = command_flags
    return ActionRequest(
        request_id="test-req-1",
        action_class=action_class,
        tool_name=tool_name,
        risk_hint=risk_hint,
        derived=derived,
    )


# --- Non execute_command actions return None ---


@pytest.mark.parametrize(
    "action_class",
    ["read_file", "write_file", "unknown", "search", ""],
)
def test_non_execute_command_returns_none(action_class: str) -> None:
    request = _make_request(action_class=action_class)
    assert evaluate_shell_rules(request) is None


# --- Dangerous patterns are denied ---


def test_sudo_command_denied() -> None:
    request = _make_request(command_flags={"sudo": True})
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "deny"
    assert outcomes[0].risk_level == "critical"
    assert outcomes[0].reasons[0].code == "dangerous_shell"


def test_curl_pipe_sh_denied() -> None:
    request = _make_request(command_flags={"curl_pipe_sh": True})
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "deny"
    assert outcomes[0].risk_level == "critical"
    assert outcomes[0].reasons[0].code == "dangerous_shell"


def test_sudo_and_curl_pipe_sh_both_denied() -> None:
    request = _make_request(command_flags={"sudo": True, "curl_pipe_sh": True})
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "deny"


# --- Git push requires approval ---


def test_git_push_requires_approval() -> None:
    request = _make_request(command_flags={"git_push": True})
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "approval_required"
    assert outcomes[0].risk_level == "critical"
    assert outcomes[0].reasons[0].code == "git_push"
    assert outcomes[0].obligations is not None
    assert outcomes[0].obligations.require_approval is True
    assert outcomes[0].obligations.require_receipt is True
    assert outcomes[0].obligations.require_preview is True
    assert outcomes[0].approval_packet is not None
    assert outcomes[0].approval_packet["risk_level"] == "critical"


# --- Mutable shell commands require approval ---


@pytest.mark.parametrize(
    "flag",
    ["writes_disk", "deletes_files", "network_access"],
)
def test_mutable_shell_requires_approval(flag: str) -> None:
    request = _make_request(command_flags={flag: True}, risk_hint="high")
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "approval_required"
    assert outcomes[0].reasons[0].code == "mutable_shell"
    assert outcomes[0].obligations is not None
    assert outcomes[0].obligations.require_approval is True


def test_mutable_shell_uses_risk_hint() -> None:
    request = _make_request(command_flags={"writes_disk": True}, risk_hint="medium")
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert outcomes[0].risk_level == "medium"
    assert outcomes[0].obligations is not None
    assert outcomes[0].obligations.approval_risk_level == "medium"


def test_mutable_shell_defaults_critical_when_no_risk_hint() -> None:
    request = _make_request(command_flags={"network_access": True}, risk_hint="")
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert outcomes[0].risk_level == "critical"


def test_mutable_shell_approval_packet_includes_tool_name() -> None:
    request = _make_request(command_flags={"writes_disk": True}, tool_name="shell_exec")
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert outcomes[0].approval_packet is not None
    assert "shell_exec" in outcomes[0].approval_packet["title"]


# --- Read-only shell commands ---


def test_readonly_shell_allowed_with_receipt() -> None:
    request = _make_request(command_flags={})
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "allow_with_receipt"
    assert outcomes[0].risk_level == "medium"
    assert outcomes[0].reasons[0].code == "readonly_shell"
    assert outcomes[0].obligations is not None
    assert outcomes[0].obligations.require_receipt is True
    assert outcomes[0].obligations.require_approval is False
    assert outcomes[0].normalized_constraints == {"shell_mode": "readonly"}


def test_readonly_shell_when_flags_all_false() -> None:
    request = _make_request(
        command_flags={"writes_disk": False, "deletes_files": False, "network_access": False}
    )
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert outcomes[0].verdict == "allow_with_receipt"


def test_no_command_flags_key_treated_as_readonly() -> None:
    """When derived has no command_flags key at all, treat as readonly."""
    request = _make_request(command_flags=None)
    # command_flags=None means derived={} via _make_request
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert outcomes[0].verdict == "allow_with_receipt"


# --- git_push takes precedence over mutable flags ---


def test_git_push_precedence_over_mutable_flags() -> None:
    request = _make_request(command_flags={"git_push": True, "network_access": True})
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert len(outcomes) == 1
    assert outcomes[0].reasons[0].code == "git_push"


# --- dangerous patterns short-circuit ---


def test_dangerous_pattern_short_circuits_other_flags() -> None:
    request = _make_request(command_flags={"sudo": True, "git_push": True, "writes_disk": True})
    outcomes = evaluate_shell_rules(request)

    assert outcomes is not None
    assert len(outcomes) == 1
    assert outcomes[0].verdict == "deny"
