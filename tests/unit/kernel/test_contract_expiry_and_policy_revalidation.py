"""Unit tests for contract expiry, policy version revalidation, and evidence scoring."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.kernel.artifacts.lineage.evidence_cases import EvidenceCaseService
from hermit.kernel.artifacts.models.artifacts import ArtifactStore
from hermit.kernel.execution.executor.executor import ToolExecutor
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.controller import TaskController


class TestContractExpiry:
    """Test _contract_expired static method."""

    @pytest.mark.parametrize(
        "expiry_at,expected",
        [
            pytest.param(time.time() - 100, True, id="past-expiry"),
            pytest.param(time.time() + 3600, False, id="future-expiry"),
            pytest.param(None, False, id="no-expiry"),
            pytest.param("not-a-number", False, id="non-numeric-expiry"),
        ],
    )
    def test_contract_expired_detection(self, expiry_at: object, expected: bool) -> None:
        contract = SimpleNamespace(expiry_at=expiry_at)
        assert ToolExecutor._contract_expired(contract) is expected

    def test_real_contract_with_expiry(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-expiry",
            goal="test expiry",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )
        expired = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="write a file",
            status="authorized",
            expiry_at=time.time() - 100,
        )
        valid = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="write another file",
            status="authorized",
            expiry_at=time.time() + 3600,
        )
        assert ToolExecutor._contract_expired(expired)
        assert not ToolExecutor._contract_expired(valid)


class TestPolicyVersionDrift:
    """Test _policy_version_drifted static method."""

    @pytest.mark.parametrize(
        "policy_version,expected",
        [
            pytest.param("old-version-1.0", True, id="old-version"),
            pytest.param("", False, id="empty-version"),
            pytest.param(None, False, id="none-version"),
            pytest.param("   ", False, id="whitespace-version"),
        ],
    )
    def test_policy_version_drift_detection(self, policy_version: object, expected: bool) -> None:
        attempt = SimpleNamespace(policy_version=policy_version)
        assert ToolExecutor._policy_version_drifted(attempt) is expected


class TestEvidenceWeightedScoring:
    """Test weighted evidence sufficiency scoring formula."""

    @pytest.mark.parametrize(
        "refs,gaps,expected_score",
        [
            pytest.param(
                {
                    "witness_ref": "w-1",
                    "policy_result_ref": "p-1",
                    "context_pack_ref": "c-1",
                    "action_request_ref": "a-1",
                },
                [],
                1.0,
                id="all-refs-full-score",
            ),
            pytest.param(
                {
                    "witness_ref": None,
                    "policy_result_ref": None,
                    "context_pack_ref": None,
                    "action_request_ref": None,
                },
                [],
                0.0,
                id="no-refs-zero-score",
            ),
            pytest.param(
                {
                    "witness_ref": "w-1",
                    "policy_result_ref": None,
                    "context_pack_ref": None,
                    "action_request_ref": None,
                },
                [],
                0.35,
                id="only-witness",
            ),
        ],
    )
    def test_sufficiency_score(
        self, refs: dict[str, str | None], gaps: list[str], expected_score: float
    ) -> None:
        score = _compute_sufficiency(**refs, gaps=gaps)
        assert abs(score - expected_score) < 0.001

    def test_gap_penalty_applied(self) -> None:
        score_no_gap = _compute_sufficiency(
            witness_ref="w-1",
            policy_result_ref="p-1",
            context_pack_ref=None,
            action_request_ref=None,
            gaps=[],
        )
        score_with_gap = _compute_sufficiency(
            witness_ref="w-1",
            policy_result_ref="p-1",
            context_pack_ref=None,
            action_request_ref=None,
            gaps=["missing_witness"],
        )
        assert score_with_gap < score_no_gap
        assert abs(score_no_gap - score_with_gap - 0.2) < 0.001


class TestEvidenceContradictionTracking:
    """Test _find_prior_contradictions."""

    def test_finds_invalidated_evidence_for_same_contract(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-contradict",
            goal="test contradictions",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )
        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="test",
            status="executing",
        )
        case = store.create_evidence_case(
            task_id=ctx.task_id,
            subject_kind="contract",
            subject_ref=contract.contract_id,
            support_refs=[],
            contradiction_refs=[],
            sufficiency_score=0.5,
            status="invalidated",
        )
        service = EvidenceCaseService(store, artifacts)
        refs = service._find_prior_contradictions(ctx.task_id, contract.contract_id)
        assert case.evidence_case_id in refs

    def test_ignores_non_invalidated_evidence(self, tmp_path: Path) -> None:
        store = KernelStore(tmp_path / "kernel" / "state.db")
        artifacts = ArtifactStore(tmp_path / "kernel" / "artifacts")
        controller = TaskController(store)
        ctx = controller.start_task(
            conversation_id="chat-contradict-2",
            goal="test non-invalidated",
            source_channel="chat",
            kind="respond",
            workspace_root=str(tmp_path),
        )
        contract = store.create_execution_contract(
            task_id=ctx.task_id,
            step_id=ctx.step_id,
            step_attempt_id=ctx.step_attempt_id,
            objective="test",
            status="executing",
        )
        store.create_evidence_case(
            task_id=ctx.task_id,
            subject_kind="contract",
            subject_ref=contract.contract_id,
            support_refs=[],
            contradiction_refs=[],
            sufficiency_score=0.8,
            status="sufficient",
        )
        service = EvidenceCaseService(store, artifacts)
        refs = service._find_prior_contradictions(ctx.task_id, contract.contract_id)
        assert len(refs) == 0


def _compute_sufficiency(
    *,
    witness_ref: str | None,
    policy_result_ref: str | None,
    context_pack_ref: str | None,
    action_request_ref: str | None,
    gaps: list[str],
) -> float:
    """Replicate the weighted scoring formula from EvidenceCaseService."""
    ref_weights: dict[str, float] = {
        "witness_ref": 0.35,
        "policy_result_ref": 0.25,
        "context_pack_ref": 0.20,
        "action_request_ref": 0.20,
    }
    ref_map: dict[str, Any] = {
        "witness_ref": witness_ref,
        "policy_result_ref": policy_result_ref,
        "context_pack_ref": context_pack_ref,
        "action_request_ref": action_request_ref,
    }
    weighted_sum = 0.0
    for key, weight in ref_weights.items():
        if ref_map.get(key):
            weighted_sum += weight
    return max(0.0, min(1.0, weighted_sum - 0.2 * len(gaps)))
