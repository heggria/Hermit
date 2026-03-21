from __future__ import annotations

import time
from typing import Any

from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.controller.contracts import ActionContract, contract_for
from hermit.kernel.execution.controller.template_learner import (
    ContractTemplateLearner,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy import ActionRequest, PolicyDecision
from hermit.runtime.capability.registry.tools import ToolSpec


class ExecutionContractService:
    def __init__(self, store: KernelStore, artifact_store: ArtifactStore) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.template_learner = ContractTemplateLearner(store)

    def synthesize_default(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        tool: ToolSpec,
        action_request: ActionRequest,
        policy: PolicyDecision,
        action_request_ref: str | None,
        witness_ref: str | None,
    ):
        action_contract = contract_for(action_request.action_class)
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        expected_effects = self._expected_effects(action_request)
        required_receipts = (
            [action_request.action_class]
            if policy.requires_receipt or action_contract.receipt_required
            else []
        )

        # -- Template-conditioned contract selection (Criterion #8) --------
        template = self.template_learner.find_matching_template(
            action_class=action_request.action_class,
            tool_name=action_request.tool_name,
            expected_effects=expected_effects,
            workspace_root=attempt_ctx.workspace_root,
        )
        selected_template_ref: str | None = None
        if template is not None:
            selected_template_ref = template.source_contract_ref
        # ------------------------------------------------------------------

        # -- Compute drift_budget, tightened by template if available -------
        request_scopes = list(action_request.resource_scopes)
        request_outside_workspace = bool(action_request.derived.get("outside_workspace"))
        request_requires_witness = bool(witness_ref or action_contract.witness_required)

        if template is not None and template.drift_budget:
            tmpl_budget = template.drift_budget
            # Intersect resource_scopes (tighter)
            tmpl_scopes = list(tmpl_budget.get("resource_scopes", []))
            if tmpl_scopes and request_scopes:
                request_scopes = [s for s in request_scopes if s in tmpl_scopes]
            elif tmpl_scopes:
                request_scopes = tmpl_scopes
            # Template outside_workspace=False overrides request True (stricter)
            if not tmpl_budget.get("outside_workspace", True):
                request_outside_workspace = False
            # Template requires_witness=True overrides request False (stricter)
            if tmpl_budget.get("requires_witness", False):
                request_requires_witness = True

        drift_budget = {
            "resource_scopes": request_scopes,
            "outside_workspace": request_outside_workspace,
            "requires_witness": request_requires_witness,
        }
        # ------------------------------------------------------------------

        # -- Policy suggestion (injected by PolicyEvidenceEnricher) ----------
        policy_suggestion_ctx: dict[str, Any] | None = (
            action_request.context.get("policy_suggestion")
            if isinstance(action_request.context.get("policy_suggestion"), dict)
            else None
        )
        # ------------------------------------------------------------------

        # -- Derive task_family from action_class --------------------------
        task_family = self._infer_task_family(action_request.action_class)
        verification_requirements = self.enrich_verification_requirements(
            task_family=task_family,
            risk_level=policy.risk_level,
        )
        # Freeze after admission: never weaken previously-admitted requirements
        previous_contracts = self.store.list_execution_contracts(
            step_attempt_id=attempt_ctx.step_attempt_id,
        )
        for prev in previous_contracts:
            if prev.verification_requirements:
                verification_requirements = self._merge_strictest(
                    prev.verification_requirements,
                    verification_requirements,
                )
                break  # only the most recent previous contract matters
        # ------------------------------------------------------------------

        contract = self.store.create_execution_contract(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            step_attempt_id=attempt_ctx.step_attempt_id,
            objective=self._objective(action_request, tool=tool),
            proposed_action_refs=[ref for ref in [action_request_ref] if ref],
            expected_effects=expected_effects,
            success_criteria={
                "tool_name": action_request.tool_name,
                "action_class": action_request.action_class,
                "requires_receipt": bool(required_receipts),
            },
            reversibility_class=self._reversibility_class(action_contract),
            required_receipt_classes=required_receipts,
            drift_budget=drift_budget,
            expiry_at=self._expiry_at(policy=policy, witness_ref=witness_ref),
            status="admissibility_pending",
            operator_summary=self._operator_summary(
                action_request=action_request,
                policy=policy,
                expected_effects=expected_effects,
            ),
            risk_budget={
                "risk_level": policy.risk_level,
                "approval_required": bool(policy.obligations.require_approval),
            },
            expected_artifact_shape={"expected_effects": expected_effects},
            contract_version=max(1, int(getattr(attempt, "contract_version", 0) or 0)),
            action_contract_refs=[action_contract.action_class],
            state_witness_ref=witness_ref,
            rollback_expectation=action_contract.rollback_strategy,
            selected_template_ref=selected_template_ref,
            task_family=task_family,
            verification_requirements=verification_requirements,
        )
        if selected_template_ref:
            self.store.update_step_attempt(
                attempt_ctx.step_attempt_id,
                selected_contract_template_ref=selected_template_ref,
            )
            if template is not None and template.drift_budget:
                self.store.append_event(
                    event_type="contract_template.applied",
                    entity_type="step_attempt",
                    entity_id=attempt_ctx.step_attempt_id,
                    task_id=attempt_ctx.task_id,
                    step_id=attempt_ctx.step_id,
                    actor="kernel",
                    payload={
                        "template_ref": selected_template_ref,
                        "contract_ref": contract.contract_id,
                        "drift_budget_applied": drift_budget,
                    },
                )
            if policy_suggestion_ctx is not None:
                self.store.append_event(
                    event_type="policy.template_suggestion_applied",
                    entity_type="step_attempt",
                    entity_id=attempt_ctx.step_attempt_id,
                    task_id=attempt_ctx.task_id,
                    step_id=attempt_ctx.step_id,
                    actor="kernel",
                    payload={
                        "template_ref": selected_template_ref,
                        "skip_approval_eligible": policy_suggestion_ctx.get(
                            "skip_approval_eligible", False
                        ),
                        "suggested_risk_level": policy_suggestion_ctx.get("suggested_risk_level"),
                        "confidence_basis": policy_suggestion_ctx.get("confidence_basis", ""),
                    },
                )
        artifact_ref = self._store_artifact(
            contract.contract_id,
            kind="execution.contract",
            payload={
                "contract_id": contract.contract_id,
                "objective": contract.objective,
                "scope": {
                    "resource_scopes": list(drift_budget.get("resource_scopes", [])),
                    "outside_workspace": drift_budget.get("outside_workspace", False),
                },
                "constraints": {
                    "risk_level": contract.risk_budget.get("risk_level", "low"),
                    "approval_required": contract.risk_budget.get("approval_required", False),
                    "requires_witness": drift_budget.get("requires_witness", False),
                },
                "acceptance": contract.success_criteria,
                "verification_requirements": contract.verification_requirements,
                "rollback_hint": contract.rollback_expectation,
                "expected_effects": contract.expected_effects,
                "required_receipt_classes": contract.required_receipt_classes,
                "risk_budget": contract.risk_budget,
                "drift_budget": contract.drift_budget,
                "reversibility_class": contract.reversibility_class,
                "operator_summary": contract.operator_summary,
                "task_family": contract.task_family,
            },
            attempt_ctx=attempt_ctx,
        )
        self.store.update_step(
            attempt_ctx.step_id,
            contract_ref=contract.contract_id,
        )
        self.store.update_step_attempt(
            attempt_ctx.step_attempt_id,
            execution_contract_ref=contract.contract_id,
            contract_version=contract.contract_version,
            context={
                **(dict(attempt.context or {}) if attempt is not None else {}),
                "execution_contract_artifact_ref": artifact_ref,
            },
        )
        self.store.append_event(
            event_type="execution_contract.selected",
            entity_type="step_attempt",
            entity_id=attempt_ctx.step_attempt_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "contract_ref": contract.contract_id,
                "artifact_ref": artifact_ref,
                "objective": contract.objective,
                "status": contract.status,
                "selected_template_ref": selected_template_ref,
            },
        )
        return contract, artifact_ref

    def supersede(
        self,
        contract_id: str,
        *,
        superseded_by_contract_id: str,
        attempt_ctx: TaskExecutionContext,
        reason: str,
    ) -> None:
        self.store.update_execution_contract(
            contract_id,
            status="superseded",
            superseded_by_contract_id=superseded_by_contract_id,
        )
        self.store.append_event(
            event_type="execution_contract.superseded",
            entity_type="execution_contract",
            entity_id=contract_id,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            actor="kernel",
            payload={
                "superseded_by_contract_id": superseded_by_contract_id,
                "reason": reason,
            },
        )

    def _store_artifact(
        self,
        contract_id: str,
        *,
        kind: str,
        payload: dict[str, Any],
        attempt_ctx: TaskExecutionContext,
    ) -> str:
        uri, content_hash = self.artifact_store.store_json(payload)
        artifact = self.store.create_artifact(
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            kind=kind,
            uri=uri,
            content_hash=content_hash,
            producer="execution_contract_service",
            retention_class="audit",
            trust_tier="derived",
            metadata={"contract_id": contract_id},
        )
        return artifact.artifact_id

    @staticmethod
    def _objective(action_request: ActionRequest, *, tool: ToolSpec) -> str:
        return f"{tool.name}: {action_request.action_class}"

    @staticmethod
    def _expected_effects(action_request: ActionRequest) -> list[str]:
        effects: list[str] = []
        for path in action_request.derived.get("target_paths", []):
            effects.append(f"path:{path}")
        for host in action_request.derived.get("network_hosts", []):
            effects.append(f"host:{host}")
        preview = str(action_request.derived.get("command_preview", "") or "").strip()
        if preview:
            effects.append(f"command:{preview}")
        if not effects:
            effects.append(f"action:{action_request.action_class}")
        return effects

    @staticmethod
    def _reversibility_class(action_contract: ActionContract) -> str:
        if action_contract.rollback_strategy in {"file_restore", "supersede_or_invalidate"}:
            return "reversible"
        if action_contract.rollback_strategy in {"compensating_action", "manual_or_followup"}:
            return "compensatable"
        return "limited"

    @staticmethod
    def _expiry_at(*, policy: PolicyDecision, witness_ref: str | None) -> float:
        ttl_seconds = 15 * 60
        if policy.obligations.require_approval or witness_ref:
            ttl_seconds = 5 * 60
        return time.time() + ttl_seconds

    @staticmethod
    def _operator_summary(
        *,
        action_request: ActionRequest,
        policy: PolicyDecision,
        expected_effects: list[str],
    ) -> str:
        return (
            f"{action_request.tool_name} intends {', '.join(expected_effects)}; "
            f"risk={policy.risk_level}; approval_required={policy.obligations.require_approval}"
        )

    # -- Verification requirements enrichment (v0.3 spec) ----------------

    _GOVERNANCE_MUTATION_CLASSES = frozenset(
        {
            "write_local",
            "write_remote",
            "execute_local",
            "execute_remote",
            "delete_local",
            "delete_remote",
        }
    )

    _SURFACE_INTEGRATION_CLASSES = frozenset(
        {
            "network_request",
            "read_remote",
        }
    )

    @staticmethod
    def _infer_task_family(action_class: str) -> str | None:
        """Infer the task family from the action class.

        Returns a TaskFamily value string or None when no mapping applies.
        """
        if action_class in ExecutionContractService._GOVERNANCE_MUTATION_CLASSES:
            return "governance_mutation"
        if action_class in ExecutionContractService._SURFACE_INTEGRATION_CLASSES:
            return "surface_integration"
        return None

    @staticmethod
    def enrich_verification_requirements(
        *,
        task_family: str | None = None,
        risk_level: str = "low",
    ) -> dict[str, Any]:
        """Generate verification_requirements dict based on task_family and risk.

        High/critical risk -> governance_bench required, performance_bench required
        Medium risk -> governance_bench optional, performance_bench optional
        Low risk -> all forbidden (minimal verification overhead)
        """
        high_risk = risk_level in {"high", "critical"}
        medium_risk = risk_level == "medium"

        if high_risk:
            governance_bench = "required"
            performance_bench = "required"
            rollback_check = "required"
            reconciliation_mode = "strict"
        elif medium_risk:
            governance_bench = "optional"
            performance_bench = "optional"
            rollback_check = "optional"
            reconciliation_mode = "standard"
        else:
            governance_bench = "forbidden"
            performance_bench = "forbidden"
            rollback_check = "forbidden"
            reconciliation_mode = "light"

        # Determine benchmark_profile from task_family
        benchmark_profile: str = "none"
        if task_family == "governance_mutation":
            benchmark_profile = "trustloop_governance"
        elif task_family == "runtime_perf":
            benchmark_profile = "runtime_perf"
        elif task_family == "surface_integration":
            benchmark_profile = "integration_regression"
        elif task_family == "learning_template":
            benchmark_profile = "template_quality"

        return {
            "functional": "required",
            "governance_bench": governance_bench,
            "performance_bench": performance_bench,
            "rollback_check": rollback_check,
            "reconciliation_mode": reconciliation_mode,
            "benchmark_profile": benchmark_profile,
            "thresholds_ref": None,
        }

    # -- Strictness merging for verification_requirements freeze ----------

    _LANE_STRICTNESS_ORDER = ("forbidden", "optional", "required")
    _RECONCILIATION_STRICTNESS_ORDER = ("light", "standard", "strict")

    @staticmethod
    def _merge_strictest(
        previous: dict[str, Any],
        current: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge two verification_requirements dicts, keeping the stricter value per field.

        For lane fields (functional, governance_bench, performance_bench, rollback_check):
            "required" > "optional" > "forbidden"
        For reconciliation_mode:
            "strict" > "standard" > "light"
        Other fields are taken from *current* (no ordering defined).
        """
        lane_order = ExecutionContractService._LANE_STRICTNESS_ORDER
        recon_order = ExecutionContractService._RECONCILIATION_STRICTNESS_ORDER
        merged = dict(current)

        for lane in ("functional", "governance_bench", "performance_bench", "rollback_check"):
            prev_val = previous.get(lane, "forbidden")
            cur_val = current.get(lane, "forbidden")
            prev_idx = lane_order.index(prev_val) if prev_val in lane_order else 0
            cur_idx = lane_order.index(cur_val) if cur_val in lane_order else 0
            merged[lane] = lane_order[max(prev_idx, cur_idx)]

        prev_recon = previous.get("reconciliation_mode", "light")
        cur_recon = current.get("reconciliation_mode", "light")
        prev_r_idx = recon_order.index(prev_recon) if prev_recon in recon_order else 0
        cur_r_idx = recon_order.index(cur_recon) if cur_recon in recon_order else 0
        merged["reconciliation_mode"] = recon_order[max(prev_r_idx, cur_r_idx)]

        return merged
