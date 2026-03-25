from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.team import (
    ACTIVE_MILESTONE_STATES,
    ACTIVE_TEAM_STATES,
    TERMINAL_MILESTONE_STATES,
    TERMINAL_TEAM_STATES,
    MilestoneRecord,
    MilestoneState,
    RoleSlotSpec,
    TeamRecord,
    TeamState,
    TeamStatusProjection,
)


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


# ------------------------------------------------------------------
# StrEnum tests
# ------------------------------------------------------------------


class TestTeamState:
    def test_values(self) -> None:
        assert TeamState.ACTIVE == "active"
        assert TeamState.PAUSED == "paused"
        assert TeamState.COMPLETED == "completed"
        assert TeamState.BLOCKED == "blocked"
        assert TeamState.FAILED == "failed"
        assert TeamState.DISBANDED == "disbanded"

    def test_is_str(self) -> None:
        assert isinstance(TeamState.ACTIVE, str)

    def test_terminal_states(self) -> None:
        assert TeamState.COMPLETED in TERMINAL_TEAM_STATES
        assert TeamState.FAILED in TERMINAL_TEAM_STATES
        assert TeamState.DISBANDED in TERMINAL_TEAM_STATES
        assert TeamState.ACTIVE not in TERMINAL_TEAM_STATES
        assert TeamState.PAUSED not in TERMINAL_TEAM_STATES
        assert TeamState.BLOCKED not in TERMINAL_TEAM_STATES

    def test_active_states(self) -> None:
        assert TeamState.ACTIVE in ACTIVE_TEAM_STATES
        assert TeamState.PAUSED in ACTIVE_TEAM_STATES
        assert TeamState.BLOCKED in ACTIVE_TEAM_STATES
        assert TeamState.COMPLETED not in ACTIVE_TEAM_STATES
        assert TeamState.FAILED not in ACTIVE_TEAM_STATES
        assert TeamState.DISBANDED not in ACTIVE_TEAM_STATES

    def test_all_states_classified(self) -> None:
        """Every TeamState must be in exactly one of TERMINAL or ACTIVE."""
        all_states = set(TeamState)
        classified = set(TERMINAL_TEAM_STATES) | set(ACTIVE_TEAM_STATES)
        assert all_states == classified


class TestMilestoneState:
    def test_values(self) -> None:
        assert MilestoneState.PENDING == "pending"
        assert MilestoneState.ACTIVE == "active"
        assert MilestoneState.COMPLETED == "completed"
        assert MilestoneState.BLOCKED == "blocked"
        assert MilestoneState.FAILED == "failed"
        assert MilestoneState.SKIPPED == "skipped"

    def test_terminal_states(self) -> None:
        assert MilestoneState.COMPLETED in TERMINAL_MILESTONE_STATES
        assert MilestoneState.FAILED in TERMINAL_MILESTONE_STATES
        assert MilestoneState.SKIPPED in TERMINAL_MILESTONE_STATES
        assert MilestoneState.PENDING not in TERMINAL_MILESTONE_STATES
        assert MilestoneState.ACTIVE not in TERMINAL_MILESTONE_STATES
        assert MilestoneState.BLOCKED not in TERMINAL_MILESTONE_STATES

    def test_active_states(self) -> None:
        assert MilestoneState.PENDING in ACTIVE_MILESTONE_STATES
        assert MilestoneState.ACTIVE in ACTIVE_MILESTONE_STATES
        assert MilestoneState.BLOCKED in ACTIVE_MILESTONE_STATES
        assert MilestoneState.COMPLETED not in ACTIVE_MILESTONE_STATES
        assert MilestoneState.FAILED not in ACTIVE_MILESTONE_STATES
        assert MilestoneState.SKIPPED not in ACTIVE_MILESTONE_STATES

    def test_all_states_classified(self) -> None:
        """Every MilestoneState must be in exactly one of TERMINAL or ACTIVE."""
        all_states = set(MilestoneState)
        classified = set(TERMINAL_MILESTONE_STATES) | set(ACTIVE_MILESTONE_STATES)
        assert all_states == classified


