"""Integration tests for KernelStore schema integrity — programs, teams,
milestones, events, and hash chain verification.

These tests exercise the store layer end-to-end against an in-memory SQLite
database to validate schema creation, CRUD operations, state transition
enforcement, event hash chaining, index usage, and canonical JSON storage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import _KNOWN_KERNEL_TABLES, KernelStore
from hermit.kernel.ledger.journal.store_support import canonical_json
from hermit.kernel.task.models.program import PROGRAM_STATE_TRANSITIONS, ProgramState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> KernelStore:
    """Fresh in-memory KernelStore per test."""
    return KernelStore(Path(":memory:"))


# ---------------------------------------------------------------------------
# 1. Schema creation — tables exist with correct columns
# ---------------------------------------------------------------------------


class TestSchemaCreation:
    def test_programs_table_exists(self, store: KernelStore) -> None:
        tables = store._existing_tables()
        assert "programs" in tables

    def test_teams_table_exists(self, store: KernelStore) -> None:
        tables = store._existing_tables()
        assert "teams" in tables

    def test_milestones_table_exists(self, store: KernelStore) -> None:
        tables = store._existing_tables()
        assert "milestones" in tables

    def test_events_table_exists(self, store: KernelStore) -> None:
        tables = store._existing_tables()
        assert "events" in tables

    def test_programs_columns(self, store: KernelStore) -> None:
        cols = {
            str(row["name"])
            for row in store._get_conn().execute("PRAGMA table_info(programs)").fetchall()
        }
        expected = {
            "program_id",
            "title",
            "goal",
            "status",
            "description",
            "priority",
            "program_contract_ref",
            "budget_limits_json",
            "milestone_ids_json",
            "metadata_json",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_teams_columns(self, store: KernelStore) -> None:
        cols = {
            str(row["name"])
            for row in store._get_conn().execute("PRAGMA table_info(teams)").fetchall()
        }
        expected = {
            "team_id",
            "program_id",
            "title",
            "workspace_id",
            "status",
            "role_assembly_json",
            "context_boundary_json",
            "created_at",
            "updated_at",
            "metadata_json",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_milestones_columns(self, store: KernelStore) -> None:
        cols = {
            str(row["name"])
            for row in store._get_conn().execute("PRAGMA table_info(milestones)").fetchall()
        }
        expected = {
            "milestone_id",
            "team_id",
            "title",
            "description",
            "status",
            "dependency_ids_json",
            "acceptance_criteria_json",
            "created_at",
            "completed_at",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"


# ---------------------------------------------------------------------------
# 2. Program CRUD
# ---------------------------------------------------------------------------


class TestProgramCRUD:
    def test_create_and_get_program(self, store: KernelStore) -> None:
        prog = store.create_program(
            title="Test Program",
            goal="Validate store integrity",
            description="Integration test program",
            priority="high",
        )
        assert prog.program_id.startswith("program_")
        assert prog.title == "Test Program"
        assert prog.goal == "Validate store integrity"
        assert prog.status == "draft"
        assert prog.priority == "high"
        assert prog.description == "Integration test program"

        retrieved = store.get_program(prog.program_id)
        assert retrieved is not None
        assert retrieved.program_id == prog.program_id
        assert retrieved.title == prog.title

    def test_list_programs(self, store: KernelStore) -> None:
        store.create_program(title="P1", goal="G1")
        store.create_program(title="P2", goal="G2")
        store.create_program(title="P3", goal="G3", priority="high")

        all_programs = store.list_programs()
        assert len(all_programs) == 3

        high_priority = store.list_programs(priority="high")
        assert len(high_priority) == 1
        assert high_priority[0].title == "P3"

    def test_update_program_status_emits_events(self, store: KernelStore) -> None:
        prog = store.create_program(title="Status Test", goal="Test transitions")

        # Count events before transition
        events_before = store._rows(
            "SELECT * FROM events WHERE entity_type = 'program' AND entity_id = ?",
            (prog.program_id,),
        )
        count_before = len(events_before)

        # draft → active
        store.update_program_status(prog.program_id, "active")

        events_after = store._rows(
            "SELECT * FROM events WHERE entity_type = 'program' AND entity_id = ?",
            (prog.program_id,),
        )
        assert len(events_after) > count_before, "Expected new event after status update"

        # Verify the new event type
        latest_event = events_after[-1]
        assert str(latest_event["event_type"]) == "program.active"

        # Verify DB state
        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.status == "active"


# ---------------------------------------------------------------------------
# 3. Team CRUD
# ---------------------------------------------------------------------------


class TestTeamCRUD:
    def test_create_and_get_team(self, store: KernelStore) -> None:
        prog = store.create_program(title="Team Host", goal="Host teams")
        team = store.create_team(
            program_id=prog.program_id,
            title="Alpha Team",
            workspace_id="ws_alpha",
            metadata={"env": "test"},
        )
        assert team.team_id.startswith("team_")
        assert team.program_id == prog.program_id
        assert team.title == "Alpha Team"
        assert team.workspace_id == "ws_alpha"
        assert team.status == "active"
        assert team.metadata == {"env": "test"}

        retrieved = store.get_team(team.team_id)
        assert retrieved is not None
        assert retrieved.team_id == team.team_id

    def test_list_teams_by_program(self, store: KernelStore) -> None:
        prog = store.create_program(title="Multi-Team", goal="Multiple teams")
        store.create_team(program_id=prog.program_id, title="T1", workspace_id="ws1")
        store.create_team(program_id=prog.program_id, title="T2", workspace_id="ws2")

        teams = store.list_teams_by_program(program_id=prog.program_id)
        assert len(teams) == 2

    def test_update_team_status_emits_events(self, store: KernelStore) -> None:
        prog = store.create_program(title="Team Status", goal="Test team status")
        team = store.create_team(
            program_id=prog.program_id,
            title="Status Team",
            workspace_id="ws_status",
        )

        events_before = store._rows(
            "SELECT * FROM events WHERE entity_type = 'team' AND entity_id = ?",
            (team.team_id,),
        )
        count_before = len(events_before)

        store.update_team_status(team.team_id, "paused")

        events_after = store._rows(
            "SELECT * FROM events WHERE entity_type = 'team' AND entity_id = ?",
            (team.team_id,),
        )
        assert len(events_after) > count_before

        latest_event = events_after[-1]
        assert str(latest_event["event_type"]) == "team.paused"

        updated = store.get_team(team.team_id)
        assert updated is not None
        assert updated.status == "paused"


# ---------------------------------------------------------------------------
# 4. Milestone CRUD
# ---------------------------------------------------------------------------


class TestMilestoneCRUD:
    def test_create_and_list_milestones(self, store: KernelStore) -> None:
        prog = store.create_program(title="Milestone Host", goal="Host milestones")
        team = store.create_team(program_id=prog.program_id, title="MS Team", workspace_id="ws_ms")

        ms1 = store.create_milestone(
            team_id=team.team_id,
            title="Milestone 1",
            description="First milestone",
            acceptance_criteria=["criterion_a", "criterion_b"],
        )
        ms2 = store.create_milestone(
            team_id=team.team_id,
            title="Milestone 2",
            dependency_ids=[ms1.milestone_id],
        )

        assert ms1.milestone_id.startswith("milestone_")
        assert ms1.status == "pending"
        assert ms1.acceptance_criteria == ["criterion_a", "criterion_b"]
        assert ms2.dependency_ids == [ms1.milestone_id]

        milestones = store.list_milestones_by_team(team_id=team.team_id)
        assert len(milestones) == 2

    def test_update_milestone_status_emits_events(self, store: KernelStore) -> None:
        prog = store.create_program(title="MS Status", goal="Test milestone status")
        team = store.create_team(
            program_id=prog.program_id, title="MS Status Team", workspace_id="ws_mss"
        )
        ms = store.create_milestone(team_id=team.team_id, title="Target MS")

        events_before = store._rows(
            "SELECT * FROM events WHERE entity_type = 'milestone' AND entity_id = ?",
            (ms.milestone_id,),
        )
        count_before = len(events_before)

        store.update_milestone_status(ms.milestone_id, "active")

        events_after = store._rows(
            "SELECT * FROM events WHERE entity_type = 'milestone' AND entity_id = ?",
            (ms.milestone_id,),
        )
        assert len(events_after) > count_before

        latest_event = events_after[-1]
        assert str(latest_event["event_type"]) == "milestone.active"

    def test_milestone_completed_sets_completed_at(self, store: KernelStore) -> None:
        prog = store.create_program(title="Complete MS", goal="Test completed_at")
        team = store.create_team(
            program_id=prog.program_id, title="Complete Team", workspace_id="ws_c"
        )
        ms = store.create_milestone(team_id=team.team_id, title="To Complete")

        assert ms.completed_at is None

        store.update_milestone_status(ms.milestone_id, "active")
        store.update_milestone_status(ms.milestone_id, "completed")

        updated = store.get_milestone(ms.milestone_id)
        assert updated is not None
        assert updated.status == "completed"
        assert updated.completed_at is not None
        assert updated.completed_at > 0


# ---------------------------------------------------------------------------
# 5. Batch query — list_teams_with_milestones
# ---------------------------------------------------------------------------


class TestBatchQuery:
    def test_list_teams_with_milestones_join(self, store: KernelStore) -> None:
        prog = store.create_program(title="Batch Q", goal="Test JOIN")
        t1 = store.create_team(program_id=prog.program_id, title="Team A", workspace_id="ws_a")
        t2 = store.create_team(program_id=prog.program_id, title="Team B", workspace_id="ws_b")
        _ms1 = store.create_milestone(team_id=t1.team_id, title="MS A1")
        _ms2 = store.create_milestone(team_id=t1.team_id, title="MS A2")
        ms3 = store.create_milestone(team_id=t2.team_id, title="MS B1")

        result = store.list_teams_with_milestones(program_id=prog.program_id)

        assert t1.team_id in result
        assert t2.team_id in result

        team_a_record, team_a_milestones = result[t1.team_id]
        assert team_a_record.title == "Team A"
        assert len(team_a_milestones) == 2
        milestone_titles = {m.title for m in team_a_milestones}
        assert milestone_titles == {"MS A1", "MS A2"}

        team_b_record, team_b_milestones = result[t2.team_id]
        assert team_b_record.title == "Team B"
        assert len(team_b_milestones) == 1
        assert team_b_milestones[0].milestone_id == ms3.milestone_id

    def test_team_with_no_milestones(self, store: KernelStore) -> None:
        prog = store.create_program(title="No MS", goal="Test empty milestones")
        t = store.create_team(
            program_id=prog.program_id, title="Lonely Team", workspace_id="ws_lonely"
        )

        result = store.list_teams_with_milestones(program_id=prog.program_id)
        assert t.team_id in result
        _, milestones = result[t.team_id]
        assert milestones == []


# ---------------------------------------------------------------------------
# 6. State transition validation
# ---------------------------------------------------------------------------


class TestStateTransitionValidation:
    def test_draft_to_completed_raises(self, store: KernelStore) -> None:
        """draft → completed is not allowed; must go through active first."""
        prog = store.create_program(title="Bad Transition", goal="Test invalid")
        assert prog.status == "draft"

        with pytest.raises(ValueError, match="Invalid program state transition"):
            store.update_program_status(prog.program_id, "completed")

    def test_draft_to_paused_raises(self, store: KernelStore) -> None:
        """draft → paused is not allowed."""
        prog = store.create_program(title="Bad Transition 2", goal="Test invalid 2")

        with pytest.raises(ValueError, match="Invalid program state transition"):
            store.update_program_status(prog.program_id, "paused")

    def test_completed_is_terminal(self, store: KernelStore) -> None:
        """completed is a terminal state — no further transitions allowed."""
        prog = store.create_program(title="Terminal", goal="Test terminal state")
        store.update_program_status(prog.program_id, "active")
        store.update_program_status(prog.program_id, "completed")

        with pytest.raises(ValueError, match="Invalid program state transition"):
            store.update_program_status(prog.program_id, "active")

    def test_failed_is_terminal(self, store: KernelStore) -> None:
        """failed is a terminal state — no further transitions allowed."""
        prog = store.create_program(title="Failed Terminal", goal="Test failed")
        store.update_program_status(prog.program_id, "failed")

        with pytest.raises(ValueError, match="Invalid program state transition"):
            store.update_program_status(prog.program_id, "active")

    def test_valid_lifecycle(self, store: KernelStore) -> None:
        """Full valid lifecycle: draft → active → paused → active → completed."""
        prog = store.create_program(title="Full Life", goal="Full lifecycle")
        store.update_program_status(prog.program_id, "active")
        store.update_program_status(prog.program_id, "paused")
        store.update_program_status(prog.program_id, "active")
        store.update_program_status(prog.program_id, "completed")

        final = store.get_program(prog.program_id)
        assert final is not None
        assert final.status == "completed"

    def test_all_transitions_match_spec(self, store: KernelStore) -> None:
        """Verify PROGRAM_STATE_TRANSITIONS covers all ProgramState values."""
        for state in ProgramState:
            assert state in PROGRAM_STATE_TRANSITIONS, (
                f"ProgramState.{state.value} is not in PROGRAM_STATE_TRANSITIONS"
            )


# ---------------------------------------------------------------------------
# 7. Event hash chain integrity
# ---------------------------------------------------------------------------


class TestEventHashChain:
    def test_task_event_hashes_are_chained(self, store: KernelStore) -> None:
        """Verify per-task event hash chain: each event's prev_event_hash
        matches the previous event's event_hash within the same task_id."""
        # Create a task to get events with a real task_id.
        task = store.create_task(
            conversation_id="conv_chain_test",
            title="Hash Chain Task",
            goal="Verify event hash chain integrity",
            source_channel="test",
        )
        task_id = task.task_id

        # Generate several events on this task via status updates and
        # manual event appends to build a chain of at least 4 events.
        store.append_event(
            event_type="test.step1",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            actor="kernel",
            payload={"step": 1},
        )
        store.append_event(
            event_type="test.step2",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            actor="kernel",
            payload={"step": 2},
        )
        store.append_event(
            event_type="test.step3",
            entity_type="task",
            entity_id=task_id,
            task_id=task_id,
            actor="kernel",
            payload={"step": 3},
        )

        # Query events for this task ordered by sequence.
        events = store._rows(
            "SELECT * FROM events WHERE task_id = ? ORDER BY event_seq ASC",
            (task_id,),
        )
        assert len(events) >= 4, f"Expected at least 4 events, got {len(events)}"

        # Verify hash chain continuity within this task.
        prev_hash: str | None = None
        for ev in events:
            event_hash = ev["event_hash"]
            stored_prev = ev["prev_event_hash"]
            algo = ev["hash_chain_algo"]

            assert event_hash is not None, f"event_hash is None for event {ev['event_id']}"
            assert len(event_hash) == 64, f"event_hash is not SHA-256 hex: {event_hash}"
            assert algo == "sha256-v1", f"Unexpected algo: {algo}"

            assert stored_prev == prev_hash, (
                f"Hash chain broken at event {ev['event_id']}: "
                f"prev_event_hash={stored_prev!r}, expected={prev_hash!r}"
            )
            prev_hash = event_hash

    def test_null_task_events_have_no_chain(self, store: KernelStore) -> None:
        """Events with task_id=None (program/team/milestone events) have
        prev_event_hash=None by design — the hash chain is per-task only."""
        prog = store.create_program(title="No Chain", goal="Test null-task events")
        store.update_program_status(prog.program_id, "active")

        events = store._rows("SELECT * FROM events WHERE task_id IS NULL ORDER BY event_seq ASC")
        assert len(events) >= 2

        for ev in events:
            # Each event has its own hash, but prev_event_hash is always None.
            assert ev["event_hash"] is not None
            assert len(ev["event_hash"]) == 64
            assert ev["prev_event_hash"] is None, (
                f"Expected prev_event_hash=None for task_id=None event "
                f"{ev['event_id']}, got {ev['prev_event_hash']!r}"
            )

    def test_independent_task_chains(self, store: KernelStore) -> None:
        """Two different tasks have independent hash chains that do not
        interfere with each other."""
        task_a = store.create_task(
            conversation_id="conv_a",
            title="Task A",
            goal="Independent chain A",
            source_channel="test",
        )
        task_b = store.create_task(
            conversation_id="conv_b",
            title="Task B",
            goal="Independent chain B",
            source_channel="test",
        )

        # Interleave events across tasks
        store.append_event(
            event_type="test.a1",
            entity_type="task",
            entity_id=task_a.task_id,
            task_id=task_a.task_id,
            actor="kernel",
            payload={"seq": "a1"},
        )
        store.append_event(
            event_type="test.b1",
            entity_type="task",
            entity_id=task_b.task_id,
            task_id=task_b.task_id,
            actor="kernel",
            payload={"seq": "b1"},
        )
        store.append_event(
            event_type="test.a2",
            entity_type="task",
            entity_id=task_a.task_id,
            task_id=task_a.task_id,
            actor="kernel",
            payload={"seq": "a2"},
        )

        # Verify chain A
        events_a = store._rows(
            "SELECT * FROM events WHERE task_id = ? ORDER BY event_seq ASC",
            (task_a.task_id,),
        )
        prev_a: str | None = None
        for ev in events_a:
            assert ev["prev_event_hash"] == prev_a
            prev_a = ev["event_hash"]

        # Verify chain B
        events_b = store._rows(
            "SELECT * FROM events WHERE task_id = ? ORDER BY event_seq ASC",
            (task_b.task_id,),
        )
        prev_b: str | None = None
        for ev in events_b:
            assert ev["prev_event_hash"] == prev_b
            prev_b = ev["event_hash"]

        # Chains must be independent — B's hashes should not appear in A's chain
        a_hashes = {ev["event_hash"] for ev in events_a}
        b_hashes = {ev["event_hash"] for ev in events_b}
        assert a_hashes.isdisjoint(b_hashes), "Task chains should be independent"


