"""Tests for Typed Blackboard Primitive (Spec 08)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.artifacts.blackboard import BlackboardService
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.records import (
    BlackboardEntryStatus,
    BlackboardEntryType,
    BlackboardRecord,
)


@pytest.fixture
def store():
    return KernelStore(Path(":memory:"))


@pytest.fixture
def service(store):
    return BlackboardService(store)


@pytest.fixture
def task_id(store):
    """Create a minimal task and return its ID."""
    task = store.create_task(
        conversation_id="conv_test",
        title="Test task",
        goal="Test blackboard",
        priority="normal",
        owner="kernel",
        policy_profile="autonomous",
        source_channel="test",
    )
    return task.task_id


@pytest.fixture
def step_id(store):
    return store.generate_id("step")


class TestBlackboardEntryType:
    def test_all_types_defined(self):
        expected = {"claim", "evidence", "patch", "risk", "conflict", "todo", "decision"}
        assert set(BlackboardEntryType.__members__) == expected

    def test_str_enum_values(self):
        assert BlackboardEntryType.claim == "claim"
        assert BlackboardEntryType.evidence == "evidence"
        assert BlackboardEntryType.risk == "risk"

    def test_is_str_subclass(self):
        assert isinstance(BlackboardEntryType.claim, str)


class TestBlackboardEntryStatus:
    def test_all_statuses_defined(self):
        expected = {"active", "superseded", "resolved"}
        assert set(BlackboardEntryStatus.__members__) == expected

    def test_is_str_subclass(self):
        assert isinstance(BlackboardEntryStatus.active, str)


class TestBlackboardRecord:
    def test_defaults(self):
        r = BlackboardRecord(
            entry_id="bb_1",
            task_id="task_1",
            step_id="step_1",
            step_attempt_id=None,
            entry_type="claim",
        )
        assert r.confidence == 0.5
        assert r.status == "active"
        assert r.content == {}
        assert r.supersedes_entry_id is None
        assert r.resolution is None
        assert r.created_at is None

    def test_with_content(self):
        r = BlackboardRecord(
            entry_id="bb_2",
            task_id="task_1",
            step_id="step_1",
            step_attempt_id="sa_1",
            entry_type="evidence",
            content={"finding": "memory leak in module X"},
            confidence=0.9,
        )
        assert r.content["finding"] == "memory leak in module X"
        assert r.confidence == 0.9


class TestBlackboardServicePost:
    def test_post_basic(self, service, task_id, step_id):
        entry = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="claim",
            content={"text": "The API is RESTful"},
            confidence=0.8,
        )
        assert entry.entry_id.startswith("bb_")
        assert entry.task_id == task_id
        assert entry.step_id == step_id
        assert entry.entry_type == "claim"
        assert entry.content == {"text": "The API is RESTful"}
        assert entry.confidence == 0.8
        assert entry.status == "active"
        assert entry.created_at is not None

    def test_post_all_entry_types(self, service, task_id, step_id):
        for entry_type in BlackboardEntryType:
            entry = service.post(
                task_id=task_id,
                step_id=step_id,
                entry_type=entry_type,
                content={"type": str(entry_type)},
            )
            assert entry.entry_type == entry_type

    def test_post_invalid_entry_type(self, service, task_id, step_id):
        with pytest.raises(ValueError, match="Invalid entry_type"):
            service.post(
                task_id=task_id,
                step_id=step_id,
                entry_type="invalid_type",
                content={},
            )

    def test_post_confidence_too_high(self, service, task_id, step_id):
        with pytest.raises(ValueError, match="confidence must be between"):
            service.post(
                task_id=task_id,
                step_id=step_id,
                entry_type="claim",
                content={},
                confidence=1.5,
            )

    def test_post_confidence_too_low(self, service, task_id, step_id):
        with pytest.raises(ValueError, match="confidence must be between"):
            service.post(
                task_id=task_id,
                step_id=step_id,
                entry_type="claim",
                content={},
                confidence=-0.1,
            )

    def test_post_confidence_boundary_zero(self, service, task_id, step_id):
        entry = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="claim",
            content={},
            confidence=0.0,
        )
        assert entry.confidence == 0.0

    def test_post_confidence_boundary_one(self, service, task_id, step_id):
        entry = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="claim",
            content={},
            confidence=1.0,
        )
        assert entry.confidence == 1.0

    def test_post_with_step_attempt_id(self, service, task_id, step_id):
        entry = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="evidence",
            content={"proof": "test passed"},
            step_attempt_id="sa_123",
        )
        assert entry.step_attempt_id == "sa_123"

    def test_post_emits_event(self, service, store, task_id, step_id):
        entry = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="risk",
            content={"risk": "data loss"},
        )
        count = store.count_events_by_type(
            entity_type="blackboard",
            entity_id=entry.entry_id,
            event_type="blackboard.entry_posted",
        )
        assert count == 1

    def test_post_default_confidence(self, service, task_id, step_id):
        entry = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="todo",
            content={"action": "review"},
        )
        assert entry.confidence == 0.5

    def test_post_content_is_copied(self, service, task_id, step_id):
        original = {"key": "value"}
        entry = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="claim",
            content=original,
        )
        original["key"] = "mutated"
        assert entry.content == {"key": "value"}


class TestBlackboardServiceQuery:
    def test_query_all(self, service, task_id, step_id):
        service.post(task_id=task_id, step_id=step_id, entry_type="claim", content={"a": 1})
        service.post(task_id=task_id, step_id=step_id, entry_type="evidence", content={"b": 2})
        results = service.query(task_id)
        assert len(results) == 2

    def test_query_by_entry_type(self, service, task_id, step_id):
        service.post(task_id=task_id, step_id=step_id, entry_type="claim", content={})
        service.post(task_id=task_id, step_id=step_id, entry_type="evidence", content={})
        service.post(task_id=task_id, step_id=step_id, entry_type="claim", content={})
        results = service.query(task_id, entry_type="claim")
        assert len(results) == 2
        assert all(r.entry_type == "claim" for r in results)

    def test_query_by_status(self, service, task_id, step_id):
        service.post(task_id=task_id, step_id=step_id, entry_type="claim", content={})
        results = service.query(task_id, status="active")
        assert len(results) == 1
        results = service.query(task_id, status="resolved")
        assert len(results) == 0

    def test_query_empty(self, service, task_id):
        results = service.query(task_id)
        assert results == []

    def test_query_task_scoped(self, service, store, step_id):
        """Entries from one task are not visible in another task's query."""
        t1 = store.create_task(
            conversation_id="c1",
            title="T1",
            goal="G1",
            priority="normal",
            owner="kernel",
            policy_profile="autonomous",
            source_channel="test",
        )
        t2 = store.create_task(
            conversation_id="c2",
            title="T2",
            goal="G2",
            priority="normal",
            owner="kernel",
            policy_profile="autonomous",
            source_channel="test",
        )
        service.post(
            task_id=t1.task_id,
            step_id=step_id,
            entry_type="claim",
            content={"x": 1},
        )
        service.post(
            task_id=t2.task_id,
            step_id=step_id,
            entry_type="claim",
            content={"y": 2},
        )
        r1 = service.query(t1.task_id)
        r2 = service.query(t2.task_id)
        assert len(r1) == 1
        assert len(r2) == 1
        assert r1[0].content == {"x": 1}
        assert r2[0].content == {"y": 2}

    def test_query_combined_filters(self, service, task_id, step_id):
        service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="claim",
            content={},
        )
        e = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="risk",
            content={},
        )
        service.resolve(e.entry_id, resolution="mitigated")
        results = service.query(task_id, entry_type="risk", status="resolved")
        assert len(results) == 1

    def test_query_ordering_by_created_at(self, service, task_id, step_id):
        """Entries are returned in creation order."""
        ids = []
        for _ in range(5):
            e = service.post(
                task_id=task_id,
                step_id=step_id,
                entry_type="claim",
                content={},
            )
            ids.append(e.entry_id)
        results = service.query(task_id)
        assert [r.entry_id for r in results] == ids


