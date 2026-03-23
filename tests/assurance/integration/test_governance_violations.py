"""Integration tests: governance violation detection.

Validates that the assurance system correctly identifies, reports, and
attributes various governance violations across the full pipeline.

All tests use pure in-memory TraceRecorder and AssuranceLab -- no KernelStore
required. Traces are built with make_envelope() and validated via
lab.run_with_trace().
"""

from __future__ import annotations

import uuid

from hermit.kernel.verification.assurance.lab import AssuranceLab
from hermit.kernel.verification.assurance.models import (
    ContractViolation,
    InvariantViolation,
    OracleSpec,
    ScenarioMetadata,
    ScenarioSpec,
)
from tests.assurance.conftest import make_envelope, make_governed_trace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _scenario(
    scenario_id: str = "gov-violation-test",
    attribution_mode: str = "post_run",
    **kwargs: object,
) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id=scenario_id,
        metadata=ScenarioMetadata(name=scenario_id),
        attribution_mode=attribution_mode,
        **kwargs,
    )


def _violation_contract_ids(
    violations: list[ContractViolation | InvariantViolation],
) -> list[str]:
    """Extract contract_id or invariant_id from each violation, preserving order."""
    ids: list[str] = []
    for v in violations:
        if isinstance(v, ContractViolation):
            ids.append(v.contract_id)
        else:
            ids.append(v.invariant_id)
    return ids


# ---------------------------------------------------------------------------
# TestMissingApproval
# ---------------------------------------------------------------------------


class TestMissingApproval:
    """tool_call.start without prior approval.granted."""

    def test_detects_missing_approval(self) -> None:
        """Trace: task.created -> tool_call.start (no approval) -> task.completed.

        The approval.gating contract (runtime, blocker) requires
        approval.granted to appear before tool_call.start.
        """
        run_id = _uid("run")
        task_id = _uid("task")
        grant_id = _uid("grant")
        lease_id = _uid("lease")
        decision_id = _uid("decision")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=grant_id,
                lease_ref=lease_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=2,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        contract_ids = _violation_contract_ids(report.violations)
        assert "approval.gating" in contract_ids

    def test_report_identifies_first_violation(self) -> None:
        """Verify report.first_violation points to the tool_call.start event."""
        run_id = _uid("run")
        task_id = _uid("task")
        grant_id = _uid("grant")
        lease_id = _uid("lease")
        decision_id = _uid("decision")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=grant_id,
                lease_ref=lease_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=2,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.first_violation is not None
        # The first violation should be the approval.gating contract violation
        # triggered by the tool_call.start event (runtime check fires per-envelope)
        assert isinstance(report.first_violation, ContractViolation)
        assert report.first_violation.contract_id == "approval.gating"

    def test_attribution_blames_missing_approval(self) -> None:
        """Verify attribution identifies the missing approval as root cause."""
        run_id = _uid("run")
        task_id = _uid("task")
        grant_id = _uid("grant")
        lease_id = _uid("lease")
        decision_id = _uid("decision")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=grant_id,
                lease_ref=lease_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=2,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(attribution_mode="post_run"), envelopes)

        assert report.attribution is not None
        # The attribution should have a selected root cause
        assert report.attribution.selected_root_cause != ""
        # The root cause should be one of the violation nodes
        assert len(report.attribution.root_cause_candidates) >= 1
        # Fix hints should reference approval
        assert len(report.attribution.fix_hints) >= 1


# ---------------------------------------------------------------------------
# TestMissingGrant
# ---------------------------------------------------------------------------


