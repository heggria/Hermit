"""Deep tests for IterationBridge — lane artifact tracking, phase-to-state
mapping, and idempotent phase transitions across all 5 lanes and 11 phases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.execution.self_modify.iteration_bridge import (
    _PHASE_TO_LANE,
    _PHASE_TO_STATE,
    LANE_EXPECTED_ARTIFACTS,
    IterationBridge,
    Lane,
    LaneArtifact,
    LaneTracker,
)
from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture()
def bridge(store: KernelStore) -> IterationBridge:
    return IterationBridge(store)


def _admit_iteration(bridge: IterationBridge, spec_id: str = "spec-test") -> str:
    """Helper to start an iteration and return its ID."""
    return bridge.on_iteration_start(spec_id=spec_id, goal="Test iteration goal")


def _record_all_lane_artifacts(
    tracker: LaneTracker,
    iteration_id: str,
    lane: Lane,
) -> None:
    """Record all expected artifacts for a lane."""
    for art_type in LANE_EXPECTED_ARTIFACTS[lane]:
        tracker.record(
            iteration_id,
            LaneArtifact(
                lane=lane,
                artifact_type=art_type,
                artifact_ref=f"ref:{lane.value}:{art_type}",
            ),
        )


# ---------------------------------------------------------------------------
# Lane artifact tracking across all 5 lanes
# ---------------------------------------------------------------------------


class TestLaneArtifactTracking:
    """Verify artifact tracking for each of the 5 lanes."""

    def test_lane_a_spec_goal_expected_artifacts(self) -> None:
        expected = LANE_EXPECTED_ARTIFACTS[Lane.spec_goal]
        assert expected == frozenset({"iteration_spec", "milestone_graph", "phase_contracts"})

    def test_lane_b_research_expected_artifacts(self) -> None:
        expected = LANE_EXPECTED_ARTIFACTS[Lane.research]
        assert expected == frozenset({"research_report", "repo_diagnosis", "evidence_bundle"})

    def test_lane_c_change_expected_artifacts(self) -> None:
        expected = LANE_EXPECTED_ARTIFACTS[Lane.change]
        assert expected == frozenset({"diff_bundle", "test_patch", "migration_notes"})

    def test_lane_d_verification_expected_artifacts(self) -> None:
        expected = LANE_EXPECTED_ARTIFACTS[Lane.verification]
        assert expected == frozenset({"benchmark_run", "replay_result", "verification_verdict"})

    def test_lane_e_reconcile_expected_artifacts(self) -> None:
        expected = LANE_EXPECTED_ARTIFACTS[Lane.reconcile]
        assert expected == frozenset(
            {
                "reconciliation_record",
                "lesson_pack",
                "template_update",
                "next_iteration_seed",
            }
        )

    def test_record_artifact_increases_count(self) -> None:
        tracker = LaneTracker()
        tracker.record(
            "iter-1",
            LaneArtifact(lane=Lane.spec_goal, artifact_type="iteration_spec", artifact_ref="ref1"),
        )
        assert len(tracker.get_lane_artifacts("iter-1", Lane.spec_goal)) == 1

    def test_record_multiple_artifacts_same_lane(self) -> None:
        tracker = LaneTracker()
        for art_type in LANE_EXPECTED_ARTIFACTS[Lane.spec_goal]:
            tracker.record(
                "iter-1",
                LaneArtifact(
                    lane=Lane.spec_goal, artifact_type=art_type, artifact_ref=f"r-{art_type}"
                ),
            )
        assert len(tracker.get_lane_artifacts("iter-1", Lane.spec_goal)) == 3

    def test_get_lane_artifacts_no_filter_returns_all(self) -> None:
        tracker = LaneTracker()
        tracker.record(
            "iter-1",
            LaneArtifact(lane=Lane.spec_goal, artifact_type="iteration_spec", artifact_ref="r1"),
        )
        tracker.record(
            "iter-1",
            LaneArtifact(lane=Lane.research, artifact_type="research_report", artifact_ref="r2"),
        )
        all_arts = tracker.get_lane_artifacts("iter-1")
        assert len(all_arts) == 2

    def test_get_lane_artifacts_unknown_iteration(self) -> None:
        tracker = LaneTracker()
        assert tracker.get_lane_artifacts("nonexistent") == []

    def test_get_all_lanes_returns_dict(self) -> None:
        tracker = LaneTracker()
        tracker.record(
            "iter-1",
            LaneArtifact(lane=Lane.spec_goal, artifact_type="iteration_spec", artifact_ref="r1"),
        )
        result = tracker.get_all_lanes("iter-1")
        assert "spec_goal" in result
        assert len(result["spec_goal"]) == 1


# ---------------------------------------------------------------------------
# Lane completion checks
# ---------------------------------------------------------------------------


class TestLaneComplete:
    """Test lane_complete() for each lane with required artifacts."""

    def test_lane_a_incomplete_with_no_artifacts(self) -> None:
        tracker = LaneTracker()
        assert not tracker.lane_complete("iter-1", Lane.spec_goal)

    def test_lane_a_complete_with_all_artifacts(self) -> None:
        tracker = LaneTracker()
        _record_all_lane_artifacts(tracker, "iter-1", Lane.spec_goal)
        assert tracker.lane_complete("iter-1", Lane.spec_goal)

    def test_lane_b_complete_with_all_artifacts(self) -> None:
        tracker = LaneTracker()
        _record_all_lane_artifacts(tracker, "iter-1", Lane.research)
        assert tracker.lane_complete("iter-1", Lane.research)

    def test_lane_c_complete_with_all_artifacts(self) -> None:
        tracker = LaneTracker()
        _record_all_lane_artifacts(tracker, "iter-1", Lane.change)
        assert tracker.lane_complete("iter-1", Lane.change)

    def test_lane_d_complete_with_all_artifacts(self) -> None:
        tracker = LaneTracker()
        _record_all_lane_artifacts(tracker, "iter-1", Lane.verification)
        assert tracker.lane_complete("iter-1", Lane.verification)

    def test_lane_e_complete_with_all_artifacts(self) -> None:
        tracker = LaneTracker()
        _record_all_lane_artifacts(tracker, "iter-1", Lane.reconcile)
        assert tracker.lane_complete("iter-1", Lane.reconcile)

    def test_lane_incomplete_when_partial_artifacts(self) -> None:
        tracker = LaneTracker()
        # Only add 1 of 3 expected artifacts for spec_goal
        tracker.record(
            "iter-1",
            LaneArtifact(lane=Lane.spec_goal, artifact_type="iteration_spec", artifact_ref="r1"),
        )
        assert not tracker.lane_complete("iter-1", Lane.spec_goal)

    def test_missing_artifacts_returns_correct_set(self) -> None:
        tracker = LaneTracker()
        tracker.record(
            "iter-1",
            LaneArtifact(lane=Lane.spec_goal, artifact_type="iteration_spec", artifact_ref="r1"),
        )
        missing = tracker.missing_artifacts("iter-1", Lane.spec_goal)
        assert missing == frozenset({"milestone_graph", "phase_contracts"})


# ---------------------------------------------------------------------------
# all_lanes_complete()
# ---------------------------------------------------------------------------


class TestAllLanesComplete:
    """Test all_lanes_complete() when some lanes are incomplete."""

    def test_all_complete_when_every_lane_filled(self) -> None:
        tracker = LaneTracker()
        for lane in Lane:
            _record_all_lane_artifacts(tracker, "iter-1", lane)
        assert tracker.all_lanes_complete("iter-1")

    def test_not_complete_when_one_lane_missing(self) -> None:
        tracker = LaneTracker()
        for lane in Lane:
            if lane != Lane.reconcile:
                _record_all_lane_artifacts(tracker, "iter-1", lane)
        assert not tracker.all_lanes_complete("iter-1")

    def test_not_complete_when_all_lanes_empty(self) -> None:
        tracker = LaneTracker()
        assert not tracker.all_lanes_complete("iter-1")

    def test_not_complete_when_only_one_lane_filled(self) -> None:
        tracker = LaneTracker()
        _record_all_lane_artifacts(tracker, "iter-1", Lane.spec_goal)
        assert not tracker.all_lanes_complete("iter-1")

    def test_summary_reflects_completion_status(self) -> None:
        tracker = LaneTracker()
        _record_all_lane_artifacts(tracker, "iter-1", Lane.spec_goal)
        summary = tracker.summary("iter-1")
        assert summary["spec_goal"]["complete"] is True
        assert summary["research"]["complete"] is False
        assert len(summary["research"]["missing"]) > 0


# ---------------------------------------------------------------------------
# Phase-to-state mapping for all 11 metaloop phases
# ---------------------------------------------------------------------------


class TestPhaseToStateMapping:
    """Verify _PHASE_TO_STATE covers all 11 metaloop phases."""

    ALL_PHASES = [
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
    ]

    def test_all_11_phases_mapped(self) -> None:
        for phase in self.ALL_PHASES:
            assert phase in _PHASE_TO_STATE, f"Phase {phase!r} not in _PHASE_TO_STATE"

    def test_pending_maps_to_draft(self) -> None:
        assert _PHASE_TO_STATE["pending"] == "draft"

    def test_researching_maps_to_researching(self) -> None:
        assert _PHASE_TO_STATE["researching"] == "researching"

    def test_generating_spec_maps_to_specifying(self) -> None:
        assert _PHASE_TO_STATE["generating_spec"] == "specifying"

    def test_spec_approval_maps_to_specifying(self) -> None:
        assert _PHASE_TO_STATE["spec_approval"] == "specifying"

    def test_decomposing_maps_to_specifying(self) -> None:
        assert _PHASE_TO_STATE["decomposing"] == "specifying"

    def test_implementing_maps_to_executing(self) -> None:
        assert _PHASE_TO_STATE["implementing"] == "executing"

    def test_reviewing_maps_to_verifying(self) -> None:
        assert _PHASE_TO_STATE["reviewing"] == "verifying"

    def test_benchmarking_maps_to_verifying(self) -> None:
        assert _PHASE_TO_STATE["benchmarking"] == "verifying"

    def test_learning_maps_to_reconciling(self) -> None:
        assert _PHASE_TO_STATE["learning"] == "reconciling"

    def test_completed_maps_to_accepted(self) -> None:
        assert _PHASE_TO_STATE["completed"] == "accepted"

    def test_failed_maps_to_rejected(self) -> None:
        assert _PHASE_TO_STATE["failed"] == "rejected"

    def test_unknown_phase_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="Unknown metaloop phase"):
            IterationBridge.map_phase_to_state("unknown_phase")


# ---------------------------------------------------------------------------
# Phase-to-lane mapping
# ---------------------------------------------------------------------------


class TestPhaseToLaneMapping:
    """Verify _PHASE_TO_LANE covers all phases."""

    def test_pending_maps_to_spec_goal(self) -> None:
        assert _PHASE_TO_LANE["pending"] == Lane.spec_goal

    def test_generating_spec_maps_to_spec_goal(self) -> None:
        assert _PHASE_TO_LANE["generating_spec"] == Lane.spec_goal

    def test_spec_approval_maps_to_spec_goal(self) -> None:
        assert _PHASE_TO_LANE["spec_approval"] == Lane.spec_goal

    def test_decomposing_maps_to_spec_goal(self) -> None:
        assert _PHASE_TO_LANE["decomposing"] == Lane.spec_goal

    def test_researching_maps_to_research(self) -> None:
        assert _PHASE_TO_LANE["researching"] == Lane.research

    def test_implementing_maps_to_change(self) -> None:
        assert _PHASE_TO_LANE["implementing"] == Lane.change

    def test_reviewing_maps_to_verification(self) -> None:
        assert _PHASE_TO_LANE["reviewing"] == Lane.verification

    def test_benchmarking_maps_to_verification(self) -> None:
        assert _PHASE_TO_LANE["benchmarking"] == Lane.verification

    def test_learning_maps_to_reconcile(self) -> None:
        assert _PHASE_TO_LANE["learning"] == Lane.reconcile

    def test_completed_maps_to_reconcile(self) -> None:
        assert _PHASE_TO_LANE["completed"] == Lane.reconcile

    def test_failed_maps_to_reconcile(self) -> None:
        assert _PHASE_TO_LANE["failed"] == Lane.reconcile

    def test_unknown_phase_raises_via_bridge(self) -> None:
        with pytest.raises(ValueError, match="Unknown metaloop phase"):
            IterationBridge.map_phase_to_lane("unknown_phase")


# ---------------------------------------------------------------------------
# Idempotent phase transitions
# ---------------------------------------------------------------------------


class TestIdempotentPhaseTransitions:
    """Ensure same phase twice causes no error (idempotent)."""

    def test_same_kernel_state_phases_skip_transition(self, bridge: IterationBridge) -> None:
        """generating_spec and spec_approval both map to 'specifying' — no kernel transition."""
        iteration_id = _admit_iteration(bridge)
        # Advance to researching first
        result1 = bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="pending",
            to_phase="researching",
        )
        assert result1 is True

        # Advance to specifying
        result2 = bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="researching",
            to_phase="generating_spec",
        )
        assert result2 is True

        # generating_spec → spec_approval: both map to 'specifying', should skip
        result3 = bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="generating_spec",
            to_phase="spec_approval",
        )
        assert result3 is True

    def test_spec_approval_to_decomposing_both_specifying(self, bridge: IterationBridge) -> None:
        """spec_approval → decomposing both map to 'specifying' — idempotent."""
        iteration_id = _admit_iteration(bridge)
        # Advance through to specifying
        bridge.on_phase_transition(
            iteration_id=iteration_id, from_phase="pending", to_phase="researching"
        )
        bridge.on_phase_transition(
            iteration_id=iteration_id, from_phase="researching", to_phase="generating_spec"
        )

        result = bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="spec_approval",
            to_phase="decomposing",
        )
        assert result is True

    def test_reviewing_to_benchmarking_both_verifying(self, bridge: IterationBridge) -> None:
        """reviewing → benchmarking both map to 'verifying' — idempotent."""
        iteration_id = _admit_iteration(bridge)
        # Advance through to verifying
        bridge.on_phase_transition(
            iteration_id=iteration_id, from_phase="pending", to_phase="researching"
        )
        bridge.on_phase_transition(
            iteration_id=iteration_id, from_phase="researching", to_phase="generating_spec"
        )
        bridge.on_phase_transition(
            iteration_id=iteration_id, from_phase="generating_spec", to_phase="implementing"
        )
        bridge.on_phase_transition(
            iteration_id=iteration_id, from_phase="implementing", to_phase="reviewing"
        )

        result = bridge.on_phase_transition(
            iteration_id=iteration_id,
            from_phase="reviewing",
            to_phase="benchmarking",
        )
        assert result is True

    def test_already_at_target_state_returns_true(self, bridge: IterationBridge) -> None:
        """If kernel is already at the target state, on_phase_transition returns True."""
        iteration_id = _admit_iteration(bridge)
        # Advance to researching
        bridge.on_phase_transition(
            iteration_id=iteration_id, from_phase="pending", to_phase="researching"
        )
        # Try again — already at researching
        result = bridge.on_phase_transition(
            iteration_id=iteration_id, from_phase="pending", to_phase="researching"
        )
        assert result is True


# ---------------------------------------------------------------------------
# Bridge record_lane_artifact validation
# ---------------------------------------------------------------------------


class TestBridgeRecordLaneArtifact:
    """Test IterationBridge.record_lane_artifact() validation."""

    def test_valid_artifact_recorded(self, bridge: IterationBridge) -> None:
        iteration_id = _admit_iteration(bridge)
        artifact = bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane=Lane.spec_goal,
            artifact_type="iteration_spec",
            artifact_ref="ref:spec",
        )
        assert artifact.lane == Lane.spec_goal
        assert artifact.artifact_type == "iteration_spec"

    def test_invalid_artifact_type_raises(self, bridge: IterationBridge) -> None:
        iteration_id = _admit_iteration(bridge)
        with pytest.raises(ValueError, match="Unexpected artifact_type"):
            bridge.record_lane_artifact(
                iteration_id=iteration_id,
                lane=Lane.spec_goal,
                artifact_type="invalid_type",
                artifact_ref="ref:bad",
            )

    def test_string_lane_accepted(self, bridge: IterationBridge) -> None:
        iteration_id = _admit_iteration(bridge)
        artifact = bridge.record_lane_artifact(
            iteration_id=iteration_id,
            lane="research",
            artifact_type="research_report",
            artifact_ref="ref:report",
        )
        assert artifact.lane == Lane.research

    def test_invalid_lane_string_raises(self, bridge: IterationBridge) -> None:
        iteration_id = _admit_iteration(bridge)
        with pytest.raises(ValueError):
            bridge.record_lane_artifact(
                iteration_id=iteration_id,
                lane="nonexistent_lane",
                artifact_type="some_type",
                artifact_ref="ref:bad",
            )


# ---------------------------------------------------------------------------
# LaneArtifact model
# ---------------------------------------------------------------------------


class TestLaneArtifactModel:
    """Verify LaneArtifact is frozen and serializable."""

    def test_frozen(self) -> None:
        art = LaneArtifact(lane=Lane.spec_goal, artifact_type="iteration_spec", artifact_ref="r1")
        with pytest.raises(AttributeError):
            art.lane = Lane.research  # type: ignore[misc]

    def test_to_dict(self) -> None:
        art = LaneArtifact(
            lane=Lane.spec_goal,
            artifact_type="iteration_spec",
            artifact_ref="r1",
            metadata={"key": "val"},
        )
        d = art.to_dict()
        assert d["lane"] == "spec_goal"
        assert d["artifact_type"] == "iteration_spec"
        assert d["artifact_ref"] == "r1"
        assert d["metadata"] == {"key": "val"}
        assert "produced_at" in d

    def test_default_metadata_is_empty(self) -> None:
        art = LaneArtifact(lane=Lane.change, artifact_type="diff_bundle", artifact_ref="r")
        assert art.metadata == {}


# ---------------------------------------------------------------------------
# Lane enum
# ---------------------------------------------------------------------------


class TestLaneEnum:
    """Verify Lane enum has exactly 5 values."""

    def test_lane_count(self) -> None:
        assert len(Lane) == 5

    def test_lane_values(self) -> None:
        expected = {"spec_goal", "research", "change", "verification", "reconcile"}
        assert {lane.value for lane in Lane} == expected
