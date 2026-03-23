"""Pre-policy evidence enrichment for action requests.

Injects template-matching, task-pattern, and trust-score evidence into
``action_request.context`` **before** policy evaluation, so that
``_apply_policy_suggestion()`` in the rules layer can read and act on the data.

Without this enricher the ``policy_suggestion`` was computed *after* policy
evaluation (inside ``synthesize_default``), making it dead code at rule-evaluation
time.
"""

from __future__ import annotations

from typing import Any

import structlog

from hermit.kernel.execution.controller.pattern_learner import TaskPatternLearner
from hermit.kernel.execution.controller.template_learner import ContractTemplateLearner
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.models.models import ActionRequest
from hermit.kernel.policy.trust.scoring import TrustScorer

log = structlog.get_logger()

# Action classes that are inherently safe (read-only / no side effects).
# Template-based policy suggestions are only injected for these classes to
# prevent confusing skip-approval hints on dangerous action classes.
_SKIP_APPROVAL_SAFE_CLASSES = frozenset({
    "read_local",
    "network_read",
    "delegate_reasoning",
    "ephemeral_ui_mutation",
    "execute_command",
    "delegate_execution",
    "approval_resolution",
    "scheduler_mutation",
})


class PolicyEvidenceEnricher:
    """Enrich an ``ActionRequest`` with template, pattern, and trust evidence."""

    def __init__(self, store: KernelStore) -> None:
        self._store = store
        self.template_learner = ContractTemplateLearner(store)
        self.pattern_learner = TaskPatternLearner(store)
        self._trust_scorer = TrustScorer(store)

    def enrich(self, action_request: ActionRequest) -> ActionRequest:
        """Add template / pattern / trust evidence to *action_request.context* in-place.

        Uses ``action_request.risk_hint`` (pre-evaluation default, typically
        ``"high"``) instead of ``policy.risk_level`` to avoid a circular
        dependency with the policy engine.
        """
        self._enrich_template(action_request)
        self._enrich_pattern(action_request)
        self._enrich_signal_risk(action_request)
        self._enrich_trust(action_request)
        return action_request

    # ------------------------------------------------------------------

    def _enrich_template(self, action_request: ActionRequest) -> None:
        expected_effects = self._expected_effects(action_request)
        workspace_root = str(action_request.context.get("workspace_root", "") or "")
        template = self.template_learner.find_matching_template(
            action_class=action_request.action_class,
            tool_name=action_request.tool_name,
            expected_effects=expected_effects,
            workspace_root=workspace_root,
        )
        if template is None:
            return

        action_request.context["matched_template_ref"] = template.source_contract_ref

        suggestion = self.template_learner.compute_policy_suggestion(
            template,
            risk_level=action_request.risk_hint or "high",
        )
        if suggestion is not None:
            # Only inject skip-approval suggestions for safe action classes.
            if action_request.action_class not in _SKIP_APPROVAL_SAFE_CLASSES:
                log.info(
                    "policy_evidence.template_suggestion_filtered",
                    action_class=action_request.action_class,
                    template_ref=suggestion.template_ref,
                    reason="action_class not in safe classes",
                )
                return

            action_request.context["policy_suggestion"] = {
                "template_ref": suggestion.template_ref,
                "suggested_risk_level": suggestion.suggested_risk_level,
                "skip_approval_eligible": suggestion.skip_approval_eligible,
                "confidence_basis": suggestion.confidence_basis,
                "reason": suggestion.reason,
            }
            log.debug(
                "policy_evidence.template_suggestion_injected",
                template_ref=suggestion.template_ref,
                risk_hint=action_request.risk_hint,
            )

    def _enrich_pattern(self, action_request: ActionRequest) -> None:
        goal: str = str(action_request.context.get("task_goal", "") or "")
        if not goal:
            return

        pattern = self.pattern_learner.find_matching_pattern(goal)
        if pattern is None:
            return

        action_request.context["task_pattern"] = {
            "pattern_fingerprint": pattern.pattern_fingerprint,
            "step_descriptions": pattern.step_descriptions,
            "invocation_count": pattern.invocation_count,
            "success_rate": pattern.success_rate,
        }
        log.debug(
            "policy_evidence.task_pattern_injected",
            fingerprint=pattern.pattern_fingerprint,
        )

    # ------------------------------------------------------------------

    def _enrich_signal_risk(self, action_request: ActionRequest) -> None:
        """Inject signal risk indicators from recent actionable signals for the task."""
        task_id: str = str(
            action_request.task_id or action_request.context.get("task_id", "") or ""
        )
        if not task_id:
            return
        if not hasattr(self._store, "actionable_signals"):
            return
        try:
            signals = self._store.actionable_signals(limit=20)
        except Exception:
            return
        task_signals = [s for s in signals if s.task_id == task_id]
        if not task_signals:
            return
        high_risk = [
            {
                "signal_id": s.signal_id,
                "risk_level": s.risk_level,
                "confidence": s.confidence,
                "summary": s.summary[:200],
            }
            for s in task_signals
            if s.risk_level in ("high", "critical")
        ]
        if high_risk:
            action_request.context["signal_risk_indicators"] = high_risk
            log.debug(
                "policy_evidence.signal_risk_injected",
                task_id=task_id,
                indicator_count=len(high_risk),
            )

    def _enrich_trust(self, action_request: ActionRequest) -> None:
        """Add trust-score risk adjustment evidence when sufficient data exists."""
        current_risk = action_request.risk_hint or "high"
        task_id = action_request.task_id or None
        try:
            adjustment = self._trust_scorer.suggest_risk_adjustment(
                action_request.action_class,
                current_risk,
                task_id=task_id,
            )
        except Exception:
            log.debug(
                "policy_evidence.trust_enrichment_skipped",
                action_class=action_request.action_class,
                reason="trust_scorer_error",
            )
            return

        if adjustment is None:
            return

        action_request.context["trust_risk_adjustment"] = {
            "current_risk_band": adjustment.current_risk_band,
            "suggested_risk_band": adjustment.suggested_risk_band,
            "reason": adjustment.reason,
            "trust_score_ref": adjustment.trust_score_ref,
        }
        log.debug(
            "policy_evidence.trust_adjustment_injected",
            action_class=action_request.action_class,
            current=adjustment.current_risk_band,
            suggested=adjustment.suggested_risk_band,
            score=adjustment.trust_score_ref,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _expected_effects(action_request: ActionRequest) -> list[str]:
        effects: list[str] = []
        derived: dict[str, Any] = action_request.derived
        for path in derived.get("target_paths", []):
            effects.append(f"path:{path}")
        for host in derived.get("network_hosts", []):
            effects.append(f"host:{host}")
        preview = str(derived.get("command_preview", "") or "").strip()
        if preview:
            effects.append(f"command:{preview}")
        if not effects:
            effects.append(f"action:{action_request.action_class}")
        return effects
