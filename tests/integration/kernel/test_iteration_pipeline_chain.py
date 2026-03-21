"""Integration test: IterationKernel admit → full state pipeline → promotion gate → lessons → next seed.

Exercises the COMPLETE iteration lifecycle using a real KernelStore (SQLite).
Covers admission validation, state machine transitions, park/resume, promotion
gating, lesson extraction, next-seed generation, loop detection, strict
admission rejection, and terminal immutability.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermit.kernel.execution.self_modify.iteration_kernel import (
    MAX_SEED_CHAIN_DEPTH,
    AdmissionError,
    InvalidTransitionError,
    IterationKernel,
    IterationLessonPack,
    IterationSpec,
    IterationState,
)
from hermit.kernel.ledger.journal.store import KernelStore

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "iter_pipeline.db")


@pytest.fixture()
def kernel(store: KernelStore) -> IterationKernel:
    return IterationKernel(store)


def _make_full_spec(spec_id: str, *, parent_iteration_id: str | None = None) -> IterationSpec:
    """Create a fully-specified IterationSpec that passes strict admission."""
    return IterationSpec(
        spec_id=spec_id,
        goal="Improve memory retrieval latency by 30%",
        constraints=["no breaking API changes", "maintain backward compatibility"],
        success_criteria=["p99 latency < 50ms", "all existing tests pass"],
        change_units=["src/hermit/kernel/context/memory/"],
        eval_requirements={"benchmark": "memory_latency", "min_score": 0.95},
        risk_budget={"band": "medium"},
        max_rounds=3,
        parent_iteration_id=parent_iteration_id,
    )


def _advance_to(kernel: IterationKernel, iteration_id: str, target: IterationState) -> None:
    """Advance an iteration through the pipeline to the target state."""
    pipeline = [
        IterationState.researching,
        IterationState.specifying,
        IterationState.executing,
        IterationState.verifying,
        IterationState.reconciling,
    ]
    for state in pipeline:
        if kernel.get_state(iteration_id) == target:
            return
        kernel.transition(iteration_id, state)
    # Handle terminal states
    if target in (IterationState.accepted, IterationState.rejected):
        kernel.transition(iteration_id, target)


def _inject_promotion_metadata(
    store: KernelStore,
    spec_id: str,
    *,
    benchmark_results: dict | None = None,
    reconciliation_summary: str = "",
    replay_stable: bool | None = None,
    unexplained_drift: list | None = None,
    next_seed: str | None = None,
) -> None:
    """Inject promotion-relevant metadata into a spec entry."""
    entry = store.get_spec_entry(spec_id)
    assert entry is not None
    meta = json.loads(entry["metadata"]) if entry.get("metadata") else {}
    if benchmark_results is not None:
        meta["benchmark_results"] = benchmark_results
    if reconciliation_summary:
        meta["reconciliation_summary"] = reconciliation_summary
    if replay_stable is not None:
        meta["replay_stable"] = replay_stable
    if unexplained_drift is not None:
        meta["unexplained_drift"] = unexplained_drift
    if next_seed is not None:
        meta["next_seed"] = next_seed
    store.update_spec_status(spec_id, entry["status"], metadata=meta)


# ------------------------------------------------------------------
# 1. Admission with validation
# ------------------------------------------------------------------


class TestAdmissionWithValidation:
    """Create IterationSpec with full fields, admit with strict=True,
    verify draft→admitted transition."""

    def test_strict_admission_succeeds_with_full_spec(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-admission-001")
        iteration_id = kernel.admit_iteration(spec, strict=True)

        # Verify iteration_id format
        assert iteration_id.startswith("iter-")
        assert len(iteration_id) == 17  # "iter-" + 12 hex chars

        # Verify state is admitted (draft→admitted happened internally)
        state = kernel.get_state(iteration_id)
        assert state == IterationState.admitted

        # Verify spec stored in backlog with correct metadata
        entry = store.get_spec_entry(spec.spec_id)
        assert entry is not None
        assert entry["goal"] == spec.goal
        assert entry["source"] == "self-iterate"
        meta = json.loads(entry["metadata"])
        assert meta["iteration_id"] == iteration_id
        assert meta["state"] == IterationState.admitted.value
        assert meta["constraints"] == spec.constraints
        assert meta["success_criteria"] == spec.success_criteria
        assert meta["change_units"] == spec.change_units
        assert meta["eval_requirements"] == spec.eval_requirements


# ------------------------------------------------------------------
# 2. Full state pipeline
# ------------------------------------------------------------------


class TestFullStatePipeline:
    """admitted → researching → specifying → executing → verifying → reconciling.
    Verify each transition succeeds."""

    def test_complete_pipeline_transitions(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-pipeline-001")
        iid = kernel.admit_iteration(spec, strict=True)

        # admitted → researching
        assert kernel.transition(iid, IterationState.researching) is True
        assert kernel.get_state(iid) == IterationState.researching

        # researching → specifying
        assert kernel.transition(iid, IterationState.specifying) is True
        assert kernel.get_state(iid) == IterationState.specifying

        # specifying → executing
        assert kernel.transition(iid, IterationState.executing) is True
        assert kernel.get_state(iid) == IterationState.executing

        # executing → verifying
        assert kernel.transition(iid, IterationState.verifying) is True
        assert kernel.get_state(iid) == IterationState.verifying

        # verifying → reconciling
        assert kernel.transition(iid, IterationState.reconciling) is True
        assert kernel.get_state(iid) == IterationState.reconciling

        # Verify the metadata state field is also updated
        entry = store.get_spec_entry(spec.spec_id)
        assert entry is not None
        meta = json.loads(entry["metadata"])
        assert meta["state"] == IterationState.reconciling.value


# ------------------------------------------------------------------
# 3. Park and resume
# ------------------------------------------------------------------


class TestParkAndResume:
    """From executing → parked → executing. Verify round-trip."""

    def test_park_from_executing_and_resume(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-park-001")
        iid = kernel.admit_iteration(spec, strict=True)
        _advance_to(kernel, iid, IterationState.executing)
        assert kernel.get_state(iid) == IterationState.executing

        # Park
        assert kernel.transition(iid, IterationState.parked) is True
        assert kernel.get_state(iid) == IterationState.parked

        # Resume back to executing
        assert kernel.transition(iid, IterationState.executing) is True
        assert kernel.get_state(iid) == IterationState.executing

    def test_park_from_verifying_and_resume(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        """Park/resume also works from verifying state."""
        spec = _make_full_spec("spec-park-002")
        iid = kernel.admit_iteration(spec, strict=True)
        _advance_to(kernel, iid, IterationState.verifying)

        assert kernel.transition(iid, IterationState.parked) is True
        assert kernel.transition(iid, IterationState.verifying) is True
        assert kernel.get_state(iid) == IterationState.verifying


# ------------------------------------------------------------------
# 4. Promotion gate success
# ------------------------------------------------------------------


class TestPromotionGateSuccess:
    """In reconciling state, inject metadata with benchmark_results,
    reconciliation_summary, replay_stable=True, no unexplained_drift.
    check_promotion_gate → True. Transition → accepted."""

    def test_promotion_gate_passes_and_transitions_to_accepted(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-promo-001")
        iid = kernel.admit_iteration(spec, strict=True)
        _advance_to(kernel, iid, IterationState.reconciling)

        # Inject passing promotion metadata
        _inject_promotion_metadata(
            store,
            spec.spec_id,
            benchmark_results={"test_pass_rate": 0.99, "latency_p99": 42},
            reconciliation_summary="All benchmarks pass. No regressions detected.",
            replay_stable=True,
            unexplained_drift=[],
        )

        # Promotion gate should pass
        assert kernel.check_promotion_gate(iid) is True

        # Transition to accepted
        assert kernel.transition(iid, IterationState.accepted) is True
        assert kernel.get_state(iid) == IterationState.accepted


# ------------------------------------------------------------------
# 5. Promotion gate failure
# ------------------------------------------------------------------


class TestPromotionGateFailure:
    """Missing replay_stable → check_promotion_gate → False."""

    def test_promotion_gate_fails_without_replay_stable(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-promo-fail-001")
        iid = kernel.admit_iteration(spec, strict=True)
        _advance_to(kernel, iid, IterationState.reconciling)

        # Inject metadata WITHOUT replay_stable
        _inject_promotion_metadata(
            store,
            spec.spec_id,
            benchmark_results={"test_pass_rate": 0.99},
            reconciliation_summary="Benchmarks pass.",
            # replay_stable intentionally omitted
        )

        assert kernel.check_promotion_gate(iid) is False

    def test_promotion_gate_fails_with_replay_stable_false(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-promo-fail-002")
        iid = kernel.admit_iteration(spec, strict=True)
        _advance_to(kernel, iid, IterationState.reconciling)

        _inject_promotion_metadata(
            store,
            spec.spec_id,
            benchmark_results={"score": 95},
            reconciliation_summary="Summary present.",
            replay_stable=False,
        )

        assert kernel.check_promotion_gate(iid) is False

    def test_promotion_gate_fails_with_unexplained_drift(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-promo-fail-003")
        iid = kernel.admit_iteration(spec, strict=True)
        _advance_to(kernel, iid, IterationState.reconciling)

        _inject_promotion_metadata(
            store,
            spec.spec_id,
            benchmark_results={"score": 95},
            reconciliation_summary="Some summary.",
            replay_stable=True,
            unexplained_drift=["metric X regressed 5%"],
        )

        assert kernel.check_promotion_gate(iid) is False


# ------------------------------------------------------------------
# 6. Lesson extraction
# ------------------------------------------------------------------


class TestLessonExtraction:
    """After acceptance, extract_lessons → verify IterationLessonPack categorized correctly."""

    def test_lessons_categorized_after_acceptance(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-lesson-001")
        iid = kernel.admit_iteration(spec, strict=True)
        _advance_to(kernel, iid, IterationState.reconciling)

        # Inject passing promotion metadata and transition to accepted
        _inject_promotion_metadata(
            store,
            spec.spec_id,
            benchmark_results={"pass_rate": 1.0},
            reconciliation_summary="All clear.",
            replay_stable=True,
        )
        kernel.transition(iid, IterationState.accepted)

        # Create lessons with different categories
        store.create_lesson("l1", iid, "playbook", "Always run benchmarks before promotion")
        store.create_lesson("l2", iid, "template", "Use dataclass for all records")
        store.create_lesson("l3", iid, "pattern", "Prefer composition over inheritance")
        store.create_lesson("l4", iid, "process", "Review before merge")
        store.create_lesson("l5", iid, "architecture", "Use event sourcing for state")
        store.create_lesson("l6", iid, "other", "Miscellaneous uncategorized insight")

        # Extract lessons
        pack = kernel.extract_lessons(iid)
        assert isinstance(pack, IterationLessonPack)
        assert pack.iteration_id == iid
        assert pack.lesson_id.startswith("lpack-")

        # playbook: "playbook" + "process" categories
        assert "Always run benchmarks before promotion" in pack.playbook_updates
        assert "Review before merge" in pack.playbook_updates

        # template: "template" category
        assert "Use dataclass for all records" in pack.template_updates

        # pattern: "pattern" + "architecture" + uncategorized
        assert "Prefer composition over inheritance" in pack.pattern_updates
        assert "Use event sourcing for state" in pack.pattern_updates
        assert "Miscellaneous uncategorized insight" in pack.pattern_updates


# ------------------------------------------------------------------
# 7. Next seed generation
# ------------------------------------------------------------------


class TestNextSeedGeneration:
    """Set metadata.next_seed, generate_next_seed → verify new IterationSpec
    with parent_iteration_id set."""

    def test_next_seed_generated_with_parent_link(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-seed-001")
        iid = kernel.admit_iteration(spec, strict=True)
        _advance_to(kernel, iid, IterationState.reconciling)

        # Inject next_seed goal into metadata
        _inject_promotion_metadata(
            store,
            spec.spec_id,
            benchmark_results={"pass_rate": 1.0},
            reconciliation_summary="All clear.",
            replay_stable=True,
            next_seed="Optimize cache invalidation strategy",
        )
        kernel.transition(iid, IterationState.accepted)

        # Generate next seed
        seed = kernel.generate_next_seed(iid)
        assert seed is not None
        assert seed.goal == "Optimize cache invalidation strategy"
        assert seed.spec_id.startswith("spec-")
        assert seed.parent_iteration_id == iid

        # Verify constraints and success_criteria carried forward
        assert seed.constraints == spec.constraints
        assert seed.success_criteria == spec.success_criteria
        assert seed.max_rounds == spec.max_rounds

    def test_no_seed_when_next_seed_absent(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-seed-002")
        iid = kernel.admit_iteration(spec, strict=True)
        assert kernel.generate_next_seed(iid) is None


# ------------------------------------------------------------------
# 8. Loop detection
# ------------------------------------------------------------------


class TestLoopDetection:
    """Create chain of 11 iterations. 11th admission → AdmissionError
    (MAX_SEED_CHAIN_DEPTH=10)."""

    def test_chain_of_11_triggers_depth_limit(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        assert MAX_SEED_CHAIN_DEPTH == 10

        prev_iid: str | None = None
        admitted_ids: list[str] = []

        # Admit iterations 0 through 10 (11 iterations, chain depth 0..10)
        for i in range(MAX_SEED_CHAIN_DEPTH + 1):
            spec = _make_full_spec(f"spec-chain-{i:03d}", parent_iteration_id=prev_iid)
            prev_iid = kernel.admit_iteration(spec, strict=True)
            admitted_ids.append(prev_iid)

        # The 12th iteration (index 11) would create depth 11 > MAX_SEED_CHAIN_DEPTH
        spec_11 = _make_full_spec("spec-chain-011", parent_iteration_id=prev_iid)
        with pytest.raises(AdmissionError, match=r"[Dd]epth"):
            kernel.admit_iteration(spec_11, strict=True)

    def test_chain_at_max_depth_succeeds(self, kernel: IterationKernel, store: KernelStore) -> None:
        """A chain of exactly MAX_SEED_CHAIN_DEPTH iterations should be admitted."""
        prev_iid: str | None = None
        for i in range(MAX_SEED_CHAIN_DEPTH):
            spec = _make_full_spec(f"spec-exact-chain-{i:03d}", parent_iteration_id=prev_iid)
            prev_iid = kernel.admit_iteration(spec, strict=True)
        assert prev_iid is not None


# ------------------------------------------------------------------
# 9. Strict admission rejection
# ------------------------------------------------------------------


class TestStrictAdmissionRejection:
    """Spec missing success_criteria + strict=True → AdmissionError."""

    def test_missing_success_criteria_strict_rejected(self, kernel: IterationKernel) -> None:
        spec = IterationSpec(
            spec_id="spec-strict-fail-001",
            goal="A goal without criteria",
            # success_criteria intentionally empty
            eval_requirements={"benchmark": "latency"},
            change_units=["src/"],
        )
        with pytest.raises(AdmissionError, match="admission failed"):
            kernel.admit_iteration(spec, strict=True)

    def test_missing_multiple_fields_strict_rejected(self, kernel: IterationKernel) -> None:
        """Missing success_criteria, eval_requirements, and change_units."""
        spec = IterationSpec(
            spec_id="spec-strict-fail-002",
            goal="A goal with nothing else",
        )
        with pytest.raises(AdmissionError, match="admission failed"):
            kernel.admit_iteration(spec, strict=True)

    def test_non_strict_admits_incomplete_spec(self, kernel: IterationKernel) -> None:
        """Non-strict mode (default) should still admit incomplete specs."""
        spec = IterationSpec(
            spec_id="spec-nonstrict-001",
            goal="A goal without criteria",
        )
        iid = kernel.admit_iteration(spec, strict=False)
        assert iid.startswith("iter-")
        assert kernel.get_state(iid) == IterationState.admitted


# ------------------------------------------------------------------
# 10. Terminal immutability
# ------------------------------------------------------------------


class TestTerminalImmutability:
    """After merge_approved or rejected, try transition → InvalidTransitionError."""

    def test_accepted_can_transition_to_pr_created(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-terminal-001")
        iid = kernel.admit_iteration(spec, strict=True)
        _advance_to(kernel, iid, IterationState.accepted)
        assert kernel.get_state(iid) == IterationState.accepted

        # accepted is no longer terminal — it can transition to pr_created
        kernel.transition(iid, IterationState.pr_created)
        assert kernel.get_state(iid) == IterationState.pr_created

        # But invalid transitions should still fail
        with pytest.raises(InvalidTransitionError, match="Invalid transition"):
            kernel.transition(iid, IterationState.researching)

    def test_merge_approved_is_immutable(self, kernel: IterationKernel, store: KernelStore) -> None:
        spec = _make_full_spec("spec-terminal-001b")
        iid = kernel.admit_iteration(spec, strict=True)
        _advance_to(kernel, iid, IterationState.accepted)
        kernel.transition(iid, IterationState.pr_created)
        kernel.transition(iid, IterationState.merge_approved)
        assert kernel.get_state(iid) == IterationState.merge_approved

        with pytest.raises(InvalidTransitionError, match="terminal state"):
            kernel.transition(iid, IterationState.researching)

        with pytest.raises(InvalidTransitionError, match="terminal state"):
            kernel.transition(iid, IterationState.reconciling)

    def test_rejected_is_immutable(self, kernel: IterationKernel, store: KernelStore) -> None:
        spec = _make_full_spec("spec-terminal-002")
        iid = kernel.admit_iteration(spec, strict=True)

        # Reject directly from admitted
        kernel.transition(iid, IterationState.rejected)
        assert kernel.get_state(iid) == IterationState.rejected

        with pytest.raises(InvalidTransitionError, match="terminal state"):
            kernel.transition(iid, IterationState.admitted)

        with pytest.raises(InvalidTransitionError, match="terminal state"):
            kernel.transition(iid, IterationState.researching)


# ------------------------------------------------------------------
# End-to-end: full lifecycle chain
# ------------------------------------------------------------------


class TestFullLifecycleChain:
    """Complete lifecycle: admit → pipeline → promotion → lessons → seed → child admit."""

    def test_complete_chain(self, kernel: IterationKernel, store: KernelStore) -> None:
        # 1. Admit
        spec = _make_full_spec("spec-e2e-001")
        iid = kernel.admit_iteration(spec, strict=True)
        assert kernel.get_state(iid) == IterationState.admitted

        # 2. Full pipeline to reconciling
        _advance_to(kernel, iid, IterationState.reconciling)
        assert kernel.get_state(iid) == IterationState.reconciling

        # 3. Park and resume mid-pipeline (from a fresh iteration)
        spec2 = _make_full_spec("spec-e2e-002")
        iid2 = kernel.admit_iteration(spec2, strict=True)
        _advance_to(kernel, iid2, IterationState.executing)
        kernel.transition(iid2, IterationState.parked)
        kernel.transition(iid2, IterationState.executing)
        assert kernel.get_state(iid2) == IterationState.executing

        # 4. Inject promotion metadata and pass gate on first iteration
        _inject_promotion_metadata(
            store,
            spec.spec_id,
            benchmark_results={"test_pass_rate": 0.99, "latency_p99": 42},
            reconciliation_summary="All benchmarks pass. No regressions.",
            replay_stable=True,
            unexplained_drift=[],
            next_seed="Follow-up: optimize index rebuild",
        )
        assert kernel.check_promotion_gate(iid) is True

        # 5. Transition to accepted
        kernel.transition(iid, IterationState.accepted)
        assert kernel.get_state(iid) == IterationState.accepted

        # 6. Create lessons
        store.create_lesson("le1", iid, "playbook", "Run full benchmark suite")
        store.create_lesson("le2", iid, "pattern", "Use immutable records")
        store.create_lesson("le3", iid, "template", "Standard test fixture pattern")

        pack = kernel.extract_lessons(iid)
        assert len(pack.playbook_updates) == 1
        assert len(pack.pattern_updates) == 1
        assert len(pack.template_updates) == 1

        # 7. Generate next seed
        seed = kernel.generate_next_seed(iid)
        assert seed is not None
        assert seed.goal == "Follow-up: optimize index rebuild"
        assert seed.parent_iteration_id == iid

        # 8. Admit the seed as a child iteration
        child_iid = kernel.admit_iteration(seed, strict=False)
        assert child_iid.startswith("iter-")
        assert kernel.get_state(child_iid) == IterationState.admitted

        # 9. Verify accepted can transition to pr_created but not to arbitrary states
        with pytest.raises(InvalidTransitionError, match="Invalid transition"):
            kernel.transition(iid, IterationState.researching)
