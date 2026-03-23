"""Integration tests: scenario matrix stress testing.

Runs the assurance system against a wide variety of trace patterns to
verify robustness. Uses parametrize for compact, comprehensive coverage.
"""

from __future__ import annotations

import time
import uuid

import pytest

from hermit.kernel.verification.assurance.lab import AssuranceLab
from hermit.kernel.verification.assurance.models import (
    AssuranceReport,
    ContractViolation,
    InvariantViolation,
    OracleSpec,
    ScenarioMetadata,
    ScenarioSpec,
)
from tests.assurance.conftest import make_envelope, make_governed_trace


def _uid(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _make_scenario(
    *,
    attribution_mode: str = "off",
    oracle: OracleSpec | None = None,
) -> ScenarioSpec:
    """Create a minimal ScenarioSpec for testing."""
    return ScenarioSpec(
        scenario_id=_uid("scenario"),
        metadata=ScenarioMetadata(name="matrix-test"),
        attribution_mode=attribution_mode,
        oracle=oracle or OracleSpec(),
    )


# ---------------------------------------------------------------------------
# TestTraceVariants
# ---------------------------------------------------------------------------


class TestTraceVariants:
    """Test various trace structures."""

    @pytest.mark.parametrize("num_steps", [1, 3, 5, 10, 20])
    def test_governed_trace_scales(self, num_steps: int) -> None:
        """make_governed_trace(num_steps) produces a valid governed trace.

        Runtime contract checks now pass prior_envelopes as context, so
        ``approval.gating`` correctly sees prior ``approval.granted``
        events and does NOT fire on clean governed traces.  All contract,
        invariant, and runtime checks produce zero violations.
        """
        lab = AssuranceLab()
        scenario = _make_scenario()
        envelopes = make_governed_trace(num_steps=num_steps)

        report = lab.run_with_trace(scenario, envelopes)

        assert isinstance(report, AssuranceReport)

        # Clean governed traces produce zero violations
        assert len(report.violations) == 0
        assert report.status == "pass"

    def test_empty_trace_handled(self) -> None:
        """Empty trace list should be handled gracefully (no crash)."""
        lab = AssuranceLab()
        scenario = _make_scenario()

        # run_with_trace with empty list -- uses a generated run_id
        report = lab.run_with_trace(scenario, [])

        assert isinstance(report, AssuranceReport)
        # An empty trace fails the task.lifecycle contract (no task.created)
        assert report.status == "fail"

    def test_single_event_trace(self) -> None:
        """Just task.created -- may fail lifecycle but must not crash."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        envelopes = [
            make_envelope(event_type="task.created", event_seq=0),
        ]

        report = lab.run_with_trace(scenario, envelopes)

        assert isinstance(report, AssuranceReport)
        # task.lifecycle requires task.completed or task.failed
        lifecycle_violations = [
            v
            for v in report.violations
            if isinstance(v, ContractViolation) and v.contract_id == "task.lifecycle"
        ]
        assert len(lifecycle_violations) == 1
        assert report.status == "fail"

    def test_task_failed_terminal_state(self) -> None:
        """task.created -> task.failed should satisfy lifecycle contract."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        now = time.time()
        envelopes = [
            make_envelope(event_type="task.created", event_seq=0, wallclock_at=now),
            make_envelope(event_type="task.failed", event_seq=1, wallclock_at=now + 0.01),
        ]

        report = lab.run_with_trace(scenario, envelopes)

        assert isinstance(report, AssuranceReport)
        # task.lifecycle should pass because task.failed is a terminal state
        lifecycle_violations = [
            v
            for v in report.violations
            if isinstance(v, ContractViolation) and v.contract_id == "task.lifecycle"
        ]
        assert len(lifecycle_violations) == 0


# ---------------------------------------------------------------------------
# TestViolationMatrix
# ---------------------------------------------------------------------------


class TestViolationMatrix:
    """Matrix of violation types vs detection."""

    @pytest.mark.parametrize(
        "missing_ref,expected_contract",
        [
            ("approval_ref", "approval.gating"),
            ("grant_ref", "side_effect.authorization"),
            ("lease_ref", "workspace.isolation"),
        ],
    )
    def test_missing_ref_detected(self, missing_ref: str, expected_contract: str) -> None:
        """Build trace with one ref missing -- correct contract catches it."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        now = time.time()

        # Build a governed trace with one step
        run_id = "run-missing-ref"
        task_id = "task-missing-ref"
        step_id = "step-0"
        attempt_id = "attempt-0"
        approval_id = _uid("approval")
        grant_id = _uid("grant")
        lease_id = _uid("lease")
        receipt_id = _uid("receipt")
        decision_id = _uid("decision")

        # Full refs for the tool_call.start
        tool_refs: dict[str, object] = {
            "grant_ref": grant_id,
            "lease_ref": lease_id,
            "decision_ref": decision_id,
            "approval_ref": approval_id,
        }
        # Remove the ref under test
        tool_refs[missing_ref] = None

        # For approval_ref, the approval.gating contract checks whether
        # approval.granted appears BEFORE tool_call.start in the trace
        # (not whether approval_ref is set on the envelope).  To trigger
        # the violation we must omit the approval.granted event entirely.
        include_approval_events = missing_ref != "approval_ref"

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
        ]

        seq = 1
        if include_approval_events:
            envelopes.extend(
                [
                    make_envelope(
                        run_id=run_id,
                        task_id=task_id,
                        event_type="approval.requested",
                        event_seq=seq,
                        wallclock_at=now + seq * 0.001,
                        step_id=step_id,
                        step_attempt_id=attempt_id,
                        approval_ref=approval_id,
                    ),
                    make_envelope(
                        run_id=run_id,
                        task_id=task_id,
                        event_type="approval.granted",
                        event_seq=seq + 1,
                        wallclock_at=now + (seq + 1) * 0.001,
                        step_id=step_id,
                        step_attempt_id=attempt_id,
                        approval_ref=approval_id,
                        decision_ref=decision_id,
                    ),
                ]
            )
            seq += 2

        envelopes.extend(
            [
                make_envelope(
                    run_id=run_id,
                    task_id=task_id,
                    event_type="tool_call.start",
                    event_seq=seq,
                    wallclock_at=now + seq * 0.001,
                    step_id=step_id,
                    step_attempt_id=attempt_id,
                    **tool_refs,
                ),
                make_envelope(
                    run_id=run_id,
                    task_id=task_id,
                    event_type="receipt.issued",
                    event_seq=seq + 1,
                    wallclock_at=now + (seq + 1) * 0.001,
                    step_id=step_id,
                    step_attempt_id=attempt_id,
                    receipt_ref=receipt_id,
                    grant_ref=grant_id,
                    lease_ref=lease_id,
                    decision_ref=decision_id,
                ),
                make_envelope(
                    run_id=run_id,
                    task_id=task_id,
                    event_type="task.completed",
                    event_seq=seq + 2,
                    wallclock_at=now + (seq + 2) * 0.001,
                ),
            ]
        )

        report = lab.run_with_trace(scenario, envelopes)

        violated_contracts = {
            v.contract_id for v in report.violations if isinstance(v, ContractViolation)
        }
        violated_invariants = {
            v.invariant_id for v in report.violations if isinstance(v, InvariantViolation)
        }
        all_violated = violated_contracts | violated_invariants

        assert expected_contract in all_violated, (
            f"Expected {expected_contract} to be violated when {missing_ref} is None, "
            f"but got violations: {all_violated}"
        )

    @pytest.mark.parametrize(
        "missing_event,expected_violation",
        [
            ("approval.granted", "approval.gating"),
            ("receipt.issued", "governance.receipt_for_mutation"),
        ],
    )
    def test_missing_event_detected(self, missing_event: str, expected_violation: str) -> None:
        """Build trace without this event -- correct checker catches it."""
        lab = AssuranceLab()
        scenario = _make_scenario()

        # Build full governed trace then remove the target event
        envelopes = make_governed_trace(num_steps=1)
        filtered = [e for e in envelopes if e.event_type != missing_event]

        report = lab.run_with_trace(scenario, filtered)

        violated_contracts = {
            v.contract_id for v in report.violations if isinstance(v, ContractViolation)
        }
        violated_invariants = {
            v.invariant_id for v in report.violations if isinstance(v, InvariantViolation)
        }
        all_violated = violated_contracts | violated_invariants

        assert expected_violation in all_violated, (
            f"Expected {expected_violation} when {missing_event} is missing, "
            f"but got violations: {all_violated}"
        )


# ---------------------------------------------------------------------------
# TestInvariantMatrix
# ---------------------------------------------------------------------------


class TestInvariantMatrix:
    """Matrix of invariant violations."""

    def test_event_seq_gap_detected(self) -> None:
        """event_seq: 0, 1, 3 (gap at 2) -- hash_chain_continuity."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        now = time.time()
        run_id = "run-gap"
        task_id = "task-gap"

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="generic",
                event_seq=1,
                wallclock_at=now + 0.001,
            ),
            # seq=2 is skipped
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=3,
                wallclock_at=now + 0.003,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)

        invariant_ids = {
            v.invariant_id for v in report.violations if isinstance(v, InvariantViolation)
        }
        assert "trace.hash_chain_continuity" in invariant_ids

    def test_event_seq_out_of_order(self) -> None:
        """event_seq: 0, 2, 1, 3 -- total_order violation."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        now = time.time()
        run_id = "run-ooo"
        task_id = "task-ooo"

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="generic",
                event_seq=2,
                wallclock_at=now + 0.002,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="generic",
                event_seq=1,
                wallclock_at=now + 0.001,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=3,
                wallclock_at=now + 0.003,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)

        invariant_ids = {
            v.invariant_id for v in report.violations if isinstance(v, InvariantViolation)
        }
        assert "scheduler.total_order_per_task" in invariant_ids

    def test_duplicate_step_attempt_claim(self) -> None:
        """Same step_attempt_id claimed by two different actors."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        now = time.time()
        run_id = "run-dup"
        task_id = "task-dup"
        attempt_id = "attempt-shared"

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="dispatch.claimed",
                event_seq=1,
                wallclock_at=now + 0.001,
                step_attempt_id=attempt_id,
                actor_id="worker-A",
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="dispatch.claimed",
                event_seq=2,
                wallclock_at=now + 0.002,
                step_attempt_id=attempt_id,
                actor_id="worker-B",
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=3,
                wallclock_at=now + 0.003,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)

        invariant_ids = {
            v.invariant_id for v in report.violations if isinstance(v, InvariantViolation)
        }
        assert "scheduler.single_winner_per_task" in invariant_ids

        # Verify evidence contains both actors
        for v in report.violations:
            if (
                isinstance(v, InvariantViolation)
                and v.invariant_id == "scheduler.single_winner_per_task"
            ):
                assert "worker-A" in v.evidence["actors"]
                assert "worker-B" in v.evidence["actors"]


