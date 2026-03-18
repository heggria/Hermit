"""Tests for TrustScorer — covers missing lines 85, 150-154."""

from __future__ import annotations

import uuid
from pathlib import Path

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.trust.scoring import TrustScorer


def _make_store(tmp_path: Path) -> KernelStore:
    return KernelStore(db_path=tmp_path / "kernel.db")


def _unique_conv_id() -> str:
    return f"conv-{uuid.uuid4().hex[:8]}"


def _seed_receipts(
    store: KernelStore,
    action_type: str,
    *,
    succeeded: int = 0,
    failed: int = 0,
    rolled_back: int = 0,
) -> None:
    conv = store.ensure_conversation(_unique_conv_id(), source_channel="test")
    task = store.create_task(
        conversation_id=conv.conversation_id,
        title="t",
        goal="g",
        status="active",
        priority="normal",
        owner="operator",
        policy_profile="default",
        source_channel="test",
    )
    step = store.create_step(task_id=task.task_id, kind="tool_call", status="active")
    attempt = store.create_step_attempt(
        task_id=task.task_id, step_id=step.step_id, attempt=1, status="active"
    )

    for _ in range(succeeded):
        store.create_receipt(
            task_id=task.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            action_type=action_type,
            input_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            output_refs=[],
            result_summary="ok",
            result_code="succeeded",
            rollback_status="not_requested",
        )

    for _ in range(failed):
        store.create_receipt(
            task_id=task.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            action_type=action_type,
            input_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            output_refs=[],
            result_summary="fail",
            result_code="failed",
            rollback_status="not_requested",
        )

    for _ in range(rolled_back):
        store.create_receipt(
            task_id=task.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            action_type=action_type,
            input_refs=[],
            environment_ref=None,
            policy_result={},
            approval_ref=None,
            output_refs=[],
            result_summary="rolled back",
            result_code="succeeded",
            rollback_status="completed",
        )


class TestSuggestRiskAdjustmentInsufficientData:
    """Cover line 85: suggest_risk_adjustment returns None when score is None."""

    def test_returns_none_with_insufficient_data(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        _seed_receipts(store, "write_local", succeeded=2)  # Less than _MIN_EXECUTIONS=5
        scorer = TrustScorer(store)
        result = scorer.suggest_risk_adjustment("write_local", "high")
        assert result is None


class TestBandForScore:
    """Cover lines 150-154: _band_for_score returns correct bands."""

    def test_high_score_returns_low(self) -> None:
        assert TrustScorer._band_for_score(0.90) == "low"

    def test_medium_score_returns_medium(self) -> None:
        assert TrustScorer._band_for_score(0.70) == "medium"

    def test_moderate_score_returns_high(self) -> None:
        assert TrustScorer._band_for_score(0.50) == "high"

    def test_low_score_returns_critical(self) -> None:
        assert TrustScorer._band_for_score(0.30) == "critical"

    def test_boundary_085_returns_low(self) -> None:
        assert TrustScorer._band_for_score(0.85) == "low"

    def test_boundary_065_returns_medium(self) -> None:
        assert TrustScorer._band_for_score(0.65) == "medium"

    def test_boundary_040_returns_high(self) -> None:
        assert TrustScorer._band_for_score(0.40) == "high"

    def test_below_040_returns_critical(self) -> None:
        assert TrustScorer._band_for_score(0.39) == "critical"
