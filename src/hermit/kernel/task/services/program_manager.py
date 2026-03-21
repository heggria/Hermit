"""ProgramManager — compiles high-level goals into Programs with Teams and Milestones.

This is the Control Plane component that transforms human intent into structured,
governable work graphs.  It does NOT execute — it only creates the organisational
structure that the execution layer (TaskController, Governor, Workers) will act on.

Hierarchy:  Human Prompt -> Program -> Milestone Graph -> Role Assembly -> Attempts

Spec-required generators (prompt leverage):
  - Task Generator:       goal + graph state  -> new TaskContractPackets
  - Follow-up Generator:  failed verification -> retry/replan/mitigation tasks
  - Background Work Selector: idle capacity   -> next-best tasks
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any

import structlog

from hermit.kernel.execution.controller.supervisor_protocol import (
    TaskContractPacket,
    VerdictPacket,
    create_task_contract,
)
from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.program import (
    ACTIVE_PROGRAM_STATES,
    PROGRAM_STATE_TRANSITIONS,
    TERMINAL_PROGRAM_STATES,
    ProgramRecord,
    ProgramState,
)
from hermit.kernel.task.models.team import (
    MilestoneRecord,
    MilestoneState,
    RoleSlotSpec,
    TeamRecord,
    TeamState,
)

_log = structlog.get_logger()

# Use canonical transitions from the model — includes 'blocked' state.
_PROGRAM_TRANSITIONS: dict[str, frozenset[str]] = {
    str(k): frozenset(str(v) for v in vs) for k, vs in PROGRAM_STATE_TRANSITIONS.items()
}

# Risk-band ordering for background work prioritisation (lower index = safer).
_RISK_BAND_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# ---------------------------------------------------------------------------
# Data containers for generator outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FollowUpTask:
    """A follow-up task generated from a failed verification."""

    action: str  # one of: retry, replan, mitigate, escalate
    contract: TaskContractPacket
    reason: str
    source_verdict: VerdictPacket


@dataclass(frozen=True)
class BackgroundWorkItem:
    """A candidate task selected by the background work selector."""

    contract: TaskContractPacket
    score: float  # 0.0–1.0; higher = better candidate
    rationale: str


@dataclass(frozen=True)
class CompilationResult:
    """Result of a full prompt-to-structure compilation.

    Contains the program, teams, milestones, and generated task contracts
    produced from a single high-level prompt.
    """

    program: ProgramRecord
    teams: list[TeamRecord] = field(default_factory=list)
    milestones: list[MilestoneRecord] = field(default_factory=list)
    task_contracts: list[TaskContractPacket] = field(default_factory=list)


class ProgramManagerError(RuntimeError):
    """Raised when a ProgramManager operation fails."""


class ProgramManager:
    """Compiles high-level goals into Programs with Teams and Milestones.

    This is the Control Plane component that transforms human intent into
    structured, governable work graphs.  It does NOT execute — it only
    creates the organisational structure.

    Beyond basic compilation, provides three spec-mandated generators for
    prompt leverage:

    * :meth:`generate_tasks` — Task Generator
    * :meth:`generate_followups` — Follow-up Generator
    * :meth:`select_background_work` — Background Work Selector
    """

    def __init__(self, store: KernelStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # Program compilation
    # ------------------------------------------------------------------

    def compile_program(
        self,
        *,
        goal: str,
        title: str | None = None,
        priority: str = "normal",
        budget_limits: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProgramRecord:
        """Compile a high-level goal into a Program.

        The program is created in ``draft`` state.  Call :meth:`activate_program`
        once teams and milestones have been attached.

        Returns the created :class:`ProgramRecord`.
        """
        resolved_title = (title or goal.strip()[:120] or "Untitled program").strip()
        program = self.store.create_program(
            title=resolved_title,
            goal=goal,
            priority=priority,
            budget_limits=budget_limits,
            metadata=metadata,
        )
        _log.info(
            "program.compiled",
            program_id=program.program_id,
            title=resolved_title,
            priority=priority,
        )
        return program

    # ------------------------------------------------------------------
    # Team management
    # ------------------------------------------------------------------

    def add_team(
        self,
        *,
        program_id: str,
        title: str,
        workspace_id: str | None = None,
        role_assembly: dict[str, RoleSlotSpec | Any] | None = None,
        context_boundary: list[str] | None = None,
    ) -> TeamRecord:
        """Add a Team to an existing Program.

        If *workspace_id* is not provided, a default workspace id derived from
        the program id is used.  The team is created in ``active`` state.

        Raises :class:`ProgramManagerError` if the program does not exist or
        is in a terminal state.
        """
        program = self._get_program_or_raise(program_id)
        if program.status in TERMINAL_PROGRAM_STATES:
            raise ProgramManagerError(
                f"Cannot add team to program {program_id} in terminal state '{program.status}'"
            )
        resolved_workspace = workspace_id or f"ws_{program_id}"
        team = self.store.create_team(
            program_id=program_id,
            title=title,
            workspace_id=resolved_workspace,
            status=TeamState.ACTIVE,
            role_assembly=role_assembly,
            context_boundary=context_boundary,
        )
        _log.info(
            "program.team_added",
            program_id=program_id,
            team_id=team.team_id,
            title=title,
            workspace_id=resolved_workspace,
        )
        return team

    # ------------------------------------------------------------------
    # Milestone management
    # ------------------------------------------------------------------

    def add_milestone(
        self,
        *,
        team_id: str,
        title: str,
        description: str = "",
        dependency_ids: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> MilestoneRecord:
        """Add a Milestone to an existing Team.

        The milestone is created in ``pending`` state.  When all of its
        *dependency_ids* are completed, the scheduler should activate it.

        Also registers the milestone with the parent program via
        :meth:`store.add_milestone_to_program`.

        Raises :class:`ProgramManagerError` if the team does not exist.
        """
        team = self._get_team_or_raise(team_id)
        normalized_deps = list(dependency_ids or [])
        self._validate_milestone_dependencies(team_id, normalized_deps)
        milestone = self.store.create_milestone(
            team_id=team_id,
            title=title,
            description=description,
            status=MilestoneState.PENDING,
            dependency_ids=normalized_deps,
            acceptance_criteria=acceptance_criteria,
        )
        # Register milestone with the parent program.
        self.store.add_milestone_to_program(team.program_id, milestone.milestone_id)
        _log.info(
            "program.milestone_added",
            team_id=team_id,
            milestone_id=milestone.milestone_id,
            title=title,
            dependency_count=len(normalized_deps),
        )
        return milestone

    # ------------------------------------------------------------------
    # Program lifecycle
    # ------------------------------------------------------------------

    def activate_program(self, program_id: str) -> None:
        """Transition program from draft to active.

        Raises :class:`ProgramManagerError` if the transition is invalid.
        """
        self._transition_program(program_id, ProgramState.active)

    def pause_program(self, program_id: str) -> None:
        """Pause an active program.

        Raises :class:`ProgramManagerError` if the transition is invalid.
        """
        self._transition_program(program_id, ProgramState.paused)

    def resume_program(self, program_id: str) -> None:
        """Resume a paused program.

        Only valid when the program is in ``paused`` state.
        Raises :class:`ProgramManagerError` if the program is not paused.
        """
        program = self._get_program_or_raise(program_id)
        if program.status != ProgramState.paused:
            raise ProgramManagerError(
                f"Cannot resume program {program_id}: current state is "
                f"'{program.status}', expected 'paused'"
            )
        self._transition_program(program_id, ProgramState.active)

    def complete_program(self, program_id: str) -> None:
        """Mark program as completed.

        Raises :class:`ProgramManagerError` if the transition is invalid.
        """
        self._transition_program(program_id, ProgramState.completed)

    def fail_program(self, program_id: str) -> None:
        """Mark program as failed.

        Raises :class:`ProgramManagerError` if the transition is invalid.
        """
        self._transition_program(program_id, ProgramState.failed)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_program_with_teams(self, program_id: str) -> dict[str, Any]:
        """Get full program structure including teams and milestones.

        Returns a dict with the program record and nested team/milestone data.
        Raises :class:`ProgramManagerError` if the program does not exist.
        """
        program = self._get_program_or_raise(program_id)
        # Single JOIN query replaces N+1 pattern (1 query for teams + N for milestones).
        teams_with_milestones = self.store.list_teams_with_milestones(program_id=program_id)
        team_entries: list[dict[str, Any]] = []
        for _team_id, (team, milestones) in teams_with_milestones.items():
            milestone_entries = [
                {
                    "milestone_id": ms.milestone_id,
                    "title": ms.title,
                    "description": ms.description,
                    "status": ms.status,
                    "dependency_ids": list(ms.dependency_ids),
                    "acceptance_criteria": list(ms.acceptance_criteria),
                    "created_at": ms.created_at,
                    "completed_at": ms.completed_at,
                }
                for ms in milestones
            ]
            team_entries.append(
                {
                    "team_id": team.team_id,
                    "title": team.title,
                    "workspace_id": team.workspace_id,
                    "status": team.status,
                    "role_assembly": {
                        k: {"role": v.role, "count": v.count, "config": v.config}
                        for k, v in team.role_assembly.items()
                    },
                    "context_boundary": list(team.context_boundary),
                    "created_at": team.created_at,
                    "metadata": dict(team.metadata),
                    "milestones": milestone_entries,
                }
            )
        return {
            "program_id": program.program_id,
            "title": program.title,
            "goal": program.goal,
            "status": program.status,
            "description": program.description,
            "priority": program.priority,
            "budget_limits": dict(program.budget_limits),
            "milestone_ids": list(program.milestone_ids),
            "metadata": dict(program.metadata),
            "created_at": program.created_at,
            "updated_at": program.updated_at,
            "teams": team_entries,
        }

    def list_active_programs(self) -> list[ProgramRecord]:
        """List all programs in active (non-terminal) states."""
        results: list[ProgramRecord] = []
        for state in ACTIVE_PROGRAM_STATES:
            results.extend(self.store.list_programs(status=state))
        # Sort by created_at descending for a consistent ordering.
        results.sort(key=lambda p: p.created_at, reverse=True)
        return results

    # ------------------------------------------------------------------
    # Prompt-leverage compilation
    # ------------------------------------------------------------------

    def compile_program_with_structure(
        self,
        *,
        goal: str,
        title: str | None = None,
        priority: str = "normal",
        budget_limits: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        team_specs: list[dict[str, Any]] | None = None,
    ) -> CompilationResult:
        """Full compilation chain: Prompt -> Program -> Teams -> Milestones -> Contracts.

        This implements the spec's "prompt leverage" concept: a single high-level
        goal is compiled into a complete organisational structure with teams,
        milestones, and initial task contracts -- maximising downstream work from
        one human prompt.

        *team_specs* is a list of dicts, each describing a team to create::

            {
                "title": "Backend Team",
                "workspace_id": "ws_backend",       # optional
                "role_assembly": {"lead": "arch"},   # optional
                "context_boundary": ["src/api/"],    # optional
                "milestones": [                      # optional
                    {
                        "title": "API Design",
                        "description": "...",
                        "acceptance_criteria": ["OpenAPI spec reviewed"],
                        "dependency_titles": [],     # refs by title within team
                    },
                ],
            }

        Returns a :class:`CompilationResult` containing all created records and
        generated task contracts.
        """
        program = self.compile_program(
            goal=goal,
            title=title,
            priority=priority,
            budget_limits=budget_limits,
            metadata=metadata,
        )
        created_teams: list[TeamRecord] = []
        created_milestones: list[MilestoneRecord] = []

        for team_spec in team_specs or []:
            team = self.add_team(
                program_id=program.program_id,
                title=team_spec["title"],
                workspace_id=team_spec.get("workspace_id"),
                role_assembly=team_spec.get("role_assembly"),
                context_boundary=team_spec.get("context_boundary"),
            )
            created_teams.append(team)

            # Track milestone titles -> ids for dependency resolution within team.
            title_to_id: dict[str, str] = {}
            for ms_spec in team_spec.get("milestones", []):
                dep_titles: list[str] = ms_spec.get("dependency_titles", [])
                dep_ids = [title_to_id[dt] for dt in dep_titles if dt in title_to_id]
                ms = self.add_milestone(
                    team_id=team.team_id,
                    title=ms_spec["title"],
                    description=ms_spec.get("description", ""),
                    dependency_ids=dep_ids or None,
                    acceptance_criteria=ms_spec.get("acceptance_criteria"),
                )
                title_to_id[ms.title] = ms.milestone_id
                created_milestones.append(ms)

        # Generate initial task contracts from the compiled structure.
        contracts = self.generate_tasks(program.program_id)

        _log.info(
            "program.compiled_with_structure",
            program_id=program.program_id,
            teams=len(created_teams),
            milestones=len(created_milestones),
            contracts=len(contracts),
        )
        return CompilationResult(
            program=program,
            teams=created_teams,
            milestones=created_milestones,
            task_contracts=contracts,
        )

    # ------------------------------------------------------------------
    # Task Generator
    # ------------------------------------------------------------------

    def generate_tasks(self, program_id: str) -> list[TaskContractPacket]:
        """Task Generator: goal + graph state -> new TaskContractPackets.

        Examines the program's milestone graph and produces a
        :class:`TaskContractPacket` for each milestone that is ready for work
        (``pending`` state with all dependencies completed, or no dependencies).

        This is the primary mechanism for prompt leverage -- turning structure
        into actionable work items.
        """
        program = self._get_program_or_raise(program_id)
        if program.status in TERMINAL_PROGRAM_STATES:
            _log.info("program.generate_tasks.skipped_terminal", program_id=program_id)
            return []

        # Single JOIN query replaces N+1 pattern (1 query for teams + N for milestones).
        teams_with_milestones = self.store.list_teams_with_milestones(program_id=program_id)
        contracts: list[TaskContractPacket] = []

        for _team_id, (team, milestones) in teams_with_milestones.items():
            # Build completed set for dependency checking.
            completed_ids = frozenset(
                ms.milestone_id for ms in milestones if ms.status == MilestoneState.COMPLETED
            )

            for ms in milestones:
                if ms.status != MilestoneState.PENDING:
                    continue
                # A milestone is ready when all its dependencies are completed.
                deps_met = all(dep_id in completed_ids for dep_id in ms.dependency_ids)
                if not deps_met:
                    continue

                task_id = self.store.generate_id("task")
                contract = create_task_contract(
                    task_id=task_id,
                    goal=ms.title,
                    scope={
                        "program_id": program_id,
                        "team_id": team.team_id,
                        "milestone_id": ms.milestone_id,
                        "workspace_id": team.workspace_id,
                        "context_boundary": list(team.context_boundary),
                    },
                    acceptance_criteria=list(ms.acceptance_criteria),
                    dependencies=list(ms.dependency_ids),
                    risk_band="medium",
                )
                contracts.append(contract)

        _log.info(
            "program.tasks_generated",
            program_id=program_id,
            count=len(contracts),
        )
        return contracts

    # ------------------------------------------------------------------
    # Follow-up Generator
    # ------------------------------------------------------------------

    def generate_followups(
        self,
        *,
        program_id: str,
        verdict: VerdictPacket,
    ) -> list[FollowUpTask]:
        """Follow-up Generator: failed verification -> retry/replan/mitigation tasks.

        Examines a :class:`VerdictPacket` and produces follow-up
        :class:`FollowUpTask` entries based on the verdict outcome:

        * ``rejected`` with no issues -> ``retry`` with same acceptance criteria
        * ``rejected`` with issues -> ``mitigate`` per issue
        * ``blocked`` -> ``escalate``
        * ``accepted_with_followups`` -> ``replan`` per recommended action
        """
        program = self._get_program_or_raise(program_id)
        if program.status in TERMINAL_PROGRAM_STATES:
            return []

        followups: list[FollowUpTask] = []

        if verdict.verdict == "rejected":
            if verdict.issues:
                for issue in verdict.issues:
                    task_id = self.store.generate_id("task")
                    issue_summary = str(issue.get("description", issue.get("title", "fix issue")))
                    contract = create_task_contract(
                        task_id=task_id,
                        goal=f"Mitigate: {issue_summary}",
                        scope={
                            "program_id": program_id,
                            "source_task_id": verdict.task_id,
                        },
                        constraints=[f"Address issue: {issue_summary}"],
                        acceptance_criteria=[
                            k for k, v in verdict.acceptance_check.items() if not v
                        ],
                        risk_band="medium",
                    )
                    followups.append(
                        FollowUpTask(
                            action="mitigate",
                            contract=contract,
                            reason=issue_summary,
                            source_verdict=verdict,
                        )
                    )
            else:
                task_id = self.store.generate_id("task")
                failed_criteria = [k for k, v in verdict.acceptance_check.items() if not v]
                contract = create_task_contract(
                    task_id=task_id,
                    goal=f"Retry: {verdict.task_id}",
                    scope={
                        "program_id": program_id,
                        "source_task_id": verdict.task_id,
                    },
                    acceptance_criteria=failed_criteria,
                    risk_band="medium",
                )
                followups.append(
                    FollowUpTask(
                        action="retry",
                        contract=contract,
                        reason="Verification rejected without specific issues",
                        source_verdict=verdict,
                    )
                )

        elif verdict.verdict == "blocked":
            task_id = self.store.generate_id("task")
            contract = create_task_contract(
                task_id=task_id,
                goal=f"Escalate blocked: {verdict.task_id}",
                scope={
                    "program_id": program_id,
                    "source_task_id": verdict.task_id,
                },
                risk_band="high",
            )
            followups.append(
                FollowUpTask(
                    action="escalate",
                    contract=contract,
                    reason=verdict.recommended_next_action or "Task is blocked",
                    source_verdict=verdict,
                )
            )

        elif verdict.verdict == "accepted_with_followups":
            recommended = verdict.recommended_next_action
            if recommended:
                task_id = self.store.generate_id("task")
                contract = create_task_contract(
                    task_id=task_id,
                    goal=f"Replan: {recommended}",
                    scope={
                        "program_id": program_id,
                        "source_task_id": verdict.task_id,
                    },
                    risk_band="low",
                )
                followups.append(
                    FollowUpTask(
                        action="replan",
                        contract=contract,
                        reason=recommended,
                        source_verdict=verdict,
                    )
                )

        _log.info(
            "program.followups_generated",
            program_id=program_id,
            source_task=verdict.task_id,
            verdict=verdict.verdict,
            count=len(followups),
        )
        return followups

    # ------------------------------------------------------------------
    # Background Work Selector
    # ------------------------------------------------------------------

    def select_background_work(
        self,
        *,
        max_items: int = 5,
        allowed_risk_bands: frozenset[str] | None = None,
    ) -> list[BackgroundWorkItem]:
        """Background Work Selector: idle capacity -> next-best tasks.

        Scans all active programs for ready milestones and scores them
        to select the best candidates for background execution during
        idle capacity.

        Scoring criteria (all contribute to a 0.0-1.0 score):
        * Lower risk band -> higher score
        * Higher program priority -> higher score
        * Milestones with no unmet dependencies -> higher score
        * Programs in ``active`` state preferred over ``draft``

        *allowed_risk_bands* restricts candidates to specific risk levels;
        defaults to ``{"low", "medium"}`` for safe background execution.
        """
        safe_bands = allowed_risk_bands or frozenset({"low", "medium"})
        active_programs = self.list_active_programs()
        candidates: list[BackgroundWorkItem] = []

        priority_scores: dict[str, float] = {
            "critical": 1.0,
            "high": 0.8,
            "normal": 0.5,
            "low": 0.3,
        }

        for program in active_programs:
            if program.status in TERMINAL_PROGRAM_STATES:
                continue

            state_bonus = 0.1 if program.status == ProgramState.active else 0.0
            prio_score = priority_scores.get(program.priority, 0.5)

            # Single JOIN query replaces N+1 pattern per program.
            teams_with_milestones = self.store.list_teams_with_milestones(
                program_id=program.program_id
            )

            for _team_id, (team, milestones) in teams_with_milestones.items():
                if team.status != TeamState.ACTIVE:
                    continue

                completed_ids = frozenset(
                    ms.milestone_id for ms in milestones if ms.status == MilestoneState.COMPLETED
                )

                for ms in milestones:
                    if ms.status != MilestoneState.PENDING:
                        continue
                    deps_met = all(dep_id in completed_ids for dep_id in ms.dependency_ids)
                    if not deps_met:
                        continue

                    # Background work defaults to "low" risk -- safe for offline.
                    risk_band = "low"
                    if risk_band not in safe_bands:
                        continue

                    risk_score = 1.0 - (_RISK_BAND_ORDER.get(risk_band, 1) / 3.0)
                    dep_score = 1.0 if not ms.dependency_ids else 0.7
                    score = (
                        prio_score * 0.4 + risk_score * 0.3 + dep_score * 0.2 + state_bonus * 0.1
                    )
                    # Clamp to [0.0, 1.0]
                    score = max(0.0, min(1.0, score))

                    task_id = self.store.generate_id("task")
                    contract = create_task_contract(
                        task_id=task_id,
                        goal=ms.title,
                        scope={
                            "program_id": program.program_id,
                            "team_id": team.team_id,
                            "milestone_id": ms.milestone_id,
                            "workspace_id": team.workspace_id,
                        },
                        acceptance_criteria=list(ms.acceptance_criteria),
                        dependencies=list(ms.dependency_ids),
                        risk_band=risk_band,
                    )
                    candidates.append(
                        BackgroundWorkItem(
                            contract=contract,
                            score=score,
                            rationale=(
                                f"Program '{program.title}' "
                                f"(priority={program.priority}), "
                                f"milestone '{ms.title}', risk={risk_band}"
                            ),
                        )
                    )

        # Select top N by score without mutating the candidates list.
        result = heapq.nlargest(max_items, candidates, key=lambda item: item.score)
        _log.info(
            "program.background_work_selected",
            candidates_total=len(candidates),
            selected=len(result),
            max_items=max_items,
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_program_or_raise(self, program_id: str) -> ProgramRecord:
        """Fetch a program by id, raising :class:`ProgramManagerError` if absent."""
        program = self.store.get_program(program_id)
        if program is None:
            raise ProgramManagerError(f"Program not found: {program_id}")
        return program

    def _get_team_or_raise(self, team_id: str) -> TeamRecord:
        """Fetch a team by id, raising :class:`ProgramManagerError` if absent."""
        team = self.store.get_team(team_id)
        if team is None:
            raise ProgramManagerError(f"Team not found: {team_id}")
        return team

    def _transition_program(self, program_id: str, target: str) -> None:
        """Validate and execute a program state transition."""
        program = self._get_program_or_raise(program_id)
        current = program.status
        allowed = _PROGRAM_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise ProgramManagerError(
                f"Invalid program transition: '{current}' -> '{target}' "
                f"(allowed: {sorted(allowed) or 'none'})"
            )
        self.store.update_program_status(program_id, target)
        _log.info(
            "program.state_transition",
            program_id=program_id,
            previous=current,
            target=target,
        )

    def _validate_milestone_dependencies(self, team_id: str, dependency_ids: list[str]) -> None:
        """Check that all dependency milestone ids exist.

        Raises :class:`ProgramManagerError` if any dependency is unknown.
        """
        if not dependency_ids:
            return
        for dep_id in dependency_ids:
            milestone = self.store.get_milestone(dep_id)
            if milestone is None:
                raise ProgramManagerError(f"Milestone dependency not found: {dep_id}")


__all__ = [
    "BackgroundWorkItem",
    "CompilationResult",
    "FollowUpTask",
    "ProgramManager",
    "ProgramManagerError",
]
