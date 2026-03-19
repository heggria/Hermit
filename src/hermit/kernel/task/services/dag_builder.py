from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from hermit.kernel.ledger.journal.store import KernelStore


@dataclass(frozen=True)
class StepNode:
    """A logical node in a DAG of steps."""

    key: str
    kind: str
    title: str
    depends_on: list[str] = field(default_factory=list)
    join_strategy: str = "all_required"
    input_bindings: dict[str, str] = field(default_factory=dict)
    max_attempts: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DAGDefinition:
    """Validated DAG structure with topological ordering."""

    nodes: dict[str, StepNode]
    roots: list[str]
    leaves: list[str]
    topological_order: list[str]


class StepDAGBuilder:
    """Validates and materializes step DAGs into the kernel store."""

    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def validate(self, nodes: list[StepNode]) -> DAGDefinition:
        """Validate a list of StepNodes as a valid DAG.

        Checks:
        - No duplicate keys
        - All depends_on references exist
        - No cycles (Kahn's algorithm)

        Disconnected subgraphs are allowed — independent parallel steps
        do not need to be connected.

        Returns a DAGDefinition with topological ordering.
        """
        if not nodes:
            raise ValueError("DAG must contain at least one node")

        node_map: dict[str, StepNode] = {}
        for node in nodes:
            if node.key in node_map:
                raise ValueError(f"Duplicate step key: {node.key}")
            node_map[node.key] = node

        for node in nodes:
            for dep in node.depends_on:
                if dep not in node_map:
                    raise ValueError(f"Step '{node.key}' depends on unknown step '{dep}'")

        in_degree: dict[str, int] = {k: 0 for k in node_map}
        adj: dict[str, list[str]] = {k: [] for k in node_map}
        reverse_adj: dict[str, list[str]] = {k: [] for k in node_map}
        for node in nodes:
            for dep in node.depends_on:
                adj[dep].append(node.key)
                reverse_adj[node.key].append(dep)
                in_degree[node.key] += 1

        queue: deque[str] = deque(k for k, d in in_degree.items() if d == 0)
        topo_order: list[str] = []
        while queue:
            current = queue.popleft()
            topo_order.append(current)
            for neighbor in adj[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(topo_order) != len(node_map):
            raise ValueError("Cycle detected in step DAG")

        roots = [k for k in topo_order if not node_map[k].depends_on]
        out_degree = {k: len(adj[k]) for k in node_map}
        leaves = [k for k in topo_order if out_degree[k] == 0]

        return DAGDefinition(
            nodes=node_map,
            roots=roots,
            leaves=leaves,
            topological_order=topo_order,
        )

    def materialize(
        self,
        task_id: str,
        dag: DAGDefinition,
        *,
        queue_priority: int = 0,
        ingress_metadata: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Create steps and step_attempts in the store from a DAGDefinition.

        Root nodes get status='ready', others get status='waiting'.
        Returns a mapping of key → step_id.

        Args:
            ingress_metadata: Base metadata merged into each step attempt's
                context.  The step title is used as ``entry_prompt`` so that
                the dispatch service can send a meaningful prompt to the LLM.
        """
        base_meta: dict[str, Any] = dict(ingress_metadata or {})
        base_meta.setdefault("dispatch_mode", "async")
        base_meta.setdefault("source", "dag")

        key_to_step_id: dict[str, str] = {}

        for key in dag.topological_order:
            node = dag.nodes[key]
            dep_step_ids = [key_to_step_id[dep] for dep in node.depends_on]
            status = "ready" if not dep_step_ids else "waiting"
            step = self._store.create_step(
                task_id=task_id,
                kind=node.kind,
                status=status,
                title=node.title,
                depends_on=dep_step_ids,
                join_strategy=node.join_strategy,
                input_bindings=node.input_bindings,
                max_attempts=node.max_attempts,
                node_key=key,
            )
            key_to_step_id[key] = step.step_id

            # Build per-step context with entry_prompt derived from the node.
            step_meta = dict(base_meta)
            step_meta["entry_prompt"] = node.title
            step_meta["raw_text"] = node.title
            step_meta["dag_node_key"] = key
            step_meta["dag_node_kind"] = node.kind
            if node.metadata:
                step_meta["dag_node_metadata"] = node.metadata

            self._store.create_step_attempt(
                task_id=task_id,
                step_id=step.step_id,
                status=status,
                queue_priority=queue_priority,
                context={"ingress_metadata": step_meta},
            )

        return key_to_step_id

    def build_and_materialize(
        self,
        task_id: str,
        nodes: list[StepNode],
        **kw: Any,
    ) -> tuple[DAGDefinition, dict[str, str]]:
        """Validate and materialize a DAG in one call."""
        dag = self.validate(nodes)
        key_to_step_id = self.materialize(task_id, dag, **kw)
        return dag, key_to_step_id
