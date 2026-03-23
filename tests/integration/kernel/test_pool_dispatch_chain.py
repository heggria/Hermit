"""Integration test: PoolAwareDispatchService dispatch chain.

Validates the full chain from step kind → role mapping → slot claim →
dispatch → release, exercising the real PoolAwareDispatchService with
faked store/runner but real WorkerPoolManager internals.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.execution.coordination.pool_dispatch import (
    _DEFAULT_ROLE,
    _KIND_TO_ROLE,
    PoolAwareDispatchService,
    _default_pool_config,
    step_kind_to_role,
)
from hermit.kernel.execution.workers.models import (
    DEFAULT_CONFLICT_LIMITS,
    WorkerPoolStatus,
    WorkerRole,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_fake_step(*, kind: str = "execute", step_id: str = "step-1") -> SimpleNamespace:
    return SimpleNamespace(step_id=step_id, kind=kind)


def _make_fake_attempt(
    *,
    step_attempt_id: str = "attempt-1",
    step_id: str = "step-1",
    task_id: str = "task-1",
    status: str = "ready",
    context: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        step_attempt_id=step_attempt_id,
        step_id=step_id,
        task_id=task_id,
        status=status,
        attempt=1,
        context=context or {},
        claimed_at=None,
        started_at=None,
        last_heartbeat_at=None,
    )


def _make_fake_store(
    *,
    ready_attempts: list[Any] | None = None,
    steps: dict[str, Any] | None = None,
) -> SimpleNamespace:
    ready_queue = list(ready_attempts or [])
    step_map = dict(steps or {})

    def list_step_attempts(*, status: str = "", limit: int = 100) -> list[Any]:
        if status == "ready":
            return list(ready_queue)
        return []

    def claim_next_ready_step_attempt() -> Any | None:
        if ready_queue:
            return ready_queue.pop(0)
        return None

    def get_step_attempt(step_attempt_id: str) -> Any | None:
        for a in ready_queue:
            if a.step_attempt_id == step_attempt_id:
                return a
        return None

    def get_step(step_id: str) -> Any | None:
        return step_map.get(step_id)

    return SimpleNamespace(
        list_step_attempts=list_step_attempts,
        claim_next_ready_step_attempt=claim_next_ready_step_attempt,
        get_step_attempt=get_step_attempt,
        get_step=get_step,
        update_step_attempt=MagicMock(),
        update_step=MagicMock(),
        update_task_status=MagicMock(),
        propagate_step_failure=MagicMock(),
        has_non_terminal_steps=MagicMock(return_value=True),
        get_task=MagicMock(return_value=None),
        retry_step=MagicMock(),
    )


def _make_fake_runner(store: Any | None = None) -> SimpleNamespace:
    fake_store = store or _make_fake_store()
    return SimpleNamespace(
        task_controller=SimpleNamespace(store=fake_store),
        process_claimed_attempt=MagicMock(),
    )


# ── 1. Step kind → role mapping completeness ────────────────────────────────


class TestStepKindRoleMappingCompleteness:
    """Verify ALL documented step kinds map to valid roles."""

    @pytest.mark.parametrize(
        ("kind", "expected_role"),
        [
            # planner
            ("plan", WorkerRole.planner),
            ("decompose", WorkerRole.planner),
            # spec (dedicated)
            ("spec", WorkerRole.spec),
            # executor
            ("execute", WorkerRole.executor),
            ("code", WorkerRole.executor),
            ("patch", WorkerRole.executor),
            ("edit", WorkerRole.executor),
            ("publish", WorkerRole.executor),
            ("rollback", WorkerRole.executor),
            # verifier
            ("review", WorkerRole.verifier),
            ("verify", WorkerRole.verifier),
            ("check", WorkerRole.verifier),
            # benchmarker
            ("benchmark", WorkerRole.benchmarker),
            # tester
            ("test", WorkerRole.tester),
            ("run_tests", WorkerRole.tester),
            # researcher
            ("search", WorkerRole.researcher),
            ("research", WorkerRole.researcher),
            ("inspect", WorkerRole.researcher),
            # reconciler
            ("reconcile", WorkerRole.reconciler),
            ("learn", WorkerRole.reconciler),
        ],
    )
    def test_kind_maps_to_expected_role(self, kind: str, expected_role: WorkerRole) -> None:
        assert step_kind_to_role(kind) == expected_role

    def test_all_documented_kinds_are_present_in_mapping(self) -> None:
        """Ensure the _KIND_TO_ROLE dict covers every kind we expect."""
        expected_kinds = {
            "plan",
            "decompose",
            "spec",
            "execute",
            "code",
            "patch",
            "edit",
            "publish",
            "rollback",
            "review",
            "verify",
            "check",
            "benchmark",
            "test",
            "run_tests",
            "search",
            "research",
            "inspect",
            "reconcile",
            "learn",
        }
        actual_kinds = set(_KIND_TO_ROLE.keys())
        assert expected_kinds == actual_kinds, (
            f"Missing: {expected_kinds - actual_kinds}, Extra: {actual_kinds - expected_kinds}"
        )

    def test_all_mapped_roles_exist_in_worker_role_enum(self) -> None:
        """Every role value in _KIND_TO_ROLE must be a valid WorkerRole member."""
        valid_roles = set(WorkerRole)
        for kind, role in _KIND_TO_ROLE.items():
            assert role in valid_roles, f"Kind '{kind}' maps to invalid role: {role}"


# ── 2. Default pool config ──────────────────────────────────────────────────


class TestDefaultPoolConfig:
    """Verify default config has correct per-role limits."""

    def test_executor_max_active(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.executor].max_active == 4

    def test_verifier_max_active(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.verifier].max_active == 3

    def test_planner_max_active(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.planner].max_active == 2

    def test_benchmarker_max_active(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.benchmarker].max_active == 2

    def test_researcher_max_active(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.researcher].max_active == 2

    def test_tester_max_active(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.tester].max_active == 2

    def test_spec_max_active(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.spec].max_active == 1

    def test_reconciler_max_active(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.reconciler].max_active == 1

    def test_all_eight_roles_have_slot_configs(self) -> None:
        cfg = _default_pool_config()
        expected_roles = {
            WorkerRole.executor,
            WorkerRole.verifier,
            WorkerRole.planner,
            WorkerRole.benchmarker,
            WorkerRole.researcher,
            WorkerRole.tester,
            WorkerRole.spec,
            WorkerRole.reconciler,
        }
        assert set(cfg.slots.keys()) == expected_roles


# ── 3. Unknown step kind fallback ────────────────────────────────────────────


class TestUnknownStepKindFallback:
    """Verify unknown kinds fall back to executor role."""

    @pytest.mark.parametrize("kind", ["unknown", "foo_bar", "", "EXECUTE", "Plan"])
    def test_unknown_kind_returns_executor(self, kind: str) -> None:
        assert step_kind_to_role(kind) == WorkerRole.executor

    def test_default_role_is_executor(self) -> None:
        assert WorkerRole.executor == _DEFAULT_ROLE


# ── 4. Total worker count ────────────────────────────────────────────────────


class TestTotalWorkerCount:
    """Verify total = sum of all per-role max_active values."""

    def test_total_equals_sum_of_role_max_active(self) -> None:
        cfg = _default_pool_config()
        expected_total = sum(sc.max_active for sc in cfg.slots.values())
        # 4 + 3 + 2 + 2 + 2 + 2 + 1 + 1 = 17
        assert expected_total == 17

    def test_pool_dispatch_service_thread_pool_matches_total(self) -> None:
        """PoolAwareDispatchService creates a ThreadPoolExecutor with
        total = sum(per-role max_active)."""
        runner = _make_fake_runner()
        svc = PoolAwareDispatchService(runner)
        assert svc._inner.worker_count == 17
        svc.stop()


# ── 5. Conflict limits ──────────────────────────────────────────────────────


class TestConflictLimits:
    """Verify default max_same_workspace=1, max_same_module=2."""

    def test_default_conflict_limits_constant(self) -> None:
        assert DEFAULT_CONFLICT_LIMITS == {
            "max_same_workspace": 1,
            "max_same_module": 2,
        }

    def test_default_pool_config_uses_default_conflict_limits(self) -> None:
        cfg = _default_pool_config()
        assert cfg.conflict_limits["max_same_workspace"] == 1
        assert cfg.conflict_limits["max_same_module"] == 2

    def test_workspace_conflict_enforced(self) -> None:
        """Two workers cannot operate on the same workspace with max_same_workspace=1."""
        runner = _make_fake_runner()
        svc = PoolAwareDispatchService(runner)  # uses default config
        pool = svc._pool

        s1 = pool.claim_slot(WorkerRole.executor, workspace="ws-alpha")
        s2 = pool.claim_slot(WorkerRole.executor, workspace="ws-alpha")
        assert s1 is not None
        assert s2 is None  # blocked by workspace conflict
        svc.stop()

    def test_module_conflict_enforced(self) -> None:
        """Only max_same_module=2 workers can target the same module."""
        runner = _make_fake_runner()
        svc = PoolAwareDispatchService(runner)
        pool = svc._pool

        s1 = pool.claim_slot(WorkerRole.executor, module="kernel.policy")
        s2 = pool.claim_slot(WorkerRole.executor, module="kernel.policy")
        s3 = pool.claim_slot(WorkerRole.executor, module="kernel.policy")
        assert s1 is not None
        assert s2 is not None
        assert s3 is None  # blocked by module conflict
        svc.stop()


# ── 6. Output artifact kinds ────────────────────────────────────────────────


class TestOutputArtifactKinds:
    """Verify each role config has output_artifact_kinds populated."""

    def test_all_roles_have_output_artifact_kinds(self) -> None:
        cfg = _default_pool_config()
        for role, slot_cfg in cfg.slots.items():
            assert len(slot_cfg.output_artifact_kinds) > 0, (
                f"Role {role} has empty output_artifact_kinds"
            )

    @pytest.mark.parametrize(
        ("role", "expected_kind"),
        [
            (WorkerRole.executor, "diff"),
            (WorkerRole.executor, "command_output"),
            (WorkerRole.verifier, "verdict"),
            (WorkerRole.verifier, "critique"),
            (WorkerRole.planner, "contract_packet"),
            (WorkerRole.planner, "dag_fragment"),
            (WorkerRole.benchmarker, "benchmark_report"),
            (WorkerRole.benchmarker, "raw_metrics"),
            (WorkerRole.researcher, "evidence_bundle"),
            (WorkerRole.researcher, "inspection_report"),
            (WorkerRole.tester, "test_report"),
            (WorkerRole.spec, "iteration_spec"),
            (WorkerRole.reconciler, "reconciliation_record"),
            (WorkerRole.reconciler, "lesson_pack"),
        ],
    )
    def test_specific_output_artifact_kind_present(
        self, role: WorkerRole, expected_kind: str
    ) -> None:
        cfg = _default_pool_config()
        assert expected_kind in cfg.slots[role].output_artifact_kinds, (
            f"Role {role} missing output_artifact_kind '{expected_kind}'"
        )


# ── 7. Pool lifecycle ────────────────────────────────────────────────────────


class TestPoolLifecycle:
    """Create PoolAwareDispatchService, get_pool_status, verify all slots idle.
    Start/stop lifecycle."""

    def test_initial_pool_status_all_idle(self) -> None:
        runner = _make_fake_runner()
        svc = PoolAwareDispatchService(runner)
        status = svc.get_pool_status()

        assert isinstance(status, WorkerPoolStatus)
        assert status.pool_id == "kernel-dispatch"
        assert status.active_slots == 0
        assert status.interrupted_slots == 0
        assert status.idle_slots == 17
        # Every role should report 0 active
        for role_name, active_count in status.by_role.items():
            assert active_count == 0, f"Role {role_name} should be idle, got {active_count}"
        svc.stop()

    def test_start_creates_daemon_threads(self) -> None:
        runner = _make_fake_runner()
        svc = PoolAwareDispatchService(runner)
        svc.start()

        assert svc._inner.thread is not None
        assert svc._inner.thread.is_alive()
        assert svc._inner.thread.daemon is True
        assert svc._inner.thread.name == "kernel-pool-dispatch-loop"

        assert svc._inner.reaper_thread is not None
        assert svc._inner.reaper_thread.is_alive()
        assert svc._inner.reaper_thread.daemon is True
        assert svc._inner.reaper_thread.name == "lease-reaper"

        svc.stop()
        svc._inner.thread.join(timeout=3)
        assert not svc._inner.thread.is_alive()

    def test_stop_signals_and_shuts_down(self) -> None:
        runner = _make_fake_runner()
        svc = PoolAwareDispatchService(runner)
        svc.start()
        svc.stop()
        svc._inner.thread.join(timeout=3)
        assert svc._inner.stop_event.is_set()
        assert not svc._inner.thread.is_alive()

    def test_wake_does_not_raise_when_not_running(self) -> None:
        runner = _make_fake_runner()
        svc = PoolAwareDispatchService(runner)
        svc.wake()  # should not raise
        svc.stop()

    def test_pool_status_after_claim_and_release(self) -> None:
        """Claim a slot, verify active count, release, verify idle count."""
        runner = _make_fake_runner()
        svc = PoolAwareDispatchService(runner)

        slot = svc._pool.claim_slot(WorkerRole.verifier)
        assert slot is not None

        status = svc.get_pool_status()
        assert status.active_slots == 1
        assert status.idle_slots == 16
        assert status.by_role["verifier"] == 1

        svc._pool.release_slot(slot.slot_id)

        status = svc.get_pool_status()
        assert status.active_slots == 0
        assert status.idle_slots == 17
        assert status.by_role["verifier"] == 0
        svc.stop()


# ── 8. Role resolution from attempt context ─────────────────────────────────


class TestRoleResolutionFromAttemptContext:
    """Mock a step attempt with kind="review", verify _resolve_role_for_attempt
    returns verifier."""

    def test_resolve_review_kind_to_verifier(self) -> None:
        step = _make_fake_step(kind="review", step_id="s-review")
        attempt = _make_fake_attempt(step_attempt_id="a-review", step_id="s-review")
        store = _make_fake_store(
            ready_attempts=[attempt],
            steps={"s-review": step},
        )
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner)

        role = svc._resolve_role_for_attempt("a-review")
        assert role == WorkerRole.verifier
        svc.stop()

    @pytest.mark.parametrize(
        ("kind", "expected_role"),
        [
            ("plan", WorkerRole.planner),
            ("code", WorkerRole.executor),
            ("benchmark", WorkerRole.benchmarker),
            ("test", WorkerRole.tester),
            ("search", WorkerRole.researcher),
            ("reconcile", WorkerRole.reconciler),
            ("spec", WorkerRole.spec),
            ("learn", WorkerRole.reconciler),
        ],
    )
    def test_resolve_various_kinds(self, kind: str, expected_role: WorkerRole) -> None:
        step = _make_fake_step(kind=kind, step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")
        store = _make_fake_store(
            ready_attempts=[attempt],
            steps={"s1": step},
        )
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner)

        role = svc._resolve_role_for_attempt("a1")
        assert role == expected_role
        svc.stop()

    def test_resolve_missing_attempt_falls_back_to_executor(self) -> None:
        store = _make_fake_store()
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner)

        role = svc._resolve_role_for_attempt("nonexistent")
        assert role == _DEFAULT_ROLE
        assert role == WorkerRole.executor
        svc.stop()

    def test_resolve_attempt_with_missing_step_falls_back(self) -> None:
        """Attempt exists but the parent step is missing from the store."""
        attempt = _make_fake_attempt(step_attempt_id="a-orphan", step_id="s-missing")
        store = _make_fake_store(ready_attempts=[attempt], steps={})
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner)

        role = svc._resolve_role_for_attempt("a-orphan")
        assert role == WorkerRole.executor
        svc.stop()


# ── 9. End-to-end dispatch chain: kind → slot → dispatch → release ──────────


class TestDispatchChainEndToEnd:
    """Full chain: step with a known kind → slot claimed → future submitted →
    future completes → slot released."""

    def test_dispatch_and_release_executor(self) -> None:
        step = _make_fake_step(kind="code", step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")
        store = _make_fake_store(ready_attempts=[attempt], steps={"s1": step})
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner)

        # Dispatch claims a slot
        result = svc._try_claim_and_dispatch()
        assert result == "a1"

        status = svc.get_pool_status()
        assert status.active_slots == 1
        assert status.by_role["executor"] == 1

        # Simulate future completion
        svc._reap_futures()

        # After reaping, all slots should be idle again
        # (the future completes immediately since the handler is a mock)
        # Give it a moment for the future to complete
        import time

        time.sleep(0.1)
        svc._reap_futures()

        status = svc.get_pool_status()
        assert status.active_slots == 0
        assert status.by_role["executor"] == 0
        svc.stop()

    def test_dispatch_chain_for_verifier_kind(self) -> None:
        step = _make_fake_step(kind="review", step_id="s-rev")
        attempt = _make_fake_attempt(step_attempt_id="a-rev", step_id="s-rev")
        store = _make_fake_store(ready_attempts=[attempt], steps={"s-rev": step})
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner)

        result = svc._try_claim_and_dispatch()
        assert result == "a-rev"

        status = svc.get_pool_status()
        assert status.by_role["verifier"] == 1
        svc.stop()

    def test_dispatch_chain_for_planner_kind(self) -> None:
        step = _make_fake_step(kind="plan", step_id="s-plan")
        attempt = _make_fake_attempt(step_attempt_id="a-plan", step_id="s-plan")
        store = _make_fake_store(ready_attempts=[attempt], steps={"s-plan": step})
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner)

        result = svc._try_claim_and_dispatch()
        assert result == "a-plan"

        status = svc.get_pool_status()
        assert status.by_role["planner"] == 1
        svc.stop()

    def test_slot_released_on_race_condition(self) -> None:
        """If peek succeeds but claim_next_ready returns None (race), slot
        must be released to avoid leaking."""
        step = _make_fake_step(kind="execute", step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")
        store = _make_fake_store(ready_attempts=[], steps={"s1": step})

        # Override list_step_attempts to return the attempt (peek succeeds)
        # but claim_next_ready returns None (claim fails)
        peek_list = [attempt]

        def patched_list(*, status: str = "", limit: int = 100) -> list[Any]:
            if status == "ready":
                return peek_list
            return []

        store.list_step_attempts = patched_list

        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner)

        result = svc._try_claim_and_dispatch()
        assert result is None

        status = svc.get_pool_status()
        assert status.active_slots == 0
        svc.stop()

    def test_slot_released_on_submit_failure(self) -> None:
        """If executor.submit() raises, the claimed pool slot must not leak."""
        step = _make_fake_step(kind="execute", step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")
        store = _make_fake_store(ready_attempts=[attempt], steps={"s1": step})
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner)

        svc._inner.executor.shutdown(wait=False)
        svc._inner.executor.submit = MagicMock(side_effect=RuntimeError("executor shutdown"))

        with pytest.raises(RuntimeError, match="executor shutdown"):
            svc._try_claim_and_dispatch()

        status = svc.get_pool_status()
        assert status.active_slots == 0
        svc.stop()
