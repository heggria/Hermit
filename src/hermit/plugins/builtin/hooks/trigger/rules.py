from __future__ import annotations

from hermit.plugins.builtin.hooks.trigger.models import TriggerRule

BUILTIN_RULES = [
    TriggerRule(
        name="test_failure",
        source_kind="test_failure",
        match_pattern=r"(?i)(FAILED|ERROR|AssertionError|assert\s+.*\s+failed)",
        summary_template="Test failure: {context}",
        suggested_goal_template="Investigate and fix test failure: {context}",
        risk_level="medium",
        policy_profile="default",
        cooldown_key_template="test_failure:{context}",
    ),
    TriggerRule(
        name="lint_violation",
        source_kind="lint_violation",
        match_pattern=r"(?i)(ruff|flake8|pylint).*?(E\d+|W\d+|F\d+|C\d+)",
        summary_template="Lint violation: {context}",
        suggested_goal_template="Fix lint violation: {context}",
        risk_level="low",
        policy_profile="autonomous",
        cooldown_key_template="lint:{context}",
    ),
    TriggerRule(
        name="todo_found",
        source_kind="todo_scan",
        match_pattern=r"(?i)\b(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)",
        summary_template="{context}",
        suggested_goal_template="Address {context}",
        risk_level="low",
        policy_profile="autonomous",
        cooldown_key_template="todo:{context}",
    ),
    TriggerRule(
        name="security_vuln",
        source_kind="security_vuln",
        match_pattern=r"(?i)(CVE-\d{4}-\d+|vulnerability|security\s+issue|critical\s+severity)",
        summary_template="Security issue: {context}",
        suggested_goal_template="Investigate security issue: {context}",
        risk_level="critical",
        policy_profile="default",
        cooldown_key_template="security:{context}",
    ),
]
