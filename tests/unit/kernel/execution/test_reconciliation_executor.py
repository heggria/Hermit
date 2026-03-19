"""Unit tests for ReconciliationExecutor."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.reconciliation_executor import ReconciliationExecutor
from hermit.kernel.execution.recovery.reconcile import ReconcileOutcome
from hermit.kernel.policy.models.models import ActionRequest
from hermit.kernel.task.models.records import ReconciliationRecord

# ---------------------------------------------------------------------------
# Fixtures
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


def _make_attempt_record(**overrides: Any) -> SimpleNamespace:
    defaults = {
        "step_attempt_id": "attempt-1",
        "execution_contract_ref": "contract-1",
        "evidence_case_ref": "evidence-1",
        "authorization_plan_ref": "authplan-1",
        "context": {},
        "selected_contract_template_ref": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def mock_store() -> MagicMock:
    store = MagicMock()
    store.get_step_attempt.return_value = _make_attempt_record()
    store.get_execution_contract.return_value = SimpleNamespace(
        contract_id="contract-1", operator_summary="test"
    )
    store.list_memory_records.return_value = []
    return store


@pytest.fixture
def mock_deps() -> dict[str, MagicMock]:
    return {
        "artifact_store": MagicMock(),
        "reconciliations": MagicMock(),
        "execution_contracts": MagicMock(),
        "evidence_cases": MagicMock(),
        "pattern_learner": MagicMock(),
    }


@pytest.fixture
def executor(mock_store: MagicMock, mock_deps: dict[str, MagicMock]) -> ReconciliationExecutor:
    return ReconciliationExecutor(store=mock_store, **mock_deps)


# ---------------------------------------------------------------------------
# _contract_refs
# ---------------------------------------------------------------------------


class TestContractRefs:
    def test_returns_refs_from_attempt(
        self, executor: ReconciliationExecutor, mock_store: MagicMock
    ) -> None:
        ctx = _make_attempt_ctx()
        refs = executor._contract_refs(ctx)
        assert refs == ("contract-1", "evidence-1", "authplan-1")
        mock_store.get_step_attempt.assert_called_once_with("attempt-1")

    def test_returns_none_tuple_when_attempt_missing(
        self, executor: ReconciliationExecutor, mock_store: MagicMock
    ) -> None:
        mock_store.get_step_attempt.return_value = None
        ctx = _make_attempt_ctx()
        assert executor._contract_refs(ctx) == (None, None, None)


# ---------------------------------------------------------------------------
# _set_attempt_phase
# ---------------------------------------------------------------------------


class TestSetAttemptPhase:
    def test_updates_phase_and_emits_event(
        self, executor: ReconciliationExecutor, mock_store: MagicMock
    ) -> None:
        ctx = _make_attempt_ctx()
        executor._set_attempt_phase(ctx, "reconciling", reason="test")
        mock_store.update_step_attempt.assert_called_once_with(
            "attempt-1", context={"phase": "reconciling"}
        )
        mock_store.append_event.assert_called_once()
        payload = mock_store.append_event.call_args[1]["payload"]
        assert payload["phase"] == "reconciling"
        assert payload["reason"] == "test"

    def test_skips_when_phase_unchanged(
        self, executor: ReconciliationExecutor, mock_store: MagicMock
    ) -> None:
        mock_store.get_step_attempt.return_value = _make_attempt_record(
            context={"phase": "executing"}
        )
        ctx = _make_attempt_ctx()
        executor._set_attempt_phase(ctx, "executing")
        mock_store.update_step_attempt.assert_not_called()
        mock_store.append_event.assert_not_called()

    def test_noop_when_attempt_missing(
        self, executor: ReconciliationExecutor, mock_store: MagicMock
    ) -> None:
        mock_store.get_step_attempt.return_value = None
        ctx = _make_attempt_ctx()
        executor._set_attempt_phase(ctx, "reconciling")
        mock_store.update_step_attempt.assert_not_called()


# ---------------------------------------------------------------------------
# _load_witness_payload
# ---------------------------------------------------------------------------


class TestLoadWitnessPayload:
    def test_returns_empty_dict_for_none_ref(self, executor: ReconciliationExecutor) -> None:
        assert executor._load_witness_payload(None) == {}

    def test_returns_empty_dict_when_artifact_missing(
        self, executor: ReconciliationExecutor, mock_store: MagicMock
    ) -> None:
        mock_store.get_artifact.return_value = None
        assert executor._load_witness_payload("witness-1") == {}

    def test_returns_parsed_dict(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        artifact = SimpleNamespace(uri="file:///witness.json")
        mock_store.get_artifact.return_value = artifact
        mock_deps["artifact_store"].read_text.return_value = json.dumps({"key": "value"})
        assert executor._load_witness_payload("witness-1") == {"key": "value"}

    def test_returns_empty_dict_on_json_error(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        artifact = SimpleNamespace(uri="file:///bad.json")
        mock_store.get_artifact.return_value = artifact
        mock_deps["artifact_store"].read_text.return_value = "not json"
        assert executor._load_witness_payload("witness-1") == {}

    def test_returns_empty_dict_on_os_error(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        artifact = SimpleNamespace(uri="file:///missing.json")
        mock_store.get_artifact.return_value = artifact
        mock_deps["artifact_store"].read_text.side_effect = OSError("not found")
        assert executor._load_witness_payload("witness-1") == {}

    def test_returns_empty_dict_for_non_dict_json(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        artifact = SimpleNamespace(uri="file:///array.json")
        mock_store.get_artifact.return_value = artifact
        mock_deps["artifact_store"].read_text.return_value = json.dumps([1, 2, 3])
        assert executor._load_witness_payload("witness-1") == {}


# ---------------------------------------------------------------------------
# record_reconciliation
# ---------------------------------------------------------------------------


class TestRecordReconciliation:
    def _setup_reconcile(
        self,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
        result_class: str = "satisfied",
    ) -> tuple[ReconciliationRecord, ReconcileOutcome]:
        reconciliation = _make_reconciliation(result_class=result_class)
        outcome = ReconcileOutcome(result_code="reconciled_applied", summary="ok", observed_refs=[])
        mock_deps["reconciliations"].reconcile_attempt.return_value = (
            reconciliation,
            outcome,
            None,
        )
        return reconciliation, outcome

    def test_returns_none_when_no_contract_ref(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
    ) -> None:
        mock_store.get_step_attempt.return_value = _make_attempt_record(execution_contract_ref=None)
        ctx = _make_attempt_ctx()
        rec, out = executor.record_reconciliation(
            attempt_ctx=ctx,
            receipt_id="rcpt-1",
            action_type="write_local",
            tool_input={},
            observables=None,
            witness_ref=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )
        assert rec is None
        assert out is None

    def test_satisfied_completes_task(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        self._setup_reconcile(mock_store, mock_deps, result_class="satisfied")
        ctx = _make_attempt_ctx()
        rec, _out = executor.record_reconciliation(
            attempt_ctx=ctx,
            receipt_id="rcpt-1",
            action_type="write_local",
            tool_input={},
            observables=None,
            witness_ref=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )
        assert rec is not None
        assert rec.result_class == "satisfied"
        mock_store.update_step_attempt.assert_any_call("attempt-1", status="succeeded")
        mock_store.update_step.assert_any_call("step-1", status="succeeded")
        mock_store.update_task_status.assert_any_call("task-1", "completed")
        mock_deps["pattern_learner"].learn_from_completed_task.assert_called_once_with("task-1")

    def test_violated_invalidates_and_degrades(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        self._setup_reconcile(mock_store, mock_deps, result_class="violated")
        ctx = _make_attempt_ctx()
        rec, _ = executor.record_reconciliation(
            attempt_ctx=ctx,
            receipt_id="rcpt-1",
            action_type="write_local",
            tool_input={},
            observables=None,
            witness_ref=None,
            result_code_hint="failed",
            authorized_effect_summary="test",
        )
        assert rec is not None
        mock_store.update_execution_contract.assert_called_once()
        status_arg = mock_store.update_execution_contract.call_args[1].get(
            "status",
            mock_store.update_execution_contract.call_args[0][1]
            if len(mock_store.update_execution_contract.call_args[0]) > 1
            else None,
        )
        assert status_arg == "violated"
        mock_store.update_task_status.assert_any_call("task-1", "failed")

    def test_partial_sets_reconciling_status(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        self._setup_reconcile(mock_store, mock_deps, result_class="partial")
        ctx = _make_attempt_ctx()
        rec, _ = executor.record_reconciliation(
            attempt_ctx=ctx,
            receipt_id="rcpt-1",
            action_type="write_local",
            tool_input={},
            observables=None,
            witness_ref=None,
            result_code_hint="partial",
            authorized_effect_summary="test",
        )
        assert rec is not None
        mock_store.update_task_status.assert_any_call("task-1", "reconciling")

    def test_ambiguous_sets_needs_attention(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        self._setup_reconcile(mock_store, mock_deps, result_class="ambiguous")
        ctx = _make_attempt_ctx()
        rec, _ = executor.record_reconciliation(
            attempt_ctx=ctx,
            receipt_id="rcpt-1",
            action_type="write_local",
            tool_input={},
            observables=None,
            witness_ref=None,
            result_code_hint="ambiguous",
            authorized_effect_summary="test",
        )
        assert rec is not None
        mock_store.update_task_status.assert_any_call("task-1", "needs_attention")

    def test_unauthorized_sets_needs_attention(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        self._setup_reconcile(mock_store, mock_deps, result_class="unauthorized")
        ctx = _make_attempt_ctx()
        rec, _ = executor.record_reconciliation(
            attempt_ctx=ctx,
            receipt_id="rcpt-1",
            action_type="write_local",
            tool_input={},
            observables=None,
            witness_ref=None,
            result_code_hint="unauthorized",
            authorized_effect_summary="test",
        )
        assert rec is not None
        mock_store.update_task_status.assert_any_call("task-1", "needs_attention")

    def test_resume_execution_returns_to_running(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        self._setup_reconcile(mock_store, mock_deps, result_class="satisfied")
        ctx = _make_attempt_ctx()
        rec, _ = executor.record_reconciliation(
            attempt_ctx=ctx,
            receipt_id="rcpt-1",
            action_type="write_local",
            tool_input={},
            observables=None,
            witness_ref=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
            resume_execution=True,
        )
        assert rec is not None
        mock_store.update_step_attempt.assert_any_call("attempt-1", status="running")
        # Should NOT complete the task when resuming
        task_status_calls = [
            c
            for c in mock_store.update_task_status.call_args_list
            if c == call("task-1", "completed")
        ]
        assert len(task_status_calls) == 0

    def test_contract_status_mapping_satisfied_with_downgrade(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        self._setup_reconcile(mock_store, mock_deps, result_class="satisfied_with_downgrade")
        ctx = _make_attempt_ctx()
        executor.record_reconciliation(
            attempt_ctx=ctx,
            receipt_id="rcpt-1",
            action_type="write_local",
            tool_input={},
            observables=None,
            witness_ref=None,
            result_code_hint="partial",
            authorized_effect_summary="test",
        )
        contract_call = mock_store.update_execution_contract.call_args
        assert contract_call[1].get("status") == "partially_satisfied"

    def test_contract_status_mapping_satisfied_sets_closed(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        """Satisfied reconciliation must transition the contract to the closed terminal state."""
        self._setup_reconcile(mock_store, mock_deps, result_class="satisfied")
        ctx = _make_attempt_ctx()
        executor.record_reconciliation(
            attempt_ctx=ctx,
            receipt_id="rcpt-1",
            action_type="write_local",
            tool_input={},
            observables=None,
            witness_ref=None,
            result_code_hint="succeeded",
            authorized_effect_summary="test",
        )
        contract_call = mock_store.update_execution_contract.call_args
        assert contract_call[1].get("status") == "closed"


# ---------------------------------------------------------------------------
# learn_contract_template
# ---------------------------------------------------------------------------


class TestLearnContractTemplate:
    def test_learns_when_contract_exists(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        reconciliation = _make_reconciliation()
        executor.learn_contract_template(reconciliation, "contract-1")
        mock_deps[
            "execution_contracts"
        ].template_learner.learn_from_reconciliation.assert_called_once()

    def test_skips_when_contract_missing(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        mock_store.get_execution_contract.return_value = None
        reconciliation = _make_reconciliation()
        executor.learn_contract_template(reconciliation, "contract-1")
        mock_deps[
            "execution_contracts"
        ].template_learner.learn_from_reconciliation.assert_not_called()


# ---------------------------------------------------------------------------
# learn_task_pattern
# ---------------------------------------------------------------------------


class TestLearnTaskPattern:
    def test_delegates_to_pattern_learner(
        self, executor: ReconciliationExecutor, mock_deps: dict[str, MagicMock]
    ) -> None:
        executor.learn_task_pattern("task-1")
        mock_deps["pattern_learner"].learn_from_completed_task.assert_called_once_with("task-1")


# ---------------------------------------------------------------------------
# record_template_outcome
# ---------------------------------------------------------------------------


class TestRecordTemplateOutcome:
    def test_records_when_template_ref_exists(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        mock_store.get_step_attempt.return_value = _make_attempt_record(
            selected_contract_template_ref="tmpl-1"
        )
        ctx = _make_attempt_ctx()
        executor.record_template_outcome(ctx, "satisfied")
        mock_deps[
            "execution_contracts"
        ].template_learner.record_template_outcome.assert_called_once_with(
            template_ref="tmpl-1",
            result_class="satisfied",
            task_id="task-1",
            step_id="step-1",
        )

    def test_skips_when_no_template_ref(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        ctx = _make_attempt_ctx()
        executor.record_template_outcome(ctx, "satisfied")
        mock_deps[
            "execution_contracts"
        ].template_learner.record_template_outcome.assert_not_called()

    def test_skips_when_attempt_missing(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
        mock_deps: dict[str, MagicMock],
    ) -> None:
        mock_store.get_step_attempt.return_value = None
        ctx = _make_attempt_ctx()
        executor.record_template_outcome(ctx, "satisfied")
        mock_deps[
            "execution_contracts"
        ].template_learner.record_template_outcome.assert_not_called()


# ---------------------------------------------------------------------------
# degrade_templates_for_violation
# ---------------------------------------------------------------------------


class TestDegradeTemplatesForViolation:
    def test_degrades_when_reconciliation_id_present(
        self, executor: ReconciliationExecutor, mock_deps: dict[str, MagicMock]
    ) -> None:
        reconciliation = _make_reconciliation(reconciliation_id="recon-99")
        executor.degrade_templates_for_violation(reconciliation)
        mock_deps[
            "execution_contracts"
        ].template_learner.degrade_templates_for_violation.assert_called_once_with("recon-99")

    def test_skips_when_reconciliation_id_empty(
        self, executor: ReconciliationExecutor, mock_deps: dict[str, MagicMock]
    ) -> None:
        reconciliation = _make_reconciliation(reconciliation_id="")
        executor.degrade_templates_for_violation(reconciliation)
        mock_deps[
            "execution_contracts"
        ].template_learner.degrade_templates_for_violation.assert_not_called()


# ---------------------------------------------------------------------------
# invalidate_memories_for_reconciliation
# ---------------------------------------------------------------------------


class TestInvalidateMemories:
    def test_invalidates_matching_memory_records(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
    ) -> None:
        matching = SimpleNamespace(memory_id="mem-1", learned_from_reconciliation_ref="recon-1")
        non_matching = SimpleNamespace(
            memory_id="mem-2", learned_from_reconciliation_ref="recon-other"
        )
        mock_store.list_memory_records.return_value = [matching, non_matching]
        reconciliation = _make_reconciliation(reconciliation_id="recon-1")
        executor.invalidate_memories_for_reconciliation(reconciliation)
        mock_store.update_memory_record.assert_called_once()
        call_args = mock_store.update_memory_record.call_args
        assert call_args[0][0] == "mem-1"
        assert call_args[1]["status"] == "invalidated"

    def test_skips_when_reconciliation_id_empty(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
    ) -> None:
        reconciliation = _make_reconciliation(reconciliation_id="")
        executor.invalidate_memories_for_reconciliation(reconciliation)
        mock_store.list_memory_records.assert_not_called()

    def test_skips_when_store_lacks_list_memory_records(
        self,
        executor: ReconciliationExecutor,
        mock_store: MagicMock,
    ) -> None:
        del mock_store.list_memory_records
        reconciliation = _make_reconciliation(reconciliation_id="recon-1")
        executor.invalidate_memories_for_reconciliation(reconciliation)
        mock_store.update_memory_record.assert_not_called()


# ---------------------------------------------------------------------------
# reconciliation_execution_status (static)
# ---------------------------------------------------------------------------


class TestReconciliationExecutionStatus:
    @pytest.mark.parametrize(
        ("result_class", "expected"),
        [
            ("satisfied", "succeeded"),
            ("partial", "reconciling"),
            ("satisfied_with_downgrade", "reconciling"),
            ("ambiguous", "needs_attention"),
            ("unauthorized", "needs_attention"),
            ("violated", "failed"),
            ("unknown_value", "reconciling"),
            ("", "reconciling"),
        ],
    )
    def test_status_mapping(self, result_class: str, expected: str) -> None:
        rec = SimpleNamespace(result_class=result_class)
        assert ReconciliationExecutor.reconciliation_execution_status(rec) == expected

    def test_none_reconciliation(self) -> None:
        assert ReconciliationExecutor.reconciliation_execution_status(None) == "reconciling"


# ---------------------------------------------------------------------------
# authorized_effect_summary (static)
# ---------------------------------------------------------------------------


class TestAuthorizedEffectSummary:
    def test_uses_contract_operator_summary_when_present(self) -> None:
        action_request = ActionRequest(request_id="req-1")
        contract = SimpleNamespace(operator_summary="contract summary")
        result = ReconciliationExecutor.authorized_effect_summary(
            action_request=action_request, contract=contract
        )
        assert result == "contract summary"

    def test_target_paths(self) -> None:
        action_request = ActionRequest(
            request_id="req-1",
            derived={"target_paths": ["/a/b", "/c/d"]},
        )
        result = ReconciliationExecutor.authorized_effect_summary(
            action_request=action_request, contract=None
        )
        assert "2 path(s)" in result

    def test_network_hosts(self) -> None:
        action_request = ActionRequest(
            request_id="req-1",
            derived={"network_hosts": ["example.com"]},
        )
        result = ReconciliationExecutor.authorized_effect_summary(
            action_request=action_request, contract=None
        )
        assert "example.com" in result

    def test_command_preview(self) -> None:
        action_request = ActionRequest(
            request_id="req-1",
            derived={"command_preview": "ls -la"},
        )
        result = ReconciliationExecutor.authorized_effect_summary(
            action_request=action_request, contract=None
        )
        assert "ls -la" in result

    def test_fallback_to_action_class(self) -> None:
        action_request = ActionRequest(
            request_id="req-1",
            action_class="write_local",
            derived={},
        )
        result = ReconciliationExecutor.authorized_effect_summary(
            action_request=action_request, contract=None
        )
        assert "write_local" in result

    def test_ignores_empty_contract_summary(self) -> None:
        action_request = ActionRequest(
            request_id="req-1",
            derived={"target_paths": ["/x"]},
        )
        contract = SimpleNamespace(operator_summary="")
        result = ReconciliationExecutor.authorized_effect_summary(
            action_request=action_request, contract=contract
        )
        assert "1 path(s)" in result
