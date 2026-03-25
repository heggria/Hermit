"""Unit tests for AssuranceLab."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from hermit.kernel.verification.assurance.models import (
    AssuranceReport,
    AttributionCase,
    ContractViolation,
    FaultHandle,
    FaultSpec,
    InvariantViolation,
    OracleSpec,
    ReplayEntry,
    ScenarioMetadata,
    ScenarioSpec,
)
from tests.assurance.conftest import make_governed_trace

if TYPE_CHECKING:
    from hermit.kernel.verification.assurance.lab import AssuranceLab


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_scenario(
    scenario_id: str = "test-scenario",
    attribution_mode: str = "off",
    **kwargs: Any,
) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id=scenario_id,
        metadata=ScenarioMetadata(name=scenario_id),
        attribution_mode=attribution_mode,
        **kwargs,
    )


def _mock_violation(
    contract_id: str = "test.contract",
    severity: str = "high",
    task_id: str = "task-test",
) -> ContractViolation:
    return ContractViolation(
        violation_id=f"viol-{contract_id}",
        contract_id=contract_id,
        severity=severity,
        mode="runtime",
        task_id=task_id,
    )


def _mock_invariant_violation(
    invariant_id: str = "test.invariant",
    severity: str = "blocker",
    task_id: str = "task-test",
) -> InvariantViolation:
    return InvariantViolation(
        violation_id=f"viol-{invariant_id}",
        invariant_id=invariant_id,
        severity=severity,
        event_id="evt-0",
        task_id=task_id,
    )


_PATCHES = (
    "hermit.kernel.verification.assurance.lab.TraceRecorder",
    "hermit.kernel.verification.assurance.lab.InvariantEngine",
    "hermit.kernel.verification.assurance.lab.AssuranceContractEngine",
    "hermit.kernel.verification.assurance.lab.FaultInjector",
    "hermit.kernel.verification.assurance.lab.ReplayService",
    "hermit.kernel.verification.assurance.lab.FailureAttributionEngine",
    "hermit.kernel.verification.assurance.lab.AssuranceReporter",
)


def _make_lab() -> AssuranceLab:
    """Create an AssuranceLab with all dependencies mocked."""
    with (
        patch(_PATCHES[0]),
        patch(_PATCHES[1]),
        patch(_PATCHES[2]),
        patch(_PATCHES[3]),
        patch(_PATCHES[4]),
        patch(_PATCHES[5]),
        patch(_PATCHES[6]),
    ):
        from hermit.kernel.verification.assurance.lab import AssuranceLab

        return AssuranceLab()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestAssuranceLabConstruction:
    def test_init_creates_all_components(self) -> None:
        """Lab initializes all sub-engines and an empty scenario registry."""
        lab = _make_lab()

        assert lab.recorder is not None
        assert lab.invariant_engine is not None
        assert lab.contract_engine is not None
        assert lab.injector is not None
        assert lab.replay_service is not None
        assert lab.attribution_engine is not None
        assert lab.reporter is not None
        assert lab._scenarios == {}


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------


class TestScenarioRegistry:
    def test_register_and_load(self) -> None:
        lab = _make_lab()
        spec = _minimal_scenario("my-scenario")

        lab.register_scenario(spec)

        loaded = lab.load_scenario("my-scenario")
        assert loaded is spec

    def test_load_missing_returns_none(self) -> None:
        lab = _make_lab()
        assert lab.load_scenario("nonexistent") is None

    def test_list_scenarios_sorted(self) -> None:
        lab = _make_lab()
        lab.register_scenario(_minimal_scenario("z-scenario"))
        lab.register_scenario(_minimal_scenario("a-scenario"))
        lab.register_scenario(_minimal_scenario("m-scenario"))

        assert lab.list_scenarios() == ["a-scenario", "m-scenario", "z-scenario"]

    def test_list_scenarios_empty(self) -> None:
        lab = _make_lab()
        assert lab.list_scenarios() == []

    def test_register_overwrites_existing(self) -> None:
        lab = _make_lab()
        spec1 = _minimal_scenario("same-id", attribution_mode="off")
        spec2 = _minimal_scenario("same-id", attribution_mode="post_run")

        lab.register_scenario(spec1)
        lab.register_scenario(spec2)

        loaded = lab.load_scenario("same-id")
        assert loaded is spec2
        assert loaded.attribution_mode == "post_run"


# ---------------------------------------------------------------------------
# Oracle checking
# ---------------------------------------------------------------------------


def _clean_report(status: str = "pass") -> AssuranceReport:
    return AssuranceReport(
        report_id="rpt-1",
        scenario_id="test",
        run_id="run-1",
        status=status,
    )


class TestCheckOracle:
    def test_oracle_pass_clean_report(self) -> None:
        lab = _make_lab()
        oracle = OracleSpec(final_state="completed")
        report = _clean_report("pass")

        assert lab.check_oracle(report, oracle) is True

    def test_oracle_fail_status_mismatch(self) -> None:
        lab = _make_lab()
        oracle = OracleSpec(final_state="completed")
        report = _clean_report("fail")

        assert lab.check_oracle(report, oracle) is False

    def test_oracle_fail_must_pass_contract_violated(self) -> None:
        lab = _make_lab()
        oracle = OracleSpec(
            final_state="completed",
            must_pass_contracts=["approval.gating"],
        )
        report = _clean_report("pass")
        report.violations = [_mock_violation(contract_id="approval.gating", severity="medium")]

        assert lab.check_oracle(report, oracle) is False

    def test_oracle_pass_violation_not_in_must_pass(self) -> None:
        lab = _make_lab()
        oracle = OracleSpec(
            final_state="completed",
            must_pass_contracts=["approval.gating"],
            max_unresolved_violations=1,
        )
        report = _clean_report("pass")
        # Violation is for a contract not in must_pass -- severity is medium (not high/blocker)
        report.violations = [_mock_violation(contract_id="other.contract", severity="medium")]

        assert lab.check_oracle(report, oracle) is True

    def test_oracle_fail_too_many_duplicates(self) -> None:
        lab = _make_lab()
        oracle = OracleSpec(
            final_state="completed",
            max_duplicate_side_effects=0,
        )
        report = _clean_report("pass")
        report.duplicates = {"count": 3}

        assert lab.check_oracle(report, oracle) is False

    def test_oracle_pass_duplicates_within_limit(self) -> None:
        lab = _make_lab()
        oracle = OracleSpec(
            final_state="completed",
            max_duplicate_side_effects=5,
        )
        report = _clean_report("pass")
        report.duplicates = {"count": 3}

        assert lab.check_oracle(report, oracle) is True

    def test_oracle_fail_unresolved_violations_exceeded(self) -> None:
        lab = _make_lab()
        oracle = OracleSpec(
            final_state="completed",
            max_unresolved_violations=0,
        )
        report = _clean_report("pass")
        report.violations = [_mock_violation(severity="high")]

        assert lab.check_oracle(report, oracle) is False

    def test_oracle_pass_allowed_failures_excluded(self) -> None:
        lab = _make_lab()
        oracle = OracleSpec(
            final_state="completed",
            allowed_failures=["test.contract"],
            max_unresolved_violations=0,
        )
        report = _clean_report("pass")
        report.violations = [_mock_violation(contract_id="test.contract", severity="high")]

        assert lab.check_oracle(report, oracle) is True

    def test_oracle_fail_expected_failed_state(self) -> None:
        """Oracle expects final_state=failed, report has status=pass."""
        lab = _make_lab()
        oracle = OracleSpec(final_state="failed")
        report = _clean_report("pass")

        assert lab.check_oracle(report, oracle) is False

    def test_oracle_pass_expected_failed_state(self) -> None:
        """Oracle expects final_state=failed, report has status=fail."""
        lab = _make_lab()
        oracle = OracleSpec(final_state="failed")
        report = _clean_report("fail")

        assert lab.check_oracle(report, oracle) is True

    def test_oracle_invariant_violations_count_as_unresolved(self) -> None:
        lab = _make_lab()
        oracle = OracleSpec(
            final_state="completed",
            max_unresolved_violations=0,
        )
        report = _clean_report("pass")
        report.violations = [_mock_invariant_violation(severity="blocker")]

        assert lab.check_oracle(report, oracle) is False


# ---------------------------------------------------------------------------
# run_with_trace
# ---------------------------------------------------------------------------


class TestRunWithTrace:
    def test_clean_trace_passes(self) -> None:
        lab = _make_lab()
        envelopes = make_governed_trace(num_steps=2)
        scenario = _minimal_scenario()

        # No violations from any engine
        lab.contract_engine.evaluate_runtime.return_value = []
        lab.invariant_engine.check.return_value = []
        lab.contract_engine.evaluate_post_run.return_value = []

        report = lab.run_with_trace(scenario, envelopes)

        assert report.status == "pass"
        assert report.violations == []
        assert report.verdict == "clean"
        assert report.scenario_id == "test-scenario"
        assert report.first_violation is None

    def test_trace_with_contract_violation_fails(self) -> None:
        lab = _make_lab()
        envelopes = make_governed_trace(num_steps=1)
        scenario = _minimal_scenario()

        violation = _mock_violation(severity="high")
        # Return violation only for the first envelope, empty for the rest
        lab.contract_engine.evaluate_runtime.side_effect = [
            [violation],
            *([[] for _ in range(len(envelopes) - 1)]),
        ]
        lab.invariant_engine.check.return_value = []
        lab.contract_engine.evaluate_post_run.return_value = []

        report = lab.run_with_trace(scenario, envelopes)

        assert report.status == "fail"
        assert len(report.violations) == 1
        assert report.first_violation is violation

    def test_trace_with_invariant_violation_fails(self) -> None:
        lab = _make_lab()
        envelopes = make_governed_trace(num_steps=1)
        scenario = _minimal_scenario()

        inv_violation = _mock_invariant_violation(severity="blocker")
        lab.contract_engine.evaluate_runtime.return_value = []
        lab.invariant_engine.check.return_value = [inv_violation]
        lab.contract_engine.evaluate_post_run.return_value = []

        report = lab.run_with_trace(scenario, envelopes)

        assert report.status == "fail"
        assert len(report.violations) == 1
        assert report.first_violation is inv_violation

    def test_trace_records_all_envelopes(self) -> None:
        lab = _make_lab()
        envelopes = make_governed_trace(num_steps=2)
        scenario = _minimal_scenario()

        lab.contract_engine.evaluate_runtime.return_value = []
        lab.invariant_engine.check.return_value = []
        lab.contract_engine.evaluate_post_run.return_value = []

        lab.run_with_trace(scenario, envelopes)

        # recorder.record_envelope should be called for each envelope
        assert lab.recorder.record_envelope.call_count == len(envelopes)

    def test_attribution_runs_when_enabled_and_violations_present(self) -> None:
        lab = _make_lab()
        envelopes = make_governed_trace(num_steps=1)
        scenario = _minimal_scenario(attribution_mode="post_run")

        violation = _mock_violation(severity="high")
        lab.contract_engine.evaluate_runtime.side_effect = [
            [violation],
            *([[] for _ in range(len(envelopes) - 1)]),
        ]
        lab.invariant_engine.check.return_value = []
        lab.contract_engine.evaluate_post_run.return_value = []

        mock_case = AttributionCase(case_id="case-1")
        lab.attribution_engine.attribute.return_value = mock_case

        report = lab.run_with_trace(scenario, envelopes)

        lab.attribution_engine.attribute.assert_called_once()
        assert report.attribution is mock_case

    def test_attribution_skipped_when_off(self) -> None:
        lab = _make_lab()
        envelopes = make_governed_trace(num_steps=1)
        scenario = _minimal_scenario(attribution_mode="off")

        violation = _mock_violation(severity="high")
        lab.contract_engine.evaluate_runtime.side_effect = [
            [violation],
            *([[] for _ in range(len(envelopes) - 1)]),
        ]
        lab.invariant_engine.check.return_value = []
        lab.contract_engine.evaluate_post_run.return_value = []

        report = lab.run_with_trace(scenario, envelopes)

        lab.attribution_engine.attribute.assert_not_called()
        assert report.attribution is None

    def test_attribution_skipped_when_no_violations(self) -> None:
        lab = _make_lab()
        envelopes = make_governed_trace(num_steps=1)
        scenario = _minimal_scenario(attribution_mode="post_run")

        lab.contract_engine.evaluate_runtime.return_value = []
        lab.invariant_engine.check.return_value = []
        lab.contract_engine.evaluate_post_run.return_value = []

        report = lab.run_with_trace(scenario, envelopes)

        lab.attribution_engine.attribute.assert_not_called()
        assert report.attribution is None

    def test_medium_severity_does_not_cause_fail(self) -> None:
        lab = _make_lab()
        envelopes = make_governed_trace(num_steps=1)
        scenario = _minimal_scenario()

        violation = _mock_violation(severity="medium")
        lab.contract_engine.evaluate_runtime.return_value = []
        lab.invariant_engine.check.return_value = []
        lab.contract_engine.evaluate_post_run.return_value = [violation]

        report = lab.run_with_trace(scenario, envelopes)

        assert report.status == "pass"
        assert len(report.violations) == 1

    def test_multiple_violation_sources_combined(self) -> None:
        lab = _make_lab()
        envelopes = make_governed_trace(num_steps=1)
        scenario = _minimal_scenario()

        runtime_v = _mock_violation(contract_id="rt", severity="medium")
        inv_v = _mock_invariant_violation(severity="blocker")
        postrun_v = _mock_violation(contract_id="pr", severity="high")

        # Return runtime violation only for the first envelope
        lab.contract_engine.evaluate_runtime.side_effect = [
            [runtime_v],
            *([[] for _ in range(len(envelopes) - 1)]),
        ]
        lab.invariant_engine.check.return_value = [inv_v]
        lab.contract_engine.evaluate_post_run.return_value = [postrun_v]

        report = lab.run_with_trace(scenario, envelopes)

        assert report.status == "fail"
        assert len(report.violations) == 3


# ---------------------------------------------------------------------------
# run (full simulation)
# ---------------------------------------------------------------------------


class TestRun:
    def test_run_produces_report(self) -> None:
        lab = _make_lab()
        scenario = _minimal_scenario()

        lab.invariant_engine.check.return_value = []
        lab.contract_engine.evaluate_post_run.return_value = []

        report = lab.run(scenario)

        assert isinstance(report, AssuranceReport)
        assert report.scenario_id == "test-scenario"
        assert report.run_id.startswith("run-")

    def test_run_arms_faults(self) -> None:
        lab = _make_lab()
        fault = FaultSpec(injection_point="queue_dispatch", fault_mode="duplicate_delivery")
        scenario = _minimal_scenario(fault_injection_plan=[fault])

        mock_handle = FaultHandle(handle_id="h-1", fault_spec=fault)
        lab.injector.arm.return_value = mock_handle
        lab.invariant_engine.check.return_value = []
        lab.contract_engine.evaluate_post_run.return_value = []

        lab.run(scenario)

        lab.injector.arm.assert_called_once_with(fault)

    def test_run_records_generated_trace(self) -> None:
        lab = _make_lab()
        scenario = _minimal_scenario()

        lab.invariant_engine.check.return_value = []
        lab.contract_engine.evaluate_post_run.return_value = []

        lab.run(scenario)

        # Minimal trace generates 2 envelopes (task.created + task.completed)
        assert lab.recorder.record_envelope.call_count == 2


# ---------------------------------------------------------------------------
# run_replay
# ---------------------------------------------------------------------------


class TestRunReplay:
    def test_replay_uses_registered_scenario(self) -> None:
        lab = _make_lab()
        scenario = _minimal_scenario("replay-scenario")
        lab.register_scenario(scenario)

        entry = ReplayEntry(
            entry_id="entry-1",
            scenario_id="replay-scenario",
            run_id="run-replay",
        )
        envelopes = make_governed_trace(num_steps=1, run_id="run-replay")

        from hermit.kernel.verification.assurance.models import ReplayResult

        lab.replay_service.replay_with_assurance.return_value = ReplayResult(
            replay_id="rpl-1",
            entry_id="entry-1",
            contract_violations=[],
            diff_summary={"same": len(envelopes)},
        )

        report = lab.run_replay(entry, envelopes)

        assert report.scenario_id == "replay-scenario"

    def test_replay_falls_back_to_minimal_scenario(self) -> None:
        lab = _make_lab()

        entry = ReplayEntry(
            entry_id="entry-1",
            scenario_id="unknown-scenario",
            run_id="run-replay",
        )
        envelopes = make_governed_trace(num_steps=1, run_id="run-replay")

        from hermit.kernel.verification.assurance.models import ReplayResult

        lab.replay_service.replay_with_assurance.return_value = ReplayResult(
            replay_id="rpl-1",
            entry_id="entry-1",
            contract_violations=[],
            diff_summary={"same": len(envelopes)},
        )

        report = lab.run_replay(entry, envelopes)

        assert report.scenario_id == "unknown-scenario"
        assert report.status == "pass"

    def test_replay_with_violations_fails(self) -> None:
        lab = _make_lab()

        entry = ReplayEntry(
            entry_id="entry-1",
            scenario_id="test-scenario",
            run_id="run-replay",
        )
        envelopes = make_governed_trace(num_steps=1, run_id="run-replay")

        from hermit.kernel.verification.assurance.models import ReplayResult

        violation = _mock_violation(severity="high")
        lab.replay_service.replay_with_assurance.return_value = ReplayResult(
            replay_id="rpl-1",
            entry_id="entry-1",
            contract_violations=[violation],
            diff_summary={"same": len(envelopes)},
        )

        report = lab.run_replay(entry, envelopes)

        assert report.status == "fail"
        assert len(report.violations) == 1
        assert report.first_violation is violation

    def test_replay_calls_replay_with_assurance(self) -> None:
        lab = _make_lab()

        entry = ReplayEntry(
            entry_id="entry-1",
            scenario_id="test-scenario",
            run_id="run-replay",
        )
        envelopes = make_governed_trace(num_steps=1, run_id="run-replay")

        from hermit.kernel.verification.assurance.models import ReplayResult

        lab.replay_service.replay_with_assurance.return_value = ReplayResult(
            replay_id="rpl-1",
            entry_id="entry-1",
            contract_violations=[],
            diff_summary={},
        )

        lab.run_replay(entry, envelopes)

        lab.replay_service.replay_with_assurance.assert_called_once_with(
            entry,
            envelopes,
            invariant_engine=lab.invariant_engine,
            contract_engine=lab.contract_engine,
        )


# ---------------------------------------------------------------------------
# replay_task
# ---------------------------------------------------------------------------


class TestReplayTask:
    def test_replay_task_returns_report(self) -> None:
        lab = _make_lab()
        envelopes = make_governed_trace(num_steps=1, run_id="run-1")

        # Set up recorder to return envelopes via public API
        lab.recorder.load_task_trace.return_value = envelopes

        mock_entry = ReplayEntry(
            entry_id="entry-1",
            scenario_id="",
            run_id="run-1",
        )
        lab.replay_service.ingest.return_value = mock_entry
        lab.invariant_engine.check.return_value = []
        lab.contract_engine.evaluate_post_run.return_value = []

        report = lab.replay_task("task-test")

        assert report is not None
        assert isinstance(report, AssuranceReport)
        assert report.status == "pass"

    def test_replay_task_returns_none_for_unknown(self) -> None:
        lab = _make_lab()
        lab.recorder.load_task_trace.return_value = []

        report = lab.replay_task("nonexistent-task")

        assert report is None

    def test_replay_task_returns_none_for_empty_trace(self) -> None:
        lab = _make_lab()
        lab.recorder.load_task_trace.return_value = []

        report = lab.replay_task("task-test")

        assert report is None
