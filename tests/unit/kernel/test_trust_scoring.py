"""Unit tests for dynamic trust scoring."""

from __future__ import annotations

import uuid
from pathlib import Path

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.trust.models import RiskAdjustment, TrustScore
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
    """Insert receipt rows directly for testing."""
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

    for _i in range(succeeded):
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

    for _i in range(failed):
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

    for _i in range(rolled_back):
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


def _seed_reconciliations(
    store: KernelStore,
    *,
    deltas: list[float],
) -> None:
    """Insert reconciliation rows with given confidence deltas."""
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
    for delta in deltas:
        store.create_reconciliation(
            task_id=task.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            contract_ref="contract-1",
            receipt_refs=[],
            observed_output_refs=[],
            intended_effect_summary="test",
            authorized_effect_summary="test",
            observed_effect_summary="test",
            receipted_effect_summary="test",
            result_class="satisfied",
            confidence_delta=delta,
            recommended_resolution="promote_learning",
            operator_summary="ok",
            final_state_witness_ref=None,
        )


class TestTrustScoreModel:
    def test_trust_score_fields(self) -> None:
        ts = TrustScore(
            subject_kind="action_class",
            subject_ref="write_local",
            total_executions=10,
            successful_executions=8,
            rolled_back_executions=1,
            reconciliation_count=5,
            avg_reconciliation_confidence=0.7,
            composite_score=0.76,
            computed_at=0.0,
        )
        assert ts.composite_score == 0.76
        assert ts.subject_kind == "action_class"

    def test_risk_adjustment_fields(self) -> None:
        adj = RiskAdjustment(
            subject_kind="action_class",
            subject_ref="write_local",
            current_risk_band="high",
            suggested_risk_band="medium",
            reason="score improved",
            trust_score_ref=0.76,
        )
        assert adj.suggested_risk_band == "medium"
        assert adj.evidence_refs == []


class TestTrustScorerBelowMinimum:
    def test_returns_none_with_fewer_than_five_executions(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        _seed_receipts(store, "write_local", succeeded=3)
        scorer = TrustScorer(store)
        assert scorer.score_action_class("write_local") is None


class TestTrustScorerComputation:
    def test_perfect_score(self, tmp_path: Path) -> None:
        """All succeeded, no rollbacks, neutral reconciliation -> high score."""
        store = _make_store(tmp_path)
        _seed_receipts(store, "write_local", succeeded=10)
        scorer = TrustScorer(store)
        score = scorer.score_action_class("write_local")
        assert score is not None
        # success_rate=1.0, rollback_rate=0.0, avg_recon=0.5 (default)
        # 0.5*1.0 + 0.3*(1-0.0) + 0.2*0.5 = 0.5+0.3+0.1 = 0.9
        assert score.composite_score == 0.9
        assert score.total_executions == 10
        assert score.successful_executions == 10
        assert score.rolled_back_executions == 0

    def test_mixed_results(self, tmp_path: Path) -> None:
        """Mix of succeeded, failed, and rolled back."""
        store = _make_store(tmp_path)
        _seed_receipts(store, "execute_command", succeeded=5, failed=3, rolled_back=2)
        scorer = TrustScorer(store)
        score = scorer.score_action_class("execute_command")
        assert score is not None
        # rolled_back receipts have result_code="succeeded" so successful=7
        # success_rate = 7/10 = 0.7, rollback_rate = 2/10 = 0.2
        # avg_recon = 0.5 (no reconciliations)
        # 0.5*0.7 + 0.3*(1-0.2) + 0.2*0.5 = 0.35+0.24+0.10 = 0.69
        assert score.composite_score == 0.69

    def test_reconciliation_confidence_affects_score(self, tmp_path: Path) -> None:
        """Positive reconciliation deltas should boost the score."""
        store = _make_store(tmp_path)
        _seed_receipts(store, "write_local", succeeded=10)
        _seed_reconciliations(store, deltas=[0.2, 0.2, 0.2, 0.2, 0.2])
        scorer = TrustScorer(store)
        score = scorer.score_action_class("write_local")
        assert score is not None
        # avg_recon = mean(0.5+0.2) = 0.7
        # 0.5*1.0 + 0.3*1.0 + 0.2*0.7 = 0.5+0.3+0.14 = 0.94
        assert score.composite_score == 0.94

    def test_filters_by_action_class(self, tmp_path: Path) -> None:
        """Only receipts matching the queried action class contribute."""
        store = _make_store(tmp_path)
        _seed_receipts(store, "write_local", succeeded=10)
        _seed_receipts(store, "read_local", succeeded=20)
        scorer = TrustScorer(store)
        score = scorer.score_action_class("write_local")
        assert score is not None
        assert score.total_executions == 10


class TestRiskAdjustment:
    def test_suggest_downgrade(self, tmp_path: Path) -> None:
        """High trust score should suggest lowering risk band."""
        store = _make_store(tmp_path)
        _seed_receipts(store, "write_local", succeeded=10)
        scorer = TrustScorer(store)
        adj = scorer.suggest_risk_adjustment("write_local", "high")
        assert adj is not None
        assert adj.suggested_risk_band == "low"
        assert adj.current_risk_band == "high"

    def test_no_adjustment_when_band_matches(self, tmp_path: Path) -> None:
        """Returns None when score already matches current band."""
        store = _make_store(tmp_path)
        _seed_receipts(store, "write_local", succeeded=10)
        scorer = TrustScorer(store)
        # Score is 0.9 -> band "low"; if current is already "low", no adjustment
        adj = scorer.suggest_risk_adjustment("write_local", "low")
        assert adj is None

    def test_log_adjustment_decision(self, tmp_path: Path) -> None:
        """Advisory adjustment is logged as an event, not auto-applied."""
        store = _make_store(tmp_path)
        _seed_receipts(store, "write_local", succeeded=10)
        scorer = TrustScorer(store)
        adj = scorer.suggest_risk_adjustment("write_local", "high")
        assert adj is not None

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

        event_id = scorer.log_adjustment_decision(
            adj,
            task_id=task.task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
        )
        assert event_id.startswith("event_")

        events = store.list_events(task_id=task.task_id)
        trust_events = [e for e in events if e["event_type"] == "trust.risk_adjustment_suggested"]
        assert len(trust_events) >= 1
        payload = trust_events[0]["payload"]
        assert payload["advisory_only"] is True
        assert payload["suggested_risk_band"] == "low"
