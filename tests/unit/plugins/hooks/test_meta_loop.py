"""Tests for the meta-loop v2 orchestrator, hooks, and poller.

v2 pipeline phases: PENDING -> PLANNING -> IMPLEMENTING -> REVIEWING -> ACCEPTED
with a revision loop: REVIEWING -> IMPLEMENTING -> REVIEWING.
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from hermit.plugins.builtin.hooks.metaloop import hooks as metaloop_hooks
from hermit.plugins.builtin.hooks.metaloop.backlog import SpecBacklog
from hermit.plugins.builtin.hooks.metaloop.models import (
    ALLOWED_TRANSITIONS,
    MAX_REVISION_CYCLES,
    TERMINAL_PHASES,
    IterationState,
    PipelinePhase,
)
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
        to_status: str = "planning",
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
        return sum(
            1
            for s in self._specs.values()
            if s["status"] not in ("completed", "failed", "accepted", "rejected")
        )

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
                and spec["status"] not in ("completed", "failed", "accepted", "rejected")
            ):
                return spec
        return None

    def list_tasks(self, limit: int = 50) -> list[Any]:
        return []

    def create_lesson(self, **kwargs: Any) -> None:
        pass

    def list_lessons(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._lessons)

    def get_lesson(self, lesson_id: str) -> dict[str, Any] | None:
        for lesson in self._lessons:
            if lesson.get("lesson_id") == lesson_id:
                return lesson
        return None

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
    def test_pending_is_not_terminal(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.PENDING)
        assert not state.is_terminal

    def test_accepted_is_terminal(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.ACCEPTED)
        assert state.is_terminal

    def test_rejected_is_terminal(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.REJECTED)
        assert state.is_terminal

    def test_failed_is_terminal(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.FAILED)
        assert state.is_terminal

    def test_frozen(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.PENDING)
        with pytest.raises(AttributeError):
            state.phase = PipelinePhase.PLANNING  # type: ignore[misc]

    def test_can_revise_within_budget(self) -> None:
        state = IterationState(spec_id="s1", phase=PipelinePhase.REVIEWING, revision_cycle=0)
        assert state.can_revise

    def test_cannot_revise_when_budget_exhausted(self) -> None:
        state = IterationState(
            spec_id="s1",
            phase=PipelinePhase.REVIEWING,
            revision_cycle=MAX_REVISION_CYCLES,
        )
        assert not state.can_revise

    def test_allowed_transitions_cover_non_terminal_phases(self) -> None:
        non_terminal = {p for p in PipelinePhase if p not in TERMINAL_PHASES}
        assert set(ALLOWED_TRANSITIONS.keys()) == non_terminal

    def test_reviewing_can_transition_to_implementing(self) -> None:
        """REVIEWING -> IMPLEMENTING is the revision loop transition."""
        allowed = ALLOWED_TRANSITIONS[PipelinePhase.REVIEWING]
        assert PipelinePhase.IMPLEMENTING in allowed

    def test_implementing_self_transition_allowed(self) -> None:
        """IMPLEMENTING -> IMPLEMENTING is allowed for dag_task_id writes."""
        allowed = ALLOWED_TRANSITIONS[PipelinePhase.IMPLEMENTING]
        assert PipelinePhase.IMPLEMENTING in allowed


# ---------------------------------------------------------------------------
# Orchestrator tests
# ---------------------------------------------------------------------------


class TestMetaLoopOrchestrator:
    def test_advance_pending_to_planning(
        self, orchestrator: MetaLoopOrchestrator, fake_store: FakeStore
    ) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="improve X")
        result = orchestrator.advance("s1")
        assert result is not None
        assert result.phase == PipelinePhase.PLANNING

    def test_advance_through_all_phases(
        self, orchestrator: MetaLoopOrchestrator, fake_store: FakeStore
    ) -> None:
        """Walk through the 3-phase pipeline: PENDING -> PLANNING -> IMPLEMENTING -> REVIEWING -> ACCEPTED."""
        from unittest.mock import AsyncMock, MagicMock, patch

        fake_store.create_spec_entry(spec_id="s1", goal="improve X")

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
            # Walk through phases until we hit a terminal state
            phases_seen = [PipelinePhase.PENDING]
            for _ in range(20):  # safety limit
                result = orchestrator.advance("s1")
                assert result is not None
                phases_seen.append(result.phase)
                if result.is_terminal:
                    break
            # Should reach ACCEPTED (benchmark passes, no council review)
            assert result.phase == PipelinePhase.ACCEPTED
            # After ACCEPTED (terminal), should not advance further
            result = orchestrator.advance("s1")
            assert result is not None
            assert result.phase == PipelinePhase.ACCEPTED

    def test_advance_nonexistent_spec(self, orchestrator: MetaLoopOrchestrator) -> None:
        result = orchestrator.advance("nonexistent")
        assert result is None

    def test_on_subtask_complete_success(
        self, orchestrator: MetaLoopOrchestrator, fake_store: FakeStore
    ) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.IMPLEMENTING.value,
            dag_task_id="dag-123",
        )
        result = orchestrator.on_subtask_complete("dag-123", success=True)
        assert result is not None
        assert result.phase == PipelinePhase.REVIEWING

    def test_on_subtask_complete_failure_with_retry(
        self, orchestrator: MetaLoopOrchestrator, fake_store: FakeStore
    ) -> None:
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.IMPLEMENTING.value,
            dag_task_id="dag-456",
        )
        result = orchestrator.on_subtask_complete("dag-456", success=False, error="build failed")
        assert result is not None
        # Should retry: reset to PENDING with attempt incremented
        assert result.phase == PipelinePhase.PENDING
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
        time.sleep(0.08)
        poller.stop()
        # The spec should have been claimed and advanced past PENDING
        entry = fake_store.get_spec_entry(spec_id="s1")
        assert entry is not None
        assert entry["status"] != "pending"

    def test_poller_no_work(self, orchestrator: MetaLoopOrchestrator, backlog: SpecBacklog) -> None:
        """Poller should not error when backlog is empty."""
        poller = SpecBacklogPoller(orchestrator, backlog, poll_interval=0.05)
        poller.start()
        time.sleep(0.08)
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
            status=PipelinePhase.IMPLEMENTING.value,
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
        assert entry["status"] == PipelinePhase.REVIEWING.value

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
# Implementing handler tests
# ---------------------------------------------------------------------------


class TestImplementingHandler:
    def test_implementing_dry_run_without_runner(
        self,
        fake_store: FakeStore,
    ) -> None:
        """Without a runner, _handle_implementing skips to REVIEWING."""
        orch = MetaLoopOrchestrator(fake_store, max_retries=2, runner=None)
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.IMPLEMENTING.value,
            metadata=json.dumps({"decomposition_plan": {"steps": [{"key": "a"}]}}),
        )
        state = IterationState(spec_id="s1", phase=PipelinePhase.IMPLEMENTING)
        result = orch._handle_implementing(state)
        assert result is not None
        # Without a runner, should advance to REVIEWING directly
        assert result.phase == PipelinePhase.REVIEWING

    def test_implementing_no_steps_advances_to_reviewing(
        self,
        fake_store: FakeStore,
    ) -> None:
        """If decomposition has no steps, advance straight to REVIEWING."""
        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.IMPLEMENTING.value,
            metadata='{"decomposition_plan": {"steps": []}}',
        )
        state = IterationState(spec_id="s1", phase=PipelinePhase.IMPLEMENTING)
        result = orch._handle_implementing(state)
        assert result is not None
        assert result.phase == PipelinePhase.REVIEWING

    def test_implementing_builds_step_nodes(
        self,
        fake_store: FakeStore,
    ) -> None:
        """DAG nodes built from decomposition plan should be valid StepNode instances."""
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
            status=PipelinePhase.IMPLEMENTING.value,
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
        state = IterationState(spec_id="s1", phase=PipelinePhase.IMPLEMENTING)
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
        assert result.phase == PipelinePhase.IMPLEMENTING
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
            status=PipelinePhase.IMPLEMENTING.value,
            dag_task_id="dag-ok",
        )
        result = orch.on_subtask_complete("dag-ok", success=True)
        assert result is not None
        assert result.phase == PipelinePhase.REVIEWING

    def test_subtask_failure_marks_failed(
        self,
        fake_store: FakeStore,
    ) -> None:
        """Failed subtask should mark spec as failed/retry."""
        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.IMPLEMENTING.value,
            dag_task_id="dag-fail",
        )
        result = orch.on_subtask_complete("dag-fail", success=False, error="build error")
        assert result is not None
        # First attempt: should retry (PENDING with attempt 2)
        assert result.phase == PipelinePhase.PENDING
        assert result.attempt == 2

    def test_subtask_success_with_worktree_metadata(
        self,
        fake_store: FakeStore,
    ) -> None:
        """When metadata has worktree info, PR info is stored (no direct merge)."""
        orch = MetaLoopOrchestrator(fake_store, max_retries=2, workspace_root="/tmp/test-repo")
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.IMPLEMENTING.value,
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

        result = orch.on_subtask_complete("dag-merge", success=True)

        assert result is not None
        assert result.phase == PipelinePhase.REVIEWING

        # Verify PR pending metadata was stored instead of merging
        entry = fake_store.get_spec_entry("s1")
        metadata = (
            json.loads(entry["metadata"])
            if isinstance(entry["metadata"], str)
            else entry["metadata"]
        )
        assert "pr_pending" in metadata
        assert metadata["pr_pending"]["ready_for_review"] is True
        assert metadata["pr_pending"]["branch"] == "self-modify/s1"

    def test_subtask_failure_cleans_worktree(
        self,
        fake_store: FakeStore,
    ) -> None:
        """Failed subtask should clean up worktree without merging."""
        from unittest.mock import MagicMock, patch

        orch = MetaLoopOrchestrator(fake_store, max_retries=2, workspace_root="/tmp/test-repo")
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.IMPLEMENTING.value,
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
        assert result.phase == PipelinePhase.PENDING  # retry
        mock_ws.remove.assert_called_once_with("s1")
        mock_ws.merge_to_main.assert_not_called()


# ---------------------------------------------------------------------------
# Revision loop tests
# ---------------------------------------------------------------------------


class TestRevisionLoop:
    def test_revision_loop_reviewing_to_implementing(
        self,
        fake_store: FakeStore,
    ) -> None:
        """REVIEWING -> IMPLEMENTING when council says 'revise'."""
        from unittest.mock import MagicMock, patch

        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test revision")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.REVIEWING.value,
            metadata=json.dumps(
                {
                    "decomposition_plan": {"steps": []},
                    "generated_spec": {"goal": "test"},
                    "revision_cycle": 0,
                }
            ),
        )

        # Mock council to return "revise" verdict
        mock_verdict = MagicMock()
        mock_verdict.verdict = "revise"
        mock_verdict.council_id = "council-1"
        mock_verdict.finding_count = 2
        mock_verdict.critical_count = 0
        mock_verdict.high_count = 1
        mock_verdict.lint_passed = True
        mock_verdict.consensus_score = 0.7
        mock_verdict.revision_directive = "Fix the edge case handling"
        mock_verdict.duration_seconds = 1.0
        mock_verdict.decided_at = 1234567890.0

        with patch.object(orch, "_run_council_review", return_value=mock_verdict):
            state = IterationState(spec_id="s1", phase=PipelinePhase.REVIEWING)
            result = orch._handle_reviewing(state)

        assert result is not None
        assert result.phase == PipelinePhase.IMPLEMENTING

        # Verify revision metadata was stored
        entry = fake_store.get_spec_entry("s1")
        metadata = (
            json.loads(entry["metadata"])
            if isinstance(entry["metadata"], str)
            else entry["metadata"]
        )
        assert metadata["revision_cycle"] == 1

    def test_revision_budget_exhausted(
        self,
        fake_store: FakeStore,
    ) -> None:
        """When revision cycle reaches MAX_REVISION_CYCLES, reject instead of revise."""
        from unittest.mock import MagicMock, patch

        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test budget")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.REVIEWING.value,
            metadata=json.dumps(
                {
                    "decomposition_plan": {"steps": []},
                    "generated_spec": {"goal": "test"},
                    "revision_cycle": MAX_REVISION_CYCLES,
                }
            ),
        )

        mock_verdict = MagicMock()
        mock_verdict.verdict = "revise"
        mock_verdict.council_id = "council-1"
        mock_verdict.finding_count = 1
        mock_verdict.critical_count = 0
        mock_verdict.high_count = 0
        mock_verdict.lint_passed = True
        mock_verdict.consensus_score = 0.6
        mock_verdict.revision_directive = "More fixes needed"
        mock_verdict.duration_seconds = 0.5
        mock_verdict.decided_at = 1234567890.0

        with patch.object(orch, "_run_council_review", return_value=mock_verdict):
            state = IterationState(spec_id="s1", phase=PipelinePhase.REVIEWING)
            result = orch._handle_reviewing(state)

        assert result is not None
        assert result.phase == PipelinePhase.REJECTED
        entry = fake_store.get_spec_entry("s1")
        assert entry["error"] is not None
        assert "budget exhausted" in entry["error"].lower() or "revision" in entry["error"].lower()


# ---------------------------------------------------------------------------
# Lessons feedback loop tests
# ---------------------------------------------------------------------------


class TestLessonsFeedbackLoop:
    def test_query_relevant_lessons_filters_by_keyword_overlap(
        self,
        fake_store: FakeStore,
    ) -> None:
        """_query_relevant_lessons scores by keyword overlap and returns top-N."""
        fake_store._lessons = [
            {"lesson_id": "L1", "category": "mistake", "summary": "auth module failed tests"},
            {
                "lesson_id": "L2",
                "category": "success_pattern",
                "summary": "cache module passed all tests",
            },
            {"lesson_id": "L3", "category": "optimization", "summary": "unrelated database work"},
        ]
        orch = MetaLoopOrchestrator(fake_store, max_retries=2)

        # Goal about "auth module tests"
        result = orch._query_relevant_lessons("fix auth module tests", limit=5)
        summaries = [r["summary"] for r in result]
        # L1 and L2 overlap on "module" and "tests"; L3 has no overlap
        assert any("auth" in s for s in summaries)
        assert any("cache" in s for s in summaries)
        assert not any("database" in s for s in summaries)
        # Mistake category gets boosted, so L1 should rank first
        assert result[0]["category"] == "mistake"

    def test_query_relevant_lessons_empty_store(
        self,
        fake_store: FakeStore,
    ) -> None:
        """Returns empty list when no lessons exist."""
        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        result = orch._query_relevant_lessons("anything")
        assert result == []

    def test_query_relevant_lessons_no_store_support(self) -> None:
        """Returns empty list when store lacks list_lessons."""
        bare_store = SimpleNamespace()
        orch = MetaLoopOrchestrator(bare_store, max_retries=2)
        result = orch._query_relevant_lessons("anything")
        assert result == []


# ---------------------------------------------------------------------------
# Signal-to-spec consumer tests
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
# Reviewing handler tests (post-accept path: benchmark + learning)
# ---------------------------------------------------------------------------


class TestReviewingHandler:
    def test_reviewing_accepts_when_council_accepts_and_benchmark_passes(
        self,
        fake_store: FakeStore,
    ) -> None:
        """REVIEWING -> ACCEPTED when council accepts and benchmark passes."""
        from unittest.mock import AsyncMock, patch

        from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkResult

        bench_result = BenchmarkResult(
            iteration_id="s1",
            spec_id="s1",
            check_passed=True,
            test_total=50,
            test_passed=50,
            coverage=92.5,
            lint_violations=0,
            duration_seconds=30.0,
            regression_detected=False,
            compared_to_baseline={},
        )

        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test accept")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.REVIEWING.value,
            metadata=json.dumps(
                {
                    "decomposition_plan": {"steps": []},
                    "generated_spec": {"goal": "test"},
                    "revision_cycle": 0,
                }
            ),
        )

        with (
            patch.object(orch, "_run_council_review", return_value=None),
            patch(
                "hermit.plugins.builtin.hooks.benchmark.runner.BenchmarkRunner.run",
                new_callable=AsyncMock,
                return_value=bench_result,
            ),
        ):
            state = IterationState(spec_id="s1", phase=PipelinePhase.REVIEWING)
            result = orch._handle_reviewing(state)

        assert result is not None
        assert result.phase == PipelinePhase.ACCEPTED

    def test_reviewing_retries_when_benchmark_fails(
        self,
        fake_store: FakeStore,
    ) -> None:
        """REVIEWING -> PENDING (retry) when benchmark check fails."""
        from unittest.mock import AsyncMock, patch

        from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkResult

        bench_result = BenchmarkResult(
            iteration_id="s1",
            spec_id="s1",
            check_passed=False,
            test_total=50,
            test_passed=40,
            coverage=80.0,
            lint_violations=5,
            duration_seconds=30.0,
            regression_detected=False,
            compared_to_baseline={},
        )

        orch = MetaLoopOrchestrator(fake_store, max_retries=2, benchmark_blocking=True)
        fake_store.create_spec_entry(spec_id="s1", goal="test fail")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.REVIEWING.value,
            metadata=json.dumps(
                {
                    "decomposition_plan": {"steps": []},
                    "generated_spec": {"goal": "test"},
                    "revision_cycle": 0,
                }
            ),
        )

        with (
            patch.object(orch, "_run_council_review", return_value=None),
            patch(
                "hermit.plugins.builtin.hooks.benchmark.runner.BenchmarkRunner.run",
                new_callable=AsyncMock,
                return_value=bench_result,
            ),
        ):
            state = IterationState(spec_id="s1", phase=PipelinePhase.REVIEWING)
            result = orch._handle_reviewing(state)

        assert result is not None
        # Should retry since benchmark failed
        assert result.phase == PipelinePhase.PENDING
        assert result.attempt == 2

    def test_reviewing_retries_on_regression(
        self,
        fake_store: FakeStore,
    ) -> None:
        """REVIEWING -> PENDING (retry) when regression detected."""
        from unittest.mock import AsyncMock, patch

        from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkResult

        bench_result = BenchmarkResult(
            iteration_id="s1",
            spec_id="s1",
            check_passed=True,
            test_total=50,
            test_passed=50,
            coverage=85.0,
            lint_violations=0,
            duration_seconds=30.0,
            regression_detected=True,
            compared_to_baseline={"coverage_delta": -5.0},
        )

        orch = MetaLoopOrchestrator(fake_store, max_retries=2, benchmark_blocking=True)
        fake_store.create_spec_entry(spec_id="s1", goal="test regression")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.REVIEWING.value,
            metadata=json.dumps(
                {
                    "decomposition_plan": {"steps": []},
                    "generated_spec": {"goal": "test"},
                    "revision_cycle": 0,
                }
            ),
        )

        with (
            patch.object(orch, "_run_council_review", return_value=None),
            patch(
                "hermit.plugins.builtin.hooks.benchmark.runner.BenchmarkRunner.run",
                new_callable=AsyncMock,
                return_value=bench_result,
            ),
        ):
            state = IterationState(spec_id="s1", phase=PipelinePhase.REVIEWING)
            result = orch._handle_reviewing(state)

        assert result is not None
        assert result.phase == PipelinePhase.PENDING
        assert result.attempt == 2

    def test_reviewing_benchmark_exception_still_accepts(
        self,
        fake_store: FakeStore,
    ) -> None:
        """When BenchmarkRunner raises, advance to ACCEPTED with benchmark_skipped metadata."""
        from unittest.mock import AsyncMock, patch

        orch = MetaLoopOrchestrator(fake_store, max_retries=2, benchmark_blocking=True)
        fake_store.create_spec_entry(spec_id="s1", goal="test exception")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.REVIEWING.value,
            metadata=json.dumps(
                {
                    "decomposition_plan": {"steps": []},
                    "generated_spec": {"goal": "test"},
                    "revision_cycle": 0,
                }
            ),
        )

        with (
            patch.object(orch, "_run_council_review", return_value=None),
            patch(
                "hermit.plugins.builtin.hooks.benchmark.runner.BenchmarkRunner.run",
                new_callable=AsyncMock,
                side_effect=RuntimeError("infra failure"),
            ),
        ):
            state = IterationState(spec_id="s1", phase=PipelinePhase.REVIEWING)
            result = orch._handle_reviewing(state)

        assert result is not None
        # Should still advance to ACCEPTED (don't block on infra failure)
        assert result.phase == PipelinePhase.ACCEPTED

        # Verify benchmark_skipped metadata
        entry = fake_store.get_spec_entry("s1")
        metadata = (
            json.loads(entry["metadata"])
            if isinstance(entry["metadata"], str)
            else entry["metadata"]
        )
        assert "benchmark" in metadata
        assert metadata["benchmark"]["benchmark_skipped"] is True

    def test_reviewing_council_rejects(
        self,
        fake_store: FakeStore,
    ) -> None:
        """REVIEWING -> REJECTED when council says 'reject'."""
        from unittest.mock import MagicMock, patch

        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test reject")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.REVIEWING.value,
            metadata=json.dumps(
                {
                    "decomposition_plan": {"steps": []},
                    "generated_spec": {"goal": "test"},
                    "revision_cycle": 0,
                }
            ),
        )

        mock_verdict = MagicMock()
        mock_verdict.verdict = "reject"
        mock_verdict.council_id = "council-1"
        mock_verdict.finding_count = 5
        mock_verdict.critical_count = 2
        mock_verdict.high_count = 3
        mock_verdict.lint_passed = False
        mock_verdict.consensus_score = 0.9
        mock_verdict.revision_directive = ""
        mock_verdict.duration_seconds = 1.0
        mock_verdict.decided_at = 1234567890.0

        with patch.object(orch, "_run_council_review", return_value=mock_verdict):
            state = IterationState(spec_id="s1", phase=PipelinePhase.REVIEWING)
            result = orch._handle_reviewing(state)

        assert result is not None
        assert result.phase == PipelinePhase.REJECTED

    def test_default_benchmark_blocking_is_true(self, fake_store: FakeStore) -> None:
        """Default benchmark_blocking should be True."""
        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        assert orch._benchmark_blocking is True

    def test_learning_spawns_followup_for_mistake_lesson(
        self,
        fake_store: FakeStore,
    ) -> None:
        """When a lesson has category 'mistake', a follow-up spec is created."""
        from unittest.mock import AsyncMock, patch

        from hermit.plugins.builtin.hooks.benchmark.models import BenchmarkResult, LessonLearned

        mock_lessons = [
            LessonLearned(
                lesson_id="lesson-1",
                iteration_id="s1",
                category="mistake",
                summary="Forgot to add type hints",
                applicable_files="all",
            ),
        ]

        bench_result = BenchmarkResult(
            iteration_id="s1",
            spec_id="s1",
            check_passed=True,
            test_total=100,
            test_passed=100,
            coverage=95.0,
            lint_violations=0,
            duration_seconds=60.0,
            regression_detected=False,
            compared_to_baseline={},
        )

        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="test")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.REVIEWING.value,
            metadata=json.dumps(
                {
                    "decomposition_plan": {"steps": []},
                    "generated_spec": {"goal": "test"},
                    "revision_cycle": 0,
                }
            ),
        )

        with (
            patch.object(orch, "_run_council_review", return_value=None),
            patch(
                "hermit.plugins.builtin.hooks.benchmark.runner.BenchmarkRunner.run",
                new_callable=AsyncMock,
                return_value=bench_result,
            ),
            patch(
                "hermit.plugins.builtin.hooks.benchmark.learning.IterationLearner.learn",
                new_callable=AsyncMock,
                return_value=mock_lessons,
            ),
        ):
            state = IterationState(spec_id="s1", phase=PipelinePhase.REVIEWING)
            result = orch._handle_reviewing(state)

        assert result is not None
        assert result.phase == PipelinePhase.ACCEPTED

        # Check that a followup spec was created
        all_specs = list(fake_store._specs.keys())
        followup_specs = [s for s in all_specs if s.startswith("followup-")]
        assert len(followup_specs) == 1

        followup = fake_store.get_spec_entry(followup_specs[0])
        assert followup is not None
        assert followup["priority"] == "high"
        assert "Forgot to add type hints" in followup["goal"]


# ---------------------------------------------------------------------------
# Planning handler tests
# ---------------------------------------------------------------------------


class TestPlanningHandler:
    def test_planning_runs_research_and_decomposition(
        self,
        fake_store: FakeStore,
    ) -> None:
        """_handle_planning runs research, spec gen, approval, and decomposition."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_report = MagicMock()
        mock_report.goal = "improve X"
        mock_report.findings = ()
        mock_report.knowledge_gaps = ()
        mock_report.query_count = 0
        mock_report.duration_seconds = 0.1

        orch = MetaLoopOrchestrator(fake_store, max_retries=2)
        fake_store.create_spec_entry(spec_id="s1", goal="improve X")
        fake_store.update_spec_status(
            spec_id="s1",
            status=PipelinePhase.PLANNING.value,
        )

        with patch(
            "hermit.plugins.builtin.hooks.research.pipeline.ResearchPipeline.run",
            new_callable=AsyncMock,
            return_value=mock_report,
        ):
            state = IterationState(spec_id="s1", phase=PipelinePhase.PLANNING)
            result = orch._handle_planning(state)

        assert result is not None
        assert result.phase == PipelinePhase.IMPLEMENTING

        # Verify metadata contains research and decomposition data
        entry = fake_store.get_spec_entry("s1")
        metadata = (
            json.loads(entry["metadata"])
            if isinstance(entry["metadata"], str)
            else entry["metadata"]
        )
        assert "research" in metadata
        assert "generated_spec" in metadata
        assert "decomposition_plan" in metadata
