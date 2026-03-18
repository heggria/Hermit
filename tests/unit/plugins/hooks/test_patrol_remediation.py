"""Tests for patrol remediation engine."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from hermit.kernel.signals.models import EvidenceSignal
from hermit.plugins.builtin.hooks.patrol.remediation import RemediationEngine
from hermit.plugins.builtin.hooks.patrol.remediation_models import (
    RemediationPolicy,
)


def _make_signal(
    *,
    source_kind: str = "lint_violation",
    risk_level: str = "low",
    summary: str = "2 lint issues found",
    cooldown_key: str = "patrol:lint",
    signal_id: str = "",
    metadata: dict | None = None,
) -> EvidenceSignal:
    return EvidenceSignal(
        source_kind=source_kind,
        source_ref=f"patrol://{source_kind}",
        summary=summary,
        risk_level=risk_level,
        cooldown_key=cooldown_key,
        suggested_goal=f"Fix {source_kind} issues: {summary}",
        metadata=metadata or {},
        **({"signal_id": signal_id} if signal_id else {}),
    )


# ------------------------------------------------------------------
# plan_remediation
# ------------------------------------------------------------------


class TestPlanRemediation:
    def test_lint_strategy(self) -> None:
        engine = RemediationEngine()
        signal = _make_signal(source_kind="lint_violation")
        plan = engine.plan_remediation(signal)

        assert plan.strategy == "Run ruff fix on affected files"
        assert plan.signal_ref == signal.signal_id

    def test_test_failure_strategy(self) -> None:
        engine = RemediationEngine()
        signal = _make_signal(source_kind="test_failure")
        plan = engine.plan_remediation(signal)

        assert plan.strategy == "Analyze test failure and fix the root cause"

    def test_todo_scan_strategy(self) -> None:
        engine = RemediationEngine()
        signal = _make_signal(source_kind="todo_scan")
        plan = engine.plan_remediation(signal)

        assert plan.strategy == "Implement the TODO item"

    def test_unknown_source_kind_fallback(self) -> None:
        engine = RemediationEngine()
        signal = _make_signal(source_kind="custom_check")
        plan = engine.plan_remediation(signal)

        assert "custom_check" in plan.strategy


# ------------------------------------------------------------------
# should_remediate — risk threshold
# ------------------------------------------------------------------


class TestShouldRemediateRisk:
    def test_low_risk_allowed(self) -> None:
        engine = RemediationEngine()
        signal = _make_signal(risk_level="low")
        assert engine.should_remediate(signal) is True

    def test_medium_risk_allowed(self) -> None:
        engine = RemediationEngine()
        signal = _make_signal(risk_level="medium")
        assert engine.should_remediate(signal) is True

    def test_high_risk_blocked(self) -> None:
        engine = RemediationEngine()
        signal = _make_signal(risk_level="high")
        assert engine.should_remediate(signal) is False

    def test_critical_risk_blocked(self) -> None:
        engine = RemediationEngine()
        signal = _make_signal(risk_level="critical")
        assert engine.should_remediate(signal) is False

    def test_custom_threshold_low(self) -> None:
        policy = RemediationPolicy(auto_fix_risk_threshold="low")
        engine = RemediationEngine(policy=policy)
        assert engine.should_remediate(_make_signal(risk_level="low")) is True
        assert engine.should_remediate(_make_signal(risk_level="medium")) is False


# ------------------------------------------------------------------
# should_remediate — cooldown
# ------------------------------------------------------------------


class TestShouldRemediateCooldown:
    def test_cooldown_blocks_repeat(self) -> None:
        engine = RemediationEngine()
        signal = _make_signal(cooldown_key="patrol:lint")
        assert engine.should_remediate(signal) is True
        # Simulate that a fix was recently done for this cooldown_key
        engine._recent_fixes["patrol:lint"] = time.time()
        assert engine.should_remediate(signal) is False

    def test_expired_cooldown_allows(self) -> None:
        policy = RemediationPolicy(cooldown_seconds=10)
        engine = RemediationEngine(policy=policy)
        engine._recent_fixes["patrol:lint"] = time.time() - 20  # expired
        signal = _make_signal(cooldown_key="patrol:lint")
        assert engine.should_remediate(signal) is True


# ------------------------------------------------------------------
# should_remediate — max concurrent
# ------------------------------------------------------------------


class TestShouldRemediateMaxConcurrent:
    def test_max_concurrent_blocks(self) -> None:
        policy = RemediationPolicy(max_concurrent=3)
        engine = RemediationEngine(policy=policy)
        engine._active_task_ids = ["t1", "t2", "t3"]
        signal = _make_signal()
        assert engine.should_remediate(signal) is False

    def test_under_max_concurrent_allows(self) -> None:
        policy = RemediationPolicy(max_concurrent=3)
        engine = RemediationEngine(policy=policy)
        engine._active_task_ids = ["t1", "t2"]
        signal = _make_signal()
        assert engine.should_remediate(signal) is True


# ------------------------------------------------------------------
# execute_remediation
# ------------------------------------------------------------------


class TestExecuteRemediation:
    def test_creates_task_with_correct_tags(self) -> None:
        engine = RemediationEngine()
        signal = _make_signal(source_kind="lint_violation")
        plan = engine.plan_remediation(signal)

        runner = MagicMock()
        runner.enqueue_ingress.return_value = "task-001"

        task_id = engine.execute_remediation(plan, runner)

        assert task_id == "task-001"
        runner.enqueue_ingress.assert_called_once_with(
            goal=plan.goal_prompt,
            source_ref="patrol/remediation",
            policy_profile="autonomous",
        )
        assert "task-001" in engine.active_task_ids

    def test_returns_none_without_ingress(self) -> None:
        engine = RemediationEngine()
        signal = _make_signal()
        plan = engine.plan_remediation(signal)

        runner = object()  # no enqueue_ingress attribute
        assert engine.execute_remediation(plan, runner) is None


# ------------------------------------------------------------------
# Policy profile assignment
# ------------------------------------------------------------------


class TestPolicyProfile:
    def test_lint_gets_autonomous(self) -> None:
        engine = RemediationEngine()
        plan = engine.plan_remediation(_make_signal(source_kind="lint_violation"))
        assert plan.policy_profile == "autonomous"

    def test_test_failure_gets_default(self) -> None:
        engine = RemediationEngine()
        plan = engine.plan_remediation(_make_signal(source_kind="test_failure"))
        assert plan.policy_profile == "default"

    def test_todo_scan_gets_default(self) -> None:
        engine = RemediationEngine()
        plan = engine.plan_remediation(_make_signal(source_kind="todo_scan"))
        assert plan.policy_profile == "default"