class TestBlackboardServiceSupersede:
    def test_supersede(self, service, store, task_id, step_id):
        old = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="claim",
            content={"v": 1},
            confidence=0.6,
        )
        new_entry = BlackboardRecord(
            entry_id=store.generate_id("bb"),
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=None,
            entry_type="claim",
            content={"v": 2},
            confidence=0.9,
        )
        result = service.supersede(old.entry_id, new_entry=new_entry)
        assert result.supersedes_entry_id == old.entry_id
        assert result.confidence == 0.9
        assert result.content == {"v": 2}
        # old entry should be superseded
        old_updated = store.get_blackboard_entry(old.entry_id)
        assert old_updated is not None
        assert old_updated.status == "superseded"

    def test_supersede_nonexistent(self, service):
        new_entry = BlackboardRecord(
            entry_id="bb_new",
            task_id="task_x",
            step_id="step_x",
            step_attempt_id=None,
            entry_type="claim",
        )
        with pytest.raises(ValueError, match="not found"):
            service.supersede("bb_nonexistent", new_entry=new_entry)

    def test_supersede_emits_event(self, service, store, task_id, step_id):
        old = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="claim",
            content={},
        )
        new_entry = BlackboardRecord(
            entry_id=store.generate_id("bb"),
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=None,
            entry_type="claim",
            content={"updated": True},
        )
        service.supersede(old.entry_id, new_entry=new_entry)
        count = store.count_events_by_type(
            entity_type="blackboard",
            entity_id=old.entry_id,
            event_type="blackboard.entry_superseded",
        )
        assert count == 1

    def test_supersede_new_entry_is_active(self, service, store, task_id, step_id):
        old = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="claim",
            content={},
        )
        new_entry = BlackboardRecord(
            entry_id=store.generate_id("bb"),
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=None,
            entry_type="claim",
            content={"v": "new"},
        )
        result = service.supersede(old.entry_id, new_entry=new_entry)
        assert result.status == "active"

    def test_supersede_only_active_visible(self, service, store, task_id, step_id):
        old = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="claim",
            content={"v": 1},
        )
        new_entry = BlackboardRecord(
            entry_id=store.generate_id("bb"),
            task_id=task_id,
            step_id=step_id,
            step_attempt_id=None,
            entry_type="claim",
            content={"v": 2},
        )
        service.supersede(old.entry_id, new_entry=new_entry)
        active = service.query(task_id, status="active")
        superseded = service.query(task_id, status="superseded")
        assert len(active) == 1
        assert active[0].content == {"v": 2}
        assert len(superseded) == 1
        assert superseded[0].content == {"v": 1}


