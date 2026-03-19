"""Tests for kernel/artifacts/lineage/evidence_cases.py — EvidenceCaseService."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from hermit.kernel.artifacts.lineage.evidence_cases import EvidenceCaseService
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.policy import ActionRequest, PolicyDecision, PolicyObligations
from hermit.kernel.task.models.records import EvidenceCaseRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx() -> TaskExecutionContext:
    return TaskExecutionContext(
        conversation_id="conv-1",
        task_id="task-1",
        step_id="step-1",
        step_attempt_id="attempt-1",
        source_channel="cli",
    )


def _action_request(action_class: str = "write_file") -> ActionRequest:
    return ActionRequest(
        request_id="req-1",
        task_id="task-1",
        step_id="step-1",
        step_attempt_id="attempt-1",
        action_class=action_class,
    )


def _policy(
    *,
    risk_level: str = "low",
    require_evidence: bool = False,
) -> PolicyDecision:
    return PolicyDecision(
        verdict="allow",
        action_class="write_file",
        risk_level=risk_level,
        obligations=PolicyObligations(require_evidence=require_evidence),
    )


def _evidence_case(
    *,
    evidence_case_id: str = "ec-1",
    task_id: str = "task-1",
    status: str = "sufficient",
    subject_ref: str = "contract-1",
    sufficiency_score: float = 0.8,
) -> EvidenceCaseRecord:
    return EvidenceCaseRecord(
        evidence_case_id=evidence_case_id,
        task_id=task_id,
        subject_kind="contract",
        subject_ref=subject_ref,
        sufficiency_score=sufficiency_score,
        status=status,
    )


def _mock_store() -> MagicMock:
    store = MagicMock()
    store.create_evidence_case.return_value = _evidence_case()
    store.create_artifact.return_value = SimpleNamespace(artifact_id="art-1")
    store.get_step_attempt.return_value = SimpleNamespace(
        context={}, task_id="task-1", step_id="step-1"
    )
    store.get_evidence_case.return_value = _evidence_case()
    store.list_evidence_cases.return_value = []
    return store


def _mock_artifact_store() -> MagicMock:
    artifact_store = MagicMock()
    artifact_store.store_json.return_value = ("uri://ec", "hash123")
    return artifact_store


# ---------------------------------------------------------------------------
# compile_for_contract
# ---------------------------------------------------------------------------


class TestCompileForContract:
    def test_creates_evidence_case(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        _ec, ref = svc.compile_for_contract(
            attempt_ctx=_ctx(),
            contract_ref="contract-1",
            action_request=_action_request(),
            policy=_policy(),
            context_pack_ref="cp-1",
            action_request_ref="ar-1",
            policy_result_ref="pr-1",
            witness_ref="w-1",
        )

        store.create_evidence_case.assert_called_once()
        assert ref == "art-1"

    def test_sufficient_with_all_refs(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.compile_for_contract(
            attempt_ctx=_ctx(),
            contract_ref="contract-1",
            action_request=_action_request(),
            policy=_policy(),
            context_pack_ref="cp-1",
            action_request_ref="ar-1",
            policy_result_ref="pr-1",
            witness_ref="w-1",
        )

        call_kwargs = store.create_evidence_case.call_args[1]
        assert call_kwargs["sufficiency_score"] >= 0.5
        assert call_kwargs["status"] == "sufficient"
        assert len(call_kwargs["support_refs"]) == 4

    def test_insufficient_when_evidence_required_but_no_witness(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.compile_for_contract(
            attempt_ctx=_ctx(),
            contract_ref="contract-1",
            action_request=_action_request(),
            policy=_policy(require_evidence=True),
            context_pack_ref="cp-1",
            action_request_ref="ar-1",
            policy_result_ref="pr-1",
            witness_ref=None,
        )

        call_kwargs = store.create_evidence_case.call_args[1]
        assert call_kwargs["status"] == "insufficient"
        assert "missing_required_witness" in call_kwargs["unresolved_gaps"]

    def test_drift_sensitivity_high_with_witness(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.compile_for_contract(
            attempt_ctx=_ctx(),
            contract_ref="contract-1",
            action_request=_action_request(),
            policy=_policy(),
            context_pack_ref=None,
            action_request_ref=None,
            policy_result_ref=None,
            witness_ref="w-1",
        )

        call_kwargs = store.create_evidence_case.call_args[1]
        assert call_kwargs["drift_sensitivity"] == "high"

    def test_drift_sensitivity_medium_without_witness(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.compile_for_contract(
            attempt_ctx=_ctx(),
            contract_ref="contract-1",
            action_request=_action_request(),
            policy=_policy(),
            context_pack_ref="cp-1",
            action_request_ref=None,
            policy_result_ref=None,
            witness_ref=None,
        )

        call_kwargs = store.create_evidence_case.call_args[1]
        assert call_kwargs["drift_sensitivity"] == "medium"

    def test_updates_step_attempt(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.compile_for_contract(
            attempt_ctx=_ctx(),
            contract_ref="contract-1",
            action_request=_action_request(),
            policy=_policy(),
            context_pack_ref=None,
            action_request_ref=None,
            policy_result_ref=None,
            witness_ref=None,
        )

        store.update_step_attempt.assert_called_once()

    def test_emits_event(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.compile_for_contract(
            attempt_ctx=_ctx(),
            contract_ref="contract-1",
            action_request=_action_request(),
            policy=_policy(),
            context_pack_ref=None,
            action_request_ref=None,
            policy_result_ref=None,
            witness_ref=None,
        )

        store.append_event.assert_called()
        event_call = store.append_event.call_args
        assert event_call[1]["event_type"] == "evidence_case.selected"

    def test_updates_execution_contract(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.compile_for_contract(
            attempt_ctx=_ctx(),
            contract_ref="contract-1",
            action_request=_action_request(),
            policy=_policy(),
            context_pack_ref=None,
            action_request_ref=None,
            policy_result_ref=None,
            witness_ref=None,
        )

        store.update_execution_contract.assert_called_once_with(
            "contract-1",
            evidence_case_ref="ec-1",
            status="admissibility_pending",
        )

    def test_sufficiency_score_clamped_0_to_1(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        # No refs → low score; with many gaps → ensure clamping to [0,1]
        svc.compile_for_contract(
            attempt_ctx=_ctx(),
            contract_ref="contract-1",
            action_request=_action_request(),
            policy=_policy(require_evidence=True),
            context_pack_ref=None,
            action_request_ref=None,
            policy_result_ref=None,
            witness_ref=None,
        )

        call_kwargs = store.create_evidence_case.call_args[1]
        score = call_kwargs["sufficiency_score"]
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# invalidate / mark_stale / mark_expired / mark_superseded
# ---------------------------------------------------------------------------


class TestInvalidation:
    def test_invalidate_updates_store(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.invalidate(
            "ec-1",
            contradictions=["drift"],
            summary="Evidence drifted",
        )

        store.update_evidence_case.assert_called_once_with(
            "ec-1",
            status="invalidated",
            contradiction_refs=["drift"],
            operator_summary="Evidence drifted",
        )

    def test_invalidate_emits_event(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.invalidate("ec-1", contradictions=[], summary="test")

        store.append_event.assert_called_once()
        event_kwargs = store.append_event.call_args[1]
        assert event_kwargs["event_type"] == "evidence_case.invalidated"

    def test_invalidate_nonexistent_does_nothing(self) -> None:
        store = _mock_store()
        store.get_evidence_case.return_value = None
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.invalidate("ec-nonexistent", contradictions=[], summary="test")

        store.update_evidence_case.assert_not_called()

    def test_mark_stale(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.mark_stale("ec-1", summary="policy changed")

        call_kwargs = store.update_evidence_case.call_args[1]
        assert call_kwargs["status"] == "stale"

    def test_mark_expired(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.mark_expired("ec-1", summary="contract expired")

        call_kwargs = store.update_evidence_case.call_args[1]
        assert call_kwargs["status"] == "expired"

    def test_mark_superseded(self) -> None:
        store = _mock_store()
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        svc.mark_superseded("ec-1", superseded_by="ec-2", summary="replaced")

        call_kwargs = store.update_evidence_case.call_args[1]
        assert call_kwargs["status"] == "superseded"
        assert "superseded_by:ec-2" in call_kwargs["contradiction_refs"]


# ---------------------------------------------------------------------------
# _find_prior_contradictions
# ---------------------------------------------------------------------------


class TestFindPriorContradictions:
    def test_finds_invalidated_cases(self) -> None:
        store = _mock_store()
        invalidated = _evidence_case(
            evidence_case_id="old-ec",
            status="invalidated",
            subject_ref="contract-1",
        )
        store.list_evidence_cases.return_value = [invalidated]
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        refs = svc._find_prior_contradictions("task-1", "contract-1")

        assert refs == ["old-ec"]

    def test_ignores_non_invalidated(self) -> None:
        store = _mock_store()
        active = _evidence_case(evidence_case_id="active-ec", status="sufficient")
        store.list_evidence_cases.return_value = [active]
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        refs = svc._find_prior_contradictions("task-1", "contract-1")

        assert refs == []

    def test_no_list_evidence_cases_method(self) -> None:
        store = _mock_store()
        del store.list_evidence_cases
        artifact_store = _mock_artifact_store()
        svc = EvidenceCaseService(store, artifact_store)

        refs = svc._find_prior_contradictions("task-1", "contract-1")

        assert refs == []