# ---------------------------------------------------------------------------
# 8. Index verification
# ---------------------------------------------------------------------------


class TestIndexVerification:
    def test_teams_program_index_used(self, store: KernelStore) -> None:
        """EXPLAIN QUERY PLAN on list_teams_by_program query should use idx_teams_program."""
        plan_rows = (
            store._get_conn()
            .execute(
                "EXPLAIN QUERY PLAN SELECT * FROM teams WHERE program_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                ("some_program_id", 50),
            )
            .fetchall()
        )
        plan_text = " ".join(str(row[3]) if len(row) > 3 else str(row) for row in plan_rows)
        assert "idx_teams_program" in plan_text, (
            f"Expected idx_teams_program in query plan, got: {plan_text}"
        )

    def test_milestones_team_index_used(self, store: KernelStore) -> None:
        """EXPLAIN QUERY PLAN on list_milestones_by_team query should use idx_milestones_team."""
        plan_rows = (
            store._get_conn()
            .execute(
                "EXPLAIN QUERY PLAN SELECT * FROM milestones WHERE team_id = ? "
                "ORDER BY created_at ASC LIMIT ?",
                ("some_team_id", 50),
            )
            .fetchall()
        )
        plan_text = " ".join(str(row[3]) if len(row) > 3 else str(row) for row in plan_rows)
        assert "idx_milestones_team" in plan_text, (
            f"Expected idx_milestones_team in query plan, got: {plan_text}"
        )

    def test_programs_status_index_exists(self, store: KernelStore) -> None:
        """Verify idx_programs_status index exists in sqlite_master."""
        row = store._row(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_programs_status'"
        )
        assert row is not None, "idx_programs_status index not found"


