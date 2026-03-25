"""Integration test: ProgramManager prompt-leverage compilation chain.

Exercises the full chain:
  compile_program_with_structure → generate_tasks → generate_followups
  → select_background_work → full lifecycle to completion.

Uses a real KernelStore (SQLite-backed) for each test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.execution.controller.supervisor_protocol import (
    TaskContractPacket,
    create_verdict,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.program import ProgramState
from hermit.kernel.task.models.team import MilestoneState
from hermit.kernel.task.services.program_manager import (
    BackgroundWorkItem,
    CompilationResult,
    FollowUpTask,
    ProgramManager,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "prompt_leverage.db")


@pytest.fixture
def pm(store: KernelStore) -> ProgramManager:
    return ProgramManager(store)


def _two_team_specs() -> list[dict]:
    """Two teams, each with milestones including dependency chains."""
    return [
        {
            "title": "Backend Team",
            "workspace_id": "ws_backend",
            "context_boundary": ["src/api/"],
            "milestones": [
                {
                    "title": "API Design",
                    "description": "Design REST endpoints",
                    "acceptance_criteria": ["OpenAPI spec reviewed", "Schema validated"],
                },
                {
                    "title": "API Implementation",
                    "description": "Implement the endpoints",
                    "dependency_titles": ["API Design"],
                    "acceptance_criteria": ["All endpoints functional", "Tests pass"],
                },
            ],
        },
        {
            "title": "Frontend Team",
            "workspace_id": "ws_frontend",
            "context_boundary": ["src/ui/"],
            "milestones": [
                {
                    "title": "UI Mockup",
                    "description": "Create wireframes",
                    "acceptance_criteria": ["Design approved"],
                },
                {
                    "title": "UI Build",
                    "description": "Build React components",
                    "dependency_titles": ["UI Mockup"],
                    "acceptance_criteria": ["Components render", "Accessibility audit"],
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# 1. compile_program_with_structure
# ---------------------------------------------------------------------------


class TestCompileProgramWithStructure:
    """Verify full prompt-to-structure compilation creates all records."""

    def test_creates_program_teams_milestones_and_initial_contracts(
        self, pm: ProgramManager, store: KernelStore
    ) -> None:
        result = pm.compile_program_with_structure(
            goal="Build a full-stack application",
            title="Full-Stack App v1",
            priority="high",
            budget_limits={"tokens": 200_000, "cost_usd": 10.0},
            metadata={"requested_by": "integration_test"},
            team_specs=_two_team_specs(),
        )

        # --- Program ---
        assert isinstance(result, CompilationResult)
        assert result.program.goal == "Build a full-stack application"
        assert result.program.title == "Full-Stack App v1"
        assert result.program.priority == "high"
        assert result.program.status == ProgramState.active
        assert result.program.budget_limits == {"tokens": 200_000, "cost_usd": 10.0}
        assert result.program.metadata == {"requested_by": "integration_test"}

        # --- Teams ---
        assert len(result.teams) == 2
        team_titles = {t.title for t in result.teams}
        assert team_titles == {"Backend Team", "Frontend Team"}

        backend_team = next(t for t in result.teams if t.title == "Backend Team")
        assert backend_team.workspace_id == "ws_backend"
        assert backend_team.context_boundary == ["src/api/"]

        frontend_team = next(t for t in result.teams if t.title == "Frontend Team")
        assert frontend_team.workspace_id == "ws_frontend"
        assert frontend_team.context_boundary == ["src/ui/"]

        # --- Milestones ---
        assert len(result.milestones) == 4
        ms_titles = [ms.title for ms in result.milestones]
        assert "API Design" in ms_titles
        assert "API Implementation" in ms_titles
        assert "UI Mockup" in ms_titles
        assert "UI Build" in ms_titles

        # Dependency wiring: API Implementation depends on API Design
        api_design = next(ms for ms in result.milestones if ms.title == "API Design")
        api_impl = next(ms for ms in result.milestones if ms.title == "API Implementation")
        assert api_design.milestone_id in api_impl.dependency_ids

        # UI Build depends on UI Mockup
        ui_mockup = next(ms for ms in result.milestones if ms.title == "UI Mockup")
        ui_build = next(ms for ms in result.milestones if ms.title == "UI Build")
        assert ui_mockup.milestone_id in ui_build.dependency_ids

        # All milestones start as pending
        assert all(ms.status == MilestoneState.PENDING for ms in result.milestones)

        # --- Initial Task Contracts ---
        # Only milestones with no deps (API Design, UI Mockup) should produce contracts
        assert len(result.task_contracts) == 2
        contract_goals = {c.goal for c in result.task_contracts}
        assert contract_goals == {"API Design", "UI Mockup"}

        # Contracts have correct scope
        for contract in result.task_contracts:
            assert isinstance(contract, TaskContractPacket)
            assert contract.scope["program_id"] == result.program.program_id
            assert "team_id" in contract.scope
            assert "workspace_id" in contract.scope

        # --- Store consistency ---
        persisted_program = store.get_program(result.program.program_id)
        assert persisted_program is not None
        assert len(persisted_program.milestone_ids) == 4

        # All milestones are registered with the program
        for ms in result.milestones:
            assert ms.milestone_id in persisted_program.milestone_ids


# ---------------------------------------------------------------------------
# 2. generate_tasks after milestone completion
# ---------------------------------------------------------------------------


class TestGenerateTasksAfterCompletion:
    """After compiling, activate, complete milestones, then generate_tasks for newly-ready ones."""

    def test_newly_ready_milestones_produce_contracts(
        self, pm: ProgramManager, store: KernelStore
    ) -> None:
        result = pm.compile_program_with_structure(
            goal="Task generation chain",
            team_specs=_two_team_specs(),
        )
        program_id = result.program.program_id

        # Program starts as active — no activation needed

        # Initially only API Design and UI Mockup are ready
        initial_contracts = pm.generate_tasks(program_id)
        initial_goals = {c.goal for c in initial_contracts}
        assert initial_goals == {"API Design", "UI Mockup"}

        # Complete "API Design" milestone (pending → active → completed)
        api_design = next(ms for ms in result.milestones if ms.title == "API Design")
        store.update_milestone_status(api_design.milestone_id, MilestoneState.ACTIVE)
        store.update_milestone_status(api_design.milestone_id, MilestoneState.COMPLETED)

        # Now generate_tasks should find "API Implementation" ready (its dep is done)
        # but NOT "UI Build" (UI Mockup still pending)
        new_contracts = pm.generate_tasks(program_id)
        new_goals = {c.goal for c in new_contracts}
        assert "API Implementation" in new_goals
        # UI Mockup is still pending, so its task is regenerated since it hasn't been marked active
        assert "UI Mockup" in new_goals
        assert "UI Build" not in new_goals

    def test_completing_all_first_tier_unlocks_all_second_tier(
        self, pm: ProgramManager, store: KernelStore
    ) -> None:
        result = pm.compile_program_with_structure(
            goal="Full unlock test",
            team_specs=_two_team_specs(),
        )
        program_id = result.program.program_id

        # Complete both root milestones
        api_design = next(ms for ms in result.milestones if ms.title == "API Design")
        ui_mockup = next(ms for ms in result.milestones if ms.title == "UI Mockup")
        store.update_milestone_status(api_design.milestone_id, MilestoneState.ACTIVE)
        store.update_milestone_status(api_design.milestone_id, MilestoneState.COMPLETED)
        store.update_milestone_status(ui_mockup.milestone_id, MilestoneState.ACTIVE)
        store.update_milestone_status(ui_mockup.milestone_id, MilestoneState.COMPLETED)

        contracts = pm.generate_tasks(program_id)
        goals = {c.goal for c in contracts}
        # Both second-tier milestones should now be ready
        assert "API Implementation" in goals
        assert "UI Build" in goals
        # Root milestones are completed, so they should NOT appear
        assert "API Design" not in goals
        assert "UI Mockup" not in goals


# ---------------------------------------------------------------------------
# 3. generate_followups
# ---------------------------------------------------------------------------


class TestGenerateFollowups:
    """Create a VerdictPacket with verdict=rejected + issues, verify FollowUpTask objects."""

    def test_rejected_with_issues_produces_mitigate_followups(self, pm: ProgramManager) -> None:
        result = pm.compile_program_with_structure(
            goal="Followup chain test",
            team_specs=[{"title": "Team A", "milestones": [{"title": "Task X"}]}],
        )
        verdict = create_verdict(
            task_id="task_original_001",
            verdict="rejected",
            acceptance_check={
                "tests_pass": False,
                "lint_clean": True,
                "coverage_80": False,
            },
            issues=[
                {"description": "Test suite has 3 failures in auth module"},
                {"description": "Coverage dropped to 65%"},
            ],
        )

        followups = pm.generate_followups(
            program_id=result.program.program_id,
            verdict=verdict,
        )

        assert len(followups) == 2
        for fu in followups:
            assert isinstance(fu, FollowUpTask)
            assert fu.action == "mitigate"
            assert fu.source_verdict is verdict
            assert fu.contract.scope["program_id"] == result.program.program_id
            assert fu.contract.scope["source_task_id"] == "task_original_001"

        # Check specific issue reasons
        reasons = [fu.reason for fu in followups]
        assert any("3 failures" in r for r in reasons)
        assert any("65%" in r for r in reasons)

        # Failed acceptance criteria propagated to mitigate contracts
        for fu in followups:
            assert "tests_pass" in fu.contract.acceptance_criteria
            assert "coverage_80" in fu.contract.acceptance_criteria
            # lint_clean passed, so it should NOT be in acceptance_criteria
            assert "lint_clean" not in fu.contract.acceptance_criteria

    def test_rejected_without_issues_produces_retry(self, pm: ProgramManager) -> None:
        result = pm.compile_program_with_structure(
            goal="Retry test",
            team_specs=[{"title": "Team A", "milestones": [{"title": "Task Y"}]}],
        )
        verdict = create_verdict(
            task_id="task_retry_001",
            verdict="rejected",
            acceptance_check={"tests_pass": False},
        )

        followups = pm.generate_followups(
            program_id=result.program.program_id,
            verdict=verdict,
        )

        assert len(followups) == 1
        assert followups[0].action == "retry"
        assert followups[0].contract.acceptance_criteria == ["tests_pass"]

    def test_blocked_produces_escalate(self, pm: ProgramManager) -> None:
        result = pm.compile_program_with_structure(
            goal="Escalation test",
            team_specs=[{"title": "Team A", "milestones": [{"title": "Task Z"}]}],
        )
        verdict = create_verdict(
            task_id="task_blocked_001",
            verdict="blocked",
            recommended_next_action="Needs API key from external vendor",
        )

        followups = pm.generate_followups(
            program_id=result.program.program_id,
            verdict=verdict,
        )

        assert len(followups) == 1
        assert followups[0].action == "escalate"
        assert followups[0].contract.risk_band == "high"
        assert followups[0].reason == "Needs API key from external vendor"


# ---------------------------------------------------------------------------
# 4. select_background_work
# ---------------------------------------------------------------------------


class TestSelectBackgroundWork:
    """Multiple active programs with ready milestones, verify risk_band filters and scoring."""

    def test_multi_program_background_selection(
        self, pm: ProgramManager, store: KernelStore
    ) -> None:
        # Create 3 programs with different priorities
        specs_simple = [{"title": "Team", "milestones": [{"title": "Work Item"}]}]

        r_high = pm.compile_program_with_structure(
            goal="High priority project",
            priority="high",
            team_specs=specs_simple,
        )

        r_normal = pm.compile_program_with_structure(  # noqa: F841
            goal="Normal priority project",
            priority="normal",
            team_specs=specs_simple,
        )

        r_low = pm.compile_program_with_structure(  # noqa: F841
            goal="Low priority project",
            priority="low",
            team_specs=specs_simple,
        )

        items = pm.select_background_work(max_items=10)
        assert len(items) == 3

        # All items are BackgroundWorkItem instances
        for item in items:
            assert isinstance(item, BackgroundWorkItem)
            assert 0.0 <= item.score <= 1.0
            assert item.contract.risk_band == "low"  # background work defaults to "low"

        # Items should be sorted by score descending (highest first)
        scores = [item.score for item in items]
        assert scores == sorted(scores, reverse=True)

        # High-priority program should score highest
        assert items[0].contract.scope["program_id"] == r_high.program.program_id

    def test_risk_band_filter_excludes_low(self, pm: ProgramManager) -> None:
        """When allowed_risk_bands excludes 'low', no background work should be returned
        because background work defaults to risk_band='low'."""
        pm.compile_program_with_structure(
            goal="Risk filter test",
            team_specs=[{"title": "Team", "milestones": [{"title": "Risky Work"}]}],
        )

        # Exclude "low" risk band — background work generates "low" risk, so nothing matches
        items = pm.select_background_work(allowed_risk_bands=frozenset({"medium", "high"}))
        assert items == []

    def test_risk_band_filter_includes_low(self, pm: ProgramManager) -> None:
        """When allowed_risk_bands includes 'low', background work is returned."""
        pm.compile_program_with_structure(
            goal="Include low test",
            team_specs=[{"title": "Team", "milestones": [{"title": "Safe Work"}]}],
        )

        items = pm.select_background_work(allowed_risk_bands=frozenset({"low"}))
        assert len(items) == 1
        assert items[0].contract.goal == "Safe Work"

    def test_blocked_milestones_excluded_from_background(
        self, pm: ProgramManager, store: KernelStore
    ) -> None:
        """Milestones with unmet dependencies should not appear in background work."""
        pm.compile_program_with_structure(
            goal="Blocked milestone bg test",
            team_specs=[
                {
                    "title": "Team",
                    "milestones": [
                        {"title": "Root Task"},
                        {"title": "Dependent Task", "dependency_titles": ["Root Task"]},
                    ],
                }
            ],
        )

        items = pm.select_background_work()
        goals = {item.contract.goal for item in items}
        assert "Root Task" in goals
        assert "Dependent Task" not in goals


# ---------------------------------------------------------------------------
# 5. Full chain: compile → activate → generate → execute → complete
# ---------------------------------------------------------------------------


class TestFullLifecycleChain:
    """End-to-end: compile → activate → generate tasks → mark milestones complete
    → generate more tasks → complete all → verify program can be completed."""

    def test_full_compilation_to_program_completion(
        self, pm: ProgramManager, store: KernelStore
    ) -> None:
        # --- Step 1: Compile ---
        result = pm.compile_program_with_structure(
            goal="End-to-end lifecycle test",
            title="E2E Program",
            priority="high",
            team_specs=_two_team_specs(),
        )
        program_id = result.program.program_id
        assert result.program.status == ProgramState.active
        assert len(result.milestones) == 4

        # --- Step 2: Verify active state ---
        program = store.get_program(program_id)
        assert program is not None
        assert program.status == ProgramState.active

        # --- Step 3: Generate initial tasks ---
        wave1 = pm.generate_tasks(program_id)
        wave1_goals = {c.goal for c in wave1}
        assert wave1_goals == {"API Design", "UI Mockup"}

        # --- Step 4: "Execute" wave 1 — mark root milestones complete ---
        api_design = next(ms for ms in result.milestones if ms.title == "API Design")
        ui_mockup = next(ms for ms in result.milestones if ms.title == "UI Mockup")
        store.update_milestone_status(api_design.milestone_id, MilestoneState.ACTIVE)
        store.update_milestone_status(api_design.milestone_id, MilestoneState.COMPLETED)
        store.update_milestone_status(ui_mockup.milestone_id, MilestoneState.ACTIVE)
        store.update_milestone_status(ui_mockup.milestone_id, MilestoneState.COMPLETED)

        # --- Step 5: Generate wave 2 tasks ---
        wave2 = pm.generate_tasks(program_id)
        wave2_goals = {c.goal for c in wave2}
        # Second-tier milestones should now be ready
        assert "API Implementation" in wave2_goals
        assert "UI Build" in wave2_goals
        # Root milestones are completed, should not reappear
        assert "API Design" not in wave2_goals
        assert "UI Mockup" not in wave2_goals

        # --- Step 6: "Execute" wave 2 — mark remaining milestones complete ---
        api_impl = next(ms for ms in result.milestones if ms.title == "API Implementation")
        ui_build = next(ms for ms in result.milestones if ms.title == "UI Build")
        store.update_milestone_status(api_impl.milestone_id, MilestoneState.ACTIVE)
        store.update_milestone_status(api_impl.milestone_id, MilestoneState.COMPLETED)
        store.update_milestone_status(ui_build.milestone_id, MilestoneState.ACTIVE)
        store.update_milestone_status(ui_build.milestone_id, MilestoneState.COMPLETED)

        # --- Step 7: No more tasks to generate ---
        wave3 = pm.generate_tasks(program_id)
        assert wave3 == []

        # --- Step 8: Complete the program ---
        pm.archive_program(program_id)
        final = store.get_program(program_id)
        assert final is not None
        assert final.status == ProgramState.archived

        # --- Step 9: Verify terminal state blocks further generation ---
        terminal_contracts = pm.generate_tasks(program_id)
        assert terminal_contracts == []

    def test_full_chain_with_followups_on_failure(
        self, pm: ProgramManager, store: KernelStore
    ) -> None:
        """Full chain where a milestone verification fails, generating followups,
        then eventually completing."""

        # Compile and activate
        result = pm.compile_program_with_structure(
            goal="Chain with failure recovery",
            team_specs=[
                {
                    "title": "Core Team",
                    "milestones": [
                        {
                            "title": "Implementation",
                            "acceptance_criteria": ["Tests pass", "No regressions"],
                        },
                        {
                            "title": "Release",
                            "dependency_titles": ["Implementation"],
                            "acceptance_criteria": ["Deployed successfully"],
                        },
                    ],
                },
            ],
        )
        program_id = result.program.program_id

        # Generate initial tasks — only Implementation is ready
        wave1 = pm.generate_tasks(program_id)
        assert len(wave1) == 1
        assert wave1[0].goal == "Implementation"

        # Simulate: Implementation task completed but verification REJECTED
        verdict = create_verdict(
            task_id=wave1[0].task_id,
            verdict="rejected",
            acceptance_check={"Tests pass": False, "No regressions": True},
            issues=[{"description": "Auth module tests failing"}],
        )

        followups = pm.generate_followups(program_id=program_id, verdict=verdict)
        assert len(followups) == 1
        assert followups[0].action == "mitigate"
        assert "Auth module" in followups[0].reason

        # The follow-up contract has the failed criteria
        assert "Tests pass" in followups[0].contract.acceptance_criteria

        # Now simulate: the fix is done, mark Implementation milestone as completed
        impl_ms = next(ms for ms in result.milestones if ms.title == "Implementation")
        store.update_milestone_status(impl_ms.milestone_id, MilestoneState.ACTIVE)
        store.update_milestone_status(impl_ms.milestone_id, MilestoneState.COMPLETED)

        # Release milestone should now be ready
        wave2 = pm.generate_tasks(program_id)
        assert len(wave2) == 1
        assert wave2[0].goal == "Release"

        # Complete Release
        release_ms = next(ms for ms in result.milestones if ms.title == "Release")
        store.update_milestone_status(release_ms.milestone_id, MilestoneState.ACTIVE)
        store.update_milestone_status(release_ms.milestone_id, MilestoneState.COMPLETED)

        # No more tasks
        wave3 = pm.generate_tasks(program_id)
        assert wave3 == []

        # Complete program
        pm.archive_program(program_id)
        final = store.get_program(program_id)
        assert final is not None
        assert final.status == ProgramState.archived

    def test_background_work_integrates_with_full_chain(
        self, pm: ProgramManager, store: KernelStore
    ) -> None:
        """Background work selector works across multiple programs during the chain."""

        # Create two programs
        r1 = pm.compile_program_with_structure(
            goal="Program Alpha",
            priority="high",
            team_specs=[
                {
                    "title": "Alpha Team",
                    "milestones": [
                        {"title": "Alpha Task 1"},
                        {"title": "Alpha Task 2", "dependency_titles": ["Alpha Task 1"]},
                    ],
                }
            ],
        )

        r2 = pm.compile_program_with_structure(  # noqa: F841
            goal="Program Beta",
            priority="low",
            team_specs=[
                {
                    "title": "Beta Team",
                    "milestones": [{"title": "Beta Task 1"}],
                }
            ],
        )

        # Background work should see both ready milestones
        bg_items = pm.select_background_work()
        bg_goals = {item.contract.goal for item in bg_items}
        assert "Alpha Task 1" in bg_goals
        assert "Beta Task 1" in bg_goals
        # Alpha Task 2 has unmet deps
        assert "Alpha Task 2" not in bg_goals

        # Higher priority program's item should score higher
        alpha_item = next(i for i in bg_items if i.contract.goal == "Alpha Task 1")
        beta_item = next(i for i in bg_items if i.contract.goal == "Beta Task 1")
        assert alpha_item.score > beta_item.score

        # Complete Alpha Task 1 -> Alpha Task 2 becomes available
        alpha_ms1 = next(ms for ms in r1.milestones if ms.title == "Alpha Task 1")
        store.update_milestone_status(alpha_ms1.milestone_id, MilestoneState.ACTIVE)
        store.update_milestone_status(alpha_ms1.milestone_id, MilestoneState.COMPLETED)

        bg_items2 = pm.select_background_work()
        bg_goals2 = {item.contract.goal for item in bg_items2}
        assert "Alpha Task 2" in bg_goals2
        assert "Beta Task 1" in bg_goals2
        # Alpha Task 1 completed, should not appear
        assert "Alpha Task 1" not in bg_goals2