# ---------------------------------------------------------------------------
# TestAttributionMatrix
# ---------------------------------------------------------------------------


class TestAttributionMatrix:
    """Attribution works for different failure patterns."""

    def test_single_root_cause(self) -> None:
        """One missing approval -- correctly identified."""
        lab = AssuranceLab()
        scenario = _make_scenario(attribution_mode="post_run")

        # Trace with tool_call but no approval.granted
        now = time.time()
        run_id = "run-attr-single"
        task_id = "task-attr-single"
        grant_id = _uid("grant")
        lease_id = _uid("lease")
        decision_id = _uid("decision")

        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=1,
                wallclock_at=now + 0.001,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=grant_id,
                lease_ref=lease_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="receipt.issued",
                event_seq=2,
                wallclock_at=now + 0.002,
                step_id="step-0",
                step_attempt_id="attempt-0",
                receipt_ref=_uid("receipt"),
                grant_ref=grant_id,
                decision_ref=decision_id,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=3,
                wallclock_at=now + 0.003,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)

        assert report.attribution is not None
        assert len(report.attribution.root_cause_candidates) > 0
        assert report.attribution.selected_root_cause != ""

    def test_cascading_failure(self) -> None:
        """Missing approval -> no grant -> multiple violations.

        Attribution should identify approval as root cause, rest as propagated.
        """
        lab = AssuranceLab()
        scenario = _make_scenario(attribution_mode="post_run")
        now = time.time()
        run_id = "run-cascade"
        task_id = "task-cascade"

        # tool_call.start with nothing: no approval, no grant, no lease
        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=1,
                wallclock_at=now + 0.001,
                step_id="step-0",
                step_attempt_id="attempt-0",
                # Missing: approval_ref, grant_ref, lease_ref, decision_ref
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=2,
                wallclock_at=now + 0.002,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)

        # Multiple violations expected (approval.gating, side_effect.authorization,
        # workspace.isolation, governance.authority_chain_complete, etc.)
        assert len(report.violations) >= 2
        assert report.attribution is not None
        assert len(report.attribution.root_cause_candidates) >= 1
        assert report.attribution.selected_root_cause != ""
        # The propagation chain should include the root cause
        assert report.attribution.selected_root_cause in (report.attribution.propagation_chain)

    def test_no_violations_no_attribution(self) -> None:
        """Clean trace (no tool_call) -- attribution is None."""
        lab = AssuranceLab()
        scenario = _make_scenario(attribution_mode="post_run")
        now = time.time()

        # A simple lifecycle trace with no tool_call events avoids all
        # contract and invariant violations.
        envelopes = [
            make_envelope(
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                event_type="task.completed",
                event_seq=1,
                wallclock_at=now + 0.001,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)

        assert report.status == "pass"
        assert len(report.violations) == 0
        # No violations means no attribution even with post_run mode
        assert report.attribution is None


# ---------------------------------------------------------------------------
# TestScenarioConfigurations
# ---------------------------------------------------------------------------


class TestScenarioConfigurations:
    """Different ScenarioSpec configurations."""

    @pytest.mark.parametrize("attribution_mode", ["off", "post_run"])
    def test_attribution_mode_respected(self, attribution_mode: str) -> None:
        """off -> no attribution in report, post_run -> attribution present (if violations)."""
        lab = AssuranceLab()
        scenario = _make_scenario(attribution_mode=attribution_mode)
        now = time.time()
        run_id = "run-attrmode"
        task_id = "task-attrmode"

        # Build a trace with a violation (missing approval)
        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=1,
                wallclock_at=now + 0.001,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=_uid("grant"),
                lease_ref=_uid("lease"),
                decision_ref=_uid("decision"),
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=2,
                wallclock_at=now + 0.002,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)

        # Should have at least one violation (missing approval.granted before tool_call)
        assert len(report.violations) > 0

        if attribution_mode == "off":
            assert report.attribution is None
        else:
            assert report.attribution is not None

    def test_oracle_with_allowed_failures(self) -> None:
        """Oracle allows specific failures -- oracle passes despite violations.

        The report status is determined by violation severity (blocker/high
        -> "fail").  The oracle's ``allowed_failures`` list filters out
        named contract violations when checking ``max_unresolved_violations``,
        so even though the report status is "fail", the oracle can still
        pass if we set ``final_state`` accordingly and allow the violations.
        """
        lab = AssuranceLab()
        oracle = OracleSpec(
            final_state="failed",
            allowed_failures=[
                "approval.gating",
                "side_effect.authorization",
                "workspace.isolation",
            ],
            max_unresolved_violations=10,
        )
        scenario = _make_scenario(oracle=oracle)
        now = time.time()
        run_id = "run-oracle-allowed"
        task_id = "task-oracle-allowed"

        # Trace with missing approval (produces blocker violations -> status=fail)
        envelopes = [
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=1,
                wallclock_at=now + 0.001,
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
                wallclock_at=now + 0.002,
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
                wallclock_at=now + 0.003,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)

        # Report has violations (status="fail"), oracle expects "failed"
        assert report.status == "fail"
        assert len(report.violations) > 0

        oracle_pass = lab.check_oracle(report, oracle)
        assert oracle_pass is True

    def test_oracle_rejects_when_must_pass_violated(self) -> None:
        """Oracle fails when a must_pass contract is violated."""
        lab = AssuranceLab()
        oracle = OracleSpec(
            final_state="failed",
            must_pass_contracts=["approval.gating"],
            max_unresolved_violations=100,
        )
        scenario = _make_scenario()
        now = time.time()

        # Trace that violates approval.gating
        envelopes = [
            make_envelope(
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                event_type="tool_call.start",
                event_seq=1,
                wallclock_at=now + 0.001,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=_uid("grant"),
                lease_ref=_uid("lease"),
                decision_ref=_uid("decision"),
            ),
            make_envelope(
                event_type="task.completed",
                event_seq=2,
                wallclock_at=now + 0.002,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)
        oracle_pass = lab.check_oracle(report, oracle)
        assert oracle_pass is False

    def test_scenario_with_no_oracle(self) -> None:
        """No oracle -- report still generated, just no oracle check."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        envelopes = make_governed_trace(num_steps=2)

        report = lab.run_with_trace(scenario, envelopes)

        assert isinstance(report, AssuranceReport)
        assert report.report_id.startswith("report-")
        assert report.scenario_id == scenario.scenario_id


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases that should not crash."""

    def test_all_events_same_timestamp(self) -> None:
        """All events share the same wallclock_at."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        now = time.time()

        envelopes = [
            make_envelope(
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                event_type="task.completed",
                event_seq=1,
                wallclock_at=now,  # same timestamp
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)
        assert isinstance(report, AssuranceReport)

    def test_very_long_payload(self) -> None:
        """Large payload dict -- no crash."""
        lab = AssuranceLab()
        scenario = _make_scenario()

        large_payload = {f"key_{i}": f"value_{i}" * 100 for i in range(500)}

        envelopes = [
            make_envelope(
                event_type="task.created",
                event_seq=0,
                payload=large_payload,
            ),
            make_envelope(
                event_type="task.completed",
                event_seq=1,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)
        assert isinstance(report, AssuranceReport)

    def test_unicode_in_payload(self) -> None:
        """Chinese/emoji in payload -- no crash."""
        lab = AssuranceLab()
        scenario = _make_scenario()

        unicode_payload = {
            "description": "This is a governed task with unicode.",
            "name_zh": "test",
            "emoji_field": "check mark",
            "mixed": "Hello, a]c",
        }

        envelopes = [
            make_envelope(
                event_type="task.created",
                event_seq=0,
                payload=unicode_payload,
            ),
            make_envelope(
                event_type="task.completed",
                event_seq=1,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)
        assert isinstance(report, AssuranceReport)

    def test_none_refs_everywhere(self) -> None:
        """All refs are None -- appropriate violations detected but no crash."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        now = time.time()

        envelopes = [
            make_envelope(
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                event_type="tool_call.start",
                event_seq=1,
                wallclock_at=now + 0.001,
                step_id="step-0",
                step_attempt_id="attempt-0",
                # All refs default to None
            ),
            make_envelope(
                event_type="task.completed",
                event_seq=2,
                wallclock_at=now + 0.002,
            ),
        ]

        report = lab.run_with_trace(scenario, envelopes)

        assert isinstance(report, AssuranceReport)
        # Should detect missing refs
        assert len(report.violations) > 0
        assert report.status == "fail"

    def test_concurrent_runs_isolated(self) -> None:
        """Two different run_ids -- each checked independently."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        now = time.time()

        run_a = "run-A"
        run_b = "run-B"
        task_a = "task-A"
        task_b = "task-B"

        envelopes_a = [
            make_envelope(
                run_id=run_a,
                task_id=task_a,
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
            ),
            make_envelope(
                run_id=run_a,
                task_id=task_a,
                event_type="task.completed",
                event_seq=1,
                wallclock_at=now + 0.001,
            ),
        ]

        envelopes_b = [
            make_envelope(
                run_id=run_b,
                task_id=task_b,
                event_type="task.created",
                event_seq=0,
                wallclock_at=now + 0.010,
            ),
            make_envelope(
                run_id=run_b,
                task_id=task_b,
                event_type="task.completed",
                event_seq=1,
                wallclock_at=now + 0.011,
            ),
        ]

        report_a = lab.run_with_trace(scenario, envelopes_a)
        report_b = lab.run_with_trace(scenario, envelopes_b)

        # Both should pass independently
        assert report_a.status == "pass"
        assert report_b.status == "pass"
        # Reports have different IDs
        assert report_a.report_id != report_b.report_id
        assert report_a.run_id != report_b.run_id

    def test_mixed_event_types_no_crash(self) -> None:
        """Various unknown/custom event types -- no crash."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        now = time.time()

        envelopes = [
            make_envelope(event_type="task.created", event_seq=0, wallclock_at=now),
            make_envelope(
                event_type="custom.something",
                event_seq=1,
                wallclock_at=now + 0.001,
            ),
            make_envelope(
                event_type="another.unknown.event",
                event_seq=2,
                wallclock_at=now + 0.002,
            ),
            make_envelope(event_type="task.completed", event_seq=3, wallclock_at=now + 0.003),
        ]

        report = lab.run_with_trace(scenario, envelopes)
        assert isinstance(report, AssuranceReport)
        assert report.status == "pass"

    def test_many_steps_same_step_id(self) -> None:
        """Multiple events referencing the same step_id -- no crash."""
        lab = AssuranceLab()
        scenario = _make_scenario()
        now = time.time()

        envelopes = [
            make_envelope(event_type="task.created", event_seq=0, wallclock_at=now),
        ]
        # 10 generic events all with step_id="step-shared"
        for i in range(1, 11):
            envelopes.append(
                make_envelope(
                    event_type="generic",
                    event_seq=i,
                    wallclock_at=now + i * 0.001,
                    step_id="step-shared",
                )
            )
        envelopes.append(
            make_envelope(event_type="task.completed", event_seq=11, wallclock_at=now + 0.012)
        )

        report = lab.run_with_trace(scenario, envelopes)
        assert isinstance(report, AssuranceReport)

    def test_zero_wallclock_at(self) -> None:
        """Events with wallclock_at=0 -- no crash."""
        lab = AssuranceLab()
        scenario = _make_scenario()

        envelopes = [
            make_envelope(event_type="task.created", event_seq=0, wallclock_at=0.0),
            make_envelope(event_type="task.completed", event_seq=1, wallclock_at=0.0),
        ]

        report = lab.run_with_trace(scenario, envelopes)
        assert isinstance(report, AssuranceReport)