class TestBlackboardServiceResolve:
    def test_resolve(self, service, task_id, step_id):
        entry = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="todo",
            content={"action": "fix bug"},
        )
        resolved = service.resolve(entry.entry_id, resolution="fixed in commit abc")
        assert resolved.status == "resolved"
        assert resolved.resolution == "fixed in commit abc"

    def test_resolve_nonexistent(self, service):
        with pytest.raises(ValueError, match="not found"):
            service.resolve("bb_nonexistent", resolution="done")

    def test_resolve_emits_event(self, service, store, task_id, step_id):
        entry = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="conflict",
            content={},
        )
        service.resolve(entry.entry_id, resolution="resolved")
        count = store.count_events_by_type(
            entity_type="blackboard",
            entity_id=entry.entry_id,
            event_type="blackboard.entry_resolved",
        )
        assert count == 1

    def test_resolve_preserves_content(self, service, task_id, step_id):
        entry = service.post(
            task_id=task_id,
            step_id=step_id,
            entry_type="risk",
            content={"severity": "high"},
        )
        resolved = service.resolve(entry.entry_id, resolution="mitigated")
        assert resolved.content == {"severity": "high"}


class TestBlackboardStorePersistence:
    def test_insert_and_get(self, store):
        record = BlackboardRecord(
            entry_id="bb_test1",
            task_id="task_1",
            step_id="step_1",
            step_attempt_id="sa_1",
            entry_type="evidence",
            content={"key": "value"},
            confidence=0.75,
            created_at=time.time(),
        )
        store.insert_blackboard_entry(record)
        got = store.get_blackboard_entry("bb_test1")
        assert got is not None
        assert got.entry_id == "bb_test1"
        assert got.content == {"key": "value"}
        assert got.confidence == 0.75
        assert got.step_attempt_id == "sa_1"

    def test_get_nonexistent(self, store):
        assert store.get_blackboard_entry("bb_nope") is None

    def test_query_ordering(self, store):
        now = time.time()
        for i in range(3):
            store.insert_blackboard_entry(
                BlackboardRecord(
                    entry_id=f"bb_{i}",
                    task_id="task_1",
                    step_id="step_1",
                    step_attempt_id=None,
                    entry_type="claim",
                    created_at=now + i,
                )
            )
        results = store.query_blackboard_entries(task_id="task_1")
        assert [r.entry_id for r in results] == ["bb_0", "bb_1", "bb_2"]

    def test_update_status_with_resolution(self, store):
        store.insert_blackboard_entry(
            BlackboardRecord(
                entry_id="bb_upd",
                task_id="task_1",
                step_id="step_1",
                step_attempt_id=None,
                entry_type="todo",
                created_at=time.time(),
            )
        )
        store.update_blackboard_entry_status("bb_upd", "resolved", resolution="done")
        got = store.get_blackboard_entry("bb_upd")
        assert got is not None
        assert got.status == "resolved"
        assert got.resolution == "done"

    def test_update_status_without_resolution(self, store):
        store.insert_blackboard_entry(
            BlackboardRecord(
                entry_id="bb_upd2",
                task_id="task_1",
                step_id="step_1",
                step_attempt_id=None,
                entry_type="claim",
                created_at=time.time(),
            )
        )
        store.update_blackboard_entry_status("bb_upd2", "superseded")
        got = store.get_blackboard_entry("bb_upd2")
        assert got is not None
        assert got.status == "superseded"
        assert got.resolution is None

    def test_insert_with_supersedes(self, store):
        store.insert_blackboard_entry(
            BlackboardRecord(
                entry_id="bb_parent",
                task_id="task_1",
                step_id="step_1",
                step_attempt_id=None,
                entry_type="claim",
                created_at=time.time(),
            )
        )
        store.insert_blackboard_entry(
            BlackboardRecord(
                entry_id="bb_child",
                task_id="task_1",
                step_id="step_1",
                step_attempt_id=None,
                entry_type="claim",
                supersedes_entry_id="bb_parent",
                created_at=time.time(),
            )
        )
        got = store.get_blackboard_entry("bb_child")
        assert got is not None
        assert got.supersedes_entry_id == "bb_parent"

    def test_content_json_roundtrip(self, store):
        complex_content = {
            "nested": {"key": [1, 2, 3]},
            "unicode": "hello world",
            "bool": True,
            "null": None,
        }
        store.insert_blackboard_entry(
            BlackboardRecord(
                entry_id="bb_json",
                task_id="task_1",
                step_id="step_1",
                step_attempt_id=None,
                entry_type="patch",
                content=complex_content,
                created_at=time.time(),
            )
        )
        got = store.get_blackboard_entry("bb_json")
        assert got is not None
        assert got.content == complex_content


