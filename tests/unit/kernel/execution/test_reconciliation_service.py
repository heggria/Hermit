"""Unit tests for ReconciliationService (reconciliations.py)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.recovery.reconcile import ReconcileOutcome
from hermit.kernel.execution.recovery.reconciliations import ReconciliationService
from hermit.kernel.task.models.records import ReconciliationRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attempt_ctx(**overrides: Any) -> TaskExecutionContext:
    defaults = {
        "conversation_id": "conv-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "source_channel": "chat",
        "workspace_root": "/tmp/ws",
    }
    defaults.update(overrides)
    return TaskExecutionContext(**defaults)


def _make_reconciliation(**overrides: Any) -> ReconciliationRecord:
    defaults = {
        "reconciliation_id": "recon-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "step_attempt_id": "attempt-1",
        "contract_ref": "contract-1",
        "result_class": "satisfied",
        "operator_summary": "ok",
    }
    defaults.update(overrides)
    return ReconciliationRecord(**defaults)


@pytest.fixture
def mock_store() -> MagicMock:
    store = MagicMock()
    store.get_step_attempt.return_value = SimpleNamespace(context={})
    store.create_reconciliation.return_value = _make_reconciliation()
    store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")
    return store


@pytest.fixture
def mock_artifact_store() -> MagicMock:
    artifact_store = MagicMock()
    artifact_store.store_json.return_value = ("file:///recon.json", "hash-1")
    return artifact_store


@pytest.fixture
def mock_reconcile_service() -> MagicMock:
    svc = MagicMock()
    svc.reconcile.return_value = ReconcileOutcome(
        result_code="reconciled_applied",
        summary="Applied",
        observed_refs=["ref-1"],
    )
    return svc


@pytest.fixture
def svc(
    mock_store: MagicMock,
    mock_artifact_store: MagicMock,
    mock_reconcile_service: MagicMock,
) -> ReconciliationService:
    return ReconciliationService(
        store=mock_store,
        artifact_store=mock_artifact_store,
        reconcile_service=mock_reconcile_service,
    )


# ---------------------------------------------------------------------------
# TestReconcileAttemptExisting
# ---------------------------------------------------------------------------


class TestReconcileAttemptExisting:
    def test_returns_existing_when_found(
        self,
        svc: ReconciliationService,
        mock_store: MagicMock,
        mock_reconcile_service: MagicMock,
    ) -> None:
        existing = _make_reconciliation(
            result_class="satisfied",
            observed_effect_summary="existing ok",
            observed_output_refs=["out-1"],
            receipt_refs=["rcpt-1"],
        )
        mock_store.list_reconciliations.return_value = [existing]
        ctx = _make_attempt_ctx()

        rec, outcome, _artifact_ref = svc.reconcile_attempt(
            attempt_ctx=ctx,
            contract_ref="contract-1",
            receipt_ref="rcpt-1",
            action_type="write_local",
            tool_input={},
            workspace_root="/tmp/ws",
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )

        assert rec is existing
        assert outcome.result_code == "satisfied"
        assert outcome.summary == "existing ok"
        mock_reconcile_service.reconcile.assert_not_called()

    def test_no_match_creates_new(
        self,
        svc: ReconciliationService,
        mock_store: MagicMock,
        mock_reconcile_service: MagicMock,
    ) -> None:
        existing = _make_reconciliation(receipt_refs=["other-rcpt"])
        mock_store.list_reconciliations.return_value = [existing]
        ctx = _make_attempt_ctx()

        _rec, _outcome, _artifact_ref = svc.reconcile_attempt(
            attempt_ctx=ctx,
            contract_ref="contract-1",
            receipt_ref="rcpt-1",
            action_type="write_local",
            tool_input={},
            workspace_root="/tmp/ws",
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )

        mock_reconcile_service.reconcile.assert_called_once()
        mock_store.create_reconciliation.assert_called_once()


# ---------------------------------------------------------------------------
# TestReconcileAttemptNew
# ---------------------------------------------------------------------------


class TestReconcileAttemptNew:
    def test_creates_new_reconciliation(
        self,
        svc: ReconciliationService,
        mock_store: MagicMock,
        mock_reconcile_service: MagicMock,
    ) -> None:
        # No list_reconciliations method → no existing found
        del mock_store.list_reconciliations
        ctx = _make_attempt_ctx()

        _rec, _outcome, _artifact_ref = svc.reconcile_attempt(
            attempt_ctx=ctx,
            contract_ref="contract-1",
            receipt_ref="rcpt-1",
            action_type="write_local",
            tool_input={},
            workspace_root="/tmp/ws",
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )

        mock_reconcile_service.reconcile.assert_called_once()
        mock_store.create_reconciliation.assert_called_once()
        mock_store.append_event.assert_called_once()
        event_kwargs = mock_store.append_event.call_args[1]
        assert event_kwargs["event_type"] == "reconciliation.closed"

    def test_stores_artifact(
        self,
        svc: ReconciliationService,
        mock_store: MagicMock,
        mock_artifact_store: MagicMock,
    ) -> None:
        del mock_store.list_reconciliations
        ctx = _make_attempt_ctx()

        svc.reconcile_attempt(
            attempt_ctx=ctx,
            contract_ref="contract-1",
            receipt_ref="rcpt-1",
            action_type="write_local",
            tool_input={},
            workspace_root="/tmp/ws",
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )

        mock_artifact_store.store_json.assert_called_once()
        mock_store.create_artifact.assert_called_once()

    def test_updates_step_attempt_with_reconciliation_ref(
        self,
        svc: ReconciliationService,
        mock_store: MagicMock,
    ) -> None:
        del mock_store.list_reconciliations
        ctx = _make_attempt_ctx()

        svc.reconcile_attempt(
            attempt_ctx=ctx,
            contract_ref="contract-1",
            receipt_ref="rcpt-1",
            action_type="write_local",
            tool_input={},
            workspace_root="/tmp/ws",
            observables=None,
            witness=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )

        mock_store.update_step_attempt.assert_called_once()
        call_kwargs = mock_store.update_step_attempt.call_args[1]
        assert call_kwargs["reconciliation_ref"] == "recon-1"


# ---------------------------------------------------------------------------
# TestResultClass
# ---------------------------------------------------------------------------


class TestResultClass:
    @pytest.mark.parametrize(
        ("hint", "result_code", "expected"),
        [
            ("dispatch_denied", "reconciled_applied", "unauthorized"),
            ("denied", "reconciled_applied", "unauthorized"),
            ("unknown_outcome", "reconciled_applied", "partial"),
            ("unknown_outcome", "reconciled_observed", "partial"),
            ("unknown_outcome", "still_unknown", "ambiguous"),
            ("drifted", "reconciled_applied", "drifted"),
            ("witness_drift", "reconciled_applied", "drifted"),
            ("contract_expiry", "reconciled_applied", "drifted"),
            ("policy_version_drift", "reconciled_applied", "drifted"),
            ("rolled_back", "reconciled_applied", "rolled_back"),
            ("rollback_succeeded", "reconciled_applied", "rolled_back"),
            ("succeeded", "reconciled_applied", "satisfied"),
            ("succeeded", "reconciled_observed", "satisfied"),
            ("succeeded", "reconciled_inferred", "satisfied"),
            ("succeeded", "reconciled_not_applied", "violated"),
            ("succeeded", "still_unknown", "satisfied_with_downgrade"),
            ("other", "reconciled_applied", "satisfied"),
            ("other", "reconciled_inferred", "satisfied"),
            ("other", "reconciled_not_applied", "violated"),
            ("other", "other_code", "ambiguous"),
        ],
    )
    def test_result_class_mapping(self, hint: str, result_code: str, expected: str) -> None:
        outcome = ReconcileOutcome(result_code=result_code, summary="", observed_refs=[])
        assert ReconciliationService._result_class(outcome, result_code_hint=hint) == expected


# ---------------------------------------------------------------------------
# TestRecommendedResolution
# ---------------------------------------------------------------------------


class TestRecommendedResolution:
    @pytest.mark.parametrize(
        ("result_class", "expected"),
        [
            ("satisfied", "promote_learning"),
            ("satisfied_with_downgrade", "promote_learning"),
            ("violated", "gather_more_evidence"),
            ("unauthorized", "request_authority"),
            ("drifted", "reenter_policy"),
            ("rolled_back", "confirm_rollback"),
            ("ambiguous", "park_and_escalate"),
            ("partial", "park_and_escalate"),
            ("unknown", "park_and_escalate"),
        ],
    )
    def test_resolution_mapping(self, result_class: str, expected: str) -> None:
        assert ReconciliationService._recommended_resolution(result_class) == expected


# ---------------------------------------------------------------------------
# TestConfidenceDelta
# ---------------------------------------------------------------------------


class TestConfidenceDelta:
    @pytest.mark.parametrize(
        ("result_code", "expected"),
        [
            ("reconciled_applied", 0.2),
            ("reconciled_observed", 0.2),
            ("reconciled_inferred", 0.05),
            ("reconciled_not_applied", -0.3),
            ("still_unknown", -0.1),
            ("other", -0.1),
        ],
    )
    def test_confidence_delta(self, result_code: str, expected: float) -> None:
        outcome = ReconcileOutcome(result_code=result_code, summary="", observed_refs=[])
        assert ReconciliationService._confidence_delta(outcome) == expected


# ---------------------------------------------------------------------------
# TestFindExistingReconciliation
# ---------------------------------------------------------------------------


class TestFindExistingReconciliation:
    def test_returns_matching(self, svc: ReconciliationService, mock_store: MagicMock) -> None:
        matching = _make_reconciliation(receipt_refs=["rcpt-1"])
        mock_store.list_reconciliations.return_value = [matching]
        result = svc._find_existing_reconciliation("attempt-1", "rcpt-1")
        assert result is matching

    def test_returns_none_when_no_match(
        self, svc: ReconciliationService, mock_store: MagicMock
    ) -> None:
        non_matching = _make_reconciliation(receipt_refs=["rcpt-other"])
        mock_store.list_reconciliations.return_value = [non_matching]
        result = svc._find_existing_reconciliation("attempt-1", "rcpt-1")
        assert result is None

    def test_returns_none_when_store_lacks_method(
        self, svc: ReconciliationService, mock_store: MagicMock
    ) -> None:
        del mock_store.list_reconciliations
        result = svc._find_existing_reconciliation("attempt-1", "rcpt-1")
        assert result is None

    def test_returns_none_when_empty_list(
        self, svc: ReconciliationService, mock_store: MagicMock
    ) -> None:
        mock_store.list_reconciliations.return_value = []
        result = svc._find_existing_reconciliation("attempt-1", "rcpt-1")
        assert result is None


# ---------------------------------------------------------------------------
# TestStoreArtifact
# ---------------------------------------------------------------------------


class TestStoreArtifact:
    def test_stores_and_returns_ref(
        self,
        svc: ReconciliationService,
        mock_store: MagicMock,
        mock_artifact_store: MagicMock,
    ) -> None:
        ctx = _make_attempt_ctx()
        result = svc._store_artifact(
            reconciliation_ref="recon-1",
            attempt_ctx=ctx,
            payload={"key": "value"},
        )
        mock_artifact_store.store_json.assert_called_once_with({"key": "value"})
        mock_store.create_artifact.assert_called_once()
        assert result == "art-1"
        create_kwargs = mock_store.create_artifact.call_args[1]
        assert create_kwargs["kind"] == "reconciliation.record"
        assert create_kwargs["producer"] == "reconciliation_service"
        assert create_kwargs["retention_class"] == "audit"
        assert create_kwargs["trust_tier"] == "derived"
