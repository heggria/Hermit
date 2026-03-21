"""Unit tests for Program model and ProgramStoreMixin."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.program import (
    ACTIVE_PROGRAM_STATES,
    PROGRAM_STATE_TRANSITIONS,
    TERMINAL_PROGRAM_STATES,
    ProgramRecord,
    ProgramState,
    ProgramStatusProjection,
)

# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestProgramState:
    def test_values(self) -> None:
        assert ProgramState.draft == "draft"
        assert ProgramState.active == "active"
        assert ProgramState.paused == "paused"
        assert ProgramState.blocked == "blocked"
        assert ProgramState.completed == "completed"
        assert ProgramState.failed == "failed"

    def test_terminal_states(self) -> None:
        assert ProgramState.completed in TERMINAL_PROGRAM_STATES
        assert ProgramState.failed in TERMINAL_PROGRAM_STATES
        assert ProgramState.active not in TERMINAL_PROGRAM_STATES
        assert ProgramState.blocked not in TERMINAL_PROGRAM_STATES

    def test_active_states(self) -> None:
        assert ProgramState.draft in ACTIVE_PROGRAM_STATES
        assert ProgramState.active in ACTIVE_PROGRAM_STATES
        assert ProgramState.paused in ACTIVE_PROGRAM_STATES
        assert ProgramState.blocked in ACTIVE_PROGRAM_STATES
        assert ProgramState.completed not in ACTIVE_PROGRAM_STATES
        assert ProgramState.failed not in ACTIVE_PROGRAM_STATES

    def test_state_transitions_completeness(self) -> None:
        """Every ProgramState member must have a transitions entry."""
        for state in ProgramState:
            assert state in PROGRAM_STATE_TRANSITIONS

    def test_terminal_states_have_no_transitions(self) -> None:
        for state in TERMINAL_PROGRAM_STATES:
            assert PROGRAM_STATE_TRANSITIONS[state] == frozenset()

    def test_blocked_state_transitions(self) -> None:
        allowed = PROGRAM_STATE_TRANSITIONS[ProgramState.blocked]
        assert ProgramState.active in allowed
        assert ProgramState.paused in allowed
        assert ProgramState.failed in allowed
        assert ProgramState.completed not in allowed

    def test_active_can_transition_to_blocked(self) -> None:
        allowed = PROGRAM_STATE_TRANSITIONS[ProgramState.active]
        assert ProgramState.blocked in allowed


class TestProgramRecord:
    def test_defaults(self) -> None:
        now = time.time()
        rec = ProgramRecord(program_id="prog_1", title="Alpha", goal="Ship v1")
        assert rec.program_id == "prog_1"
        assert rec.title == "Alpha"
        assert rec.goal == "Ship v1"
        assert rec.status == ProgramState.draft
        assert rec.description == ""
        assert rec.priority == "normal"
        assert rec.program_contract_ref is None
        assert rec.budget_limits == {}
        assert rec.milestone_ids == []
        assert rec.metadata == {}
        assert rec.created_at >= now
        assert rec.updated_at >= now

    def test_custom_fields(self) -> None:
        rec = ProgramRecord(
            program_id="prog_2",
            title="Beta",
            goal="Migrate DB",
            status=ProgramState.active,
            description="Full migration",
            priority="high",
            program_contract_ref="contract_abc123",
            budget_limits={"tokens": 100000},
            milestone_ids=["ms_1", "ms_2"],
            metadata={"owner": "eng"},
        )
        assert rec.status == "active"
        assert rec.description == "Full migration"
        assert rec.priority == "high"
        assert rec.program_contract_ref == "contract_abc123"
        assert rec.budget_limits == {"tokens": 100000}
        assert rec.milestone_ids == ["ms_1", "ms_2"]
        assert rec.metadata == {"owner": "eng"}

    def test_mutable_defaults_isolation(self) -> None:
        a = ProgramRecord(program_id="a", title="A", goal="g")
        b = ProgramRecord(program_id="b", title="B", goal="g")
        a.milestone_ids.append("ms_x")
        assert "ms_x" not in b.milestone_ids
        a.budget_limits["tokens"] = 5
        assert "tokens" not in b.budget_limits


class TestProgramStatusProjection:
    def test_defaults(self) -> None:
        proj = ProgramStatusProjection(
            program_id="prog_1",
            title="Alpha",
            overall_state="active",
        )
        assert proj.progress_pct == 0.0
        assert proj.current_phase == ""
        assert proj.active_teams == 0
        assert proj.queued_tasks == 0
        assert proj.running_attempts == 0
        assert proj.blocked_items == 0
        assert proj.awaiting_human is False
        assert proj.latest_summary == ""
        assert proj.latest_risks == []
        assert proj.latest_benchmark_status == ""
        assert proj.last_updated_at == 0.0

    def test_custom_values(self) -> None:
        proj = ProgramStatusProjection(
            program_id="prog_1",
            title="Alpha",
            overall_state="active",
            progress_pct=75.5,
            current_phase="implementation",
            active_teams=3,
            queued_tasks=12,
            running_attempts=4,
            blocked_items=1,
            awaiting_human=True,
            latest_summary="On track",
            latest_risks=["scope_creep", "resource_shortage"],
            latest_benchmark_status="passing",
            last_updated_at=1700000000.0,
        )
        assert proj.progress_pct == 75.5
        assert proj.active_teams == 3
        assert proj.awaiting_human is True
        assert proj.latest_risks == ["scope_creep", "resource_shortage"]


# ---------------------------------------------------------------------------
# Store mixin tests
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


class TestProgramStoreMixin:
    def test_create_program_defaults(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship v1")
        assert prog.program_id.startswith("program_")
        assert prog.title == "Alpha"
        assert prog.goal == "Ship v1"
        assert prog.status == "draft"
        assert prog.description == ""
        assert prog.priority == "normal"
        assert prog.program_contract_ref is None
        assert prog.budget_limits == {}
        assert prog.milestone_ids == []
        assert prog.metadata == {}
        assert prog.created_at > 0
        assert prog.updated_at > 0

    def test_create_program_custom(self, store: KernelStore) -> None:
        prog = store.create_program(
            title="Beta",
            goal="Migrate",
            description="Full migration",
            priority="high",
            budget_limits={"tokens": 50000},
            metadata={"team": "infra"},
        )
        assert prog.title == "Beta"
        assert prog.description == "Full migration"
        assert prog.priority == "high"
        assert prog.budget_limits == {"tokens": 50000}
        assert prog.metadata == {"team": "infra"}

    def test_create_program_with_contract_ref(self, store: KernelStore) -> None:
        prog = store.create_program(
            title="Contracted",
            goal="With contract",
            program_contract_ref="contract_xyz",
        )
        assert prog.program_contract_ref == "contract_xyz"
        fetched = store.get_program(prog.program_id)
        assert fetched is not None
        assert fetched.program_contract_ref == "contract_xyz"

    def test_get_program(self, store: KernelStore) -> None:
        prog = store.create_program(title="Gamma", goal="Test")
        fetched = store.get_program(prog.program_id)
        assert fetched is not None
        assert fetched.program_id == prog.program_id
        assert fetched.title == "Gamma"

    def test_get_program_not_found(self, store: KernelStore) -> None:
        assert store.get_program("program_nonexistent") is None

    def test_list_programs_all(self, store: KernelStore) -> None:
        store.create_program(title="P1", goal="G1")
        store.create_program(title="P2", goal="G2")
        store.create_program(title="P3", goal="G3")
        programs = store.list_programs()
        assert len(programs) == 3

    def test_list_programs_filter_by_status(self, store: KernelStore) -> None:
        p1 = store.create_program(title="P1", goal="G1")
        store.create_program(title="P2", goal="G2")
        store.update_program_status(p1.program_id, "active")
        active = store.list_programs(status="active")
        assert len(active) == 1
        assert active[0].program_id == p1.program_id
        drafts = store.list_programs(status="draft")
        assert len(drafts) == 1

    def test_list_programs_filter_by_priority(self, store: KernelStore) -> None:
        store.create_program(title="P1", goal="G1", priority="high")
        store.create_program(title="P2", goal="G2", priority="normal")
        high = store.list_programs(priority="high")
        assert len(high) == 1
        assert high[0].title == "P1"

    def test_list_programs_limit(self, store: KernelStore) -> None:
        for i in range(5):
            store.create_program(title=f"P{i}", goal=f"G{i}")
        limited = store.list_programs(limit=3)
        assert len(limited) == 3

    def test_list_programs_ordered_by_created_desc(self, store: KernelStore) -> None:
        p1 = store.create_program(title="First", goal="G1")
        p2 = store.create_program(title="Second", goal="G2")
        programs = store.list_programs()
        assert programs[0].program_id == p2.program_id
        assert programs[1].program_id == p1.program_id

    def test_update_program_status(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        assert prog.status == "draft"
        store.update_program_status(prog.program_id, "active")
        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.status == "active"
        assert updated.updated_at > prog.updated_at

    def test_update_program_status_with_payload(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.update_program_status(prog.program_id, "active")
        store.update_program_status(
            prog.program_id,
            "completed",
            payload={"reason": "all_milestones_reached"},
        )
        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.status == "completed"

    def test_update_program_status_invalid_transition_raises(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        # draft -> completed is not allowed
        with pytest.raises(ValueError, match="Invalid program state transition"):
            store.update_program_status(prog.program_id, "completed")

    def test_update_program_status_terminal_cannot_transition(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.update_program_status(prog.program_id, "active")
        store.update_program_status(prog.program_id, "completed")
        with pytest.raises(ValueError, match="terminal state"):
            store.update_program_status(prog.program_id, "active")

    def test_update_program_status_blocked_transition(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.update_program_status(prog.program_id, "active")
        store.update_program_status(prog.program_id, "blocked")
        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.status == "blocked"
        # Can unblock back to active
        store.update_program_status(prog.program_id, "active")
        updated2 = store.get_program(prog.program_id)
        assert updated2 is not None
        assert updated2.status == "active"

    def test_update_program_status_nonexistent_program(self, store: KernelStore) -> None:
        # Should silently return without error
        store.update_program_status("program_nonexistent", "active")

    def test_update_program_contract_ref(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        assert prog.program_contract_ref is None
        store.update_program_contract_ref(prog.program_id, "contract_abc")
        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.program_contract_ref == "contract_abc"
        assert updated.updated_at > prog.updated_at

    def test_update_program_contract_ref_emits_event(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.update_program_contract_ref(prog.program_id, "contract_abc")
        events = store.list_events(event_type="program.contract_updated")
        assert len(events) >= 1
        latest = events[0]
        assert latest["entity_id"] == prog.program_id
        assert latest["payload"]["program_contract_ref"] == "contract_abc"

    def test_add_milestone_to_program(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        assert prog.milestone_ids == []
        store.add_milestone_to_program(prog.program_id, "ms_1")
        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.milestone_ids == ["ms_1"]
        assert updated.updated_at > prog.updated_at

    def test_add_multiple_milestones(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.add_milestone_to_program(prog.program_id, "ms_1")
        store.add_milestone_to_program(prog.program_id, "ms_2")
        store.add_milestone_to_program(prog.program_id, "ms_3")
        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.milestone_ids == ["ms_1", "ms_2", "ms_3"]

    def test_add_duplicate_milestone_idempotent(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.add_milestone_to_program(prog.program_id, "ms_1")
        store.add_milestone_to_program(prog.program_id, "ms_1")
        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.milestone_ids == ["ms_1"]

    def test_add_milestone_nonexistent_program(self, store: KernelStore) -> None:
        # Should silently return without error
        store.add_milestone_to_program("program_nonexistent", "ms_1")

    def test_remove_milestone_from_program(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.add_milestone_to_program(prog.program_id, "ms_1")
        store.add_milestone_to_program(prog.program_id, "ms_2")
        store.remove_milestone_from_program(prog.program_id, "ms_1")
        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.milestone_ids == ["ms_2"]

    def test_remove_milestone_nonexistent_is_noop(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.add_milestone_to_program(prog.program_id, "ms_1")
        store.remove_milestone_from_program(prog.program_id, "ms_nonexistent")
        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.milestone_ids == ["ms_1"]

    def test_remove_milestone_emits_event(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.add_milestone_to_program(prog.program_id, "ms_1")
        store.remove_milestone_from_program(prog.program_id, "ms_1")
        events = store.list_events(event_type="program.milestone_removed")
        assert len(events) >= 1
        latest = events[0]
        assert latest["payload"]["milestone_id"] == "ms_1"

    def test_update_program_metadata(self, store: KernelStore) -> None:
        prog = store.create_program(
            title="Alpha",
            goal="Ship",
            metadata={"team": "eng"},
        )
        store.update_program_metadata(prog.program_id, {"owner": "alice", "team": "platform"})
        updated = store.get_program(prog.program_id)
        assert updated is not None
        assert updated.metadata["owner"] == "alice"
        assert updated.metadata["team"] == "platform"  # overwritten
        assert updated.updated_at > prog.updated_at

    def test_update_program_metadata_emits_event(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.update_program_metadata(prog.program_id, {"key": "val"})
        events = store.list_events(event_type="program.metadata_updated")
        assert len(events) >= 1
        latest = events[0]
        assert latest["entity_id"] == prog.program_id
        assert latest["payload"]["metadata"]["key"] == "val"

    def test_create_program_emits_event(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        events = store.list_events(event_type="program.created")
        assert len(events) >= 1
        latest = events[0]
        assert latest["entity_id"] == prog.program_id
        assert latest["payload"]["title"] == "Alpha"

    def test_update_status_emits_event(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.update_program_status(prog.program_id, "active")
        events = store.list_events(event_type="program.active")
        assert len(events) >= 1
        latest = events[0]
        assert latest["entity_id"] == prog.program_id

    def test_blocked_status_emits_event(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.update_program_status(prog.program_id, "active")
        store.update_program_status(prog.program_id, "blocked")
        events = store.list_events(event_type="program.blocked")
        assert len(events) >= 1
        latest = events[0]
        assert latest["entity_id"] == prog.program_id

    def test_add_milestone_emits_event(self, store: KernelStore) -> None:
        prog = store.create_program(title="Alpha", goal="Ship")
        store.add_milestone_to_program(prog.program_id, "ms_1")
        events = store.list_events(event_type="program.milestone_added")
        assert len(events) >= 1
        latest = events[0]
        assert latest["payload"]["milestone_id"] == "ms_1"

    def test_full_lifecycle(self, store: KernelStore) -> None:
        prog = store.create_program(
            title="Full Lifecycle",
            goal="Validate all transitions",
            priority="high",
        )
        assert prog.status == "draft"

        store.update_program_status(prog.program_id, "active")
        store.add_milestone_to_program(prog.program_id, "ms_research")
        store.add_milestone_to_program(prog.program_id, "ms_impl")

        store.update_program_status(prog.program_id, "paused")
        store.update_program_status(prog.program_id, "active")
        store.update_program_status(prog.program_id, "completed")

        final = store.get_program(prog.program_id)
        assert final is not None
        assert final.status == "completed"
        assert final.milestone_ids == ["ms_research", "ms_impl"]
        assert final.priority == "high"

    def test_full_lifecycle_with_blocked(self, store: KernelStore) -> None:
        """Test lifecycle that includes the blocked state."""
        prog = store.create_program(title="Blocked Lifecycle", goal="Test blocked")
        store.update_program_status(prog.program_id, "active")
        store.update_program_status(prog.program_id, "blocked")
        store.update_program_status(prog.program_id, "active")
        store.update_program_status(prog.program_id, "blocked")
        store.update_program_status(prog.program_id, "failed")

        final = store.get_program(prog.program_id)
        assert final is not None
        assert final.status == "failed"

    def test_create_program_event_includes_contract_ref(self, store: KernelStore) -> None:
        store.create_program(
            title="With Contract",
            goal="Test contract ref in event",
            program_contract_ref="contract_ref_123",
        )
        events = store.list_events(event_type="program.created")
        assert len(events) >= 1
        latest = events[-1]
        assert latest["payload"]["program_contract_ref"] == "contract_ref_123"
