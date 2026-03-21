"""Tests for IterationBridge — metaloop-to-kernel synchronization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermit.kernel.execution.self_modify.iteration_bridge import (
    _PHASE_TO_LANE,
    _PHASE_TO_STATE,
    LANE_EXPECTED_ARTIFACTS,
    BridgeVerdict,
    IterationBridge,
    Lane,
    LaneArtifact,
    LaneTracker,
)
from hermit.kernel.execution.self_modify.iteration_kernel import (
    IterationKernel,
    IterationLessonPack,
)
from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture()
def bridge(store: KernelStore) -> IterationBridge:
    return IterationBridge(store)


# ------------------------------------------------------------------
# map_phase_to_state — static mapping
# ------------------------------------------------------------------


class TestMapPhaseToState:
    def test_pending_maps_to_draft(self) -> None:
        assert IterationBridge.map_phase_to_state("pending") == "draft"

    def test_researching_maps_to_researching(self) -> None:
        assert IterationBridge.map_phase_to_state("researching") == "researching"

    def test_generating_spec_maps_to_specifying(self) -> None:
        assert IterationBridge.map_phase_to_state("generating_spec") == "specifying"

    def test_spec_approval_maps_to_specifying(self) -> None:
        assert IterationBridge.map_phase_to_state("spec_approval") == "specifying"

    def test_decomposing_maps_to_specifying(self) -> None:
        assert IterationBridge.map_phase_to_state("decomposing") == "specifying"

    def test_implementing_maps_to_executing(self) -> None:
        assert IterationBridge.map_phase_to_state("implementing") == "executing"

    def test_reviewing_maps_to_verifying(self) -> None:
        assert IterationBridge.map_phase_to_state("reviewing") == "verifying"

    def test_benchmarking_maps_to_verifying(self) -> None:
        assert IterationBridge.map_phase_to_state("benchmarking") == "verifying"

    def test_learning_maps_to_reconciling(self) -> None:
        assert IterationBridge.map_phase_to_state("learning") == "reconciling"

    def test_completed_maps_to_accepted(self) -> None:
        assert IterationBridge.map_phase_to_state("completed") == "accepted"

    def test_failed_maps_to_rejected(self) -> None:
        assert IterationBridge.map_phase_to_state("failed") == "rejected"

    def test_unknown_phase_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown metaloop phase"):
            IterationBridge.map_phase_to_state("nonexistent_phase")

    def test_all_expected_phases_are_mapped(self) -> None:
        """Every metaloop phase string must have a mapping."""
        expected_phases = {
            "pending",
            "researching",
            "generating_spec",
            "spec_approval",
            "decomposing",
            "implementing",
            "reviewing",
            "benchmarking",
            "learning",
            "completed",
            "failed",
        }
        assert set(_PHASE_TO_STATE.keys()) == expected_phases


# ------------------------------------------------------------------
# on_iteration_start
# ------------------------------------------------------------------


class TestOnIterationStart:
    def test_returns_iteration_id(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-start-001",
            goal="Improve test coverage",
        )
        assert iid.startswith("iter-")
        assert len(iid) == 17  # "iter-" + 12 hex chars

    def test_kernel_state_is_admitted(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-start-002",
            goal="Refactor memory module",
        )
        assert bridge.get_kernel_state(iid) == "admitted"

    def test_passes_constraints(self, bridge: IterationBridge, store: KernelStore) -> None:
        bridge.on_iteration_start(
            spec_id="spec-start-003",
            goal="Optimize latency",
            constraints=["no breaking changes", "p99 < 50ms"],
        )
        entry = store.get_spec_entry("spec-start-003")
        assert entry is not None
        meta = json.loads(entry["metadata"])
        assert "no breaking changes" in meta["constraints"]

    def test_rejects_empty_goal(self, bridge: IterationBridge) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            bridge.on_iteration_start(spec_id="spec-empty", goal="")

    def test_defaults_constraints_to_empty(
        self, bridge: IterationBridge, store: KernelStore
    ) -> None:
        bridge.on_iteration_start(
            spec_id="spec-no-constraints",
            goal="Some goal",
        )
        entry = store.get_spec_entry("spec-no-constraints")
        assert entry is not None
        meta = json.loads(entry["metadata"])
        assert meta["constraints"] == []


# ------------------------------------------------------------------
# on_phase_transition — same kernel state (no-op)
# ------------------------------------------------------------------


class TestPhaseTransitionSameState:
    def test_generating_spec_to_spec_approval_is_noop(self, bridge: IterationBridge) -> None:
        """Both phases map to 'specifying', so no kernel transition needed."""
        iid = bridge.on_iteration_start(
            spec_id="spec-same-001",
            goal="Test same-state transitions",
        )
        # Move to researching, then specifying in kernel
        bridge.on_phase_transition(iteration_id=iid, from_phase="pending", to_phase="researching")
        bridge.on_phase_transition(
            iteration_id=iid, from_phase="researching", to_phase="generating_spec"
        )
        # generating_spec -> spec_approval: both map to specifying
        result = bridge.on_phase_transition(
            iteration_id=iid,
            from_phase="generating_spec",
            to_phase="spec_approval",
        )
        assert result is True
        assert bridge.get_kernel_state(iid) == "specifying"

    def test_spec_approval_to_decomposing_is_noop(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-same-002",
            goal="Test sub-state transitions",
        )
        bridge.on_phase_transition(iteration_id=iid, from_phase="pending", to_phase="researching")
        bridge.on_phase_transition(
            iteration_id=iid, from_phase="researching", to_phase="generating_spec"
        )
        result = bridge.on_phase_transition(
            iteration_id=iid,
            from_phase="spec_approval",
            to_phase="decomposing",
        )
        assert result is True

    def test_reviewing_to_benchmarking_is_noop(self, bridge: IterationBridge) -> None:
        """Both map to 'verifying'."""
        iid = bridge.on_iteration_start(
            spec_id="spec-same-003",
            goal="Test verifying sub-states",
        )
        # Advance through the full pipeline to verifying
        for from_p, to_p in [
            ("pending", "researching"),
            ("researching", "generating_spec"),
            ("decomposing", "implementing"),
            ("implementing", "reviewing"),
        ]:
            bridge.on_phase_transition(iteration_id=iid, from_phase=from_p, to_phase=to_p)
        result = bridge.on_phase_transition(
            iteration_id=iid,
            from_phase="reviewing",
            to_phase="benchmarking",
        )
        assert result is True
        assert bridge.get_kernel_state(iid) == "verifying"


# ------------------------------------------------------------------
# on_phase_transition — valid kernel transitions
# ------------------------------------------------------------------


class TestPhaseTransitionValid:
    def _advance_to_phase(
        self,
        bridge: IterationBridge,
        spec_id: str,
        target_phase: str,
    ) -> str:
        """Helper: start an iteration and advance to a target metaloop phase."""
        iid = bridge.on_iteration_start(spec_id=spec_id, goal="Test transitions")

        phase_sequence = [
            ("pending", "researching"),
            ("researching", "generating_spec"),
            ("decomposing", "implementing"),
            ("implementing", "reviewing"),
            ("benchmarking", "learning"),
            ("learning", "completed"),
        ]
        for from_p, to_p in phase_sequence:
            bridge.on_phase_transition(iteration_id=iid, from_phase=from_p, to_phase=to_p)
            if to_p == target_phase:
                break
        return iid

    def test_pending_to_researching(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-valid-001",
            goal="Test transition",
        )
        result = bridge.on_phase_transition(
            iteration_id=iid, from_phase="pending", to_phase="researching"
        )
        assert result is True
        assert bridge.get_kernel_state(iid) == "researching"

    def test_researching_to_generating_spec(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-valid-002",
            goal="Test transition",
        )
        bridge.on_phase_transition(iteration_id=iid, from_phase="pending", to_phase="researching")
        result = bridge.on_phase_transition(
            iteration_id=iid, from_phase="researching", to_phase="generating_spec"
        )
        assert result is True
        assert bridge.get_kernel_state(iid) == "specifying"

    def test_decomposing_to_implementing(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-valid-003",
            goal="Test transition",
        )
        bridge.on_phase_transition(iteration_id=iid, from_phase="pending", to_phase="researching")
        bridge.on_phase_transition(
            iteration_id=iid, from_phase="researching", to_phase="generating_spec"
        )
        result = bridge.on_phase_transition(
            iteration_id=iid, from_phase="decomposing", to_phase="implementing"
        )
        assert result is True
        assert bridge.get_kernel_state(iid) == "executing"

    def test_implementing_to_reviewing(self, bridge: IterationBridge) -> None:
        iid = self._advance_to_phase(bridge, "spec-valid-004", "implementing")
        result = bridge.on_phase_transition(
            iteration_id=iid, from_phase="implementing", to_phase="reviewing"
        )
        assert result is True
        assert bridge.get_kernel_state(iid) == "verifying"

    def test_benchmarking_to_learning(self, bridge: IterationBridge) -> None:
        iid = self._advance_to_phase(bridge, "spec-valid-005", "reviewing")
        result = bridge.on_phase_transition(
            iteration_id=iid, from_phase="benchmarking", to_phase="learning"
        )
        assert result is True
        assert bridge.get_kernel_state(iid) == "reconciling"


# ------------------------------------------------------------------
# on_phase_transition — invalid transitions
# ------------------------------------------------------------------


class TestPhaseTransitionInvalid:
    def test_returns_false_for_missing_iteration(self, bridge: IterationBridge) -> None:
        result = bridge.on_phase_transition(
            iteration_id="iter-nonexistent",
            from_phase="pending",
            to_phase="researching",
        )
        assert result is False

    def test_returns_false_for_invalid_skip(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-invalid-001",
            goal="Test invalid skip",
        )
        # Attempt to skip directly from admitted to executing
        result = bridge.on_phase_transition(
            iteration_id=iid,
            from_phase="pending",
            to_phase="implementing",
        )
        assert result is False


# ------------------------------------------------------------------
# on_phase_transition — idempotency
# ------------------------------------------------------------------


class TestPhaseTransitionIdempotent:
    def test_repeated_transition_returns_true(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-idemp-001",
            goal="Test idempotent transition",
        )
        bridge.on_phase_transition(iteration_id=iid, from_phase="pending", to_phase="researching")
        # Repeat the same transition
        result = bridge.on_phase_transition(
            iteration_id=iid, from_phase="pending", to_phase="researching"
        )
        assert result is True
        assert bridge.get_kernel_state(iid) == "researching"


# ------------------------------------------------------------------
# on_iteration_complete — promotion path
# ------------------------------------------------------------------


class TestOnIterationCompletePromoted:
    def _advance_to_reconciling(self, bridge: IterationBridge, spec_id: str) -> str:
        """Advance through the full pipeline to reconciling."""
        iid = bridge.on_iteration_start(spec_id=spec_id, goal="Test completion")
        transitions = [
            ("pending", "researching"),
            ("researching", "generating_spec"),
            ("decomposing", "implementing"),
            ("implementing", "reviewing"),
            ("benchmarking", "learning"),
        ]
        for from_p, to_p in transitions:
            bridge.on_phase_transition(iteration_id=iid, from_phase=from_p, to_phase=to_p)
        return iid

    def test_promoted_with_all_gate_conditions(self, bridge: IterationBridge) -> None:
        iid = self._advance_to_reconciling(bridge, "spec-complete-001")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"test_pass_rate": 0.98, "lint_score": 100},
            reconciliation_summary="All checks passed.",
            replay_stable=True,
        )
        assert verdict["result"] == "accepted"
        assert verdict["promoted"] is True
        assert bridge.get_kernel_state(iid) == "accepted"

    def test_lesson_pack_included_when_promoted(
        self, bridge: IterationBridge, store: KernelStore
    ) -> None:
        iid = self._advance_to_reconciling(bridge, "spec-complete-002")
        store.create_lesson("l1", iid, "playbook", "Always lint first")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 95},
            reconciliation_summary="Good outcome.",
            replay_stable=True,
        )
        assert verdict["promoted"] is True
        assert verdict["lesson_pack"] is not None
        assert "Always lint first" in verdict["lesson_pack"]["playbook_updates"]

    def test_rejected_without_replay_stable(self, bridge: IterationBridge) -> None:
        """Promotion requires replay_stable=True."""
        iid = self._advance_to_reconciling(bridge, "spec-complete-003")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 100},
            reconciliation_summary="Everything looks great.",
            # replay_stable defaults to False
        )
        assert verdict["result"] == "rejected"
        assert verdict["promoted"] is False

    def test_rejected_with_unexplained_drift(self, bridge: IterationBridge) -> None:
        """Promotion blocked when unexplained drift is present."""
        iid = self._advance_to_reconciling(bridge, "spec-complete-004")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 100},
            reconciliation_summary="Summary present.",
            replay_stable=True,
            unexplained_drift=["metric X regressed 5%"],
        )
        assert verdict["result"] == "rejected"
        assert verdict["promoted"] is False


# ------------------------------------------------------------------
# on_iteration_complete — rejection path
# ------------------------------------------------------------------


class TestOnIterationCompleteRejected:
    def _advance_to_reconciling(self, bridge: IterationBridge, spec_id: str) -> str:
        iid = bridge.on_iteration_start(spec_id=spec_id, goal="Test rejection")
        transitions = [
            ("pending", "researching"),
            ("researching", "generating_spec"),
            ("decomposing", "implementing"),
            ("implementing", "reviewing"),
            ("benchmarking", "learning"),
        ]
        for from_p, to_p in transitions:
            bridge.on_phase_transition(iteration_id=iid, from_phase=from_p, to_phase=to_p)
        return iid

    def test_rejected_without_benchmark(self, bridge: IterationBridge) -> None:
        iid = self._advance_to_reconciling(bridge, "spec-reject-001")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={},
            reconciliation_summary="Summary present.",
        )
        assert verdict["result"] == "rejected"
        assert verdict["promoted"] is False
        assert bridge.get_kernel_state(iid) == "rejected"

    def test_rejected_without_summary(self, bridge: IterationBridge) -> None:
        iid = self._advance_to_reconciling(bridge, "spec-reject-002")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 90},
            reconciliation_summary="",
        )
        assert verdict["result"] == "rejected"
        assert verdict["promoted"] is False

    def test_no_lesson_pack_when_rejected(self, bridge: IterationBridge) -> None:
        iid = self._advance_to_reconciling(bridge, "spec-reject-003")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={},
            reconciliation_summary="",
        )
        assert "lesson_pack" not in verdict


# ------------------------------------------------------------------
# on_iteration_complete — followup seeds
# ------------------------------------------------------------------


class TestOnIterationCompleteFollowup:
    def _advance_to_reconciling_with_seed(
        self,
        bridge: IterationBridge,
        store: KernelStore,
        spec_id: str,
        seed_goal: str,
    ) -> str:
        iid = bridge.on_iteration_start(spec_id=spec_id, goal="Test followup")
        transitions = [
            ("pending", "researching"),
            ("researching", "generating_spec"),
            ("decomposing", "implementing"),
            ("implementing", "reviewing"),
            ("benchmarking", "learning"),
        ]
        for from_p, to_p in transitions:
            bridge.on_phase_transition(iteration_id=iid, from_phase=from_p, to_phase=to_p)
        # Inject next_seed into metadata
        entry = store.get_spec_entry(spec_id)
        assert entry is not None
        meta = json.loads(entry["metadata"]) if entry["metadata"] else {}
        meta["next_seed"] = seed_goal
        store.update_spec_status(spec_id, entry["status"], metadata=meta)
        return iid

    def test_returns_next_seed_goal(self, bridge: IterationBridge, store: KernelStore) -> None:
        iid = self._advance_to_reconciling_with_seed(
            bridge,
            store,
            "spec-followup-001",
            "Optimize cache invalidation",
        )
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 95},
            reconciliation_summary="Passed with seed.",
            replay_stable=True,
        )
        assert verdict["result"] == "accepted_with_followups"
        assert verdict["next_seed_goal"] == "Optimize cache invalidation"


# ------------------------------------------------------------------
# check_promotion_gate — delegation
# ------------------------------------------------------------------


class TestCheckPromotionGate:
    def test_delegates_to_kernel(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-gate-001",
            goal="Test gate",
        )
        # Not in reconciling state — should return False
        assert bridge.check_promotion_gate(iid) is False


# ------------------------------------------------------------------
# extract_lessons — delegation
# ------------------------------------------------------------------


class TestExtractLessons:
    def test_delegates_to_kernel(self, bridge: IterationBridge, store: KernelStore) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-lessons-001",
            goal="Test lessons",
        )
        store.create_lesson("l1", iid, "pattern", "Use composition")
        pack = bridge.extract_lessons(iid)
        assert isinstance(pack, IterationLessonPack)
        assert "Use composition" in pack.pattern_updates


# ------------------------------------------------------------------
# get_kernel_state
# ------------------------------------------------------------------


class TestGetKernelState:
    def test_returns_state_string(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-state-001",
            goal="Test state query",
        )
        assert bridge.get_kernel_state(iid) == "admitted"

    def test_raises_for_missing(self, bridge: IterationBridge) -> None:
        with pytest.raises(KeyError, match="not found"):
            bridge.get_kernel_state("iter-ghost")


# ------------------------------------------------------------------
# BridgeVerdict
# ------------------------------------------------------------------


class TestBridgeVerdict:
    def test_to_dict_without_lesson_pack(self) -> None:
        verdict = BridgeVerdict(
            iteration_id="iter-v001",
            result="rejected",
            promoted=False,
        )
        d = verdict.to_dict()
        assert d["iteration_id"] == "iter-v001"
        assert d["result"] == "rejected"
        assert d["promoted"] is False
        assert "lesson_pack" not in d
        assert d["lane_artifacts"] == {}

    def test_to_dict_with_lesson_pack(self) -> None:
        pack = IterationLessonPack(
            lesson_id="lpack-001",
            iteration_id="iter-v002",
            playbook_updates=["Run lint first"],
        )
        verdict = BridgeVerdict(
            iteration_id="iter-v002",
            result="accepted",
            promoted=True,
            lesson_pack=pack,
        )
        d = verdict.to_dict()
        assert d["lesson_pack"]["playbook_updates"] == ["Run lint first"]

    def test_to_dict_includes_benchmark_results(self) -> None:
        verdict = BridgeVerdict(
            iteration_id="iter-v003",
            result="accepted",
            promoted=True,
            benchmark_results={"score": 100},
            reconciliation_summary="Perfect.",
        )
        d = verdict.to_dict()
        assert d["benchmark_results"] == {"score": 100}
        assert d["reconciliation_summary"] == "Perfect."

    def test_to_dict_includes_lane_artifacts(self) -> None:
        verdict = BridgeVerdict(
            iteration_id="iter-v004",
            result="accepted",
            promoted=True,
            lane_artifacts={
                "spec_goal": [{"artifact_type": "iteration_spec", "artifact_ref": "spec-001"}],
            },
        )
        d = verdict.to_dict()
        assert "spec_goal" in d["lane_artifacts"]
        assert d["lane_artifacts"]["spec_goal"][0]["artifact_type"] == "iteration_spec"


# ------------------------------------------------------------------
# kernel property
# ------------------------------------------------------------------


class TestKernelProperty:
    def test_exposes_iteration_kernel(self, bridge: IterationBridge) -> None:
        assert isinstance(bridge.kernel, IterationKernel)


# ------------------------------------------------------------------
# Full pipeline integration
# ------------------------------------------------------------------


class TestFullPipeline:
    """Walks the entire metaloop phase sequence through the bridge."""

    def test_full_iteration_lifecycle(self, bridge: IterationBridge, store: KernelStore) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-full-001",
            goal="End-to-end pipeline test",
            constraints=["no regressions"],
        )
        assert bridge.get_kernel_state(iid) == "admitted"

        # Phase sequence matching metaloop PHASE_ORDER
        phase_transitions = [
            ("pending", "researching", "researching"),
            ("researching", "generating_spec", "specifying"),
            ("generating_spec", "spec_approval", "specifying"),  # same-state
            ("spec_approval", "decomposing", "specifying"),  # same-state
            ("decomposing", "implementing", "executing"),
            ("implementing", "reviewing", "verifying"),
            ("reviewing", "benchmarking", "verifying"),  # same-state
            ("benchmarking", "learning", "reconciling"),
        ]

        for from_p, to_p, expected_kernel in phase_transitions:
            result = bridge.on_phase_transition(iteration_id=iid, from_phase=from_p, to_phase=to_p)
            assert result is True, f"Transition {from_p} -> {to_p} failed"
            assert bridge.get_kernel_state(iid) == expected_kernel, (
                f"Expected {expected_kernel} after {from_p} -> {to_p}"
            )

        # Add a lesson before completion
        store.create_lesson("l-full", iid, "playbook", "Always test first")

        # Complete with promotion
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"pass_rate": 1.0, "coverage": 0.92},
            reconciliation_summary="All 5 lanes passed successfully.",
            replay_stable=True,
        )

        assert verdict["result"] == "accepted"
        assert verdict["promoted"] is True
        assert verdict["lesson_pack"] is not None
        assert "Always test first" in verdict["lesson_pack"]["playbook_updates"]
        assert bridge.get_kernel_state(iid) == "accepted"


# ------------------------------------------------------------------
# Lane enum and LANE_EXPECTED_ARTIFACTS
# ------------------------------------------------------------------


class TestLaneDefinitions:
    def test_lane_has_five_values(self) -> None:
        assert len(Lane) == 5
        assert set(Lane) == {
            Lane.spec_goal,
            Lane.research,
            Lane.change,
            Lane.verification,
            Lane.reconcile,
        }

    def test_expected_artifacts_covers_all_lanes(self) -> None:
        assert set(LANE_EXPECTED_ARTIFACTS.keys()) == set(Lane)

    def test_lane_a_spec_goal_artifacts(self) -> None:
        expected = {"iteration_spec", "milestone_graph", "phase_contracts"}
        assert LANE_EXPECTED_ARTIFACTS[Lane.spec_goal] == frozenset(expected)

    def test_lane_b_research_artifacts(self) -> None:
        expected = {"research_report", "repo_diagnosis", "evidence_bundle"}
        assert LANE_EXPECTED_ARTIFACTS[Lane.research] == frozenset(expected)

    def test_lane_c_change_artifacts(self) -> None:
        expected = {"diff_bundle", "test_patch", "migration_notes"}
        assert LANE_EXPECTED_ARTIFACTS[Lane.change] == frozenset(expected)

    def test_lane_d_verification_artifacts(self) -> None:
        expected = {"benchmark_run", "replay_result", "verification_verdict"}
        assert LANE_EXPECTED_ARTIFACTS[Lane.verification] == frozenset(expected)

    def test_lane_e_reconcile_artifacts(self) -> None:
        expected = {
            "reconciliation_record",
            "lesson_pack",
            "template_update",
            "next_iteration_seed",
        }
        assert LANE_EXPECTED_ARTIFACTS[Lane.reconcile] == frozenset(expected)


# ------------------------------------------------------------------
# LaneArtifact
# ------------------------------------------------------------------


class TestLaneArtifact:
    def test_to_dict(self) -> None:
        art = LaneArtifact(
            lane=Lane.spec_goal,
            artifact_type="iteration_spec",
            artifact_ref="spec-001",
            metadata={"version": 1},
        )
        d = art.to_dict()
        assert d["lane"] == "spec_goal"
        assert d["artifact_type"] == "iteration_spec"
        assert d["artifact_ref"] == "spec-001"
        assert d["metadata"] == {"version": 1}
        assert "produced_at" in d

    def test_frozen(self) -> None:
        art = LaneArtifact(
            lane=Lane.change,
            artifact_type="diff_bundle",
            artifact_ref="ref-123",
        )
        with pytest.raises(AttributeError):
            art.lane = Lane.research  # type: ignore[misc]


# ------------------------------------------------------------------
# _PHASE_TO_LANE mapping
# ------------------------------------------------------------------


class TestPhaseToLaneMapping:
    def test_all_metaloop_phases_have_lane(self) -> None:
        """Every metaloop phase string must map to a lane."""
        expected_phases = {
            "pending",
            "researching",
            "generating_spec",
            "spec_approval",
            "decomposing",
            "implementing",
            "reviewing",
            "benchmarking",
            "learning",
            "completed",
            "failed",
        }
        assert set(_PHASE_TO_LANE.keys()) == expected_phases

    def test_spec_goal_phases(self) -> None:
        for phase in ("pending", "generating_spec", "spec_approval", "decomposing"):
            assert _PHASE_TO_LANE[phase] == Lane.spec_goal

    def test_research_phase(self) -> None:
        assert _PHASE_TO_LANE["researching"] == Lane.research

    def test_change_phase(self) -> None:
        assert _PHASE_TO_LANE["implementing"] == Lane.change

    def test_verification_phases(self) -> None:
        for phase in ("reviewing", "benchmarking"):
            assert _PHASE_TO_LANE[phase] == Lane.verification

    def test_reconcile_phases(self) -> None:
        for phase in ("learning", "completed", "failed"):
            assert _PHASE_TO_LANE[phase] == Lane.reconcile


# ------------------------------------------------------------------
# map_phase_to_lane
# ------------------------------------------------------------------


class TestMapPhaseToLane:
    def test_known_phases(self) -> None:
        assert IterationBridge.map_phase_to_lane("pending") == Lane.spec_goal
        assert IterationBridge.map_phase_to_lane("researching") == Lane.research
        assert IterationBridge.map_phase_to_lane("implementing") == Lane.change
        assert IterationBridge.map_phase_to_lane("reviewing") == Lane.verification
        assert IterationBridge.map_phase_to_lane("learning") == Lane.reconcile

    def test_unknown_phase_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown metaloop phase"):
            IterationBridge.map_phase_to_lane("nonexistent")


# ------------------------------------------------------------------
# LaneTracker
# ------------------------------------------------------------------


class TestLaneTracker:
    def test_record_and_retrieve(self) -> None:
        tracker = LaneTracker()
        art = LaneArtifact(
            lane=Lane.spec_goal,
            artifact_type="iteration_spec",
            artifact_ref="ref-001",
        )
        tracker.record("iter-001", art)
        artifacts = tracker.get_lane_artifacts("iter-001", Lane.spec_goal)
        assert len(artifacts) == 1
        assert artifacts[0].artifact_ref == "ref-001"

    def test_retrieve_all_lanes(self) -> None:
        tracker = LaneTracker()
        tracker.record(
            "iter-001",
            LaneArtifact(lane=Lane.spec_goal, artifact_type="iteration_spec", artifact_ref="a"),
        )
        tracker.record(
            "iter-001",
            LaneArtifact(lane=Lane.research, artifact_type="evidence_bundle", artifact_ref="b"),
        )
        all_artifacts = tracker.get_lane_artifacts("iter-001")
        assert len(all_artifacts) == 2

    def test_get_all_lanes_as_dicts(self) -> None:
        tracker = LaneTracker()
        tracker.record(
            "iter-001",
            LaneArtifact(lane=Lane.change, artifact_type="diff_bundle", artifact_ref="d1"),
        )
        result = tracker.get_all_lanes("iter-001")
        assert "change" in result
        assert result["change"][0]["artifact_type"] == "diff_bundle"

    def test_empty_iteration_returns_empty(self) -> None:
        tracker = LaneTracker()
        assert tracker.get_lane_artifacts("iter-nope") == []
        assert tracker.get_all_lanes("iter-nope") == {}

    def test_missing_artifacts(self) -> None:
        tracker = LaneTracker()
        tracker.record(
            "iter-001",
            LaneArtifact(lane=Lane.spec_goal, artifact_type="iteration_spec", artifact_ref="a"),
        )
        missing = tracker.missing_artifacts("iter-001", Lane.spec_goal)
        assert missing == frozenset({"milestone_graph", "phase_contracts"})

    def test_lane_complete(self) -> None:
        tracker = LaneTracker()
        iid = "iter-complete"
        for art_type in ("iteration_spec", "milestone_graph", "phase_contracts"):
            tracker.record(
                iid,
                LaneArtifact(lane=Lane.spec_goal, artifact_type=art_type, artifact_ref=art_type),
            )
        assert tracker.lane_complete(iid, Lane.spec_goal) is True
        assert tracker.lane_complete(iid, Lane.research) is False

    def test_all_lanes_complete(self) -> None:
        tracker = LaneTracker()
        iid = "iter-all"
        for lane, expected_types in LANE_EXPECTED_ARTIFACTS.items():
            for art_type in expected_types:
                tracker.record(
                    iid,
                    LaneArtifact(
                        lane=lane,
                        artifact_type=art_type,
                        artifact_ref=f"{lane}-{art_type}",
                    ),
                )
        assert tracker.all_lanes_complete(iid) is True

    def test_all_lanes_complete_false_when_partial(self) -> None:
        tracker = LaneTracker()
        iid = "iter-partial"
        tracker.record(
            iid,
            LaneArtifact(lane=Lane.spec_goal, artifact_type="iteration_spec", artifact_ref="a"),
        )
        assert tracker.all_lanes_complete(iid) is False

    def test_summary(self) -> None:
        tracker = LaneTracker()
        iid = "iter-summary"
        tracker.record(
            iid,
            LaneArtifact(lane=Lane.change, artifact_type="diff_bundle", artifact_ref="d1"),
        )
        tracker.record(
            iid,
            LaneArtifact(lane=Lane.change, artifact_type="test_patch", artifact_ref="t1"),
        )
        summary = tracker.summary(iid)
        assert summary["change"]["produced"] == ["diff_bundle", "test_patch"]
        assert summary["change"]["missing"] == ["migration_notes"]
        assert summary["change"]["complete"] is False
        # Lanes with nothing produced should show all as missing
        assert len(summary["spec_goal"]["missing"]) == 3
        assert summary["spec_goal"]["complete"] is False


# ------------------------------------------------------------------
# record_lane_artifact (on bridge)
# ------------------------------------------------------------------


class TestRecordLaneArtifact:
    def test_record_valid_artifact(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(spec_id="spec-lane-001", goal="Test lane recording")
        art = bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.spec_goal,
            artifact_type="iteration_spec",
            artifact_ref="spec-ref-001",
        )
        assert art.lane == Lane.spec_goal
        assert art.artifact_type == "iteration_spec"
        assert art.artifact_ref == "spec-ref-001"

    def test_record_with_string_lane(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(spec_id="spec-lane-002", goal="String lane test")
        art = bridge.record_lane_artifact(
            iteration_id=iid,
            lane="research",
            artifact_type="evidence_bundle",
            artifact_ref="eb-001",
        )
        assert art.lane == Lane.research

    def test_record_with_metadata(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(spec_id="spec-lane-003", goal="Metadata test")
        art = bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.change,
            artifact_type="diff_bundle",
            artifact_ref="d-001",
            metadata={"files_changed": 3},
        )
        assert art.metadata == {"files_changed": 3}

    def test_reject_unexpected_artifact_type(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(spec_id="spec-lane-004", goal="Invalid artifact test")
        with pytest.raises(ValueError, match="Unexpected artifact_type"):
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.spec_goal,
                artifact_type="wrong_type",
                artifact_ref="ref",
            )

    def test_reject_invalid_lane_string(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(spec_id="spec-lane-005", goal="Invalid lane test")
        with pytest.raises(ValueError):
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane="nonexistent_lane",
                artifact_type="whatever",
                artifact_ref="ref",
            )

    def test_artifacts_visible_via_lane_tracker(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(spec_id="spec-lane-006", goal="Tracker visibility")
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.verification,
            artifact_type="benchmark_run",
            artifact_ref="bench-001",
        )
        tracker_arts = bridge.lane_tracker.get_lane_artifacts(iid, Lane.verification)
        assert len(tracker_arts) == 1
        assert tracker_arts[0].artifact_type == "benchmark_run"


# ------------------------------------------------------------------
# lane_tracker property
# ------------------------------------------------------------------


class TestLaneTrackerProperty:
    def test_exposes_lane_tracker(self, bridge: IterationBridge) -> None:
        assert isinstance(bridge.lane_tracker, LaneTracker)


# ------------------------------------------------------------------
# on_iteration_complete — lane artifacts in verdict
# ------------------------------------------------------------------


class TestOnIterationCompleteLaneArtifacts:
    def _advance_to_reconciling(self, bridge: IterationBridge, spec_id: str) -> str:
        iid = bridge.on_iteration_start(spec_id=spec_id, goal="Lane artifact in verdict")
        transitions = [
            ("pending", "researching"),
            ("researching", "generating_spec"),
            ("decomposing", "implementing"),
            ("implementing", "reviewing"),
            ("benchmarking", "learning"),
        ]
        for from_p, to_p in transitions:
            bridge.on_phase_transition(iteration_id=iid, from_phase=from_p, to_phase=to_p)
        return iid

    def test_verdict_includes_lane_artifacts(self, bridge: IterationBridge) -> None:
        iid = self._advance_to_reconciling(bridge, "spec-lanev-001")
        # Record some lane artifacts
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.spec_goal,
            artifact_type="iteration_spec",
            artifact_ref="spec-ref",
        )
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.change,
            artifact_type="diff_bundle",
            artifact_ref="diff-ref",
        )
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 99},
            reconciliation_summary="All good.",
            replay_stable=True,
        )
        assert "lane_artifacts" in verdict
        assert "spec_goal" in verdict["lane_artifacts"]
        assert "change" in verdict["lane_artifacts"]
        assert verdict["lane_artifacts"]["spec_goal"][0]["artifact_type"] == "iteration_spec"

    def test_verdict_empty_lane_artifacts_when_none_recorded(self, bridge: IterationBridge) -> None:
        iid = self._advance_to_reconciling(bridge, "spec-lanev-002")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 99},
            reconciliation_summary="All good.",
            replay_stable=True,
        )
        assert verdict["lane_artifacts"] == {}


# ------------------------------------------------------------------
# Full pipeline integration with lane artifacts
# ------------------------------------------------------------------


class TestFullPipelineWithLanes:
    """Full lifecycle test including lane artifact tracking."""

    def test_full_lifecycle_with_all_lane_artifacts(
        self, bridge: IterationBridge, store: KernelStore
    ) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-fulllane-001",
            goal="Full pipeline with lane tracking",
            constraints=["no regressions"],
        )

        # Record Lane A artifacts (spec_goal)
        for art_type in ("iteration_spec", "milestone_graph", "phase_contracts"):
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.spec_goal,
                artifact_type=art_type,
                artifact_ref=f"lane-a-{art_type}",
            )

        # Advance through phases and record artifacts along the way
        bridge.on_phase_transition(iteration_id=iid, from_phase="pending", to_phase="researching")

        # Record Lane B artifacts (research)
        for art_type in ("research_report", "repo_diagnosis", "evidence_bundle"):
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.research,
                artifact_type=art_type,
                artifact_ref=f"lane-b-{art_type}",
            )

        bridge.on_phase_transition(
            iteration_id=iid, from_phase="researching", to_phase="generating_spec"
        )
        bridge.on_phase_transition(
            iteration_id=iid, from_phase="decomposing", to_phase="implementing"
        )

        # Record Lane C artifacts (change)
        for art_type in ("diff_bundle", "test_patch", "migration_notes"):
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.change,
                artifact_type=art_type,
                artifact_ref=f"lane-c-{art_type}",
            )

        bridge.on_phase_transition(
            iteration_id=iid, from_phase="implementing", to_phase="reviewing"
        )

        # Record Lane D artifacts (verification)
        for art_type in ("benchmark_run", "replay_result", "verification_verdict"):
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.verification,
                artifact_type=art_type,
                artifact_ref=f"lane-d-{art_type}",
            )

        bridge.on_phase_transition(iteration_id=iid, from_phase="benchmarking", to_phase="learning")

        # Record Lane E artifacts (reconcile)
        for art_type in (
            "reconciliation_record",
            "lesson_pack",
            "template_update",
            "next_iteration_seed",
        ):
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.reconcile,
                artifact_type=art_type,
                artifact_ref=f"lane-e-{art_type}",
            )

        # All lanes should be complete
        assert bridge.lane_tracker.all_lanes_complete(iid) is True

        # Add a lesson and complete
        store.create_lesson("l-fulllane", iid, "playbook", "Lane tracking works")

        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"pass_rate": 1.0},
            reconciliation_summary="All 5 lanes complete.",
            replay_stable=True,
        )

        assert verdict["result"] == "accepted"
        assert verdict["promoted"] is True
        # Verify lane artifacts are in the verdict
        la = verdict["lane_artifacts"]
        assert len(la) == 5
        assert len(la["spec_goal"]) == 3
        assert len(la["research"]) == 3
        assert len(la["change"]) == 3
        assert len(la["verification"]) == 3
        assert len(la["reconcile"]) == 4

        # Summary should show all complete
        summary = bridge.lane_tracker.summary(iid)
        for lane_name, lane_data in summary.items():
            assert lane_data["complete"] is True, f"Lane {lane_name} is not complete"
