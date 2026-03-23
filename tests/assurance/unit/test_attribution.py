"""Unit tests for FailureAttributionEngine."""

from __future__ import annotations

import time
import uuid

import pytest

from hermit.kernel.verification.assurance.attribution import (
    _NODE_TYPE_CONTRACT_VIOLATION,
    _NODE_TYPE_EVENT,
    _NODE_TYPE_INVARIANT_VIOLATION,
    _NODE_TYPE_RECOVERY_ACTION,
    _ROLE_ENABLER,
    _ROLE_MITIGATOR,
    _ROLE_PROPAGATOR,
    _ROLE_ROOT_CAUSE,
    _ROLE_UNKNOWN,
    _ROLE_VICTIM,
    FailureAttributionEngine,
)
from hermit.kernel.verification.assurance.models import (
    AttributionCase,
    AttributionEdge,
    AttributionNode,
    ContractViolation,
    CounterfactualMutation,
    InvariantViolation,
    ReplayResult,
    TraceEnvelope,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(prefix: str = "test") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _envelope(
    *,
    trace_id: str | None = None,
    event_type: str = "generic",
    event_seq: int = 0,
    task_id: str = "task-1",
    run_id: str = "run-1",
    causation_id: str | None = None,
    correlation_id: str | None = None,
    approval_ref: str | None = None,
    **kwargs: object,
) -> TraceEnvelope:
    return TraceEnvelope(
        trace_id=trace_id or _uid("trace"),
        run_id=run_id,
        task_id=task_id,
        event_type=event_type,
        event_seq=event_seq,
        wallclock_at=time.time() + event_seq * 0.001,
        logical_clock=event_seq,
        causation_id=causation_id,
        correlation_id=correlation_id,
        approval_ref=approval_ref,
        **kwargs,  # type: ignore[arg-type]
    )


def _contract_violation(
    *,
    violation_id: str | None = None,
    contract_id: str = "c-1",
    event_id: str | None = None,
    task_id: str = "task-1",
    severity: str = "high",
    remediation_hint: str = "",
    detected_at: float | None = None,
) -> ContractViolation:
    return ContractViolation(
        violation_id=violation_id or _uid("cv"),
        contract_id=contract_id,
        severity=severity,
        mode="runtime",
        task_id=task_id,
        event_id=event_id,
        remediation_hint=remediation_hint,
        detected_at=detected_at or time.time(),
    )


def _invariant_violation(
    *,
    violation_id: str | None = None,
    invariant_id: str = "inv-1",
    event_id: str = "",
    task_id: str = "task-1",
    severity: str = "blocker",
    detected_at: float | None = None,
) -> InvariantViolation:
    return InvariantViolation(
        violation_id=violation_id or _uid("iv"),
        invariant_id=invariant_id,
        severity=severity,
        event_id=event_id,
        task_id=task_id,
        detected_at=detected_at or time.time(),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine() -> FailureAttributionEngine:
    return FailureAttributionEngine()


def _make_linear_causal_trace() -> tuple[
    list[TraceEnvelope],
    list[ContractViolation],
    dict[str, str],
]:
    """Build a linear causal chain: A -> B -> C, with a violation at C.

    Returns (envelopes, violations, id_map) where id_map has keys
    "a", "b", "c", "v".
    """
    id_a = _uid("trace")
    id_b = _uid("trace")
    id_c = _uid("trace")
    viol_id = _uid("cv")

    envelopes = [
        _envelope(trace_id=id_a, event_type="task.created", event_seq=0),
        _envelope(trace_id=id_b, event_type="tool_call.start", event_seq=1, causation_id=id_a),
        _envelope(trace_id=id_c, event_type="receipt.issued", event_seq=2, causation_id=id_b),
    ]
    violations = [
        _contract_violation(violation_id=viol_id, event_id=id_c),
    ]
    ids = {"a": id_a, "b": id_b, "c": id_c, "v": viol_id}
    return envelopes, violations, ids


# ---------------------------------------------------------------------------
# Tests: build_causal_graph
# ---------------------------------------------------------------------------


class TestBuildCausalGraph:
    """Tests for FailureAttributionEngine.build_causal_graph."""

    def test_creates_event_nodes_from_envelopes(self, engine: FailureAttributionEngine) -> None:
        envelopes = [
            _envelope(event_seq=0),
            _envelope(event_seq=1),
        ]
        nodes, _edges = engine.build_causal_graph(envelopes, [])

        assert len(nodes) == 2
        assert all(n.node_type == _NODE_TYPE_EVENT for n in nodes)

    def test_creates_violation_nodes(self, engine: FailureAttributionEngine) -> None:
        envelopes = [_envelope(event_seq=0)]
        cv = _contract_violation()
        iv = _invariant_violation()

        nodes, _edges = engine.build_causal_graph(envelopes, [cv, iv])

        type_map = {n.node_id: n.node_type for n in nodes}
        assert type_map[cv.violation_id] == _NODE_TYPE_CONTRACT_VIOLATION
        assert type_map[iv.violation_id] == _NODE_TYPE_INVARIANT_VIOLATION

    def test_causation_id_creates_caused_by_edges(
        self, engine: FailureAttributionEngine,
    ) -> None:
        id_parent = _uid("trace")
        id_child = _uid("trace")

        envelopes = [
            _envelope(trace_id=id_parent, event_seq=0),
            _envelope(trace_id=id_child, event_seq=1, causation_id=id_parent),
        ]
        _nodes, edges = engine.build_causal_graph(envelopes, [])

        caused_by = [e for e in edges if e.edge_type == "caused_by"]
        assert len(caused_by) == 1
        assert caused_by[0].source == id_parent
        assert caused_by[0].target == id_child

    def test_correlation_id_creates_propagates_to_edges(
        self, engine: FailureAttributionEngine,
    ) -> None:
        corr = _uid("corr")
        id_1 = _uid("trace")
        id_2 = _uid("trace")
        id_3 = _uid("trace")

        envelopes = [
            _envelope(trace_id=id_1, event_seq=0, correlation_id=corr),
            _envelope(trace_id=id_2, event_seq=1, correlation_id=corr),
            _envelope(trace_id=id_3, event_seq=2, correlation_id=corr),
        ]
        _nodes, edges = engine.build_causal_graph(envelopes, [])

        prop_edges = [e for e in edges if e.edge_type == "propagates_to"]
        assert len(prop_edges) == 2
        # Should be ordered by event_seq
        assert prop_edges[0].source == id_1
        assert prop_edges[0].target == id_2
        assert prop_edges[1].source == id_2
        assert prop_edges[1].target == id_3

    def test_approval_ref_creates_guards_edge(
        self, engine: FailureAttributionEngine,
    ) -> None:
        approval = _uid("approval")
        id_approval = _uid("trace")
        id_tool = _uid("trace")

        envelopes = [
            _envelope(
                trace_id=id_approval,
                event_type="approval.granted",
                event_seq=0,
                approval_ref=approval,
            ),
            _envelope(
                trace_id=id_tool,
                event_type="tool_call.start",
                event_seq=1,
                approval_ref=approval,
            ),
        ]
        _nodes, edges = engine.build_causal_graph(envelopes, [])

        guards = [e for e in edges if e.edge_type == "guards"]
        assert len(guards) == 1
        assert guards[0].source == id_approval
        assert guards[0].target == id_tool

    def test_recovery_events_create_mitigates_edges(
        self, engine: FailureAttributionEngine,
    ) -> None:
        id_failure = _uid("trace")
        id_recovery = _uid("trace")

        envelopes = [
            _envelope(trace_id=id_failure, event_type="tool_call.start", event_seq=0),
            _envelope(
                trace_id=id_recovery,
                event_type="recovery.started",
                event_seq=1,
                causation_id=id_failure,
            ),
        ]
        nodes, edges = engine.build_causal_graph(envelopes, [])

        mitigates = [e for e in edges if e.edge_type == "mitigates"]
        assert len(mitigates) == 1
        assert mitigates[0].source == id_recovery
        assert mitigates[0].target == id_failure

        # Recovery event should be typed as recovery_action
        recovery_node = next(n for n in nodes if n.node_id == id_recovery)
        assert recovery_node.node_type == _NODE_TYPE_RECOVERY_ACTION

    def test_violation_linked_to_event_via_event_id(
        self, engine: FailureAttributionEngine,
    ) -> None:
        id_event = _uid("trace")
        viol = _contract_violation(event_id=id_event)

        envelopes = [_envelope(trace_id=id_event, event_seq=0)]
        _nodes, edges = engine.build_causal_graph(envelopes, [viol])

        linked = [
            e for e in edges
            if e.target == viol.violation_id and e.edge_type == "caused_by"
        ]
        assert len(linked) == 1
        assert linked[0].source == id_event


# ---------------------------------------------------------------------------
# Tests: find_first_divergence
# ---------------------------------------------------------------------------


class TestFindFirstDivergence:
    """Tests for FailureAttributionEngine.find_first_divergence."""

    def test_returns_none_when_no_violations(
        self, engine: FailureAttributionEngine,
    ) -> None:
        envelopes = [_envelope(event_seq=0)]
        assert engine.find_first_divergence(envelopes, []) is None

    def test_picks_earliest_by_event_seq(
        self, engine: FailureAttributionEngine,
    ) -> None:
        id_early = _uid("trace")
        id_late = _uid("trace")

        envelopes = [
            _envelope(trace_id=id_early, event_seq=1),
            _envelope(trace_id=id_late, event_seq=5),
        ]
        v_early = _contract_violation(violation_id="v-early", event_id=id_early)
        v_late = _contract_violation(violation_id="v-late", event_id=id_late)

        # Pass violations in reverse order to confirm ordering is by seq
        result = engine.find_first_divergence(envelopes, [v_late, v_early])
        assert result == "v-early"

    def test_falls_back_to_detected_at_when_no_event_id(
        self, engine: FailureAttributionEngine,
    ) -> None:
        now = time.time()
        v1 = _contract_violation(violation_id="v-1", detected_at=now + 10)
        v2 = _contract_violation(violation_id="v-2", detected_at=now + 1)

        result = engine.find_first_divergence([], [v1, v2])
        assert result == "v-2"

    def test_handles_mixed_contract_and_invariant(
        self, engine: FailureAttributionEngine,
    ) -> None:
        id_first = _uid("trace")
        id_second = _uid("trace")

        envelopes = [
            _envelope(trace_id=id_first, event_seq=2),
            _envelope(trace_id=id_second, event_seq=7),
        ]
        cv = _contract_violation(violation_id="cv-1", event_id=id_second)
        iv = _invariant_violation(violation_id="iv-1", event_id=id_first)

        result = engine.find_first_divergence(envelopes, [cv, iv])
        assert result == "iv-1"


# ---------------------------------------------------------------------------
# Tests: classify_nodes
# ---------------------------------------------------------------------------


class TestClassifyNodes:
    """Tests for FailureAttributionEngine.classify_nodes."""

    def test_root_cause_assigned(self, engine: FailureAttributionEngine) -> None:
        nodes = [
            AttributionNode(node_id="n1", node_type="event", ref="n1"),
        ]
        result = engine.classify_nodes(nodes, [], "n1")
        assert result[0].role == _ROLE_ROOT_CAUSE

    def test_enabler_assigned_to_predecessor(
        self, engine: FailureAttributionEngine,
    ) -> None:
        nodes = [
            AttributionNode(node_id="enabler", node_type="event", ref="enabler"),
            AttributionNode(node_id="root", node_type="contract_violation", ref="root"),
        ]
        edges = [
            AttributionEdge(source="enabler", target="root", edge_type="caused_by"),
        ]
        result = engine.classify_nodes(nodes, edges, "root")

        role_map = {n.node_id: n.role for n in result}
        assert role_map["root"] == _ROLE_ROOT_CAUSE
        assert role_map["enabler"] == _ROLE_ENABLER

    def test_propagator_and_victim_assigned(
        self, engine: FailureAttributionEngine,
    ) -> None:
        # root -> mid -> leaf
        nodes = [
            AttributionNode(node_id="root", node_type="contract_violation", ref="root"),
            AttributionNode(node_id="mid", node_type="event", ref="mid"),
            AttributionNode(node_id="leaf", node_type="event", ref="leaf"),
        ]
        edges = [
            AttributionEdge(source="root", target="mid", edge_type="propagates_to"),
            AttributionEdge(source="mid", target="leaf", edge_type="propagates_to"),
        ]
        result = engine.classify_nodes(nodes, edges, "root")

        role_map = {n.node_id: n.role for n in result}
        assert role_map["root"] == _ROLE_ROOT_CAUSE
        assert role_map["mid"] == _ROLE_PROPAGATOR
        assert role_map["leaf"] == _ROLE_VICTIM

    def test_mitigator_assigned_to_recovery_node(
        self, engine: FailureAttributionEngine,
    ) -> None:
        nodes = [
            AttributionNode(node_id="root", node_type="contract_violation", ref="root"),
            AttributionNode(node_id="recovery", node_type="recovery_action", ref="recovery"),
        ]
        edges = [
            AttributionEdge(source="recovery", target="root", edge_type="mitigates"),
        ]
        result = engine.classify_nodes(nodes, edges, "root")

        role_map = {n.node_id: n.role for n in result}
        assert role_map["recovery"] == _ROLE_MITIGATOR

    def test_unconnected_nodes_remain_unknown(
        self, engine: FailureAttributionEngine,
    ) -> None:
        nodes = [
            AttributionNode(node_id="root", node_type="contract_violation", ref="root"),
            AttributionNode(node_id="unrelated", node_type="event", ref="unrelated"),
        ]
        result = engine.classify_nodes(nodes, [], "root")

        role_map = {n.node_id: n.role for n in result}
        assert role_map["root"] == _ROLE_ROOT_CAUSE
        assert role_map["unrelated"] == _ROLE_UNKNOWN

    def test_classify_preserves_node_metadata(
        self, engine: FailureAttributionEngine,
    ) -> None:
        nodes = [
            AttributionNode(
                node_id="n1",
                node_type="contract_violation",
                ref="ref-abc",
            ),
        ]
        result = engine.classify_nodes(nodes, [], "n1")
        assert result[0].node_type == "contract_violation"
        assert result[0].ref == "ref-abc"


# ---------------------------------------------------------------------------
# Tests: select_root_cause
# ---------------------------------------------------------------------------


class TestSelectRootCause:
    """Tests for FailureAttributionEngine.select_root_cause."""

    def test_returns_empty_when_no_candidates(
        self, engine: FailureAttributionEngine,
    ) -> None:
        assert engine.select_root_cause([], [], []) == ""

    def test_prefers_earliest_without_counterfactuals(
        self, engine: FailureAttributionEngine,
    ) -> None:
        nodes = [
            AttributionNode(node_id="first", node_type="contract_violation", ref="first"),
            AttributionNode(node_id="second", node_type="contract_violation", ref="second"),
        ]
        # select_root_cause picks the first item in the candidates list
        result = engine.select_root_cause(["first", "second"], nodes, [])
        assert result == "first"

    def test_prefers_counterfactual_with_most_eliminations(
        self, engine: FailureAttributionEngine,
    ) -> None:
        nodes = [
            AttributionNode(node_id="a", node_type="contract_violation", ref="a"),
            AttributionNode(node_id="b", node_type="contract_violation", ref="b"),
        ]

        # Replay removing "b" has 0 remaining violations (eliminated all)
        # Replay removing "a" has 2 remaining violations
        replay_remove_b = ReplayResult(
            replay_id="r-b",
            entry_id="entry-1",
            mutations=[
                CounterfactualMutation(
                    mutation_id="m-b",
                    mutation_type="drop_event",
                    target_ref="b",
                ),
            ],
            contract_violations=[],  # 0 remaining
        )
        replay_remove_a = ReplayResult(
            replay_id="r-a",
            entry_id="entry-1",
            mutations=[
                CounterfactualMutation(
                    mutation_id="m-a",
                    mutation_type="drop_event",
                    target_ref="a",
                ),
            ],
            contract_violations=[
                _contract_violation(violation_id="v-x"),
                _contract_violation(violation_id="v-y"),
            ],
        )

        result = engine.select_root_cause(
            ["a", "b"], nodes, [], [replay_remove_a, replay_remove_b],
        )
        # "b" eliminated more violations (0 remaining vs 2 remaining)
        assert result == "b"

    def test_falls_back_to_earliest_when_counterfactuals_dont_match(
        self, engine: FailureAttributionEngine,
    ) -> None:
        nodes = [
            AttributionNode(node_id="x", node_type="contract_violation", ref="x"),
            AttributionNode(node_id="y", node_type="contract_violation", ref="y"),
        ]
        # Replay removes an unrelated node, so counterfactuals don't help
        replay = ReplayResult(
            replay_id="r-1",
            entry_id="entry-1",
            mutations=[
                CounterfactualMutation(
                    mutation_id="m-1",
                    mutation_type="drop_event",
                    target_ref="unrelated",
                ),
            ],
        )
        # Falls back to first candidate in list
        result = engine.select_root_cause(["x", "y"], nodes, [], [replay])
        assert result == "x"


# ---------------------------------------------------------------------------
# Tests: full attribute flow
# ---------------------------------------------------------------------------


class TestAttributeFlow:
    """End-to-end tests for FailureAttributionEngine.attribute."""

    def test_complete_attribution_case_structure(
        self, engine: FailureAttributionEngine,
    ) -> None:
        envelopes, violations, ids = _make_linear_causal_trace()

        case = engine.attribute(envelopes, violations)

        assert isinstance(case, AttributionCase)
        assert case.case_id.startswith("attr-")
        assert case.first_divergence == ids["v"]
        assert case.selected_root_cause == ids["v"]
        assert ids["v"] in case.root_cause_candidates
        assert len(case.nodes) == 4  # 3 envelopes + 1 violation
        assert len(case.edges) > 0
        assert case.confidence > 0.0
        assert case.failure_signature != ""

    def test_empty_violations_produce_empty_attribution(
        self, engine: FailureAttributionEngine,
    ) -> None:
        envelopes = [_envelope(event_seq=0), _envelope(event_seq=1)]
        case = engine.attribute(envelopes, [])

        assert case.first_divergence == ""
        assert case.selected_root_cause == ""
        assert case.root_cause_candidates == []
        assert case.confidence == 0.0

    def test_propagation_chain_populated(
        self, engine: FailureAttributionEngine,
    ) -> None:
        envelopes, violations, _ids = _make_linear_causal_trace()
        case = engine.attribute(envelopes, violations)

        # The propagation chain should start at the selected root cause
        assert case.propagation_chain[0] == case.selected_root_cause

    def test_multiple_violations_select_earliest_root_cause(
        self, engine: FailureAttributionEngine,
    ) -> None:
        id_1 = _uid("trace")
        id_2 = _uid("trace")
        id_3 = _uid("trace")

        envelopes = [
            _envelope(trace_id=id_1, event_seq=0),
            _envelope(trace_id=id_2, event_seq=3),
            _envelope(trace_id=id_3, event_seq=7),
        ]

        v_early = _contract_violation(violation_id="v-early", event_id=id_1)
        v_late = _contract_violation(violation_id="v-late", event_id=id_3)

        case = engine.attribute(envelopes, [v_late, v_early])

        assert case.first_divergence == "v-early"
        assert case.selected_root_cause == "v-early"

    def test_counterfactuals_influence_root_cause_selection(
        self, engine: FailureAttributionEngine,
    ) -> None:
        id_a = _uid("trace")
        id_b = _uid("trace")

        envelopes = [
            _envelope(trace_id=id_a, event_seq=0),
            _envelope(trace_id=id_b, event_seq=1),
        ]

        v_a = _contract_violation(violation_id="v-a", event_id=id_a)
        v_b = _contract_violation(violation_id="v-b", event_id=id_b)

        # Counterfactual: removing v-b eliminates everything
        cf = ReplayResult(
            replay_id="cf-1",
            entry_id="entry-1",
            mutations=[
                CounterfactualMutation(
                    mutation_id="m-1",
                    mutation_type="drop_event",
                    target_ref="v-b",
                ),
            ],
            contract_violations=[],
        )
        # Counterfactual: removing v-a still leaves violations
        cf2 = ReplayResult(
            replay_id="cf-2",
            entry_id="entry-1",
            mutations=[
                CounterfactualMutation(
                    mutation_id="m-2",
                    mutation_type="drop_event",
                    target_ref="v-a",
                ),
            ],
            contract_violations=[_contract_violation(), _contract_violation()],
        )

        case = engine.attribute(
            envelopes, [v_a, v_b], counterfactual_results=[cf, cf2],
        )

        # v-b should be selected because removing it eliminated all violations
        assert case.selected_root_cause == "v-b"
        # Confidence should be higher with counterfactuals
        assert case.confidence >= 0.8

    def test_fix_hints_collected_from_violations(
        self, engine: FailureAttributionEngine,
    ) -> None:
        envelopes = [_envelope(event_seq=0)]
        v = _contract_violation(remediation_hint="Check approval flow")
        case = engine.attribute(envelopes, [v])

        assert "Check approval flow" in case.fix_hints

    def test_evidence_refs_collected(
        self, engine: FailureAttributionEngine,
    ) -> None:
        receipt = _uid("receipt")
        env = _envelope(event_seq=0, receipt_ref=receipt, artifact_refs=["art-1"])
        v = _contract_violation()

        case = engine.attribute([env], [v])

        assert receipt in case.evidence_refs
        assert "art-1" in case.evidence_refs
        assert v.violation_id in case.evidence_refs

    def test_failure_signature_format(
        self, engine: FailureAttributionEngine,
    ) -> None:
        envelopes = [_envelope(event_seq=0)]
        v1 = _contract_violation(contract_id="approval.gating")
        v2 = _invariant_violation(invariant_id="state.transition")

        case = engine.attribute(envelopes, [v1, v2])

        assert "contract:approval.gating" in case.failure_signature
        assert "invariant:state.transition" in case.failure_signature

    def test_recovery_nodes_classified_as_mitigator(
        self, engine: FailureAttributionEngine,
    ) -> None:
        id_fail = _uid("trace")
        id_recovery = _uid("trace")

        envelopes = [
            _envelope(trace_id=id_fail, event_type="tool_call.start", event_seq=0),
            _envelope(
                trace_id=id_recovery,
                event_type="recovery.started",
                event_seq=1,
                causation_id=id_fail,
            ),
        ]
        v = _contract_violation(event_id=id_fail)

        case = engine.attribute(envelopes, [v])

        role_map = {n.node_id: n.role for n in case.nodes}
        assert role_map[id_recovery] == _ROLE_MITIGATOR