# ---------------------------------------------------------------------------
# 9. Canonical JSON — deterministic key ordering
# ---------------------------------------------------------------------------


class TestCanonicalJson:
    def test_program_metadata_canonical(self, store: KernelStore) -> None:
        """Verify stored JSON in programs uses canonical format (sorted keys,
        no extra whitespace)."""
        metadata = {"zebra": 1, "alpha": 2, "middle": 3}
        prog = store.create_program(title="Canonical", goal="Test JSON", metadata=metadata)

        raw_row = store._row(
            "SELECT metadata_json FROM programs WHERE program_id = ?",
            (prog.program_id,),
        )
        assert raw_row is not None
        stored_json = str(raw_row["metadata_json"])

        # canonical_json sorts keys and uses compact separators
        expected = canonical_json(metadata)
        assert stored_json == expected, f"Stored: {stored_json!r}, Expected: {expected!r}"

        # Verify sorted key order
        parsed = json.loads(stored_json)
        assert list(parsed.keys()) == sorted(parsed.keys())

    def test_team_context_boundary_canonical(self, store: KernelStore) -> None:
        """Verify context_boundary_json is stored canonically."""
        prog = store.create_program(title="CB Test", goal="Context boundary")
        team = store.create_team(
            program_id=prog.program_id,
            title="CB Team",
            workspace_id="ws_cb",
            context_boundary=["z_scope", "a_scope"],
        )

        raw_row = store._row(
            "SELECT context_boundary_json FROM teams WHERE team_id = ?",
            (team.team_id,),
        )
        assert raw_row is not None
        stored_json = str(raw_row["context_boundary_json"])
        expected = canonical_json(["z_scope", "a_scope"])
        assert stored_json == expected

    def test_event_payload_canonical(self, store: KernelStore) -> None:
        """Verify event payload_json is stored in canonical format."""
        prog = store.create_program(title="Event Canonical", goal="Test event JSON")

        events = store._rows(
            "SELECT payload_json FROM events WHERE entity_type = 'program' AND entity_id = ?",
            (prog.program_id,),
        )
        assert len(events) >= 1
        for ev in events:
            raw = str(ev["payload_json"])
            parsed = json.loads(raw)
            re_canonical = canonical_json(parsed)
            assert raw == re_canonical, (
                f"Event payload not canonical: stored={raw!r}, re-canonical={re_canonical!r}"
            )