# ------------------------------------------------------------------
# RoleSlotSpec tests
# ------------------------------------------------------------------


class TestRoleSlotSpec:
    def test_construction(self) -> None:
        spec = RoleSlotSpec(role="executor", count=4, config={"timeout": 300})
        assert spec.role == "executor"
        assert spec.count == 4
        assert spec.config == {"timeout": 300}

    def test_defaults(self) -> None:
        spec = RoleSlotSpec(role="planner")
        assert spec.count == 1
        assert spec.config == {}

    def test_role_assembly_typed(self) -> None:
        """role_assembly should accept dict[str, RoleSlotSpec]."""
        assembly = {
            "executor": RoleSlotSpec(role="executor", count=4),
            "verifier": RoleSlotSpec(role="verifier", count=3),
            "planner": RoleSlotSpec(role="planner", count=2),
        }
        team = TeamRecord(
            team_id="team_1",
            program_id="prog_1",
            title="Alpha",
            workspace_id="ws_1",
            status="active",
            role_assembly=assembly,
        )
        assert len(team.role_assembly) == 3
        assert team.role_assembly["executor"].count == 4
        assert team.role_assembly["verifier"].role == "verifier"


# ------------------------------------------------------------------
# Dataclass tests
# ------------------------------------------------------------------


class TestTeamRecord:
    def test_required_fields(self) -> None:
        team = TeamRecord(
            team_id="team_1",
            program_id="prog_1",
            title="Alpha Team",
            workspace_id="ws_1",
            status="active",
        )
        assert team.team_id == "team_1"
        assert team.program_id == "prog_1"
        assert team.title == "Alpha Team"
        assert team.workspace_id == "ws_1"
        assert team.status == "active"

    def test_defaults(self) -> None:
        team = TeamRecord(
            team_id="team_1",
            program_id="prog_1",
            title="Alpha Team",
            workspace_id="ws_1",
            status="active",
        )
        assert team.role_assembly == {}
        assert team.context_boundary == []
        assert team.created_at == 0.0
        assert team.updated_at == 0.0
        assert team.metadata == {}

    def test_role_assembly_with_role_slot_spec(self) -> None:
        roles = {
            "executor": RoleSlotSpec(role="executor", count=4),
            "planner": RoleSlotSpec(role="planner", count=2),
        }
        team = TeamRecord(
            team_id="team_1",
            program_id="prog_1",
            title="Alpha Team",
            workspace_id="ws_1",
            status="active",
            role_assembly=roles,
        )
        assert team.role_assembly["executor"].count == 4
        assert team.role_assembly["planner"].count == 2

    def test_context_boundary(self) -> None:
        boundary = ["src/module_a", "src/module_b"]
        team = TeamRecord(
            team_id="team_1",
            program_id="prog_1",
            title="Alpha Team",
            workspace_id="ws_1",
            status="active",
            context_boundary=boundary,
        )
        assert team.context_boundary == boundary


class TestMilestoneRecord:
    def test_required_fields(self) -> None:
        ms = MilestoneRecord(
            milestone_id="ms_1",
            team_id="team_1",
            title="MVP",
            description="Minimum viable product",
            status="pending",
        )
        assert ms.milestone_id == "ms_1"
        assert ms.team_id == "team_1"
        assert ms.title == "MVP"
        assert ms.description == "Minimum viable product"
        assert ms.status == "pending"

    def test_defaults(self) -> None:
        ms = MilestoneRecord(
            milestone_id="ms_1",
            team_id="team_1",
            title="MVP",
            description="",
            status="pending",
        )
        assert ms.dependency_ids == []
        assert ms.acceptance_criteria == []
        assert ms.created_at == 0.0
        assert ms.completed_at is None

    def test_acceptance_criteria(self) -> None:
        criteria = ["All tests pass", "Coverage > 80%"]
        ms = MilestoneRecord(
            milestone_id="ms_1",
            team_id="team_1",
            title="MVP",
            description="",
            status="pending",
            acceptance_criteria=criteria,
        )
        assert ms.acceptance_criteria == criteria

    def test_dependency_ids(self) -> None:
        ms = MilestoneRecord(
            milestone_id="ms_2",
            team_id="team_1",
            title="Beta",
            description="",
            status="pending",
            dependency_ids=["ms_1"],
        )
        assert ms.dependency_ids == ["ms_1"]