class TestMissingGrant:
    """tool_call.start without grant_ref."""

    def test_detects_missing_grant(self) -> None:
        """Trace with approval but tool_call.start lacking grant_ref.

        Expected: side_effect.authorization contract violation (runtime, blocker).
        """
        run_id = _uid("run")
        task_id = _uid("task")
        approval_id = _uid("approval")
        lease_id = _uid("lease")
        decision_id = _uid("decision")
        receipt_id = _uid("receipt")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="approval.granted",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                approval_ref=approval_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=2,
                step_id="step-0",
                step_attempt_id="attempt-0",
                # No grant_ref
                lease_ref=lease_id,
                decision_ref=decision_id,
                approval_ref=approval_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="receipt.issued",
                event_seq=3,
                step_id="step-0",
                step_attempt_id="attempt-0",
                receipt_ref=receipt_id,
                decision_ref=decision_id,
                lease_ref=lease_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=4,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        contract_ids = _violation_contract_ids(report.violations)
        assert "side_effect.authorization" in contract_ids

    def test_detects_missing_lease(self) -> None:
        """tool_call.start without lease_ref triggers workspace.isolation violation."""
        run_id = _uid("run")
        task_id = _uid("task")
        approval_id = _uid("approval")
        grant_id = _uid("grant")
        decision_id = _uid("decision")
        receipt_id = _uid("receipt")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="approval.granted",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                approval_ref=approval_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=2,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=grant_id,
                # No lease_ref
                decision_ref=decision_id,
                approval_ref=approval_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="receipt.issued",
                event_seq=3,
                step_id="step-0",
                step_attempt_id="attempt-0",
                receipt_ref=receipt_id,
                grant_ref=grant_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=4,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        contract_ids = _violation_contract_ids(report.violations)
        assert "workspace.isolation" in contract_ids


# ---------------------------------------------------------------------------
# TestMissingReceipt
# ---------------------------------------------------------------------------


class TestMissingReceipt:
    """tool_call.start without matching receipt.issued."""

    def test_detects_missing_receipt(self) -> None:
        """Trace: tool_call.start -> task.completed (no receipt.issued).

        Expected: governance.receipt_for_mutation invariant violation (high).
        """
        run_id = _uid("run")
        task_id = _uid("task")
        approval_id = _uid("approval")
        grant_id = _uid("grant")
        lease_id = _uid("lease")
        decision_id = _uid("decision")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="approval.granted",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                approval_ref=approval_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=2,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=grant_id,
                lease_ref=lease_id,
                decision_ref=decision_id,
                approval_ref=approval_id,
            ),
            # No receipt.issued event
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=3,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        ids = _violation_contract_ids(report.violations)
        assert "governance.receipt_for_mutation" in ids

        # Verify the violation is an InvariantViolation
        receipt_violations = [
            v
            for v in report.violations
            if isinstance(v, InvariantViolation)
            and v.invariant_id == "governance.receipt_for_mutation"
        ]
        assert len(receipt_violations) == 1
        assert receipt_violations[0].step_attempt_id == "attempt-0"


# ---------------------------------------------------------------------------
# TestDuplicateExecution
# ---------------------------------------------------------------------------


class TestDuplicateExecution:
    """Same step_attempt produces duplicate receipts."""

    def test_detects_duplicate_receipt(self) -> None:
        """Two receipt.issued events with same (step_attempt_id, receipt_ref) pair.

        Expected: no_duplicate_execution contract violation (post_run, blocker).
        """
        run_id = _uid("run")
        task_id = _uid("task")
        approval_id = _uid("approval")
        grant_id = _uid("grant")
        lease_id = _uid("lease")
        decision_id = _uid("decision")
        receipt_id = _uid("receipt")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="approval.granted",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                approval_ref=approval_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=2,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=grant_id,
                lease_ref=lease_id,
                decision_ref=decision_id,
                approval_ref=approval_id,
            ),
            # First receipt
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="receipt.issued",
                event_seq=3,
                step_id="step-0",
                step_attempt_id="attempt-0",
                receipt_ref=receipt_id,
                grant_ref=grant_id,
                lease_ref=lease_id,
                decision_ref=decision_id,
            ),
            # Duplicate receipt — same (step_attempt_id, receipt_ref) pair
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="receipt.issued",
                event_seq=4,
                step_id="step-0",
                step_attempt_id="attempt-0",
                receipt_ref=receipt_id,
                grant_ref=grant_id,
                lease_ref=lease_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=5,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        contract_ids = _violation_contract_ids(report.violations)
        assert "no_duplicate_execution" in contract_ids


# ---------------------------------------------------------------------------
# TestAuthorityChainIncomplete
# ---------------------------------------------------------------------------


