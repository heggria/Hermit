from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import structlog

from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()

# ── Safe predicate evaluation ────────────────────────────────────────

_SAFE_AST_NODES: frozenset[type] = frozenset(
    {
        ast.Expression,
        ast.Compare,
        ast.BoolOp,
        ast.UnaryOp,
        ast.Name,
        ast.Constant,
        ast.Load,
        ast.And,
        ast.Or,
        ast.Not,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
        ast.Tuple,
        ast.List,
    }
)


def _safe_eval_predicate(predicate: str, namespace: dict[str, Any]) -> bool:
    """Evaluate a predicate string safely against a namespace.

    Only allows simple comparison and boolean expressions (``Compare``,
    ``BoolOp``, ``UnaryOp``, ``Name``, ``Constant``).  Any other AST node
    (``Call``, ``Attribute``, ``Subscript``, ``Lambda``, etc.) causes a
    ``ValueError`` to be raised.
    """
    tree = ast.parse(predicate, mode="eval")
    for node in ast.walk(tree):
        if type(node) not in _SAFE_AST_NODES:
            raise ValueError(f"Unsafe AST node {type(node).__name__!r} in predicate: {predicate!r}")
    # All nodes validated — safe to evaluate with restricted namespace.
    return bool(eval(compile(tree, "<predicate>", "eval"), {"__builtins__": {}}, namespace))


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
    predicate: str | None = None
    heartbeat_interval_seconds: float | None = None
    verification_required: bool = False
    verifies: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    monotonicity_class: str = "compensatable_mutation"


