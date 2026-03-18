"""Remediation Engine — autonomous patrol-to-fix loop with governed execution."""

from __future__ import annotations

import time
from typing import Any

import structlog

from hermit.kernel.signals.models import EvidenceSignal
from hermit.plugins.builtin.hooks.patrol.remediation_models import (
    RemediationPlan,
    RemediationPolicy,
)

log = structlog.get_logger()

_RISK_LEVELS = ("low", "medium", "high", "critical")

_STRATEGY_MAP: dict[str, str] = {
    "lint_violation": "Run ruff fix on affected files",
    "test_failure": "Analyze test failure and fix the root cause",
    "todo_scan": "Implement the TODO item",
}

_AUTONOMOUS_SOURCE_KINDS: frozenset[str] = frozenset({"lint_violation"})


class RemediationEngine:
    """Maps patrol signals to governed fix tasks.

    Standalone engine — not tightly coupled to PatrolEngine.
    Integration is additive via hook wiring.
    """

    def __init__(self, *, policy: RemediationPolicy | None = None) -> None:
        self._policy = policy or RemediationPolicy()
        self._active_task_ids: list[str] = []
        self._recent_fixes: dict[str, float] = {}  # cooldown_key -> timestamp

    @property
    def policy(self) -> RemediationPolicy:
        return self._policy

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    def plan_remediation(self, signal: EvidenceSignal) -> RemediationPlan:
        """Create a remediation plan from an evidence signal."""
        strategy = _STRATEGY_MAP.get(signal.source_kind, f"Fix issue: {signal.source_kind}")

        affected_paths: list[str] = []
        if signal.metadata:
            paths = signal.metadata.get("affected_paths")
            if isinstance(paths, list):
                affected_paths = [str(p) for p in paths]  # pyright: ignore[reportUnknownVariableType,reportUnknownArgumentType]

        context_parts: list[str] = [strategy]
        if signal.summary:
            context_parts.append(f"Details: {signal.summary}")
        if affected_paths:
            context_parts.append(f"Affected files: {', '.join(affected_paths)}")
        goal_prompt = signal.suggested_goal or ". ".join(context_parts)

        policy_profile = (
            "autonomous" if signal.source_kind in _AUTONOMOUS_SOURCE_KINDS else "default"
        )

        return RemediationPlan(
            signal_ref=signal.signal_id,
            strategy=strategy,
            goal_prompt=goal_prompt,
            policy_profile=policy_profile,
            priority="high" if signal.risk_level == "medium" else "normal",
            affected_paths=affected_paths,
        )

    # ------------------------------------------------------------------
    # Gate
    # ------------------------------------------------------------------

    def should_remediate(self, signal: EvidenceSignal) -> bool:
        """Decide whether a signal warrants autonomous remediation."""
        # Risk threshold check
        threshold = self._policy.auto_fix_risk_threshold
        try:
            threshold_idx = _RISK_LEVELS.index(threshold)
        except ValueError:
            threshold_idx = 1  # default to "medium"
        try:
            signal_idx = _RISK_LEVELS.index(signal.risk_level)
        except ValueError:
            signal_idx = len(_RISK_LEVELS)  # unknown risk -> reject
        if signal_idx > threshold_idx:
            log.debug(
                "remediation_skipped_risk",
                signal=signal.signal_id,
                risk=signal.risk_level,
                threshold=threshold,
            )
            return False

        # Cooldown check — same cooldown_key within cooldown_seconds
        cooldown_key = signal.cooldown_key or signal.signal_id
        now = time.time()
        last_fix = self._recent_fixes.get(cooldown_key)
        if last_fix is not None and (now - last_fix) < self._policy.cooldown_seconds:
            log.debug(
                "remediation_skipped_cooldown",
                signal=signal.signal_id,
                cooldown_key=cooldown_key,
            )
            return False

        # Max concurrent check
        if len(self._active_task_ids) >= self._policy.max_concurrent:
            log.debug(
                "remediation_skipped_max_concurrent",
                signal=signal.signal_id,
                active=len(self._active_task_ids),
            )
            return False

        return True

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute_remediation(self, plan: RemediationPlan, runner: Any) -> str | None:
        """Create a governed fix task via the runner's ingress.

        Returns the task_id on success, None if task creation was not possible.
        """
        enqueue = getattr(runner, "enqueue_ingress", None)
        if enqueue is None:
            log.warning("remediation_no_ingress", signal_ref=plan.signal_ref)
            return None

        task_id: str | None = enqueue(
            goal=plan.goal_prompt,
            source_ref="patrol/remediation",
            policy_profile=plan.policy_profile,
        )

        if task_id:
            self._active_task_ids.append(task_id)
            self._recent_fixes[plan.signal_ref] = time.time()
            log.info(
                "remediation_task_created",
                task_id=task_id,
                strategy=plan.strategy,
                policy=plan.policy_profile,
            )
        return task_id

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------

    def mark_completed(self, task_id: str) -> None:
        """Remove a task from the active list once it finishes."""
        if task_id in self._active_task_ids:
            self._active_task_ids.remove(task_id)

    @property
    def active_task_ids(self) -> list[str]:
        return list(self._active_task_ids)
