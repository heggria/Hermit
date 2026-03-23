"""Unit tests for TraceRecorder."""

from __future__ import annotations

import pytest

from hermit.kernel.verification.assurance.models import TraceEnvelope
from hermit.kernel.verification.assurance.recorder import TraceRecorder

# Fixtures `make_envelope` and `make_governed_trace` are importable helpers
# in tests/assurance/conftest.py — they are used here directly as functions
# rather than pytest fixtures.

# ---------------------------------------------------------------------------
# start_run
# ---------------------------------------------------------------------------


class TestStartRun:
    def test_returns_unique_ids(self) -> None:
        recorder = TraceRecorder()
        ids = {recorder.start_run() for _ in range(50)}
        assert len(ids) == 50, "start_run must produce unique run_ids"

    def test_includes_run_prefix(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        assert run_id.startswith("run-")

    def test_stores_scenario_id(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run(scenario_id="gov-chaos-restart-v1")
        envelope = recorder.record("task.created", "task-1", run_id=run_id)
        assert envelope.scenario_id == "gov-chaos-restart-v1"

    def test_no_scenario_id_leaves_none(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        envelope = recorder.record("task.created", "task-1", run_id=run_id)
        assert envelope.scenario_id is None


# ---------------------------------------------------------------------------
# record — monotonic event_seq
# ---------------------------------------------------------------------------


class TestRecord:
    def test_monotonic_event_seq(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        seqs = []
        for i in range(20):
            env = recorder.record(f"event.{i}", "task-1", run_id=run_id)
            seqs.append(env.event_seq)
        assert seqs == list(range(20))

    def test_event_seq_starts_at_zero(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        env = recorder.record("first", "task-1", run_id=run_id)
        assert env.event_seq == 0

    def test_record_without_run_auto_starts(self) -> None:
        recorder = TraceRecorder()
        env = recorder.record("event", "task-1")
        assert env.event_seq == 0
        assert env.run_id  # auto-generated run_id

    def test_record_unknown_run_raises(self) -> None:
        recorder = TraceRecorder()
        recorder.start_run()
        with pytest.raises(ValueError, match="Unknown run_id"):
            recorder.record("event", "task-1", run_id="run-nonexistent")

    def test_record_uses_latest_run_when_no_run_id(self) -> None:
        recorder = TraceRecorder()
        _first = recorder.start_run()
        second = recorder.start_run()
        env = recorder.record("event", "task-1")
        assert env.run_id == second

    def test_record_populates_all_fields(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run(scenario_id="scn-1")
        env = recorder.record(
            "tool_call.start",
            "task-42",
            run_id=run_id,
            step_id="step-0",
            step_attempt_id="attempt-0",
            phase="execution",
            actor_id="actor-1",
            causation_id="cause-1",
            correlation_id="corr-1",
            artifact_refs=["art-a", "art-b"],
            approval_ref="appr-1",
            decision_ref="dec-1",
            grant_ref="grant-1",
            lease_ref="lease-1",
            receipt_ref="rcpt-1",
            restart_epoch=2,
            payload={"tool": "bash"},
        )
        assert env.event_type == "tool_call.start"
        assert env.task_id == "task-42"
        assert env.step_id == "step-0"
        assert env.step_attempt_id == "attempt-0"
        assert env.phase == "execution"
        assert env.actor_id == "actor-1"
        assert env.causation_id == "cause-1"
        assert env.correlation_id == "corr-1"
        assert env.artifact_refs == ["art-a", "art-b"]
        assert env.approval_ref == "appr-1"
        assert env.decision_ref == "dec-1"
        assert env.grant_ref == "grant-1"
        assert env.lease_ref == "lease-1"
        assert env.receipt_ref == "rcpt-1"
        assert env.restart_epoch == 2
        assert env.payload == {"tool": "bash"}
        assert env.scenario_id == "scn-1"
        assert env.wallclock_at > 0
        assert env.trace_id.startswith("trace-")

    def test_logical_clock_increments(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        clocks = [recorder.record("e", "t", run_id=run_id).logical_clock for _ in range(5)]
        assert clocks == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# get_trace — filtering
# ---------------------------------------------------------------------------


class TestGetTrace:
    def test_unfiltered_returns_all(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        for i in range(5):
            recorder.record(f"type.{i % 2}", f"task-{i % 3}", run_id=run_id)
        assert len(recorder.get_trace(run_id)) == 5

    def test_filter_by_task_id(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        recorder.record("a", "task-1", run_id=run_id)
        recorder.record("b", "task-2", run_id=run_id)
        recorder.record("c", "task-1", run_id=run_id)
        result = recorder.get_trace(run_id, task_id="task-1")
        assert len(result) == 2
        assert all(e.task_id == "task-1" for e in result)

    def test_filter_by_event_type(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        recorder.record("approval.requested", "t", run_id=run_id)
        recorder.record("approval.granted", "t", run_id=run_id)
        recorder.record("approval.requested", "t", run_id=run_id)
        result = recorder.get_trace(run_id, event_type="approval.requested")
        assert len(result) == 2

    def test_filter_by_phase(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        recorder.record("a", "t", run_id=run_id, phase="policy_eval")
        recorder.record("b", "t", run_id=run_id, phase="execution")
        recorder.record("c", "t", run_id=run_id, phase="policy_eval")
        result = recorder.get_trace(run_id, phase="policy_eval")
        assert len(result) == 2
        assert all(e.phase == "policy_eval" for e in result)

    def test_conjunctive_filters(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        recorder.record("approval.requested", "task-1", run_id=run_id, phase="policy_eval")
        recorder.record("approval.requested", "task-2", run_id=run_id, phase="policy_eval")
        recorder.record("tool_call.start", "task-1", run_id=run_id, phase="execution")
        result = recorder.get_trace(
            run_id, task_id="task-1", event_type="approval.requested", phase="policy_eval"
        )
        assert len(result) == 1
        assert result[0].task_id == "task-1"
        assert result[0].event_type == "approval.requested"

    def test_unknown_run_returns_empty(self) -> None:
        recorder = TraceRecorder()
        assert recorder.get_trace("run-nonexistent") == []


# ---------------------------------------------------------------------------
# get_trace_slice — windowed retrieval
# ---------------------------------------------------------------------------


class TestGetTraceSlice:
    def test_window_around_center(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        for _ in range(30):
            recorder.record("e", "t", run_id=run_id)

        result = recorder.get_trace_slice(run_id, center_event_seq=15, window=5)
        seqs = [e.event_seq for e in result]
        assert seqs == list(range(10, 21))

    def test_window_clamps_at_start(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        for _ in range(10):
            recorder.record("e", "t", run_id=run_id)

        result = recorder.get_trace_slice(run_id, center_event_seq=2, window=5)
        seqs = [e.event_seq for e in result]
        # Should include seq 0..7 (clamped at 0, not negative)
        assert seqs == list(range(0, 8))

    def test_window_clamps_at_end(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        for _ in range(10):
            recorder.record("e", "t", run_id=run_id)

        result = recorder.get_trace_slice(run_id, center_event_seq=8, window=5)
        seqs = [e.event_seq for e in result]
        # Should include seq 3..9
        assert seqs == list(range(3, 10))

    def test_default_window_is_10(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        for _ in range(50):
            recorder.record("e", "t", run_id=run_id)

        result = recorder.get_trace_slice(run_id, center_event_seq=25)
        seqs = [e.event_seq for e in result]
        assert seqs == list(range(15, 36))

    def test_empty_run_returns_empty(self) -> None:
        recorder = TraceRecorder()
        assert recorder.get_trace_slice("run-unknown", 5) == []


# ---------------------------------------------------------------------------
# export_trace
# ---------------------------------------------------------------------------


class TestExportTrace:
    def test_export_returns_dicts(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        recorder.record("task.created", "task-1", run_id=run_id)
        recorder.record("receipt.issued", "task-1", run_id=run_id, receipt_ref="rcpt-1")

        exported = recorder.export_trace(run_id)
        assert isinstance(exported, list)
        assert len(exported) == 2
        for item in exported:
            assert isinstance(item, dict)

    def test_export_dict_keys_match_dataclass(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        env = recorder.record("task.created", "task-1", run_id=run_id)
        exported = recorder.export_trace(run_id)
        d = exported[0]

        # Verify critical fields are present
        assert d["trace_id"] == env.trace_id
        assert d["run_id"] == run_id
        assert d["task_id"] == "task-1"
        assert d["event_type"] == "task.created"
        assert d["event_seq"] == 0
        assert "wallclock_at" in d
        assert "logical_clock" in d
        assert "payload" in d

    def test_export_unknown_run_returns_empty(self) -> None:
        recorder = TraceRecorder()
        assert recorder.export_trace("run-nonexistent") == []

    def test_export_preserves_payload(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        recorder.record("e", "t", run_id=run_id, payload={"key": "value", "n": 42})
        exported = recorder.export_trace(run_id)
        assert exported[0]["payload"] == {"key": "value", "n": 42}


# ---------------------------------------------------------------------------
# Multiple independent runs
# ---------------------------------------------------------------------------


class TestMultipleRuns:
    def test_independent_event_seq(self) -> None:
        recorder = TraceRecorder()
        run_a = recorder.start_run()
        run_b = recorder.start_run()

        for _ in range(5):
            recorder.record("e", "t", run_id=run_a)
        for _ in range(3):
            recorder.record("e", "t", run_id=run_b)

        trace_a = recorder.get_trace(run_a)
        trace_b = recorder.get_trace(run_b)

        assert [e.event_seq for e in trace_a] == list(range(5))
        assert [e.event_seq for e in trace_b] == list(range(3))

    def test_runs_isolated_from_each_other(self) -> None:
        recorder = TraceRecorder()
        run_a = recorder.start_run()
        run_b = recorder.start_run()

        recorder.record("event.a", "task-a", run_id=run_a)
        recorder.record("event.b", "task-b", run_id=run_b)

        trace_a = recorder.get_trace(run_a)
        trace_b = recorder.get_trace(run_b)

        assert len(trace_a) == 1
        assert trace_a[0].event_type == "event.a"
        assert len(trace_b) == 1
        assert trace_b[0].event_type == "event.b"

    def test_export_runs_independently(self) -> None:
        recorder = TraceRecorder()
        run_a = recorder.start_run(scenario_id="scn-a")
        run_b = recorder.start_run(scenario_id="scn-b")

        recorder.record("a1", "t", run_id=run_a)
        recorder.record("a2", "t", run_id=run_a)
        recorder.record("b1", "t", run_id=run_b)

        export_a = recorder.export_trace(run_a)
        export_b = recorder.export_trace(run_b)

        assert len(export_a) == 2
        assert len(export_b) == 1
        assert export_a[0]["scenario_id"] == "scn-a"
        assert export_b[0]["scenario_id"] == "scn-b"

    def test_different_scenario_ids(self) -> None:
        recorder = TraceRecorder()
        run_a = recorder.start_run(scenario_id="chaos-restart")
        run_b = recorder.start_run(scenario_id="approval-deadlock")

        env_a = recorder.record("e", "t", run_id=run_a)
        env_b = recorder.record("e", "t", run_id=run_b)

        assert env_a.scenario_id == "chaos-restart"
        assert env_b.scenario_id == "approval-deadlock"


# ---------------------------------------------------------------------------
# Persistence via KernelStore
# ---------------------------------------------------------------------------


class TestRecorderPersistence:
    """Tests for TraceRecorder with KernelStore persistence."""

    def test_record_with_store_persists_to_db(self, kernel_store) -> None:
        recorder = TraceRecorder(store=kernel_store)
        run_id = recorder.start_run()
        env = recorder.record("task.created", "task-1", run_id=run_id)

        # Verify persisted in store
        rows = kernel_store.get_trace_envelopes(run_id)
        assert len(rows) == 1
        assert rows[0]["trace_id"] == env.trace_id
        assert rows[0]["task_id"] == "task-1"
        assert rows[0]["event_type"] == "task.created"

    def test_record_with_store_also_in_memory(self, kernel_store) -> None:
        recorder = TraceRecorder(store=kernel_store)
        run_id = recorder.start_run()
        recorder.record("task.created", "task-1", run_id=run_id)

        # In-memory access still works
        trace = recorder.get_trace(run_id)
        assert len(trace) == 1
        assert trace[0].task_id == "task-1"

    def test_load_trace_from_store(self, kernel_store) -> None:
        recorder = TraceRecorder(store=kernel_store)
        run_id = recorder.start_run()
        recorder.record("task.created", "task-1", run_id=run_id, payload={"key": "val"})
        recorder.record("approval.requested", "task-1", run_id=run_id)

        # Create a fresh recorder with same store to confirm we load from DB
        recorder2 = TraceRecorder(store=kernel_store)
        loaded = recorder2.load_trace(run_id)
        assert len(loaded) == 2
        assert all(isinstance(e, TraceEnvelope) for e in loaded)
        assert loaded[0].event_type == "task.created"
        assert loaded[0].payload == {"key": "val"}
        assert loaded[1].event_type == "approval.requested"

    def test_load_trace_without_store_uses_memory(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        recorder.record("task.created", "task-1", run_id=run_id)
        recorder.record("approval.requested", "task-1", run_id=run_id)

        loaded = recorder.load_trace(run_id)
        assert len(loaded) == 2
        assert loaded[0].event_type == "task.created"

    def test_load_trace_unknown_run_returns_empty(self) -> None:
        recorder = TraceRecorder()
        assert recorder.load_trace("run-nonexistent") == []

    def test_load_task_trace_from_store(self, kernel_store) -> None:
        recorder = TraceRecorder(store=kernel_store)
        run_id = recorder.start_run()
        recorder.record("task.created", "task-1", run_id=run_id)
        recorder.record("task.created", "task-2", run_id=run_id)
        recorder.record("approval.requested", "task-1", run_id=run_id)

        # Fresh recorder to prove DB retrieval
        recorder2 = TraceRecorder(store=kernel_store)
        loaded = recorder2.load_task_trace("task-1")
        assert len(loaded) == 2
        assert all(e.task_id == "task-1" for e in loaded)

    def test_load_task_trace_across_runs(self, kernel_store) -> None:
        recorder = TraceRecorder(store=kernel_store)
        run_a = recorder.start_run()
        run_b = recorder.start_run()
        recorder.record("e1", "task-shared", run_id=run_a)
        recorder.record("e2", "task-other", run_id=run_a)
        recorder.record("e3", "task-shared", run_id=run_b)

        loaded = recorder.load_task_trace("task-shared")
        assert len(loaded) == 2
        event_types = {e.event_type for e in loaded}
        assert event_types == {"e1", "e3"}

    def test_load_task_trace_without_store_uses_memory(self) -> None:
        recorder = TraceRecorder()
        run_a = recorder.start_run()
        run_b = recorder.start_run()
        recorder.record("e1", "task-x", run_id=run_a)
        recorder.record("e2", "task-y", run_id=run_a)
        recorder.record("e3", "task-x", run_id=run_b)

        loaded = recorder.load_task_trace("task-x")
        assert len(loaded) == 2
        assert all(e.task_id == "task-x" for e in loaded)

    def test_persist_run_batch(self, kernel_store) -> None:
        # Create recorder WITHOUT store — record in memory only
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        recorder.record("task.created", "task-1", run_id=run_id)
        recorder.record("approval.requested", "task-1", run_id=run_id)
        recorder.record("receipt.issued", "task-1", run_id=run_id)

        # Nothing persisted yet
        rows = kernel_store.get_trace_envelopes(run_id)
        assert len(rows) == 0

        # Now attach store and batch persist
        recorder._store = kernel_store
        count = recorder.persist_run(run_id)
        assert count == 3

        # Verify all persisted
        rows = kernel_store.get_trace_envelopes(run_id)
        assert len(rows) == 3

    def test_persist_run_without_store_raises(self) -> None:
        recorder = TraceRecorder()
        run_id = recorder.start_run()
        recorder.record("e", "t", run_id=run_id)

        with pytest.raises(RuntimeError, match="No store configured"):
            recorder.persist_run(run_id)

    def test_backward_compatible_without_store(self) -> None:
        """TraceRecorder works identically without a store — no regressions."""
        recorder = TraceRecorder()
        run_id = recorder.start_run(scenario_id="scn-1")
        env = recorder.record(
            "tool_call.start",
            "task-42",
            run_id=run_id,
            step_id="step-0",
            phase="execution",
            payload={"tool": "bash"},
        )
        assert env.event_type == "tool_call.start"
        assert env.scenario_id == "scn-1"

        trace = recorder.get_trace(run_id)
        assert len(trace) == 1

        exported = recorder.export_trace(run_id)
        assert len(exported) == 1
        assert exported[0]["payload"] == {"tool": "bash"}

    def test_persisted_envelope_roundtrip_fidelity(self, kernel_store) -> None:
        """All TraceEnvelope fields survive persist + load roundtrip."""
        recorder = TraceRecorder(store=kernel_store)
        run_id = recorder.start_run(scenario_id="scn-rt")
        original = recorder.record(
            "tool_call.start",
            "task-99",
            run_id=run_id,
            step_id="step-1",
            step_attempt_id="attempt-1",
            phase="execution",
            actor_id="actor-1",
            causation_id="cause-1",
            correlation_id="corr-1",
            artifact_refs=["art-x"],
            approval_ref="appr-1",
            decision_ref="dec-1",
            grant_ref="grant-1",
            lease_ref="lease-1",
            receipt_ref="rcpt-1",
            restart_epoch=3,
            payload={"nested": {"data": [1, 2, 3]}},
        )

        # Load via fresh recorder
        recorder2 = TraceRecorder(store=kernel_store)
        loaded = recorder2.load_trace(run_id)
        assert len(loaded) == 1
        rt = loaded[0]

        assert rt.trace_id == original.trace_id
        assert rt.run_id == original.run_id
        assert rt.task_id == original.task_id
        assert rt.event_type == original.event_type
        assert rt.event_seq == original.event_seq
        assert rt.logical_clock == original.logical_clock
        assert rt.scenario_id == original.scenario_id
        assert rt.step_id == original.step_id
        assert rt.step_attempt_id == original.step_attempt_id
        assert rt.phase == original.phase
        assert rt.actor_id == original.actor_id
        assert rt.causation_id == original.causation_id
        assert rt.correlation_id == original.correlation_id
        assert rt.artifact_refs == original.artifact_refs
        assert rt.approval_ref == original.approval_ref
        assert rt.decision_ref == original.decision_ref
        assert rt.grant_ref == original.grant_ref
        assert rt.lease_ref == original.lease_ref
        assert rt.receipt_ref == original.receipt_ref
        assert rt.restart_epoch == original.restart_epoch
        assert rt.payload == original.payload
