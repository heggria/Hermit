"""Tests for Communication Budget & Monotonicity Guard (Spec 09)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.policy.guards.rules_budget import (
    _DEFAULT_COMMUNICATION_BUDGET_RATIO,
    _MONOTONICITY_REQUIRE_COORDINATION,
    _MONOTONICITY_SKIP_COORDINATION,
    _VALID_MONOTONICITY_CLASSES,
    evaluate_communication_budget_guard,
    evaluate_monotonicity_guard,
)
from hermit.kernel.policy.models.models import ActionRequest
from hermit.kernel.task.models.records import TaskRecord
from hermit.kernel.task.services.dag_builder import StepDAGBuilder, StepNode

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture
def builder(store: KernelStore) -> StepDAGBuilder:
    return StepDAGBuilder(store)


def _make_request(
    *,
    monotonicity_class: str = "",
    communication_budget_ratio: float | None = None,
    **extra_context: object,
) -> ActionRequest:
    ctx: dict[str, object] = {}
    if monotonicity_class:
        ctx["monotonicity_class"] = monotonicity_class
    if communication_budget_ratio is not None:
        ctx["communication_budget_ratio"] = communication_budget_ratio
    ctx.update(extra_context)
    return ActionRequest(
        request_id="req_test",
        tool_name="test_tool",
        action_class="write_local",
        context=ctx,
    )


def _make_task(store: KernelStore, **kwargs: object) -> str:
    task = store.create_task(
        conversation_id="conv_1",
        title="test",
        goal="test",
        source_channel="test",
    )
    return task.task_id


# ---------------------------------------------------------------------------
# TaskRecord budget fields
# ---------------------------------------------------------------------------


class TestTaskRecordBudgetFields:
    def test_default_budget_fields(self) -> None:
        record = TaskRecord(
            task_id="t1",
            conversation_id="c1",
            title="test",
            goal="test",
            status="running",
            priority="normal",
            owner_principal_id="hermit",
            policy_profile="default",
            source_channel="test",
        )
        assert record.budget_tokens_used == 0
        assert record.budget_tokens_limit is None

    def test_budget_fields_set(self) -> None:
        record = TaskRecord(
            task_id="t1",
            conversation_id="c1",
            title="test",
            goal="test",
            status="running",
            priority="normal",
            owner_principal_id="hermit",
            policy_profile="default",
            source_channel="test",
            budget_tokens_used=500,
            budget_tokens_limit=1000,
        )
        assert record.budget_tokens_used == 500
        assert record.budget_tokens_limit == 1000


# ---------------------------------------------------------------------------
# StepNode monotonicity_class field
# ---------------------------------------------------------------------------


class TestStepNodeMonotonicity:
    def test_default_monotonicity_class(self) -> None:
        node = StepNode(key="a", kind="execute", title="A")
        assert node.monotonicity_class == "compensatable_mutation"

    def test_readonly_monotonicity(self) -> None:
        node = StepNode(key="a", kind="execute", title="A", monotonicity_class="readonly")
        assert node.monotonicity_class == "readonly"

    def test_additive_monotonicity(self) -> None:
        node = StepNode(key="a", kind="execute", title="A", monotonicity_class="additive")
        assert node.monotonicity_class == "additive"

    def test_irreversible_mutation(self) -> None:
        node = StepNode(
            key="a",
            kind="execute",
            title="A",
            monotonicity_class="irreversible_mutation",
        )
        assert node.monotonicity_class == "irreversible_mutation"

    def test_dag_with_mixed_monotonicity(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="read", kind="read", title="Read", monotonicity_class="readonly"),
            StepNode(
                key="write",
                kind="execute",
                title="Write",
                depends_on=["read"],
                monotonicity_class="compensatable_mutation",
            ),
        ]
        dag = builder.validate(nodes)
        assert dag.nodes["read"].monotonicity_class == "readonly"
        assert dag.nodes["write"].monotonicity_class == "compensatable_mutation"


# ---------------------------------------------------------------------------
# Monotonicity guard
# ---------------------------------------------------------------------------


class TestMonotonicityGuard:
    def test_no_class_returns_none(self) -> None:
        request = _make_request()
        result = evaluate_monotonicity_guard(request)
        assert result is None

    def test_empty_class_returns_none(self) -> None:
        request = _make_request(monotonicity_class="")
        result = evaluate_monotonicity_guard(request)
        assert result is None

    def test_readonly_skips_coordination(self) -> None:
        request = _make_request(monotonicity_class="readonly")
        result = evaluate_monotonicity_guard(request)
        assert result is not None
        assert result.verdict == "allow"
        assert result.obligations.require_approval is False
        assert result.obligations.require_receipt is False
        assert result.risk_level == "low"
        assert result.reasons[0].code == "monotonicity_skip"

    def test_additive_skips_coordination_but_requires_receipt(self) -> None:
        request = _make_request(monotonicity_class="additive")
        result = evaluate_monotonicity_guard(request)
        assert result is not None
        assert result.verdict == "allow"
        assert result.obligations.require_approval is False
        assert result.obligations.require_receipt is True

    def test_compensatable_mutation_returns_none(self) -> None:
        request = _make_request(monotonicity_class="compensatable_mutation")
        result = evaluate_monotonicity_guard(request)
        assert result is None

    def test_irreversible_mutation_returns_none(self) -> None:
        request = _make_request(monotonicity_class="irreversible_mutation")
        result = evaluate_monotonicity_guard(request)
        assert result is None

    def test_unknown_class_returns_none(self) -> None:
        request = _make_request(monotonicity_class="unknown_value")
        result = evaluate_monotonicity_guard(request)
        assert result is None


# ---------------------------------------------------------------------------
# Communication budget guard
# ---------------------------------------------------------------------------


class TestCommunicationBudgetGuard:
    def test_no_limit_returns_none(self) -> None:
        request = _make_request()
        result = evaluate_communication_budget_guard(
            request, budget_tokens_used=500, budget_tokens_limit=None
        )
        assert result is None

    def test_zero_limit_returns_none(self) -> None:
        request = _make_request()
        result = evaluate_communication_budget_guard(
            request, budget_tokens_used=500, budget_tokens_limit=0
        )
        assert result is None

    def test_negative_limit_returns_none(self) -> None:
        request = _make_request()
        result = evaluate_communication_budget_guard(
            request, budget_tokens_used=500, budget_tokens_limit=-1
        )
        assert result is None

    def test_budget_exceeded_denies(self) -> None:
        request = _make_request()
        result = evaluate_communication_budget_guard(
            request, budget_tokens_used=1000, budget_tokens_limit=1000
        )
        assert result is not None
        assert result.verdict == "deny"
        assert result.reasons[0].code == "budget_exceeded"
        assert result.risk_level == "high"

    def test_budget_over_limit_denies(self) -> None:
        request = _make_request()
        result = evaluate_communication_budget_guard(
            request, budget_tokens_used=1500, budget_tokens_limit=1000
        )
        assert result is not None
        assert result.verdict == "deny"

    def test_under_budget_no_communication_returns_none(self) -> None:
        request = _make_request()
        result = evaluate_communication_budget_guard(
            request,
            budget_tokens_used=500,
            budget_tokens_limit=1000,
            communication_tokens=0,
        )
        assert result is None

    def test_communication_exceeds_ratio_warns(self) -> None:
        request = _make_request()
        result = evaluate_communication_budget_guard(
            request,
            budget_tokens_used=200,
            budget_tokens_limit=1000,
            communication_tokens=400,  # 40% > 30% default
        )
        assert result is not None
        assert result.verdict == "allow"
        assert result.reasons[0].code == "communication_budget_warning"
        assert result.risk_level == "medium"
        assert result.obligations.require_receipt is True

    def test_communication_within_ratio_returns_none(self) -> None:
        request = _make_request()
        result = evaluate_communication_budget_guard(
            request,
            budget_tokens_used=200,
            budget_tokens_limit=1000,
            communication_tokens=200,  # 20% < 30% default
        )
        assert result is None

    def test_custom_ratio_threshold(self) -> None:
        request = _make_request(communication_budget_ratio=0.1)
        result = evaluate_communication_budget_guard(
            request,
            budget_tokens_used=200,
            budget_tokens_limit=1000,
            communication_tokens=150,  # 15% > 10% custom
        )
        assert result is not None
        assert result.verdict == "allow"
        assert result.reasons[0].code == "communication_budget_warning"

    def test_custom_ratio_within_returns_none(self) -> None:
        request = _make_request(communication_budget_ratio=0.5)
        result = evaluate_communication_budget_guard(
            request,
            budget_tokens_used=200,
            budget_tokens_limit=1000,
            communication_tokens=400,  # 40% < 50% custom
        )
        assert result is None


# ---------------------------------------------------------------------------
# Budget tracking in store
# ---------------------------------------------------------------------------


class TestBudgetTrackingStore:
    def test_task_created_with_default_budget(self, store: KernelStore) -> None:
        task = store.create_task(
            conversation_id="c1",
            title="test",
            goal="test",
            source_channel="test",
        )
        loaded = store.get_task(task.task_id)
        assert loaded is not None
        assert loaded.budget_tokens_used == 0
        assert loaded.budget_tokens_limit is None

    def test_update_task_budget(self, store: KernelStore) -> None:
        task = store.create_task(
            conversation_id="c1",
            title="test",
            goal="test",
            source_channel="test",
        )
        store.update_task_budget(task.task_id, budget_tokens_used=500)
        loaded = store.get_task(task.task_id)
        assert loaded is not None
        assert loaded.budget_tokens_used == 500

    def test_update_task_budget_increments(self, store: KernelStore) -> None:
        task = store.create_task(
            conversation_id="c1",
            title="test",
            goal="test",
            source_channel="test",
        )
        store.update_task_budget(task.task_id, budget_tokens_used=100)
        store.update_task_budget(task.task_id, budget_tokens_used=300)
        loaded = store.get_task(task.task_id)
        assert loaded is not None
        assert loaded.budget_tokens_used == 300

    def test_budget_limit_stored_via_sql(self, store: KernelStore) -> None:
        """Verify budget_tokens_limit is readable when set via direct SQL."""
        task = store.create_task(
            conversation_id="c1",
            title="test",
            goal="test",
            source_channel="test",
        )
        # Set budget limit directly since create_task doesn't expose it yet
        store._get_conn().execute(
            "UPDATE tasks SET budget_tokens_limit = ? WHERE task_id = ?",
            (5000, task.task_id),
        )
        loaded = store.get_task(task.task_id)
        assert loaded is not None
        assert loaded.budget_tokens_limit == 5000


# ---------------------------------------------------------------------------
# Constants and module-level checks
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_valid_monotonicity_classes(self) -> None:
        assert {
            "readonly",
            "additive",
            "compensatable_mutation",
            "irreversible_mutation",
        } == _VALID_MONOTONICITY_CLASSES

    def test_skip_and_require_are_disjoint(self) -> None:
        assert set() == _MONOTONICITY_SKIP_COORDINATION & _MONOTONICITY_REQUIRE_COORDINATION

    def test_skip_and_require_cover_all(self) -> None:
        assert (
            _MONOTONICITY_SKIP_COORDINATION | _MONOTONICITY_REQUIRE_COORDINATION
            == _VALID_MONOTONICITY_CLASSES
        )

    def test_default_ratio(self) -> None:
        assert _DEFAULT_COMMUNICATION_BUDGET_RATIO == 0.3


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    def test_fresh_db_has_budget_columns(self, store: KernelStore) -> None:
        """A fresh KernelStore should have budget columns in the tasks table."""
        cols = {row[1] for row in store._get_conn().execute("PRAGMA table_info(tasks)").fetchall()}
        assert "budget_tokens_used" in cols
        assert "budget_tokens_limit" in cols

    def test_migration_idempotent(self, store: KernelStore) -> None:
        """Running migration again should not fail."""
        store._migrate_budget_v17()
        cols = {row[1] for row in store._get_conn().execute("PRAGMA table_info(tasks)").fetchall()}
        assert "budget_tokens_used" in cols
        assert "budget_tokens_limit" in cols
