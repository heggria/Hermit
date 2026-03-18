from __future__ import annotations

from hermit.plugins.builtin.hooks.trigger.models import TriggerRule

BUILTIN_RULES = [
    TriggerRule(
        name="test_failure",
        source_kind="test_failure",
        match_pattern=r"(?i)(FAILED|ERROR|AssertionError|assert\s+.*\s+failed)",
        suggested_goal_template="Fix failing test: {match}",
        risk_level="medium",
        policy_profile="default",
        cooldown_key_template="test_failure:{match}",
    ),
    TriggerRule(
        name="lint_violation",
        source_kind="lint_violation",
        match_pattern=r"(?i)(ruff|flake8|pylint).*?(E\d+|W\d+|F\d+|C\d+)",
        suggested_goal_template="Fix lint violation: {match}",
        risk_level="low",
        policy_profile="autonomous",
        cooldown_key_template="lint:{match}",
    ),
    TriggerRule(
        name="todo_found",
        source_kind="todo_scan",
        match_pattern=r"(?i)\b(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)",
        suggested_goal_template="Address TODO: {match}",
        risk_level="low",
        policy_profile="autonomous",
        cooldown_key_template="todo:{match}",
    ),
    TriggerRule(
        name="security_vuln",
        source_kind="security_vuln",
        match_pattern=r"(?i)(CVE-\d{4}-\d+|vulnerability|security\s+issue|critical\s+severity)",
        suggested_goal_template="Investigate security issue: {match}",
        risk_level="critical",
        policy_profile="default",
        cooldown_key_template="security:{match}",
    ),
]
