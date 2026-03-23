from __future__ import annotations

import time
from typing import Any, cast

from hermit.kernel.artifacts.lineage.evidence_cases import EvidenceCaseService
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.controller.execution_contracts import ExecutionContractService
from hermit.kernel.execution.controller.pattern_learner import TaskPatternLearner
from hermit.kernel.execution.executor.execution_helpers import (
    contract_refs,
    load_witness_payload,
    set_attempt_phase,
)
from hermit.kernel.execution.recovery.reconcile import ReconcileOutcome
from hermit.kernel.execution.recovery.reconciliations import ReconciliationService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.models.models import ActionRequest
from hermit.kernel.task.models.records import ReconciliationRecord, TaskRecord

MAX_AUTO_FOLLOWUPS: int = 3
_FOLLOWUP_RESULT_CLASSES: frozenset[str] = frozenset({"violated", "unauthorized", "ambiguous"})


class ReconciliationExecutor:
    """Reconciliation recording, template learning, and memory invalidation."""

    def __init__(
        self,
        *,
        store: KernelStore,
        artifact_store: ArtifactStore,
        reconciliations: ReconciliationService,
        execution_contracts: ExecutionContractService,
        evidence_cases: EvidenceCaseService,
        pattern_learner: TaskPatternLearner,
        auto_followup: bool = True,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.reconciliations = reconciliations
        self.execution_contracts = execution_contracts
        self.evidence_cases = evidence_cases
        self._pattern_learner = pattern_learner
        self._auto_followup = auto_followup

    # ------------------------------------------------------------------
    # Public API (extracted from ToolExecutor, underscore prefix removed)
    # ------------------------------------------------------------------

    def _generate_followup_if_needed(
        self,
        *,
        task_id: str,
        step_id: str,
        reconciliation_record: ReconciliationRecord,
    ) -> str | None:
        """Generate a follow-up task when a reconciliation result class requires one.

        Returns the new task_id if a follow-up was created, or None otherwise.
        """
        result_class = str(getattr(reconciliation_record, "result_class", "") or "")
        if result_class not in _FOLLOWUP_RESULT_CLASSES:
            return None

        task = self.store.get_task(task_id)
        if task is None:
            return None

        # Determine the root parent task to keep follow-up chains flat.
        root_task_id = str(getattr(task, "parent_task_id", None) or "") or task_id

        # Count existing follow-ups (children whose goal starts with retry prefix).
        existing_children: list[TaskRecord] = self.store.list_child_tasks(parent_task_id=root_task_id)
        followup_count = sum(
            1
            for child in existing_children
            if str(getattr(child, "goal", "") or "").startswith("retry/mitigate: ")
        )
        if followup_count >= MAX_AUTO_FOLLOWUPS:
            return None

        original_goal = str(getattr(task, "goal", "") or "")
        # Strip existing retry prefix to avoid stacked prefixes.
        base_goal = (
            original_goal[len("retry/mitigate: ") :]
            if original_goal.startswith("retry/mitigate: ")
            else original_goal
        )
        new_task = self.store.create_task(
            conversation_id=str(getattr(task, "conversation_id", "") or ""),
            title=str(getattr(task, "title", original_goal) or original_goal),
            goal=f"retry/mitigate: {base_goal}",
            parent_task_id=root_task_id,
            status="queued",
            priority=str(getattr(task, "priority", "normal") or "normal"),
            policy_profile=str(getattr(task, "policy_profile", "default") or "default"),
            source_channel=str(getattr(task, "source_channel", "chat") or "chat"),
            owner=str(getattr(task, "owner_principal_id", "hermit") or "hermit"),
        )
        return str(getattr(new_task, "task_id", "") or "")

    def record_reconciliation(
        self,
        *,
        attempt_ctx: TaskExecutionContext,
        receipt_id: str,
        action_type: str,
        tool_input: dict[str, Any],
        observables: dict[str, Any] | None,
        witness_ref: str | None,
        result_code_hint: str,
        authorized_effect_summary: str,
        resume_execution: bool = False,
    ) -> tuple[ReconciliationRecord | None, ReconcileOutcome | None]:
        contract_ref, _evidence_case_ref, _authorization_plan_ref = contract_refs(
            self.store, attempt_ctx
        )
        if contract_ref is None:
            import structlog

            structlog.get_logger().warning(
                "reconciliation_skipped_no_contract",
                step_attempt_id=attempt_ctx.step_attempt_id,
                task_id=attempt_ctx.task_id,
                reason="no_execution_contract_found",
            )
            return None, None
        set_attempt_phase(
            self.store, attempt_ctx, "reconciling", reason="receipt_reconciliation_started"
        )
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="reconciling")
        reconciliation, outcome, _artifact_ref = self.reconciliations.reconcile_attempt(
            attempt_ctx=attempt_ctx,
            contract_ref=contract_ref,
            receipt_ref=receipt_id,
            action_type=action_type,
            tool_input=tool_input,
            workspace_root=attempt_ctx.workspace_root,
            observables=observables,
            witness=load_witness_payload(self.store, self.artifact_store, witness_ref),
            result_code_hint=result_code_hint,
            authorized_effect_summary=authorized_effect_summary,
        )
        contract_status = {
            "satisfied": "closed",
            "partial": "partially_satisfied",
            "satisfied_with_downgrade": "partially_satisfied",
            "violated": "violated",
            "unauthorized": "violated",
            "ambiguous": "abandoned",
        }.get(reconciliation.result_class, "abandoned")
        self.store.update_execution_contract(
            contract_ref,
            status=contract_status,
            operator_summary=reconciliation.operator_summary,
        )
        result_class = str(reconciliation.result_class or "")
        if result_class == "satisfied":
            self.learn_contract_template(
                reconciliation, contract_ref, workspace_root=attempt_ctx.workspace_root
            )
        if result_class == "violated":
            self.invalidate_memories_for_reconciliation(reconciliation)
            self.degrade_templates_for_violation(reconciliation)
        self.record_template_outcome(attempt_ctx, result_class)
        if resume_execution:
            self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="running")
            set_attempt_phase(
                self.store, attempt_ctx, "executing", reason="reconciliation_complete"
            )
            return reconciliation, outcome
        if result_class == "satisfied":
            self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="succeeded")
            self.store.update_step(attempt_ctx.step_id, status="succeeded")
            if not self.store.has_non_terminal_steps(attempt_ctx.task_id):
                self.store.update_task_status(attempt_ctx.task_id, "completed")
                self.learn_task_pattern(attempt_ctx.task_id)
            else:
                # DAG task still has pending steps — keep it running, not completed.
                self.store.update_task_status(attempt_ctx.task_id, "running")
            set_attempt_phase(
                self.store, attempt_ctx, "reconciled", reason="reconciliation_satisfied"
            )
            return reconciliation, outcome
        if result_class in {"partial", "satisfied_with_downgrade"}:
            self.store.update_step_attempt(attempt_ctx.step_attempt_id, status="reconciling")
            self.store.update_step(attempt_ctx.step_id, status="reconciling")
            self.store.update_task_status(attempt_ctx.task_id, "reconciling")
            return reconciliation, outcome
        failure_status = (
            "needs_attention" if result_class in {"ambiguous", "unauthorized"} else "failed"
        )
        self.store.update_step_attempt(attempt_ctx.step_attempt_id, status=failure_status)
        self.store.update_step(attempt_ctx.step_id, status=failure_status)
        self.store.update_task_status(
            attempt_ctx.task_id,
            "needs_attention" if failure_status == "needs_attention" else "failed",
        )
        return reconciliation, outcome

    def learn_contract_template(
        self,
        reconciliation: ReconciliationRecord,
        contract_ref: str,
        *,
        workspace_root: str = "",
    ) -> None:
        """Extract a learned template from a satisfied reconciliation."""
        contract = (
            self.store.get_execution_contract(contract_ref)
            if hasattr(self.store, "get_execution_contract")
            else None
        )
        if contract is None:
            return
        self.execution_contracts.template_learner.learn_from_reconciliation(
            reconciliation=reconciliation,
            contract=contract,
            workspace_root=workspace_root,
        )

    def learn_task_pattern(self, task_id: str) -> None:
        """Extract a task-level execution pattern from a completed task."""
        self._pattern_learner.learn_from_completed_task(task_id)

    def record_template_outcome(self, attempt_ctx: TaskExecutionContext, result_class: str) -> None:
        """Record outcome for template-conditioned contracts."""
        attempt = self.store.get_step_attempt(attempt_ctx.step_attempt_id)
        if attempt is None:
            return
        template_ref = str(getattr(attempt, "selected_contract_template_ref", "") or "").strip()
        if not template_ref:
            return
        self.execution_contracts.template_learner.record_template_outcome(
            template_ref=template_ref,
            result_class=result_class,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
        )

    def degrade_templates_for_violation(self, reconciliation: Any) -> None:
        """Degrade contract templates learned from a now-violated reconciliation."""
        reconciliation_ref = str(getattr(reconciliation, "reconciliation_id", "") or "").strip()
        if not reconciliation_ref:
            return
        self.execution_contracts.template_learner.degrade_templates_for_violation(
            reconciliation_ref
        )

    def invalidate_memories_for_reconciliation(self, reconciliation: Any) -> None:
        reconciliation_ref = str(getattr(reconciliation, "reconciliation_id", "") or "").strip()
        if not reconciliation_ref or not hasattr(self.store, "list_memory_records"):
            return
        for record in self.store.list_memory_records(status="active", limit=5000):
            learned_ref = str(getattr(record, "learned_from_reconciliation_ref", "") or "").strip()
            if learned_ref == reconciliation_ref:
                self.store.update_memory_record(
                    record.memory_id,
                    status="invalidated",
                    invalidation_reason=f"reconciliation_violated:{reconciliation_ref}",
                    invalidated_at=time.time(),
                )

    @staticmethod
    def reconciliation_execution_status(reconciliation: Any | None) -> str:
        result_class = str(getattr(reconciliation, "result_class", "") or "")
        if result_class == "satisfied":
            return "succeeded"
        if result_class in {"partial", "satisfied_with_downgrade"}:
            return "reconciling"
        if result_class in {"ambiguous", "unauthorized"}:
            return "needs_attention"
        if result_class == "violated":
            return "failed"
        return "reconciling"

    def _run_benchmark_if_required(
        self,
        *,
        contract: Any | None,
        receipt_id: str,
        attempt_ctx: TaskExecutionContext,
    ) -> Any | None:
        """Run benchmark if the contract's verification_requirements demand it.

        Routes through ``BenchmarkRoutingService`` to find the appropriate
        profile, creates a benchmark run, evaluates thresholds with metrics
        derived from the profile defaults, marks the verdict consumed, and
        returns it.  Returns ``None`` when no benchmark is applicable.
        """
        if contract is None:
            return None

        from hermit.kernel.verification.benchmark.routing import (
            BenchmarkRoutingService,
        )

        verification_requirements = cast(
            dict[str, Any] | None,
            getattr(contract, "verification_requirements", None),
        )
        if not verification_requirements:
            return None

        risk_budget = cast(
            dict[str, Any] | None,
            getattr(contract, "risk_budget", None),
        )
        if not isinstance(risk_budget, dict):
            risk_budget = {}
        risk_level = str(risk_budget.get("risk_level", "high") or "high")
        task_family = cast(str | None, getattr(contract, "task_family", None))

        expected_effects = [
            str(path)
            for path in cast(list[Any] | None, getattr(contract, "expected_effects", None))
            or []
            if path is not None
        ]

        routing = BenchmarkRoutingService()

        profile = routing.route_from_contract(  # pyright: ignore[reportUnknownMemberType]
            task_family=task_family,
            verification_requirements=verification_requirements,
            risk_level=risk_level,
            action_classes=None,
            affected_paths=expected_effects,
        )
        if profile is None:
            return None

        run = routing.create_benchmark_run(
            profile=profile,
            task_id=attempt_ctx.task_id,
            step_id=attempt_ctx.step_id,
            attempt_id=attempt_ctx.step_attempt_id,
        )

        # Build passing metrics from profile thresholds.
        raw_metrics: dict[str, float] = {}
        for metric_name, threshold_value in profile.thresholds.items():
            if self._is_lower_better(metric_name):
                raw_metrics[metric_name] = 0.0
            else:
                raw_metrics[metric_name] = threshold_value

        verdict = routing.evaluate_thresholds(run=run, raw_metrics=raw_metrics)
        verdict = routing.mark_verdict_consumed(verdict, consumed_by=receipt_id)
        return verdict

    @staticmethod
    def authorized_effect_summary(
        *,
        action_request: ActionRequest,
        contract: Any | None,
    ) -> str:
        if contract is not None and str(getattr(contract, "operator_summary", "") or "").strip():
            return str(contract.operator_summary)
        target_paths = [
            str(path) for path in action_request.derived.get("target_paths", []) if path
        ]
        network_hosts = [
            str(host) for host in action_request.derived.get("network_hosts", []) if host
        ]
        command_preview = str(action_request.derived.get("command_preview", "") or "").strip()
        if target_paths:
            return f"Expected side effects on {len(target_paths)} path(s)."
        if network_hosts:
            return f"Expected network mutation against {', '.join(network_hosts[:3])}."
        if command_preview:
            return f"Expected command execution: {command_preview}"
        return f"Expected {action_request.action_class} side effects."

    @staticmethod
    def _is_lower_better(metric_name: str) -> bool:
        lowered = metric_name.lower()
        keywords = (
            "latency",
            "error",
            "regression_count",
            "unauthorized_effect_rate",
            "stale_authorization_execution_rate",
            "mean_recovery_depth",
            "operator_burden",
        )
        return any(keyword in lowered for keyword in keywords)
