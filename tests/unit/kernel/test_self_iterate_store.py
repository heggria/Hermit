"""Tests for SelfIterateStoreMixin — spec_backlog and iteration_lessons CRUD."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture()
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


# ------------------------------------------------------------------
# spec_backlog tests
# ------------------------------------------------------------------


class TestCreateSpecEntry:
    def test_creates_and_returns_spec(self, store: KernelStore) -> None:
        result = store.create_spec_entry("spec_001", "Build feature X")
        assert result is not None
        assert result["spec_id"] == "spec_001"
        assert result["goal"] == "Build feature X"
        assert result["priority"] == "normal"
        assert result["source"] == "human"
        assert result["status"] == "pending"
        assert result["trust_zone"] == "normal"
        assert result["created_at"] is not None
        assert result["updated_at"] is not None

    def test_creates_with_optional_fields(self, store: KernelStore) -> None:
        result = store.create_spec_entry(
            "spec_002",
            "Improve perf",
            priority="high",
            source="signal",
            trust_zone="elevated",
            research_hints=["check cache", "profile DB"],
            metadata={"origin": "auto"},
        )
        assert result is not None
        assert result["priority"] == "high"
        assert result["source"] == "signal"
        assert result["trust_zone"] == "elevated"
        assert "check cache" in result["research_hints"]
        assert "auto" in result["metadata"]


class TestGetSpecEntry:
    def test_returns_none_for_missing(self, store: KernelStore) -> None:
        assert store.get_spec_entry("nonexistent") is None

    def test_returns_existing_spec(self, store: KernelStore) -> None:
        store.create_spec_entry("spec_010", "Test goal")
        result = store.get_spec_entry("spec_010")
        assert result is not None
        assert result["goal"] == "Test goal"


class TestListSpecBacklog:
    def test_returns_empty_list_when_no_specs(self, store: KernelStore) -> None:
        assert store.list_spec_backlog() == []

    def test_lists_all_specs(self, store: KernelStore) -> None:
        store.create_spec_entry("s1", "Goal 1")
        store.create_spec_entry("s2", "Goal 2")
        result = store.list_spec_backlog()
        assert len(result) == 2

    def test_filters_by_status(self, store: KernelStore) -> None:
        store.create_spec_entry("s1", "Goal 1")
        store.create_spec_entry("s2", "Goal 2")
        store.update_spec_status("s2", "in_progress")
        pending = store.list_spec_backlog(status="pending")
        assert len(pending) == 1
        assert pending[0]["spec_id"] == "s1"

    def test_filters_by_source(self, store: KernelStore) -> None:
        store.create_spec_entry("s1", "Goal 1", source="human")
        store.create_spec_entry("s2", "Goal 2", source="signal")
        result = store.list_spec_backlog(source="signal")
        assert len(result) == 1
        assert result[0]["spec_id"] == "s2"

    def test_respects_limit(self, store: KernelStore) -> None:
        for i in range(5):
            store.create_spec_entry(f"s{i}", f"Goal {i}")
        result = store.list_spec_backlog(limit=3)
        assert len(result) == 3


class TestUpdateSpecStatus:
    def test_updates_status(self, store: KernelStore) -> None:
        store.create_spec_entry("s1", "Goal")
        updated = store.update_spec_status("s1", "in_progress")
        assert updated is True
        spec = store.get_spec_entry("s1")
        assert spec is not None
        assert spec["status"] == "in_progress"

    def test_returns_false_for_missing_spec(self, store: KernelStore) -> None:
        assert store.update_spec_status("missing", "done") is False

    def test_updates_extra_fields(self, store: KernelStore) -> None:
        store.create_spec_entry("s1", "Goal")
        store.update_spec_status("s1", "running", dag_task_id="task_abc", priority="high")
        spec = store.get_spec_entry("s1")
        assert spec is not None
        assert spec["dag_task_id"] == "task_abc"
        assert spec["priority"] == "high"

    def test_ignores_unknown_extra_fields(self, store: KernelStore) -> None:
        store.create_spec_entry("s1", "Goal")
        # Should not raise even with unknown keys
        store.update_spec_status("s1", "done", unknown_field="ignored")
        spec = store.get_spec_entry("s1")
        assert spec is not None
        assert spec["status"] == "done"


class TestRemoveSpecEntry:
    def test_removes_existing_spec(self, store: KernelStore) -> None:
        store.create_spec_entry("s1", "Goal")
        assert store.remove_spec_entry("s1") is True
        assert store.get_spec_entry("s1") is None

    def test_returns_false_for_missing(self, store: KernelStore) -> None:
        assert store.remove_spec_entry("nonexistent") is False


class TestGetSpecByDagTaskId:
    def test_returns_none_when_no_match(self, store: KernelStore) -> None:
        assert store.get_spec_by_dag_task_id("task_missing") is None

    def test_returns_spec_with_matching_dag_task_id(self, store: KernelStore) -> None:
        store.create_spec_entry("s1", "Goal")
        store.update_spec_status("s1", "running", dag_task_id="task_123")
        result = store.get_spec_by_dag_task_id("task_123")
        assert result is not None
        assert result["spec_id"] == "s1"


class TestCountSpecsByStatus:
    def test_returns_zero_for_no_matches(self, store: KernelStore) -> None:
        assert store.count_specs_by_status("pending") == 0

    def test_counts_correctly(self, store: KernelStore) -> None:
        store.create_spec_entry("s1", "Goal 1")
        store.create_spec_entry("s2", "Goal 2")
        store.create_spec_entry("s3", "Goal 3")
        store.update_spec_status("s3", "done")
        assert store.count_specs_by_status("pending") == 2
        assert store.count_specs_by_status("done") == 1


class TestClaimNextSpec:
    def test_returns_none_when_nothing_to_claim(self, store: KernelStore) -> None:
        assert store.claim_next_spec("pending", "in_progress") is None

    def test_claims_and_transitions_spec(self, store: KernelStore) -> None:
        store.create_spec_entry("s1", "Goal 1")
        store.create_spec_entry("s2", "Goal 2")
        claimed = store.claim_next_spec("pending", "in_progress")
        assert claimed is not None
        assert claimed["status"] == "in_progress"
        # Only one should have been claimed
        remaining = store.list_spec_backlog(status="pending")
        assert len(remaining) == 1

    def test_does_not_claim_wrong_status(self, store: KernelStore) -> None:
        store.create_spec_entry("s1", "Goal 1")
        store.update_spec_status("s1", "done")
        assert store.claim_next_spec("pending", "in_progress") is None


# ------------------------------------------------------------------
# iteration_lessons tests
# ------------------------------------------------------------------


class TestCreateLesson:
    def test_creates_and_returns_lesson(self, store: KernelStore) -> None:
        result = store.create_lesson(
            "les_001", "iter_01", "build_failure", "Webpack config was wrong"
        )
        assert result is not None
        assert result["lesson_id"] == "les_001"
        assert result["iteration_id"] == "iter_01"
        assert result["category"] == "build_failure"
        assert result["summary"] == "Webpack config was wrong"
        assert result["created_at"] is not None

    def test_creates_with_optional_fields(self, store: KernelStore) -> None:
        result = store.create_lesson(
            "les_002",
            "iter_02",
            "test_failure",
            "Missing mock",
            trigger_condition="jest test suite",
            resolution="Add mock for API",
            applicable_files=["src/api.ts", "src/api.test.ts"],
            metadata={"severity": "medium"},
        )
        assert result is not None
        assert result["trigger_condition"] == "jest test suite"
        assert result["resolution"] == "Add mock for API"
        assert "src/api.ts" in result["applicable_files"]


class TestGetLesson:
    def test_returns_none_for_missing(self, store: KernelStore) -> None:
        assert store.get_lesson("nonexistent") is None

    def test_returns_existing_lesson(self, store: KernelStore) -> None:
        store.create_lesson("les_010", "iter_01", "perf", "Slow query")
        result = store.get_lesson("les_010")
        assert result is not None
        assert result["summary"] == "Slow query"


class TestListLessons:
    def test_returns_empty_list_when_no_lessons(self, store: KernelStore) -> None:
        assert store.list_lessons() == []

    def test_lists_all_lessons(self, store: KernelStore) -> None:
        store.create_lesson("l1", "iter_01", "build", "Summary 1")
        store.create_lesson("l2", "iter_01", "test", "Summary 2")
        result = store.list_lessons()
        assert len(result) == 2

    def test_filters_by_categories(self, store: KernelStore) -> None:
        store.create_lesson("l1", "iter_01", "build", "Summary 1")
        store.create_lesson("l2", "iter_01", "test", "Summary 2")
        store.create_lesson("l3", "iter_01", "perf", "Summary 3")
        result = store.list_lessons(categories=["build", "perf"])
        assert len(result) == 2
        cats = {r["category"] for r in result}
        assert cats == {"build", "perf"}

    def test_filters_by_applicable_to(self, store: KernelStore) -> None:
        store.create_lesson(
            "l1",
            "iter_01",
            "build",
            "S1",
            applicable_files=["src/foo.ts", "src/bar.ts"],
        )
        store.create_lesson(
            "l2",
            "iter_01",
            "build",
            "S2",
            applicable_files=["src/baz.ts"],
        )
        result = store.list_lessons(applicable_to="src/foo.ts")
        assert len(result) == 1
        assert result[0]["lesson_id"] == "l1"

    def test_filters_by_iteration_ids(self, store: KernelStore) -> None:
        store.create_lesson("l1", "iter_01", "build", "S1")
        store.create_lesson("l2", "iter_02", "test", "S2")
        store.create_lesson("l3", "iter_03", "perf", "S3")
        result = store.list_lessons(iteration_ids=["iter_01", "iter_03"])
        assert len(result) == 2
        ids = {r["iteration_id"] for r in result}
        assert ids == {"iter_01", "iter_03"}

    def test_respects_limit(self, store: KernelStore) -> None:
        for i in range(5):
            store.create_lesson(f"l{i}", "iter_01", "build", f"Summary {i}")
        result = store.list_lessons(limit=2)
        assert len(result) == 2


# ------------------------------------------------------------------
# Schema migration
# ------------------------------------------------------------------


class TestSchemaVersion:
    def test_schema_version_is_18(self, store: KernelStore) -> None:
        row = (
            store._get_conn()
            .execute("SELECT value FROM kernel_meta WHERE key = 'schema_version'")
            .fetchone()
        )
        assert row is not None
        assert row[0] == "18"

    def test_tables_exist(self, store: KernelStore) -> None:
        tables = {
            row[0]
            for row in store._get_conn()
            .execute("SELECT name FROM sqlite_master WHERE type='table'")
            .fetchall()
        }
        assert "spec_backlog" in tables
        assert "iteration_lessons" in tables
