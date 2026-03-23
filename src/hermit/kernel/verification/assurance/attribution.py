"""Causal attribution engine for failure root-cause analysis.

Builds a causal graph from trace envelopes and violations, identifies the
first divergence point, classifies nodes by role (root_cause, enabler,
propagator, victim, mitigator), and selects the root cause — optionally
informed by counterfactual replay results.
"""

from __future__ import annotations

from collections import deque

import structlog

from hermit.kernel.verification.assurance.models import (
    AttributionCase,
    AttributionEdge,
    AttributionNode,
    ContractViolation,
    InvariantViolation,
    ReplayResult,
    TraceEnvelope,
    _id,
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Node type constants
# ---------------------------------------------------------------------------

_NODE_TYPE_EVENT = "event"
_NODE_TYPE_CONTRACT_VIOLATION = "contract_violation"
_NODE_TYPE_INVARIANT_VIOLATION = "invariant_violation"
_NODE_TYPE_RECOVERY_ACTION = "recovery_action"

# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------

_ROLE_ROOT_CAUSE = "root_cause"
_ROLE_ENABLER = "enabler"
_ROLE_PROPAGATOR = "propagator"
_ROLE_VICTIM = "victim"
_ROLE_MITIGATOR = "mitigator"
_ROLE_UNKNOWN = "unknown"

# Event types that signal recovery actions
_RECOVERY_EVENT_TYPES = frozenset({
    "recovery.started",
    "recovery.completed",
    "rollback.started",
    "rollback.completed",
    "reconciliation.started",
    "reconciliation.completed",
})


class FailureAttributionEngine:
    """Builds causal attribution graphs and selects root causes.

    The engine processes trace envelopes (runtime events) and violations
    (contract or invariant) to construct a directed causal graph.  It then
    walks that graph to classify every node by its role in the failure and
    selects the most likely root cause.
    """

    def __init__(self) -> None:
        self._log = log.bind(component="attribution_engine")

    # -- public entry point -------------------------------------------------

    def attribute(
        self,
        envelopes: list[TraceEnvelope],
        violations: list[ContractViolation | InvariantViolation],
        *,
        counterfactual_results: list[ReplayResult] | None = None,
    ) -> AttributionCase:
        """Build a complete attribution case from traces and violations.

        1. Build the causal graph (nodes + edges).
        2. Find the first divergence (earliest violation by event_seq).
        3. Collect root-cause candidates.
        4. Select the root cause (optionally using counterfactuals).
        5. Classify every node by role.
        6. Extract the propagation chain.
        """
        nodes, edges = self.build_causal_graph(envelopes, violations)

        first_div = self.find_first_divergence(envelopes, violations)

        # Candidate root causes: all violation nodes, ordered so that
        # the first-divergence candidate (earliest by event_seq) comes
        # first.  This ensures select_root_cause prefers it when there
        # are no counterfactual results.
        candidates = [n.node_id for n in nodes if n.node_type in (
            _NODE_TYPE_CONTRACT_VIOLATION,
            _NODE_TYPE_INVARIANT_VIOLATION,
        )]
        if first_div and first_div in candidates:
            candidates.remove(first_div)
            candidates.insert(0, first_div)

        selected = ""
        if candidates:
            selected = self.select_root_cause(
                candidates, nodes, edges, counterfactual_results,
            )

        root_id = selected or first_div or ""

        if root_id:
            nodes = self.classify_nodes(nodes, edges, root_id)

        propagation_chain = self._extract_propagation_chain(nodes, edges, root_id)

        counterfactual_ids = [
            r.replay_id for r in (counterfactual_results or [])
        ]

        evidence_refs = self._collect_evidence_refs(envelopes, violations)

        fix_hints = self._collect_fix_hints(violations)

        failure_sig = self._compute_failure_signature(violations)

        confidence = self._compute_confidence(
            candidates, counterfactual_results,
        )

        case = AttributionCase(
            case_id=_id("attr"),
            failure_signature=failure_sig,
            first_divergence=first_div or "",
            root_cause_candidates=candidates,
            selected_root_cause=selected,
            propagation_chain=propagation_chain,
            counterfactuals=counterfactual_ids,
            confidence=confidence,
            evidence_refs=evidence_refs,
            fix_hints=fix_hints,
            nodes=nodes,
            edges=edges,
        )

        self._log.info(
            "attribution_complete",
            case_id=case.case_id,
            root_cause=selected,
            node_count=len(nodes),
            edge_count=len(edges),
        )
        return case

    # -- graph construction -------------------------------------------------

    def build_causal_graph(
        self,
        envelopes: list[TraceEnvelope],
        violations: list[ContractViolation | InvariantViolation],
    ) -> tuple[list[AttributionNode], list[AttributionEdge]]:
        """Create nodes and edges from envelopes and violations.

        Nodes
        -----
        - One node per envelope (type ``event``).
        - One node per violation (``contract_violation`` or
          ``invariant_violation``).

        Edges
        -----
        - ``caused_by``: from an event whose ``trace_id`` matches
          another event's ``causation_id``.
        - ``propagates_to``: events sharing the same ``correlation_id``.
        - ``guards``: approval event -> tool_call event via
          ``approval_ref``.
        - ``mitigates``: recovery events connected to the events they
          recover.
        """
        nodes: list[AttributionNode] = []
        edges: list[AttributionEdge] = []

        # Index envelopes by trace_id for causation lookups
        trace_id_to_node_id: dict[str, str] = {}
        envelope_node_ids: list[str] = []

        for env in envelopes:
            node_type = (
                _NODE_TYPE_RECOVERY_ACTION
                if env.event_type in _RECOVERY_EVENT_TYPES
                else _NODE_TYPE_EVENT
            )
            node = AttributionNode(
                node_id=env.trace_id,
                node_type=node_type,
                ref=env.trace_id,
            )
            nodes.append(node)
            trace_id_to_node_id[env.trace_id] = env.trace_id
            envelope_node_ids.append(env.trace_id)

        # Violation nodes
        for v in violations:
            if isinstance(v, ContractViolation):
                node = AttributionNode(
                    node_id=v.violation_id,
                    node_type=_NODE_TYPE_CONTRACT_VIOLATION,
                    ref=v.violation_id,
                )
            else:
                node = AttributionNode(
                    node_id=v.violation_id,
                    node_type=_NODE_TYPE_INVARIANT_VIOLATION,
                    ref=v.violation_id,
                )
            nodes.append(node)

            # Link violation to its originating event if present
            event_id = getattr(v, "event_id", None)
            if event_id and event_id in trace_id_to_node_id:
                edges.append(AttributionEdge(
                    source=trace_id_to_node_id[event_id],
                    target=v.violation_id,
                    edge_type="caused_by",
                ))

        # Build edges from envelope relationships
        # Index by causation_id for caused_by edges
        causation_index: dict[str, list[str]] = {}
        correlation_index: dict[str, list[str]] = {}
        approval_index: dict[str, list[str]] = {}

        for env in envelopes:
            if env.causation_id:
                causation_index.setdefault(env.causation_id, []).append(env.trace_id)
            if env.correlation_id:
                correlation_index.setdefault(env.correlation_id, []).append(env.trace_id)
            if env.approval_ref:
                approval_index.setdefault(env.approval_ref, []).append(env.trace_id)

        # caused_by: if envelope B has causation_id == envelope A's trace_id,
        # then A caused B
        for env in envelopes:
            if env.causation_id and env.causation_id in trace_id_to_node_id:
                edges.append(AttributionEdge(
                    source=trace_id_to_node_id[env.causation_id],
                    target=env.trace_id,
                    edge_type="caused_by",
                ))

        # propagates_to: events sharing the same correlation_id
        for _corr_id, node_ids in correlation_index.items():
            if len(node_ids) < 2:
                continue
            # Sort by event_seq to establish propagation direction
            seq_map: dict[str, int] = {}
            for env in envelopes:
                if env.trace_id in node_ids:
                    seq_map[env.trace_id] = env.event_seq
            sorted_ids = sorted(node_ids, key=lambda nid: seq_map.get(nid, 0))
            for i in range(len(sorted_ids) - 1):
                edges.append(AttributionEdge(
                    source=sorted_ids[i],
                    target=sorted_ids[i + 1],
                    edge_type="propagates_to",
                ))

        # guards: approval events that guard tool_call events
        for env in envelopes:
            if env.event_type == "tool_call.start" and env.approval_ref:
                # Find the approval.granted event with the same approval_ref
                for other in envelopes:
                    if (
                        other.event_type == "approval.granted"
                        and other.approval_ref == env.approval_ref
                    ):
                        edges.append(AttributionEdge(
                            source=other.trace_id,
                            target=env.trace_id,
                            edge_type="guards",
                        ))
                        break

        # mitigates: recovery events connected to preceding failure events
        for env in envelopes:
            if (
                env.event_type in _RECOVERY_EVENT_TYPES
                and env.causation_id
                and env.causation_id in trace_id_to_node_id
            ):
                edges.append(AttributionEdge(
                    source=env.trace_id,
                    target=trace_id_to_node_id[env.causation_id],
                    edge_type="mitigates",
                ))

        return nodes, edges

    # -- first divergence ---------------------------------------------------

    def find_first_divergence(
        self,
        envelopes: list[TraceEnvelope],
        violations: list[ContractViolation | InvariantViolation],
    ) -> str | None:
        """Return the node_id of the earliest violation by event_seq.

        For contract violations, we match ``event_id`` to an envelope's
        ``trace_id`` to find the event_seq.  For invariant violations, we
        use the ``event_id`` field directly.  Violations without a matching
        event are ordered by ``detected_at``.
        """
        if not violations:
            return None

        # Build a lookup from trace_id -> event_seq
        seq_by_trace: dict[str, int] = {
            env.trace_id: env.event_seq for env in envelopes
        }

        best_id: str | None = None
        best_seq: float = float("inf")

        for v in violations:
            event_id = getattr(v, "event_id", None)
            if event_id and event_id in seq_by_trace:
                seq = seq_by_trace[event_id]
            else:
                # Fall back to detected_at as a float pseudo-sequence
                seq = int(v.detected_at * 1_000_000)

            if seq < best_seq:
                best_seq = seq
                best_id = v.violation_id

        return best_id

    # -- node classification ------------------------------------------------

    def classify_nodes(
        self,
        nodes: list[AttributionNode],
        edges: list[AttributionEdge],
        root_cause_id: str,
    ) -> list[AttributionNode]:
        """Assign roles to nodes via BFS from the root cause.

        Roles:
        - ``root_cause``:  the root_cause_id node itself.
        - ``enabler``:     direct predecessors of the root cause (nodes
                           with an edge *into* root_cause_id).
        - ``propagator``:  nodes reachable from root_cause_id via
                           forward edges.
        - ``victim``:      terminal failure nodes (no outgoing causal
                           edges) on the propagation path.
        - ``mitigator``:   recovery action nodes.
        """
        node_map = {n.node_id: n for n in nodes}

        # Build adjacency: forward (source -> target) and reverse
        forward: dict[str, list[str]] = {}
        reverse: dict[str, list[str]] = {}
        edge_types: dict[tuple[str, str], str] = {}

        for e in edges:
            forward.setdefault(e.source, []).append(e.target)
            reverse.setdefault(e.target, []).append(e.source)
            edge_types[(e.source, e.target)] = e.edge_type

        # Start with all nodes as unknown
        roles: dict[str, str] = {n.node_id: _ROLE_UNKNOWN for n in nodes}

        # 1) Root cause
        if root_cause_id in node_map:
            roles[root_cause_id] = _ROLE_ROOT_CAUSE

        # 2) Enablers: direct predecessors of root_cause_id
        for pred in reverse.get(root_cause_id, []):
            if pred != root_cause_id:
                roles[pred] = _ROLE_ENABLER

        # 3) Mitigators: any recovery_action node
        for n in nodes:
            if n.node_type == _NODE_TYPE_RECOVERY_ACTION:
                roles[n.node_id] = _ROLE_MITIGATOR

        # 4) BFS forward from root_cause_id to find propagators / victims
        visited: set[str] = set()
        queue: deque[str] = deque()

        for succ in forward.get(root_cause_id, []):
            if succ != root_cause_id and roles.get(succ) == _ROLE_UNKNOWN:
                queue.append(succ)

        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)

            # Skip nodes already classified as enabler / mitigator / root_cause
            if roles[nid] not in (_ROLE_UNKNOWN, _ROLE_PROPAGATOR, _ROLE_VICTIM):
                continue

            successors = [
                s for s in forward.get(nid, [])
                if s not in visited and s != root_cause_id
            ]

            if not successors:
                # Terminal node on the propagation path -> victim
                roles[nid] = _ROLE_VICTIM
            else:
                roles[nid] = _ROLE_PROPAGATOR
                for s in successors:
                    queue.append(s)

        # Rebuild nodes with assigned roles
        classified: list[AttributionNode] = []
        for n in nodes:
            classified.append(AttributionNode(
                node_id=n.node_id,
                node_type=n.node_type,
                ref=n.ref,
                role=roles.get(n.node_id, _ROLE_UNKNOWN),
            ))
        return classified

    # -- root cause selection -----------------------------------------------

    def select_root_cause(
        self,
        candidates: list[str],
        nodes: list[AttributionNode],
        edges: list[AttributionEdge],
        counterfactual_results: list[ReplayResult] | None = None,
    ) -> str:
        """Select the most likely root cause from candidates.

        Strategy:
        1. If counterfactual replays are available, prefer the candidate
           whose removal eliminates the most violations.
        2. Otherwise, prefer the earliest candidate.  "Earliest" is
           determined by position in the *candidates* list -- callers
           are expected to supply candidates ordered by causal priority
           (e.g. first divergence first).
        """
        if not candidates:
            return ""

        if counterfactual_results:
            best = self._select_by_counterfactual(
                candidates, counterfactual_results,
            )
            if best:
                return best

        # Prefer the first candidate in the supplied list.
        return candidates[0]

    # -- private helpers ----------------------------------------------------

    def _select_by_counterfactual(
        self,
        candidates: list[str],
        results: list[ReplayResult],
    ) -> str:
        """Pick the candidate whose removal in counterfactual replay
        eliminated the most violations.

        Each ``ReplayResult`` is assumed to represent the replay with one
        candidate removed -- matched by checking whether the candidate's
        id appears in the mutation's ``target_ref``.  The candidate whose
        removal leaves the fewest remaining violations is selected.
        """
        remaining_scores: dict[str, int] = {}

        for result in results:
            # Determine which candidate this replay removed
            removed_candidates: set[str] = set()
            for mutation in result.mutations:
                if mutation.mutation_type == "drop_event":
                    removed_candidates.add(mutation.target_ref)

            for cand in candidates:
                if cand in removed_candidates:
                    remaining = len(result.contract_violations)
                    # Accumulate remaining violations across replays
                    # that removed this candidate.
                    remaining_scores[cand] = (
                        remaining_scores.get(cand, 0) + remaining
                    )

        if not remaining_scores:
            return ""

        # The candidate whose removal left the fewest remaining violations.
        return min(remaining_scores, key=lambda c: remaining_scores[c])

    def _extract_propagation_chain(
        self,
        nodes: list[AttributionNode],
        edges: list[AttributionEdge],
        root_id: str,
    ) -> list[str]:
        """BFS forward from root cause to collect the propagation chain."""
        if not root_id:
            return []

        forward: dict[str, list[str]] = {}
        for e in edges:
            if e.edge_type in ("caused_by", "propagates_to"):
                forward.setdefault(e.source, []).append(e.target)

        chain: list[str] = [root_id]
        visited: set[str] = {root_id}
        queue: deque[str] = deque(forward.get(root_id, []))

        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)
            chain.append(nid)
            for succ in forward.get(nid, []):
                if succ not in visited:
                    queue.append(succ)

        return chain

    def _collect_evidence_refs(
        self,
        envelopes: list[TraceEnvelope],
        violations: list[ContractViolation | InvariantViolation],
    ) -> list[str]:
        """Gather unique artifact / receipt refs from envelopes and violations."""
        refs: list[str] = []
        seen: set[str] = set()

        for env in envelopes:
            for ref in env.artifact_refs:
                if ref not in seen:
                    refs.append(ref)
                    seen.add(ref)
            for ref_attr in ("receipt_ref", "grant_ref", "lease_ref"):
                val = getattr(env, ref_attr, None)
                if val and val not in seen:
                    refs.append(val)
                    seen.add(val)

        for v in violations:
            if v.violation_id not in seen:
                refs.append(v.violation_id)
                seen.add(v.violation_id)

        return refs

    def _collect_fix_hints(
        self,
        violations: list[ContractViolation | InvariantViolation],
    ) -> list[str]:
        """Deduplicate remediation hints from violations."""
        hints: list[str] = []
        seen: set[str] = set()
        for v in violations:
            hint = getattr(v, "remediation_hint", "")
            if hint and hint not in seen:
                hints.append(hint)
                seen.add(hint)
        return hints

    def _compute_failure_signature(
        self,
        violations: list[ContractViolation | InvariantViolation],
    ) -> str:
        """Compute a short signature summarising the failure pattern."""
        if not violations:
            return ""

        type_counts: dict[str, int] = {}
        for v in violations:
            if isinstance(v, ContractViolation):
                key = f"contract:{v.contract_id}"
            else:
                key = f"invariant:{v.invariant_id}"
            type_counts[key] = type_counts.get(key, 0) + 1

        parts = sorted(
            f"{k}x{c}" if c > 1 else k for k, c in type_counts.items()
        )
        return "|".join(parts)

    def _compute_confidence(
        self,
        candidates: list[str],
        counterfactual_results: list[ReplayResult] | None,
    ) -> float:
        """Heuristic confidence score for the attribution.

        - Base 0.5 if we have candidates.
        - +0.3 if counterfactual evidence is available.
        - +0.2 if there is exactly one candidate (unambiguous).
        """
        if not candidates:
            return 0.0

        score = 0.5
        if counterfactual_results:
            score += 0.3
        if len(candidates) == 1:
            score += 0.2
        return min(score, 1.0)
