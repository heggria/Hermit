"""Unit tests for AssuranceStoreMixin CRUD operations."""

from __future__ import annotations

import json
import time

import pytest

from hermit.kernel.ledger.journal.store import KernelStore


@pytest.fixture
def store(kernel_store: KernelStore) -> KernelStore:
    """Alias for the in-memory kernel_store fixture."""
    return kernel_store


# ---------------------------------------------------------------------------
# Schema & indexes
# ---------------------------------------------------------------------------


class TestAssuranceSchema:
    def test_assurance_tables_exist(self, store: KernelStore) -> None:
        conn = store._get_conn()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "assurance_trace_envelopes" in tables
        assert "assurance_scenarios" in tables
        assert "assurance_reports" in tables
        assert "assurance_replay_entries" in tables

    def test_assurance_indexes_exist(self, store: KernelStore) -> None:
        conn = store._get_conn()
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        expected = {
            "idx_assurance_trace_run",
            "idx_assurance_trace_task",
            "idx_assurance_trace_seq",
            "idx_assurance_report_run",
            "idx_assurance_replay_scenario",
        }
        assert expected.issubset(indexes), f"Missing indexes: {expected - indexes}"


# ---------------------------------------------------------------------------
# Trace envelopes
# ---------------------------------------------------------------------------


class TestTraceEnvelopes:
    def test_create_and_retrieve_envelope(self, store: KernelStore) -> None:
        envelope_data = {"event": "task.created", "payload": {"key": "value"}}
        result = store.create_trace_envelope(
            trace_id="trace-001",
            run_id="run-001",
            task_id="task-001",
            event_seq=0,
            event_type="task.created",
            envelope_json=envelope_data,
            wallclock_at=time.time(),
            scenario_id="scn-001",
        )
        assert result["trace_id"] == "trace-001"
        assert result["run_id"] == "run-001"
        assert result["task_id"] == "task-001"
        assert result["event_seq"] == 0
        assert result["event_type"] == "task.created"
        assert result["scenario_id"] == "scn-001"
        # envelope_json is stored as canonical JSON string
        parsed = json.loads(result["envelope_json"])
        assert parsed["event"] == "task.created"

    def test_create_envelope_with_string_json(self, store: KernelStore) -> None:
        raw = '{"raw":true}'
        result = store.create_trace_envelope(
            trace_id="trace-002",
            run_id="run-001",
            task_id="task-001",
            event_seq=1,
            event_type="step.started",
            envelope_json=raw,
            wallclock_at=time.time(),
        )
        assert result["envelope_json"] == raw

    def test_get_trace_envelopes_by_run(self, store: KernelStore) -> None:
        now = time.time()
        for i in range(5):
            store.create_trace_envelope(
                trace_id=f"trace-{i}",
                run_id="run-A",
                task_id="task-A",
                event_seq=i,
                event_type="step.event",
                envelope_json={"seq": i},
                wallclock_at=now + i,
            )
        # Different run
        store.create_trace_envelope(
            trace_id="trace-other",
            run_id="run-B",
            task_id="task-B",
            event_seq=0,
            event_type="step.event",
            envelope_json={},
            wallclock_at=now,
        )

        results = store.get_trace_envelopes("run-A")
        assert len(results) == 5
        # Ordered by event_seq
        seqs = [r["event_seq"] for r in results]
        assert seqs == [0, 1, 2, 3, 4]

    def test_get_trace_envelopes_filtered_by_task(self, store: KernelStore) -> None:
        now = time.time()
        store.create_trace_envelope(
            trace_id="t1", run_id="run-X", task_id="task-1",
            event_seq=0, event_type="a", envelope_json={}, wallclock_at=now,
        )
        store.create_trace_envelope(
            trace_id="t2", run_id="run-X", task_id="task-2",
            event_seq=1, event_type="b", envelope_json={}, wallclock_at=now,
        )

        results = store.get_trace_envelopes("run-X", task_id="task-1")
        assert len(results) == 1
        assert results[0]["task_id"] == "task-1"

    def test_get_trace_envelopes_filtered_by_event_type(self, store: KernelStore) -> None:
        now = time.time()
        store.create_trace_envelope(
            trace_id="t1", run_id="run-Y", task_id="task-1",
            event_seq=0, event_type="approval.requested", envelope_json={}, wallclock_at=now,
        )
        store.create_trace_envelope(
            trace_id="t2", run_id="run-Y", task_id="task-1",
            event_seq=1, event_type="receipt.issued", envelope_json={}, wallclock_at=now,
        )

        results = store.get_trace_envelopes("run-Y", event_type="receipt.issued")
        assert len(results) == 1
        assert results[0]["event_type"] == "receipt.issued"

    def test_get_trace_envelopes_with_limit(self, store: KernelStore) -> None:
        now = time.time()
        for i in range(10):
            store.create_trace_envelope(
                trace_id=f"t-{i}", run_id="run-limit", task_id="task-1",
                event_seq=i, event_type="evt", envelope_json={}, wallclock_at=now + i,
            )

        results = store.get_trace_envelopes("run-limit", limit=3)
        assert len(results) == 3

    def test_get_trace_envelopes_empty(self, store: KernelStore) -> None:
        results = store.get_trace_envelopes("nonexistent-run")
        assert results == []


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