class TestAuthorityChainIncomplete:
    """tool_call.start missing decision_ref or grant_ref or lease_ref."""

    def test_missing_decision_ref(self) -> None:
        """tool_call.start with grant_ref and lease_ref but no decision_ref.

        Expected: governance.authority_chain_complete invariant violation.
        """
        run_id = _uid("run")
        task_id = _uid("task")
        approval_id = _uid("approval")
        grant_id = _uid("grant")
        lease_id = _uid("lease")
        receipt_id = _uid("receipt")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="approval.granted",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                approval_ref=approval_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=2,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=grant_id,
                lease_ref=lease_id,
                # No decision_ref
                approval_ref=approval_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="receipt.issued",
                event_seq=3,
                step_id="step-0",
                step_attempt_id="attempt-0",
                receipt_ref=receipt_id,
                grant_ref=grant_id,
                lease_ref=lease_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=4,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        ids = _violation_contract_ids(report.violations)
        assert "governance.authority_chain_complete" in ids

        # Verify the missing field is decision_ref
        chain_violations = [
            v
            for v in report.violations
            if isinstance(v, InvariantViolation)
            and v.invariant_id == "governance.authority_chain_complete"
        ]
        assert len(chain_violations) >= 1
        assert "decision_ref" in chain_violations[0].evidence["missing_refs"]

    def test_missing_all_refs(self) -> None:
        """tool_call.start with no decision_ref, no grant_ref, no lease_ref.

        Expected: governance.authority_chain_complete with all three missing refs,
        plus side_effect.authorization and workspace.isolation contract violations.
        """
        run_id = _uid("run")
        task_id = _uid("task")
        approval_id = _uid("approval")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="approval.granted",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                approval_ref=approval_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=2,
                step_id="step-0",
                step_attempt_id="attempt-0",
                # No grant_ref, no lease_ref, no decision_ref
                approval_ref=approval_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=3,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        ids = _violation_contract_ids(report.violations)

        # Authority chain invariant catches missing decision_ref + grant_ref + lease_ref
        assert "governance.authority_chain_complete" in ids

        # Contract-level checks also fire
        assert "side_effect.authorization" in ids
        assert "workspace.isolation" in ids

        # Verify all three refs are identified as missing
        chain_violations = [
            v
            for v in report.violations
            if isinstance(v, InvariantViolation)
            and v.invariant_id == "governance.authority_chain_complete"
        ]
        assert len(chain_violations) >= 1
        missing = chain_violations[0].evidence["missing_refs"]
        assert "decision_ref" in missing
        assert "grant_ref" in missing
        assert "lease_ref" in missing


# ---------------------------------------------------------------------------
# TestMultipleViolations
# ---------------------------------------------------------------------------


class TestMultipleViolations:
    """Trace with multiple concurrent violations."""

    def test_multiple_violations_all_detected(self) -> None:
        """Trace with missing approval AND missing receipt AND missing grant.

        All should appear in report.violations.
        """
        run_id = _uid("run")
        task_id = _uid("task")
        lease_id = _uid("lease")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            # tool_call.start with no approval, no grant, no decision
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                lease_ref=lease_id,
                # No grant_ref, no decision_ref, no approval before this
            ),
            # No receipt.issued
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=2,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        ids = _violation_contract_ids(report.violations)

        # approval.gating — no approval.granted before tool_call.start
        assert "approval.gating" in ids
        # side_effect.authorization — no grant_ref on tool_call.start
        assert "side_effect.authorization" in ids
        # governance.receipt_for_mutation — no receipt for the tool_call
        assert "governance.receipt_for_mutation" in ids
        # governance.authority_chain_complete — missing decision_ref + grant_ref
        assert "governance.authority_chain_complete" in ids

        # Multiple violations detected
        assert len(report.violations) >= 4

    def test_violations_sorted_by_event_seq(self) -> None:
        """Violations should be in trace order, not random."""
        run_id = _uid("run")
        task_id = _uid("task")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            # First tool_call without approval or refs
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
            ),
            # Second tool_call without approval or refs
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=2,
                step_id="step-1",
                step_attempt_id="attempt-1",
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=3,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        assert len(report.violations) >= 2

        # For invariant violations, check trace_slice_start ordering
        invariant_violations = [v for v in report.violations if isinstance(v, InvariantViolation)]
        if len(invariant_violations) >= 2:
            for i in range(len(invariant_violations) - 1):
                assert (
                    invariant_violations[i].trace_slice_start
                    <= invariant_violations[i + 1].trace_slice_start
                ), "Invariant violations should be sorted by trace_slice_start"


