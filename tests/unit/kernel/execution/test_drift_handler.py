"""Unit tests for DriftHandler drift supersession logic."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.execution.executor.drift_handler import (
    _AUTH_STATUS_OVERRIDES,
    _AUTHORIZATION_SUMMARIES,
    _EVIDENCE_SUMMARIES,
    _MAX_DRIFT_REENTRIES,
    _REASON_KEYS,
    _REENTRY_BOUNDARIES,
    DriftHandler,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    *,
    step_attempt_id: str = "sa-1",
    task_id: str = "task-1",
    step_id: str = "step-1",
) -> TaskExecutionContext:
    return TaskExecutionContext(
        conversation_id="conv-1",
        task_id=task_id,
        step_id=step_id,
        step_attempt_id=step_attempt_id,
        source_channel="test",
    )


def _make_attempt(
    *,
    step_attempt_id: str = "sa-1",
    task_id: str = "task-1",
    step_id: str = "step-1",
    attempt: int = 1,
    context: dict[str, Any] | None = None,
    queue_priority: int = 0,
    contract_version: int = 1,
    execution_contract_ref: str | None = "contract-1",
    evidence_case_ref: str | None = "ec-1",
    authorization_plan_ref: str | None = "ap-1",
    approval_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        step_attempt_id=step_attempt_id,
        task_id=task_id,
        step_id=step_id,
        attempt=attempt,
        context=context or {},
        queue_priority=queue_priority,
        contract_version=contract_version,
        execution_contract_ref=execution_contract_ref,
        evidence_case_ref=evidence_case_ref,
        authorization_plan_ref=authorization_plan_ref,
        approval_id=approval_id,
    )


def _make_handler() -> tuple[DriftHandler, dict[str, MagicMock]]:
    """Build a DriftHandler wired to MagicMock collaborators."""
    store = MagicMock()
    store.generate_id.return_value = "contract-new"
    artifact_store = MagicMock()
    execution_contracts = MagicMock()
    evidence_cases = MagicMock()
    authorization_plans = MagicMock()

    handler = DriftHandler(
        store=store,
        artifact_store=artifact_store,
        execution_contracts=execution_contracts,
        evidence_cases=evidence_cases,
        authorization_plans=authorization_plans,
    )
    return handler, {
        "store": store,
        "artifact_store": artifact_store,
        "execution_contracts": execution_contracts,
        "evidence_cases": evidence_cases,
        "authorization_plans": authorization_plans,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDriftHandlerSupersession:
    """Core supersede_attempt_for_drift behaviour."""

    def test_raises_on_unknown_step_attempt(self) -> None:
        handler, deps = _make_handler()
        deps["store"].get_step_attempt.return_value = None

        with pytest.raises(KeyError, match="Unknown step attempt"):
            handler.supersede_attempt_for_drift(
                attempt_ctx=_make_ctx(),
                tool_name="bash",
                tool_input={"cmd": "echo hi"},
                drift_reason="witness_drift",
                execute_fn=MagicMock(),
            )

    @pytest.mark.parametrize("drift_reason", list(_REASON_KEYS.keys()))
    def test_supersedes_and_creates_successor_for_known_reasons(self, drift_reason: str) -> None:
        handler, deps = _make_handler()
        current = _make_attempt()
        successor = _make_attempt(step_attempt_id="sa-2", attempt=2)
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = successor

        sentinel = object()
        execute_fn = MagicMock(return_value=sentinel)

        result = handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="write_file",
            tool_input={"path": "/tmp/x"},
            drift_reason=drift_reason,
            execute_fn=execute_fn,
        )

        assert result is sentinel
        execute_fn.assert_called_once()
        # The successor context should be passed, not the original
        call_ctx = execute_fn.call_args[0][0]
        assert call_ctx.step_attempt_id == "sa-2"

    def test_current_attempt_marked_superseded(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt()
        successor = _make_attempt(step_attempt_id="sa-2", attempt=2)
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = successor
        # CAS returns True so supersession proceeds
        deps["store"].try_supersede_step_attempt.return_value = True

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(return_value=None),
        )

        # try_supersede_step_attempt is called with the attempt id and finished_at
        cas_call = deps["store"].try_supersede_step_attempt.call_args
        assert cas_call.args[0] == "sa-1"
        assert "finished_at" in cas_call.kwargs

    def test_step_set_to_awaiting_approval(self) -> None:
        handler, deps = _make_handler()
        deps["store"].get_step_attempt.return_value = _make_attempt()
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(return_value=None),
        )

        deps["store"].update_step.assert_called_once_with("step-1", status="awaiting_approval")

    def test_task_set_to_blocked(self) -> None:
        handler, deps = _make_handler()
        deps["store"].get_step_attempt.return_value = _make_attempt()
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(return_value=None),
        )

        deps["store"].update_task_status.assert_called_once_with("task-1", "blocked")

    def test_successor_attempt_increments_attempt_and_contract_version(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(attempt=3, contract_version=5)
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-succ", attempt=4
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="evidence_drift",
            execute_fn=MagicMock(return_value=None),
        )

        create_call = deps["store"].create_step_attempt.call_args
        assert create_call.kwargs["attempt"] == 4
        assert create_call.kwargs["contract_version"] == 6

    def test_successor_context_has_reentry_metadata(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(context={"existing_key": "val"})
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="approval_drift",
            execute_fn=MagicMock(return_value=None),
        )

        ctx_arg = deps["store"].create_step_attempt.call_args.kwargs["context"]
        assert ctx_arg["existing_key"] == "val"
        assert ctx_arg["reentered_via"] == "approval_drift"
        assert ctx_arg["recompile_required"] is True
        assert ctx_arg["reentry_required"] is True
        assert ctx_arg["reentry_boundary"] == "approval_revalidation"
        assert ctx_arg["supersedes_step_attempt_id"] == "sa-1"

    def test_supersession_event_appended(self) -> None:
        handler, deps = _make_handler()
        deps["store"].get_step_attempt.return_value = _make_attempt()
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(return_value=None),
        )

        # Find the step_attempt.superseded event
        superseded_calls = [
            c
            for c in deps["store"].append_event.call_args_list
            if c.kwargs.get("event_type") == "step_attempt.superseded"
        ]
        assert len(superseded_calls) == 1
        payload = superseded_calls[0].kwargs["payload"]
        assert payload["step_attempt_id"] == "sa-1"
        assert payload["superseded_by_step_attempt_id"] == "sa-2"
        assert payload["reason"] == "witness_drift_reenter_policy"

    def test_back_link_set_on_original_attempt(self) -> None:
        handler, deps = _make_handler()
        deps["store"].get_step_attempt.return_value = _make_attempt()
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )
        # CAS returns True so supersession proceeds
        deps["store"].try_supersede_step_attempt.return_value = True

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(return_value=None),
        )

        # The sole update_step_attempt call sets the back-link to the successor
        # (status="superseded" is now handled atomically by try_supersede_step_attempt)
        backlink_call = deps["store"].update_step_attempt.call_args_list[0]
        assert backlink_call == call("sa-1", superseded_by_step_attempt_id="sa-2")


class TestContractSupersession:
    """Contract-specific drift handling."""

    def test_contract_expired_marks_expired_then_supersedes(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(execution_contract_ref="c-old")
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="contract_expiry",
            execute_fn=MagicMock(return_value=None),
        )

        deps["store"].update_execution_contract.assert_called_once_with("c-old", status="expired")
        deps["execution_contracts"].supersede.assert_called_once()

    def test_non_expiry_drift_skips_status_update(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(execution_contract_ref="c-old")
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(return_value=None),
        )

        deps["store"].update_execution_contract.assert_not_called()
        deps["execution_contracts"].supersede.assert_called_once()

    def test_no_contract_ref_skips_contract_handling(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(execution_contract_ref=None)
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(return_value=None),
        )

        deps["execution_contracts"].supersede.assert_not_called()


class TestEvidenceCaseHandling:
    """Evidence case drift paths."""

    def test_contract_expiry_marks_evidence_expired(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(evidence_case_ref="ec-1")
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="contract_expiry",
            execute_fn=MagicMock(return_value=None),
        )

        deps["evidence_cases"].mark_expired.assert_called_once_with(
            "ec-1", summary=_EVIDENCE_SUMMARIES["contract_expiry"]
        )
        deps["evidence_cases"].invalidate.assert_not_called()
        deps["evidence_cases"].mark_stale.assert_not_called()

    def test_policy_version_drift_marks_evidence_stale(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(evidence_case_ref="ec-1")
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="policy_version_drift",
            execute_fn=MagicMock(return_value=None),
        )

        deps["evidence_cases"].mark_stale.assert_called_once_with(
            "ec-1", summary=_EVIDENCE_SUMMARIES["policy_version_drift"]
        )
        deps["evidence_cases"].invalidate.assert_not_called()
        deps["evidence_cases"].mark_expired.assert_not_called()

    @pytest.mark.parametrize(
        "drift_reason",
        ["witness_drift", "approval_drift", "evidence_drift"],
    )
    def test_other_drifts_invalidate_evidence(self, drift_reason: str) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(evidence_case_ref="ec-1")
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason=drift_reason,
            execute_fn=MagicMock(return_value=None),
        )

        deps["evidence_cases"].invalidate.assert_called_once_with(
            "ec-1",
            contradictions=[drift_reason],
            summary=_EVIDENCE_SUMMARIES[drift_reason],
        )

    def test_no_evidence_ref_skips_evidence_handling(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(evidence_case_ref=None)
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(return_value=None),
        )

        deps["evidence_cases"].invalidate.assert_not_called()
        deps["evidence_cases"].mark_expired.assert_not_called()
        deps["evidence_cases"].mark_stale.assert_not_called()


class TestAuthorizationPlanInvalidation:
    """Authorization plan invalidation and status overrides."""

    @pytest.mark.parametrize(
        "drift_reason,expected_status",
        [
            ("contract_expiry", "expired"),
            ("policy_version_drift", "superseded"),
            ("witness_drift", "invalidated"),
            ("approval_drift", "invalidated"),
            ("evidence_drift", "invalidated"),
        ],
    )
    def test_authorization_plan_invalidated_with_correct_status(
        self, drift_reason: str, expected_status: str
    ) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(authorization_plan_ref="ap-1")
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason=drift_reason,
            execute_fn=MagicMock(return_value=None),
        )

        deps["authorization_plans"].invalidate.assert_called_once_with(
            "ap-1",
            gaps=[drift_reason],
            summary=_AUTHORIZATION_SUMMARIES[drift_reason],
            status=expected_status,
        )

    def test_no_auth_plan_ref_skips_invalidation(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(authorization_plan_ref=None)
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(return_value=None),
        )

        deps["authorization_plans"].invalidate.assert_not_called()


class TestApprovalDriftEvent:
    """Approval drift event emission."""

    def test_approval_drift_event_emitted_when_approval_id_present(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(approval_id="approval-42")
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="approval_drift",
            execute_fn=MagicMock(return_value=None),
        )

        drift_calls = [
            c
            for c in deps["store"].append_event.call_args_list
            if c.kwargs.get("event_type") == "approval.drifted"
        ]
        assert len(drift_calls) == 1
        payload = drift_calls[0].kwargs["payload"]
        assert payload["approval_id"] == "approval-42"
        assert payload["drift_kind"] == "approval_drift"

    def test_no_approval_id_skips_drift_event(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(approval_id=None)
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(return_value=None),
        )

        drift_calls = [
            c
            for c in deps["store"].append_event.call_args_list
            if c.kwargs.get("event_type") == "approval.drifted"
        ]
        assert len(drift_calls) == 0


class TestUnknownDriftReason:
    """Fallback behaviour for unrecognized drift reasons."""

    def test_unknown_reason_uses_fallback_keys(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt()
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = _make_attempt(
            step_attempt_id="sa-2", attempt=2
        )

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="totally_unknown",
            execute_fn=MagicMock(return_value=None),
        )

        # Should use default reason key
        supersede_call = deps["execution_contracts"].supersede.call_args
        assert supersede_call.kwargs["reason"] == "contract_drift_reenter_policy"

        # Should use default reentry boundary
        create_call = deps["store"].create_step_attempt.call_args
        assert create_call.kwargs["reentry_boundary"] == "policy_recompile"

        # Evidence invalidated with default summary
        deps["evidence_cases"].invalidate.assert_called_once()
        inv_call = deps["evidence_cases"].invalidate.call_args
        assert "contract loop drifted" in inv_call.kwargs["summary"]

        # Authorization plan uses default "invalidated" status
        auth_call = deps["authorization_plans"].invalidate.call_args
        assert auth_call.kwargs["status"] == "invalidated"


class TestDriftReentryLimit:
    """Verify the reentry loop breaker works."""

    def test_exceeding_limit_fails_attempt(self) -> None:
        handler, deps = _make_handler()
        # Simulate an attempt that has already been reentered _MAX_DRIFT_REENTRIES times
        current = _make_attempt(
            context={
                "reentered_via": "contract_expiry",
                "drift_reentry_count": _MAX_DRIFT_REENTRIES,
            },
        )
        deps["store"].get_step_attempt.return_value = current
        execute_fn = MagicMock()

        result = handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="contract_expiry",
            execute_fn=execute_fn,
        )

        # Should NOT re-enter — execute_fn never called
        execute_fn.assert_not_called()
        # Should fail the attempt
        deps["store"].update_step_attempt.assert_called_once()
        fail_call = deps["store"].update_step_attempt.call_args
        assert fail_call.kwargs["status"] == "failed"
        assert "drift_reentry_limit_exceeded" in fail_call.kwargs["status_reason"]
        # Should fail the step and task
        deps["store"].update_step.assert_called_once()
        assert deps["store"].update_step.call_args.kwargs["status"] == "failed"
        deps["store"].update_task_status.assert_called_once_with(
            "task-1",
            "failed",
            payload=deps["store"].update_task_status.call_args.kwargs["payload"],
        )
        # Result should indicate failure
        assert result.result_code == "failed"
        assert result.execution_status == "failed"

    def test_within_limit_proceeds_normally(self) -> None:
        handler, deps = _make_handler()
        # count=2, limit=3 → still under limit, should proceed
        current = _make_attempt(
            context={
                "reentered_via": "contract_expiry",
                "drift_reentry_count": 2,
            },
        )
        successor = _make_attempt(step_attempt_id="sa-2", attempt=2)
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = successor

        sentinel = object()
        execute_fn = MagicMock(return_value=sentinel)

        result = handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="contract_expiry",
            execute_fn=execute_fn,
        )

        assert result is sentinel
        execute_fn.assert_called_once()

    def test_different_drift_reason_resets_count(self) -> None:
        handler, deps = _make_handler()
        # Previous reason was contract_expiry with high count,
        # but new reason is witness_drift → count resets to 1
        current = _make_attempt(
            context={
                "reentered_via": "contract_expiry",
                "drift_reentry_count": _MAX_DRIFT_REENTRIES,
            },
        )
        successor = _make_attempt(step_attempt_id="sa-2", attempt=2)
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = successor

        sentinel = object()
        execute_fn = MagicMock(return_value=sentinel)

        result = handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",  # different reason
            execute_fn=execute_fn,
        )

        assert result is sentinel
        execute_fn.assert_called_once()
        # Successor context should have count=1
        ctx_arg = deps["store"].create_step_attempt.call_args.kwargs["context"]
        assert ctx_arg["drift_reentry_count"] == 1

    def test_successor_carries_drift_count(self) -> None:
        handler, deps = _make_handler()
        current = _make_attempt(
            context={
                "reentered_via": "contract_expiry",
                "drift_reentry_count": 1,
            },
        )
        successor = _make_attempt(step_attempt_id="sa-2", attempt=2)
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = successor

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="contract_expiry",
            execute_fn=MagicMock(return_value=None),
        )

        ctx_arg = deps["store"].create_step_attempt.call_args.kwargs["context"]
        assert ctx_arg["drift_reentry_count"] == 2


class TestConcurrentSupersessionRace:
    """CAS guard prevents duplicate successor creation on concurrent drift signals."""

    def test_race_lost_returns_skipped_result(self) -> None:
        """When try_supersede_step_attempt returns False, return skipped without successor."""
        handler, deps = _make_handler()
        current = _make_attempt()
        deps["store"].get_step_attempt.return_value = current
        # Simulate losing the CAS race (another thread already superseded)
        deps["store"].try_supersede_step_attempt.return_value = False

        result = handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(),
        )

        assert result.result_code == "skipped"
        assert result.execution_status == "superseded"
        assert "sa-1" in result.model_content

    def test_race_lost_does_not_create_successor(self) -> None:
        """When CAS fails, create_step_attempt must NOT be called."""
        handler, deps = _make_handler()
        deps["store"].get_step_attempt.return_value = _make_attempt()
        deps["store"].try_supersede_step_attempt.return_value = False

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="approval_drift",
            execute_fn=MagicMock(),
        )

        deps["store"].create_step_attempt.assert_not_called()

    def test_race_lost_does_not_invoke_execute_fn(self) -> None:
        """When CAS fails, execute_fn must NOT be called."""
        handler, deps = _make_handler()
        deps["store"].get_step_attempt.return_value = _make_attempt()
        deps["store"].try_supersede_step_attempt.return_value = False
        execute_fn = MagicMock()

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="evidence_drift",
            execute_fn=execute_fn,
        )

        execute_fn.assert_not_called()

    def test_race_won_proceeds_to_create_successor(self) -> None:
        """When CAS succeeds (True), the normal supersession path runs."""
        handler, deps = _make_handler()
        current = _make_attempt()
        successor = _make_attempt(step_attempt_id="sa-2", attempt=2)
        deps["store"].get_step_attempt.return_value = current
        deps["store"].create_step_attempt.return_value = successor
        deps["store"].try_supersede_step_attempt.return_value = True

        sentinel = object()
        execute_fn = MagicMock(return_value=sentinel)

        result = handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(),
            tool_name="bash",
            tool_input={},
            drift_reason="contract_expiry",
            execute_fn=execute_fn,
        )

        assert result is sentinel
        deps["store"].create_step_attempt.assert_called_once()
        execute_fn.assert_called_once()

    def test_cas_called_with_correct_attempt_id(self) -> None:
        """try_supersede_step_attempt receives the current attempt's ID."""
        handler, deps = _make_handler()
        current = _make_attempt(step_attempt_id="sa-unique-42")
        deps["store"].get_step_attempt.return_value = current
        deps["store"].try_supersede_step_attempt.return_value = False

        handler.supersede_attempt_for_drift(
            attempt_ctx=_make_ctx(step_attempt_id="sa-unique-42"),
            tool_name="bash",
            tool_input={},
            drift_reason="witness_drift",
            execute_fn=MagicMock(),
        )

        cas_call = deps["store"].try_supersede_step_attempt.call_args
        assert cas_call.args[0] == "sa-unique-42"
        assert "finished_at" in cas_call.kwargs


class TestLookupTableConsistency:
    """Verify the module-level lookup tables are consistent."""

    def test_all_reason_keys_have_reentry_boundaries(self) -> None:
        assert set(_REASON_KEYS.keys()) == set(_REENTRY_BOUNDARIES.keys())

    def test_all_reason_keys_have_evidence_summaries(self) -> None:
        assert set(_REASON_KEYS.keys()) == set(_EVIDENCE_SUMMARIES.keys())

    def test_all_reason_keys_have_authorization_summaries(self) -> None:
        assert set(_REASON_KEYS.keys()) == set(_AUTHORIZATION_SUMMARIES.keys())

    def test_auth_status_overrides_are_subset_of_reasons(self) -> None:
        assert set(_AUTH_STATUS_OVERRIDES.keys()).issubset(set(_REASON_KEYS.keys()))