class TestScenarios:
    def test_create_and_get_scenario(self, store: KernelStore) -> None:
        spec = {
            "scenario_id": "scn-001",
            "metadata": {"name": "test_scenario"},
            "schema_version": 2,
        }
        result = store.create_scenario(
            scenario_id="scn-001",
            schema_version=2,
            spec_json=spec,
        )
        assert result["scenario_id"] == "scn-001"
        assert result["schema_version"] == 2
        parsed = json.loads(result["spec_json"])
        assert parsed["metadata"]["name"] == "test_scenario"
        assert result["created_at"] > 0
        assert result["updated_at"] is not None

    def test_create_scenario_with_string_json(self, store: KernelStore) -> None:
        raw = '{"raw_spec": true}'
        result = store.create_scenario(
            scenario_id="scn-002",
            schema_version=1,
            spec_json=raw,
        )
        assert result["spec_json"] == raw

    def test_get_scenario_not_found(self, store: KernelStore) -> None:
        assert store.get_scenario("nonexistent") is None

    def test_get_scenario_returns_correct_row(self, store: KernelStore) -> None:
        store.create_scenario("scn-A", 1, {"name": "A"})
        store.create_scenario("scn-B", 2, {"name": "B"})

        result = store.get_scenario("scn-B")
        assert result is not None
        assert result["scenario_id"] == "scn-B"
        assert result["schema_version"] == 2


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


class TestReports:
    def test_create_and_get_report(self, store: KernelStore) -> None:
        report_data = {"violations": [], "timelines": {}}
        result = store.create_report(
            report_id="rpt-001",
            scenario_id="scn-001",
            run_id="run-001",
            status="pass",
            verdict="All contracts passed",
            report_json=report_data,
        )
        assert result["report_id"] == "rpt-001"
        assert result["scenario_id"] == "scn-001"
        assert result["run_id"] == "run-001"
        assert result["status"] == "pass"
        assert result["verdict"] == "All contracts passed"
        assert result["created_at"] > 0

    def test_get_report_not_found(self, store: KernelStore) -> None:
        assert store.get_report("nonexistent") is None

    def test_create_report_with_none_scenario(self, store: KernelStore) -> None:
        result = store.create_report(
            report_id="rpt-002",
            scenario_id=None,
            run_id=None,
            status="fail",
            verdict=None,
            report_json="{}",
        )
        assert result["scenario_id"] is None
        assert result["run_id"] is None
        assert result["verdict"] is None

    def test_list_reports_all(self, store: KernelStore) -> None:
        store.create_report("rpt-1", "scn-1", "run-1", "pass", "ok", "{}")
        store.create_report("rpt-2", "scn-1", "run-2", "fail", "bad", "{}")
        store.create_report("rpt-3", "scn-2", "run-3", "pass", "ok", "{}")

        results = store.list_reports()
        assert len(results) == 3

    def test_list_reports_by_scenario(self, store: KernelStore) -> None:
        store.create_report("rpt-1", "scn-1", "run-1", "pass", "ok", "{}")
        store.create_report("rpt-2", "scn-1", "run-2", "fail", "bad", "{}")
        store.create_report("rpt-3", "scn-2", "run-3", "pass", "ok", "{}")

        results = store.list_reports(scenario_id="scn-1")
        assert len(results) == 2
        assert all(r["scenario_id"] == "scn-1" for r in results)

    def test_list_reports_by_run(self, store: KernelStore) -> None:
        store.create_report("rpt-1", "scn-1", "run-1", "pass", "ok", "{}")
        store.create_report("rpt-2", "scn-1", "run-2", "fail", "bad", "{}")

        results = store.list_reports(run_id="run-2")
        assert len(results) == 1
        assert results[0]["run_id"] == "run-2"

    def test_list_reports_by_scenario_and_run(self, store: KernelStore) -> None:
        store.create_report("rpt-1", "scn-1", "run-1", "pass", "ok", "{}")
        store.create_report("rpt-2", "scn-1", "run-2", "fail", "bad", "{}")
        store.create_report("rpt-3", "scn-2", "run-1", "pass", "ok", "{}")

        results = store.list_reports(scenario_id="scn-1", run_id="run-1")
        assert len(results) == 1
        assert results[0]["report_id"] == "rpt-1"

    def test_list_reports_with_limit(self, store: KernelStore) -> None:
        for i in range(10):
            store.create_report(f"rpt-{i}", "scn-1", f"run-{i}", "pass", "ok", "{}")

        results = store.list_reports(limit=3)
        assert len(results) == 3

    def test_list_reports_empty(self, store: KernelStore) -> None:
        results = store.list_reports()
        assert results == []