class TestSchemaVersion:
    def test_schema_version_bumped(self, store):
        version = int(store.schema_version())
        # Should be >= 15 (where blackboard was introduced)
        assert version >= 15


class TestCrossStepVisibility:
    def test_entries_visible_across_steps(self, service, task_id, store):
        """Entries posted by step A are visible when step B queries."""
        step_a = store.generate_id("step")
        step_b = store.generate_id("step")
        service.post(
            task_id=task_id,
            step_id=step_a,
            entry_type="claim",
            content={"from": "A"},
        )
        service.post(
            task_id=task_id,
            step_id=step_b,
            entry_type="evidence",
            content={"from": "B"},
        )
        all_entries = service.query(task_id)
        assert len(all_entries) == 2
        step_ids = {e.step_id for e in all_entries}
        assert step_a in step_ids
        assert step_b in step_ids


class TestContextCompilerBlackboard:
    def test_context_pack_includes_blackboard(self):
        from hermit.kernel.context.compiler.compiler import ContextCompiler
        from hermit.kernel.context.models.context import (
            TaskExecutionContext,
            WorkingStateSnapshot,
        )

        compiler = ContextCompiler()
        ctx = TaskExecutionContext(
            conversation_id="conv_1",
            task_id="task_1",
            step_id="step_1",
            step_attempt_id="sa_1",
            source_channel="test",
            workspace_root="",
        )
        ws = WorkingStateSnapshot()
        bb_entries = [
            {
                "entry_id": "bb_1",
                "entry_type": "claim",
                "content": {"text": "hello"},
                "confidence": 0.8,
            },
        ]
        pack = compiler.compile(
            context=ctx,
            working_state=ws,
            beliefs=[],
            memories=[],
            query="test query",
            blackboard_entries=bb_entries,
        )
        assert pack.blackboard_entries == bb_entries
        payload = pack.to_payload()
        assert "blackboard_entries" in payload
        assert payload["blackboard_entries"] == bb_entries

    def test_context_pack_empty_blackboard(self):
        from hermit.kernel.context.compiler.compiler import ContextCompiler
        from hermit.kernel.context.models.context import (
            TaskExecutionContext,
            WorkingStateSnapshot,
        )

        compiler = ContextCompiler()
        ctx = TaskExecutionContext(
            conversation_id="conv_1",
            task_id="task_1",
            step_id="step_1",
            step_attempt_id="sa_1",
            source_channel="test",
            workspace_root="",
        )
        ws = WorkingStateSnapshot()
        pack = compiler.compile(
            context=ctx,
            working_state=ws,
            beliefs=[],
            memories=[],
            query="test",
        )
        assert pack.blackboard_entries == []
        payload = pack.to_payload()
        assert payload["blackboard_entries"] == []

    def test_context_pack_blackboard_in_payload_hash(self):
        """Blackboard entries affect the pack hash."""
        from hermit.kernel.context.compiler.compiler import ContextCompiler
        from hermit.kernel.context.models.context import (
            TaskExecutionContext,
            WorkingStateSnapshot,
        )

        compiler = ContextCompiler()
        ctx = TaskExecutionContext(
            conversation_id="conv_1",
            task_id="task_1",
            step_id="step_1",
            step_attempt_id="sa_1",
            source_channel="test",
            workspace_root="",
        )
        ws = WorkingStateSnapshot()
        pack_empty = compiler.compile(
            context=ctx,
            working_state=ws,
            beliefs=[],
            memories=[],
            query="test",
        )
        pack_with = compiler.compile(
            context=ctx,
            working_state=ws,
            beliefs=[],
            memories=[],
            query="test",
            blackboard_entries=[{"entry_type": "claim", "content": {}}],
        )
        assert pack_empty.pack_hash != pack_with.pack_hash
