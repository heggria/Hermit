from __future__ import annotations

from hermit.kernel.ledger.journal.store import KernelStore


class DecisionService:
    def __init__(self, store: KernelStore) -> None:
        self.store = store

    def record(
        self,
        *,
        task_id: str,
        step_id: str,
        step_attempt_id: str,
        decision_type: str,
        verdict: str,
        reason: str,
        evidence_refs: list[str] | None = None,
        policy_ref: str | None = None,
        approval_ref: str | None = None,
        contract_ref: str | None = None,
        authorization_plan_ref: str | None = None,
        evidence_case_ref: str | None = None,
        reconciliation_ref: str | None = None,
        action_type: str | None = None,
        decided_by: str = "kernel",
    ) -> str:
        """Persist a governance decision and return its ``decision_id``.

        All required string arguments must be non-empty so that the resulting
        decision record is meaningful in an audit trail.  Passing a blank value
        for any of them raises ``ValueError`` immediately rather than storing a
        silent no-op entry in the ledger.

        Args:
            task_id: Identifier of the task this decision belongs to.
            step_id: Identifier of the step within the task.
            step_attempt_id: Identifier of the specific attempt being decided.
            decision_type: Semantic category of the decision (e.g.
                ``"approval_resolution"``).
            verdict: Outcome of the decision (e.g. ``"approved"`` or
                ``"denied"``).  Must not be blank.
            reason: Human-readable justification for the verdict.  Must not be
                blank.
            evidence_refs: Optional list of artifact references that support
                this decision.
            policy_ref: Optional reference to the policy that governed this
                decision.
            approval_ref: Optional reference to the related approval record.
            contract_ref: Optional reference to the authorizing contract.
            authorization_plan_ref: Optional reference to the authorization
                plan.
            evidence_case_ref: Optional reference to an evidence-case artifact.
            reconciliation_ref: Optional reference to a reconciliation record.
            action_type: Optional label describing the action class being
                decided upon.
            decided_by: Principal that issued this decision.  Defaults to
                ``"kernel"``.

        Returns:
            The newly created ``decision_id`` string.

        Raises:
            ValueError: If any required string argument is blank.
        """
        _required = {
            "task_id": task_id,
            "step_id": step_id,
            "step_attempt_id": step_attempt_id,
            "decision_type": decision_type,
            "verdict": verdict,
            "reason": reason,
        }
        blank = [name for name, val in _required.items() if not val or not val.strip()]
        if blank:
            raise ValueError(
                f"DecisionService.record() requires non-blank values for: {', '.join(blank)}"
            )

        decision = self.store.create_decision(
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=step_attempt_id,
            decision_type=decision_type,
            verdict=verdict,
            reason=reason,
            evidence_refs=evidence_refs,
            policy_ref=policy_ref,
            approval_ref=approval_ref,
            contract_ref=contract_ref,
            authorization_plan_ref=authorization_plan_ref,
            evidence_case_ref=evidence_case_ref,
            reconciliation_ref=reconciliation_ref,
            action_type=action_type,
            decided_by=decided_by,
        )
        return decision.decision_id
