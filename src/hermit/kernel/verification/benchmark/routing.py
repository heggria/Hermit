from __future__ import annotations

import time
import uuid
from dataclasses import replace

import structlog

from hermit.kernel.verification.benchmark.models import (
    BenchmarkProfile,
    BenchmarkResultClass,
    BenchmarkRun,
    BenchmarkVerdict,
    TaskFamily,
)
from hermit.kernel.verification.benchmark.registry import BenchmarkProfileRegistry

__all__ = [
    "BenchmarkRoutingService",
    "MissingBaselineError",
    "ProfileFamilyMismatchError",
    "VerdictAlreadyConsumedError",
    "VerdictNotConsumedError",
]

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Path-based heuristics for task family classification
# ---------------------------------------------------------------------------

_FAMILY_PATH_RULES: list[tuple[TaskFamily, tuple[str, ...]]] = [
    (
        TaskFamily.governance_mutation,
        ("kernel/policy/", "kernel/authority/", "policy/approval", "kernel/verification/"),
    ),
    (
        TaskFamily.runtime_perf,
        ("execution/", "runtime/", "dispatch/", "kernel/ledger/"),
    ),
    (
        TaskFamily.surface_integration,
        ("surfaces/", "cli/", "adapters/", "plugins/builtin/adapters/"),
    ),
    (
        TaskFamily.learning_template,
        ("memory/", "template/", "pattern/", "context/memory/"),
    ),
]

# Governance-bench and performance-bench values that enable benchmarking.
_BENCH_ENABLED_VALUES = ("required", "optional")

# Lower-is-better keywords for threshold semantics.
# Spec governance metrics: unauthorized_effect_rate, stale_authorization_execution_rate,
# mean_recovery_depth, and operator_burden are all lower-is-better.
_LOWER_BETTER_KEYWORDS = (
    "latency",
    "error",
    "regression_count",
    "unauthorized_effect_rate",
    "stale_authorization_execution_rate",
    "mean_recovery_depth",
    "operator_burden",
)


class ProfileFamilyMismatchError(ValueError):
    """Raised when an explicit benchmark_profile does not match the task_family.

    Spec error #1: "跑错 benchmark"
    """


class MissingBaselineError(ValueError):
    """Raised when a profile requires a baseline_ref but the run has none.

    Spec error #2: "结果没法比较"
    """


class VerdictAlreadyConsumedError(ValueError):
    """Raised when attempting to consume a verdict that was already consumed.

    Idempotency guard: each verdict may only be consumed once by reconciliation.
    """


class VerdictNotConsumedError(ValueError):
    """Raised when a failed verdict has not been consumed by reconciliation.

    Security invariant: failed verdicts must be explicitly consumed to prevent
    silent ignorance of benchmark failures.
    """


