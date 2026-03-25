from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.dag_builder import StepDAGBuilder, StepNode


@dataclass(frozen=True)
class DAGPlan:
    """A plan describing how to decompose a goal into a DAG of steps."""

    goal: str
    nodes: list[StepNode]
    rationale: str = ""


class DAGOrchestrator:
    """Orchestrate DAG-structured task execution."""

    def __init__(self, store: KernelStore, dag_builder: StepDAGBuilder) -> None:
        self._store = store
        self._dag_builder = dag_builder

    def plan_from_nodes(
        self,
        goal: str,
        nodes: list[dict[str, Any]],
        rationale: str = "",
    ) -> DAGPlan:
        """Create a DAGPlan from raw node dictionaries.

        Each node dict should have: key, kind, title, and optionally
        depends_on, join_strategy, input_bindings, max_attempts, metadata.
        """
        step_nodes = [
            StepNode(
                key=n["key"],
                kind=n.get("kind", "execute"),
                title=n.get("title", n["key"]),
                depends_on=list(n.get("depends_on", [])),
                join_strategy=n.get("join_strategy", "all_required"),
                input_bindings=dict(n.get("input_bindings", {})),
                max_attempts=int(n.get("max_attempts", 1)),
                metadata=dict(n.get("metadata", {})),
            )
            for n in nodes
        ]
        return DAGPlan(goal=goal, nodes=step_nodes, rationale=rationale)

    def materialize_and_dispatch(
        self,
        task_id: str,
        plan: DAGPlan,
        *,
        queue_priority: int = 0,
    ) -> dict[str, str]:
        """Validate and materialize a DAG plan into the kernel store.

        Returns key → step_id mapping.
        """
        _dag, key_to_step_id = self._dag_builder.build_and_materialize(
            task_id, plan.nodes, queue_priority=queue_priority
        )
        return key_to_step_id

    def get_step_statuses(self, task_id: str, step_ids: list[str]) -> dict[str, str]:
        """Get current status of given steps."""
        result: dict[str, str] = {}
        for step_id in step_ids:
            step = self._store.get_step(step_id)
            if step is not None:
                result[step_id] = step.status
        return result
