"""Unit tests for SupervisionService."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermit.kernel.execution.controller.supervision import SupervisionService
from hermit.kernel.task.models.records import IngressRecord, TaskRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(**overrides: Any) -> TaskRecord:
    defaults = {
        "task_id": "task-1",
        "conversation_id": "conv-1",
        "title": "Test Task",
        "goal": "test goal",
        "status": "running",
        "priority": "normal",
        "owner_principal_id": "user-1",
        "policy_profile": "default",
        "source_channel": "chat",
    }
    defaults.update(overrides)
    return TaskRecord(**defaults)


def _make_ingress(**overrides: Any) -> IngressRecord:
    defaults = {
        "ingress_id": "ing-1",
        "conversation_id": "conv-1",
        "source_channel": "chat",
        "actor_principal_id": "user-1",
        "raw_text": "hello",
        "status": "received",
        "resolution": "none",
        "chosen_task_id": None,
        "parent_task_id": None,
        "confidence": 0.9,
        "margin": 0.1,
        "rationale": {"reason_codes": ["test"], "resolved_by": "router"},
    }
    defaults.update(overrides)
    return IngressRecord(**defaults)


def _make_cached_projection(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "task": {"task_id": "task-1", "status": "running"},
        "projection": {
            "events_processed": 10,
            "last_event_seq": 10,
            "steps": {"s1": {}},
            "step_attempts": {"sa1": {}},
            "approvals": {},
            "decisions": {"d1": {}},
            "capability_grants": {"cg1": {}},
            "workspace_leases": {"wl1": {}},
            "receipts": {"r1": {}},
            "beliefs": {"b1": {}},
            "memory_records": {"m1": {}},
        },
        "proof": {
            "chain_verification": {"valid": True},
            "latest_receipt": None,
            "latest_decision": None,
            "latest_capability_grant": None,
            "latest_workspace_lease": None,
        },
        "claims": None,
        "knowledge": [],
        "beliefs": [],
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture
def mock_store() -> MagicMock:
    store = MagicMock()
    store.get_task.return_value = _make_task()
    store.list_ingresses.return_value = []
    store.list_step_attempts.return_value = []
    store.get_rollback_for_receipt.return_value = None
    return store


@pytest.fixture
def service(mock_store: MagicMock) -> SupervisionService:
    return SupervisionService(mock_store)


# ---------------------------------------------------------------------------
# TestTrim
# ---------------------------------------------------------------------------


class TestTrim:
    def test_short_string(self) -> None:
        assert SupervisionService._trim("hello", 10) == "hello"

    def test_exact_limit(self) -> None:
        assert SupervisionService._trim("hello", 5) == "hello"

    def test_long_string_truncated(self) -> None:
        result = SupervisionService._trim("hello world this is long", 10)
        assert len(result) <= 10
        assert result.endswith("…")

    def test_empty_string(self) -> None:
        assert SupervisionService._trim("", 10) == ""

    def test_whitespace_normalized(self) -> None:
        assert SupervisionService._trim("hello   world  test", 100) == "hello world test"

    def test_none_treated_as_empty(self) -> None:
        assert SupervisionService._trim("", 5) == ""

    def test_limit_zero(self) -> None:
        result = SupervisionService._trim("hello", 0)
        assert result == "…"


# ---------------------------------------------------------------------------
# TestRollbackReceipt
# ---------------------------------------------------------------------------


class TestRollbackReceipt:
    def test_delegates_to_rollbacks(
        self, service: SupervisionService, mock_store: MagicMock
    ) -> None:
        service.rollbacks = MagicMock()
        service.rollbacks.execute.return_value = {"status": "rolled_back"}
        result = service.rollback_receipt("rcpt-1")
        service.rollbacks.execute.assert_called_once_with("rcpt-1")
        assert result == {"status": "rolled_back"}


# ---------------------------------------------------------------------------
# TestReentryObservability
# ---------------------------------------------------------------------------


class TestReentryObservability:
    def test_empty_attempts(self, service: SupervisionService, mock_store: MagicMock) -> None:
        mock_store.list_step_attempts.return_value = []
        result = service._reentry_observability("task-1")
        assert result["required_count"] == 0
        assert result["resolved_count"] == 0
        assert result["recent_attempts"] == []

    def test_counts_reentry_required(
        self, service: SupervisionService, mock_store: MagicMock
    ) -> None:
        attempts = [
            SimpleNamespace(
                step_attempt_id="sa-1",
                status="running",
                context={"reentry_required": True, "reentry_reason": "test"},
            ),
            SimpleNamespace(
                step_attempt_id="sa-2",
                status="running",
                context={"reentry_required": True, "reentry_reason": "test2"},
            ),
            SimpleNamespace(
                step_attempt_id="sa-3",
                status="running",
                context={},
            ),
        ]
        mock_store.list_step_attempts.return_value = attempts
        result = service._reentry_observability("task-1")
        assert result["required_count"] == 2

    def test_counts_reentry_resolved(
        self, service: SupervisionService, mock_store: MagicMock
    ) -> None:
        attempts = [
            SimpleNamespace(
                step_attempt_id="sa-1",
                status="succeeded",
                context={"reentry_resolved_at": 1234567890.0, "reentry_reason": "done"},
            ),
        ]
        mock_store.list_step_attempts.return_value = attempts
        result = service._reentry_observability("task-1")
        assert result["resolved_count"] == 1

    def test_recent_filtered_by_reentry_fields(
        self, service: SupervisionService, mock_store: MagicMock
    ) -> None:
        attempts = [
            SimpleNamespace(
                step_attempt_id="sa-1",
                status="running",
                context={"reentry_reason": "need_input"},
            ),
            SimpleNamespace(
                step_attempt_id="sa-2",
                status="running",
                context={"reentry_boundary": "policy"},
            ),
            SimpleNamespace(
                step_attempt_id="sa-3",
                status="running",
                context={"reentered_via": "manual"},
            ),
            SimpleNamespace(
                step_attempt_id="sa-4",
                status="running",
                context={"recovery_required": True},
            ),
            SimpleNamespace(
                step_attempt_id="sa-5",
                status="running",
                context={"phase": "executing"},
            ),
        ]
        mock_store.list_step_attempts.return_value = attempts
        result = service._reentry_observability("task-1")
        assert len(result["recent_attempts"]) == 4
        ids = [a["step_attempt_id"] for a in result["recent_attempts"]]
        assert "sa-5" not in ids

    def test_recent_limited_to_5(self, service: SupervisionService, mock_store: MagicMock) -> None:
        attempts = [
            SimpleNamespace(
                step_attempt_id=f"sa-{i}",
                status="running",
                context={"reentry_reason": f"reason-{i}"},
            )
            for i in range(10)
        ]
        mock_store.list_step_attempts.return_value = attempts
        result = service._reentry_observability("task-1")
        assert len(result["recent_attempts"]) == 5

    def test_recent_attempt_fields(
        self, service: SupervisionService, mock_store: MagicMock
    ) -> None:
        attempts = [
            SimpleNamespace(
                step_attempt_id="sa-1",
                status="running",
                context={
                    "phase": "executing",
                    "reentry_reason": "need_input",
                    "reentry_boundary": "policy",
                    "reentered_via": "manual",
                    "reentry_required": True,
                    "recovery_required": False,
                    "reentry_requested_at": 100.0,
                    "reentry_resolved_at": 200.0,
                },
            ),
        ]
        mock_store.list_step_attempts.return_value = attempts
        result = service._reentry_observability("task-1")
        entry = result["recent_attempts"][0]
        assert entry["step_attempt_id"] == "sa-1"
        assert entry["status"] == "running"
        assert entry["phase"] == "executing"
        assert entry["reentry_reason"] == "need_input"
        assert entry["reentry_boundary"] == "policy"
        assert entry["reentered_via"] == "manual"
        assert entry["reentry_required"] is True
        assert entry["recovery_required"] is False
        assert entry["reentry_requested_at"] == 100.0
        assert entry["reentry_resolved_at"] == 200.0


# ---------------------------------------------------------------------------
# TestSerializeIngress
# ---------------------------------------------------------------------------


class TestSerializeIngress:
    def test_serialize_with_relation(self, service: SupervisionService) -> None:
        ingress = _make_ingress()
        result = service._serialize_ingress(ingress, relation="chosen_task")
        assert result["relation"] == "chosen_task"
        assert result["ingress_id"] == "ing-1"

    def test_serialize_without_relation(self, service: SupervisionService) -> None:
        ingress = _make_ingress()
        result = service._serialize_ingress(ingress)
        assert "relation" not in result

    def test_serialize_all_fields(self, service: SupervisionService) -> None:
        ingress = _make_ingress(
            reply_to_ref="reply-1",
            quoted_message_ref="quote-1",
            explicit_task_ref="task-ref-1",
            referenced_artifact_refs=["art-1"],
        )
        result = service._serialize_ingress(ingress)
        assert result["ingress_id"] == "ing-1"
        assert result["status"] == "received"
        assert result["resolution"] == "none"
        assert result["chosen_task_id"] is None
        assert result["parent_task_id"] is None
        assert result["actor_principal_id"] == "user-1"
        assert result["source_channel"] == "chat"
        assert "hello" in result["raw_excerpt"]
        assert result["reply_to_ref"] == "reply-1"
        assert result["quoted_message_ref"] == "quote-1"
        assert result["explicit_task_ref"] == "task-ref-1"
        assert result["referenced_artifact_refs"] == ["art-1"]
        assert result["confidence"] == 0.9
        assert result["margin"] == 0.1
        assert result["reason_codes"] == ["test"]
        assert result["resolved_by"] == "router"

    def test_serialize_trims_raw_text(self, service: SupervisionService) -> None:
        ingress = _make_ingress(raw_text="x" * 500)
        result = service._serialize_ingress(ingress)
        assert len(result["raw_excerpt"]) <= 240


class TestSerializeIngressList:
    def test_serializes_list(self, service: SupervisionService) -> None:
        ingresses = [_make_ingress(ingress_id="ing-1"), _make_ingress(ingress_id="ing-2")]
        result = service._serialize_ingress_list(ingresses)
        assert len(result) == 2
        assert result[0]["ingress_id"] == "ing-1"
        assert result[1]["ingress_id"] == "ing-2"


# ---------------------------------------------------------------------------
# TestRecentRelatedIngresses
# ---------------------------------------------------------------------------


class TestRecentRelatedIngresses:
    def test_chosen_task_relation(self, service: SupervisionService, mock_store: MagicMock) -> None:
        ingresses = [_make_ingress(ingress_id="ing-1", chosen_task_id="task-1")]
        mock_store.list_ingresses.return_value = ingresses
        result = service._recent_related_ingresses(conversation_id="conv-1", task_id="task-1")
        assert len(result) == 1
        assert result[0]["relation"] == "chosen_task"

    def test_parent_task_relation(self, service: SupervisionService, mock_store: MagicMock) -> None:
        ingresses = [_make_ingress(ingress_id="ing-1", parent_task_id="task-1")]
        mock_store.list_ingresses.return_value = ingresses
        result = service._recent_related_ingresses(conversation_id="conv-1", task_id="task-1")
        assert len(result) == 1
        assert result[0]["relation"] == "parent_task"

    def test_unrelated_skipped(self, service: SupervisionService, mock_store: MagicMock) -> None:
        ingresses = [
            _make_ingress(ingress_id="ing-1", chosen_task_id="other", parent_task_id="other")
        ]
        mock_store.list_ingresses.return_value = ingresses
        result = service._recent_related_ingresses(conversation_id="conv-1", task_id="task-1")
        assert len(result) == 0

    def test_limit_respected(self, service: SupervisionService, mock_store: MagicMock) -> None:
        ingresses = [
            _make_ingress(ingress_id=f"ing-{i}", chosen_task_id="task-1") for i in range(10)
        ]
        mock_store.list_ingresses.return_value = ingresses
        result = service._recent_related_ingresses(
            conversation_id="conv-1", task_id="task-1", limit=3
        )
        assert len(result) == 3

    def test_mixed_relations(self, service: SupervisionService, mock_store: MagicMock) -> None:
        ingresses = [
            _make_ingress(ingress_id="ing-1", chosen_task_id="task-1"),
            _make_ingress(ingress_id="ing-2", parent_task_id="task-1"),
            _make_ingress(ingress_id="ing-3", chosen_task_id="other"),
        ]
        mock_store.list_ingresses.return_value = ingresses
        result = service._recent_related_ingresses(conversation_id="conv-1", task_id="task-1")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestBuildIngressObservability
# ---------------------------------------------------------------------------


class TestBuildIngressObservability:
    def test_task_none_returns_empty(self, service: SupervisionService) -> None:
        result = service._build_ingress_observability(None)
        assert result["conversation"] == {}
        assert result["task"]["recent_related_ingresses"] == []
        assert result["task"]["pending_disambiguations"] == []

    def test_with_task_builds_conversation(
        self, service: SupervisionService, mock_store: MagicMock
    ) -> None:
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "task-1",
            "focus_reason": "user selected",
            "open_tasks": [
                {"task_id": "task-1", "title": "Test", "status": "running"},
            ],
            "pending_ingress_count": 2,
            "ingress_metrics": {"total": 5},
        }
        task = _make_task()
        result = service._build_ingress_observability(task)
        conv = result["conversation"]
        assert conv["conversation_id"] == "conv-1"
        assert conv["focus"]["task_id"] == "task-1"
        assert conv["focus"]["title"] == "Test"
        assert conv["focus"]["status"] == "running"
        assert conv["focus"]["reason"] == "user selected"
        assert conv["pending_ingress_count"] == 2
        assert conv["metrics"] == {"total": 5}

    def test_focus_task_not_matching(
        self, service: SupervisionService, mock_store: MagicMock
    ) -> None:
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "task-other",
            "focus_reason": "",
            "open_tasks": [
                {"task_id": "task-1", "title": "Test", "status": "running"},
            ],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        task = _make_task()
        result = service._build_ingress_observability(task)
        assert result["conversation"]["focus"]["title"] == ""
        assert result["conversation"]["focus"]["status"] == ""

    def test_task_section_includes_is_focus(
        self, service: SupervisionService, mock_store: MagicMock
    ) -> None:
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "task-1",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        task = _make_task()
        result = service._build_ingress_observability(task)
        assert result["task"]["is_focus"] is True
        assert result["task"]["task_id"] == "task-1"

    def test_not_focus(self, service: SupervisionService, mock_store: MagicMock) -> None:
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "task-other",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        task = _make_task()
        result = service._build_ingress_observability(task)
        assert result["task"]["is_focus"] is False

    def test_null_safe_fields(self, service: SupervisionService, mock_store: MagicMock) -> None:
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": None,
            "focus_reason": None,
            "open_tasks": None,
            "pending_ingress_count": None,
            "ingress_metrics": None,
        }
        task = _make_task()
        result = service._build_ingress_observability(task)
        assert result["conversation"]["focus"]["task_id"] == ""
        assert result["conversation"]["pending_ingress_count"] == 0
        assert result["conversation"]["metrics"] == {}
        assert result["conversation"]["open_tasks"] == []


# ---------------------------------------------------------------------------
# TestBuildTaskCase
# ---------------------------------------------------------------------------


class TestBuildTaskCase:
    def _setup_cached(
        self,
        service: SupervisionService,
        mock_store: MagicMock,
        **cached_overrides: Any,
    ) -> dict[str, Any]:
        cached = _make_cached_projection(**cached_overrides)
        service.projections = MagicMock()
        service.projections.ensure_task_projection.return_value = cached
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        return cached

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_basic_structure(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {"status": "verified"}
        self._setup_cached(service, mock_store)
        result = service.build_task_case("task-1")
        assert "task" in result
        assert "projection" in result
        assert "operator_answers" in result
        assert "ingress_observability" in result

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_projection_counts(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        self._setup_cached(service, mock_store)
        result = service.build_task_case("task-1")
        proj = result["projection"]
        assert proj["events_processed"] == 10
        assert proj["last_event_seq"] == 10
        assert proj["step_count"] == 1
        assert proj["step_attempt_count"] == 1
        assert proj["approval_count"] == 0
        assert proj["decision_count"] == 1
        assert proj["capability_grant_count"] == 1
        assert proj["workspace_lease_count"] == 1
        assert proj["receipt_count"] == 1
        assert proj["belief_count"] == 1
        assert proj["memory_count"] == 1

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_uses_cached_claims_when_present(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        self._setup_cached(service, mock_store, claims={"status": "from_cache"})
        result = service.build_task_case("task-1")
        assert result["operator_answers"]["claims"] == {"status": "from_cache"}
        mock_claims.assert_not_called()

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_calls_task_claim_status_when_no_cached_claims(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {"status": "computed"}
        self._setup_cached(service, mock_store, claims=None)
        result = service.build_task_case("task-1")
        mock_claims.assert_called_once()
        assert result["operator_answers"]["claims"] == {"status": "computed"}

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_approvals_sorted_descending(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        cached = _make_cached_projection()
        cached["projection"]["approvals"] = {
            "a1": {"approval_id": "a1", "last_event_at": 100.0},
            "a2": {"approval_id": "a2", "last_event_at": 300.0},
            "a3": {"approval_id": "a3", "last_event_at": 200.0},
        }
        service.projections = MagicMock()
        service.projections.ensure_task_projection.return_value = cached
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        result = service.build_task_case("task-1")
        approval = result["operator_answers"]["approval"]
        assert approval["approval_id"] == "a2"

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_no_approvals(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        self._setup_cached(service, mock_store)
        result = service.build_task_case("task-1")
        assert result["operator_answers"]["approval"] is None

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_with_rollback(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        cached = _make_cached_projection()
        cached["proof"]["latest_receipt"] = {
            "receipt_id": "rcpt-1",
            "rollback_supported": True,
            "rollback_strategy": "file_restore",
        }
        service.projections = MagicMock()
        service.projections.ensure_task_projection.return_value = cached
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        rollback_record = SimpleNamespace(rollback_id="rb-1", status="completed")
        mock_store.get_rollback_for_receipt.return_value = rollback_record
        result = service.build_task_case("task-1")
        assert result["operator_answers"]["rollback"] is not None
        assert result["operator_answers"]["rollback"]["rollback_id"] == "rb-1"

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_no_rollback_when_no_receipt_id(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        cached = _make_cached_projection()
        cached["proof"]["latest_receipt"] = {"rollback_supported": False}
        service.projections = MagicMock()
        service.projections.ensure_task_projection.return_value = cached
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        result = service.build_task_case("task-1")
        assert result["operator_answers"]["rollback"] is None

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_rollback_none_when_record_none(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        cached = _make_cached_projection()
        cached["proof"]["latest_receipt"] = {"receipt_id": "rcpt-1"}
        service.projections = MagicMock()
        service.projections.ensure_task_projection.return_value = cached
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        mock_store.get_rollback_for_receipt.return_value = None
        result = service.build_task_case("task-1")
        assert result["operator_answers"]["rollback"] is None

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_knowledge(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        cached = _make_cached_projection()
        cached["knowledge"] = [{"memory_id": "mem-1"}, {"memory_id": "mem-2"}]
        cached["beliefs"] = [{"belief_id": "b1"}, {"belief_id": "b2"}, {"belief_id": "b3"}]
        service.projections = MagicMock()
        service.projections.ensure_task_projection.return_value = cached
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        result = service.build_task_case("task-1")
        knowledge = result["operator_answers"]["knowledge"]
        assert knowledge["latest_memory"] == {"memory_id": "mem-1"}
        assert len(knowledge["recent_beliefs"]) == 3

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_empty_knowledge(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        self._setup_cached(service, mock_store)
        result = service.build_task_case("task-1")
        assert result["operator_answers"]["knowledge"]["latest_memory"] is None

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_target_paths_from_capability_grant(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        cached = _make_cached_projection()
        cached["proof"]["latest_capability_grant"] = {
            "constraints": {"target_paths": ["/a/b", "/c/d"]},
        }
        service.projections = MagicMock()
        service.projections.ensure_task_projection.return_value = cached
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        result = service.build_task_case("task-1")
        authority = result["operator_answers"]["authority"]
        assert authority["target_paths"] == ["/a/b", "/c/d"]

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_no_capability_grant(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        self._setup_cached(service, mock_store)
        result = service.build_task_case("task-1")
        authority = result["operator_answers"]["authority"]
        assert authority["target_paths"] == []
        assert authority["capability_grant"] is None

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_decision_evidence_refs(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        cached = _make_cached_projection()
        cached["proof"]["latest_decision"] = {
            "reason": "approved by policy",
            "evidence_refs": ["ev-1", "ev-2"],
        }
        service.projections = MagicMock()
        service.projections.ensure_task_projection.return_value = cached
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        result = service.build_task_case("task-1")
        answers = result["operator_answers"]
        assert answers["why_execute"] == "approved by policy"
        assert answers["evidence_refs"] == ["ev-1", "ev-2"]

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_no_decision(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        self._setup_cached(service, mock_store)
        result = service.build_task_case("task-1")
        assert result["operator_answers"]["why_execute"] is None
        assert result["operator_answers"]["evidence_refs"] == []

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_beliefs_limited_to_5(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        cached = _make_cached_projection()
        cached["beliefs"] = [{"id": i} for i in range(10)]
        service.projections = MagicMock()
        service.projections.ensure_task_projection.return_value = cached
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        result = service.build_task_case("task-1")
        assert len(result["operator_answers"]["knowledge"]["recent_beliefs"]) == 5

    @patch("hermit.kernel.execution.controller.supervision.task_claim_status")
    def test_rollback_available_flag(
        self,
        mock_claims: MagicMock,
        service: SupervisionService,
        mock_store: MagicMock,
    ) -> None:
        mock_claims.return_value = {}
        cached = _make_cached_projection()
        cached["proof"]["latest_receipt"] = {
            "rollback_supported": True,
            "rollback_strategy": "file_restore",
        }
        service.projections = MagicMock()
        service.projections.ensure_task_projection.return_value = cached
        service.conversation_projections = MagicMock()
        service.conversation_projections.ensure.return_value = {
            "focus_task_id": "",
            "focus_reason": "",
            "open_tasks": [],
            "pending_ingress_count": 0,
            "ingress_metrics": {},
        }
        result = service.build_task_case("task-1")
        authority = result["operator_answers"]["authority"]
        assert authority["rollback_available"] is True
        assert authority["rollback_strategy"] == "file_restore"