# ---------------------------------------------------------------------------
# 10. Program contract ref
# ---------------------------------------------------------------------------


class TestProgramContractRef:
    def test_update_and_retrieve_contract_ref(self, store: KernelStore) -> None:
        prog = store.create_program(title="Contract Ref", goal="Test contract ref")
        assert prog.program_contract_ref is None

        store.update_program_contract_ref(prog.program_id, "contract_abc123")

        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.program_contract_ref == "contract_abc123"

    def test_create_with_contract_ref(self, store: KernelStore) -> None:
        prog = store.create_program(
            title="With Contract",
            goal="Created with ref",
            program_contract_ref="contract_initial",
        )
        assert prog.program_contract_ref == "contract_initial"

    def test_contract_ref_update_emits_event(self, store: KernelStore) -> None:
        prog = store.create_program(title="Contract Event", goal="Test contract event")

        events_before = store._rows(
            "SELECT * FROM events WHERE entity_type = 'program' AND entity_id = ?",
            (prog.program_id,),
        )

        store.update_program_contract_ref(prog.program_id, "contract_xyz")

        events_after = store._rows(
            "SELECT * FROM events WHERE entity_type = 'program' AND entity_id = ?",
            (prog.program_id,),
        )
        assert len(events_after) > len(events_before)
        latest = events_after[-1]
        assert str(latest["event_type"]) == "program.contract_updated"


