"""Tests for IterationKernel — self-iteration meta-program lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.execution.self_modify.iteration_kernel import (
    _ALLOWED_RISK_BANDS,
    ITERATION_TRANSITIONS,
    MAX_SEED_CHAIN_DEPTH,
    AdmissionError,
    InvalidTransitionError,
    IterationKernel,
    IterationLessonPack,
    IterationSpec,
    IterationState,
    PolicyRejectionError,
)
from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture()
def kernel(store: KernelStore) -> IterationKernel:
    return IterationKernel(store)


@pytest.fixture()
def sample_spec() -> IterationSpec:
    return IterationSpec(
        spec_id="spec-test-001",
        goal="Improve memory retrieval latency",
        constraints=["no breaking changes"],
        success_criteria=["p99 < 50ms"],
        change_units=["src/hermit/kernel/context/memory/"],
        eval_requirements={"benchmark": "memory_latency"},
        risk_budget={"band": "medium"},
        max_rounds=3,
    )


@pytest.fixture()
def minimal_spec() -> IterationSpec:
    """A spec with only the goal set — for backward-compatibility tests."""
    return IterationSpec(
        spec_id="spec-minimal-001",
        goal="Quick fix",
    )


# ------------------------------------------------------------------
# Admission validation
# ------------------------------------------------------------------


class TestValidateAdmission:
    def test_fully_specified_spec_passes(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        errors = kernel.validate_admission(sample_spec)
        assert errors == []

    def test_empty_goal_fails(self, kernel: IterationKernel) -> None:
        spec = IterationSpec(spec_id="spec-v1", goal="")
        errors = kernel.validate_admission(spec)
        assert any("goal" in e for e in errors)

    def test_missing_success_criteria_fails(self, kernel: IterationKernel) -> None:
        spec = IterationSpec(
            spec_id="spec-v2",
            goal="Some goal",
            change_units=["src/"],
            eval_requirements={"bench": True},
        )
        errors = kernel.validate_admission(spec)
        assert any("success_criteria" in e for e in errors)

    def test_missing_eval_requirements_fails(self, kernel: IterationKernel) -> None:
        spec = IterationSpec(
            spec_id="spec-v3",
            goal="Some goal",
            success_criteria=["pass"],
            change_units=["src/"],
        )
        errors = kernel.validate_admission(spec)
        assert any("eval_requirements" in e for e in errors)

    def test_missing_change_units_fails(self, kernel: IterationKernel) -> None:
        spec = IterationSpec(
            spec_id="spec-v4",
            goal="Some goal",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
        )
        errors = kernel.validate_admission(spec)
        assert any("change_units" in e for e in errors)

    def test_invalid_risk_band_fails(self, kernel: IterationKernel) -> None:
        spec = IterationSpec(
            spec_id="spec-v5",
            goal="Some goal",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
            change_units=["src/"],
            risk_budget={"band": "critical"},
        )
        errors = kernel.validate_admission(spec)
        assert any("risk_budget.band" in e for e in errors)

    def test_valid_risk_bands_accepted(self, kernel: IterationKernel) -> None:
        for band in _ALLOWED_RISK_BANDS:
            spec = IterationSpec(
                spec_id=f"spec-band-{band}",
                goal="Goal",
                success_criteria=["pass"],
                eval_requirements={"bench": True},
                change_units=["src/"],
                risk_budget={"band": band},
            )
            errors = kernel.validate_admission(spec)
            assert errors == [], f"Band '{band}' should be accepted"

    def test_empty_risk_band_is_ok(self, kernel: IterationKernel) -> None:
        """When risk_budget has no 'band' key, it's not an error."""
        spec = IterationSpec(
            spec_id="spec-no-band",
            goal="Goal",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
            change_units=["src/"],
            risk_budget={},
        )
        errors = kernel.validate_admission(spec)
        assert errors == []

    def test_multiple_errors_returned(self, kernel: IterationKernel) -> None:
        spec = IterationSpec(spec_id="spec-multi", goal="Goal")
        errors = kernel.validate_admission(spec)
        # Missing success_criteria, eval_requirements, and change_units.
        assert len(errors) == 3


