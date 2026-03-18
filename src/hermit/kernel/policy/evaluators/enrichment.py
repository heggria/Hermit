"""Pre-policy evidence enrichment for action requests.

Injects template-matching and task-pattern evidence into ``action_request.context``
**before** policy evaluation, so that ``_apply_policy_suggestion()`` in the rules
layer can read and act on the data.

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

log = structlog.get_logger()


class PolicyEvidenceEnricher:
    """Enrich an ``ActionRequest`` with template and pattern evidence."""

    def __init__(self, store: KernelStore) -> None:
        self.template_learner = ContractTemplateLearner(store)
        self.pattern_learner = TaskPatternLearner(store)

    def enrich(self, action_request: ActionRequest) -> ActionRequest:
        """Add template / pattern evidence to *action_request.context* in-place.

        Uses ``action_request.risk_hint`` (pre-evaluation default, typically
        ``"high"``) instead of ``policy.risk_level`` to avoid a circular
        dependency with the policy engine.
        """
        self._enrich_template(action_request)
        self._enrich_pattern(action_request)
        return action_request

    # ------------------------------------------------------------------

    def _enrich_template(self, action_request: ActionRequest) -> None:
        expected_effects = self._expected_effects(action_request)
        template = self.template_learner.find_matching_template(
            action_class=action_request.action_class,
            tool_name=action_request.tool_name,
            expected_effects=expected_effects,
        )
        if template is None:
            return

        action_request.context["matched_template_ref"] = template.source_contract_ref

        suggestion = self.template_learner.compute_policy_suggestion(
            template,
            risk_level=action_request.risk_hint or "high",
        )
        if suggestion is not None:
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