# ---------------------------------------------------------------------------
# TestCleanGovernedTrace
# ---------------------------------------------------------------------------


class TestCleanGovernedTrace:
    """A properly governed trace should pass all checks including runtime.

    Runtime contract checks now pass prior_envelopes as context, so
    ``approval.gating`` correctly sees prior ``approval.granted`` events
    and does NOT fire on clean governed traces.  All contract, invariant,
    and runtime checks produce zero violations.
    """

    def test_invariants_pass_for_governed_trace(self) -> None:
        """All invariant checks pass on make_governed_trace(num_steps=5)."""
        envelopes = make_governed_trace(num_steps=5)

        lab = AssuranceLab()
        invariant_violations = lab.invariant_engine.check(envelopes)

        assert invariant_violations == []

    def test_post_run_contracts_pass_for_governed_trace(self) -> None:
        """All post_run contract checks pass on make_governed_trace(num_steps=5)."""
        envelopes = make_governed_trace(num_steps=5)

        lab = AssuranceLab()
        post_run_violations = lab.contract_engine.evaluate_post_run(envelopes)

        assert post_run_violations == []

    def test_full_governance_chain_no_invariant_violations(self) -> None:
        """make_governed_trace produces zero invariant violations for 10 steps."""
        envelopes = make_governed_trace(num_steps=10)

        lab = AssuranceLab()
        invariant_violations = lab.invariant_engine.check(envelopes)

        assert len(invariant_violations) == 0

    def test_oracle_satisfied_with_clean_governed_trace(self) -> None:
        """Oracle passes when governed trace produces zero violations.

        Runtime contract checks now pass prior_envelopes as context, so
        ``approval.gating`` correctly sees prior ``approval.granted``
        events and does NOT fire on clean governed traces.  The oracle
        passes because there are zero violations.
        """
        envelopes = make_governed_trace(num_steps=3)

        oracle = OracleSpec(
            final_state="completed",  # clean trace -> status="pass" -> final_state="completed"
            must_pass_contracts=[
                "task.lifecycle",
                "no_duplicate_execution",
                "receipt.linkage",
            ],
            max_unresolved_violations=0,
        )

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(attribution_mode="off"), envelopes)

        # Clean governed trace: zero violations, status="pass"
        assert len(report.violations) == 0
        assert report.status == "pass"
        assert lab.check_oracle(report, oracle) is True

    def test_oracle_fails_when_post_run_contract_violated(self) -> None:
        """Oracle rejects a trace that violates a must_pass post_run contract.

        A trace with task.created but no terminal event (task.completed or
        task.failed) violates the task.lifecycle contract.
        """
        run_id = _uid("run")
        task_id = _uid("task")

        # Trace with task.created but no terminal event
        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
        ]

        oracle = OracleSpec(
            final_state="failed",  # matches report status (blocker violation)
            must_pass_contracts=["task.lifecycle"],
        )

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(attribution_mode="off"), envelopes)

        # task.lifecycle is violated (missing terminal event)
        assert report.status == "fail"
        contract_ids = _violation_contract_ids(report.violations)
        assert "task.lifecycle" in contract_ids

        # Oracle rejects because must_pass contract is violated
        assert lab.check_oracle(report, oracle) is False

    def test_report_clean_for_governed_trace(self) -> None:
        """Governed trace produces zero violations with context-aware runtime checks.

        Runtime contract checks now pass prior_envelopes as context, so
        ``approval.gating`` correctly sees prior ``approval.granted``
        events.  A clean governed trace produces zero violations across
        all check modes (runtime, post_run, invariant).
        """
        envelopes = make_governed_trace(num_steps=2)

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(attribution_mode="off"), envelopes)

        # Zero violations of any kind
        assert len(report.violations) == 0
        assert report.status == "pass"

    def test_governed_trace_with_many_steps(self) -> None:
        """Larger governed trace (10 steps) has zero invariant/post_run violations."""
        envelopes = make_governed_trace(num_steps=10)

        lab = AssuranceLab()

        # Invariant checks pass
        inv_violations = lab.invariant_engine.check(envelopes)
        assert len(inv_violations) == 0

        # Post_run contract checks pass
        post_violations = lab.contract_engine.evaluate_post_run(envelopes)
        assert len(post_violations) == 0

    def test_oracle_rejects_violated_contract(self) -> None:
        """Oracle must_pass_contracts check fails when contract is violated."""
        run_id = _uid("run")
        task_id = _uid("task")

        # Minimal trace that violates approval.gating
        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=_uid("grant"),
                lease_ref=_uid("lease"),
                decision_ref=_uid("decision"),
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="receipt.issued",
                event_seq=2,
                step_id="step-0",
                step_attempt_id="attempt-0",
                receipt_ref=_uid("receipt"),
                grant_ref=_uid("grant"),
                decision_ref=_uid("decision"),
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=3,
            ),
        ]

        oracle = OracleSpec(
            final_state="completed",
            must_pass_contracts=["approval.gating"],
        )

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(attribution_mode="off"), envelopes)

        # Oracle should fail because approval.gating is violated
        assert lab.check_oracle(report, oracle) is False


