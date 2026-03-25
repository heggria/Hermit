"""Team-aware task decomposition.

Converts a team's role_assembly and role_graph_edges into a StepNode DAG
that can be materialized by StepDAGBuilder.
"""

from __future__ import annotations

import structlog

from hermit.kernel.task.models.team import RoleSlotSpec, TeamRecord
from hermit.kernel.task.services.dag_builder import StepNode

_log = structlog.get_logger()

__all__ = ["TeamDecomposer", "decompose_team_to_steps"]

# Map team role names to step kinds recognized by pool_dispatch step_kind_to_role().
_ROLE_TO_STEP_KIND: dict[str, str] = {
    "researcher": "research",
    "planner": "plan",
    "executor": "execute",
    "coder": "code",
    "reviewer": "review",
    "verifier": "verify",
    "tester": "test",
    "benchmarker": "benchmark",
    "spec": "spec",
    "reconciler": "reconcile",
}

_DEFAULT_STEP_KIND = "execute"


class TeamDecomposer:
    """Decomposes a task goal into DAG steps based on team topology."""

    def decompose(
        self,
        *,
        team: TeamRecord,
        goal: str,
    ) -> list[StepNode]:
        """Convert team role_assembly + role_graph_edges into StepNodes.

        Each role in role_assembly becomes one step.  The role's ``count``
        controls the worker-pool slot limit (not the number of steps),
        consistent with the deliberation integration pattern.

        The ``role_graph_edges`` stored in team metadata define
        ``depends_on`` relationships between steps.
        """
        role_assembly = team.role_assembly
        edges: list[dict[str, str]] = team.metadata.get("role_graph_edges", [])

        if not role_assembly:
            _log.warning("team_decomposer.empty_role_assembly", team_id=team.team_id)
            return [StepNode(key="respond", kind="execute", title=goal)]

        # Build reverse adjacency: target -> [sources] for depends_on.
        reverse_adj: dict[str, list[str]] = {role: [] for role in role_assembly}
        for edge in edges:
            src = edge.get("source", "")
            tgt = edge.get("target", "")
            if src in role_assembly and tgt in role_assembly:
                reverse_adj.setdefault(tgt, []).append(src)

        nodes: list[StepNode] = []
        for role_name, slot_spec in role_assembly.items():
            step_kind = _ROLE_TO_STEP_KIND.get(
                slot_spec.role,
                _ROLE_TO_STEP_KIND.get(role_name, _DEFAULT_STEP_KIND),
            )
            depends_on = reverse_adj.get(role_name, [])
            title = self._build_step_title(role_name, goal, slot_spec)
            nodes.append(
                StepNode(
                    key=role_name,
                    kind=step_kind,
                    title=title,
                    depends_on=depends_on,
                    metadata={
                        "team_role": role_name,
                        "team_id": team.team_id,
                        "role_count": slot_spec.count,
                        "role_config": slot_spec.config,
                    },
                )
            )

        _log.info(
            "team_decomposer.decomposed",
            team_id=team.team_id,
            roles=len(nodes),
            edges=len(edges),
        )
        return nodes

    def _build_step_title(
        self,
        role_name: str,
        goal: str,
        slot_spec: RoleSlotSpec,
    ) -> str:
        """Build a human-readable title for a team role step."""
        role_label = role_name.replace("_", " ").title()
        role_instruction = slot_spec.config.get("instruction", "")
        if role_instruction:
            return f"[{role_label}] {role_instruction}"
        return f"[{role_label}] {goal}"


def decompose_team_to_steps(
    *,
    team: TeamRecord,
    goal: str,
) -> list[StepNode]:
    """Convenience function wrapping TeamDecomposer.decompose()."""
    return TeamDecomposer().decompose(team=team, goal=goal)