class TestTeamStatusProjection:
    def test_construction(self) -> None:
        proj = TeamStatusProjection(
            team_id="team_1",
            title="Alpha",
            state="active",
            workspace="ws_1",
            active_workers=3,
            milestone_progress="2/5",
            blockers=["waiting on approval"],
        )
        assert proj.team_id == "team_1"
        assert proj.active_workers == 3
        assert proj.milestone_progress == "2/5"
        assert proj.blockers == ["waiting on approval"]

    def test_defaults(self) -> None:
        proj = TeamStatusProjection(
            team_id="team_1",
            title="Alpha",
            state="active",
            workspace="ws_1",
        )
        assert proj.active_workers == 0
        assert proj.milestone_progress == "0/0"
        assert proj.blockers == []


# ------------------------------------------------------------------
# Store mixin tests
# ------------------------------------------------------------------


class TestTeamStoreMixin:
    def test_create_and_get_team(self, store: KernelStore) -> None:
        team = store.create_team(
            program_id="prog_1",
            title="Alpha Team",
            workspace_id="ws_1",
        )
        assert team.team_id.startswith("team_")
        assert team.program_id == "prog_1"
        assert team.title == "Alpha Team"
        assert team.workspace_id == "ws_1"
        assert team.status == "active"
        assert team.created_at > 0

        fetched = store.get_team(team.team_id)
        assert fetched is not None
        assert fetched.team_id == team.team_id
        assert fetched.title == "Alpha Team"

    def test_get_team_not_found(self, store: KernelStore) -> None:
        assert store.get_team("nonexistent") is None

    def test_create_team_with_role_slot_spec(self, store: KernelStore) -> None:
        """role_assembly with RoleSlotSpec round-trips through the store."""
        roles = {
            "executor": RoleSlotSpec(role="executor", count=4, config={"timeout": 60}),
            "verifier": RoleSlotSpec(role="verifier", count=3),
        }
        boundary = ["src/module_a"]
        meta = {"priority": "high"}
        team = store.create_team(
            program_id="prog_1",
            title="Beta Team",
            workspace_id="ws_2",
            status="paused",
            role_assembly=roles,
            context_boundary=boundary,
            metadata=meta,
        )
        assert team.status == "paused"
        assert isinstance(team.role_assembly["executor"], RoleSlotSpec)
        assert team.role_assembly["executor"].count == 4
        assert team.role_assembly["executor"].config == {"timeout": 60}
        assert team.role_assembly["verifier"].count == 3
        assert team.context_boundary == boundary
        assert team.metadata == meta

        # Verify round-trip from DB
        fetched = store.get_team(team.team_id)
        assert fetched is not None
        assert isinstance(fetched.role_assembly["executor"], RoleSlotSpec)
        assert fetched.role_assembly["executor"].count == 4
        assert fetched.role_assembly["executor"].config == {"timeout": 60}

    def test_create_team_with_legacy_role_assembly(self, store: KernelStore) -> None:
        """Legacy dict values are wrapped into RoleSlotSpec on read."""
        roles = {"lead": "agent_1", "reviewer": "agent_2"}
        team = store.create_team(
            program_id="prog_1",
            title="Legacy Team",
            workspace_id="ws_3",
            role_assembly=roles,
        )
        # Legacy values get wrapped in RoleSlotSpec
        assert isinstance(team.role_assembly["lead"], RoleSlotSpec)
        assert team.role_assembly["lead"].config == {"legacy_value": "agent_1"}
        assert team.role_assembly["lead"].count == 1

    def test_list_teams_by_program(self, store: KernelStore) -> None:
        store.create_team(program_id="prog_1", title="Team A", workspace_id="ws_1")
        store.create_team(program_id="prog_1", title="Team B", workspace_id="ws_2")
        store.create_team(program_id="prog_2", title="Team C", workspace_id="ws_3")

        teams = store.list_teams_by_program(program_id="prog_1")
        assert len(teams) == 2
        titles = {t.title for t in teams}
        assert titles == {"Team A", "Team B"}

    def test_list_teams_by_program_empty(self, store: KernelStore) -> None:
        teams = store.list_teams_by_program(program_id="nonexistent")
        assert teams == []

    def test_list_teams_by_program_limit(self, store: KernelStore) -> None:
        for i in range(5):
            store.create_team(program_id="prog_1", title=f"Team {i}", workspace_id=f"ws_{i}")
        teams = store.list_teams_by_program(program_id="prog_1", limit=3)
        assert len(teams) == 3


