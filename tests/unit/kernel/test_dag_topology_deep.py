"""Deep DAG topology tests for StepRecord dependency graphs.

Tests diamond, linear chain, parallel fan-out, cycle detection,
and join_strategy semantics across step dependency DAGs.
"""

from __future__ import annotations

import pytest

from hermit.kernel.task.models.records import StepRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step(
    step_id: str,
    task_id: str = "task_001",
    *,
    kind: str = "execute",
    status: str = "pending",
    depends_on: list[str] | None = None,
    join_strategy: str = "all_required",
    node_key: str | None = None,
) -> StepRecord:
    """Build a StepRecord with sensible defaults for topology tests."""
    return StepRecord(
        step_id=step_id,
        task_id=task_id,
        kind=kind,
        status=status,
        attempt=1,
        node_key=node_key or step_id,
        depends_on=list(depends_on or []),
        join_strategy=join_strategy,
    )


def _build_dag(steps: list[StepRecord]) -> dict[str, StepRecord]:
    """Index steps by step_id for lookup."""
    return {s.step_id: s for s in steps}


def _roots(dag: dict[str, StepRecord]) -> list[str]:
    """Return step_ids with no dependencies (entry points)."""
    return sorted(sid for sid, s in dag.items() if not s.depends_on)


def _leaves(dag: dict[str, StepRecord]) -> list[str]:
    """Return step_ids that no other step depends on (exit points)."""
    depended_on: set[str] = set()
    for s in dag.values():
        depended_on.update(s.depends_on)
    return sorted(sid for sid in dag if sid not in depended_on)


def _topo_sort(dag: dict[str, StepRecord]) -> list[str]:
    """Kahn's algorithm topological sort. Raises ValueError on cycle."""
    in_degree = {sid: len(dag[sid].depends_on) for sid in dag}
    queue = [sid for sid, d in in_degree.items() if d == 0]
    result: list[str] = []
    while queue:
        queue.sort()
        node = queue.pop(0)
        result.append(node)
        for sid, s in dag.items():
            if node in s.depends_on:
                in_degree[sid] -= 1
                if in_degree[sid] == 0:
                    queue.append(sid)
    if len(result) != len(dag):
        raise ValueError("Cycle detected in DAG")
    return result


def _ready_steps(
    dag: dict[str, StepRecord],
    completed: set[str],
    join_strategies: dict[str, str] | None = None,
) -> list[str]:
    """Return step_ids whose dependencies are satisfied.

    For 'all_required': all deps must be in completed.
    For 'any_sufficient': at least one dep must be in completed (or no deps).
    """
    strategies = join_strategies or {}
    ready: list[str] = []
    for sid, step in dag.items():
        if sid in completed:
            continue
        strategy = strategies.get(sid, step.join_strategy)
        if not step.depends_on:
            ready.append(sid)
        elif strategy == "all_required":
            if all(d in completed for d in step.depends_on):
                ready.append(sid)
        elif strategy == "any_sufficient" and any(d in completed for d in step.depends_on):
            ready.append(sid)
    return sorted(ready)


# ---------------------------------------------------------------------------
# Diamond DAG: A → B, A → C, B → D, C → D
# ---------------------------------------------------------------------------


