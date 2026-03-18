"""Contract template learning from reconciled outcomes.

When a reconciliation result is ``satisfied``, the learner extracts a
template from the execution contract and stores it as a
``contract_template`` memory record.  On subsequent similar actions the
learner retrieves the best-matching template so the contract synthesis
path can prefer parameters that previously succeeded.

Phase 0.2.c -- Criterion #8 implementation.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import structlog

from hermit.kernel.execution.controller.template_models import (
    ContractTemplate,
    TemplateMatch,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import (
    ExecutionContractRecord,
    MemoryRecord,
    ReconciliationRecord,
)

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Fingerprint helpers
# ---------------------------------------------------------------------------


def _action_fingerprint(action_class: str, tool_name: str, effects: list[str]) -> str:
    """Stable fingerprint for grouping similar action patterns."""
    normalised_effects = sorted({_normalise_effect(e) for e in effects})
    raw = f"{action_class}:{tool_name}:{','.join(normalised_effects)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _normalise_effect(effect: str) -> str:
    """Strip volatile path segments so templates generalise across runs."""
    if effect.startswith("path:"):
        import posixpath

        return f"path:*/{posixpath.basename(effect[5:])}"
    return effect


def _effects_similarity(a: list[str], b: list[str]) -> float:
    """Return 0..1 Jaccard similarity on normalised effects."""
    norm_a = {_normalise_effect(e) for e in a}
    norm_b = {_normalise_effect(e) for e in b}
    if not norm_a and not norm_b:
        return 1.0
    if not norm_a or not norm_b:
        return 0.0
    intersection = norm_a & norm_b
    union = norm_a | norm_b
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Learner service
# ---------------------------------------------------------------------------

_MINIMUM_MATCH_SIMILARITY = 0.4
_PROMOTION_THRESHOLD = 3
_HIGH_CONFIDENCE_THRESHOLD = 0.8


class ContractTemplateLearner:
    """Learns from reconciled outcomes and surfaces matching templates."""

    def __init__(self, store: KernelStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Learning: extract template after a satisfied reconciliation
    # ------------------------------------------------------------------

    def learn_from_reconciliation(
        self,
        *,
        reconciliation: ReconciliationRecord,
        contract: ExecutionContractRecord,
    ) -> MemoryRecord | None:
        """Extract a contract template from a *satisfied* reconciliation.

        Returns the created ``MemoryRecord`` (``memory_kind="contract_template"``)
        or ``None`` when the reconciliation is not eligible.
        """
        if reconciliation.result_class != "satisfied":
            return None

        tool_name = str(contract.success_criteria.get("tool_name", "") or "")
        action_class = str(contract.success_criteria.get("action_class", "") or "")
        if not action_class:
            action_class = (
                contract.action_contract_refs[0] if contract.action_contract_refs else "unknown"
            )

        risk_level = str(contract.risk_budget.get("risk_level", "medium") or "medium")
        fingerprint = _action_fingerprint(action_class, tool_name, list(contract.expected_effects))

        # Check for an existing template with the same fingerprint
        existing = self._find_template_by_fingerprint(fingerprint)
        if existing is not None:
            # Record the additional reconciliation as validation
            self.store.update_memory_record(
                existing.memory_id,
                validation_basis=f"reconciliation:{reconciliation.reconciliation_id}",
                last_validated_at=time.time(),
            )
            self.store.append_event(
                event_type="contract_template.reinforced",
                entity_type="memory_record",
                entity_id=existing.memory_id,
                task_id=reconciliation.task_id,
                step_id=reconciliation.step_id,
                actor="kernel",
                payload={
                    "reconciliation_ref": reconciliation.reconciliation_id,
                    "fingerprint": fingerprint,
                },
            )
            log.debug(
                "contract_template.reinforced",
                memory_id=existing.memory_id,
                fingerprint=fingerprint,
            )
            return existing

        structured_assertion: dict[str, Any] = {
            "action_class": action_class,
            "tool_name": tool_name,
            "risk_level": risk_level,
            "reversibility_class": contract.reversibility_class,
            "expected_effects": list(contract.expected_effects),
            "success_criteria": dict(contract.success_criteria),
            "drift_budget": dict(contract.drift_budget),
            "fingerprint": fingerprint,
            "source_contract_ref": contract.contract_id,
            "invocation_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "success_rate": 0.0,
            "last_failure_at": None,
            "last_used_at": time.time(),
            "resource_scope_pattern": list(contract.drift_budget.get("resource_scopes", [])),
            "constraint_defaults": {
                "reversibility_class": contract.reversibility_class,
                "risk_level": risk_level,
            },
            "evidence_requirements": list(contract.required_receipt_classes),
        }

        memory = self.store.create_memory_record(
            task_id=reconciliation.task_id,
            conversation_id=None,
            category="contract_template",
            claim_text=(
                f"Learned contract template for {action_class}/{tool_name} "
                f"with effects {', '.join(contract.expected_effects[:3])}"
            ),
            structured_assertion=structured_assertion,
            scope_kind="global",
            scope_ref="",
            promotion_reason="reconciliation_satisfied",
            retention_class="durable_template",
            status="active",
            confidence=0.8,
            trust_tier="durable",
            evidence_refs=[contract.contract_id, reconciliation.reconciliation_id],
            memory_kind="contract_template",
            validation_basis=f"reconciliation:{reconciliation.reconciliation_id}",
            last_validated_at=time.time(),
            learned_from_reconciliation_ref=reconciliation.reconciliation_id,
        )

        self.store.append_event(
            event_type="contract_template.learned",
            entity_type="memory_record",
            entity_id=memory.memory_id,
            task_id=reconciliation.task_id,
            step_id=reconciliation.step_id,
            actor="kernel",
            payload={
                "reconciliation_ref": reconciliation.reconciliation_id,
                "contract_ref": contract.contract_id,
                "fingerprint": fingerprint,
                "action_class": action_class,
                "tool_name": tool_name,
            },
        )

        log.info(
            "contract_template.learned",
            memory_id=memory.memory_id,
            fingerprint=fingerprint,
            action_class=action_class,
            tool_name=tool_name,
        )
        return memory

    # ------------------------------------------------------------------
    # Matching: find templates for a proposed action
    # ------------------------------------------------------------------

    def _reinforcement_count(self, memory_id: str) -> int:
        """Count ``contract_template.reinforced`` events for a memory record."""
        with self.store._lock:  # pyright: ignore[reportPrivateUsage]
            rows = self.store._rows(  # pyright: ignore[reportPrivateUsage]
                "SELECT COUNT(*) AS cnt FROM events "
                "WHERE entity_type = 'memory_record' AND entity_id = ? "
                "AND event_type = 'contract_template.reinforced'",
                (memory_id,),
            )
        return int(rows[0]["cnt"]) if rows else 0

    def _success_count_for(self, record: MemoryRecord) -> int:
        """Total success count = 1 (initial creation) + reinforcement events."""
        return 1 + self._reinforcement_count(record.memory_id)

    def find_matching_template(
        self,
        *,
        action_class: str,
        tool_name: str,
        expected_effects: list[str],
    ) -> ContractTemplate | None:
        """Return the best-matching template for a similar action, or ``None``.

        Only templates that have been promoted (>= ``_PROMOTION_THRESHOLD``
        successful reconciliations) are considered.
        """
        templates = self._active_templates()
        if not templates:
            return None

        best: MemoryRecord | None = None
        best_score = 0.0

        for record in templates:
            sa = dict(record.structured_assertion or {})
            rec_action = str(sa.get("action_class", ""))
            rec_tool = str(sa.get("tool_name", ""))

            # Must match action class
            if rec_action != action_class:
                continue

            # Promotion threshold: need >= _PROMOTION_THRESHOLD successes
            success_count = self._success_count_for(record)
            if success_count < _PROMOTION_THRESHOLD:
                continue

            rec_effects = list(sa.get("expected_effects", []))
            similarity = _effects_similarity(expected_effects, rec_effects)

            # Bonus for exact tool match
            tool_bonus = 0.3 if rec_tool == tool_name else 0.0
            composite = similarity + tool_bonus
            if composite > best_score and similarity >= _MINIMUM_MATCH_SIMILARITY:
                best_score = composite
                best = record

        if best is None:
            return None

        sa = dict(best.structured_assertion or {})
        return self._template_from_assertion(sa, best)

    def match_template(
        self,
        *,
        action_class: str,
        tool_name: str,
        expected_effects: list[str],
    ) -> TemplateMatch | None:
        """Return a ``TemplateMatch`` with confidence, or ``None``.

        This is the rich-result variant of ``find_matching_template``.
        """
        templates = self._active_templates()
        if not templates:
            return None

        best: MemoryRecord | None = None
        best_score = 0.0
        best_reasons: list[str] = []
        best_similarity = 0.0

        for record in templates:
            sa = dict(record.structured_assertion or {})
            rec_action = str(sa.get("action_class", ""))
            rec_tool = str(sa.get("tool_name", ""))

            if rec_action != action_class:
                continue

            success_count = self._success_count_for(record)
            if success_count < _PROMOTION_THRESHOLD:
                continue

            rec_effects = list(sa.get("expected_effects", []))
            similarity = _effects_similarity(expected_effects, rec_effects)

            tool_bonus = 0.3 if rec_tool == tool_name else 0.0
            composite = similarity + tool_bonus
            if composite > best_score and similarity >= _MINIMUM_MATCH_SIMILARITY:
                best_score = composite
                best = record
                best_similarity = similarity
                reasons = [f"action_class={rec_action}"]
                if rec_tool == tool_name:
                    reasons.append(f"tool_name={rec_tool}")
                reasons.append(f"effects_similarity={similarity:.2f}")
                reasons.append(f"success_count={success_count}")
                best_reasons = reasons

        if best is None:
            return None

        sa = dict(best.structured_assertion or {})
        confidence = min(1.0, best_similarity * 0.7 + 0.3)
        template = self._template_from_assertion(sa, best)
        return TemplateMatch(
            template_ref=best.memory_id,
            confidence=confidence,
            match_reasons=best_reasons,
            template=template,
        )

    # ------------------------------------------------------------------
    # Application: pre-fill a contract from a template
    # ------------------------------------------------------------------

    def apply_template(
        self,
        template: ContractTemplate,
        *,
        action_class: str,
        tool_name: str,
        expected_effects: list[str],
        resource_scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate pre-filled contract parameters from a template.

        Returns a dict of contract fields that can be merged into
        ``create_execution_contract`` kwargs.
        """
        return {
            "reversibility_class": template.reversibility_class,
            "risk_budget": {
                "risk_level": template.risk_level,
                "approval_required": template.success_criteria.get("requires_receipt", False),
            },
            "drift_budget": dict(template.drift_budget)
            if template.drift_budget
            else {
                "resource_scopes": list(resource_scopes or []),
                "outside_workspace": False,
            },
            "required_receipt_classes": list(template.evidence_requirements),
            "expected_effects": expected_effects,
            "success_criteria": {
                "tool_name": tool_name,
                "action_class": action_class,
                "requires_receipt": bool(template.evidence_requirements),
            },
            "selected_template_ref": template.source_contract_ref,
        }

    # ------------------------------------------------------------------
    # Degradation: invalidate templates when reconciliation is violated
    # ------------------------------------------------------------------

    def degrade_templates_for_violation(self, reconciliation_ref: str) -> list[str]:
        """Record failure for templates learned from a now-violated reconciliation.

        Uses success_rate-based degradation: templates are only invalidated
        when invocation_count >= 5 and success_rate < 0.3. Otherwise the
        failure is recorded but the template remains active.

        Returns memory IDs that were invalidated.
        """
        invalidated: list[str] = []
        for record in self._active_templates():
            learned_ref = str(record.learned_from_reconciliation_ref or "").strip()
            if learned_ref != reconciliation_ref:
                continue

            sa = dict(record.structured_assertion or {})
            failure_count = int(sa.get("failure_count", 0)) + 1
            invocation_count = int(sa.get("invocation_count", 0))
            # Count this as an invocation if it wasn't already tracked
            if invocation_count == 0:
                invocation_count = 1
            success_count = int(sa.get("success_count", 0))
            success_rate = success_count / invocation_count if invocation_count > 0 else 0.0

            sa["failure_count"] = failure_count
            sa["invocation_count"] = invocation_count
            sa["success_rate"] = success_rate
            sa["last_failure_at"] = time.time()

            self.store.update_memory_record(
                record.memory_id,
                structured_assertion=sa,
            )

            # Only invalidate if enough data and low success rate
            if invocation_count >= 5 and success_rate < 0.3:
                self.store.update_memory_record(
                    record.memory_id,
                    status="invalidated",
                    invalidation_reason=f"reconciliation_violated:{reconciliation_ref}",
                    invalidated_at=time.time(),
                )
                invalidated.append(record.memory_id)

        return invalidated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _active_templates(self) -> list[MemoryRecord]:
        """Return all active ``contract_template`` memory records."""
        all_active = self.store.list_memory_records(status="active", limit=500)
        return [r for r in all_active if r.memory_kind == "contract_template"]

    def _find_template_by_fingerprint(self, fingerprint: str) -> MemoryRecord | None:
        for record in self._active_templates():
            sa = dict(record.structured_assertion or {})
            if str(sa.get("fingerprint", "")) == fingerprint:
                return record
        return None

    @staticmethod
    def _template_from_assertion(sa: dict[str, Any], record: MemoryRecord) -> ContractTemplate:
        """Build a ``ContractTemplate`` from a memory record's structured assertion."""
        return ContractTemplate(
            action_class=str(sa.get("action_class", "")),
            tool_name=str(sa.get("tool_name", "")),
            risk_level=str(sa.get("risk_level", "medium")),
            reversibility_class=str(sa.get("reversibility_class", "limited")),
            expected_effects=list(sa.get("expected_effects", [])),
            success_criteria=dict(sa.get("success_criteria", {})),
            drift_budget=dict(sa.get("drift_budget", {})),
            source_contract_ref=str(sa.get("source_contract_ref", "")),
            source_reconciliation_ref=record.learned_from_reconciliation_ref or "",
            success_count=int(sa.get("success_count", 1)),
            last_used_at=float(sa.get("last_used_at", 0.0)),
            resource_scope_pattern=list(sa.get("resource_scope_pattern", [])),
            constraint_defaults=dict(sa.get("constraint_defaults", {})),
            evidence_requirements=list(sa.get("evidence_requirements", [])),
        )