# ---------------------------------------------------------------------------
# Replay entries
# ---------------------------------------------------------------------------


class TestReplayEntries:
    def test_create_and_list_replay_entry(self, store: KernelStore) -> None:
        entry_data = {"trace_events": [1, 2, 3]}
        result = store.create_replay_entry(
            entry_id="replay-001",
            scenario_id="scn-001",
            run_id="run-001",
            event_head_hash="abc123",
            source="live",
            sanitized=False,
            entry_json=entry_data,
        )
        assert result["entry_id"] == "replay-001"
        assert result["scenario_id"] == "scn-001"
        assert result["run_id"] == "run-001"
        assert result["event_head_hash"] == "abc123"
        assert result["source"] == "live"
        assert result["sanitized"] == 0
        parsed = json.loads(result["entry_json"])
        assert parsed["trace_events"] == [1, 2, 3]

    def test_create_replay_entry_sanitized(self, store: KernelStore) -> None:
        result = store.create_replay_entry(
            entry_id="replay-002",
            scenario_id="scn-001",
            run_id="run-001",
            event_head_hash=None,
            source="synthetic",
            sanitized=True,
            entry_json="{}",
        )
        assert result["sanitized"] == 1
        assert result["source"] == "synthetic"
        assert result["event_head_hash"] is None

    def test_list_replay_entries_all(self, store: KernelStore) -> None:
        store.create_replay_entry("r1", "scn-1", "run-1", None, "live", False, "{}")
        store.create_replay_entry("r2", "scn-2", "run-2", None, "live", False, "{}")

        results = store.list_replay_entries()
        assert len(results) == 2

    def test_list_replay_entries_by_scenario(self, store: KernelStore) -> None:
        store.create_replay_entry("r1", "scn-1", "run-1", None, "live", False, "{}")
        store.create_replay_entry("r2", "scn-1", "run-2", None, "live", False, "{}")
        store.create_replay_entry("r3", "scn-2", "run-3", None, "live", False, "{}")

        results = store.list_replay_entries(scenario_id="scn-1")
        assert len(results) == 2
        assert all(r["scenario_id"] == "scn-1" for r in results)

    def test_list_replay_entries_with_limit(self, store: KernelStore) -> None:
        for i in range(10):
            store.create_replay_entry(
                f"r-{i}", "scn-1", f"run-{i}", None, "live", False, "{}"
            )

        results = store.list_replay_entries(limit=4)
        assert len(results) == 4

    def test_list_replay_entries_empty(self, store: KernelStore) -> None:
        results = store.list_replay_entries()
        assert results == []

    def test_list_replay_entries_none_scenario_filter(self, store: KernelStore) -> None:
        store.create_replay_entry("r1", None, "run-1", None, "live", False, "{}")
        store.create_replay_entry("r2", "scn-1", "run-2", None, "live", False, "{}")

        # Without filter, returns all
        results = store.list_replay_entries()
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Duplicate key rejection
# ---------------------------------------------------------------------------


class TestConstraints:
    def test_duplicate_trace_id_rejected(self, store: KernelStore) -> None:
        now = time.time()
        store.create_trace_envelope(
            "dup-trace", "run-1", "task-1", 0, "evt", "{}", now,
        )
        with pytest.raises(Exception):
            store.create_trace_envelope(
                "dup-trace", "run-1", "task-1", 1, "evt", "{}", now,
            )

    def test_duplicate_scenario_id_rejected(self, store: KernelStore) -> None:
        store.create_scenario("dup-scn", 1, "{}")
        with pytest.raises(Exception):
            store.create_scenario("dup-scn", 2, "{}")

    def test_duplicate_report_id_rejected(self, store: KernelStore) -> None:
        store.create_report("dup-rpt", None, None, "pass", None, "{}")
        with pytest.raises(Exception):
            store.create_report("dup-rpt", None, None, "fail", None, "{}")

    def test_duplicate_replay_entry_id_rejected(self, store: KernelStore) -> None:
        store.create_replay_entry("dup-re", None, None, None, "live", False, "{}")
        with pytest.raises(Exception):
            store.create_replay_entry("dup-re", None, None, None, "live", False, "{}")
