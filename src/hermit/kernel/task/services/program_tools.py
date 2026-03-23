"""MCP-ready tool service for Program, Team, and Status Projection operations.

Bridges MCP tool calls to ProgramStoreMixin, KernelTeamStoreMixin, and
StatusProjectionService.  Each method returns a plain dict suitable for
JSON serialization in MCP tool responses.
"""

from __future__ import annotations

from typing import Any

import structlog

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.models.program import TERMINAL_PROGRAM_STATES, ProgramState
from hermit.kernel.task.projections.status import StatusProjectionService

_log = structlog.get_logger()

# Valid control actions and the program states they transition *from* → *to*.
_CONTROL_TRANSITIONS: dict[str, dict[str, str]] = {
    "pause": {
        ProgramState.active: ProgramState.paused,
    },
    "resume": {
        ProgramState.paused: ProgramState.active,
    },
    "activate": {
        ProgramState.draft: ProgramState.active,
    },
    "complete": {
        ProgramState.active: ProgramState.completed,
        ProgramState.paused: ProgramState.completed,
    },
}


class ProgramToolService:
    """Bridge between MCP tools and ProgramManager/StatusProjectionService.

    Each method returns a dict suitable for MCP tool response serialization.
    """

    def __init__(self, store: KernelStore) -> None:
        self.store = store
        self._projection = StatusProjectionService(store)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def create_program(
        self,
        *,
        goal: str,
        title: str | None = None,
        priority: str = "normal",
    ) -> dict[str, Any]:
        """Create a new program from a high-level goal."""
        if not goal:
            return {"error": "goal is required"}

        resolved_title = title if title else goal[:80]
        try:
            program = self.store.create_program(
                title=resolved_title,
                goal=goal,
                priority=priority,
            )
        except Exception as exc:
            _log.error("program_create_failed", error=str(exc))
            return {"error": str(exc)}

        _log.info(
            "program_created",
            program_id=program.program_id,
            title=program.title,
        )
        return {
            "program_id": program.program_id,
            "title": program.title,
            "goal": program.goal,
            "status": program.status,
            "priority": program.priority,
            "created_at": program.created_at,
        }

    def add_team_to_program(
        self,
        *,
        program_id: str,
        title: str,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a team to an existing program."""
        if not program_id:
            return {"error": "program_id is required"}
        if not title:
            return {"error": "title is required"}

        program = self.store.get_program(program_id)
        if program is None:
            return {"error": f"Program not found: {program_id}"}

        resolved_workspace = workspace_id if workspace_id else f"ws-{program_id}"
        try:
            team = self.store.create_team(
                program_id=program_id,
                title=title,
                workspace_id=resolved_workspace,
            )
        except Exception as exc:
            _log.error("team_create_failed", error=str(exc))
            return {"error": str(exc)}

        _log.info(
            "team_added",
            team_id=team.team_id,
            program_id=program_id,
        )
        return {
            "team_id": team.team_id,
            "program_id": team.program_id,
            "title": team.title,
            "workspace_id": team.workspace_id,
            "status": team.status,
            "created_at": team.created_at,
        }

    def add_milestone(
        self,
        *,
        team_id: str,
        title: str,
        acceptance_criteria: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add a milestone to a team."""
        if not team_id:
            return {"error": "team_id is required"}
        if not title:
            return {"error": "title is required"}

        team = self.store.get_team(team_id)
        if team is None:
            return {"error": f"Team not found: {team_id}"}

        try:
            milestone = self.store.create_milestone(
                team_id=team_id,
                title=title,
                acceptance_criteria=acceptance_criteria,
            )
        except Exception as exc:
            _log.error("milestone_create_failed", error=str(exc))
            return {"error": str(exc)}

        # Link milestone to the parent program.
        self.store.add_milestone_to_program(team.program_id, milestone.milestone_id)

        _log.info(
            "milestone_added",
            milestone_id=milestone.milestone_id,
            team_id=team_id,
        )
        return {
            "milestone_id": milestone.milestone_id,
            "team_id": milestone.team_id,
            "title": milestone.title,
            "status": milestone.status,
            "acceptance_criteria": milestone.acceptance_criteria,
            "created_at": milestone.created_at,
        }

    # ------------------------------------------------------------------
    # Read (projection) helpers
    # ------------------------------------------------------------------

    def get_program_status(self, *, program_id: str) -> dict[str, Any]:
        """Get program status projection (read path)."""
        if not program_id:
            return {"error": "program_id is required"}

        program = self.store.get_program(program_id)
        if program is None:
            return {"error": f"Program not found: {program_id}"}

        # Assemble projection using the task-based StatusProjectionService
        # if the program has an associated root task; otherwise build a
        # lightweight projection from the program record itself.
        try:
            projection = self._projection.get_program_status(program_id)
            return {
                "program_id": projection.program_id,
                "title": projection.title,
                "overall_state": projection.overall_state,
                "progress_pct": projection.progress_pct,
                "current_phase": projection.current_phase,
                "active_teams": projection.active_teams,
                "queued_tasks": projection.queued_tasks,
                "running_attempts": projection.running_attempts,
                "blocked_items": projection.blocked_items,
                "awaiting_human": projection.awaiting_human,
                "latest_summary": projection.latest_summary,
                "latest_risks": projection.latest_risks,
                "latest_benchmark_status": projection.latest_benchmark_status,
                "last_updated_at": projection.last_updated_at,
            }
        except KeyError:
            # No matching root task — fall back to direct program record.
            teams = self.store.list_teams_by_program(program_id=program_id)
            return {
                "program_id": program.program_id,
                "title": program.title,
                "overall_state": program.status,
                "progress_pct": 0.0,
                "current_phase": program.status,
                "active_teams": len(teams),
                "queued_tasks": 0,
                "running_attempts": 0,
                "blocked_items": 0,
                "awaiting_human": False,
                "latest_summary": program.goal[:300] if program.goal else "",
                "latest_risks": [],
                "latest_benchmark_status": "",
                "last_updated_at": program.updated_at,
            }

    def list_programs(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List programs with optional status filter."""
        clamped_limit = min(max(limit, 1), 100)
        try:
            programs = self.store.list_programs(
                status=status if status else None,
                limit=clamped_limit,
            )
        except Exception as exc:
            _log.error("programs_list_failed", error=str(exc))
            return {"error": str(exc)}

        items = [
            {
                "program_id": p.program_id,
                "title": p.title,
                "status": p.status,
                "priority": p.priority,
                "goal": p.goal[:120] if p.goal else "",
                "created_at": p.created_at,
                "updated_at": p.updated_at,
            }
            for p in programs
        ]
        return {"programs": items, "count": len(items)}

    def get_team_status(self, *, team_id: str) -> dict[str, Any]:
        """Get team status projection."""
        if not team_id:
            return {"error": "team_id is required"}

        team = self.store.get_team(team_id)
        if team is None:
            return {"error": f"Team not found: {team_id}"}

        try:
            projection = self._projection.get_team_status(team_id)
            result: dict[str, Any] = {
                "team_id": projection.team_id,
                "title": projection.title,
                "state": projection.state,
                "workspace": projection.workspace,
                "active_workers": projection.active_workers,
                "milestone_progress": projection.milestone_progress,
                "blockers": projection.blockers,
            }
        except KeyError:
            # No matching task — fall back to direct team record.
            milestones = self.store.list_milestones_by_team(team_id=team_id)
            done = sum(1 for m in milestones if m.status == "completed")
            result = {
                "team_id": team.team_id,
                "title": team.title,
                "state": team.status,
                "workspace": team.workspace_id,
                "active_workers": 0,
                "milestone_progress": f"{done}/{len(milestones)}",
                "blockers": [],
            }

        # Enrich with milestone details.
        milestones = self.store.list_milestones_by_team(team_id=team_id)
        result["milestones"] = [
            {
                "milestone_id": m.milestone_id,
                "title": m.title,
                "status": m.status,
            }
            for m in milestones
        ]
        return result

    def get_task_status(self, *, task_id: str) -> dict[str, Any]:
        """Get task status projection (read path, no side effects)."""
        if not task_id:
            return {"error": "task_id is required"}

        try:
            projection = self._projection.get_task_status(task_id)
            return {
                "task_id": projection.task_id,
                "title": projection.title,
                "state": projection.state,
                "goal": projection.goal,
                "priority": projection.priority,
                "parent_task_id": projection.parent_task_id,
                "total_steps": projection.total_steps,
                "completed_steps": projection.completed_steps,
                "running_steps": projection.running_steps,
                "blocked_steps": projection.blocked_steps,
                "failed_steps": projection.failed_steps,
                "pending_approvals": projection.pending_approvals,
                "latest_event": projection.latest_event,
                "blockers": projection.blockers,
                "last_updated_at": projection.last_updated_at,
            }
        except KeyError:
            return {"error": f"Task not found: {task_id}"}

    def get_attempt_status(self, *, step_attempt_id: str) -> dict[str, Any]:
        """Get attempt status projection (read path, no side effects)."""
        if not step_attempt_id:
            return {"error": "step_attempt_id is required"}

        try:
            projection = self._projection.get_attempt_status(step_attempt_id)
            return {
                "step_attempt_id": projection.step_attempt_id,
                "task_id": projection.task_id,
                "step_id": projection.step_id,
                "attempt_number": projection.attempt_number,
                "status": projection.status,
                "status_reason": projection.status_reason,
                "has_approval": projection.has_approval,
                "has_capability_grant": projection.has_capability_grant,
                "started_at": projection.started_at,
                "finished_at": projection.finished_at,
                "failure_reason": projection.failure_reason,
            }
        except KeyError:
            return {"error": f"Step attempt not found: {step_attempt_id}"}

    def get_program_summary_text(self, *, program_id: str) -> dict[str, Any]:
        """Get a formatted program summary string for IM / CLI display."""
        if not program_id:
            return {"error": "program_id is required"}

        program = self.store.get_program(program_id)
        if program is None:
            return {"error": f"Program not found: {program_id}"}

        try:
            projection = self._projection.get_program_status(program_id)
            text = StatusProjectionService.format_program_summary(projection)
            return {"summary": text}
        except KeyError:
            return {"summary": f"Program: {program.title}\nStatus: {program.status}"}

    # ------------------------------------------------------------------
    # Control commands
    # ------------------------------------------------------------------

    def control_program(
        self,
        *,
        program_id: str,
        action: str,
    ) -> dict[str, Any]:
        """Execute a control action: pause, resume, activate, complete."""
        if not program_id:
            return {"error": "program_id is required"}
        if not action:
            return {"error": "action is required"}

        transitions = _CONTROL_TRANSITIONS.get(action)
        if transitions is None:
            valid_actions = ", ".join(sorted(_CONTROL_TRANSITIONS))
            return {"error": f"Unknown action '{action}'. Valid: {valid_actions}"}

        program = self.store.get_program(program_id)
        if program is None:
            return {"error": f"Program not found: {program_id}"}

        current = program.status
        if current in TERMINAL_PROGRAM_STATES:
            return {
                "error": f"Program is in terminal state '{current}' and cannot be changed",
                "program_id": program_id,
                "status": current,
            }

        target = transitions.get(current)
        if target is None:
            return {
                "error": (
                    f"Cannot '{action}' a program in state '{current}'. "
                    f"Valid source states for '{action}': "
                    f"{', '.join(sorted(transitions))}"
                ),
                "program_id": program_id,
                "status": current,
            }

        try:
            self.store.update_program_status(
                program_id,
                target,
                payload={"action": action, "previous_status": current},
            )
        except Exception as exc:
            _log.error(
                "program_control_failed",
                program_id=program_id,
                action=action,
                error=str(exc),
            )
            return {"error": str(exc)}

        _log.info(
            "program_controlled",
            program_id=program_id,
            action=action,
            previous=current,
            new_status=target,
        )
        return {
            "program_id": program_id,
            "action": action,
            "previous_status": current,
            "new_status": target,
        }

    # ------------------------------------------------------------------
    # Approval queue
    # ------------------------------------------------------------------

    def get_approval_queue(self) -> dict[str, Any]:
        """Get pending approvals across all programs."""
        try:
            projection = self._projection.get_approval_queue()
            return {
                "pending_approvals": projection.pending_approvals,
                "total_count": projection.total_count,
                "high_priority_count": projection.high_priority_count,
            }
        except Exception as exc:
            _log.error("approval_queue_failed", error=str(exc))
            return {"error": str(exc)}