class TestDiamondDag:
    """Test diamond topology: A fans out to B and C, which converge on D."""

    @pytest.fixture()
    def diamond(self) -> dict[str, StepRecord]:
        return _build_dag(
            [
                _step("A"),
                _step("B", depends_on=["A"]),
                _step("C", depends_on=["A"]),
                _step("D", depends_on=["B", "C"]),
            ]
        )

    def test_root_is_a(self, diamond: dict[str, StepRecord]) -> None:
        assert _roots(diamond) == ["A"]

    def test_leaf_is_d(self, diamond: dict[str, StepRecord]) -> None:
        assert _leaves(diamond) == ["D"]

    def test_topo_order_a_before_d(self, diamond: dict[str, StepRecord]) -> None:
        order = _topo_sort(diamond)
        assert order.index("A") < order.index("B")
        assert order.index("A") < order.index("C")
        assert order.index("B") < order.index("D")
        assert order.index("C") < order.index("D")

    def test_initial_ready_only_a(self, diamond: dict[str, StepRecord]) -> None:
        assert _ready_steps(diamond, set()) == ["A"]

    def test_after_a_ready_b_and_c(self, diamond: dict[str, StepRecord]) -> None:
        assert _ready_steps(diamond, {"A"}) == ["B", "C"]

    def test_after_b_only_d_not_ready(self, diamond: dict[str, StepRecord]) -> None:
        """D requires both B and C (all_required), so only B done is not enough."""
        ready = _ready_steps(diamond, {"A", "B"})
        assert "D" not in ready
        assert "C" in ready

    def test_after_b_and_c_d_ready(self, diamond: dict[str, StepRecord]) -> None:
        assert _ready_steps(diamond, {"A", "B", "C"}) == ["D"]

    def test_all_completed(self, diamond: dict[str, StepRecord]) -> None:
        assert _ready_steps(diamond, {"A", "B", "C", "D"}) == []

    def test_d_has_two_dependencies(self, diamond: dict[str, StepRecord]) -> None:
        assert len(diamond["D"].depends_on) == 2
        assert set(diamond["D"].depends_on) == {"B", "C"}

    def test_step_count(self, diamond: dict[str, StepRecord]) -> None:
        assert len(diamond) == 4


# ---------------------------------------------------------------------------
# Linear chain: A → B → C → D
# ---------------------------------------------------------------------------


class TestLinearChain:
    """Test sequential dependency chain."""

    @pytest.fixture()
    def chain(self) -> dict[str, StepRecord]:
        return _build_dag(
            [
                _step("A"),
                _step("B", depends_on=["A"]),
                _step("C", depends_on=["B"]),
                _step("D", depends_on=["C"]),
            ]
        )

    def test_root_is_a(self, chain: dict[str, StepRecord]) -> None:
        assert _roots(chain) == ["A"]

    def test_leaf_is_d(self, chain: dict[str, StepRecord]) -> None:
        assert _leaves(chain) == ["D"]

    def test_topo_order_is_linear(self, chain: dict[str, StepRecord]) -> None:
        assert _topo_sort(chain) == ["A", "B", "C", "D"]

    def test_only_one_ready_at_a_time(self, chain: dict[str, StepRecord]) -> None:
        assert _ready_steps(chain, set()) == ["A"]
        assert _ready_steps(chain, {"A"}) == ["B"]
        assert _ready_steps(chain, {"A", "B"}) == ["C"]
        assert _ready_steps(chain, {"A", "B", "C"}) == ["D"]

    def test_skipping_middle_blocks_downstream(self, chain: dict[str, StepRecord]) -> None:
        """If A is done but B is not, C should not be ready."""
        assert _ready_steps(chain, {"A"}) == ["B"]
        assert "C" not in _ready_steps(chain, {"A"})

    def test_each_step_has_exactly_one_dep(self, chain: dict[str, StepRecord]) -> None:
        assert len(chain["B"].depends_on) == 1
        assert len(chain["C"].depends_on) == 1
        assert len(chain["D"].depends_on) == 1


# ---------------------------------------------------------------------------
# Parallel fan-out: A, B, C (no dependencies)
# ---------------------------------------------------------------------------


class TestParallelFanOut:
    """Test steps with no inter-dependencies that can run simultaneously."""

    @pytest.fixture()
    def parallel(self) -> dict[str, StepRecord]:
        return _build_dag(
            [
                _step("A"),
                _step("B"),
                _step("C"),
            ]
        )

    def test_all_are_roots(self, parallel: dict[str, StepRecord]) -> None:
        assert _roots(parallel) == ["A", "B", "C"]

    def test_all_are_leaves(self, parallel: dict[str, StepRecord]) -> None:
        assert _leaves(parallel) == ["A", "B", "C"]

    def test_all_ready_initially(self, parallel: dict[str, StepRecord]) -> None:
        assert _ready_steps(parallel, set()) == ["A", "B", "C"]

    def test_completing_one_does_not_affect_others(self, parallel: dict[str, StepRecord]) -> None:
        assert _ready_steps(parallel, {"A"}) == ["B", "C"]
        assert _ready_steps(parallel, {"A", "B"}) == ["C"]

    def test_topo_sort_all_valid(self, parallel: dict[str, StepRecord]) -> None:
        order = _topo_sort(parallel)
        assert set(order) == {"A", "B", "C"}

    def test_no_dependencies(self, parallel: dict[str, StepRecord]) -> None:
        for step in parallel.values():
            assert step.depends_on == []


