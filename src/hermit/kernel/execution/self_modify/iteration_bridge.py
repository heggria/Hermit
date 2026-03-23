"""Iteration Bridge — connects plugin-level metaloop with kernel-level IterationKernel.

Self-iteration is NOT a second runtime. It is a Meta-Program running on the same
Control Plane infrastructure. The metaloop plugin (hooks/metaloop/) implements the
9-phase pipeline; the IterationKernel provides the formal state machine. This
bridge synchronizes them so metaloop phases map to IterationKernel states.

Metaloop IterationPhase -> kernel IterationState mapping:
    PENDING         -> draft
    RESEARCHING     -> researching
    GENERATING_SPEC -> specifying
    SPEC_APPROVAL   -> specifying  (sub-state)
    DECOMPOSING     -> specifying  (sub-state)
    IMPLEMENTING    -> executing
    REVIEWING       -> verifying
    BENCHMARKING    -> verifying   (sub-state)
    LEARNING        -> reconciling
    COMPLETED       -> accepted
    FAILED          -> rejected

5-Lane artifact tracking (spec §self-loop):
    Lane A (spec_goal):     iteration_spec, milestone_graph, phase_contracts
    Lane B (research):      research_report, repo_diagnosis, evidence_bundle
    Lane C (change):        diff_bundle, test_patch, migration_notes
    Lane D (verification):  benchmark_run, replay_result, verification_verdict
    Lane E (reconcile):     reconciliation_record, lesson_pack, template_update,
                            next_iteration_seed

Only reconciled outcomes (lane E complete + promotion gate passed) can drive
template promotion, policy relaxation, or durable memory updates.

The bridge provides kernel-governed hooks that the metaloop can call at phase
transitions to keep the IterationKernel state machine synchronized without
modifying the plugin code itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

import structlog

from hermit.kernel.execution.self_modify.iteration_kernel import (
    InvalidTransitionError,
    IterationKernel,
    IterationLessonPack,
    IterationSpec,
    IterationState,
)
from hermit.kernel.execution.self_modify.iteration_proof import (
    build_iteration_proof,
    export_iteration_proof,
)

from ._metadata_utils import parse_metadata

if TYPE_CHECKING:
    from hermit.kernel.execution.self_modify.workspace import SelfModifyWorkspace

__all__ = [
    "LANE_EXPECTED_ARTIFACTS",
    "BridgeVerdict",
    "IterationBridge",
    "Lane",
    "LaneArtifact",
    "LaneTracker",
]

logger = structlog.get_logger()


def _now_ts() -> float:
    return datetime.now(UTC).timestamp()


def _default_metadata() -> dict[str, Any]:
    return {}


def _default_benchmark_results() -> dict[str, Any]:
    return {}


def _default_lane_artifacts() -> dict[str, list[dict[str, Any]]]:
    return {}


# ---------------------------------------------------------------------------
# Lane definitions (spec §self-loop, 5 lanes)
# ---------------------------------------------------------------------------


class Lane(StrEnum):
    """The 5 self-iteration lanes from the spec.

    Lane A: Spec/Goal — produces iteration_spec, milestone_graph, phase_contracts
    Lane B: Research/Evidence — produces research_report, repo_diagnosis, evidence_bundle
    Lane C: Change — produces diff_bundle, test_patch, migration_notes
    Lane D: Verification/Benchmark — produces benchmark_run, replay_result,
            verification_verdict
    Lane E: Reconcile/Learn — produces reconciliation_record, lesson_pack,
            template_update, next_iteration_seed
    """

    spec_goal = "spec_goal"
    research = "research"
    change = "change"
    verification = "verification"
    reconcile = "reconcile"


# Expected artifact types per lane (from spec).
LANE_EXPECTED_ARTIFACTS: dict[Lane, frozenset[str]] = {
    Lane.spec_goal: frozenset({"iteration_spec", "milestone_graph", "phase_contracts"}),
    Lane.research: frozenset({"research_report", "repo_diagnosis", "evidence_bundle"}),
    Lane.change: frozenset({"diff_bundle", "test_patch", "migration_notes"}),
    Lane.verification: frozenset({"benchmark_run", "replay_result", "verification_verdict"}),
    Lane.reconcile: frozenset(
        {
            "reconciliation_record",
            "lesson_pack",
            "template_update",
            "next_iteration_seed",
        }
    ),
}


@dataclass(frozen=True)
class LaneArtifact:
    """A single artifact produced by a lane during an iteration."""

    lane: Lane
    artifact_type: str
    artifact_ref: str  # reference (path, ID, or inline summary)
    produced_at: float = field(default_factory=_now_ts)
    metadata: dict[str, Any] = field(default_factory=_default_metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value,
            "artifact_type": self.artifact_type,
            "artifact_ref": self.artifact_ref,
            "produced_at": self.produced_at,
            "metadata": self.metadata,
        }


# Maps metaloop phases to the lane they belong to.
_PHASE_TO_LANE: dict[str, Lane] = {
    "pending": Lane.spec_goal,
    "generating_spec": Lane.spec_goal,
    "spec_approval": Lane.spec_goal,
    "decomposing": Lane.spec_goal,
    "researching": Lane.research,
    "implementing": Lane.change,
    "reviewing": Lane.verification,
    "benchmarking": Lane.verification,
    "learning": Lane.reconcile,
    "completed": Lane.reconcile,
    "failed": Lane.reconcile,
}


class LaneTracker:
    """Tracks lane artifacts per iteration.

    Thread-safe for single-writer scenarios (which is the norm for
    iteration pipelines). Stores artifacts in-memory, keyed by
    iteration_id -> lane -> list[LaneArtifact].
    """

    def __init__(self) -> None:
        self._artifacts: dict[str, dict[Lane, list[LaneArtifact]]] = {}

    def record(
        self,
        iteration_id: str,
        artifact: LaneArtifact,
    ) -> None:
        """Record an artifact for an iteration's lane."""
        by_lane = self._artifacts.setdefault(iteration_id, {})
        by_lane.setdefault(artifact.lane, []).append(artifact)

    def get_lane_artifacts(
        self,
        iteration_id: str,
        lane: Lane | None = None,
    ) -> list[LaneArtifact]:
        """Return artifacts for an iteration, optionally filtered by lane."""
        by_lane = self._artifacts.get(iteration_id, {})
        if lane is not None:
            return list(by_lane.get(lane, []))
        result: list[LaneArtifact] = []
        for artifacts in by_lane.values():
            result.extend(artifacts)
        return result

    def get_all_lanes(self, iteration_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return all artifacts grouped by lane as plain dicts."""
        by_lane = self._artifacts.get(iteration_id, {})
        return {
            lane.value: [a.to_dict() for a in artifacts]
            for lane, artifacts in by_lane.items()
        }

    def missing_artifacts(
        self,
        iteration_id: str,
        lane: Lane,
    ) -> frozenset[str]:
        """Return expected artifact types not yet produced for a lane."""
        expected = LANE_EXPECTED_ARTIFACTS.get(lane, frozenset())
        produced = {a.artifact_type for a in self.get_lane_artifacts(iteration_id, lane)}
        return expected - produced

    def lane_complete(self, iteration_id: str, lane: Lane) -> bool:
        """Return True if every expected artifact type for the lane has been produced."""
        return len(self.missing_artifacts(iteration_id, lane)) == 0

    def all_lanes_complete(self, iteration_id: str) -> bool:
        """Return True if every lane has all expected artifacts."""
        return all(self.lane_complete(iteration_id, lane) for lane in Lane)

    def summary(self, iteration_id: str) -> dict[str, dict[str, Any]]:
        """Return a per-lane summary: produced types, missing types, complete flag."""
        result: dict[str, dict[str, Any]] = {}
        for lane in Lane:
            produced = {a.artifact_type for a in self.get_lane_artifacts(iteration_id, lane)}
            missing = self.missing_artifacts(iteration_id, lane)
            result[lane.value] = {
                "produced": sorted(produced),
                "missing": sorted(missing),
                "complete": len(missing) == 0,
            }
        return result


# ---------------------------------------------------------------------------
# Phase-to-state mapping
# ---------------------------------------------------------------------------

# Maps metaloop IterationPhase string values to kernel IterationState values.
# Multiple plugin phases can map to a single kernel state (e.g. GENERATING_SPEC,
# SPEC_APPROVAL, and DECOMPOSING all map to 'specifying').
_PHASE_TO_STATE: dict[str, str] = {
    "pending": IterationState.draft.value,
    "researching": IterationState.researching.value,
    "generating_spec": IterationState.specifying.value,
    "spec_approval": IterationState.specifying.value,
    "decomposing": IterationState.specifying.value,
    "implementing": IterationState.executing.value,
    "reviewing": IterationState.verifying.value,
    "benchmarking": IterationState.verifying.value,
    "learning": IterationState.reconciling.value,
    "completed": IterationState.accepted.value,
    "failed": IterationState.rejected.value,
}


# ---------------------------------------------------------------------------
# Bridge result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BridgeVerdict:
    """Outcome returned by on_iteration_complete."""

    iteration_id: str
    result: str  # "accepted" | "rejected" | "accepted_with_followups"
    promoted: bool
    reconciliation_summary: str = ""
    lesson_pack: IterationLessonPack | None = None
    next_seed_goal: str | None = None
    benchmark_results: dict[str, Any] = field(default_factory=_default_benchmark_results)
    lane_artifacts: dict[str, list[dict[str, Any]]] = field(
        default_factory=_default_lane_artifacts
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for MCP / plugin consumption."""
        d: dict[str, Any] = {
            "iteration_id": self.iteration_id,
            "result": self.result,
            "promoted": self.promoted,
            "benchmark_results": self.benchmark_results,
            "reconciliation_summary": self.reconciliation_summary,
            "next_seed_goal": self.next_seed_goal,
            "lane_artifacts": self.lane_artifacts,
        }
        if self.lesson_pack is not None:
            d["lesson_pack"] = asdict(self.lesson_pack)
        return d


# ---------------------------------------------------------------------------
# IterationBridge
# ---------------------------------------------------------------------------


class IterationBridge:
    """Bridges plugin-level metaloop phases with kernel-level IterationKernel.

    Maps metaloop IterationPhase -> kernel IterationState:
    - PENDING -> draft
    - RESEARCHING -> researching
    - GENERATING_SPEC -> specifying
    - SPEC_APPROVAL -> specifying (sub-state)
    - DECOMPOSING -> specifying
    - IMPLEMENTING -> executing
    - REVIEWING -> verifying
    - BENCHMARKING -> verifying
    - LEARNING -> reconciling
    - COMPLETED -> accepted
    - FAILED -> rejected

    Tracks 5-lane artifact production (spec §self-loop):
    - Lane A (spec_goal): iteration_spec, milestone_graph, phase_contracts
    - Lane B (research): research_report, repo_diagnosis, evidence_bundle
    - Lane C (change): diff_bundle, test_patch, migration_notes
    - Lane D (verification): benchmark_run, replay_result, verification_verdict
    - Lane E (reconcile): reconciliation_record, lesson_pack, template_update,
                          next_iteration_seed

    Provides kernel-governed hooks that the metaloop can call at phase transitions
    to ensure the IterationKernel state machine stays synchronized.
    """

    def __init__(self, store: object) -> None:
        self._store: Any = store
        self._kernel: IterationKernel = IterationKernel(store)
        self._lane_tracker = LaneTracker()

    @property
    def kernel(self) -> IterationKernel:
        """Expose the underlying kernel for direct queries when needed."""
        return self._kernel

    @property
    def lane_tracker(self) -> LaneTracker:
        """Expose the lane tracker for direct queries when needed."""
        return self._lane_tracker

    # ------------------------------------------------------------------
    # Iteration lifecycle
    # ------------------------------------------------------------------

    def on_iteration_start(
        self,
        *,
        spec_id: str,
        goal: str,
        constraints: list[str] | None = None,
    ) -> str:
        """Called when metaloop starts a new iteration.

        Creates an IterationSpec and admits it through the kernel.
        Returns the iteration_id assigned by the kernel.

        Raises ValueError if goal is empty.
        """
        spec = IterationSpec(
            spec_id=spec_id,
            goal=goal,
            constraints=constraints or [],
        )
        iteration_id = self._kernel.admit_iteration(  # pyright: ignore[reportUnknownMemberType]
            spec
        )

        logger.info(
            "iteration_bridge.started",
            iteration_id=iteration_id,
            spec_id=spec_id,
            goal=goal[:80],
        )
        return iteration_id

    # ------------------------------------------------------------------
    # Phase transitions
    # ------------------------------------------------------------------

    def on_phase_transition(
        self,
        *,
        iteration_id: str,
        from_phase: str,
        to_phase: str,
    ) -> bool:
        """Called by metaloop at each phase transition.

        Maps the plugin-level phase to a kernel state and validates the
        transition through the IterationKernel state machine.

        Returns True if the kernel transition succeeded or was unnecessary
        (when two adjacent plugin phases map to the same kernel state).
        Returns False if the kernel transition was rejected by the state machine.
        """
        from_state_str = self.map_phase_to_state(from_phase)
        to_state_str = self.map_phase_to_state(to_phase)

        # When adjacent plugin phases map to the same kernel state
        # (e.g. GENERATING_SPEC -> SPEC_APPROVAL, both 'specifying'),
        # no kernel transition is needed.
        if from_state_str == to_state_str:
            logger.debug(
                "iteration_bridge.same_state_skip",
                iteration_id=iteration_id,
                from_phase=from_phase,
                to_phase=to_phase,
                kernel_state=to_state_str,
            )
            return True

        target_state = IterationState(to_state_str)

        # Check whether the kernel is already at the target state.
        # This makes the bridge idempotent for repeated calls.
        try:
            current_kernel_state = self._kernel.get_state(iteration_id)
        except KeyError:
            logger.warning(
                "iteration_bridge.iteration_not_found",
                iteration_id=iteration_id,
            )
            return False

        if current_kernel_state == target_state:
            logger.debug(
                "iteration_bridge.already_at_state",
                iteration_id=iteration_id,
                state=target_state.value,
            )
            return True

        try:
            result = self._kernel.transition(iteration_id, target_state)
        except InvalidTransitionError:
            logger.warning(
                "iteration_bridge.invalid_transition",
                iteration_id=iteration_id,
                from_phase=from_phase,
                to_phase=to_phase,
                current_kernel_state=current_kernel_state.value,
                target_kernel_state=target_state.value,
            )
            return False
        except KeyError:
            logger.warning(
                "iteration_bridge.iteration_not_found",
                iteration_id=iteration_id,
            )
            return False

        if result:
            logger.info(
                "iteration_bridge.transitioned",
                iteration_id=iteration_id,
                from_phase=from_phase,
                to_phase=to_phase,
                kernel_state=target_state.value,
            )

        return result

    # ------------------------------------------------------------------
    # Lane artifact tracking
    # ------------------------------------------------------------------

    def record_lane_artifact(
        self,
        *,
        iteration_id: str,
        lane: Lane | str,
        artifact_type: str,
        artifact_ref: str,
        metadata: dict[str, Any] | None = None,
    ) -> LaneArtifact:
        """Record a lane artifact produced during an iteration phase.

        Validates that the artifact_type is expected for the given lane.
        Returns the recorded LaneArtifact.

        Raises ValueError if the lane string is unrecognized or the
        artifact_type is not in the lane's expected set.
        """
        lane = Lane(lane)

        expected = LANE_EXPECTED_ARTIFACTS.get(lane, frozenset())
        if artifact_type not in expected:
            raise ValueError(
                f"Unexpected artifact_type {artifact_type!r} for lane {lane.value}; "
                f"expected one of {sorted(expected)}"
            )

        artifact = LaneArtifact(
            lane=lane,
            artifact_type=artifact_type,
            artifact_ref=artifact_ref,
            metadata=metadata or {},
        )
        self._lane_tracker.record(iteration_id, artifact)

        logger.info(
            "iteration_bridge.lane_artifact_recorded",
            iteration_id=iteration_id,
            lane=lane.value,
            artifact_type=artifact_type,
        )
        return artifact

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    def on_iteration_complete(
        self,
        *,
        iteration_id: str,
        benchmark_results: dict[str, Any] | None = None,
        reconciliation_summary: str = "",
        replay_stable: bool = False,
        unexplained_drift: list[str] | None = None,
    ) -> dict[str, Any]:
        """Called when metaloop reaches COMPLETED or FAILED.

        Stores benchmark results, reconciliation summary, replay stability,
        and unexplained drift in metadata, checks the promotion gate,
        extracts lessons if promoted, and transitions to the terminal state.

        Promotion requires ALL of (per spec — Iteration Promotion Gate):
        - benchmark_results non-empty (benchmark passes)
        - reconciliation_summary non-empty (reconcile satisfied)
        - replay_stable=True (replay stability verified)
        - unexplained_drift empty or absent (no high-risk unexplained drift)

        Returns a verdict dict with the outcome including lane artifact summary.
        """
        benchmark: dict[str, Any] = benchmark_results or {}
        drift: list[str] = unexplained_drift or []

        # Inject all gate-relevant fields into metadata
        # so check_promotion_gate can inspect them.
        self._inject_metadata(
            iteration_id,
            benchmark_results=benchmark,
            reconciliation_summary=reconciliation_summary,
            replay_stable=replay_stable,
            unexplained_drift=drift,
        )

        promoted = self.check_promotion_gate(iteration_id)

        lesson_pack: IterationLessonPack | None = None
        next_seed_goal: str | None = None

        if promoted:
            # Extract lessons — only reconciled outcomes become durable learning.
            lesson_pack = self.extract_lessons(iteration_id)

            # Transition to accepted.
            try:
                self._kernel.transition(iteration_id, IterationState.accepted)
            except (InvalidTransitionError, KeyError):
                logger.warning(
                    "iteration_bridge.accept_transition_failed",
                    iteration_id=iteration_id,
                )

            # Check for a follow-up seed.
            seed = self._kernel.generate_next_seed(iteration_id)
            if seed is not None:
                next_seed_goal = seed.goal

            result_str = "accepted_with_followups" if next_seed_goal else "accepted"
        else:
            # Not promoted — reject.
            try:
                self._kernel.transition(iteration_id, IterationState.rejected)
            except (InvalidTransitionError, KeyError):
                logger.warning(
                    "iteration_bridge.reject_transition_failed",
                    iteration_id=iteration_id,
                )
            result_str = "rejected"

        # Collect lane artifact summary for the verdict.
        lane_artifacts = self._lane_tracker.get_all_lanes(iteration_id)

        # Build and export iteration proof bundle.
        proof_hash = self._build_and_export_proof(
            iteration_id=iteration_id,
            result=result_str,
            lane_artifacts=lane_artifacts,
        )

        verdict = BridgeVerdict(
            iteration_id=iteration_id,
            result=result_str,
            promoted=promoted,
            benchmark_results=benchmark,
            reconciliation_summary=reconciliation_summary,
            lesson_pack=lesson_pack,
            next_seed_goal=next_seed_goal,
            lane_artifacts=lane_artifacts,
        )

        verdict_dict = verdict.to_dict()
        if proof_hash:
            verdict_dict["proof_hash"] = proof_hash

        logger.info(
            "iteration_bridge.completed",
            iteration_id=iteration_id,
            result=result_str,
            promoted=promoted,
            proof_hash=proof_hash[:16] if proof_hash else None,
        )

        return verdict_dict

    # ------------------------------------------------------------------
    # PR creation (replaces direct merge to main)
    # ------------------------------------------------------------------

    def create_iteration_pr(
        self,
        *,
        iteration_id: str,
        workspace: SelfModifyWorkspace | None = None,
        iteration_summary: str = "",
        benchmark_results: dict[str, Any] | None = None,
        lessons: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a PR branch for an accepted iteration instead of merging.

        Transitions the kernel state from accepted -> pr_created and
        stores PR metadata in the spec backlog.

        The iteration must be in 'accepted' state (promotion gate passed).
        Does NOT merge — waits for explicit approval via approve_iteration_merge().

        Args:
            iteration_id: The iteration to create a PR for.
            workspace: A SelfModifyWorkspace instance (or None for metadata-only).
            iteration_summary: Human-readable summary for the PR body.
            benchmark_results: Benchmark data to include in the PR body.
            lessons: Lessons learned to include in the PR body.

        Returns a dict with PR info (branch_name, title, body, pr_url, pushed).
        """
        # Verify current state is accepted
        try:
            current_state = self._kernel.get_state(iteration_id)
        except KeyError:
            logger.warning(
                "iteration_bridge.pr.iteration_not_found",
                iteration_id=iteration_id,
            )
            return {"error": f"Iteration not found: {iteration_id}"}

        if current_state != IterationState.accepted:
            logger.warning(
                "iteration_bridge.pr.wrong_state",
                iteration_id=iteration_id,
                current_state=current_state.value,
            )
            return {
                "error": (
                    f"Cannot create PR: iteration is in '{current_state.value}' state, "
                    f"expected 'accepted'"
                ),
            }

        pr_branch = f"iteration/{iteration_id}"
        title = f"iteration: {iteration_id}"

        # Build PR body
        body_parts = [f"## Self-Iteration: {iteration_id}"]
        if iteration_summary:
            body_parts.append(f"\n### Summary\n{iteration_summary}")
        if benchmark_results:
            body_parts.append(f"\n### Benchmark Results\n```json\n{benchmark_results}\n```")
        if lessons:
            lesson_text = "\n".join(f"- {ls}" for ls in lessons)
            body_parts.append(f"\n### Lessons Learned\n{lesson_text}")
        body_parts.append(
            "\n---\n*This PR was created automatically by Hermit's "
            "self-iteration pipeline. It requires human review before merging.*"
        )
        body = "\n".join(body_parts)

        pr_info: dict[str, Any] = {
            "iteration_id": iteration_id,
            "branch_name": pr_branch,
            "title": title,
            "body": body,
            "pr_url": None,
            "pushed": False,
        }

        # If a workspace is provided, do the actual git operations
        if workspace is not None:
            try:
                from hermit.kernel.execution.self_modify.merger import WorktreeMerger

                merger = WorktreeMerger(workspace)
                from hermit.kernel.execution.self_modify.models import (
                    SelfModPhase,
                    SelfModSession,
                )

                session = SelfModSession(
                    iteration_id=iteration_id,
                    phase=SelfModPhase.MERGING,
                )
                import asyncio

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop is not None and loop.is_running():
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        _, merger_pr_info = pool.submit(
                            asyncio.run,
                            merger.create_pr(  # pyright: ignore[reportUnknownMemberType]
                                session,
                                iteration_summary=iteration_summary,
                                benchmark_results=benchmark_results,
                                lessons=lessons,
                            ),
                        ).result()  # pyright: ignore[reportUnknownMemberType]
                else:
                    _, merger_pr_info = asyncio.run(
                        merger.create_pr(  # pyright: ignore[reportUnknownMemberType]
                            session,
                            iteration_summary=iteration_summary,
                            benchmark_results=benchmark_results,
                            lessons=lessons,
                        )
                    )
                pr_info = cast(
                    dict[str, Any],
                    cast(Any, merger_pr_info).to_dict(),
                )
            except Exception:
                logger.warning(
                    "iteration_bridge.pr.git_operations_failed",
                    iteration_id=iteration_id,
                    exc_info=True,
                )
                # Continue with metadata-only PR info

        # Transition: accepted -> pr_created
        try:
            self._kernel.transition(iteration_id, IterationState.pr_created)
        except (InvalidTransitionError, KeyError):
            logger.warning(
                "iteration_bridge.pr.transition_failed",
                iteration_id=iteration_id,
            )

        # Store PR info in spec metadata
        self._inject_pr_metadata(iteration_id, pr_info)

        logger.info(
            "iteration_bridge.pr.created",
            iteration_id=iteration_id,
            branch=pr_branch,
            pr_url=pr_info.get("pr_url"),
        )

        return pr_info

    def approve_iteration_merge(
        self,
        *,
        iteration_id: str,
        workspace: SelfModifyWorkspace | None = None,
    ) -> dict[str, Any]:
        """Approve and merge an iteration that has a PR.

        Checks that the iteration is in 'pr_created' state, checks that
        the benchmark passed (from stored metadata), performs the merge,
        and triggers a graceful reload via SIGHUP (NOT os.execv).

        Args:
            iteration_id: The iteration to approve.
            workspace: A SelfModifyWorkspace instance (required for actual merge).

        Returns a dict with merge result info.
        """
        # Verify current state is pr_created
        try:
            current_state = self._kernel.get_state(iteration_id)
        except KeyError:
            return {"error": f"Iteration not found: {iteration_id}"}

        if current_state != IterationState.pr_created:
            return {
                "error": (
                    f"Cannot approve merge: iteration is in '{current_state.value}' state, "
                    f"expected 'pr_created'"
                ),
            }

        # Verify benchmark passed (from stored metadata)
        entry = self._kernel.find_entry(iteration_id)
        if entry is not None:
            raw_meta = entry.get("metadata")
            meta = parse_metadata(raw_meta)
            benchmark = meta.get("benchmark_results", {})
            if not isinstance(benchmark, dict):
                return {"error": "Cannot approve: no benchmark results found"}
            if not benchmark:
                return {"error": "Cannot approve: no benchmark results found"}

        commit_sha = ""

        # Perform the actual merge if workspace is provided
        if workspace is not None:
            try:
                from hermit.kernel.execution.self_modify.merger import WorktreeMerger

                merger = WorktreeMerger(workspace)

                import asyncio

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop is not None and loop.is_running():
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        commit_sha = pool.submit(
                            asyncio.run,
                            merger.merge_approved(iteration_id),
                        ).result()
                else:
                    commit_sha = asyncio.run(merger.merge_approved(iteration_id))
            except Exception as exc:
                logger.error(
                    "iteration_bridge.merge.failed",
                    iteration_id=iteration_id,
                    exc_info=True,
                )
                return {"error": f"Merge failed: {exc}"}

        # Transition: pr_created -> merge_approved
        try:
            self._kernel.transition(iteration_id, IterationState.merge_approved)
        except (InvalidTransitionError, KeyError):
            logger.warning(
                "iteration_bridge.merge.transition_failed",
                iteration_id=iteration_id,
            )

        logger.info(
            "iteration_bridge.merge.approved",
            iteration_id=iteration_id,
            commit_sha=commit_sha,
        )

        return {
            "iteration_id": iteration_id,
            "merged": True,
            "commit_sha": commit_sha,
            "state": "merge_approved",
        }

    # ------------------------------------------------------------------
    # Delegation methods
    # ------------------------------------------------------------------

    def check_promotion_gate(self, iteration_id: str) -> bool:
        """Delegates to IterationKernel.check_promotion_gate().

        Returns True if the iteration passes the promotion gate:
        - Current state must be 'reconciling'.
        - Metadata must contain benchmark_results with at least one entry.
        - Metadata must contain a non-empty reconciliation_summary.
        - Metadata must have replay_stable=True.
        - Metadata must have no unexplained_drift entries.
        """
        return self._kernel.check_promotion_gate(iteration_id)

    def extract_lessons(self, iteration_id: str) -> IterationLessonPack:
        """Delegates to IterationKernel.extract_lessons().

        Returns an IterationLessonPack with categorized lessons:
        - playbook_updates: process/workflow lessons
        - template_updates: scaffold/boilerplate lessons
        - pattern_updates: architecture/design lessons
        """
        return self._kernel.extract_lessons(iteration_id)

    def get_kernel_state(self, iteration_id: str) -> str:
        """Return the current kernel state as a string.

        Raises KeyError if the iteration is not found.
        """
        return self._kernel.get_state(iteration_id).value

    # ------------------------------------------------------------------
    # Static mapping
    # ------------------------------------------------------------------

    @staticmethod
    def map_phase_to_state(phase: str) -> str:
        """Maps metaloop IterationPhase string to IterationState string.

        Returns the corresponding kernel state value. Raises ValueError
        if the phase is not recognized.
        """
        state = _PHASE_TO_STATE.get(phase)
        if state is None:
            raise ValueError(f"Unknown metaloop phase: {phase!r}")
        return state

    @staticmethod
    def map_phase_to_lane(phase: str) -> Lane:
        """Maps metaloop IterationPhase string to its Lane.

        Returns the corresponding Lane. Raises ValueError if the phase
        is not recognized.
        """
        lane = _PHASE_TO_LANE.get(phase)
        if lane is None:
            raise ValueError(f"Unknown metaloop phase: {phase!r}")
        return lane

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_and_export_proof(
        self,
        *,
        iteration_id: str,
        result: str,
        lane_artifacts: dict[str, list[dict[str, Any]]],
    ) -> str:
        """Build, export, and store an iteration proof bundle.

        Returns the chain_hash on success, empty string on failure.
        """
        try:
            proof = build_iteration_proof(
                self._store,
                iteration_id,
                result=result,
                lane_artifacts=lane_artifacts,
            )
            export_iteration_proof(proof)
            self._store_proof_hash(iteration_id, proof.chain_hash)
            return proof.chain_hash
        except Exception:
            logger.warning(
                "iteration_bridge.proof_export_failed",
                iteration_id=iteration_id,
                exc_info=True,
            )
            return ""

    def _store_proof_hash(self, iteration_id: str, chain_hash: str) -> None:
        """Store the proof chain_hash in the spec_backlog metadata."""
        entry = self._kernel.find_entry(iteration_id)
        if entry is None:
            return

        spec_id = entry["spec_id"]
        raw_meta = entry.get("metadata")
        meta = parse_metadata(raw_meta)

        meta["proof_chain_hash"] = chain_hash

        self._store.update_spec_status(  # type: ignore[union-attr]
            spec_id,
            entry.get("status", "accepted"),
            metadata=meta,
        )

    def _inject_metadata(
        self,
        iteration_id: str,
        *,
        benchmark_results: dict[str, Any],
        reconciliation_summary: str,
        replay_stable: bool = False,
        unexplained_drift: list[str] | None = None,
    ) -> None:
        """Merge promotion-gate fields into the spec metadata."""
        entry = self._kernel.find_entry(iteration_id)
        if entry is None:
            return

        spec_id = entry["spec_id"]
        raw_meta = entry.get("metadata")
        meta = parse_metadata(raw_meta)

        meta["benchmark_results"] = benchmark_results
        meta["reconciliation_summary"] = reconciliation_summary
        meta["replay_stable"] = replay_stable
        if unexplained_drift is not None:
            meta["unexplained_drift"] = unexplained_drift

        self._store.update_spec_status(  # type: ignore[union-attr]
            spec_id,
            entry.get("status", "reconciling"),
            metadata=meta,
        )

    def _inject_pr_metadata(self, iteration_id: str, pr_info: dict[str, Any]) -> None:
        """Store PR info in the spec_backlog metadata."""
        entry = self._kernel.find_entry(iteration_id)
        if entry is None:
            return

        spec_id = entry["spec_id"]
        raw_meta = entry.get("metadata")
        meta = parse_metadata(raw_meta)

        meta["pr_info"] = pr_info

        self._store.update_spec_status(  # type: ignore[union-attr]
            spec_id,
            entry.get("status", "pr_created"),
            metadata=meta,
        )