class BenchmarkRoutingService:
    """Routes task verification through the appropriate benchmark profile.

    Flow:
    1. Read verification_requirements from contract
    2. Determine task_family (from contract or heuristic classification)
    3. Route to matching BenchmarkProfile via registry
    4. Validate profile matches task_family (防止跑错 benchmark)
    5. Validate baseline_ref if profile declares one (防止结果没法比较)
    6. Execute benchmark run (or mark as skipped if forbidden)
    7. Produce BenchmarkVerdict with benchmark_result_class
    8. Feed verdict into reconciliation input (not directly into memory)
    """

    def __init__(self, registry: BenchmarkProfileRegistry | None = None) -> None:
        self.registry = registry or BenchmarkProfileRegistry()

    # ------------------------------------------------------------------
    # Task family classification
    # ------------------------------------------------------------------

    def classify_task_family(
        self,
        *,
        action_classes: list[str],
        affected_paths: list[str],
        task_family_hint: str | None = None,
    ) -> TaskFamily:
        """Classify task into a TaskFamily based on action classes and paths.

        Heuristics:
        - kernel/policy/approval paths -> governance_mutation
        - execution/runtime/dispatch paths -> runtime_perf
        - surfaces/cli/adapters paths -> surface_integration
        - memory/template/pattern paths -> learning_template

        Falls back to ``runtime_perf`` when no heuristic matches.
        """
        if task_family_hint:
            try:
                return TaskFamily(task_family_hint)
            except ValueError:
                log.warning(
                    "benchmark_routing.invalid_family_hint",
                    hint=task_family_hint,
                )

        # Score each family by the number of matching paths.
        scores: dict[TaskFamily, int] = {family: 0 for family in TaskFamily}

        for path in affected_paths:
            normalised = path.replace("\\", "/").lower()
            for family, fragments in _FAMILY_PATH_RULES:
                for fragment in fragments:
                    if fragment in normalised:
                        scores[family] += 1
                        break  # one match per path per family is enough

        # Action-class boost: governance-related action classes.
        governance_actions = {"approval_resolution", "policy_mutation", "governance_mutation"}
        for ac in action_classes:
            if ac in governance_actions:
                scores[TaskFamily.governance_mutation] += 2

        perf_actions = {"execute_command", "vcs_mutation"}
        for ac in action_classes:
            if ac in perf_actions:
                scores[TaskFamily.runtime_perf] += 1

        best_family = max(scores, key=lambda f: scores[f])
        if scores[best_family] == 0:
            # No signal at all -- default to runtime_perf.
            return TaskFamily.runtime_perf

        return best_family

    # ------------------------------------------------------------------
    # Profile resolution
    # ------------------------------------------------------------------

    def resolve_profile(
        self,
        *,
        task_family: TaskFamily,
        risk_band: str,
        explicit_profile: str | None = None,
    ) -> BenchmarkProfile | None:
        """Resolve the benchmark profile for a task.

        If *explicit_profile* is provided and found in the registry it is
        returned directly regardless of risk band.  Otherwise the registry
        routes based on task_family + risk_band.

        Raises :class:`BenchmarkForbiddenError` when the risk band is ``low``
        (spec: benchmark for low risk is forbidden, not silently skipped).
        Returns ``None`` only when no matching profile exists for the family.
        """
        if explicit_profile:
            profile = self.registry.get_profile(explicit_profile)
            if profile is not None:
                log.debug(
                    "benchmark_routing.explicit_profile_resolved",
                    profile_id=explicit_profile,
                )
                return profile
            log.warning(
                "benchmark_routing.explicit_profile_not_found",
                profile_id=explicit_profile,
            )

        # Propagates BenchmarkForbiddenError for low risk bands.
        profile = self.registry.route_task(task_family, risk_band=risk_band)
        if profile is not None:
            log.debug(
                "benchmark_routing.profile_routed",
                profile_id=profile.profile_id,
                task_family=str(task_family),
                risk_band=risk_band,
            )
        return profile

    # ------------------------------------------------------------------
    # Validation: prevent "跑错 benchmark" and "结果没法比较"
    # ------------------------------------------------------------------

    def validate_profile_family(
        self,
        *,
        profile: BenchmarkProfile,
        task_family: TaskFamily,
    ) -> None:
        """Validate that the benchmark profile matches the task family.

        Spec error #1: "跑错 benchmark" -- running the wrong benchmark for a
        task family.  When an explicit ``benchmark_profile`` is declared in the
        contract, this method ensures it aligns with the classified task family.

        Raises ``ProfileFamilyMismatchError`` on mismatch.
        """
        if profile.task_family != task_family:
            msg = (
                f"Profile '{profile.profile_id}' is for task_family "
                f"'{profile.task_family}' but task was classified as "
                f"'{task_family}'"
            )
            log.error(
                "benchmark_routing.profile_family_mismatch",
                profile_id=profile.profile_id,
                profile_family=str(profile.task_family),
                task_family=str(task_family),
            )
            raise ProfileFamilyMismatchError(msg)

    def validate_baseline_ref(self, *, run: BenchmarkRun) -> None:
        """Validate that the run has a baseline_ref when the profile requires one.

        Spec error #2: "结果没法比较" -- benchmark results cannot be compared
        when a profile declares a baseline_ref but the run does not carry one.

        Raises ``MissingBaselineError`` when baseline_ref is missing but required.
        """
        profile = self.registry.get_profile(run.profile_id)
        if profile is None:
            return
        if profile.baseline_ref is not None and run.baseline_ref is None:
            msg = (
                f"Profile '{profile.profile_id}' requires baseline_ref "
                f"'{profile.baseline_ref}' but the run has no baseline_ref"
            )
            log.error(
                "benchmark_routing.missing_baseline_ref",
                profile_id=profile.profile_id,
                required_baseline=profile.baseline_ref,
            )
            raise MissingBaselineError(msg)

    # ------------------------------------------------------------------
    # Benchmark run lifecycle
    # ------------------------------------------------------------------

    def create_benchmark_run(
        self,
        *,
        profile: BenchmarkProfile,
        task_id: str,
        step_id: str,
        attempt_id: str,
        environment_tag: str | None = None,
        commit_ref: str | None = None,
    ) -> BenchmarkRun:
        """Create a benchmark run record (not yet executed).

        Each run is bound to an *environment_tag* and *commit_ref* per spec so
        that results are reproducible and comparable.
        """
        run_id = f"bench_run_{uuid.uuid4().hex[:12]}"
        run = BenchmarkRun(
            run_id=run_id,
            profile_id=profile.profile_id,
            task_id=task_id,
            step_id=step_id,
            attempt_id=attempt_id,
            baseline_ref=profile.baseline_ref,
            started_at=time.time(),
            environment_tag=environment_tag,
            commit_ref=commit_ref,
        )
        log.info(
            "benchmark_routing.run_created",
            run_id=run_id,
            profile_id=profile.profile_id,
            task_id=task_id,
        )
        return run

    # ------------------------------------------------------------------
    # Threshold evaluation
    # ------------------------------------------------------------------

    def evaluate_thresholds(
        self,
        *,
        run: BenchmarkRun,
        raw_metrics: dict[str, float],
    ) -> BenchmarkVerdict:
        """Compare raw metrics against profile thresholds.

        Returns verdict with pass/fail, regressions, improvements.

        Threshold semantics (from spec):
        - For metrics whose name contains ``latency`` or ``error`` the value
          must be **at most** the threshold (lower is better).
        - For ``regression_count`` the value must be **at most** the threshold.
        - For all other metrics (throughput, accuracy, coverage, etc.) the value
          must be **at least** the threshold (higher is better).
        """
        profile = self.registry.get_profile(run.profile_id)
        thresholds: dict[str, float] = profile.thresholds if profile else {}

        threshold_results: dict[str, bool] = {}
        regressions: list[str] = []
        improvements: list[str] = []

        for metric_name, threshold_value in thresholds.items():
            actual = raw_metrics.get(metric_name)
            if actual is None:
                # Missing metric counts as a regression.
                threshold_results[metric_name] = False
                regressions.append(f"{metric_name}: missing (threshold={threshold_value})")
                continue

            if self._is_lower_better(metric_name):
                passed = actual <= threshold_value
            else:
                passed = actual >= threshold_value

            threshold_results[metric_name] = passed

            if not passed:
                regressions.append(f"{metric_name}: {actual} vs threshold {threshold_value}")
            elif self._is_significant_improvement(actual, threshold_value, metric_name):
                improvements.append(f"{metric_name}: {actual} vs threshold {threshold_value}")

        overall_passed = all(threshold_results.values()) if threshold_results else True

        # Update the run record (immutable copy).
        completed_run = replace(
            run,
            raw_metrics=dict(raw_metrics),
            threshold_results=dict(threshold_results),
            passed=overall_passed,
            completed_at=time.time(),
        )

        verdict_id = f"bench_verdict_{uuid.uuid4().hex[:12]}"
        result_class = (
            BenchmarkResultClass.satisfied if overall_passed else BenchmarkResultClass.violated
        )
        verdict = BenchmarkVerdict(
            verdict_id=verdict_id,
            run_id=completed_run.run_id,
            profile_id=completed_run.profile_id,
            task_id=completed_run.task_id,
            overall_passed=overall_passed,
            benchmark_result_class=result_class,
            regressions=regressions,
            improvements=improvements,
            notes=self._build_verdict_notes(completed_run, regressions, improvements),
        )

        log.info(
            "benchmark_routing.verdict_issued",
            verdict_id=verdict_id,
            run_id=run.run_id,
            overall_passed=overall_passed,
            benchmark_result_class=str(result_class),
            regressions_count=len(regressions),
            improvements_count=len(improvements),
        )
        return verdict

    # ------------------------------------------------------------------
    # Verification requirements inspection
    # ------------------------------------------------------------------

    def should_benchmark(self, verification_requirements: dict | None) -> bool:
        """Check if benchmarking is required based on verification_requirements.

        Returns ``True`` when the requirements contain a meaningful
        ``benchmark_profile`` (not empty or ``"none"``) or when either
        ``governance_bench`` or ``performance_bench`` is set to
        ``"required"`` or ``"optional"``.
        """
        if verification_requirements is None:
            return False
        profile = str(verification_requirements.get("benchmark_profile", "") or "")
        if profile and profile != "none":
            return True
        governance = str(verification_requirements.get("governance_bench", "forbidden"))
        performance = str(verification_requirements.get("performance_bench", "forbidden"))
        return governance in _BENCH_ENABLED_VALUES or performance in _BENCH_ENABLED_VALUES

    def route_from_contract(
        self,
        *,
        task_family: str | None,
        verification_requirements: dict | None,
        risk_level: str = "low",
        action_classes: list[str] | None = None,
        affected_paths: list[str] | None = None,
    ) -> BenchmarkProfile | None:
        """Read verification_requirements from a contract and resolve the profile.

        This is the primary entry point for the routing service when called
        from verification/reconciliation supervisors.  It reads the
        ``benchmark_profile`` and ``task_family`` declared in the contract,
        classifies the task family (using heuristics when not declared),
        and resolves the appropriate benchmark profile.

        Returns ``None`` when benchmarking is not applicable.
        """
        if not self.should_benchmark(verification_requirements):
            return None

        # Determine the task family.
        family = self.classify_task_family(
            action_classes=action_classes or [],
            affected_paths=affected_paths or [],
            task_family_hint=task_family,
        )

        # Check for an explicit profile in the contract.
        explicit_profile: str | None = None
        if verification_requirements is not None:
            raw_profile = str(verification_requirements.get("benchmark_profile", "") or "")
            if raw_profile and raw_profile != "none":
                explicit_profile = raw_profile

        return self.resolve_profile(
            task_family=family,
            risk_band=risk_level,
            explicit_profile=explicit_profile,
        )

    # ------------------------------------------------------------------
    # Reconciliation integration
    # ------------------------------------------------------------------

    def format_verdict_for_reconciliation(self, verdict: BenchmarkVerdict) -> dict:
        """Format benchmark verdict as input for reconciliation.

        The returned dict is intended to be merged into the
        ``observables`` or ``witness`` payload consumed by the
        reconciliation executor.

        Spec constraint: "benchmark verdict 不能直接进 memory" -- verdicts
        must flow through reconciliation, never directly into memory.
        The ``reconciliation_only`` flag is set to enforce this at the
        consumer level.
        """
        return {
            "benchmark_verdict_id": verdict.verdict_id,
            "benchmark_run_id": verdict.run_id,
            "benchmark_profile_id": verdict.profile_id,
            "benchmark_task_id": verdict.task_id,
            "benchmark_passed": verdict.overall_passed,
            "benchmark_regressions": list(verdict.regressions),
            "benchmark_improvements": list(verdict.improvements),
            "benchmark_notes": verdict.notes,
            "benchmark_result_class": str(verdict.benchmark_result_class),
            "reconciliation_only": True,
        }

    # ------------------------------------------------------------------
    # Verdict consumption tracking
    # ------------------------------------------------------------------

    def mark_verdict_consumed(
        self, verdict: BenchmarkVerdict, *, consumed_by: str
    ) -> BenchmarkVerdict:
        """Mark a verdict as consumed by reconciliation.

        Returns new verdict with consumed=True.
        Raises if verdict was already consumed (idempotency guard).
        """
        if verdict.consumed:
            msg = f"Verdict '{verdict.verdict_id}' was already consumed by '{verdict.consumed_by}'"
            log.error(
                "benchmark_routing.verdict_already_consumed",
                verdict_id=verdict.verdict_id,
                consumed_by=verdict.consumed_by,
                attempted_by=consumed_by,
            )
            raise VerdictAlreadyConsumedError(msg)

        consumed_verdict = replace(verdict, consumed=True, consumed_by=consumed_by)
        log.info(
            "benchmark_routing.verdict_consumed",
            verdict_id=verdict.verdict_id,
            consumed_by=consumed_by,
        )
        return consumed_verdict

    @staticmethod
    def require_verdict_consumption(verdict: BenchmarkVerdict) -> None:
        """Raise if a failed verdict has not been consumed.

        Security invariant: failed verdicts (overall_passed=False) must be
        explicitly consumed by reconciliation. This prevents failed benchmark
        results from being silently ignored in the pipeline.

        Passing verdicts are allowed to remain unconsumed (no enforcement
        needed for successful results).
        """
        if not verdict.overall_passed and not verdict.consumed:
            msg = (
                f"Failed verdict '{verdict.verdict_id}' has not been consumed "
                f"by reconciliation — failed benchmark results must not be ignored"
            )
            raise VerdictNotConsumedError(msg)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_lower_better(metric_name: str) -> bool:
        """Return True for metrics where a lower value is better.

        Spec: "latency/error = lower is better"
        """
        name_lower = metric_name.lower()
        return any(kw in name_lower for kw in _LOWER_BETTER_KEYWORDS)

    @staticmethod
    def _is_significant_improvement(
        actual: float,
        threshold: float,
        metric_name: str,
    ) -> bool:
        """Return True when the actual value significantly exceeds the threshold."""
        if threshold == 0.0:
            return actual != 0.0
        ratio = actual / threshold
        # For lower-is-better metrics an improvement means actual << threshold.
        name_lower = metric_name.lower()
        if any(kw in name_lower for kw in _LOWER_BETTER_KEYWORDS):
            return ratio <= 0.8  # 20 % improvement
        return ratio >= 1.2  # 20 % improvement

    @staticmethod
    def _build_verdict_notes(
        run: BenchmarkRun,
        regressions: list[str],
        improvements: list[str],
    ) -> str:
        """Build a human-readable summary for the verdict."""
        parts: list[str] = [f"Profile: {run.profile_id}"]
        if run.passed:
            parts.append("All thresholds passed.")
        else:
            parts.append(f"{len(regressions)} regression(s) detected.")
        if improvements:
            parts.append(f"{len(improvements)} significant improvement(s).")
        return " ".join(parts)
