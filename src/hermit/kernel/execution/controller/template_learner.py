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
from pathlib import Path
from typing import Any

import structlog

from hermit.kernel.execution.controller.template_models import (
    ContractTemplate,
    PolicySuggestion,
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


def _resolve_workspace(workspace_root: str) -> str:
    """Resolve workspace_root to an absolute path string, or empty string."""
    if not workspace_root:
        return ""
    return str(Path(workspace_root).resolve())


# ---------------------------------------------------------------------------
# Learner service
# ---------------------------------------------------------------------------

_MINIMUM_MATCH_SIMILARITY = 0.4
_PROMOTION_THRESHOLD = 3
_HIGH_CONFIDENCE_THRESHOLD = 0.8
_SCOPE_BONUS = 0.1


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
        workspace_root: str = "",
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

        # Compute scope from workspace_root
        resolved_ws = _resolve_workspace(workspace_root)
        scope_kind = "workspace" if resolved_ws else "global"
        scope_ref = resolved_ws if resolved_ws else "global"

        # Check for an existing template with the same fingerprint and scope
        existing = self._find_template_by_fingerprint(fingerprint, workspace_root=workspace_root)
        if existing is not None:
            # Update tracking fields in structured assertion
            sa = dict(existing.structured_assertion or {})
            inv = int(sa.get("invocation_count", 0)) + 1
            succ = int(sa.get("success_count", 0)) + 1
            sa["invocation_count"] = inv
            sa["success_count"] = succ
            sa["success_rate"] = succ / inv if inv > 0 else 0.0
            sa["last_used_at"] = time.time()
            # Record the additional reconciliation as validation
            self.store.update_memory_record(
                existing.memory_id,
                structured_assertion=sa,
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
            # Check for cross-workspace promotion after reinforcement
            if scope_kind == "workspace":
                self.promote_to_global(fingerprint=fingerprint)
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
            "invocation_count": 1,
            "success_count": 1,
            "failure_count": 0,
            "success_rate": 1.0,
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
            scope_kind=scope_kind,
            scope_ref=scope_ref,
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
                "scope_kind": scope_kind,
                "scope_ref": scope_ref,
            },
        )

        log.info(
            "contract_template.learned",
            memory_id=memory.memory_id,
            fingerprint=fingerprint,
            action_class=action_class,
            tool_name=tool_name,
            scope_kind=scope_kind,
        )
        return memory

    # ------------------------------------------------------------------
    # Matching: find templates for a proposed action
    # ------------------------------------------------------------------

    def _reinforcement_count(self, memory_id: str) -> int:
        """Count ``contract_template.reinforced`` events for a memory record."""
        rows = self.store._rows(
            "SELECT COUNT(*) AS cnt FROM events "
            "WHERE entity_type = 'memory_record' AND entity_id = ? "
            "AND event_type = 'contract_template.reinforced'",
            (memory_id,),
        )
        return int(rows[0]["cnt"]) if rows else 0

    def _success_count_for(self, record: MemoryRecord) -> int:
        """Total success count from structured assertion, falling back to event counting."""
        sa = dict(record.structured_assertion or {})
        sa_count = int(sa.get("success_count", 0))
        if sa_count > 0:
            return sa_count
        return 1 + self._reinforcement_count(record.memory_id)

    def find_matching_template(
        self,
        *,
        action_class: str,
        tool_name: str,
        expected_effects: list[str],
        workspace_root: str = "",
    ) -> ContractTemplate | None:
        """Return the best-matching template for a similar action, or ``None``.

        Only templates that have been promoted (>= ``_PROMOTION_THRESHOLD``
        successful reconciliations) are considered.  Workspace-scoped templates
        get a scoring bonus over global ones.
        """
        templates = self._active_templates(workspace_root=workspace_root)
        if not templates:
            return None

        resolved_ws = _resolve_workspace(workspace_root)
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
            # Bonus for workspace-scoped template matching current workspace
            scope_bonus = (
                _SCOPE_BONUS
                if resolved_ws
                and record.scope_kind == "workspace"
                and record.scope_ref == resolved_ws
                else 0.0
            )
            composite = similarity + tool_bonus + scope_bonus
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
        workspace_root: str = "",
    ) -> TemplateMatch | None:
        """Return a ``TemplateMatch`` with confidence, or ``None``.

        This is the rich-result variant of ``find_matching_template``.
        """
        templates = self._active_templates(workspace_root=workspace_root)
        if not templates:
            return None

        resolved_ws = _resolve_workspace(workspace_root)
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
            scope_bonus = (
                _SCOPE_BONUS
                if resolved_ws
                and record.scope_kind == "workspace"
                and record.scope_ref == resolved_ws
                else 0.0
            )
            composite = similarity + tool_bonus + scope_bonus
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
    # Policy suggestion: compute approval relaxation from template confidence
    # ------------------------------------------------------------------

    def compute_policy_suggestion(
        self,
        template: ContractTemplate,
        *,
        risk_level: str = "high",
    ) -> PolicySuggestion | None:
        """Compute a policy suggestion based on template confidence.

        Returns ``None`` if the template has insufficient history.

        Thresholds:
        - invocation_count >= 5 and success_rate >= 0.95 -> skip_approval_eligible
        - invocation_count >= 5 and success_rate >= 0.80 -> suggest lower risk_level
        - critical risk_level -> never skip approval
        """
        if template.invocation_count < 5:
            return None

        basis = f"{template.invocation_count} invocations, {template.success_rate:.0%} success"

        if template.success_rate >= 0.95:
            return PolicySuggestion(
                template_ref=template.source_contract_ref,
                suggested_risk_level="medium" if risk_level in {"high", "critical"} else None,
                skip_approval_eligible=risk_level != "critical",
                confidence_basis=basis,
                reason=(
                    "High-confidence template eligible for approval skip"
                    if risk_level != "critical"
                    else "High-confidence template but critical risk prevents approval skip"
                ),
            )

        if template.success_rate >= 0.80:
            suggested = None
            if risk_level == "high":
                suggested = "medium"
            elif risk_level == "critical":
                suggested = "high"
            return PolicySuggestion(
                template_ref=template.source_contract_ref,
                suggested_risk_level=suggested,
                skip_approval_eligible=False,
                confidence_basis=basis,
                reason="Moderate-confidence template suggests lower risk level",
            )

        return None

    # ------------------------------------------------------------------
    # Outcome tracking: record success/failure after template use
    # ------------------------------------------------------------------

    def record_template_outcome(
        self,
        *,
        template_ref: str,
        result_class: str,
        task_id: str | None = None,
        step_id: str | None = None,
    ) -> None:
        """Record the outcome of using a template-conditioned contract.

        Called after reconciliation when the step had a ``selected_template_ref``.
        Updates invocation_count, success/failure counts, and success_rate.
        Auto-invalidates when invocation_count >= 5 and success_rate < 0.3.
        """
        record = self._find_template_by_source_contract_ref(template_ref)
        if record is None:
            return

        sa = dict(record.structured_assertion or {})
        invocation_count = int(sa.get("invocation_count", 0)) + 1
        success_count = int(sa.get("success_count", 0))
        failure_count = int(sa.get("failure_count", 0))
        last_failure_at = sa.get("last_failure_at")

        if result_class == "satisfied":
            success_count += 1
        elif result_class in {"violated", "ambiguous", "unauthorized"}:
            failure_count += 1
            last_failure_at = time.time()

        success_rate = success_count / invocation_count if invocation_count > 0 else 0.0

        sa["invocation_count"] = invocation_count
        sa["success_count"] = success_count
        sa["failure_count"] = failure_count
        sa["success_rate"] = success_rate
        sa["last_failure_at"] = last_failure_at

        self.store.update_memory_record(
            record.memory_id,
            structured_assertion=sa,
        )

        self.store.append_event(
            event_type="contract_template.outcome_recorded",
            entity_type="memory_record",
            entity_id=record.memory_id,
            task_id=task_id or "",
            step_id=step_id or "",
            actor="kernel",
            payload={
                "template_ref": template_ref,
                "result_class": result_class,
                "invocation_count": invocation_count,
                "success_rate": success_rate,
            },
        )

        # Auto-invalidate unreliable templates
        if invocation_count >= 5 and success_rate < 0.3:
            self.store.update_memory_record(
                record.memory_id,
                status="invalidated",
                invalidation_reason=(
                    f"low_success_rate:{success_rate:.2f} after {invocation_count} invocations"
                ),
                invalidated_at=time.time(),
            )
            log.info(
                "contract_template.auto_invalidated",
                memory_id=record.memory_id,
                success_rate=success_rate,
                invocation_count=invocation_count,
            )

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
    # Cross-workspace promotion
    # ------------------------------------------------------------------

    def promote_to_global(
        self,
        *,
        fingerprint: str,
        min_workspaces: int = 2,
        min_success_rate: float = 0.8,
    ) -> MemoryRecord | None:
        """Promote a workspace-scoped template to global if it appears
        in multiple workspaces with high success rates.

        Returns the created global ``MemoryRecord`` or ``None``.
        """
        # Check if a global template with this fingerprint already exists
        all_global = self.store.list_memory_records(status="active", scope_kind="global", limit=500)
        for r in all_global:
            if r.memory_kind != "contract_template":
                continue
            sa = dict(r.structured_assertion or {})
            if str(sa.get("fingerprint", "")) == fingerprint:
                return None  # Already promoted

        # Gather all workspace-scoped templates with matching fingerprint
        all_ws = self.store.list_memory_records(status="active", scope_kind="workspace", limit=500)
        matching: list[MemoryRecord] = []
        distinct_workspaces: set[str] = set()
        for r in all_ws:
            if r.memory_kind != "contract_template":
                continue
            sa = dict(r.structured_assertion or {})
            if str(sa.get("fingerprint", "")) != fingerprint:
                continue
            rate = float(sa.get("success_rate", 0.0))
            if rate < min_success_rate:
                return None  # Any workspace below threshold blocks promotion
            matching.append(r)
            distinct_workspaces.add(r.scope_ref or "")

        if len(distinct_workspaces) < min_workspaces:
            return None

        # Aggregate stats across workspace templates
        total_invocations = 0
        total_successes = 0
        total_failures = 0
        evidence_refs: list[str] = []
        # Use first match as representative for template fields
        rep_sa = dict(matching[0].structured_assertion or {})
        for r in matching:
            sa = dict(r.structured_assertion or {})
            total_invocations += int(sa.get("invocation_count", 0))
            total_successes += int(sa.get("success_count", 0))
            total_failures += int(sa.get("failure_count", 0))
            evidence_refs.append(r.memory_id)

        combined_rate = total_successes / total_invocations if total_invocations > 0 else 0.0

        promoted_assertion: dict[str, Any] = {
            **rep_sa,
            "invocation_count": total_invocations,
            "success_count": total_successes,
            "failure_count": total_failures,
            "success_rate": combined_rate,
            "last_used_at": time.time(),
            "promotion_reason": "cross_workspace_convergence",
        }

        memory = self.store.create_memory_record(
            task_id="",
            conversation_id=None,
            category="contract_template",
            claim_text=(
                f"Promoted global template for "
                f"{rep_sa.get('action_class', '')}/{rep_sa.get('tool_name', '')} "
                f"from {len(distinct_workspaces)} workspaces"
            ),
            structured_assertion=promoted_assertion,
            scope_kind="global",
            scope_ref="global",
            promotion_reason="cross_workspace_convergence",
            retention_class="durable_template",
            status="active",
            confidence=min(1.0, combined_rate),
            trust_tier="durable",
            evidence_refs=evidence_refs,
            memory_kind="contract_template",
            validation_basis=f"cross_workspace:{len(distinct_workspaces)}_workspaces",
            last_validated_at=time.time(),
        )

        self.store.append_event(
            event_type="contract_template.promoted_to_global",
            entity_type="memory_record",
            entity_id=memory.memory_id,
            task_id="",
            step_id="",
            actor="kernel",
            payload={
                "fingerprint": fingerprint,
                "source_workspace_count": len(distinct_workspaces),
                "source_memory_ids": evidence_refs,
                "combined_success_rate": combined_rate,
            },
        )

        log.info(
            "contract_template.promoted_to_global",
            memory_id=memory.memory_id,
            fingerprint=fingerprint,
            workspace_count=len(distinct_workspaces),
        )
        return memory

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _active_templates(self, workspace_root: str = "") -> list[MemoryRecord]:
        """Return active ``contract_template`` memory records visible to the given workspace.

        Returns workspace-scoped templates for the given workspace plus all global templates.
        When ``workspace_root`` is empty, returns only global templates (backward-compatible).
        """
        resolved_ws = _resolve_workspace(workspace_root)
        results: list[MemoryRecord] = []
        seen_ids: set[str] = set()

        # Workspace-scoped templates
        if resolved_ws:
            ws_records = self.store.list_memory_records(
                status="active", scope_kind="workspace", scope_ref=resolved_ws, limit=500
            )
            for r in ws_records:
                if r.memory_kind == "contract_template" and r.memory_id not in seen_ids:
                    results.append(r)
                    seen_ids.add(r.memory_id)

        # Global templates
        global_records = self.store.list_memory_records(
            status="active", scope_kind="global", limit=500
        )
        for r in global_records:
            if r.memory_kind == "contract_template" and r.memory_id not in seen_ids:
                results.append(r)
                seen_ids.add(r.memory_id)

        # Backward compat: when no workspace, also fetch records with empty scope
        if not resolved_ws:
            all_active = self.store.list_memory_records(status="active", limit=500)
            for r in all_active:
                if r.memory_kind == "contract_template" and r.memory_id not in seen_ids:
                    results.append(r)
                    seen_ids.add(r.memory_id)

        return results

    def _find_template_by_fingerprint(
        self, fingerprint: str, *, workspace_root: str = ""
    ) -> MemoryRecord | None:
        for record in self._active_templates(workspace_root=workspace_root):
            sa = dict(record.structured_assertion or {})
            if str(sa.get("fingerprint", "")) == fingerprint:
                # When workspace-scoped, only match within same scope
                resolved_ws = _resolve_workspace(workspace_root)
                if resolved_ws:
                    if record.scope_kind == "workspace" and record.scope_ref == resolved_ws:
                        return record
                    # Skip workspace-scoped records from other workspaces
                    if record.scope_kind == "workspace" and record.scope_ref != resolved_ws:
                        continue
                    # Global record is acceptable as fallback
                    return record
                return record
        return None

    def _find_template_by_source_contract_ref(self, ref: str) -> MemoryRecord | None:
        for record in self._active_templates():
            sa = dict(record.structured_assertion or {})
            if str(sa.get("source_contract_ref", "")) == ref:
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
            invocation_count=int(sa.get("invocation_count", 0)),
            success_count=int(sa.get("success_count", 0)),
            failure_count=int(sa.get("failure_count", 0)),
            success_rate=float(sa.get("success_rate", 0.0)),
            last_failure_at=sa.get("last_failure_at"),
            last_used_at=float(sa.get("last_used_at", 0.0)),
            resource_scope_pattern=list(sa.get("resource_scope_pattern", [])),
            constraint_defaults=dict(sa.get("constraint_defaults", {})),
            evidence_requirements=list(sa.get("evidence_requirements", [])),
            workspace_ref=record.scope_ref or "",
            scope_kind=record.scope_kind or "global",
        )