class TestUpdateTeamStatus:
    def test_update_team_status(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        assert team.status == "active"

        store.update_team_status(team.team_id, "paused")
        fetched = store.get_team(team.team_id)
        assert fetched is not None
        assert fetched.status == "paused"
        assert fetched.updated_at > team.updated_at

    def test_update_team_to_failed(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        store.update_team_status(team.team_id, TeamState.FAILED)
        fetched = store.get_team(team.team_id)
        assert fetched is not None
        assert fetched.status == "failed"

    def test_update_team_to_disbanded(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        store.update_team_status(team.team_id, TeamState.DISBANDED)
        fetched = store.get_team(team.team_id)
        assert fetched is not None
        assert fetched.status == "disbanded"

    def test_update_team_status_event(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        store.update_team_status(team.team_id, "blocked")
        events = store.list_events(event_type="team.blocked", limit=10)
        assert len(events) >= 1
        event = events[0]
        assert event["entity_type"] == "team"
        assert event["entity_id"] == team.team_id
        assert event["payload"]["status"] == "blocked"


class TestMilestoneStoreMixin:
    def test_create_and_get_milestone(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        ms = store.create_milestone(
            team_id=team.team_id,
            title="MVP",
            description="Minimum viable product",
        )
        assert ms.milestone_id.startswith("milestone_")
        assert ms.team_id == team.team_id
        assert ms.title == "MVP"
        assert ms.description == "Minimum viable product"
        assert ms.status == "pending"
        assert ms.completed_at is None
        assert ms.created_at > 0

        fetched = store.get_milestone(ms.milestone_id)
        assert fetched is not None
        assert fetched.milestone_id == ms.milestone_id

    def test_get_milestone_not_found(self, store: KernelStore) -> None:
        assert store.get_milestone("nonexistent") is None

    def test_create_milestone_with_all_fields(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        ms = store.create_milestone(
            team_id=team.team_id,
            title="Beta Release",
            description="Feature complete",
            status="active",
            dependency_ids=["ms_prev"],
            acceptance_criteria=["All tests pass", "Docs updated"],
        )
        assert ms.status == "active"
        assert ms.dependency_ids == ["ms_prev"]
        assert ms.acceptance_criteria == ["All tests pass", "Docs updated"]

    def test_list_milestones_by_team(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        store.create_milestone(team_id=team.team_id, title="MS 1")
        store.create_milestone(team_id=team.team_id, title="MS 2")
        store.create_milestone(team_id=team.team_id, title="MS 3")

        milestones = store.list_milestones_by_team(team_id=team.team_id)
        assert len(milestones) == 3
        # Should be ordered by created_at ASC
        titles = [m.title for m in milestones]
        assert titles == ["MS 1", "MS 2", "MS 3"]

    def test_list_milestones_by_team_empty(self, store: KernelStore) -> None:
        milestones = store.list_milestones_by_team(team_id="nonexistent")
        assert milestones == []

    def test_update_milestone_status_to_active(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        ms = store.create_milestone(team_id=team.team_id, title="MS 1")
        assert ms.status == "pending"

        store.update_milestone_status(ms.milestone_id, "active")
        updated = store.get_milestone(ms.milestone_id)
        assert updated is not None
        assert updated.status == "active"
        assert updated.completed_at is None

    def test_update_milestone_status_to_completed(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        ms = store.create_milestone(team_id=team.team_id, title="MS 1")

        store.update_milestone_status(ms.milestone_id, "active")
        store.update_milestone_status(ms.milestone_id, "completed")
        updated = store.get_milestone(ms.milestone_id)
        assert updated is not None
        assert updated.status == "completed"
        assert updated.completed_at is not None
        assert updated.completed_at > 0

    def test_update_milestone_status_to_blocked(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        ms = store.create_milestone(team_id=team.team_id, title="MS 1")

        store.update_milestone_status(ms.milestone_id, "active")
        store.update_milestone_status(ms.milestone_id, "blocked")
        updated = store.get_milestone(ms.milestone_id)
        assert updated is not None
        assert updated.status == "blocked"
        assert updated.completed_at is None

    def test_update_milestone_status_to_failed(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        ms = store.create_milestone(team_id=team.team_id, title="MS 1")

        store.update_milestone_status(ms.milestone_id, "active")
        store.update_milestone_status(ms.milestone_id, MilestoneState.FAILED)
        updated = store.get_milestone(ms.milestone_id)
        assert updated is not None
        assert updated.status == "failed"
        assert updated.completed_at is None

    def test_update_milestone_status_to_skipped(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        ms = store.create_milestone(team_id=team.team_id, title="MS 1")

        store.update_milestone_status(ms.milestone_id, MilestoneState.SKIPPED)
        updated = store.get_milestone(ms.milestone_id)
        assert updated is not None
        assert updated.status == "skipped"
        assert updated.completed_at is None

    def test_completed_at_set_on_completion(self, store: KernelStore) -> None:
        """When a milestone is completed, completed_at should be set."""
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        ms = store.create_milestone(team_id=team.team_id, title="MS 1")

        store.update_milestone_status(ms.milestone_id, "active")
        store.update_milestone_status(ms.milestone_id, "completed")
        completed = store.get_milestone(ms.milestone_id)
        assert completed is not None
        assert completed.completed_at is not None
        assert completed.completed_at > 0


# ------------------------------------------------------------------
# Event emission tests
# ------------------------------------------------------------------


class TestTeamEvents:
    def test_team_created_event(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        events = store.list_events(event_type="team.created", limit=10)
        assert len(events) >= 1
        event = events[0]
        assert event["entity_type"] == "team"
        assert event["entity_id"] == team.team_id
        assert event["payload"]["program_id"] == "prog_1"

    def test_milestone_created_event(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        ms = store.create_milestone(team_id=team.team_id, title="MVP")
        events = store.list_events(event_type="milestone.created", limit=10)
        assert len(events) >= 1
        event = events[0]
        assert event["entity_type"] == "milestone"
        assert event["entity_id"] == ms.milestone_id
        assert event["payload"]["team_id"] == team.team_id

    def test_milestone_status_change_event(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        ms = store.create_milestone(team_id=team.team_id, title="MVP")
        store.update_milestone_status(ms.milestone_id, "active")
        store.update_milestone_status(ms.milestone_id, "completed")
        events = store.list_events(event_type="milestone.completed", limit=10)
        assert len(events) >= 1
        event = events[0]
        assert event["payload"]["status"] == "completed"
        assert event["payload"]["completed_at"] is not None

    def test_milestone_failed_event(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        ms = store.create_milestone(team_id=team.team_id, title="MVP")
        store.update_milestone_status(ms.milestone_id, "active")
        store.update_milestone_status(ms.milestone_id, MilestoneState.FAILED)
        events = store.list_events(event_type="milestone.failed", limit=10)
        assert len(events) >= 1
        event = events[0]
        assert event["payload"]["status"] == "failed"

    def test_team_status_change_event(self, store: KernelStore) -> None:
        team = store.create_team(program_id="prog_1", title="Alpha", workspace_id="ws_1")
        store.update_team_status(team.team_id, TeamState.FAILED)
        events = store.list_events(event_type="team.failed", limit=10)
        assert len(events) >= 1
        event = events[0]
        assert event["entity_type"] == "team"
        assert event["entity_id"] == team.team_id
        assert event["payload"]["status"] == "failed"