# ---------------------------------------------------------------------------
# Cycle detection: A → B → C → A
# ---------------------------------------------------------------------------


class TestCycleDetection:
    """Test that circular dependencies are detected and rejected."""

    @pytest.fixture()
    def cycle(self) -> dict[str, StepRecord]:
        return _build_dag(
            [
                _step("A", depends_on=["C"]),
                _step("B", depends_on=["A"]),
                _step("C", depends_on=["B"]),
            ]
        )

    def test_topo_sort_raises_on_cycle(self, cycle: dict[str, StepRecord]) -> None:
        with pytest.raises(ValueError, match="Cycle detected"):
            _topo_sort(cycle)

    def test_no_roots_in_cycle(self, cycle: dict[str, StepRecord]) -> None:
        """A pure cycle has no root nodes (all have dependencies)."""
        assert _roots(cycle) == []

    def test_no_ready_steps_in_cycle(self, cycle: dict[str, StepRecord]) -> None:
        """Nothing can start in a pure cycle since all steps have unmet deps."""
        assert _ready_steps(cycle, set()) == []

    def test_self_cycle_detected(self) -> None:
        """A step depending on itself is a trivial cycle."""
        dag = _build_dag([_step("A", depends_on=["A"])])
        with pytest.raises(ValueError, match="Cycle detected"):
            _topo_sort(dag)

    def test_two_node_cycle(self) -> None:
        dag = _build_dag(
            [
                _step("X", depends_on=["Y"]),
                _step("Y", depends_on=["X"]),
            ]
        )
        with pytest.raises(ValueError, match="Cycle detected"):
            _topo_sort(dag)


# ---------------------------------------------------------------------------
# Join strategy: all_required vs any_sufficient
# ---------------------------------------------------------------------------


class TestJoinStrategy:
    """Test join_strategy semantics on convergence nodes."""

    @pytest.fixture()
    def converge_dag(self) -> dict[str, StepRecord]:
        """A, B, C → D where D can use different join strategies."""
        return _build_dag(
            [
                _step("A"),
                _step("B"),
                _step("C"),
                _step("D", depends_on=["A", "B", "C"], join_strategy="all_required"),
            ]
        )

    def test_all_required_needs_all_deps(self, converge_dag: dict[str, StepRecord]) -> None:
        assert "D" not in _ready_steps(converge_dag, {"A"})
        assert "D" not in _ready_steps(converge_dag, {"A", "B"})
        assert "D" in _ready_steps(converge_dag, {"A", "B", "C"})

    def test_any_sufficient_needs_one_dep(self, converge_dag: dict[str, StepRecord]) -> None:
        """Override D's join_strategy to any_sufficient at evaluation time."""
        # With any_sufficient, completing just A should make D ready
        ready = _ready_steps(
            converge_dag,
            {"A"},
            join_strategies={"D": "any_sufficient"},
        )
        assert "D" in ready

    def test_any_sufficient_zero_deps_still_ready(self) -> None:
        """A step with no deps is always ready regardless of strategy."""
        dag = _build_dag([_step("X", join_strategy="any_sufficient")])
        assert _ready_steps(dag, set()) == ["X"]

    def test_all_required_is_default(self) -> None:
        step = StepRecord(
            step_id="s1",
            task_id="t1",
            kind="execute",
            status="pending",
            attempt=1,
        )
        assert step.join_strategy == "all_required"

    def test_step_record_preserves_join_strategy(self) -> None:
        step = _step("X", join_strategy="any_sufficient")
        assert step.join_strategy == "any_sufficient"

    def test_any_sufficient_with_none_completed(self, converge_dag: dict[str, StepRecord]) -> None:
        """any_sufficient still needs at least one dep completed."""
        ready = _ready_steps(
            converge_dag,
            set(),
            join_strategies={"D": "any_sufficient"},
        )
        # A, B, C have no deps so they're ready. D needs at least one.
        assert "D" not in ready


