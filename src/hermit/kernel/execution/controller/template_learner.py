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
from dataclasses import dataclass, field
from typing import Any

import structlog

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import (
    ExecutionContractRecord,
    MemoryRecord,
    ReconciliationRecord,
)

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Template descriptor
# ---------------------------------------------------------------------------


@dataclass
class ContractTemplate:
    """Lightweight descriptor extracted from a satisfied execution contract."""

    action_class: str
    tool_name: str
    risk_level: str
    reversibility_class: str
    expected_effects: list[str] = field(default_factory=lambda: [])
    success_criteria: dict[str, Any] = field(default_factory=lambda: {})
    drift_budget: dict[str, Any] = field(default_factory=lambda: {})
    source_contract_ref: str = ""
    source_reconciliation_ref: str = ""


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

    def find_matching_template(
        self,
        *,
        action_class: str,
        tool_name: str,
        expected_effects: list[str],
    ) -> ContractTemplate | None:
        """Return the best-matching template for a similar action, or ``None``."""
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
        tmpl_effects: list[str] = list(sa.get("expected_effects", []))
        tmpl_criteria: dict[str, Any] = dict(sa.get("success_criteria", {}))
        tmpl_budget: dict[str, Any] = dict(sa.get("drift_budget", {}))
        return ContractTemplate(
            action_class=str(sa.get("action_class", "")),
            tool_name=str(sa.get("tool_name", "")),
            risk_level=str(sa.get("risk_level", "medium")),
            reversibility_class=str(sa.get("reversibility_class", "limited")),
            expected_effects=tmpl_effects,
            success_criteria=tmpl_criteria,
            drift_budget=tmpl_budget,
            source_contract_ref=str(sa.get("source_contract_ref", "")),
            source_reconciliation_ref=best.learned_from_reconciliation_ref or "",
        )

    # ------------------------------------------------------------------
    # Degradation: invalidate templates when reconciliation is violated
    # ------------------------------------------------------------------

    def degrade_templates_for_violation(self, reconciliation_ref: str) -> list[str]:
        """Invalidate templates learned from a now-violated reconciliation.

        Returns memory IDs that were invalidated.
        """
        invalidated: list[str] = []
        for record in self._active_templates():
            learned_ref = str(record.learned_from_reconciliation_ref or "").strip()
            if learned_ref != reconciliation_ref:
                continue
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
