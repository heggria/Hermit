"""E2E tests for the self-iteration pipeline from spec admission through promotion.

Exercises the real IterationKernel + KernelStore to validate the full
iteration lifecycle: admission, state transitions, promotion gating,
policy rejection, lesson extraction with evidence refs, and seed chain
depth limiting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermit.kernel.execution.self_modify.iteration_kernel import (
    MAX_SEED_CHAIN_DEPTH,
    AdmissionError,
    IterationKernel,
    IterationLessonPack,
    IterationSpec,
    IterationState,
    PolicyRejectionError,
)
from hermit.kernel.ledger.journal.store import KernelStore

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture()
def kernel(store: KernelStore) -> IterationKernel:
    return IterationKernel(store)


def _make_full_spec(
    spec_id: str,
    goal: str = "Improve retrieval latency",
    *,
    parent_iteration_id: str | None = None,
    risk_budget: dict | None = None,
) -> IterationSpec:
    """Build a fully-valid IterationSpec for e2e tests."""
    return IterationSpec(
        spec_id=spec_id,
        goal=goal,
        constraints=["no breaking changes"],
        success_criteria=["p99 < 50ms"],
        change_units=["src/hermit/kernel/context/memory/"],
        eval_requirements={"benchmark": "memory_latency"},
        risk_budget=risk_budget or {"band": "medium"},
        max_rounds=3,
        parent_iteration_id=parent_iteration_id,
    )


def _advance_to_reconciling(kernel: IterationKernel, spec: IterationSpec) -> str:
    """Admit a spec and advance it through the full pipeline to reconciling."""
    iid = kernel.admit_iteration(spec)
    kernel.transition(iid, IterationState.researching)
    kernel.transition(iid, IterationState.specifying)
    kernel.transition(iid, IterationState.executing)
    kernel.transition(iid, IterationState.verifying)
    kernel.transition(iid, IterationState.reconciling)
    return iid


def _set_promotion_metadata(
    store: KernelStore,
    spec_id: str,
    *,
    benchmark_results: dict | None = None,
    reconciliation_summary: str = "",
    replay_stable: bool | None = None,
) -> None:
    """Inject promotion-relevant metadata into a spec entry."""
    entry = store.get_spec_entry(spec_id)
    assert entry is not None
    meta = json.loads(entry["metadata"]) if entry["metadata"] else {}
    if benchmark_results is not None:
        meta["benchmark_results"] = benchmark_results
    if reconciliation_summary:
        meta["reconciliation_summary"] = reconciliation_summary
    if replay_stable is not None:
        meta["replay_stable"] = replay_stable
    store.update_spec_status(
        spec_id,
        IterationState.reconciling.value,
        metadata=meta,
    )


# ------------------------------------------------------------------
# Test 25: Full iteration lifecycle — admit to accepted
# ------------------------------------------------------------------


class TestFullIterationLifecycleAdmitToAccepted:
    """Verify the complete happy-path: admission through every phase to accepted."""

    def test_full_iteration_lifecycle_admit_to_accepted(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-e2e-lifecycle")

        # 1. Admit
        iteration_id = kernel.admit_iteration(spec)
        assert iteration_id.startswith("iter-")
        assert kernel.get_state(iteration_id) == IterationState.admitted

        # 2. Transition through every phase
        expected_transitions = [
            IterationState.researching,
            IterationState.specifying,
            IterationState.executing,
            IterationState.verifying,
            IterationState.reconciling,
        ]
        for target_state in expected_transitions:
            assert kernel.transition(iteration_id, target_state) is True
            assert kernel.get_state(iteration_id) == target_state

        # 3. Set metadata so the promotion gate passes
        _set_promotion_metadata(
            store,
            spec.spec_id,
            benchmark_results={"test_pass_rate": 1.0, "lint_score": 100},
            reconciliation_summary="All checks passed, no regressions.",
            replay_stable=True,
        )

        # 4. Verify promotion gate passes
        assert kernel.check_promotion_gate(iteration_id) is True

        # 5. Transition to accepted
        assert kernel.transition(iteration_id, IterationState.accepted) is True
        assert kernel.get_state(iteration_id) == IterationState.accepted

        # 6. Verify the spec entry is persisted correctly
        entry = store.get_spec_entry(spec.spec_id)
        assert entry is not None
        meta = json.loads(entry["metadata"]) if entry["metadata"] else {}
        assert meta["state"] == IterationState.accepted.value
        assert meta["iteration_id"] == iteration_id


# ------------------------------------------------------------------
# Test 26: Policy check rejects high-risk iteration
# ------------------------------------------------------------------


class TestPolicyCheckRejectsHighRiskIteration:
    """Verify that a policy_check callback can reject admission with no side effects."""

    def test_policy_check_rejects_high_risk_iteration(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec(
            "spec-e2e-policy-reject",
            goal="Risky kernel refactor",
            risk_budget={"band": "high", "trust_zone": "restricted"},
        )

        def rejecting_policy(details: dict) -> dict | None:
            return {"reason": "too risky"}

        # Admission must raise PolicyRejectionError
        with pytest.raises(PolicyRejectionError, match="too risky"):
            kernel.admit_iteration(spec, policy_check=rejecting_policy)

        # No spec entry should exist in the store — zero side effects
        entry = store.get_spec_entry(spec.spec_id)
        assert entry is None

        # Verify that a valid backlog scan also returns nothing for this spec
        backlog = store.list_spec_backlog(limit=100)
        spec_ids = [e["spec_id"] for e in backlog]
        assert spec.spec_id not in spec_ids


# ------------------------------------------------------------------
# Test 27: Lessons carry evidence_ref through extraction
# ------------------------------------------------------------------


class TestLessonsCarryEvidenceRefThroughExtraction:
    """Verify that evidence_ref flows from lesson creation through extraction."""

    def test_lessons_carry_evidence_ref_through_extraction(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        spec = _make_full_spec("spec-e2e-lessons")

        # Admit and advance to reconciling
        iteration_id = _advance_to_reconciling(kernel, spec)
        assert kernel.get_state(iteration_id) == IterationState.reconciling

        # Create lessons with various categories and evidence refs
        store.create_lesson(
            "les-1",
            iteration_id,
            "playbook",
            "Always run lint before commit",
            evidence_ref="artifact:abc123",
        )
        store.create_lesson(
            "les-2",
            iteration_id,
            "template",
            "Use dataclass for records",
            evidence_ref="artifact:def456",
        )
        store.create_lesson(
            "les-3",
            iteration_id,
            "pattern",
            "Prefer composition over inheritance",
            evidence_ref="artifact:ghi789",
        )

        # Extract lessons
        pack = kernel.extract_lessons(iteration_id)
        assert isinstance(pack, IterationLessonPack)
        assert pack.iteration_id == iteration_id

        # Verify evidence_refs contains the artifact reference
        assert "artifact:abc123" in pack.evidence_refs
        assert "artifact:def456" in pack.evidence_refs
        assert "artifact:ghi789" in pack.evidence_refs

        # Verify lessons are categorized correctly
        assert "Always run lint before commit" in pack.playbook_updates
        assert "Use dataclass for records" in pack.template_updates
        assert "Prefer composition over inheritance" in pack.pattern_updates

        # Verify counts
        assert len(pack.playbook_updates) == 1
        assert len(pack.template_updates) == 1
        assert len(pack.pattern_updates) == 1
        assert len(pack.evidence_refs) == 3


# ------------------------------------------------------------------
# Test 28: Seed chain depth limit rejects deep chains
# ------------------------------------------------------------------


class TestSeedChainDepthLimitRejectsDeepChains:
    """Verify that chains exceeding MAX_SEED_CHAIN_DEPTH are rejected."""

    def test_seed_chain_depth_limit_rejects_deep_chains(
        self, kernel: IterationKernel, store: KernelStore
    ) -> None:
        assert MAX_SEED_CHAIN_DEPTH == 10, "Test assumes MAX_SEED_CHAIN_DEPTH == 10"

        # Build a chain of iterations: iteration_0 (no parent) -> iteration_1 -> ... -> iteration_10
        prev_iid: str | None = None
        admitted_ids: list[str] = []

        for i in range(MAX_SEED_CHAIN_DEPTH + 1):
            spec = _make_full_spec(
                f"spec-chain-{i:03d}",
                goal=f"Chain iteration {i}",
                parent_iteration_id=prev_iid,
            )
            iid = kernel.admit_iteration(spec)
            admitted_ids.append(iid)
            prev_iid = iid

        # All 11 iterations (depth 0..10) should have been admitted
        assert len(admitted_ids) == MAX_SEED_CHAIN_DEPTH + 1

        # Now attempt to admit iteration_11 (depth 11 > MAX_SEED_CHAIN_DEPTH)
        spec_too_deep = _make_full_spec(
            "spec-chain-011",
            goal="Chain iteration 11 — too deep",
            parent_iteration_id=prev_iid,
        )
        with pytest.raises(AdmissionError, match=r"[Dd]epth"):
            kernel.admit_iteration(spec_too_deep)

        # Verify the rejected spec was NOT persisted
        entry = store.get_spec_entry("spec-chain-011")
        assert entry is None