# ---------------------------------------------------------------------------
# Complex topologies
# ---------------------------------------------------------------------------


class TestComplexTopology:
    """Test wider and deeper DAG structures."""

    def test_wide_fan_in(self) -> None:
        """10 independent steps converging into a single join step."""
        sources = [_step(f"S{i}") for i in range(10)]
        join = _step("JOIN", depends_on=[f"S{i}" for i in range(10)])
        dag = _build_dag(sources + [join])

        assert len(_roots(dag)) == 10
        assert _leaves(dag) == ["JOIN"]
        assert len(_ready_steps(dag, set())) == 10
        assert "JOIN" not in _ready_steps(dag, {f"S{i}" for i in range(9)})
        assert "JOIN" in _ready_steps(dag, {f"S{i}" for i in range(10)})

    def test_deep_chain_20_steps(self) -> None:
        """A 20-step linear chain."""
        steps = [
            _step(f"STEP_{i}", depends_on=[f"STEP_{i - 1}"] if i > 0 else None) for i in range(20)
        ]
        dag = _build_dag(steps)

        order = _topo_sort(dag)
        assert order == [f"STEP_{i}" for i in range(20)]
        assert _roots(dag) == ["STEP_0"]
        assert _leaves(dag) == ["STEP_19"]

    def test_diamond_with_extra_branch(self) -> None:
        """Diamond A→B,C→D plus extra E→D."""
        dag = _build_dag(
            [
                _step("A"),
                _step("B", depends_on=["A"]),
                _step("C", depends_on=["A"]),
                _step("E"),
                _step("D", depends_on=["B", "C", "E"]),
            ]
        )

        assert sorted(_roots(dag)) == ["A", "E"]
        assert _leaves(dag) == ["D"]
        assert "D" not in _ready_steps(dag, {"A", "B", "C"})
        assert "D" in _ready_steps(dag, {"A", "B", "C", "E"})

    def test_multi_exit_dag(self) -> None:
        """DAG with multiple exit points (multiple leaves)."""
        dag = _build_dag(
            [
                _step("ROOT"),
                _step("L1", depends_on=["ROOT"]),
                _step("L2", depends_on=["ROOT"]),
                _step("L3", depends_on=["ROOT"]),
            ]
        )

        assert _roots(dag) == ["ROOT"]
        assert sorted(_leaves(dag)) == ["L1", "L2", "L3"]


# ---------------------------------------------------------------------------
# StepRecord field integrity
# ---------------------------------------------------------------------------


class TestStepRecordFields:
    """Verify StepRecord dataclass field behavior for DAG-relevant fields."""

    def test_default_depends_on_is_empty(self) -> None:
        s = StepRecord(step_id="s", task_id="t", kind="k", status="pending", attempt=1)
        assert s.depends_on == []

    def test_depends_on_is_not_shared(self) -> None:
        """Each StepRecord should have its own depends_on list."""
        s1 = StepRecord(step_id="s1", task_id="t", kind="k", status="pending", attempt=1)
        s2 = StepRecord(step_id="s2", task_id="t", kind="k", status="pending", attempt=1)
        s1.depends_on.append("x")
        assert s2.depends_on == []

    def test_input_bindings_default_empty(self) -> None:
        s = StepRecord(step_id="s", task_id="t", kind="k", status="pending", attempt=1)
        assert s.input_bindings == {}

    def test_max_attempts_default(self) -> None:
        s = StepRecord(step_id="s", task_id="t", kind="k", status="pending", attempt=1)
        assert s.max_attempts == 1

    def test_node_key_optional(self) -> None:
        s = StepRecord(step_id="s", task_id="t", kind="k", status="pending", attempt=1)
        assert s.node_key is None