# ------------------------------------------------------------------
# Admission
# ------------------------------------------------------------------


class TestAdmitIteration:
    def test_admits_valid_spec(self, kernel: IterationKernel, sample_spec: IterationSpec) -> None:
        iteration_id = kernel.admit_iteration(sample_spec)
        assert iteration_id.startswith("iter-")
        assert len(iteration_id) == 17  # "iter-" + 12 hex chars

    def test_admitted_state_is_admitted(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iteration_id = kernel.admit_iteration(sample_spec)
        state = kernel.get_state(iteration_id)
        assert state == IterationState.admitted

    def test_rejects_empty_goal(self, kernel: IterationKernel) -> None:
        spec = IterationSpec(spec_id="spec-empty", goal="")
        with pytest.raises(ValueError, match="non-empty"):
            kernel.admit_iteration(spec)

    def test_rejects_whitespace_goal(self, kernel: IterationKernel) -> None:
        spec = IterationSpec(spec_id="spec-ws", goal="   ")
        with pytest.raises(ValueError, match="non-empty"):
            kernel.admit_iteration(spec)

    def test_spec_stored_in_backlog(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        kernel.admit_iteration(sample_spec)
        entry = store.get_spec_entry(sample_spec.spec_id)
        assert entry is not None
        assert entry["goal"] == sample_spec.goal
        assert entry["source"] == "self-iterate"

    def test_non_strict_admits_incomplete_spec(
        self, kernel: IterationKernel, minimal_spec: IterationSpec
    ) -> None:
        """Default (strict=False) admits even if admission checks have warnings."""
        iteration_id = kernel.admit_iteration(minimal_spec)
        assert iteration_id.startswith("iter-")
        assert kernel.get_state(iteration_id) == IterationState.admitted

    def test_strict_rejects_incomplete_spec(self, kernel: IterationKernel) -> None:
        """strict=True raises AdmissionError when checks fail."""
        spec = IterationSpec(spec_id="spec-strict", goal="Some goal")
        with pytest.raises(AdmissionError, match="admission failed"):
            kernel.admit_iteration(spec, strict=True)

    def test_strict_accepts_complete_spec(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        """strict=True passes when all admission checks are satisfied."""
        iteration_id = kernel.admit_iteration(sample_spec, strict=True)
        assert iteration_id.startswith("iter-")

    def test_strict_rejects_invalid_risk_band(self, kernel: IterationKernel) -> None:
        spec = IterationSpec(
            spec_id="spec-bad-band",
            goal="Goal",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
            change_units=["src/"],
            risk_budget={"band": "catastrophic"},
        )
        with pytest.raises(AdmissionError, match=r"risk_budget\.band"):
            kernel.admit_iteration(spec, strict=True)


# ------------------------------------------------------------------
# State queries
# ------------------------------------------------------------------


class TestGetState:
    def test_returns_state_for_existing_iteration(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iteration_id = kernel.admit_iteration(sample_spec)
        assert kernel.get_state(iteration_id) == IterationState.admitted

    def test_raises_for_missing_iteration(self, kernel: IterationKernel) -> None:
        with pytest.raises(KeyError, match="not found"):
            kernel.get_state("iter-nonexistent")


# ------------------------------------------------------------------
# State transitions — valid
# ------------------------------------------------------------------


class TestValidTransitions:
    def test_admitted_to_researching(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        assert kernel.transition(iid, IterationState.researching) is True
        assert kernel.get_state(iid) == IterationState.researching

    def test_researching_to_specifying(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.researching)
        assert kernel.transition(iid, IterationState.specifying) is True
        assert kernel.get_state(iid) == IterationState.specifying

    def test_specifying_to_executing(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.researching)
        kernel.transition(iid, IterationState.specifying)
        assert kernel.transition(iid, IterationState.executing) is True
        assert kernel.get_state(iid) == IterationState.executing

    def test_executing_to_verifying(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.researching)
        kernel.transition(iid, IterationState.specifying)
        kernel.transition(iid, IterationState.executing)
        assert kernel.transition(iid, IterationState.verifying) is True
        assert kernel.get_state(iid) == IterationState.verifying

    def test_verifying_to_reconciling(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.researching)
        kernel.transition(iid, IterationState.specifying)
        kernel.transition(iid, IterationState.executing)
        kernel.transition(iid, IterationState.verifying)
        assert kernel.transition(iid, IterationState.reconciling) is True
        assert kernel.get_state(iid) == IterationState.reconciling

    def test_reconciling_to_accepted(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.researching)
        kernel.transition(iid, IterationState.specifying)
        kernel.transition(iid, IterationState.executing)
        kernel.transition(iid, IterationState.verifying)
        kernel.transition(iid, IterationState.reconciling)
        assert kernel.transition(iid, IterationState.accepted) is True
        assert kernel.get_state(iid) == IterationState.accepted

    def test_reconciling_to_rejected(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.researching)
        kernel.transition(iid, IterationState.specifying)
        kernel.transition(iid, IterationState.executing)
        kernel.transition(iid, IterationState.verifying)
        kernel.transition(iid, IterationState.reconciling)
        assert kernel.transition(iid, IterationState.rejected) is True
        assert kernel.get_state(iid) == IterationState.rejected

    def test_admitted_to_rejected(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        assert kernel.transition(iid, IterationState.rejected) is True
        assert kernel.get_state(iid) == IterationState.rejected

    def test_park_from_researching(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.researching)
        assert kernel.transition(iid, IterationState.parked) is True
        assert kernel.get_state(iid) == IterationState.parked

    def test_unpark_to_researching(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.researching)
        kernel.transition(iid, IterationState.parked)
        assert kernel.transition(iid, IterationState.researching) is True
        assert kernel.get_state(iid) == IterationState.researching


# ------------------------------------------------------------------
# State transitions — invalid
# ------------------------------------------------------------------


class TestInvalidTransitions:
    def test_cannot_skip_phases(self, kernel: IterationKernel, sample_spec: IterationSpec) -> None:
        iid = kernel.admit_iteration(sample_spec)
        with pytest.raises(InvalidTransitionError, match="Invalid transition"):
            kernel.transition(iid, IterationState.executing)

    def test_cannot_go_backwards(self, kernel: IterationKernel, sample_spec: IterationSpec) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.researching)
        kernel.transition(iid, IterationState.specifying)
        with pytest.raises(InvalidTransitionError, match="Invalid transition"):
            kernel.transition(iid, IterationState.researching)

    def test_cannot_transition_from_accepted(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.researching)
        kernel.transition(iid, IterationState.specifying)
        kernel.transition(iid, IterationState.executing)
        kernel.transition(iid, IterationState.verifying)
        kernel.transition(iid, IterationState.reconciling)
        kernel.transition(iid, IterationState.accepted)
        # accepted now transitions to pr_created or rejected, not researching
        with pytest.raises(InvalidTransitionError, match="Invalid transition"):
            kernel.transition(iid, IterationState.researching)

    def test_cannot_transition_from_rejected(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.rejected)
        with pytest.raises(InvalidTransitionError, match="terminal state"):
            kernel.transition(iid, IterationState.admitted)

    def test_raises_for_missing_iteration(self, kernel: IterationKernel) -> None:
        with pytest.raises(KeyError, match="not found"):
            kernel.transition("iter-ghost", IterationState.researching)


# ------------------------------------------------------------------
# Promotion gate
# ------------------------------------------------------------------


class TestCheckPromotionGate:
    def _advance_to_reconciling(self, kernel: IterationKernel, spec: IterationSpec) -> str:
        """Helper to advance an iteration through the full pipeline to reconciling."""
        iid = kernel.admit_iteration(spec)
        kernel.transition(iid, IterationState.researching)
        kernel.transition(iid, IterationState.specifying)
        kernel.transition(iid, IterationState.executing)
        kernel.transition(iid, IterationState.verifying)
        kernel.transition(iid, IterationState.reconciling)
        return iid

    def _set_promotion_metadata(
        self,
        store: KernelStore,
        spec_id: str,
        *,
        benchmark_results: dict | None = None,
        reconciliation_summary: str = "",
        replay_stable: bool | None = None,
        unexplained_drift: list | None = None,
    ) -> None:
        """Inject promotion-relevant metadata into a spec entry."""
        import json

        entry = store.get_spec_entry(spec_id)
        assert entry is not None
        meta = json.loads(entry["metadata"]) if entry["metadata"] else {}
        if benchmark_results is not None:
            meta["benchmark_results"] = benchmark_results
        if reconciliation_summary:
            meta["reconciliation_summary"] = reconciliation_summary
        if replay_stable is not None:
            meta["replay_stable"] = replay_stable
        if unexplained_drift is not None:
            meta["unexplained_drift"] = unexplained_drift
        store.update_spec_status(
            spec_id,
            IterationState.reconciling.value,
            metadata=meta,
        )

    def test_passes_with_all_gate_conditions_met(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        iid = self._advance_to_reconciling(kernel, sample_spec)
        self._set_promotion_metadata(
            store,
            sample_spec.spec_id,
            benchmark_results={"test_pass_rate": 0.98, "lint_score": 100},
            reconciliation_summary="All checks passed, no regressions.",
            replay_stable=True,
        )
        assert kernel.check_promotion_gate(iid) is True

    def test_fails_without_benchmark(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        iid = self._advance_to_reconciling(kernel, sample_spec)
        self._set_promotion_metadata(
            store,
            sample_spec.spec_id,
            reconciliation_summary="Summary present.",
            replay_stable=True,
        )
        assert kernel.check_promotion_gate(iid) is False

    def test_fails_without_summary(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        iid = self._advance_to_reconciling(kernel, sample_spec)
        self._set_promotion_metadata(
            store,
            sample_spec.spec_id,
            benchmark_results={"score": 95},
            replay_stable=True,
        )
        assert kernel.check_promotion_gate(iid) is False

    def test_fails_without_replay_stable(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        """Promotion requires replay_stable=True."""
        iid = self._advance_to_reconciling(kernel, sample_spec)
        self._set_promotion_metadata(
            store,
            sample_spec.spec_id,
            benchmark_results={"score": 95},
            reconciliation_summary="Good.",
            # replay_stable not set
        )
        assert kernel.check_promotion_gate(iid) is False

    def test_fails_with_replay_stable_false(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        iid = self._advance_to_reconciling(kernel, sample_spec)
        self._set_promotion_metadata(
            store,
            sample_spec.spec_id,
            benchmark_results={"score": 95},
            reconciliation_summary="Good.",
            replay_stable=False,
        )
        assert kernel.check_promotion_gate(iid) is False

    def test_fails_with_unexplained_drift(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        """Promotion blocked when unexplained_drift contains entries."""
        iid = self._advance_to_reconciling(kernel, sample_spec)
        self._set_promotion_metadata(
            store,
            sample_spec.spec_id,
            benchmark_results={"score": 95},
            reconciliation_summary="Some summary.",
            replay_stable=True,
            unexplained_drift=["metric X regressed 5%"],
        )
        assert kernel.check_promotion_gate(iid) is False

    def test_passes_with_empty_unexplained_drift(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        """Empty unexplained_drift list is acceptable."""
        iid = self._advance_to_reconciling(kernel, sample_spec)
        self._set_promotion_metadata(
            store,
            sample_spec.spec_id,
            benchmark_results={"score": 100},
            reconciliation_summary="All clear.",
            replay_stable=True,
            unexplained_drift=[],
        )
        assert kernel.check_promotion_gate(iid) is True

    def test_fails_when_not_reconciling(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        kernel.transition(iid, IterationState.researching)
        assert kernel.check_promotion_gate(iid) is False

    def test_raises_for_missing_iteration(self, kernel: IterationKernel) -> None:
        with pytest.raises(KeyError, match="not found"):
            kernel.check_promotion_gate("iter-missing")


# ------------------------------------------------------------------
# Lesson extraction
# ------------------------------------------------------------------


class TestExtractLessons:
    def test_extracts_empty_pack_when_no_lessons(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        pack = kernel.extract_lessons(iid)
        assert isinstance(pack, IterationLessonPack)
        assert pack.iteration_id == iid
        assert pack.playbook_updates == []
        assert pack.template_updates == []
        assert pack.pattern_updates == []

    def test_categorizes_lessons_correctly(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        store.create_lesson("l1", iid, "playbook", "Always run lint first")
        store.create_lesson("l2", iid, "template", "Use dataclass for records")
        store.create_lesson("l3", iid, "pattern", "Prefer composition over inheritance")
        store.create_lesson("l4", iid, "process", "Review before merge")
        store.create_lesson("l5", iid, "other", "Miscellaneous insight")

        pack = kernel.extract_lessons(iid)
        assert "Always run lint first" in pack.playbook_updates
        assert "Review before merge" in pack.playbook_updates
        assert "Use dataclass for records" in pack.template_updates
        assert "Prefer composition over inheritance" in pack.pattern_updates
        assert "Miscellaneous insight" in pack.pattern_updates  # uncategorized -> pattern

    def test_raises_for_missing_iteration(self, kernel: IterationKernel) -> None:
        with pytest.raises(KeyError, match="not found"):
            kernel.extract_lessons("iter-missing")


# ------------------------------------------------------------------
# Next-seed generation
# ------------------------------------------------------------------


class TestGenerateNextSeed:
    def test_returns_none_when_no_seed(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        assert kernel.generate_next_seed(iid) is None

    def test_generates_seed_from_metadata(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        import json

        entry = store.get_spec_entry(sample_spec.spec_id)
        assert entry is not None
        meta = json.loads(entry["metadata"]) if entry["metadata"] else {}
        meta["next_seed"] = "Optimize cache invalidation"
        store.update_spec_status(
            sample_spec.spec_id,
            IterationState.admitted.value,
            metadata=meta,
        )

        seed = kernel.generate_next_seed(iid)
        assert seed is not None
        assert seed.goal == "Optimize cache invalidation"
        assert seed.spec_id.startswith("spec-")
        assert seed.constraints == sample_spec.constraints
        assert seed.success_criteria == sample_spec.success_criteria

    def test_returns_none_for_empty_seed(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        iid = kernel.admit_iteration(sample_spec)
        import json

        entry = store.get_spec_entry(sample_spec.spec_id)
        assert entry is not None
        meta = json.loads(entry["metadata"]) if entry["metadata"] else {}
        meta["next_seed"] = "   "
        store.update_spec_status(
            sample_spec.spec_id,
            IterationState.admitted.value,
            metadata=meta,
        )
        assert kernel.generate_next_seed(iid) is None

    def test_raises_for_missing_iteration(self, kernel: IterationKernel) -> None:
        with pytest.raises(KeyError, match="not found"):
            kernel.generate_next_seed("iter-missing")


# ------------------------------------------------------------------
# Transition coverage — verify all defined transitions are accounted for
# ------------------------------------------------------------------


class TestTransitionMap:
    def test_all_non_terminal_states_have_transitions(self) -> None:
        """Every non-terminal state must appear in ITERATION_TRANSITIONS.

        Terminal states may also appear if they have an explicit empty
        transition set (e.g. merge_approved).
        """
        non_terminal = {s for s in IterationState if s not in {"merge_approved", "rejected"}}
        assert non_terminal.issubset(set(ITERATION_TRANSITIONS.keys()))

    def test_terminal_states_have_no_transitions(self) -> None:
        """Terminal states must have no outgoing transitions."""
        assert IterationState.merge_approved not in ITERATION_TRANSITIONS or (
            ITERATION_TRANSITIONS.get(IterationState.merge_approved) == set()
        )
        assert IterationState.rejected not in ITERATION_TRANSITIONS


# ------------------------------------------------------------------
# Seed chain loop detection
# ------------------------------------------------------------------


class TestSeedChainLoopDetection:
    def test_admits_spec_without_parent(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        """Specs with no parent_iteration_id should be admitted normally."""
        iid = kernel.admit_iteration(sample_spec)
        assert iid.startswith("iter-")

    def test_admits_spec_with_valid_parent(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        """A child spec referencing an existing parent should be admitted."""
        parent_spec = IterationSpec(
            spec_id="spec-parent-001",
            goal="Parent iteration",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
            change_units=["src/"],
        )
        parent_iid = kernel.admit_iteration(parent_spec)

        child_spec = IterationSpec(
            spec_id="spec-child-001",
            goal="Child iteration",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
            change_units=["src/"],
            parent_iteration_id=parent_iid,
        )
        child_iid = kernel.admit_iteration(child_spec)
        assert child_iid.startswith("iter-")

    def test_rejects_circular_seed_chain(self, kernel: IterationKernel, store: KernelStore) -> None:
        """A spec whose parent_iteration_id matches its own spec_id should
        be rejected as circular."""
        spec = IterationSpec(
            spec_id="spec-circle",
            goal="I reference myself",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
            change_units=["src/"],
            parent_iteration_id="spec-circle",
        )
        with pytest.raises(AdmissionError, match=r"[Cc]ircular"):
            kernel.admit_iteration(spec)

    def test_rejects_indirect_cycle(self, kernel: IterationKernel, store: KernelStore) -> None:
        """A → B → A should be detected as a cycle."""
        import json

        spec_a = IterationSpec(
            spec_id="spec-a",
            goal="Iteration A",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
            change_units=["src/"],
        )
        iid_a = kernel.admit_iteration(spec_a)

        spec_b = IterationSpec(
            spec_id="spec-b",
            goal="Iteration B",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
            change_units=["src/"],
            parent_iteration_id=iid_a,
        )
        iid_b = kernel.admit_iteration(spec_b)

        # Now try to admit spec-c that references iid_b, but give it
        # spec_id = spec_a's iteration_id to simulate A → B → A cycle.
        # Instead, we create a real cycle: C references B, and we manually
        # patch B's parent to point at C's spec_id.
        # Simpler approach: create spec-c with parent=iid_b, and then
        # check that a deeper cycle A→B→C→A is caught.

        # Patch spec-a's metadata to set parent_iteration_id = iid_b
        # (creating A → B → A loop when C tries parent=iid_a).
        entry = store.get_spec_entry("spec-a")
        assert entry is not None
        meta = json.loads(entry["metadata"]) if entry["metadata"] else {}
        meta["parent_iteration_id"] = iid_b
        store.update_spec_status("spec-a", IterationState.admitted.value, metadata=meta)

        # Now admit spec-c with parent=iid_a. Chain: C → A → B → A (cycle)
        spec_c = IterationSpec(
            spec_id="spec-c",
            goal="Iteration C",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
            change_units=["src/"],
            parent_iteration_id=iid_a,
        )
        with pytest.raises(AdmissionError, match=r"[Cc]ircular"):
            kernel.admit_iteration(spec_c)

    def test_rejects_deep_chain_exceeding_max_depth(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        """A chain deeper than MAX_SEED_CHAIN_DEPTH should be rejected."""
        # Build a chain of MAX_SEED_CHAIN_DEPTH + 2 iterations.
        # Iterations 0..MAX_SEED_CHAIN_DEPTH are admitted (chain depth 0..10).
        # Iteration MAX_SEED_CHAIN_DEPTH+1 would have depth 11 and should fail.
        prev_iid: str | None = None
        for i in range(MAX_SEED_CHAIN_DEPTH + 2):
            spec = IterationSpec(
                spec_id=f"spec-deep-{i:03d}",
                goal=f"Deep iteration {i}",
                success_criteria=["pass"],
                eval_requirements={"bench": True},
                change_units=["src/"],
                parent_iteration_id=prev_iid,
            )
            if i <= MAX_SEED_CHAIN_DEPTH:
                prev_iid = kernel.admit_iteration(spec)
            else:
                # This one should exceed the depth limit.
                with pytest.raises(AdmissionError, match=r"[Dd]epth"):
                    kernel.admit_iteration(spec)

    def test_admits_chain_at_max_depth(self, kernel: IterationKernel, store: KernelStore) -> None:
        """A chain exactly at MAX_SEED_CHAIN_DEPTH should be admitted."""
        prev_iid: str | None = None
        for i in range(MAX_SEED_CHAIN_DEPTH):
            spec = IterationSpec(
                spec_id=f"spec-exact-{i:03d}",
                goal=f"Chain iteration {i}",
                success_criteria=["pass"],
                eval_requirements={"bench": True},
                change_units=["src/"],
                parent_iteration_id=prev_iid,
            )
            prev_iid = kernel.admit_iteration(spec)
        # All MAX_SEED_CHAIN_DEPTH iterations should have been admitted.
        assert prev_iid is not None

    def test_generate_next_seed_sets_parent(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        """generate_next_seed should set parent_iteration_id on the seed."""
        import json

        iid = kernel.admit_iteration(sample_spec)

        entry = store.get_spec_entry(sample_spec.spec_id)
        assert entry is not None
        meta = json.loads(entry["metadata"]) if entry["metadata"] else {}
        meta["next_seed"] = "Follow-up goal"
        store.update_spec_status(
            sample_spec.spec_id,
            IterationState.admitted.value,
            metadata=meta,
        )

        seed = kernel.generate_next_seed(iid)
        assert seed is not None
        assert seed.parent_iteration_id == iid

    def test_max_seed_chain_depth_constant(self) -> None:
        """MAX_SEED_CHAIN_DEPTH should be 10."""
        assert MAX_SEED_CHAIN_DEPTH == 10

    def test_parent_iteration_id_stored_in_metadata(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        """parent_iteration_id should be persisted in metadata."""
        import json

        parent_spec = IterationSpec(
            spec_id="spec-p",
            goal="Parent",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
            change_units=["src/"],
        )
        parent_iid = kernel.admit_iteration(parent_spec)

        child_spec = IterationSpec(
            spec_id="spec-ch",
            goal="Child",
            success_criteria=["pass"],
            eval_requirements={"bench": True},
            change_units=["src/"],
            parent_iteration_id=parent_iid,
        )
        kernel.admit_iteration(child_spec)

        entry = store.get_spec_entry("spec-ch")
        assert entry is not None
        meta = json.loads(entry["metadata"]) if entry["metadata"] else {}
        assert meta.get("parent_iteration_id") == parent_iid


# ------------------------------------------------------------------
# Policy check callback
# ------------------------------------------------------------------


class TestPolicyCheckCallback:
    def test_admits_when_policy_check_returns_none(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        """policy_check returning None means approval — admission proceeds."""
        calls: list[dict] = []

        def approving_check(details: dict) -> dict | None:
            calls.append(details)
            return None

        iid = kernel.admit_iteration(sample_spec, policy_check=approving_check)
        assert iid.startswith("iter-")
        assert kernel.get_state(iid) == IterationState.admitted
        assert len(calls) == 1
        assert calls[0]["goal"] == sample_spec.goal
        assert calls[0]["risk_budget"] == sample_spec.risk_budget

    def test_rejects_when_policy_check_returns_rejection(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        """policy_check returning a dict with 'reason' should raise PolicyRejectionError."""

        def rejecting_check(details: dict) -> dict | None:
            return {"reason": "risk budget exceeds operator threshold"}

        with pytest.raises(PolicyRejectionError, match="risk budget exceeds"):
            kernel.admit_iteration(sample_spec, policy_check=rejecting_check)

        # Verify no store mutation occurred — spec should not exist.
        entry = store.get_spec_entry(sample_spec.spec_id)
        assert entry is None

    def test_policy_check_receives_trust_zone(self, kernel: IterationKernel) -> None:
        """policy_check should receive trust_zone from risk_budget."""
        received: list[dict] = []

        def capture_check(details: dict) -> dict | None:
            received.append(details)
            return None

        spec = IterationSpec(
            spec_id="spec-tz",
            goal="Test trust zone propagation",
            risk_budget={"band": "high", "trust_zone": "restricted"},
        )
        kernel.admit_iteration(spec, policy_check=capture_check)
        assert received[0]["trust_zone"] == "restricted"

    def test_policy_check_default_trust_zone(self, kernel: IterationKernel) -> None:
        """When risk_budget has no trust_zone, default is 'normal'."""
        received: list[dict] = []

        def capture_check(details: dict) -> dict | None:
            received.append(details)
            return None

        spec = IterationSpec(
            spec_id="spec-dtz",
            goal="Test default trust zone",
            risk_budget={"band": "low"},
        )
        kernel.admit_iteration(spec, policy_check=capture_check)
        assert received[0]["trust_zone"] == "normal"

    def test_no_policy_check_is_backward_compatible(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        """Omitting policy_check should work exactly as before."""
        iid = kernel.admit_iteration(sample_spec)
        assert iid.startswith("iter-")
        assert kernel.get_state(iid) == IterationState.admitted


# ------------------------------------------------------------------
# Lessons with evidence_ref
# ------------------------------------------------------------------


class TestLessonsEvidenceRef:
    def test_evidence_ref_persisted_and_returned(
        self, kernel: IterationKernel, sample_spec: IterationSpec, store: KernelStore
    ) -> None:
        """Lessons with evidence_ref should persist the ref and return it."""
        iid = kernel.admit_iteration(sample_spec)
        store.create_lesson(
            "l-ev-1",
            iid,
            "playbook",
            "Use lint before commit",
            evidence_ref="artifact://reconcile/rec-001",
        )
        store.create_lesson(
            "l-ev-2",
            iid,
            "pattern",
            "Prefer composition",
            evidence_ref="artifact://benchmark/bench-042",
        )
        store.create_lesson(
            "l-ev-3",
            iid,
            "template",
            "No evidence for this one",
        )

        pack = kernel.extract_lessons(iid)
        assert isinstance(pack, IterationLessonPack)
        # evidence_refs should have one entry per lesson (3 total)
        assert len(pack.evidence_refs) == 3
        assert "artifact://reconcile/rec-001" in pack.evidence_refs
        assert "artifact://benchmark/bench-042" in pack.evidence_refs
        assert None in pack.evidence_refs

    def test_evidence_ref_stored_in_db(self, store: KernelStore) -> None:
        """evidence_ref should be stored and retrievable from the lesson row."""
        store.create_lesson(
            "l-db-1",
            "iter-test",
            "pattern",
            "Some lesson",
            evidence_ref="artifact://proof/prf-99",
        )
        lesson = store.get_lesson("l-db-1")
        assert lesson is not None
        assert lesson["evidence_ref"] == "artifact://proof/prf-99"

    def test_evidence_ref_defaults_to_none(self, store: KernelStore) -> None:
        """When evidence_ref is not provided, it should be None in the DB."""
        store.create_lesson(
            "l-db-2",
            "iter-test",
            "playbook",
            "No ref lesson",
        )
        lesson = store.get_lesson("l-db-2")
        assert lesson is not None
        assert lesson["evidence_ref"] is None

    def test_lesson_pack_evidence_refs_field_exists(
        self, kernel: IterationKernel, sample_spec: IterationSpec
    ) -> None:
        """IterationLessonPack should have evidence_refs even with no lessons."""
        iid = kernel.admit_iteration(sample_spec)
        pack = kernel.extract_lessons(iid)
        assert hasattr(pack, "evidence_refs")
        assert pack.evidence_refs == []