# ---------------------------------------------------------------------------
# TestReceiptLinkage
# ---------------------------------------------------------------------------


class TestReceiptLinkage:
    """receipt.issued events must carry decision_ref and grant_ref."""

    def test_receipt_without_decision_ref(self) -> None:
        """receipt.issued missing decision_ref triggers receipt.linkage violation."""
        run_id = _uid("run")
        task_id = _uid("task")
        approval_id = _uid("approval")
        grant_id = _uid("grant")
        lease_id = _uid("lease")
        decision_id = _uid("decision")
        receipt_id = _uid("receipt")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="approval.granted",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                approval_ref=approval_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=2,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=grant_id,
                lease_ref=lease_id,
                decision_ref=decision_id,
                approval_ref=approval_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="receipt.issued",
                event_seq=3,
                step_id="step-0",
                step_attempt_id="attempt-0",
                receipt_ref=receipt_id,
                grant_ref=grant_id,
                # No decision_ref on receipt
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=4,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        contract_ids = _violation_contract_ids(report.violations)
        assert "receipt.linkage" in contract_ids

    def test_receipt_without_grant_ref(self) -> None:
        """receipt.issued missing grant_ref triggers receipt.linkage violation."""
        run_id = _uid("run")
        task_id = _uid("task")
        approval_id = _uid("approval")
        grant_id = _uid("grant")
        lease_id = _uid("lease")
        decision_id = _uid("decision")
        receipt_id = _uid("receipt")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="approval.granted",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
                approval_ref=approval_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=2,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=grant_id,
                lease_ref=lease_id,
                decision_ref=decision_id,
                approval_ref=approval_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="receipt.issued",
                event_seq=3,
                step_id="step-0",
                step_attempt_id="attempt-0",
                receipt_ref=receipt_id,
                decision_ref=decision_id,
                # No grant_ref on receipt
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=4,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        contract_ids = _violation_contract_ids(report.violations)
        assert "receipt.linkage" in contract_ids


# ---------------------------------------------------------------------------
# TestSideEffectAuthorized
# ---------------------------------------------------------------------------


class TestSideEffectAuthorized:
    """tool_call.start must have approval_ref or grant_ref (invariant)."""

    def test_tool_call_without_any_authorization(self) -> None:
        """tool_call.start with neither approval_ref nor grant_ref.

        Expected: governance.side_effect_authorized invariant violation.
        """
        run_id = _uid("run")
        task_id = _uid("task")
        lease_id = _uid("lease")
        decision_id = _uid("decision")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="approval.granted",
                event_seq=1,
                step_id="step-0",
                step_attempt_id="attempt-0",
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=2,
                step_id="step-0",
                step_attempt_id="attempt-0",
                # No approval_ref, no grant_ref
                lease_ref=lease_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=3,
            ),
        ]

        lab = AssuranceLab()
        report = lab.run_with_trace(_scenario(), envelopes)

        assert report.status == "fail"
        ids = _violation_contract_ids(report.violations)
        assert "governance.side_effect_authorized" in ids