# ---------------------------------------------------------------------------
# 11. Milestone add/remove on program
# ---------------------------------------------------------------------------


class TestMilestoneAddRemove:
    def test_add_milestone_to_program(self, store: KernelStore) -> None:
        prog = store.create_program(title="MS Add", goal="Test add milestone")
        team = store.create_team(program_id=prog.program_id, title="MS Team", workspace_id="ws_ms")
        ms = store.create_milestone(team_id=team.team_id, title="MS to add")

        assert prog.milestone_ids == []

        store.add_milestone_to_program(prog.program_id, ms.milestone_id)

        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert ms.milestone_id in updated.milestone_ids

    def test_remove_milestone_from_program(self, store: KernelStore) -> None:
        prog = store.create_program(title="MS Remove", goal="Test remove milestone")
        team = store.create_team(
            program_id=prog.program_id, title="Remove Team", workspace_id="ws_rm"
        )
        ms1 = store.create_milestone(team_id=team.team_id, title="MS Keep")
        ms2 = store.create_milestone(team_id=team.team_id, title="MS Remove")

        store.add_milestone_to_program(prog.program_id, ms1.milestone_id)
        store.add_milestone_to_program(prog.program_id, ms2.milestone_id)

        prog_with_both = store.get_program(prog.program_id)
        assert prog_with_both is not None
        assert len(prog_with_both.milestone_ids) == 2

        store.remove_milestone_from_program(prog.program_id, ms2.milestone_id)

        prog_after_remove = store.get_program(prog.program_id)
        assert prog_after_remove is not None
        assert ms1.milestone_id in prog_after_remove.milestone_ids
        assert ms2.milestone_id not in prog_after_remove.milestone_ids

    def test_add_duplicate_milestone_is_idempotent(self, store: KernelStore) -> None:
        prog = store.create_program(title="Dup MS", goal="Test idempotent add")
        team = store.create_team(
            program_id=prog.program_id, title="Dup Team", workspace_id="ws_dup"
        )
        ms = store.create_milestone(team_id=team.team_id, title="Dup MS")

        store.add_milestone_to_program(prog.program_id, ms.milestone_id)
        store.add_milestone_to_program(prog.program_id, ms.milestone_id)

        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.milestone_ids.count(ms.milestone_id) == 1

    def test_remove_nonexistent_milestone_is_noop(self, store: KernelStore) -> None:
        prog = store.create_program(title="Remove None", goal="Test noop remove")

        events_before = store._rows(
            "SELECT * FROM events WHERE entity_type = 'program' AND entity_id = ?",
            (prog.program_id,),
        )

        store.remove_milestone_from_program(prog.program_id, "nonexistent_id")

        events_after = store._rows(
            "SELECT * FROM events WHERE entity_type = 'program' AND entity_id = ?",
            (prog.program_id,),
        )
        # No event should be emitted for a no-op remove
        assert len(events_after) == len(events_before)


# ---------------------------------------------------------------------------
# 12. _KNOWN_KERNEL_TABLES includes programs, teams, milestones
# ---------------------------------------------------------------------------


class TestKnownKernelTables:
    def test_programs_in_known_tables(self) -> None:
        assert "programs" in _KNOWN_KERNEL_TABLES

    def test_teams_in_known_tables(self) -> None:
        assert "teams" in _KNOWN_KERNEL_TABLES

    def test_milestones_in_known_tables(self) -> None:
        assert "milestones" in _KNOWN_KERNEL_TABLES

    def test_events_in_known_tables(self) -> None:
        assert "events" in _KNOWN_KERNEL_TABLES
