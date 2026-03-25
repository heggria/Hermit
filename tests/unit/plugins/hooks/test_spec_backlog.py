"""Tests for the SpecBacklog DB-backed priority queue."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from hermit.plugins.builtin.hooks.metaloop.backlog import SpecBacklog
from hermit.plugins.builtin.hooks.metaloop.models import IterationState, PipelinePhase

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeStore:
    """In-memory store mimicking the spec_backlog table interface."""

    def __init__(self) -> None:
        self._specs: dict[str, dict[str, Any]] = {}
        self._claim_lock = threading.Lock()

    def create_spec_entry(self, *, spec_id: str, goal: str, **kwargs: Any) -> None:
        self._specs[spec_id] = {
            "spec_id": spec_id,
            "goal": goal,
            "status": "pending",
            "attempt": 1,
            "dag_task_id": None,
            "error": None,
            "priority": kwargs.get("priority", "normal"),
        }

    def get_spec_entry(self, spec_id: str = "", **kwargs: Any) -> dict[str, Any] | None:
        sid = spec_id or kwargs.get("spec_id", "")
        return self._specs.get(sid)

    def list_spec_backlog(
        self,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        results = list(self._specs.values())
        if status:
            results = [s for s in results if s["status"] == status]
        if priority:
            results = [s for s in results if s.get("priority") == priority]
        return results[:limit]

    def update_spec_status(
        self,
        spec_id: str = "",
        status: str = "",
        *,
        expected_status: str | None = None,
        dag_task_id: str | None = None,
        error: str | None = None,
        **kwargs: Any,
    ) -> bool:
        if spec_id not in self._specs:
            return False
        if expected_status is not None and self._specs[spec_id]["status"] != expected_status:
            return False
        self._specs[spec_id]["status"] = status
        if dag_task_id is not None:
            self._specs[spec_id]["dag_task_id"] = dag_task_id
        if error is not None:
            self._specs[spec_id]["error"] = error
        if "metadata" in kwargs:
            self._specs[spec_id]["metadata"] = kwargs["metadata"]
        return True

    def claim_next_spec(
        self,
        from_status: str = "pending",
        to_status: str = "planning",
    ) -> dict[str, Any] | None:
        with self._claim_lock:
            for spec in self._specs.values():
                if spec["status"] == from_status:
                    spec["status"] = to_status
                    return spec
        return None

    def increment_spec_attempt(self, *, spec_id: str) -> None:
        if spec_id in self._specs:
            self._specs[spec_id]["attempt"] += 1


@pytest.fixture
def fake_store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def backlog(fake_store: FakeStore) -> SpecBacklog:
    return SpecBacklog(fake_store)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSpecBacklogPeek:
    def test_peek_empty_backlog(self, backlog: SpecBacklog) -> None:
        assert backlog.peek_next() is None

    def test_peek_returns_pending(self, backlog: SpecBacklog, fake_store: FakeStore) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="improve X")
        result = backlog.peek_next()
        assert result is not None
        assert result.spec_id == "s1"
        assert result.phase == PipelinePhase.PENDING

    def test_peek_skips_non_pending(self, backlog: SpecBacklog, fake_store: FakeStore) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="improve X")
        fake_store.update_spec_status(spec_id="s1", status=PipelinePhase.PLANNING.value)
        assert backlog.peek_next() is None

    def test_peek_does_not_mutate(self, backlog: SpecBacklog, fake_store: FakeStore) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="improve X")
        backlog.peek_next()
        entry = fake_store.get_spec_entry(spec_id="s1")
        assert entry is not None
        assert entry["status"] == "pending"


class TestSpecBacklogClaim:
    def test_claim_empty_backlog(self, backlog: SpecBacklog) -> None:
        assert backlog.claim_next() is None

    def test_claim_transitions_to_planning(
        self, backlog: SpecBacklog, fake_store: FakeStore
    ) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="improve X")
        result = backlog.claim_next()
        assert result is not None
        assert result.spec_id == "s1"
        assert result.phase == PipelinePhase.PLANNING

    def test_claim_is_atomic(self, backlog: SpecBacklog, fake_store: FakeStore) -> None:
        """Only one claim should succeed for a single spec."""
        fake_store.create_spec_entry(spec_id="s1", goal="improve X")
        results: list[IterationState | None] = []

        def claim() -> None:
            results.append(backlog.claim_next())

        threads = [threading.Thread(target=claim) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        claimed = [r for r in results if r is not None]
        assert len(claimed) == 1
        assert claimed[0].spec_id == "s1"


class TestSpecBacklogAdvance:
    def test_advance_phase(self, backlog: SpecBacklog, fake_store: FakeStore) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        result = backlog.advance_phase("s1", PipelinePhase.PLANNING)
        assert result is not None
        assert result.phase == PipelinePhase.PLANNING
        entry = fake_store.get_spec_entry(spec_id="s1")
        assert entry is not None
        assert entry["status"] == "planning"

    def test_advance_with_dag_task_id(self, backlog: SpecBacklog, fake_store: FakeStore) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        backlog.advance_phase("s1", PipelinePhase.PLANNING)
        result = backlog.advance_phase("s1", PipelinePhase.IMPLEMENTING, dag_task_id="dag-abc")
        assert result is not None
        assert result.dag_task_id == "dag-abc"

    def test_advance_nonexistent_spec(self, backlog: SpecBacklog) -> None:
        result = backlog.advance_phase("nonexistent", PipelinePhase.PLANNING)
        assert result is None


class TestSpecBacklogMarkFailed:
    def test_retry_on_first_failure(self, backlog: SpecBacklog, fake_store: FakeStore) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(spec_id="s1", status=PipelinePhase.IMPLEMENTING.value)
        result = backlog.mark_failed("s1", error="build error", max_retries=2)
        assert result is not None
        assert result.phase == PipelinePhase.PENDING
        assert result.attempt == 2
        assert result.error == "build error"

    def test_final_failure_after_max_retries(
        self, backlog: SpecBacklog, fake_store: FakeStore
    ) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store._specs["s1"]["attempt"] = 2  # Already at max
        fake_store.update_spec_status(spec_id="s1", status=PipelinePhase.REVIEWING.value)
        result = backlog.mark_failed("s1", error="review failed", max_retries=2)
        assert result is not None
        assert result.phase == PipelinePhase.FAILED

    def test_mark_failed_nonexistent(self, backlog: SpecBacklog) -> None:
        result = backlog.mark_failed("nonexistent", error="oops")
        assert result is None


class TestSpecBacklogGetState:
    def test_get_state(self, backlog: SpecBacklog, fake_store: FakeStore) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        state = backlog.get_state("s1")
        assert state is not None
        assert state.spec_id == "s1"
        assert state.phase == PipelinePhase.PENDING

    def test_get_state_nonexistent(self, backlog: SpecBacklog) -> None:
        assert backlog.get_state("nonexistent") is None


class TestSpecBacklogNoStoreSupport:
    def test_no_store_methods(self) -> None:
        """SpecBacklog should gracefully return None when store lacks methods."""
        bare_store = object()  # No spec backlog methods
        backlog = SpecBacklog(bare_store)
        assert backlog.peek_next() is None
        assert backlog.claim_next() is None
        assert backlog.get_state("s1") is None
        assert backlog.advance_phase("s1", PipelinePhase.PLANNING) is None
        assert backlog.mark_failed("s1", error="no store") is None
