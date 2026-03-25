"""AssuranceLab — scenario runner for the Trace-Contract-Driven Assurance System.

Orchestrates trace recording, fault injection, invariant/contract checking,
failure attribution, and report generation into a single entry point.
"""

from __future__ import annotations

import time
import uuid

import structlog

from hermit.kernel.verification.assurance.attribution import FailureAttributionEngine
from hermit.kernel.verification.assurance.contracts import AssuranceContractEngine
from hermit.kernel.verification.assurance.injection import FaultInjector
from hermit.kernel.verification.assurance.invariants import InvariantEngine
from hermit.kernel.verification.assurance.models import (
    AssuranceReport,
    ContractViolation,
    CounterfactualMutation,
    FaultHandle,
    InvariantViolation,
    OracleSpec,
    ReplayEntry,
    ScenarioMetadata,
    ScenarioSpec,
    TraceEnvelope,
)
from hermit.kernel.verification.assurance.recorder import TraceRecorder
from hermit.kernel.verification.assurance.replay import ReplayService
from hermit.kernel.verification.assurance.reporting import AssuranceReporter

log = structlog.get_logger()


def _rid() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def _report_id() -> str:
    return f"report-{uuid.uuid4().hex[:12]}"


class AssuranceLab:
    """Scenario runner and top-level assurance orchestrator.

    Ties together recording, fault injection, invariant/contract checking,
    attribution, replay, and reporting into a unified pipeline for governed
    execution assurance.
    """

    def __init__(self) -> None:
        self.recorder = TraceRecorder()
        self.invariant_engine = InvariantEngine()
        self.contract_engine = AssuranceContractEngine()
        self.injector = FaultInjector(harness_mode=True)
        self.replay_service = ReplayService()
        self.attribution_engine = FailureAttributionEngine()
        self.reporter = AssuranceReporter()
        self._scenarios: dict[str, ScenarioSpec] = {}

    # ------------------------------------------------------------------
    # Scenario registry
    # ------------------------------------------------------------------

    def register_scenario(self, spec: ScenarioSpec) -> None:
        """Register a scenario spec for later retrieval."""
        self._scenarios[spec.scenario_id] = spec
        log.info("assurance.scenario_registered", scenario_id=spec.scenario_id)

    def load_scenario(self, scenario_id: str) -> ScenarioSpec | None:
        """Load a previously registered scenario by ID."""
        return self._scenarios.get(scenario_id)

    def list_scenarios(self) -> list[str]:
        """Return sorted list of registered scenario IDs."""
        return sorted(self._scenarios.keys())

    # ------------------------------------------------------------------
    # Core run methods
    # ------------------------------------------------------------------

    def run(self, scenario: ScenarioSpec) -> AssuranceReport:
        """Run a full assurance scenario.

        1. Start run via recorder
        2. Arm faults from ``scenario.fault_injection_plan``
        3. Simulate workload by generating a minimal governed trace
        4. Post-run: check invariants + contracts
        5. Attribution if ``attribution_mode != 'off'``
        6. Build report via reporter
        7. Return report
        """
        run_id = _rid()
        log.info(
            "assurance.run_start",
            scenario_id=scenario.scenario_id,
            run_id=run_id,
        )

        # 1. Start run
        self.recorder.start_run(run_id=run_id, scenario_id=scenario.scenario_id)

        # 2. Arm faults
        fault_handles = self._arm_faults(scenario)

        # 3. Generate minimal trace (MVP: single task lifecycle)
        envelopes = self._generate_minimal_trace(
            run_id=run_id,
            scenario=scenario,
        )
        for env in envelopes:
            self.recorder.record_envelope(env)

        # 4–6. Delegate to shared post-processing
        return self._finalize_run(
            run_id=run_id,
            scenario=scenario,
            envelopes=envelopes,
            fault_handles=fault_handles,
        )

    def run_with_trace(
        self,
        scenario: ScenarioSpec,
        envelopes: list[TraceEnvelope],
    ) -> AssuranceReport:
        """Run assurance checks against a provided trace.

        This is the primary MVP entry point — accepts a pre-recorded trace
        and validates it against the scenario's contracts and invariants.

        1. Record all envelopes via recorder
        2. Runtime contract checks per envelope
        3. Post-run invariant + contract checks
        4. Attribution if needed
        5. Build and return report
        """
        run_id = envelopes[0].run_id if envelopes else _rid()
        log.info(
            "assurance.run_with_trace",
            scenario_id=scenario.scenario_id,
            run_id=run_id,
            envelope_count=len(envelopes),
        )

        self.recorder.start_run(run_id=run_id, scenario_id=scenario.scenario_id)

        # 1. Record all envelopes
        for env in envelopes:
            self.recorder.record_envelope(env)

        # 2. Runtime contract checks per envelope
        runtime_violations = self._check_runtime_contracts(envelopes, scenario)

        # 3–5. Post-run processing
        return self._finalize_run(
            run_id=run_id,
            scenario=scenario,
            envelopes=envelopes,
            fault_handles=[],
            runtime_violations=runtime_violations,
        )

    def run_replay(
        self,
        entry: ReplayEntry,
        envelopes: list[TraceEnvelope],
    ) -> AssuranceReport:
        """Replay historical trace and produce assurance report.

        Uses ``replay_with_assurance`` to run invariant and contract checks
        during the replay, then builds a full assurance report from the results.
        """
        log.info(
            "assurance.run_replay",
            entry_id=entry.entry_id,
            scenario_id=entry.scenario_id,
            run_id=entry.run_id,
        )

        replay_result = self.replay_service.replay_with_assurance(
            entry,
            envelopes,
            invariant_engine=self.invariant_engine,
            contract_engine=self.contract_engine,
        )

        scenario = self.load_scenario(entry.scenario_id)
        if scenario is None:
            scenario = ScenarioSpec(
                scenario_id=entry.scenario_id,
                metadata=ScenarioMetadata(name=entry.scenario_id),
            )

        # Determine status from replay violations
        violations: list[ContractViolation | InvariantViolation] = list(
            replay_result.contract_violations
        )
        has_blockers = any(v.severity == "blocker" for v in violations)
        has_high = any(v.severity == "high" for v in violations)
        status = "fail" if (has_blockers or has_high) else "pass"

        first_violation = violations[0] if violations else None

        report = AssuranceReport(
            report_id=_report_id(),
            scenario_id=scenario.scenario_id,
            run_id=entry.run_id,
            status=status,
            verdict=f"{len(violations)} violation(s) found" if violations else "clean",
            first_violation=first_violation,
            violations=violations,
            replay_diff=replay_result.diff_summary,
        )

        log.info(
            "assurance.run_replay_complete",
            entry_id=entry.entry_id,
            status=status,
            violation_count=len(violations),
        )
        return report

    def replay_task(
        self, task_id: str, *, attribution_mode: str = "post_run"
    ) -> AssuranceReport | None:
        """Load a task's trace from the recorder and replay through assurance.

        This is the main end-to-end entry point:
        task_id -> load trace -> replay -> invariant/contract checks -> report.

        Returns ``None`` if no trace is found for the given *task_id*.
        """
        envelopes = self.recorder.load_task_trace(task_id)

        if not envelopes:
            log.info("assurance.replay_task_no_trace", task_id=task_id)
            return None

        # Sort by event_seq for deterministic ordering
        envelopes.sort(key=lambda e: e.event_seq)

        run_id = envelopes[0].run_id
        entry = self.replay_service.ingest(
            run_id,
            envelopes,
            scenario_id=envelopes[0].scenario_id or "",
        )

        # Build scenario with the requested attribution mode
        scenario = self.load_scenario(entry.scenario_id)
        if scenario is None:
            scenario = ScenarioSpec(
                scenario_id=entry.scenario_id or task_id,
                metadata=ScenarioMetadata(name=entry.scenario_id or task_id),
                attribution_mode=attribution_mode,
            )
        else:
            # Override attribution_mode for this replay
            scenario = ScenarioSpec(
                scenario_id=scenario.scenario_id,
                metadata=scenario.metadata,
                attribution_mode=attribution_mode,
                fault_injection_plan=scenario.fault_injection_plan,
                oracle=scenario.oracle,
            )

        return self._finalize_run(
            run_id=run_id,
            scenario=scenario,
            envelopes=envelopes,
            fault_handles=[],
        )

    def replay_counterfactual_task(
        self,
        task_id: str,
        mutations: list[CounterfactualMutation],
    ) -> AssuranceReport | None:
        """Load a task's trace, apply mutations, and replay through assurance.

        Like :meth:`replay_task` but applies counterfactual mutations to the
        trace before running assurance checks.

        Returns ``None`` if no trace is found for the given *task_id*.
        """
        envelopes = self.recorder.load_task_trace(task_id)

        if not envelopes:
            log.info("assurance.replay_counterfactual_task_no_trace", task_id=task_id)
            return None

        envelopes.sort(key=lambda e: e.event_seq)

        run_id = envelopes[0].run_id
        entry = self.replay_service.ingest(
            run_id,
            envelopes,
            scenario_id=envelopes[0].scenario_id or "",
        )

        replay_result = self.replay_service.counterfactual_with_assurance(
            entry,
            envelopes,
            mutations,
            invariant_engine=self.invariant_engine,
            contract_engine=self.contract_engine,
        )

        scenario = self.load_scenario(envelopes[0].scenario_id or "")
        if scenario is None:
            scenario_id = envelopes[0].scenario_id or task_id
            scenario = ScenarioSpec(
                scenario_id=scenario_id,
                metadata=ScenarioMetadata(name=scenario_id),
            )

        violations: list[ContractViolation | InvariantViolation] = list(
            replay_result.contract_violations
        )
        has_blockers = any(v.severity == "blocker" for v in violations)
        has_high = any(v.severity == "high" for v in violations)
        status = "fail" if (has_blockers or has_high) else "pass"

        first_violation = violations[0] if violations else None

        report = AssuranceReport(
            report_id=_report_id(),
            scenario_id=scenario.scenario_id,
            run_id=run_id,
            status=status,
            verdict=f"{len(violations)} violation(s) found" if violations else "clean",
            first_violation=first_violation,
            violations=violations,
            replay_diff=replay_result.diff_summary,
        )

        return report

    # ------------------------------------------------------------------
    # Oracle
    # ------------------------------------------------------------------

    def check_oracle(self, report: AssuranceReport, oracle: OracleSpec) -> bool:
        """Validate a report against an oracle specification.

        Checks:
        - Final status matches ``oracle.final_state``
        - All ``must_pass_contracts`` are free of violations
        - Duplicate side effects within ``max_duplicate_side_effects``
        - Unresolved violations within ``max_unresolved_violations``

        Returns True if all oracle criteria are satisfied.
        """
        # Status check
        if oracle.final_state == "completed" and report.status != "pass":
            log.debug("oracle.status_mismatch", expected="pass", actual=report.status)
            return False
        if oracle.final_state == "failed" and report.status != "fail":
            log.debug("oracle.status_mismatch", expected="fail", actual=report.status)
            return False

        # Must-pass contract check
        violated_contract_ids = {
            v.contract_id for v in report.violations if isinstance(v, ContractViolation)
        }
        for contract_id in oracle.must_pass_contracts:
            if contract_id in violated_contract_ids:
                log.debug(
                    "oracle.must_pass_violated",
                    contract_id=contract_id,
                )
                return False

        # Duplicate side effects check
        dup_count = report.duplicates.get("count", 0)
        if dup_count > oracle.max_duplicate_side_effects:
            log.debug(
                "oracle.duplicate_limit_exceeded",
                count=dup_count,
                limit=oracle.max_duplicate_side_effects,
            )
            return False

        # Unresolved violations check
        unresolved = [
            v
            for v in report.violations
            if v.severity in ("blocker", "high")
            and (
                not isinstance(v, ContractViolation) or v.contract_id not in oracle.allowed_failures
            )
        ]
        if len(unresolved) > oracle.max_unresolved_violations:
            log.debug(
                "oracle.unresolved_limit_exceeded",
                count=len(unresolved),
                limit=oracle.max_unresolved_violations,
            )
            return False

        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _arm_faults(self, scenario: ScenarioSpec) -> list[FaultHandle]:
        """Arm all faults from the scenario's fault injection plan."""
        handles: list[FaultHandle] = []
        for fault_spec in scenario.fault_injection_plan:
            handle = self.injector.arm(fault_spec)
            handles.append(handle)
            log.debug(
                "assurance.fault_armed",
                handle_id=handle.handle_id,
                injection_point=fault_spec.injection_point,
            )
        return handles

    def _generate_minimal_trace(
        self,
        *,
        run_id: str,
        scenario: ScenarioSpec,
    ) -> list[TraceEnvelope]:
        """Generate a minimal governed-execution trace for simulation.

        MVP: produces task_created + task_completed lifecycle events.
        """
        now = time.time()
        task_id = f"sim-task-{uuid.uuid4().hex[:8]}"
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"

        return [
            TraceEnvelope(
                trace_id=trace_id,
                run_id=run_id,
                task_id=task_id,
                event_type="task.created",
                event_seq=0,
                wallclock_at=now,
                logical_clock=0,
                scenario_id=scenario.scenario_id,
            ),
            TraceEnvelope(
                trace_id=trace_id,
                run_id=run_id,
                task_id=task_id,
                event_type="task.completed",
                event_seq=1,
                wallclock_at=now + 0.001,
                logical_clock=1,
                scenario_id=scenario.scenario_id,
            ),
        ]

    def _check_runtime_contracts(
        self,
        envelopes: list[TraceEnvelope],
        scenario: ScenarioSpec,
    ) -> list[ContractViolation]:
        """Run runtime contract checks per envelope with accumulated context."""
        violations: list[ContractViolation] = []
        for i, env in enumerate(envelopes):
            prior = envelopes[:i]
            per_env = self.contract_engine.evaluate_runtime(env, context={"prior_envelopes": prior})
            violations.extend(per_env)
        return violations

    def _finalize_run(
        self,
        *,
        run_id: str,
        scenario: ScenarioSpec,
        envelopes: list[TraceEnvelope],
        fault_handles: list[FaultHandle],
        runtime_violations: list[ContractViolation] | None = None,
    ) -> AssuranceReport:
        """Shared post-processing: invariants, contracts, attribution, report."""
        all_violations: list[ContractViolation | InvariantViolation] = list(
            runtime_violations or []
        )

        # Post-run invariant checks
        invariant_violations = self.invariant_engine.check(envelopes)
        all_violations.extend(invariant_violations)

        # Post-run contract checks
        contract_violations = self.contract_engine.evaluate_post_run(envelopes)
        all_violations.extend(contract_violations)

        # Determine status
        has_blockers = any(v.severity == "blocker" for v in all_violations)
        has_high = any(v.severity == "high" for v in all_violations)
        status = "fail" if (has_blockers or has_high) else "pass"

        # Attribution
        attribution = None
        if scenario.attribution_mode != "off" and all_violations:
            attribution = self.attribution_engine.attribute(
                envelopes=envelopes,
                violations=all_violations,
            )

        # First violation
        first_violation = all_violations[0] if all_violations else None

        # Build report
        report = AssuranceReport(
            report_id=_report_id(),
            scenario_id=scenario.scenario_id,
            run_id=run_id,
            status=status,
            verdict=f"{len(all_violations)} violation(s) found" if all_violations else "clean",
            first_violation=first_violation,
            violations=all_violations,
            attribution=attribution,
        )

        log.info(
            "assurance.run_complete",
            scenario_id=scenario.scenario_id,
            run_id=run_id,
            status=status,
            violation_count=len(all_violations),
        )
        return report
