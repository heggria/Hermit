"""Integration tests for the end-to-end trace replay pipeline.

Tests the full flow:
  generate/capture trace -> persist -> load -> replay -> assurance checks -> report
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict

from hermit.kernel.verification.assurance.attribution import FailureAttributionEngine
from hermit.kernel.verification.assurance.contracts import AssuranceContractEngine
from hermit.kernel.verification.assurance.invariants import InvariantEngine
from hermit.kernel.verification.assurance.lab import AssuranceLab
from hermit.kernel.verification.assurance.models import (
    ContractViolation,
    CounterfactualMutation,
    InvariantViolation,
    OracleSpec,
    ScenarioMetadata,
    ScenarioSpec,
    TraceEnvelope,
)
from hermit.kernel.verification.assurance.recorder import TraceRecorder
from hermit.kernel.verification.assurance.replay import ReplayService
from hermit.kernel.verification.assurance.reporting import AssuranceReporter
from tests.assurance.conftest import make_envelope, make_governed_trace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _minimal_scenario(
    scenario_id: str = "integration-test",
    attribution_mode: str = "off",
    **kwargs: object,
) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id=scenario_id,
        metadata=ScenarioMetadata(name=scenario_id),
        attribution_mode=attribution_mode,
        **kwargs,
    )


def _build_assurance_report(
    envelopes: list[TraceEnvelope],
    scenario: ScenarioSpec,
    *,
    contract_engine: AssuranceContractEngine | None = None,
    invariant_engine: InvariantEngine | None = None,
    reporter: AssuranceReporter | None = None,
    attribution_engine: FailureAttributionEngine | None = None,
):
    """Wire real engines together to produce an assurance report.

    This replicates the logic of AssuranceLab.run_with_trace() but uses
    the correct method names on the real engine implementations.
    """
    ce = contract_engine or AssuranceContractEngine()
    ie = invariant_engine or InvariantEngine()
    rp = reporter or AssuranceReporter()
    ae = attribution_engine or FailureAttributionEngine()

    run_id = envelopes[0].run_id if envelopes else _uid("run")

    # Runtime contract checks (per envelope, accumulating prior context)
    runtime_violations: list[ContractViolation] = []
    prior: list[TraceEnvelope] = []
    for env in envelopes:
        per_env = ce.evaluate_runtime(env, context={"prior_envelopes": prior})
        runtime_violations.extend(per_env)
        prior.append(env)

    # Post-run checks
    invariant_violations = ie.check(envelopes)
    contract_violations = ce.evaluate_post_run(envelopes)

    # Attribution
    all_violations: list[ContractViolation | InvariantViolation] = [
        *runtime_violations,
        *invariant_violations,
        *contract_violations,
    ]

    attribution = None
    if scenario.attribution_mode != "off" and all_violations:
        attribution = ae.attribute(envelopes=envelopes, violations=all_violations)

    # Build report via reporter
    return rp.build_report(
        run_id=run_id,
        scenario_id=scenario.scenario_id,
        invariant_violations=[v for v in all_violations if isinstance(v, InvariantViolation)],
        contract_violations=[v for v in all_violations if isinstance(v, ContractViolation)],
        envelopes=envelopes,
        attribution=attribution,
        oracle=scenario.oracle if scenario.oracle.must_pass_contracts else None,
    )


# ---------------------------------------------------------------------------
# TestTraceCapture
# ---------------------------------------------------------------------------


class TestTraceCapture:
    """Tests for TraceRecorder capture and persistence."""

    def test_recorder_captures_governed_trace(self) -> None:
        """Create TraceRecorder, record a governed trace manually, verify envelopes."""
        recorder = TraceRecorder()
        run_id = recorder.start_run(scenario_id="capture-test")
        task_id = "task-capture"

        # Simulate governed execution: task.created -> approval cycle -> task.completed
        recorder.record("task.created", task_id, run_id=run_id)

        approval_ref = _uid("approval")
        grant_ref = _uid("grant")
        lease_ref = _uid("lease")
        decision_ref = _uid("decision")
        receipt_ref = _uid("receipt")

        recorder.record(
            "approval.requested",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            approval_ref=approval_ref,
        )
        recorder.record(
            "approval.granted",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            approval_ref=approval_ref,
            decision_ref=decision_ref,
        )
        recorder.record(
            "tool_call.start",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            grant_ref=grant_ref,
            lease_ref=lease_ref,
            decision_ref=decision_ref,
            approval_ref=approval_ref,
        )
        recorder.record(
            "receipt.issued",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            receipt_ref=receipt_ref,
            grant_ref=grant_ref,
            lease_ref=lease_ref,
            decision_ref=decision_ref,
        )
        recorder.record("task.completed", task_id, run_id=run_id)

        # Verify
        trace = recorder.get_trace(run_id)
        assert len(trace) == 6

        event_types = [e.event_type for e in trace]
        assert event_types == [
            "task.created",
            "approval.requested",
            "approval.granted",
            "tool_call.start",
            "receipt.issued",
            "task.completed",
        ]

        # Verify monotonic event_seq
        seqs = [e.event_seq for e in trace]
        assert seqs == list(range(6))

        # Verify ref fields are stored
        tool_call_env = trace[3]
        assert tool_call_env.grant_ref == grant_ref
        assert tool_call_env.lease_ref == lease_ref
        assert tool_call_env.decision_ref == decision_ref

    def test_recorder_persists_to_store(self, kernel_store) -> None:
        """Record trace, persist envelopes to KernelStore, verify retrieval."""
        recorder = TraceRecorder()
        run_id = recorder.start_run(scenario_id="persist-test")
        task_id = "task-persist"

        # Record a minimal governed trace
        recorder.record("task.created", task_id, run_id=run_id)
        recorder.record(
            "approval.requested",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            approval_ref=_uid("approval"),
        )
        recorder.record("task.completed", task_id, run_id=run_id)

        # Persist to store
        trace = recorder.get_trace(run_id)
        for env in trace:
            kernel_store.create_trace_envelope(
                trace_id=env.trace_id,
                run_id=env.run_id,
                task_id=env.task_id,
                event_seq=env.event_seq,
                event_type=env.event_type,
                envelope_json=asdict(env),
                wallclock_at=env.wallclock_at,
                scenario_id=env.scenario_id,
            )

        # Retrieve from store
        stored = kernel_store.get_trace_envelopes(run_id)
        assert len(stored) == 3
        assert stored[0]["event_type"] == "task.created"
        assert stored[1]["event_type"] == "approval.requested"
        assert stored[2]["event_type"] == "task.completed"


# ---------------------------------------------------------------------------
# TestReplayPipeline
# ---------------------------------------------------------------------------


class TestReplayPipeline:
    """Tests for replay, assurance checking, and counterfactual analysis."""

    def test_clean_trace_passes_assurance(self) -> None:
        """A valid governed trace should produce a passing assurance report."""
        envelopes = make_governed_trace(num_steps=3)
        scenario = _minimal_scenario()

        report = _build_assurance_report(envelopes, scenario)

        assert report.status == "pass"
        assert report.verdict == "clean"
        assert report.first_violation is None
        assert report.violations == []

    def test_bad_trace_fails_assurance(self) -> None:
        """A trace with tool_call.start before approval.granted should fail."""
        now = time.time()
        run_id = "run-bad"
        task_id = "task-bad"

        # Build a bad trace: task.created -> tool_call.start (no approval!)
        # -> receipt.issued -> task.completed
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
                lease_ref=_uid("lease"),
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

        scenario = _minimal_scenario()
        report = _build_assurance_report(envelopes, scenario)

        assert report.status == "fail"
        assert report.first_violation is not None

    def test_ingest_and_replay(self) -> None:
        """Create trace, ingest into ReplayService, replay, verify diff shows all same."""
        envelopes = make_governed_trace(num_steps=2)
        run_id = envelopes[0].run_id

        replay_service = ReplayService()
        entry = replay_service.ingest(run_id, envelopes, scenario_id="replay-test")

        result = replay_service.replay(entry, envelopes)

        assert result.entry_id == entry.entry_id
        assert result.diff_summary["same"] == len(envelopes)
        assert result.diff_summary["diverged"] == 0
        assert result.diff_summary["missing"] == []
        assert result.diff_summary["extra"] == []
        assert result.diff_summary["head_hash_match"] is True

    def test_counterfactual_drops_event(self) -> None:
        """Drop an event via counterfactual, verify diff shows missing."""
        envelopes = make_governed_trace(num_steps=2)
        run_id = envelopes[0].run_id

        replay_service = ReplayService()
        entry = replay_service.ingest(run_id, envelopes, scenario_id="cf-drop-test")

        # Drop the second envelope (approval.requested for step-0)
        target_trace_id = envelopes[1].trace_id
        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="drop_event",
                target_ref=target_trace_id,
                description="Drop approval.requested to test missing detection",
            )
        ]

        result = replay_service.counterfactual(entry, envelopes, mutations)

        assert len(result.diff_summary["missing"]) == 1
        assert target_trace_id in result.diff_summary["missing"]
        # The remaining events should all be same
        assert result.diff_summary["same"] == len(envelopes) - 1


# ---------------------------------------------------------------------------
# TestEndToEnd
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Full pipeline tests: capture -> persist -> load -> replay -> report."""

    def test_full_pipeline(self, kernel_store) -> None:
        """Exercise the complete assurance pipeline end-to-end.

        1. Create TraceRecorder and record governed execution events
        2. Persist the trace to KernelStore
        3. Load the trace from store
        4. Run assurance checks (contracts + invariants)
        5. Build report and verify it passes
        6. Check the report has correct timeline and evidence_refs
        """
        # 1. Create recorder and simulate governed execution
        recorder = TraceRecorder()
        run_id = recorder.start_run(scenario_id="e2e-pipeline")
        task_id = "task-e2e"

        approval_ref = _uid("approval")
        grant_ref = _uid("grant")
        lease_ref = _uid("lease")
        decision_ref = _uid("decision")
        receipt_ref = _uid("receipt")

        recorder.record("task.created", task_id, run_id=run_id)
        recorder.record(
            "approval.requested",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            approval_ref=approval_ref,
        )
        recorder.record(
            "approval.granted",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            approval_ref=approval_ref,
            decision_ref=decision_ref,
        )
        recorder.record(
            "tool_call.start",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            grant_ref=grant_ref,
            lease_ref=lease_ref,
            decision_ref=decision_ref,
            approval_ref=approval_ref,
        )
        recorder.record(
            "receipt.issued",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            receipt_ref=receipt_ref,
            grant_ref=grant_ref,
            lease_ref=lease_ref,
            decision_ref=decision_ref,
        )
        recorder.record("task.completed", task_id, run_id=run_id)

        trace = recorder.get_trace(run_id)
        assert len(trace) == 6

        # 2. Persist to store
        for env in trace:
            kernel_store.create_trace_envelope(
                trace_id=env.trace_id,
                run_id=env.run_id,
                task_id=env.task_id,
                event_seq=env.event_seq,
                event_type=env.event_type,
                envelope_json=asdict(env),
                wallclock_at=env.wallclock_at,
                scenario_id=env.scenario_id,
            )

        # 3. Load from store
        stored_rows = kernel_store.get_trace_envelopes(run_id)
        assert len(stored_rows) == 6

        # Reconstruct envelopes from store
        loaded_envelopes = []
        for row in stored_rows:
            env_data = json.loads(row["envelope_json"])
            loaded_envelopes.append(TraceEnvelope(**env_data))

        assert len(loaded_envelopes) == 6
        assert loaded_envelopes[0].event_type == "task.created"
        assert loaded_envelopes[-1].event_type == "task.completed"

        # 4-5. Run assurance checks and build report
        scenario = _minimal_scenario(
            scenario_id="e2e-pipeline",
            oracle=OracleSpec(
                final_state="completed",
                must_pass_contracts=["task.lifecycle", "approval.gating"],
            ),
        )

        report = _build_assurance_report(loaded_envelopes, scenario)

        # 6. Verify
        assert report.status == "pass"
        assert report.violations == []
        assert report.first_violation is None
        assert report.scenario_id == "e2e-pipeline"

        # Timeline should be populated
        assert report.timelines.get("event_count") == 6
        assert "start" in report.timelines
        assert "end" in report.timelines

        # Evidence refs should include governance refs from the trace
        assert grant_ref in report.evidence_refs
        assert lease_ref in report.evidence_refs
        assert decision_ref in report.evidence_refs
        assert receipt_ref in report.evidence_refs
        assert approval_ref in report.evidence_refs

        # Oracle check
        lab = AssuranceLab()
        assert lab.check_oracle(report, scenario.oracle) is True

    def test_full_pipeline_with_violation(self, kernel_store) -> None:
        """Same pipeline but with a governance violation.

        Introduces tool_call.start without grant_ref -> should fail
        with the right contract violation.
        """
        # 1. Record a trace with a missing grant_ref on tool_call.start
        recorder = TraceRecorder()
        run_id = recorder.start_run(scenario_id="e2e-violation")
        task_id = "task-violation"

        approval_ref = _uid("approval")
        decision_ref = _uid("decision")
        lease_ref = _uid("lease")

        recorder.record("task.created", task_id, run_id=run_id)
        recorder.record(
            "approval.requested",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            approval_ref=approval_ref,
        )
        recorder.record(
            "approval.granted",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            approval_ref=approval_ref,
            decision_ref=decision_ref,
        )
        # tool_call.start WITHOUT grant_ref -- violates side_effect.authorization
        # and governance.authority_chain_complete invariant
        recorder.record(
            "tool_call.start",
            task_id,
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            lease_ref=lease_ref,
            decision_ref=decision_ref,
            approval_ref=approval_ref,
            # No grant_ref!
        )
        recorder.record("task.completed", task_id, run_id=run_id)

        trace = recorder.get_trace(run_id)

        # 2. Persist
        for env in trace:
            kernel_store.create_trace_envelope(
                trace_id=env.trace_id,
                run_id=env.run_id,
                task_id=env.task_id,
                event_seq=env.event_seq,
                event_type=env.event_type,
                envelope_json=asdict(env),
                wallclock_at=env.wallclock_at,
                scenario_id=env.scenario_id,
            )

        # 3. Load
        stored_rows = kernel_store.get_trace_envelopes(run_id)
        loaded_envelopes = [
            TraceEnvelope(**json.loads(row["envelope_json"])) for row in stored_rows
        ]

        # 4. Run assurance
        scenario = _minimal_scenario(
            scenario_id="e2e-violation",
            attribution_mode="post_run",
        )
        report = _build_assurance_report(loaded_envelopes, scenario)

        # 5. Verify failure
        assert report.status == "fail"
        assert report.first_violation is not None
        assert len(report.violations) > 0

        # Should have side_effect.authorization contract violation (runtime check)
        contract_violation_ids = {
            v.contract_id for v in report.violations if isinstance(v, ContractViolation)
        }
        assert "side_effect.authorization" in contract_violation_ids

        # Should also have governance.authority_chain_complete invariant violation
        invariant_violation_ids = {
            v.invariant_id for v in report.violations if isinstance(v, InvariantViolation)
        }
        assert "governance.authority_chain_complete" in invariant_violation_ids

        # Also governance.receipt_for_mutation since no receipt.issued
        assert "governance.receipt_for_mutation" in invariant_violation_ids

    def test_counterfactual_identifies_root_cause(self) -> None:
        """Demonstrate counterfactual replay's attribution power.

        1. Create a trace where approval is denied (causing downstream failure)
        2. Run assurance -> should fail (no approval.granted before tool_call.start)
        3. Toggle the denied approval to granted via counterfactual
        4. Run assurance on the counterfactual trace -> should pass
        5. This shows the denied approval was the root cause
        """
        now = time.time()
        run_id = "run-cf-root"
        task_id = "task-cf-root"

        approval_ref = _uid("approval")
        decision_ref = _uid("decision")
        grant_ref = _uid("grant")
        lease_ref = _uid("lease")
        receipt_ref = _uid("receipt")

        # Build trace with approval.denied followed by tool_call.start
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
                event_type="approval.requested",
                event_seq=1,
                wallclock_at=now + 0.001,
                step_id="step-0",
                step_attempt_id="attempt-0",
                approval_ref=approval_ref,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="approval.denied",
                event_seq=2,
                wallclock_at=now + 0.002,
                step_id="step-0",
                step_attempt_id="attempt-0",
                approval_ref=approval_ref,
                decision_ref=decision_ref,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="tool_call.start",
                event_seq=3,
                wallclock_at=now + 0.003,
                step_id="step-0",
                step_attempt_id="attempt-0",
                grant_ref=grant_ref,
                lease_ref=lease_ref,
                decision_ref=decision_ref,
                approval_ref=approval_ref,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="receipt.issued",
                event_seq=4,
                wallclock_at=now + 0.004,
                step_id="step-0",
                step_attempt_id="attempt-0",
                receipt_ref=receipt_ref,
                grant_ref=grant_ref,
                lease_ref=lease_ref,
                decision_ref=decision_ref,
            ),
            make_envelope(
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=5,
                wallclock_at=now + 0.005,
            ),
        ]

        # Step 2: Original trace should fail (approval.denied, not approval.granted)
        scenario = _minimal_scenario()
        original_report = _build_assurance_report(envelopes, scenario)

        # The approval.gating contract checks that approval.granted appears before
        # tool_call.start. With approval.denied instead, this should fail.
        assert original_report.status == "fail"
        contract_ids = {
            v.contract_id for v in original_report.violations if isinstance(v, ContractViolation)
        }
        assert "approval.gating" in contract_ids

        # Step 3: Apply counterfactual -- toggle the denied approval to granted
        replay_service = ReplayService()
        entry = replay_service.ingest(run_id, envelopes, scenario_id="cf-root")

        denied_trace_id = envelopes[2].trace_id
        mutations = [
            CounterfactualMutation(
                mutation_id=_uid("mut"),
                mutation_type="toggle_approval",
                target_ref=denied_trace_id,
                description="Toggle approval.denied -> approval.granted",
            )
        ]

        cf_result = replay_service.counterfactual(entry, envelopes, mutations)

        # Step 4: Verify the counterfactual produced a divergence at the toggled event
        assert cf_result.diff_summary["diverged"] >= 1

        # Reconstruct the mutated trace for assurance checking
        # Apply the same mutation to get the modified envelopes
        from dataclasses import replace

        mutated_envelopes = list(envelopes)
        for i, env in enumerate(mutated_envelopes):
            if env.trace_id == denied_trace_id:
                mutated_envelopes[i] = replace(env, event_type="approval.granted")
                break

        cf_report = _build_assurance_report(mutated_envelopes, scenario)

        # Step 5: The counterfactual trace (with approval.granted) should pass
        # the approval.gating contract
        cf_contract_ids = {
            v.contract_id for v in cf_report.violations if isinstance(v, ContractViolation)
        }
        assert "approval.gating" not in cf_contract_ids

        # The original had approval.gating violation, the counterfactual does not.
        # This demonstrates that the denied approval was the root cause of the
        # approval.gating failure.
        original_violation_count = len(original_report.violations)
        cf_violation_count = len(cf_report.violations)
        assert cf_violation_count < original_violation_count