@dataclass(frozen=True)
class ConditionalStepNode(StepNode):
    """A step node with a conditional predicate evaluated against upstream outputs.

    The predicate is a simple Python expression evaluated with upstream output
    values as the namespace.  If it evaluates to False, the step is skipped.
    """

    pass  # predicate field inherited from StepNode


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
            for ref in node.verifies:
                if ref not in node_map:
                    raise ValueError(f"Step '{node.key}' verifies unknown step '{ref}'")
            for ref in node.supersedes:
                if ref not in node_map:
                    raise ValueError(f"Step '{node.key}' supersedes unknown step '{ref}'")

        in_degree: dict[str, int] = {k: 0 for k in node_map}
        adj: dict[str, list[str]] = {k: [] for k in node_map}
        reverse_adj: dict[str, list[str]] = {k: [] for k in node_map}
        for node in nodes:
            # depends_on edges: dep -> node (node runs after dep)
            for dep in node.depends_on:
                adj[dep].append(node.key)
                reverse_adj[node.key].append(dep)
                in_degree[node.key] += 1
            # verifies edges: verified -> verifier (verifier runs after verified)
            for ref in node.verifies:
                if ref not in node.depends_on:
                    adj[ref].append(node.key)
                    reverse_adj[node.key].append(ref)
                    in_degree[node.key] += 1
            # supersedes edges: superseded -> superseder (superseder runs after superseded)
            for ref in node.supersedes:
                if ref not in node.depends_on and ref not in node.verifies:
                    adj[ref].append(node.key)
                    reverse_adj[node.key].append(ref)
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
        workspace_root: str = "",
    ) -> dict[str, str]:
        """Create steps and step_attempts in the store from a DAGDefinition.

        Root nodes get status='ready', others get status='waiting'.
        Returns a mapping of key → step_id.

        Args:
            ingress_metadata: Base metadata merged into each step attempt's
                context.  The step title is used as ``entry_prompt`` so that
                the dispatch service can send a meaningful prompt to the LLM.
            workspace_root: Filesystem root for workspace lease validation.
                Must be set so that the dispatch service can reconstruct a
                correct ``TaskExecutionContext`` when claiming the attempt.
        """
        base_meta: dict[str, Any] = dict(ingress_metadata or {})
        base_meta.setdefault("dispatch_mode", "async")
        base_meta.setdefault("source", "dag")

        key_to_step_id: dict[str, str] = {}

        for key in dag.topological_order:
            node = dag.nodes[key]
            dep_step_ids = [key_to_step_id[dep] for dep in node.depends_on]
            status = "ready" if not dep_step_ids else "waiting"
            verifies_step_ids = [key_to_step_id[ref] for ref in node.verifies]
            supersedes_step_ids = [key_to_step_id[ref] for ref in node.supersedes]
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
                verification_required=node.verification_required,
                verifies=verifies_step_ids,
                supersedes=supersedes_step_ids,
            )
            key_to_step_id[key] = step.step_id

            # Build per-step context with entry_prompt derived from the node.
            step_meta = dict(base_meta)
            step_meta["entry_prompt"] = node.title
            step_meta["raw_text"] = node.title
            step_meta["dag_node_key"] = key
            step_meta["dag_node_kind"] = node.kind
            node_metadata = dict(node.metadata) if node.metadata else {}
            if node.predicate:
                node_metadata["predicate"] = node.predicate
            if node_metadata:
                step_meta["dag_node_metadata"] = node_metadata

            attempt_context: dict[str, Any] = {"ingress_metadata": step_meta}
            if node.heartbeat_interval_seconds is not None:
                attempt_context["heartbeat_interval_seconds"] = node.heartbeat_interval_seconds
            if workspace_root:
                attempt_context["workspace_root"] = workspace_root

            self._store.create_step_attempt(
                task_id=task_id,
                step_id=step.step_id,
                status=status,
                queue_priority=queue_priority,
                context=attempt_context,
            )

        return key_to_step_id

    @staticmethod
    def compute_super_steps(dag: DAGDefinition) -> list[list[str]]:
        """Group DAG nodes into super-steps (topological levels).

        Each super-step contains nodes that can execute in parallel — they
        share the same topological depth.  At a super-step boundary (all
        nodes in the group complete), a checkpoint can be emitted.

        Returns a list of groups ordered by execution depth.
        """
        depth: dict[str, int] = {}
        for key in dag.topological_order:
            node = dag.nodes[key]
            if not node.depends_on:
                depth[key] = 0
            else:
                depth[key] = max(depth.get(dep, 0) for dep in node.depends_on) + 1

        max_depth = max(depth.values()) if depth else 0
        levels: list[list[str]] = [[] for _ in range(max_depth + 1)]
        for key in dag.topological_order:
            levels[depth[key]].append(key)
        return levels

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

    # ------------------------------------------------------------------
    # DAG topology mutation API
    # ------------------------------------------------------------------

    def add_step(
        self,
        task_id: str,
        node: StepNode,
        *,
        queue_priority: int = 0,
        ingress_metadata: dict[str, Any] | None = None,
        workspace_root: str = "",
    ) -> str:
        """Add a new step to a running DAG.

        Resolves dependency keys to step_ids, validates no cycles, creates the
        step and its initial attempt, and emits a ``dag.topology_changed`` event.

        Returns the new step_id.
        """
        key_to_step_id = self._store.get_key_to_step_id(task_id)

        # Validate: no duplicate key
        if node.key in key_to_step_id:
            raise ValueError(f"Step key '{node.key}' already exists in task {task_id}")

        # Validate: all dependencies exist
        for dep_key in node.depends_on:
            if dep_key not in key_to_step_id:
                raise ValueError(f"Step '{node.key}' depends on unknown step '{dep_key}'")

        dep_step_ids = [key_to_step_id[dep] for dep in node.depends_on]

        # Cycle detection happens inside create_step via _check_dag_cycles
        step = self._store.create_step(
            task_id=task_id,
            kind=node.kind,
            status="ready" if not dep_step_ids else "waiting",
            title=node.title,
            depends_on=dep_step_ids,
            join_strategy=node.join_strategy,
            input_bindings=node.input_bindings,
            max_attempts=node.max_attempts,
            node_key=node.key,
        )

        # Build attempt context
        base_meta: dict[str, Any] = dict(ingress_metadata or {})
        base_meta.setdefault("dispatch_mode", "async")
        base_meta.setdefault("source", "dag")
        base_meta["entry_prompt"] = node.title
        base_meta["raw_text"] = node.title
        base_meta["dag_node_key"] = node.key
        base_meta["dag_node_kind"] = node.kind
        node_metadata = dict(node.metadata) if node.metadata else {}
        if node.predicate:
            node_metadata["predicate"] = node.predicate
        if node_metadata:
            base_meta["dag_node_metadata"] = node_metadata

        attempt_context: dict[str, Any] = {"ingress_metadata": base_meta}
        if workspace_root:
            attempt_context["workspace_root"] = workspace_root

        self._store.create_step_attempt(
            task_id=task_id,
            step_id=step.step_id,
            status="ready" if not dep_step_ids else "waiting",
            queue_priority=queue_priority,
            context=attempt_context,
        )

        # Emit topology changed event
        self._store.append_event(
            event_type="dag.topology_changed",
            entity_type="step",
            entity_id=step.step_id,
            task_id=task_id,
            step_id=step.step_id,
            actor="kernel",
            payload={
                "mutation": "add_step",
                "node_key": node.key,
                "depends_on": dep_step_ids,
                "step_id": step.step_id,
            },
        )

        log.info(
            "dag.step_added",
            task_id=task_id,
            step_id=step.step_id,
            node_key=node.key,
        )
        return step.step_id

    def skip_step(
        self,
        task_id: str,
        step_key: str,
        *,
        reason: str = "",
    ) -> None:
        """Skip a step by its node key, marking it terminal without execution.

        The step is set to ``skipped`` status and downstream dependents are
        activated (since ``skipped`` counts as a success status).
        """
        step = self._store.get_step_by_node_key(task_id, step_key)
        if step is None:
            raise ValueError(f"Step with key '{step_key}' not found in task {task_id}")
        self._store.skip_step(task_id, step.step_id, reason=reason)
        log.info(
            "dag.step_skipped",
            task_id=task_id,
            step_id=step.step_id,
            node_key=step_key,
            reason=reason,
        )

    def rewire_dependency(
        self,
        task_id: str,
        step_key: str,
        new_depends_on_keys: list[str],
    ) -> None:
        """Change the dependencies of a step identified by its node key.

        Resolves keys to step_ids, validates no cycles, and updates the step's
        ``depends_on_json`` in the store.
        """
        step = self._store.get_step_by_node_key(task_id, step_key)
        if step is None:
            raise ValueError(f"Step with key '{step_key}' not found in task {task_id}")

        key_to_step_id = self._store.get_key_to_step_id(task_id)
        resolved_deps: list[str] = []
        for dep_key in new_depends_on_keys:
            if dep_key not in key_to_step_id:
                raise ValueError(f"Step '{step_key}' depends on unknown step '{dep_key}'")
            resolved_deps.append(key_to_step_id[dep_key])

        # Cycle detection: build the full adjacency with the proposed change
        # and run Kahn's algorithm.
        self._validate_no_cycles_for_rewire(task_id, step.step_id, resolved_deps)

        self._store.update_step_depends_on(
            step.step_id, task_id=task_id, new_depends_on=resolved_deps
        )
        log.info(
            "dag.step_rewired",
            task_id=task_id,
            step_id=step.step_id,
            node_key=step_key,
            new_depends_on=resolved_deps,
        )

    def _validate_no_cycles_for_rewire(
        self,
        task_id: str,
        target_step_id: str,
        new_depends_on: list[str],
    ) -> None:
        """Check that rewiring does not introduce a cycle.

        Builds the full step adjacency graph with the proposed dependency
        change and runs Kahn's algorithm.
        """
        rows = self._store.list_steps(task_id=task_id)
        # Build adjacency: for each step, its depends_on lists the steps it
        # depends on.  We need the forward adjacency: dep -> [dependents].
        adj: dict[str, list[str]] = {}
        all_ids: set[str] = set()
        for s in rows:
            all_ids.add(s.step_id)
            adj.setdefault(s.step_id, [])

        for s in rows:
            deps = new_depends_on if s.step_id == target_step_id else list(s.depends_on)
            for dep_id in deps:
                adj.setdefault(dep_id, [])
                adj[dep_id].append(s.step_id)

        # Kahn's algorithm
        in_degree: dict[str, int] = {sid: 0 for sid in all_ids}
        for sid in all_ids:
            deps = (
                new_depends_on
                if sid == target_step_id
                else [d for s in rows if s.step_id == sid for d in s.depends_on]
            )
            in_degree[sid] = len(deps)

        queue_k: deque[str] = deque(sid for sid, deg in in_degree.items() if deg == 0)
        visited = 0
        while queue_k:
            current = queue_k.popleft()
            visited += 1
            for neighbor in adj.get(current, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue_k.append(neighbor)

        if visited != len(all_ids):
            raise ValueError(f"Cycle detected in step DAG for task {task_id}")

    @staticmethod
    def evaluate_predicate(
        predicate: str | None,
        upstream_outputs: dict[str, Any],
    ) -> bool:
        """Evaluate a conditional predicate against upstream output values.

        The predicate is a simple Python expression evaluated with
        ``upstream_outputs`` as the namespace.  Returns ``False`` on any
        error or if the predicate is empty/None.
        """
        if not predicate:
            return False
        try:
            result = _safe_eval_predicate(predicate, upstream_outputs)
            return bool(result)
        except Exception:
            log.warning(
                "dag.predicate_eval_failed",
                predicate=predicate,
                exc_info=True,
            )
            return False
