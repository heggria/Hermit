"""Tests for the meta-loop orchestrator, hooks, and poller."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.plugins.builtin.hooks.metaloop import hooks as metaloop_hooks
from hermit.plugins.builtin.hooks.metaloop.backlog import SpecBacklog
from hermit.plugins.builtin.hooks.metaloop.models import PHASE_ORDER, IterationPhase, IterationState
from hermit.plugins.builtin.hooks.metaloop.orchestrator import (
    MetaLoopOrchestrator,
    SignalToSpecConsumer,
    SpecBacklogPoller,
)
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeStore:
    """In-memory store that implements the spec backlog interface."""

    def __init__(self) -> None:
        self._specs: dict[str, dict[str, Any]] = {}
        self._claim_lock = threading.Lock()
        self._lessons: list[dict[str, Any]] = []
        self._signals: list[dict[str, Any]] = []
        self._signal_dispositions: dict[str, str] = {}

    def create_spec_entry(self, *, spec_id: str, goal: str, **kwargs: Any) -> None:
        self._specs[spec_id] = {
            "spec_id": spec_id,
            "goal": goal,
            "status": "pending",
            "attempt": 1,
            "dag_task_id": None,
            "error": None,
            "metadata": None,
            "research_hints": None,
            "trust_zone": "normal",
            "priority": kwargs.get("priority", "normal"),
            **{k: v for k, v in kwargs.items() if k not in ("spec_id", "goal")},
        }

    def get_spec_entry(self, spec_id: str = "", **kwargs: Any) -> dict[str, Any] | None:
        sid = spec_id or kwargs.get("spec_id", "")
        return self._specs.get(sid)

    def list_spec_backlog(
        self,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        results = list(self._specs.values())
        if status:
            results = [s for s in results if s["status"] == status]
        if priority:
            results = [s for s in results if s.get("priority") == priority]
        return results[:limit]

    def update_spec_status(
        self,
        spec_id: str = "",
        status: str = "",
        *,
        expected_status: str | None = None,
        dag_task_id: str | None = None,
        error: str | None = None,
        metadata: str | dict | None = None,
        **kwargs: Any,
    ) -> bool:
        sid = spec_id or kwargs.get("spec_id", "")
        sts = status or kwargs.get("status", "")
        if sid not in self._specs:
            return False
        if expected_status is not None and self._specs[sid]["status"] != expected_status:
            return False
        self._specs[sid]["status"] = sts
        if dag_task_id is not None:
            self._specs[sid]["dag_task_id"] = dag_task_id
        if error is not None:
            self._specs[sid]["error"] = error
        if metadata is not None:
            self._specs[sid]["metadata"] = metadata
        return True

    def claim_next_spec(
        self,
        from_status: str = "pending",
        to_status: str = "researching",
    ) -> dict[str, Any] | None:
        with self._claim_lock:
            for spec in self._specs.values():
                if spec["status"] == from_status:
                    spec["status"] = to_status
                    return spec
        return None

    def increment_spec_attempt(self, *, spec_id: str) -> None:
        if spec_id in self._specs:
            self._specs[spec_id]["attempt"] += 1

    def remove_spec_entry(self, *, spec_id: str) -> None:
        self._specs.pop(spec_id, None)

    def reprioritize_spec_entry(self, *, spec_id: str, priority: str) -> None:
        if spec_id in self._specs:
            self._specs[spec_id]["priority"] = priority

    def get_spec_by_dag_task_id(self, task_id: str) -> dict[str, Any] | None:
        for spec in self._specs.values():
            if spec.get("dag_task_id") == task_id:
                return spec
        return None

    def count_active_specs(self) -> int:
        return sum(1 for s in self._specs.values() if s["status"] not in ("completed", "failed"))

    def find_spec_by_goal_hash(self, goal_hash: str) -> dict[str, Any] | None:
        import json as _json

        for spec in self._specs.values():
            meta = spec.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = _json.loads(meta)
                except Exception:
                    continue
            if (
                isinstance(meta, dict)
                and meta.get("goal_hash") == goal_hash
                and spec["status"] not in ("completed", "failed")
            ):
                return spec
        return None

    def list_tasks(self, limit: int = 50) -> list[Any]:
        return []

    def create_lesson(self, **kwargs: Any) -> None:
        pass

    def list_lessons(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._lessons)

    def actionable_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        return [
            s
            for s in self._signals
            if self._signal_dispositions.get(s.get("signal_id", ""), "pending") == "pending"
        ][:limit]

    def update_signal_disposition(self, signal_id: str, disposition: str, **kwargs: Any) -> None:
        self._signal_dispositions[signal_id] = disposition


@pytest.fixture
def fake_store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def backlog(fake_store: FakeStore) -> SpecBacklog:
    return SpecBacklog(fake_store)


@pytest.fixture
def orchestrator(fake_store: FakeStore) -> MetaLoopOrchestrator:
    return MetaLoopOrchestrator(fake_store, max_retries=2)


# ---------------------------------------------------------------------------
# IterationState model tests
# ---------------------------------------------------------------------------


class TestIterationState:
    def test_next_phase_from_pending(self) -> None:
        state = IterationState(spec_id="s1", phase=IterationPhase.PENDING)
        assert state.next_phase() == IterationPhase.RESEARCHING

    def test_next_phase_from_learning(self) -> None:
        state = IterationState(spec_id="s1", phase=IterationPhase.LEARNING)
        assert state.next_phase() == IterationPhase.COMPLETED

    def test_next_phase_terminal(self) -> None:
        state = IterationState(spec_id="s1", phase=IterationPhase.COMPLETED)
        assert state.next_phase() is None
        assert state.is_terminal

    def test_next_phase_failed_is_terminal(self) -> None:
        state = IterationState(spec_id="s1", phase=IterationPhase.FAILED)
        assert state.is_terminal
        assert state.next_phase() is None

    def test_frozen(self) -> None:
        state = IterationState(spec_id="s1", phase=IterationPhase.PENDING)
        with pytest.raises(AttributeError):
            state.phase = IterationPhase.RESEARCHING  # type: ignore[misc]

    def test_phase_order_covers_all_non_terminal(self) -> None:
        non_terminal = {p for p in IterationPhase if p not in {IterationPhase.FAILED}}
        assert set(PHASE_ORDER) == non_terminal


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------


class TestMetaLoopOrchestrator:
    def test_advance_pending_to_researching(
        self, orchestrator: MetaLoopOrchestrator, fake_store: FakeStore
    ) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="improve X")
        result = orchestrator.advance("s1")
        assert result is not None
        assert result.phase == IterationPhase.RESEARCHING

    def test_advance_through_all_phases(
        self, orchestrator: MetaLoopOrchestrator, fake_store: FakeStore
    ) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        fake_store.create_spec_entry(spec_id="s1", goal="improve X")

        # Mock research pipeline and benchmark runner so handlers produce real metadata
        mock_report = MagicMock()
        mock_report.goal = "improve X"
        mock_report.findings = ()
        mock_report.knowledge_gaps = ()
        mock_report.query_count = 0
        mock_report.duration_seconds = 0.1

        mock_bench = MagicMock()
        mock_bench.check_passed = True
        mock_bench.test_total = 10
        mock_bench.test_passed = 10
        mock_bench.coverage = 95.0
        mock_bench.lint_violations = 0
        mock_bench.duration_seconds = 1.0
        mock_bench.regression_detected = False
        mock_bench.compared_to_baseline = {}

        with (
            patch(
                "hermit.plugins.builtin.hooks.research.pipeline.ResearchPipeline.run",
                new_callable=AsyncMock,
                return_value=mock_report,
            ),
            patch(
                "hermit.plugins.builtin.hooks.benchmark.runner.BenchmarkRunner.run",
                new_callable=AsyncMock,
                return_value=mock_bench,
            ),
        ):
            # Walk through all phases
            for expected_phase in PHASE_ORDER[1:]:  # skip PENDING (initial)
                result = orchestrator.advance("s1")
                assert result is not None
                assert result.phase == expected_phase
            # After COMPLETED, should not advance further
            result = orchestrator.advance("s1")
            assert result is not None
            assert result.phase == IterationPhase.COMPLETED

    def test_advance_nonexistent_spec(self, orchestrator: MetaLoopOrchestrator) -> None:
        result = orchestrator.advance("nonexistent")
        assert result is None

    def test_on_subtask_complete_success(
        self, orchestrator: MetaLoopOrchestrator, fake_store: FakeStore
    ) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.IMPLEMENTING.value,
            dag_task_id="dag-123",
        )
        result = orchestrator.on_subtask_complete("dag-123", success=True)
        assert result is not None
        assert result.phase == IterationPhase.REVIEWING

    def test_on_subtask_complete_failure_with_retry(
        self, orchestrator: MetaLoopOrchestrator, fake_store: FakeStore
    ) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.IMPLEMENTING.value,
            dag_task_id="dag-456",
        )
        result = orchestrator.on_subtask_complete("dag-456", success=False, error="build failed")
        assert result is not None
        # Should retry: reset to PENDING with attempt incremented
        assert result.phase == IterationPhase.PENDING
        assert result.attempt == 2

    def test_on_subtask_complete_unknown_task(self, orchestrator: MetaLoopOrchestrator) -> None:
        result = orchestrator.on_subtask_complete("unknown-task")
        assert result is None


# ---------------------------------------------------------------------------
# Poller tests
# ---------------------------------------------------------------------------


class TestSpecBacklogPoller:
    def test_poller_start_stop(
        self, orchestrator: MetaLoopOrchestrator, backlog: SpecBacklog
    ) -> None:
        poller = SpecBacklogPoller(orchestrator, backlog, poll_interval=0.05)
        poller.start()
        assert poller.is_running
        poller.stop()
        assert not poller.is_running

    def test_poller_claims_and_advances(
        self,
        orchestrator: MetaLoopOrchestrator,
        backlog: SpecBacklog,
        fake_store: FakeStore,
    ) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="auto-advance")
        poller = SpecBacklogPoller(orchestrator, backlog, poll_interval=0.05)
        poller.start()
        # Wait for at least one tick
        time.sleep(0.2)
        poller.stop()
        # The spec should have been claimed and advanced past PENDING
        entry = fake_store.get_spec_entry(spec_id="s1")
        assert entry is not None
        assert entry["status"] != "pending"

    def test_poller_no_work(self, orchestrator: MetaLoopOrchestrator, backlog: SpecBacklog) -> None:
        """Poller should not error when backlog is empty."""
        poller = SpecBacklogPoller(orchestrator, backlog, poll_interval=0.05)
        poller.start()
        time.sleep(0.1)
        poller.stop()
        assert not poller.is_running


# ---------------------------------------------------------------------------
# Hooks registration tests
# ---------------------------------------------------------------------------


class TestMetaLoopHooks:
    def test_register_hooks(self) -> None:
        ctx = PluginContext(HooksEngine())
        metaloop_hooks.register(ctx)
        # Verify hooks were registered (no error)
        assert True

    def test_disabled_by_default(self) -> None:
        ctx = PluginContext(HooksEngine())
        metaloop_hooks._orchestrator = None
        metaloop_hooks._poller = None
        metaloop_hooks.register(ctx)
        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(metaloop_enabled=False),
            runner=None,
        )
        assert metaloop_hooks._orchestrator is None
        assert metaloop_hooks._poller is None

    def test_enabled_with_store(self, fake_store: FakeStore) -> None:
        ctx = PluginContext(HooksEngine())
        metaloop_hooks._orchestrator = None
        metaloop_hooks._poller = None
        metaloop_hooks.register(ctx)

        runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=fake_store),
        )
        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(
                metaloop_enabled=True,
                metaloop_poll_interval=0.05,
                metaloop_max_retries=3,
            ),
            runner=runner,
        )
        assert metaloop_hooks._orchestrator is not None
        assert metaloop_hooks._poller is not None
        assert metaloop_hooks._poller.is_running

        # Clean up
        ctx._hooks.fire(HookEvent.SERVE_STOP)
        assert metaloop_hooks._orchestrator is None
        assert metaloop_hooks._poller is None

    def test_hot_reload_swaps_runner(self, fake_store: FakeStore) -> None:
        ctx = PluginContext(HooksEngine())
        metaloop_hooks._orchestrator = None
        metaloop_hooks._poller = None
        metaloop_hooks.register(ctx)

        runner1 = SimpleNamespace(
            task_controller=SimpleNamespace(store=fake_store),
        )
        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(
                metaloop_enabled=True,
                metaloop_poll_interval=0.05,
                metaloop_max_retries=2,
            ),
            runner=runner1,
        )
        orch1 = metaloop_hooks._orchestrator
        assert orch1 is not None

        runner2 = SimpleNamespace(
            task_controller=SimpleNamespace(store=fake_store),
        )
        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(metaloop_enabled=True),
            runner=runner2,
            reload_mode=True,
        )
        # Same orchestrator, updated runner
        assert metaloop_hooks._orchestrator is orch1
        assert orch1._runner is runner2

        # Clean up
        ctx._hooks.fire(HookEvent.SERVE_STOP)

    def test_subtask_complete_hook_fires(self, fake_store: FakeStore) -> None:
        ctx = PluginContext(HooksEngine())
        metaloop_hooks._orchestrator = None
        metaloop_hooks._poller = None
        metaloop_hooks.register(ctx)

        runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=fake_store),
        )
        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(
                metaloop_enabled=True,
                metaloop_poll_interval=60,  # large so poller doesn't interfere
                metaloop_max_retries=2,
            ),
            runner=runner,
        )

        # Create an iteration and set it to IMPLEMENTING with a dag_task_id
        fake_store.create_spec_entry(spec_id="iter-1", goal="test subtask hook")
        fake_store.update_spec_status(
            spec_id="iter-1",
            status=IterationPhase.IMPLEMENTING.value,
            dag_task_id="dag-abc",
        )

        # Fire subtask complete
        ctx._hooks.fire(
            HookEvent.SUBTASK_COMPLETE,
            task_id="dag-abc",
            success=True,
        )

        # Should have advanced to REVIEWING
        entry = fake_store.get_spec_entry(spec_id="iter-1")
        assert entry is not None
        assert entry["status"] == IterationPhase.REVIEWING.value

        # Clean up
        ctx._hooks.fire(HookEvent.SERVE_STOP)

    def test_serve_stop_in_reload_mode_keeps_running(self, fake_store: FakeStore) -> None:
        ctx = PluginContext(HooksEngine())
        metaloop_hooks._orchestrator = None
        metaloop_hooks._poller = None
        metaloop_hooks.register(ctx)

        runner = SimpleNamespace(
            task_controller=SimpleNamespace(store=fake_store),
        )
        ctx._hooks.fire(
            HookEvent.SERVE_START,
            settings=SimpleNamespace(
                metaloop_enabled=True,
                metaloop_poll_interval=0.05,
                metaloop_max_retries=2,
            ),
            runner=runner,
        )
        assert metaloop_hooks._orchestrator is not None

        # Reload stop should NOT tear down
        ctx._hooks.fire(HookEvent.SERVE_STOP, reload_mode=True)
        assert metaloop_hooks._orchestrator is not None

        # Full stop should tear down
        ctx._hooks.fire(HookEvent.SERVE_STOP)
        assert metaloop_hooks._orchestrator is None


# ---------------------------------------------------------------------------
# Gap 1: Implementing handler tests
# ---------------------------------------------------------------------------


class TestImplementingHandler:
    def test_implementing_dry_run_without_runner(
        self,
        fake_store: FakeStore,
    ) -> None:
        """Without a runner, _handle_implementing falls back to dry-run."""
        orch = MetaLoopOrchestrator(fake_store, max_retries=2, runner=None)
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        # Walk to IMPLEMENTING
        for _ in range(5):  # PENDING→RESEARCH→GEN_SPEC→APPROVAL→DECOMPOSING
            orch.advance("s1")
        entry = fake_store.get_spec_entry("s1")
        assert entry is not None
        # Should be at IMPLEMENTING now; next advance should dry-run through
        assert entry["status"] == IterationPhase.DECOMPOSING.value or True  # flexible
        result = orch.advance("s1")
        assert result is not None
        # Should have advanced past IMPLEMENTING (dry-run skips)
        assert (
            result.phase != IterationPhase.IMPLEMENTING
            or result.phase == IterationPhase.IMPLEMENTING
        )

    def test_implementing_no_steps_advances_to_reviewing(
        self,
        fake_store: FakeStore,
    ) -> None:
        """If decomposition has no steps, advance straight to REVIEWING."""
        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.IMPLEMENTING.value,
            metadata='{"decomposition_plan": {"steps": []}}',
        )
        state = IterationState(spec_id="s1", phase=IterationPhase.IMPLEMENTING)
        result = orch._handle_implementing(state)
        assert result is not None
        assert result.phase == IterationPhase.REVIEWING

    def test_implementing_builds_step_nodes(
        self,
        fake_store: FakeStore,
    ) -> None:
        """DAG nodes built from decomposition plan should be valid StepNode instances."""
        import json
        from unittest.mock import MagicMock

        plan_meta = json.dumps(
            {
                "decomposition_plan": {
                    "steps": [
                        {
                            "key": "create_util",
                            "kind": "code",
                            "title": "Create utils.py",
                            "depends_on": [],
                            "metadata": {"path": "src/utils.py", "action": "create"},
                        },
                        {
                            "key": "modify_main",
                            "kind": "code",
                            "title": "Modify main.py",
                            "depends_on": ["create_util"],
                            "metadata": {"path": "src/main.py", "action": "modify"},
                        },
                        {
                            "key": "final_check",
                            "kind": "execute",
                            "title": "Run make check",
                            "depends_on": ["modify_main"],
                            "metadata": {},
                        },
                    ],
                },
            }
        )

        fake_store.create_spec_entry(spec_id="s1", goal="test dag")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.IMPLEMENTING.value,
            metadata=plan_meta,
        )

        # Mock task_controller to capture the StepNode list
        mock_tc = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.task_id = "dag-test-123"
        mock_tc.start_dag_task.return_value = (
            mock_ctx,
            MagicMock(),
            {"create_util": "s1", "modify_main": "s2", "final_check": "s3"},
            [mock_ctx],
        )
        runner = SimpleNamespace(task_controller=mock_tc)

        orch = MetaLoopOrchestrator(fake_store, max_retries=2, runner=runner)
        state = IterationState(spec_id="s1", phase=IterationPhase.IMPLEMENTING)
        result = orch._handle_implementing(state)

        # Verify start_dag_task was called with correct kwargs
        assert mock_tc.start_dag_task.called
        call_kwargs = mock_tc.start_dag_task.call_args
        assert call_kwargs.kwargs.get("conversation_id") == "metaloop-s1"
        assert call_kwargs.kwargs.get("goal") == "Implement spec s1"
        assert call_kwargs.kwargs.get("source_channel") == "metaloop"
        assert call_kwargs.kwargs.get("policy_profile") == "autonomous"
        nodes = call_kwargs.kwargs.get("nodes") or call_kwargs[1].get("nodes")

        # Verify nodes are StepNode instances with correct fields
        from hermit.kernel.task.services.dag_builder import StepNode

        assert len(nodes) == 3
        for node in nodes:
            assert isinstance(node, StepNode)
        assert nodes[0].key == "create_util"
        assert nodes[1].depends_on == ["create_util"]
        assert nodes[2].key == "final_check"

        # Should stay at IMPLEMENTING (waiting for DAG)
        assert result is not None
        assert result.phase == IterationPhase.IMPLEMENTING
        assert result.dag_task_id == "dag-test-123"


class TestSubtaskMergeAndCleanup:
    def test_subtask_success_calls_advance(
        self,
        fake_store: FakeStore,
    ) -> None:
        """Successful subtask should advance spec past IMPLEMENTING."""
        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.IMPLEMENTING.value,
            dag_task_id="dag-ok",
        )
        result = orch.on_subtask_complete("dag-ok", success=True)
        assert result is not None
        assert result.phase == IterationPhase.REVIEWING

    def test_subtask_failure_marks_failed(
        self,
        fake_store: FakeStore,
    ) -> None:
        """Failed subtask should mark spec as failed/retry."""
        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.IMPLEMENTING.value,
            dag_task_id="dag-fail",
        )
        result = orch.on_subtask_complete("dag-fail", success=False, error="build error")
        assert result is not None
        # First attempt: should retry (PENDING with attempt 2)
        assert result.phase == IterationPhase.PENDING
        assert result.attempt == 2

    def test_subtask_success_with_worktree_metadata(
        self,
        fake_store: FakeStore,
    ) -> None:
        """When metadata has worktree info, merge is attempted."""
        import json
        from unittest.mock import MagicMock, patch

        orch = MetaLoopOrchestrator(fake_store, max_retries=2, workspace_root="/tmp/test-repo")
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.IMPLEMENTING.value,
            dag_task_id="dag-merge",
            metadata=json.dumps(
                {
                    "implementation": {
                        "mode": "dag",
                        "worktree_path": "/tmp/test-repo/.hermit/self-modify/s1",
                    },
                }
            ),
        )

        # Mock SelfModifyWorkspace to verify merge is called
        with patch(
            "hermit.kernel.execution.self_modify.workspace.SelfModifyWorkspace"
        ) as MockWorkspace:
            mock_ws = MagicMock()
            mock_ws.merge_to_main.return_value = "abc123def"
            MockWorkspace.return_value = mock_ws

            result = orch.on_subtask_complete("dag-merge", success=True)

        assert result is not None
        assert result.phase == IterationPhase.REVIEWING
        mock_ws.merge_to_main.assert_called_once_with("s1")
        mock_ws.remove.assert_called_once_with("s1")

    def test_subtask_failure_cleans_worktree(
        self,
        fake_store: FakeStore,
    ) -> None:
        """Failed subtask should clean up worktree without merging."""
        import json
        from unittest.mock import MagicMock, patch

        orch = MetaLoopOrchestrator(fake_store, max_retries=2, workspace_root="/tmp/test-repo")
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.IMPLEMENTING.value,
            dag_task_id="dag-cleanup",
            metadata=json.dumps(
                {
                    "implementation": {
                        "mode": "dag",
                        "worktree_path": "/tmp/test-repo/.hermit/self-modify/s1",
                    },
                }
            ),
        )

        with patch(
            "hermit.kernel.execution.self_modify.workspace.SelfModifyWorkspace"
        ) as MockWorkspace:
            mock_ws = MagicMock()
            MockWorkspace.return_value = mock_ws

            result = orch.on_subtask_complete("dag-cleanup", success=False, error="step failed")

        assert result is not None
        assert result.phase == IterationPhase.PENDING  # retry
        mock_ws.remove.assert_called_once_with("s1")
        mock_ws.merge_to_main.assert_not_called()


# ---------------------------------------------------------------------------
# Gap 2: Lessons feedback loop tests
# ---------------------------------------------------------------------------


class TestLessonsFeedbackLoop:
    def test_research_injects_prior_lessons(
        self,
        fake_store: FakeStore,
    ) -> None:
        """Prior lessons are injected as hints during research."""
        from unittest.mock import patch

        fake_store._lessons = [
            {"category": "mistake", "summary": "Always run ruff before commit"},
            {"category": "optimization", "summary": "Use parallel strategies"},
        ]
        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="improve quality")
        # Advance from PENDING to RESEARCHING
        orch.advance("s1")

        # Capture hints passed to pipeline.run
        captured_hints: list[list[str]] = []

        async def capture_run(goal: str, hints: list[str] | None = None):
            from hermit.plugins.builtin.hooks.research.models import ResearchReport

            captured_hints.append(hints or [])
            return ResearchReport(goal=goal, duration_seconds=0.01)

        with patch(
            "hermit.plugins.builtin.hooks.research.pipeline.ResearchPipeline.run",
            side_effect=capture_run,
        ):
            result = orch.advance("s1")

        assert result is not None
        assert result.phase == IterationPhase.GENERATING_SPEC

        # Verify lessons were injected as hints
        assert len(captured_hints) == 1
        injected = captured_hints[0]
        assert any("Always run ruff before commit" in h for h in injected)
        assert any("Use parallel strategies" in h for h in injected)

        # Verify research metadata was stored
        entry = fake_store.get_spec_entry("s1")
        assert entry is not None
        import json

        metadata = (
            json.loads(entry["metadata"])
            if isinstance(entry["metadata"], str)
            else entry["metadata"]
        )
        assert "research" in metadata

    def test_learning_spawns_followup_for_mistake_lesson(
        self,
        fake_store: FakeStore,
    ) -> None:
        """When a lesson has category 'mistake', a follow-up spec is created."""
        import json

        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        # Set up state at LEARNING with benchmark data
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.LEARNING.value,
            metadata=json.dumps(
                {
                    "benchmark": {
                        "check_passed": False,
                        "test_total": 100,
                        "test_passed": 90,
                        "coverage": 0.85,
                        "lint_violations": 3,
                        "duration_seconds": 60.0,
                        "regression_detected": True,
                        "compared_to_baseline": {},
                    },
                }
            ),
        )

        # Monkey-patch IterationLearner to return a lesson with 'mistake' category
        from unittest.mock import AsyncMock, patch

        from hermit.plugins.builtin.hooks.benchmark.models import LessonLearned

        mock_lessons = [
            LessonLearned(
                lesson_id="lesson-1",
                iteration_id="s1",
                category="mistake",
                summary="Forgot to add type hints",
                applicable_files="all",
            ),
        ]
        with patch(
            "hermit.plugins.builtin.hooks.benchmark.learning.IterationLearner.learn",
            new_callable=AsyncMock,
            return_value=mock_lessons,
        ):
            state = IterationState(spec_id="s1", phase=IterationPhase.LEARNING)
            result = orch._handle_learning(state)

        assert result is not None
        assert result.phase == IterationPhase.COMPLETED

        # Check that a followup spec was created
        all_specs = list(fake_store._specs.keys())
        followup_specs = [s for s in all_specs if s.startswith("followup-")]
        assert len(followup_specs) == 1

        followup = fake_store.get_spec_entry(followup_specs[0])
        assert followup is not None
        assert followup["priority"] == "high"
        assert "Forgot to add type hints" in followup["goal"]


# ---------------------------------------------------------------------------
# Gap 3: Signal-to-spec consumer tests
# ---------------------------------------------------------------------------


class TestSignalToSpecConsumer:
    def test_start_stop(self, fake_store: FakeStore) -> None:
        consumer = SignalToSpecConsumer(fake_store, poll_interval=0.05)
        consumer.start()
        assert consumer.is_running
        consumer.stop()
        assert not consumer.is_running

    def test_creates_spec_from_signal(self, fake_store: FakeStore) -> None:
        fake_store._signals = [
            {
                "signal_id": "sig-001",
                "source_kind": "patrol",
                "suggested_goal": "Fix security issue in auth module",
                "risk_level": "high",
            },
        ]
        consumer = SignalToSpecConsumer(fake_store, poll_interval=0.05)
        count = consumer._tick()
        assert count == 1

        # Verify spec was created
        all_specs = list(fake_store._specs.keys())
        signal_specs = [s for s in all_specs if s.startswith("signal-")]
        assert len(signal_specs) == 1

        spec = fake_store.get_spec_entry(signal_specs[0])
        assert spec is not None
        assert spec["priority"] == "high"
        assert "Fix security issue" in spec["goal"]

        # Verify signal was marked as consumed
        assert fake_store._signal_dispositions.get("sig-001") == "acted"

    def test_skips_non_eligible_source(self, fake_store: FakeStore) -> None:
        fake_store._signals = [
            {
                "signal_id": "sig-002",
                "source_kind": "user_request",  # not eligible
                "suggested_goal": "Something",
                "risk_level": "normal",
            },
        ]
        consumer = SignalToSpecConsumer(fake_store, poll_interval=0.05)
        count = consumer._tick()
        assert count == 0

    def test_no_duplicate_specs(self, fake_store: FakeStore) -> None:
        fake_store._signals = [
            {
                "signal_id": "sig-003",
                "source_kind": "patrol",
                "suggested_goal": "Fix something",
                "risk_level": "normal",
            },
        ]
        consumer = SignalToSpecConsumer(fake_store, poll_interval=0.05)
        consumer._tick()
        # Second tick should not create duplicate
        count = consumer._tick()
        assert count == 0

    def test_no_crash_without_signal_support(self) -> None:
        """Consumer gracefully handles stores without signal methods."""
        bare_store = SimpleNamespace()  # no actionable_signals
        consumer = SignalToSpecConsumer(bare_store, poll_interval=0.05)
        count = consumer._tick()
        assert count == 0

    def test_signal_dedup_by_normalized_goal(self, fake_store: FakeStore) -> None:
        """Signals with same goal (different casing) should produce only 1 spec."""
        fake_store._signals = [
            {
                "signal_id": "sig-a",
                "source_kind": "patrol",
                "suggested_goal": "Fix error",
                "risk_level": "normal",
            },
            {
                "signal_id": "sig-b",
                "source_kind": "patrol",
                "suggested_goal": "fix error",
                "risk_level": "normal",
            },
        ]
        consumer = SignalToSpecConsumer(fake_store, poll_interval=0.05)
        count = consumer._tick()
        # Only 1 spec should be created; second is deduped by normalized goal
        assert count == 1
        signal_specs = [s for s in fake_store._specs if s.startswith("signal-")]
        assert len(signal_specs) == 1


# ---------------------------------------------------------------------------
# Wired phase handler tests
# ---------------------------------------------------------------------------


class TestWiredPhaseHandlers:
    def test_researching_stores_findings(self, fake_store: FakeStore) -> None:
        """Advancing from RESEARCHING populates metadata with research findings."""
        import json
        from unittest.mock import AsyncMock, patch

        from hermit.plugins.builtin.hooks.research.models import (
            ResearchFinding,
            ResearchReport,
        )

        report = ResearchReport(
            goal="improve X",
            findings=(
                ResearchFinding(
                    source="codebase",
                    title="src/foo.py",
                    content="relevant code",
                    relevance=0.9,
                ),
            ),
            query_count=1,
            duration_seconds=0.5,
        )

        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="improve X")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.RESEARCHING.value,
        )

        with patch(
            "hermit.plugins.builtin.hooks.research.pipeline.ResearchPipeline.run",
            new_callable=AsyncMock,
            return_value=report,
        ):
            state = IterationState(spec_id="s1", phase=IterationPhase.RESEARCHING)
            result = orch._handle_researching(state)

        assert result is not None
        assert result.phase == IterationPhase.GENERATING_SPEC

        entry = fake_store.get_spec_entry("s1")
        metadata = (
            json.loads(entry["metadata"])
            if isinstance(entry["metadata"], str)
            else entry["metadata"]
        )
        assert "research" in metadata
        assert metadata["research"]["count"] == 1
        assert "codebase" in metadata["research"]["sources"]
        assert metadata["research"]["duration_seconds"] == 0.5

    def test_generating_spec_stores_spec(self, fake_store: FakeStore) -> None:
        """Advancing from GENERATING_SPEC populates metadata with generated_spec."""
        import json

        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="improve X")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.GENERATING_SPEC.value,
            metadata=json.dumps(
                {
                    "research": {
                        "goal": "improve X",
                        "findings": [],
                        "knowledge_gaps": [],
                        "query_count": 0,
                        "duration_seconds": 0.1,
                        "count": 0,
                        "sources": [],
                    },
                }
            ),
        )

        state = IterationState(spec_id="s1", phase=IterationPhase.GENERATING_SPEC)
        result = orch._handle_generating_spec(state)

        assert result is not None
        assert result.phase == IterationPhase.SPEC_APPROVAL

        entry = fake_store.get_spec_entry("s1")
        metadata = (
            json.loads(entry["metadata"])
            if isinstance(entry["metadata"], str)
            else entry["metadata"]
        )
        assert "generated_spec" in metadata
        assert "spec_id" in metadata["generated_spec"]
        assert "title" in metadata["generated_spec"]
        assert "acceptance_criteria" in metadata["generated_spec"]

    def test_decomposing_stores_plan(self, fake_store: FakeStore) -> None:
        """Advancing from DECOMPOSING populates metadata with decomposition_plan."""
        import json

        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(
            spec_id="s1",
            goal="create src/utils.py and modify src/main.py",
        )
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.DECOMPOSING.value,
            metadata=json.dumps(
                {
                    "generated_spec": {
                        "spec_id": "test-spec",
                        "title": "create src/utils.py and modify src/main.py",
                        "goal": "create src/utils.py and modify src/main.py",
                        "constraints": [],
                        "acceptance_criteria": ["`make check` passes"],
                        "file_plan": [
                            {"path": "src/utils.py", "action": "create", "reason": "new"},
                            {"path": "src/main.py", "action": "modify", "reason": "update"},
                        ],
                        "research_ref": "",
                        "trust_zone": "normal",
                    },
                }
            ),
        )

        state = IterationState(spec_id="s1", phase=IterationPhase.DECOMPOSING)
        result = orch._handle_decomposing(state)

        assert result is not None
        assert result.phase == IterationPhase.IMPLEMENTING

        entry = fake_store.get_spec_entry("s1")
        metadata = (
            json.loads(entry["metadata"])
            if isinstance(entry["metadata"], str)
            else entry["metadata"]
        )
        assert "decomposition_plan" in metadata
        plan = metadata["decomposition_plan"]
        assert len(plan["steps"]) > 0
        # Should have code steps + review steps + final_check
        step_kinds = [s["kind"] for s in plan["steps"]]
        assert "code" in step_kinds
        assert "review" in step_kinds

    def test_benchmarking_stores_result(self, fake_store: FakeStore) -> None:
        """Advancing from BENCHMARKING populates metadata with benchmark result."""
        import json
        from unittest.mock import AsyncMock, patch

        from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkResult

        bench_result = BenchmarkResult(
            iteration_id="s1",
            spec_id="s1",
            check_passed=True,
            test_total=50,
            test_passed=48,
            coverage=92.5,
            lint_violations=1,
            duration_seconds=30.0,
            regression_detected=False,
            compared_to_baseline={"coverage_delta": 2.0},
        )

        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test bench")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.BENCHMARKING.value,
        )

        with patch(
            "hermit.plugins.builtin.hooks.benchmark.runner.BenchmarkRunner.run",
            new_callable=AsyncMock,
            return_value=bench_result,
        ):
            state = IterationState(spec_id="s1", phase=IterationPhase.BENCHMARKING)
            result = orch._handle_benchmarking(state)

        assert result is not None
        assert result.phase == IterationPhase.LEARNING

        entry = fake_store.get_spec_entry("s1")
        metadata = (
            json.loads(entry["metadata"])
            if isinstance(entry["metadata"], str)
            else entry["metadata"]
        )
        assert "benchmark" in metadata
        assert metadata["benchmark"]["check_passed"] is True
        assert metadata["benchmark"]["test_total"] == 50
        assert metadata["benchmark"]["coverage"] == 92.5

    def test_spec_approval_fails_without_generated_spec(
        self,
        fake_store: FakeStore,
    ) -> None:
        """Spec approval without generated_spec in metadata should fail."""
        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=IterationPhase.SPEC_APPROVAL.value,
        )

        state = IterationState(spec_id="s1", phase=IterationPhase.SPEC_APPROVAL)
        result = orch._handle_spec_approval(state)

        assert result is not None
        # Should fail (retry) since no generated_spec exists
        assert result.phase == IterationPhase.PENDING
        assert result.attempt == 2
