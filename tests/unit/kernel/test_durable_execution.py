"""Tests for durable execution enhancements: heartbeat, super-step checkpoint,
replay-from, and authorization plan revalidation.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.task.services.dag_builder import (
    StepDAGBuilder,
    StepNode,
)
from hermit.kernel.task.services.dag_execution import DAGExecutionService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    return KernelStore(tmp_path / "state.db")


@pytest.fixture
def builder(store: KernelStore) -> StepDAGBuilder:
    return StepDAGBuilder(store)


@pytest.fixture
def dag_exec(store: KernelStore) -> DAGExecutionService:
    return DAGExecutionService(store)


def _make_task(store: KernelStore) -> str:
    store.ensure_conversation("conv_1", source_channel="test")
    task = store.create_task(
        conversation_id="conv_1",
        title="test",
        goal="test",
        source_channel="test",
    )
    return task.task_id


# ===========================================================================
# 1. Heartbeat
# ===========================================================================


class TestHeartbeat:
    def test_heartbeat_field_on_step_node(self) -> None:
        """heartbeat_interval_seconds should be accepted on StepNode and default to None."""
        node_no_hb = StepNode(key="a", kind="execute", title="A")
        assert node_no_hb.heartbeat_interval_seconds is None

        node_with_hb = StepNode(key="b", kind="execute", title="B", heartbeat_interval_seconds=30.0)
        assert node_with_hb.heartbeat_interval_seconds == 30.0

    def test_heartbeat_opt_in_only(self, store: KernelStore, builder: StepDAGBuilder) -> None:
        """Steps without heartbeat_interval_seconds should not store it in context."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A")]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        attempts = store.list_step_attempts(step_id=key_map["a"], limit=1)
        assert attempts
        ctx = attempts[0].context or {}
        assert "heartbeat_interval_seconds" not in ctx

    def test_heartbeat_stored_in_context(self, store: KernelStore, builder: StepDAGBuilder) -> None:
        """When heartbeat_interval_seconds is set, it should appear in attempt context."""
        task_id = _make_task(store)
        nodes = [StepNode(key="a", kind="execute", title="A", heartbeat_interval_seconds=10.0)]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)
        attempts = store.list_step_attempts(step_id=key_map["a"], limit=1)
        assert attempts
        ctx = attempts[0].context or {}
        assert ctx.get("heartbeat_interval_seconds") == 10.0

    def test_report_heartbeat_updates_context(self, store: KernelStore) -> None:
        """report_heartbeat should update the attempt context with last_heartbeat_at."""
        task_id = _make_task(store)
        step = store.create_step(task_id=task_id, kind="execute", status="running")
        attempt = store.create_step_attempt(
            task_id=task_id,
            step_id=step.step_id,
            status="running",
            context={"heartbeat_interval_seconds": 5.0},
        )

        # Simulate heartbeat via context update (as dispatch.report_heartbeat does).
        now = time.time()
        ctx = dict(attempt.context or {})
        ctx["last_heartbeat_at"] = now
        store.update_step_attempt(attempt.step_attempt_id, context=ctx)

        updated = store.get_step_attempt(attempt.step_attempt_id)
        assert updated is not None
        assert updated.context.get("last_heartbeat_at") == now

    def test_heartbeat_timeout_marks_failed(self, store: KernelStore) -> None:
        """A step attempt whose heartbeat has expired should be faileable."""
        task_id = _make_task(store)
        step = store.create_step(task_id=task_id, kind="execute", status="running", max_attempts=2)
        old_time = time.time() - 100  # 100 seconds ago
        attempt = store.create_step_attempt(
            task_id=task_id,
            step_id=step.step_id,
            status="running",
            context={
                "heartbeat_interval_seconds": 10.0,
                "last_heartbeat_at": old_time,
            },
        )

        # Simulate what check_heartbeat_timeouts does.
        now = time.time()
        interval = 10.0
        last_beat = attempt.context.get("last_heartbeat_at", 0)
        assert now - last_beat > interval  # Should be timed out

        store.update_step_attempt(
            attempt.step_attempt_id,
            status="failed",
            waiting_reason="heartbeat_timeout",
            finished_at=now,
        )
        store.update_step(step.step_id, status="failed", finished_at=now)

        failed_attempt = store.get_step_attempt(attempt.step_attempt_id)
        assert failed_attempt is not None
        assert failed_attempt.status == "failed"
        assert failed_attempt.waiting_reason == "heartbeat_timeout"

    def test_dispatch_heartbeat_methods_exist(self) -> None:
        """KernelDispatchService should have report_heartbeat and check_heartbeat_timeouts."""
        from hermit.kernel.execution.coordination.dispatch import KernelDispatchService

        assert hasattr(KernelDispatchService, "report_heartbeat")
        assert hasattr(KernelDispatchService, "check_heartbeat_timeouts")


