"""Integration test — IterationBridge complete lifecycle with 5-lane artifact tracking.

Exercises the COMPLETE bridge lifecycle:
  Start → phase transitions → lane artifact recording → completion → BridgeVerdict

Uses a real KernelStore (SQLite) to validate the full governed chain end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.execution.self_modify.iteration_bridge import (
    LANE_EXPECTED_ARTIFACTS,
    BridgeVerdict,
    IterationBridge,
    Lane,
)
from hermit.kernel.ledger.journal.store import KernelStore

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "bridge_chain.db")


@pytest.fixture()
def bridge(store: KernelStore) -> IterationBridge:
    return IterationBridge(store)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

# Full metaloop phase transition sequence (from_phase, to_phase).
FULL_PHASE_SEQUENCE: list[tuple[str, str]] = [
    ("pending", "researching"),
    ("researching", "generating_spec"),
    ("generating_spec", "spec_approval"),
    ("spec_approval", "decomposing"),
    ("decomposing", "implementing"),
    ("implementing", "reviewing"),
    ("reviewing", "benchmarking"),
    ("benchmarking", "learning"),
]

# Artifacts to record for each lane (subset of the full expected set).
LANE_ARTIFACTS_SUBSET: dict[Lane, list[tuple[str, str]]] = {
    Lane.spec_goal: [
        ("iteration_spec", "ref://spec/iteration_spec"),
        ("milestone_graph", "ref://spec/milestone_graph"),
    ],
    Lane.research: [
        ("research_report", "ref://research/report"),
        ("evidence_bundle", "ref://research/evidence"),
    ],
    Lane.change: [
        ("diff_bundle", "ref://change/diff"),
        ("test_patch", "ref://change/test_patch"),
    ],
    Lane.verification: [
        ("benchmark_run", "ref://verify/benchmark"),
        ("verification_verdict", "ref://verify/verdict"),
    ],
    Lane.reconcile: [
        ("reconciliation_record", "ref://reconcile/record"),
        ("lesson_pack", "ref://reconcile/lessons"),
    ],
}

# Complete artifacts for every lane (all expected types present).
LANE_ARTIFACTS_COMPLETE: dict[Lane, list[tuple[str, str]]] = {
    lane: [(art_type, f"ref://{lane.value}/{art_type}") for art_type in expected]
    for lane, expected in LANE_EXPECTED_ARTIFACTS.items()
}


def _advance_to_reconciling(bridge: IterationBridge, spec_id: str) -> str:
    """Start an iteration and advance through all phases to reconciling."""
    iid = bridge.on_iteration_start(
        spec_id=spec_id,
        goal="Integration test iteration",
        constraints=["no regressions", "maintain coverage"],
    )
    for from_p, to_p in FULL_PHASE_SEQUENCE:
        result = bridge.on_phase_transition(iteration_id=iid, from_phase=from_p, to_phase=to_p)
        assert result is True, f"Phase transition {from_p} -> {to_p} failed"
    return iid


def _record_all_lane_artifacts(
    bridge: IterationBridge,
    iteration_id: str,
    artifacts: dict[Lane, list[tuple[str, str]]],
) -> None:
    """Record all specified lane artifacts for an iteration."""
    for lane, art_list in artifacts.items():
        for art_type, art_ref in art_list:
            bridge.record_lane_artifact(
                iteration_id=iteration_id,
                lane=lane,
                artifact_type=art_type,
                artifact_ref=art_ref,
            )


# ------------------------------------------------------------------
# 1. Start iteration
# ------------------------------------------------------------------


class TestStartIteration:
    """on_iteration_start with goal + constraints -> verify iteration_id returned,
    kernel state = admitted."""

    def test_returns_iteration_id(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-start-001",
            goal="Validate bridge chain start",
            constraints=["no breaking changes"],
        )
        assert iid.startswith("iter-")
        assert len(iid) == 17  # "iter-" + 12 hex chars

    def test_kernel_state_is_admitted(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-start-002",
            goal="Validate admitted state",
        )
        assert bridge.get_kernel_state(iid) == "admitted"

    def test_constraints_stored_in_metadata(
        self, bridge: IterationBridge, store: KernelStore
    ) -> None:
        bridge.on_iteration_start(
            spec_id="spec-chain-start-003",
            goal="Validate constraints",
            constraints=["p99 < 100ms", "no API changes"],
        )
        import json

        entry = store.get_spec_entry("spec-chain-start-003")
        assert entry is not None
        meta = json.loads(entry["metadata"])
        assert "p99 < 100ms" in meta["constraints"]
        assert "no API changes" in meta["constraints"]


# ------------------------------------------------------------------
# 2. Phase transitions through 5 lanes
# ------------------------------------------------------------------


class TestPhaseTransitions:
    """Full phase transition sequence with correct kernel state at each step."""

    # Expected (from_phase, to_phase, expected_kernel_state) after transition.
    EXPECTED_STATES: list[tuple[str, str, str]] = [
        ("pending", "researching", "researching"),  # Lane: research
        ("researching", "generating_spec", "specifying"),  # Lane: spec_goal
        ("generating_spec", "spec_approval", "specifying"),  # same state, no-op
        ("spec_approval", "decomposing", "specifying"),  # same state, no-op
        ("decomposing", "implementing", "executing"),  # Lane: change
        ("implementing", "reviewing", "verifying"),  # Lane: verification
        ("reviewing", "benchmarking", "verifying"),  # same state, no-op
        ("benchmarking", "learning", "reconciling"),  # Lane: reconcile
    ]

    def test_all_phase_transitions_succeed(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-phases-001",
            goal="Test full phase sequence",
        )
        assert bridge.get_kernel_state(iid) == "admitted"

        for from_p, to_p, expected_state in self.EXPECTED_STATES:
            result = bridge.on_phase_transition(iteration_id=iid, from_phase=from_p, to_phase=to_p)
            assert result is True, f"Transition {from_p} -> {to_p} failed"
            assert bridge.get_kernel_state(iid) == expected_state, (
                f"Expected kernel state {expected_state} after {from_p} -> {to_p}, "
                f"got {bridge.get_kernel_state(iid)}"
            )

    def test_noop_transitions_dont_change_state(self, bridge: IterationBridge) -> None:
        """GENERATING_SPEC->SPEC_APPROVAL, SPEC_APPROVAL->DECOMPOSING,
        REVIEWING->BENCHMARKING should all be no-ops (same kernel state)."""
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-noop-001",
            goal="Test no-op transitions",
        )
        # Advance to specifying
        bridge.on_phase_transition(iteration_id=iid, from_phase="pending", to_phase="researching")
        bridge.on_phase_transition(
            iteration_id=iid, from_phase="researching", to_phase="generating_spec"
        )
        assert bridge.get_kernel_state(iid) == "specifying"

        # These should be no-ops — both map to "specifying"
        r1 = bridge.on_phase_transition(
            iteration_id=iid, from_phase="generating_spec", to_phase="spec_approval"
        )
        assert r1 is True
        assert bridge.get_kernel_state(iid) == "specifying"

        r2 = bridge.on_phase_transition(
            iteration_id=iid, from_phase="spec_approval", to_phase="decomposing"
        )
        assert r2 is True
        assert bridge.get_kernel_state(iid) == "specifying"

        # Advance to verifying
        bridge.on_phase_transition(
            iteration_id=iid, from_phase="decomposing", to_phase="implementing"
        )
        bridge.on_phase_transition(
            iteration_id=iid, from_phase="implementing", to_phase="reviewing"
        )
        assert bridge.get_kernel_state(iid) == "verifying"

        # This should be a no-op — both map to "verifying"
        r3 = bridge.on_phase_transition(
            iteration_id=iid, from_phase="reviewing", to_phase="benchmarking"
        )
        assert r3 is True
        assert bridge.get_kernel_state(iid) == "verifying"


# ------------------------------------------------------------------
# 3. Lane artifact tracking
# ------------------------------------------------------------------


class TestLaneArtifactTracking:
    """Record artifacts for each lane and verify lane_tracker reports correctly."""

    def test_record_artifacts_per_lane(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-lane-001",
            goal="Test lane artifact recording",
        )

        # Record spec_goal lane artifacts
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.spec_goal,
            artifact_type="iteration_spec",
            artifact_ref="ref://spec/iteration_spec",
        )
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.spec_goal,
            artifact_type="milestone_graph",
            artifact_ref="ref://spec/milestone_graph",
        )

        # Record research lane artifacts
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.research,
            artifact_type="research_report",
            artifact_ref="ref://research/report",
        )
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.research,
            artifact_type="evidence_bundle",
            artifact_ref="ref://research/evidence",
        )

        # Record change lane artifacts
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.change,
            artifact_type="diff_bundle",
            artifact_ref="ref://change/diff",
        )
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.change,
            artifact_type="test_patch",
            artifact_ref="ref://change/test_patch",
        )

        # Record verification lane artifacts
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.verification,
            artifact_type="benchmark_run",
            artifact_ref="ref://verify/benchmark",
        )
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.verification,
            artifact_type="verification_verdict",
            artifact_ref="ref://verify/verdict",
        )

        # Record reconcile lane artifacts
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.reconcile,
            artifact_type="reconciliation_record",
            artifact_ref="ref://reconcile/record",
        )
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.reconcile,
            artifact_type="lesson_pack",
            artifact_ref="ref://reconcile/lessons",
        )

        # Verify lane tracker reports all 5 lanes
        all_lanes = bridge.lane_tracker.get_all_lanes(iid)
        assert len(all_lanes) == 5
        assert set(all_lanes.keys()) == {
            "spec_goal",
            "research",
            "change",
            "verification",
            "reconcile",
        }

        # Verify correct artifact counts per lane
        assert len(all_lanes["spec_goal"]) == 2
        assert len(all_lanes["research"]) == 2
        assert len(all_lanes["change"]) == 2
        assert len(all_lanes["verification"]) == 2
        assert len(all_lanes["reconcile"]) == 2

    def test_lane_artifacts_have_correct_types(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-lane-002",
            goal="Validate artifact types",
        )
        _record_all_lane_artifacts(bridge, iid, LANE_ARTIFACTS_SUBSET)

        # Verify specific artifact types per lane
        spec_arts = bridge.lane_tracker.get_lane_artifacts(iid, Lane.spec_goal)
        spec_types = {a.artifact_type for a in spec_arts}
        assert spec_types == {"iteration_spec", "milestone_graph"}

        research_arts = bridge.lane_tracker.get_lane_artifacts(iid, Lane.research)
        research_types = {a.artifact_type for a in research_arts}
        assert research_types == {"research_report", "evidence_bundle"}

        change_arts = bridge.lane_tracker.get_lane_artifacts(iid, Lane.change)
        change_types = {a.artifact_type for a in change_arts}
        assert change_types == {"diff_bundle", "test_patch"}

        verify_arts = bridge.lane_tracker.get_lane_artifacts(iid, Lane.verification)
        verify_types = {a.artifact_type for a in verify_arts}
        assert verify_types == {"benchmark_run", "verification_verdict"}

        reconcile_arts = bridge.lane_tracker.get_lane_artifacts(iid, Lane.reconcile)
        reconcile_types = {a.artifact_type for a in reconcile_arts}
        assert reconcile_types == {"reconciliation_record", "lesson_pack"}


# ------------------------------------------------------------------
# 4. Missing artifacts
# ------------------------------------------------------------------


class TestMissingArtifacts:
    """Check missing_artifacts returns expected gaps for incomplete lanes."""

    def test_missing_artifacts_for_partially_filled_lane(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-missing-001",
            goal="Test missing artifacts",
        )
        # Record only iteration_spec for spec_goal lane
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.spec_goal,
            artifact_type="iteration_spec",
            artifact_ref="ref://spec/iteration_spec",
        )

        missing = bridge.lane_tracker.missing_artifacts(iid, Lane.spec_goal)
        assert missing == frozenset({"milestone_graph", "phase_contracts"})

    def test_missing_artifacts_for_empty_lane(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-missing-002",
            goal="Test fully missing lane",
        )
        missing = bridge.lane_tracker.missing_artifacts(iid, Lane.research)
        assert missing == LANE_EXPECTED_ARTIFACTS[Lane.research]

    def test_no_missing_artifacts_when_lane_complete(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-missing-003",
            goal="Test complete lane",
        )
        # Record all expected artifacts for spec_goal
        for art_type in LANE_EXPECTED_ARTIFACTS[Lane.spec_goal]:
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.spec_goal,
                artifact_type=art_type,
                artifact_ref=f"ref://spec/{art_type}",
            )
        missing = bridge.lane_tracker.missing_artifacts(iid, Lane.spec_goal)
        assert missing == frozenset()

    def test_missing_artifacts_across_all_lanes(self, bridge: IterationBridge) -> None:
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-missing-004",
            goal="Test partial coverage across lanes",
        )
        # Record partial artifacts
        _record_all_lane_artifacts(bridge, iid, LANE_ARTIFACTS_SUBSET)

        # Check that each lane has the correct missing artifacts
        summary = bridge.lane_tracker.summary(iid)
        assert summary["spec_goal"]["complete"] is False
        assert "phase_contracts" in summary["spec_goal"]["missing"]

        assert summary["research"]["complete"] is False
        assert "repo_diagnosis" in summary["research"]["missing"]

        assert summary["change"]["complete"] is False
        assert "migration_notes" in summary["change"]["missing"]

        assert summary["verification"]["complete"] is False
        assert "replay_result" in summary["verification"]["missing"]

        assert summary["reconcile"]["complete"] is False
        assert "template_update" in summary["reconcile"]["missing"]
        assert "next_iteration_seed" in summary["reconcile"]["missing"]


# ------------------------------------------------------------------
# 5. Completion with promotion
# ------------------------------------------------------------------


class TestCompletionWithPromotion:
    """on_iteration_complete with all gate conditions met -> promoted=True."""

    def test_promoted_with_all_gate_conditions(self, bridge: IterationBridge) -> None:
        iid = _advance_to_reconciling(bridge, "spec-chain-promote-001")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"test_pass_rate": 0.98, "coverage": 0.92},
            reconciliation_summary="All checks passed, no regressions.",
            replay_stable=True,
        )
        assert verdict["result"] == "accepted"
        assert verdict["promoted"] is True
        assert bridge.get_kernel_state(iid) == "accepted"

    def test_promoted_includes_benchmark_results(self, bridge: IterationBridge) -> None:
        iid = _advance_to_reconciling(bridge, "spec-chain-promote-002")
        benchmark = {"test_pass_rate": 1.0, "lint_score": 100, "coverage": 0.95}
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results=benchmark,
            reconciliation_summary="Perfect run.",
            replay_stable=True,
        )
        assert verdict["benchmark_results"] == benchmark

    def test_promoted_with_lessons(self, bridge: IterationBridge, store: KernelStore) -> None:
        iid = _advance_to_reconciling(bridge, "spec-chain-promote-003")
        store.create_lesson("lesson-promo-1", iid, "playbook", "Always run lint first")
        store.create_lesson("lesson-promo-2", iid, "pattern", "Use composition over inheritance")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 95},
            reconciliation_summary="Good outcome with lessons.",
            replay_stable=True,
        )
        assert verdict["promoted"] is True
        assert verdict["lesson_pack"] is not None
        assert "Always run lint first" in verdict["lesson_pack"]["playbook_updates"]
        assert "Use composition over inheritance" in verdict["lesson_pack"]["pattern_updates"]


# ------------------------------------------------------------------
# 6. Completion with rejection
# ------------------------------------------------------------------


class TestCompletionWithRejection:
    """on_iteration_complete without required gate conditions -> promoted=False."""

    def test_rejected_without_benchmark_results(self, bridge: IterationBridge) -> None:
        iid = _advance_to_reconciling(bridge, "spec-chain-reject-001")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            # No benchmark_results provided (defaults to None -> {})
            reconciliation_summary="Summary present.",
            replay_stable=True,
        )
        assert verdict["result"] == "rejected"
        assert verdict["promoted"] is False
        assert bridge.get_kernel_state(iid) == "rejected"

    def test_rejected_without_reconciliation_summary(self, bridge: IterationBridge) -> None:
        iid = _advance_to_reconciling(bridge, "spec-chain-reject-002")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 100},
            reconciliation_summary="",
            replay_stable=True,
        )
        assert verdict["result"] == "rejected"
        assert verdict["promoted"] is False

    def test_rejected_without_replay_stable(self, bridge: IterationBridge) -> None:
        iid = _advance_to_reconciling(bridge, "spec-chain-reject-003")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 100},
            reconciliation_summary="Summary present.",
            # replay_stable defaults to False
        )
        assert verdict["result"] == "rejected"
        assert verdict["promoted"] is False

    def test_rejected_with_unexplained_drift(self, bridge: IterationBridge) -> None:
        iid = _advance_to_reconciling(bridge, "spec-chain-reject-004")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 100},
            reconciliation_summary="Summary present.",
            replay_stable=True,
            unexplained_drift=["metric X regressed 5%"],
        )
        assert verdict["result"] == "rejected"
        assert verdict["promoted"] is False

    def test_rejected_verdict_has_no_lesson_pack(self, bridge: IterationBridge) -> None:
        iid = _advance_to_reconciling(bridge, "spec-chain-reject-005")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={},
            reconciliation_summary="",
        )
        assert verdict["promoted"] is False
        assert "lesson_pack" not in verdict


# ------------------------------------------------------------------
# 7. Verdict includes lane artifacts
# ------------------------------------------------------------------


class TestVerdictLaneArtifacts:
    """BridgeVerdict.to_dict() includes lane_artifacts."""

    def test_verdict_to_dict_includes_lane_artifacts_key(self) -> None:
        verdict = BridgeVerdict(
            iteration_id="iter-test-dict-001",
            result="accepted",
            promoted=True,
            lane_artifacts={
                "spec_goal": [{"artifact_type": "iteration_spec", "artifact_ref": "ref-001"}],
                "research": [{"artifact_type": "evidence_bundle", "artifact_ref": "ref-002"}],
            },
        )
        d = verdict.to_dict()
        assert "lane_artifacts" in d
        assert "spec_goal" in d["lane_artifacts"]
        assert "research" in d["lane_artifacts"]
        assert d["lane_artifacts"]["spec_goal"][0]["artifact_type"] == "iteration_spec"

    def test_verdict_from_complete_has_lane_artifacts(self, bridge: IterationBridge) -> None:
        iid = _advance_to_reconciling(bridge, "spec-chain-verdict-001")

        # Record some lane artifacts before completion
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.spec_goal,
            artifact_type="iteration_spec",
            artifact_ref="ref://spec/iteration_spec",
        )
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.change,
            artifact_type="diff_bundle",
            artifact_ref="ref://change/diff",
        )
        bridge.record_lane_artifact(
            iteration_id=iid,
            lane=Lane.verification,
            artifact_type="benchmark_run",
            artifact_ref="ref://verify/benchmark",
        )

        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 99},
            reconciliation_summary="Lanes tracked.",
            replay_stable=True,
        )
        la = verdict["lane_artifacts"]
        assert "spec_goal" in la
        assert "change" in la
        assert "verification" in la
        assert la["spec_goal"][0]["artifact_type"] == "iteration_spec"
        assert la["change"][0]["artifact_type"] == "diff_bundle"
        assert la["verification"][0]["artifact_type"] == "benchmark_run"

    def test_verdict_empty_lane_artifacts_when_none_recorded(self, bridge: IterationBridge) -> None:
        iid = _advance_to_reconciling(bridge, "spec-chain-verdict-002")
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"score": 99},
            reconciliation_summary="No artifacts.",
            replay_stable=True,
        )
        assert verdict["lane_artifacts"] == {}


# ------------------------------------------------------------------
# 8. Full pipeline — start -> transitions -> artifacts -> complete
# ------------------------------------------------------------------


class TestFullPipelineChain:
    """Complete end-to-end: start -> all transitions -> all lane artifacts -> complete."""

    def test_full_lifecycle_promoted(self, bridge: IterationBridge, store: KernelStore) -> None:
        # 1. Start iteration
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-full-001",
            goal="Full pipeline integration test with lane tracking",
            constraints=["no regressions", "maintain 80% coverage"],
        )
        assert bridge.get_kernel_state(iid) == "admitted"

        # 2. Record spec_goal lane artifacts (produced during planning)
        for art_type in LANE_EXPECTED_ARTIFACTS[Lane.spec_goal]:
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.spec_goal,
                artifact_type=art_type,
                artifact_ref=f"ref://spec/{art_type}",
            )

        # 3. Phase: PENDING -> RESEARCHING
        assert bridge.on_phase_transition(
            iteration_id=iid, from_phase="pending", to_phase="researching"
        )
        assert bridge.get_kernel_state(iid) == "researching"

        # Record research lane artifacts
        for art_type in LANE_EXPECTED_ARTIFACTS[Lane.research]:
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.research,
                artifact_type=art_type,
                artifact_ref=f"ref://research/{art_type}",
            )

        # 4. Phase: RESEARCHING -> GENERATING_SPEC
        assert bridge.on_phase_transition(
            iteration_id=iid, from_phase="researching", to_phase="generating_spec"
        )
        assert bridge.get_kernel_state(iid) == "specifying"

        # 5. Phase: GENERATING_SPEC -> SPEC_APPROVAL (no-op, same kernel state)
        assert bridge.on_phase_transition(
            iteration_id=iid, from_phase="generating_spec", to_phase="spec_approval"
        )
        assert bridge.get_kernel_state(iid) == "specifying"

        # 6. Phase: SPEC_APPROVAL -> DECOMPOSING (no-op, same kernel state)
        assert bridge.on_phase_transition(
            iteration_id=iid, from_phase="spec_approval", to_phase="decomposing"
        )
        assert bridge.get_kernel_state(iid) == "specifying"

        # 7. Phase: DECOMPOSING -> IMPLEMENTING
        assert bridge.on_phase_transition(
            iteration_id=iid, from_phase="decomposing", to_phase="implementing"
        )
        assert bridge.get_kernel_state(iid) == "executing"

        # Record change lane artifacts
        for art_type in LANE_EXPECTED_ARTIFACTS[Lane.change]:
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.change,
                artifact_type=art_type,
                artifact_ref=f"ref://change/{art_type}",
            )

        # 8. Phase: IMPLEMENTING -> REVIEWING
        assert bridge.on_phase_transition(
            iteration_id=iid, from_phase="implementing", to_phase="reviewing"
        )
        assert bridge.get_kernel_state(iid) == "verifying"

        # Record verification lane artifacts
        for art_type in LANE_EXPECTED_ARTIFACTS[Lane.verification]:
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.verification,
                artifact_type=art_type,
                artifact_ref=f"ref://verify/{art_type}",
            )

        # 9. Phase: REVIEWING -> BENCHMARKING (no-op, same kernel state)
        assert bridge.on_phase_transition(
            iteration_id=iid, from_phase="reviewing", to_phase="benchmarking"
        )
        assert bridge.get_kernel_state(iid) == "verifying"

        # 10. Phase: BENCHMARKING -> LEARNING
        assert bridge.on_phase_transition(
            iteration_id=iid, from_phase="benchmarking", to_phase="learning"
        )
        assert bridge.get_kernel_state(iid) == "reconciling"

        # Record reconcile lane artifacts
        for art_type in LANE_EXPECTED_ARTIFACTS[Lane.reconcile]:
            bridge.record_lane_artifact(
                iteration_id=iid,
                lane=Lane.reconcile,
                artifact_type=art_type,
                artifact_ref=f"ref://reconcile/{art_type}",
            )

        # 11. Verify all lanes complete
        assert bridge.lane_tracker.all_lanes_complete(iid) is True
        summary = bridge.lane_tracker.summary(iid)
        for lane_name, lane_data in summary.items():
            assert lane_data["complete"] is True, f"Lane {lane_name} not complete"
            assert lane_data["missing"] == []

        # 12. Add lessons for the iteration
        store.create_lesson("lesson-full-1", iid, "playbook", "Always validate inputs")
        store.create_lesson("lesson-full-2", iid, "template", "Use bridge pattern for FSM")
        store.create_lesson("lesson-full-3", iid, "pattern", "5-lane artifact tracking")

        # 13. Complete with promotion
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={
                "test_pass_rate": 1.0,
                "lint_score": 100,
                "coverage": 0.95,
            },
            reconciliation_summary="All 5 lanes complete. No regressions.",
            replay_stable=True,
        )

        # 14. Verify final state
        assert verdict["result"] == "accepted"
        assert verdict["promoted"] is True
        assert bridge.get_kernel_state(iid) == "accepted"

        # 15. Verify lessons in verdict
        lp = verdict["lesson_pack"]
        assert lp is not None
        assert "Always validate inputs" in lp["playbook_updates"]
        assert "Use bridge pattern for FSM" in lp["template_updates"]
        assert "5-lane artifact tracking" in lp["pattern_updates"]

        # 16. Verify lane artifacts in verdict
        la = verdict["lane_artifacts"]
        assert len(la) == 5
        assert len(la["spec_goal"]) == len(LANE_EXPECTED_ARTIFACTS[Lane.spec_goal])
        assert len(la["research"]) == len(LANE_EXPECTED_ARTIFACTS[Lane.research])
        assert len(la["change"]) == len(LANE_EXPECTED_ARTIFACTS[Lane.change])
        assert len(la["verification"]) == len(LANE_EXPECTED_ARTIFACTS[Lane.verification])
        assert len(la["reconcile"]) == len(LANE_EXPECTED_ARTIFACTS[Lane.reconcile])

    def test_full_lifecycle_rejected(self, bridge: IterationBridge) -> None:
        """Full pipeline ending in rejection (missing benchmark)."""
        iid = bridge.on_iteration_start(
            spec_id="spec-chain-full-002",
            goal="Full pipeline rejection test",
        )

        # Advance through all phases
        for from_p, to_p in FULL_PHASE_SEQUENCE:
            bridge.on_phase_transition(iteration_id=iid, from_phase=from_p, to_phase=to_p)

        # Record partial artifacts
        _record_all_lane_artifacts(bridge, iid, LANE_ARTIFACTS_SUBSET)

        # Complete without benchmark -> rejection
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={},
            reconciliation_summary="Missing benchmark data.",
        )

        assert verdict["result"] == "rejected"
        assert verdict["promoted"] is False
        assert bridge.get_kernel_state(iid) == "rejected"
        # Lane artifacts should still be present in verdict even on rejection
        assert len(verdict["lane_artifacts"]) == 5

    def test_full_lifecycle_with_followup_seed(
        self, bridge: IterationBridge, store: KernelStore
    ) -> None:
        """Full pipeline with follow-up seed generation."""
        import json

        iid = bridge.on_iteration_start(
            spec_id="spec-chain-full-003",
            goal="Full pipeline with follow-up seed",
        )

        # Advance through all phases
        for from_p, to_p in FULL_PHASE_SEQUENCE:
            bridge.on_phase_transition(iteration_id=iid, from_phase=from_p, to_phase=to_p)

        # Inject a next_seed into metadata
        entry = store.get_spec_entry("spec-chain-full-003")
        assert entry is not None
        meta = json.loads(entry["metadata"]) if entry["metadata"] else {}
        meta["next_seed"] = "Optimize cache invalidation strategy"
        store.update_spec_status("spec-chain-full-003", entry["status"], metadata=meta)

        # Complete with promotion
        verdict = bridge.on_iteration_complete(
            iteration_id=iid,
            benchmark_results={"pass_rate": 1.0},
            reconciliation_summary="Passed with follow-up.",
            replay_stable=True,
        )

        assert verdict["result"] == "accepted_with_followups"
        assert verdict["promoted"] is True
        assert verdict["next_seed_goal"] == "Optimize cache invalidation strategy"