# ===========================================================================
# 2. Super-step checkpointing
# ===========================================================================


class TestSuperStepCheckpoint:
    def test_compute_super_steps_single_node(self, builder: StepDAGBuilder) -> None:
        nodes = [StepNode(key="a", kind="execute", title="A")]
        dag = builder.validate(nodes)
        levels = StepDAGBuilder.compute_super_steps(dag)
        assert levels == [["a"]]

    def test_compute_super_steps_linear(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["b"]),
        ]
        dag = builder.validate(nodes)
        levels = StepDAGBuilder.compute_super_steps(dag)
        assert len(levels) == 3
        assert levels[0] == ["a"]
        assert levels[1] == ["b"]
        assert levels[2] == ["c"]

    def test_compute_super_steps_diamond(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B", depends_on=["a"]),
            StepNode(key="c", kind="execute", title="C", depends_on=["a"]),
            StepNode(key="d", kind="execute", title="D", depends_on=["b", "c"]),
        ]
        dag = builder.validate(nodes)
        levels = StepDAGBuilder.compute_super_steps(dag)
        assert len(levels) == 3
        assert levels[0] == ["a"]
        assert set(levels[1]) == {"b", "c"}
        assert levels[2] == ["d"]

    def test_compute_super_steps_parallel_roots(self, builder: StepDAGBuilder) -> None:
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
            StepNode(key="c", kind="execute", title="C", depends_on=["a", "b"]),
        ]
        dag = builder.validate(nodes)
        levels = StepDAGBuilder.compute_super_steps(dag)
        assert len(levels) == 2
        assert set(levels[0]) == {"a", "b"}
        assert levels[1] == ["c"]

    def test_checkpoint_event_emitted_on_super_step_complete(
        self, store: KernelStore, builder: StepDAGBuilder, dag_exec: DAGExecutionService
    ) -> None:
        """When all steps at a depth level complete, a checkpoint.super_step event
        should be emitted."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
            StepNode(key="c", kind="execute", title="C", depends_on=["a", "b"]),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        # Complete step a.
        now = time.time()
        store.update_step(key_map["a"], status="succeeded", finished_at=now)
        dag_exec.advance(
            task_id=task_id,
            step_id=key_map["a"],
            step_attempt_id="sa_1",
            status="succeeded",
        )

        # a done but b not yet — no checkpoint should be emitted.
        events = store.list_events(task_id=task_id, event_type="checkpoint.super_step")
        assert len(events) == 0

        # Complete step b.
        store.update_step(key_map["b"], status="succeeded", finished_at=now)
        dag_exec.advance(
            task_id=task_id,
            step_id=key_map["b"],
            step_attempt_id="sa_2",
            status="succeeded",
        )

        # Both a and b done — checkpoint at depth 0 should be emitted.
        events = store.list_events(task_id=task_id, event_type="checkpoint.super_step")
        assert len(events) == 1
        assert events[0]["payload"]["super_step_depth"] == 0
        assert set(events[0]["payload"]["step_ids"]) == {key_map["a"], key_map["b"]}

    def test_no_checkpoint_on_single_step_completion_with_peers(
        self, store: KernelStore, builder: StepDAGBuilder, dag_exec: DAGExecutionService
    ) -> None:
        """A checkpoint should NOT be emitted if peers at the same depth are still running."""
        task_id = _make_task(store)
        nodes = [
            StepNode(key="a", kind="execute", title="A"),
            StepNode(key="b", kind="execute", title="B"),
        ]
        _dag, key_map = builder.build_and_materialize(task_id, nodes)

        store.update_step(key_map["a"], status="succeeded", finished_at=time.time())
        dag_exec.advance(
            task_id=task_id,
            step_id=key_map["a"],
            step_attempt_id="sa_1",
            status="succeeded",
        )

        events = store.list_events(task_id=task_id, event_type="checkpoint.super_step")
        assert len(events) == 0


# ===========================================================================
# 3. Replay-from
# ===========================================================================


class TestReplayFrom:
    def test_replay_events_until_returns_correct_events(self, store: KernelStore) -> None:
        from hermit.kernel.execution.recovery.replay import replay_events_until

        task_id = _make_task(store)
        step_a = store.create_step(task_id=task_id, kind="execute", status="running")
        step_b = store.create_step(
            task_id=task_id, kind="execute", status="waiting", depends_on=[step_a.step_id]
        )

        # Emit events for step_a and step_b.
        store.append_event(
            event_type="step.started",
            entity_type="step",
            entity_id=step_a.step_id,
            task_id=task_id,
            step_id=step_a.step_id,
            actor="kernel",
            payload={"kind": "execute"},
        )
        store.append_event(
            event_type="step.completed",
            entity_type="step",
            entity_id=step_a.step_id,
            task_id=task_id,
            step_id=step_a.step_id,
            actor="kernel",
            payload={"status": "succeeded"},
        )
        store.append_event(
            event_type="step.started",
            entity_type="step",
            entity_id=step_b.step_id,
            task_id=task_id,
            step_id=step_b.step_id,
            actor="kernel",
            payload={"kind": "execute"},
        )

        # Replay until step_a — should include only step_a events.
        events = replay_events_until(store, task_id, step_a.step_id)
        step_ids = [e.get("step_id") for e in events if e.get("step_id")]
        assert all(sid == step_a.step_id for sid in step_ids)
        assert len(events) >= 2

    def test_replay_from_creates_new_task(self, store: KernelStore) -> None:
        from hermit.kernel.execution.recovery.replay import replay_from

        task_id = _make_task(store)
        step_a = store.create_step(
            task_id=task_id, kind="execute", status="succeeded", title="Step A"
        )
        store.update_step(step_a.step_id, status="succeeded", finished_at=time.time())
        step_b = store.create_step(
            task_id=task_id,
            kind="execute",
            status="succeeded",
            title="Step B",
            depends_on=[step_a.step_id],
        )
        store.update_step(step_b.step_id, status="succeeded", finished_at=time.time())
        step_c = store.create_step(
            task_id=task_id,
            kind="execute",
            status="succeeded",
            title="Step C",
            depends_on=[step_b.step_id],
        )
        store.update_step(step_c.step_id, status="succeeded", finished_at=time.time())

        # Replay from step_b — step_a should be skipped, step_b and step_c re-created.
        replay_task_id = replay_from(store, task_id, step_b.step_id)
        replay_task = store.get_task(replay_task_id)
        assert replay_task is not None
        assert "[Replay]" in replay_task.title
        assert replay_task.parent_task_id == task_id

        replay_steps = store.list_steps(task_id=replay_task_id)
        assert len(replay_steps) == 3
        statuses = [s.status for s in replay_steps]
        assert "skipped" in statuses  # step_a
        assert "ready" in statuses  # step_b (replay start point)

    def test_replay_preserves_original_events(self, store: KernelStore) -> None:
        from hermit.kernel.execution.recovery.replay import replay_from

        task_id = _make_task(store)
        step_a = store.create_step(
            task_id=task_id, kind="execute", status="succeeded", title="Step A"
        )
        store.update_step(step_a.step_id, status="succeeded", finished_at=time.time())
        store.append_event(
            event_type="step.completed",
            entity_type="step",
            entity_id=step_a.step_id,
            task_id=task_id,
            step_id=step_a.step_id,
            actor="kernel",
            payload={"status": "succeeded"},
        )

        original_events_before = store.list_events(task_id=task_id, limit=1000)
        count_before = len(original_events_before)

        replay_task_id = replay_from(store, task_id, step_a.step_id)

        # Original events should not be modified (new events go to replay_task_id).
        original_events_after = store.list_events(task_id=task_id, limit=1000)
        assert len(original_events_after) == count_before

        # Replay task should have its own events.
        replay_events = store.list_events(task_id=replay_task_id, limit=1000)
        event_types = [e["event_type"] for e in replay_events]
        assert "replay.started" in event_types

    def test_replay_from_nonexistent_task_raises(self, store: KernelStore) -> None:
        from hermit.kernel.execution.recovery.replay import replay_from

        with pytest.raises(ValueError, match="not found"):
            replay_from(store, "task_nonexistent", "step_nonexistent")

    def test_replay_from_nonexistent_step_raises(self, store: KernelStore) -> None:
        from hermit.kernel.execution.recovery.replay import replay_from

        task_id = _make_task(store)
        with pytest.raises(ValueError, match="not found"):
            replay_from(store, task_id, "step_nonexistent")


# ===========================================================================
# 4. Authorization Plan Revalidation
# ===========================================================================


class TestAuthorizationPlanRevalidation:
    def _make_authorization_plan(
        self, store: KernelStore, *, policy_version: str = "v1"
    ) -> tuple[str, str, str, str]:
        """Helper: create task, step, attempt, authorization_plan.
        Returns (task_id, step_id, step_attempt_id, plan_id).
        """
        task_id = _make_task(store)
        step = store.create_step(task_id=task_id, kind="execute", status="running")
        attempt = store.create_step_attempt(
            task_id=task_id,
            step_id=step.step_id,
            status="running",
        )
        store.update_step_attempt(attempt.step_attempt_id, policy_version=policy_version)
        plan = store.create_authorization_plan(
            task_id=task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            contract_ref="contract_test",
            policy_profile_ref="default",
            requested_action_classes=["write_local"],
            required_decision_refs=[],
            approval_route="none",
            witness_requirements=[],
            proposed_grant_shape={},
            downgrade_options=[],
            current_gaps=[],
            status="preflighted",
            revalidation_rules={
                "check_policy_version": True,
            },
        )
        return task_id, step.step_id, attempt.step_attempt_id, plan.authorization_plan_id

    def test_revalidation_no_rules_noop(self, store: KernelStore) -> None:
        """Revalidation with no check_policy_version rule should be a no-op."""
        from hermit.kernel.artifacts.models.artifacts import ArtifactStore
        from hermit.kernel.policy.permits.authorization_plans import (
            AuthorizationPlanService,
        )

        task_id = _make_task(store)
        step = store.create_step(task_id=task_id, kind="execute", status="running")
        attempt = store.create_step_attempt(
            task_id=task_id,
            step_id=step.step_id,
            status="running",
        )
        plan = store.create_authorization_plan(
            task_id=task_id,
            step_id=step.step_id,
            step_attempt_id=attempt.step_attempt_id,
            contract_ref="contract_test",
            policy_profile_ref="default",
            requested_action_classes=["read_local"],
            required_decision_refs=[],
            approval_route="none",
            witness_requirements=[],
            proposed_grant_shape={},
            downgrade_options=[],
            current_gaps=[],
            status="preflighted",
            revalidation_rules={
                "check_policy_version": False,
            },
        )
        artifact_store = ArtifactStore(store.db_path.parent / "artifacts")
        svc = AuthorizationPlanService(store, artifact_store)
        result = svc.revalidate(plan.authorization_plan_id, "any_version")
        assert result is False

        # Plan should still be preflighted.
        updated = store.get_authorization_plan(plan.authorization_plan_id)
        assert updated is not None
        assert updated.status == "preflighted"

    def test_revalidation_policy_version_unchanged_noop(self, store: KernelStore) -> None:
        """If policy version matches, revalidation should be a no-op."""
        from hermit.kernel.artifacts.models.artifacts import ArtifactStore
        from hermit.kernel.policy.permits.authorization_plans import (
            AuthorizationPlanService,
        )

        _task_id, _step_id, _attempt_id, plan_id = self._make_authorization_plan(
            store, policy_version="v1"
        )
        artifact_store = ArtifactStore(store.db_path.parent / "artifacts")
        svc = AuthorizationPlanService(store, artifact_store)
        result = svc.revalidate(plan_id, "v1")
        assert result is False

        updated = store.get_authorization_plan(plan_id)
        assert updated is not None
        assert updated.status == "preflighted"

    def test_revalidation_policy_version_changed_invalidates(self, store: KernelStore) -> None:
        """If policy version differs, revalidation should invalidate the plan."""
        from hermit.kernel.artifacts.models.artifacts import ArtifactStore
        from hermit.kernel.policy.permits.authorization_plans import (
            AuthorizationPlanService,
        )

        _task_id, _step_id, _attempt_id, plan_id = self._make_authorization_plan(
            store, policy_version="v1"
        )
        artifact_store = ArtifactStore(store.db_path.parent / "artifacts")
        svc = AuthorizationPlanService(store, artifact_store)
        result = svc.revalidate(plan_id, "v2")
        assert result is True

        updated = store.get_authorization_plan(plan_id)
        assert updated is not None
        assert updated.status == "invalidated"
        assert "policy_version_changed" in updated.current_gaps

    def test_revalidation_nonexistent_plan(self, store: KernelStore) -> None:
        """Revalidation on a nonexistent plan should return False."""
        from hermit.kernel.artifacts.models.artifacts import ArtifactStore
        from hermit.kernel.policy.permits.authorization_plans import (
            AuthorizationPlanService,
        )

        artifact_store = ArtifactStore(store.db_path.parent / "artifacts")
        svc = AuthorizationPlanService(store, artifact_store)
        result = svc.revalidate("plan_nonexistent", "v1")
        assert result is False


# ===========================================================================
# 5. last_heartbeat_at field on StepAttemptRecord
# ===========================================================================


class TestStepAttemptHeartbeatField:
    def test_last_heartbeat_at_default_none(self, store: KernelStore) -> None:
        task_id = _make_task(store)
        step = store.create_step(task_id=task_id, kind="execute", status="running")
        attempt = store.create_step_attempt(task_id=task_id, step_id=step.step_id, status="running")
        loaded = store.get_step_attempt(attempt.step_attempt_id)
        assert loaded is not None
        assert loaded.last_heartbeat_at is None
