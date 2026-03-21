"""Tests for PoolAwareDispatchService — role-aware slot management."""

from __future__ import annotations

import concurrent.futures
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from hermit.kernel.execution.coordination.pool_dispatch import (
    _DEFAULT_ROLE,
    PoolAwareDispatchService,
    _default_pool_config,
    step_kind_to_role,
)
from hermit.kernel.execution.workers.models import (
    WorkerPoolConfig,
    WorkerPoolStatus,
    WorkerRole,
    WorkerSlotConfig,
)

# ── step_kind_to_role mapping ───────────────────────────────────────────────


class TestStepKindToRole:
    def test_plan_maps_to_planner(self) -> None:
        assert step_kind_to_role("plan") == WorkerRole.planner

    def test_decompose_maps_to_planner(self) -> None:
        assert step_kind_to_role("decompose") == WorkerRole.planner

    def test_research_maps_to_researcher(self) -> None:
        assert step_kind_to_role("research") == WorkerRole.researcher

    def test_inspect_maps_to_researcher(self) -> None:
        assert step_kind_to_role("inspect") == WorkerRole.researcher

    def test_spec_maps_to_spec(self) -> None:
        assert step_kind_to_role("spec") == WorkerRole.spec

    def test_execute_maps_to_executor(self) -> None:
        assert step_kind_to_role("execute") == WorkerRole.executor

    def test_code_maps_to_executor(self) -> None:
        assert step_kind_to_role("code") == WorkerRole.executor

    def test_patch_maps_to_executor(self) -> None:
        assert step_kind_to_role("patch") == WorkerRole.executor

    def test_edit_maps_to_executor(self) -> None:
        assert step_kind_to_role("edit") == WorkerRole.executor

    def test_publish_maps_to_executor(self) -> None:
        assert step_kind_to_role("publish") == WorkerRole.executor

    def test_rollback_maps_to_executor(self) -> None:
        assert step_kind_to_role("rollback") == WorkerRole.executor

    def test_review_maps_to_verifier(self) -> None:
        assert step_kind_to_role("review") == WorkerRole.verifier

    def test_verify_maps_to_verifier(self) -> None:
        assert step_kind_to_role("verify") == WorkerRole.verifier

    def test_check_maps_to_verifier(self) -> None:
        assert step_kind_to_role("check") == WorkerRole.verifier

    def test_benchmark_maps_to_benchmarker(self) -> None:
        assert step_kind_to_role("benchmark") == WorkerRole.benchmarker

    def test_test_maps_to_tester(self) -> None:
        assert step_kind_to_role("test") == WorkerRole.tester

    def test_run_tests_maps_to_tester(self) -> None:
        assert step_kind_to_role("run_tests") == WorkerRole.tester

    def test_search_maps_to_researcher(self) -> None:
        assert step_kind_to_role("search") == WorkerRole.researcher

    def test_reconcile_maps_to_reconciler(self) -> None:
        assert step_kind_to_role("reconcile") == WorkerRole.reconciler

    def test_learn_maps_to_reconciler(self) -> None:
        assert step_kind_to_role("learn") == WorkerRole.reconciler

    def test_unknown_kind_falls_back_to_executor(self) -> None:
        assert step_kind_to_role("unknown_thing") == WorkerRole.executor

    def test_empty_string_falls_back_to_executor(self) -> None:
        assert step_kind_to_role("") == WorkerRole.executor


# ── default pool config ─────────────────────────────────────────────────────


class TestDefaultPoolConfig:
    def test_pool_id(self) -> None:
        cfg = _default_pool_config()
        assert cfg.pool_id == "kernel-dispatch"

    def test_executor_slots(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.executor].max_active == 4

    def test_verifier_slots(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.verifier].max_active == 3

    def test_planner_slots(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.planner].max_active == 2

    def test_benchmarker_slots(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.benchmarker].max_active == 2

    def test_researcher_slots(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.researcher].max_active == 2

    def test_reconciler_slots(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.reconciler].max_active == 1

    def test_conflict_limits(self) -> None:
        cfg = _default_pool_config()
        assert cfg.conflict_limits == {"max_same_workspace": 1, "max_same_module": 2}

    def test_total_slots(self) -> None:
        """Total = sum of all per-role max_active, not a hard global cap."""
        cfg = _default_pool_config()
        total = sum(sc.max_active for sc in cfg.slots.values())
        assert total == 17  # 4+3+2+2+2+2+1+1

    def test_all_mapped_roles_have_slots(self) -> None:
        """Every role referenced in _KIND_TO_ROLE must have a slot config."""
        cfg = _default_pool_config()
        for role in (
            WorkerRole.planner,
            WorkerRole.executor,
            WorkerRole.verifier,
            WorkerRole.benchmarker,
            WorkerRole.researcher,
            WorkerRole.reconciler,
            WorkerRole.tester,
            WorkerRole.spec,
        ):
            assert role in cfg.slots, f"{role} missing from default config"

    def test_tester_slots(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.tester].max_active == 2

    def test_spec_slots(self) -> None:
        cfg = _default_pool_config()
        assert cfg.slots[WorkerRole.spec].max_active == 1

    def test_executor_output_artifact_kinds(self) -> None:
        cfg = _default_pool_config()
        assert "diff" in cfg.slots[WorkerRole.executor].output_artifact_kinds

    def test_benchmarker_output_artifact_kinds(self) -> None:
        cfg = _default_pool_config()
        assert "benchmark_report" in cfg.slots[WorkerRole.benchmarker].output_artifact_kinds


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_pool_config(
    *,
    executor_max: int = 2,
    planner_max: int = 1,
    verifier_max: int = 1,
    benchmarker_max: int = 1,
    researcher_max: int = 0,
    reconciler_max: int = 0,
    max_global_active: int = 0,
    max_per_supervisor: int = 0,
    conflict_limits: dict[str, int] | None = None,
) -> WorkerPoolConfig:
    slots: dict[WorkerRole, WorkerSlotConfig] = {
        WorkerRole.executor: WorkerSlotConfig(
            role=WorkerRole.executor,
            max_active=executor_max,
            accepted_step_kinds=["execute", "code", "patch", "edit"],
        ),
        WorkerRole.planner: WorkerSlotConfig(
            role=WorkerRole.planner,
            max_active=planner_max,
            accepted_step_kinds=["plan", "research", "spec"],
        ),
        WorkerRole.verifier: WorkerSlotConfig(
            role=WorkerRole.verifier,
            max_active=verifier_max,
            accepted_step_kinds=["review", "verify", "check"],
        ),
        WorkerRole.benchmarker: WorkerSlotConfig(
            role=WorkerRole.benchmarker,
            max_active=benchmarker_max,
            accepted_step_kinds=["benchmark", "test"],
        ),
    }
    if researcher_max > 0:
        slots[WorkerRole.researcher] = WorkerSlotConfig(
            role=WorkerRole.researcher,
            max_active=researcher_max,
            accepted_step_kinds=["search"],
        )
    if reconciler_max > 0:
        slots[WorkerRole.reconciler] = WorkerSlotConfig(
            role=WorkerRole.reconciler,
            max_active=reconciler_max,
            accepted_step_kinds=["reconcile"],
        )
    return WorkerPoolConfig(
        pool_id="test-pool",
        team_id="test-team",
        slots=slots,
        conflict_limits=conflict_limits
        if conflict_limits is not None
        else {"max_same_workspace": 1},
        max_global_active=max_global_active,
        max_per_supervisor=max_per_supervisor,
    )


def _make_fake_step(*, kind: str = "execute", step_id: str = "step-1") -> SimpleNamespace:
    return SimpleNamespace(
        step_id=step_id,
        kind=kind,
    )


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
    """Build a fake store with controllable list_step_attempts and
    claim_next_ready_step_attempt behaviour."""
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


# ── PoolAwareDispatchService construction ────────────────────────────────────


class TestPoolAwareDispatchServiceInit:
    def test_creates_with_default_config(self) -> None:
        runner = _make_fake_runner()
        svc = PoolAwareDispatchService(runner)
        status = svc.get_pool_status()
        assert status.pool_id == "kernel-dispatch"
        assert status.idle_slots == 17  # 4+3+2+2+2+2+1+1
        svc.stop()

    def test_creates_with_custom_config(self) -> None:
        runner = _make_fake_runner()
        cfg = _make_pool_config(executor_max=1, planner_max=1, verifier_max=1, benchmarker_max=1)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)
        status = svc.get_pool_status()
        assert status.pool_id == "test-pool"
        assert status.idle_slots == 4
        svc.stop()


# ── get_pool_status ──────────────────────────────────────────────────────────


class TestGetPoolStatus:
    def test_returns_worker_pool_status(self) -> None:
        runner = _make_fake_runner()
        cfg = _make_pool_config(executor_max=2, planner_max=1, verifier_max=1, benchmarker_max=1)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)
        status = svc.get_pool_status()
        assert isinstance(status, WorkerPoolStatus)
        assert status.active_slots == 0
        assert status.idle_slots == 5
        svc.stop()


# ── delegated methods ────────────────────────────────────────────────────────


class TestDelegatedMethods:
    def test_register_kind_handler_delegates(self) -> None:
        runner = _make_fake_runner()
        svc = PoolAwareDispatchService(runner, pool_config=_make_pool_config())
        handler = MagicMock()
        svc.register_kind_handler("custom", handler)
        assert svc._inner._kind_handlers["custom"] is handler
        svc.stop()

    def test_report_heartbeat_delegates(self) -> None:
        store = _make_fake_store()
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner, pool_config=_make_pool_config())
        # Should not raise even when the store update fails
        svc.report_heartbeat("attempt-1")
        svc.stop()


# ── _resolve_role_for_attempt ────────────────────────────────────────────────


class TestResolveRoleForAttempt:
    def test_resolves_executor_role(self) -> None:
        step = _make_fake_step(kind="execute", step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")
        store = _make_fake_store(
            ready_attempts=[attempt],
            steps={"s1": step},
        )
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner, pool_config=_make_pool_config())
        role = svc._resolve_role_for_attempt("a1")
        assert role == WorkerRole.executor
        svc.stop()

    def test_resolves_planner_role(self) -> None:
        step = _make_fake_step(kind="plan", step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")
        store = _make_fake_store(
            ready_attempts=[attempt],
            steps={"s1": step},
        )
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner, pool_config=_make_pool_config())
        role = svc._resolve_role_for_attempt("a1")
        assert role == WorkerRole.planner
        svc.stop()

    def test_falls_back_to_default_on_missing_attempt(self) -> None:
        store = _make_fake_store()
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner, pool_config=_make_pool_config())
        role = svc._resolve_role_for_attempt("nonexistent")
        assert role == _DEFAULT_ROLE
        svc.stop()


# ── _try_claim_and_dispatch ──────────────────────────────────────────────────


class TestTryClaimAndDispatch:
    def test_returns_none_when_no_ready_attempts(self) -> None:
        store = _make_fake_store(ready_attempts=[])
        runner = _make_fake_runner(store)
        svc = PoolAwareDispatchService(runner, pool_config=_make_pool_config())
        result = svc._try_claim_and_dispatch()
        assert result is None
        svc.stop()

    def test_claims_slot_and_dispatches(self) -> None:
        step = _make_fake_step(kind="execute", step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")
        store = _make_fake_store(
            ready_attempts=[attempt],
            steps={"s1": step},
        )
        runner = _make_fake_runner(store)
        cfg = _make_pool_config(executor_max=2)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        result = svc._try_claim_and_dispatch()
        assert result == "a1"

        # Pool should show one active executor slot.
        status = svc.get_pool_status()
        assert status.by_role["executor"] == 1
        svc.stop()

    def test_rejects_when_role_slots_full(self) -> None:
        step = _make_fake_step(kind="plan", step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")
        store = _make_fake_store(
            ready_attempts=[attempt],
            steps={"s1": step},
        )
        runner = _make_fake_runner(store)
        cfg = _make_pool_config(planner_max=0)  # zero planner slots
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        result = svc._try_claim_and_dispatch()
        assert result is None
        svc.stop()

    def test_releases_slot_when_no_attempt_claimed(self) -> None:
        """If peek returns a role but claim_next_ready returns None
        (race condition), the pool slot must be released."""
        step = _make_fake_step(kind="execute", step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")

        # We create a store where list returns an attempt but claim returns None
        # (simulating another thread winning the race).
        peek_list = [attempt]
        store = _make_fake_store(
            ready_attempts=[],  # claim will return None
            steps={"s1": step},
        )
        # Override list_step_attempts to return the attempt for peeking.
        original_list = store.list_step_attempts

        def patched_list(*, status: str = "", limit: int = 100) -> list[Any]:
            if status == "ready":
                return peek_list
            return original_list(status=status, limit=limit)

        store.list_step_attempts = patched_list

        runner = _make_fake_runner(store)
        cfg = _make_pool_config(executor_max=1)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        result = svc._try_claim_and_dispatch()
        assert result is None

        # Slot must have been released.
        status = svc.get_pool_status()
        assert status.active_slots == 0
        assert status.by_role["executor"] == 0
        svc.stop()

    def test_dispatches_search_step_via_researcher_slot(self) -> None:
        """Steps with kind=search should claim a researcher slot."""
        step = _make_fake_step(kind="search", step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")
        store = _make_fake_store(
            ready_attempts=[attempt],
            steps={"s1": step},
        )
        runner = _make_fake_runner(store)
        cfg = _make_pool_config(researcher_max=1)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        result = svc._try_claim_and_dispatch()
        assert result == "a1"

        status = svc.get_pool_status()
        assert status.by_role["researcher"] == 1
        svc.stop()

    def test_dispatches_reconcile_step_via_reconciler_slot(self) -> None:
        """Steps with kind=reconcile should claim a reconciler slot."""
        step = _make_fake_step(kind="reconcile", step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")
        store = _make_fake_store(
            ready_attempts=[attempt],
            steps={"s1": step},
        )
        runner = _make_fake_runner(store)
        cfg = _make_pool_config(reconciler_max=1)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        result = svc._try_claim_and_dispatch()
        assert result == "a1"

        status = svc.get_pool_status()
        assert status.by_role["reconciler"] == 1
        svc.stop()

    def test_passes_supervisor_id_from_context(self) -> None:
        """supervisor_id in attempt context should be forwarded to pool."""
        step = _make_fake_step(kind="execute", step_id="s1")
        attempt = _make_fake_attempt(
            step_attempt_id="a1",
            step_id="s1",
            context={"supervisor_id": "sup-1"},
        )
        store = _make_fake_store(
            ready_attempts=[attempt],
            steps={"s1": step},
        )
        runner = _make_fake_runner(store)
        cfg = _make_pool_config(executor_max=2)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        result = svc._try_claim_and_dispatch()
        assert result == "a1"
        svc.stop()

    def test_passes_workspace_from_context(self) -> None:
        """workspace in attempt context should be forwarded to pool."""
        step = _make_fake_step(kind="execute", step_id="s1")
        attempt = _make_fake_attempt(
            step_attempt_id="a1",
            step_id="s1",
            context={"workspace": "ws-alpha"},
        )
        store = _make_fake_store(
            ready_attempts=[attempt],
            steps={"s1": step},
        )
        runner = _make_fake_runner(store)
        cfg = _make_pool_config(executor_max=2, conflict_limits={"max_same_workspace": 1})
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        result = svc._try_claim_and_dispatch()
        assert result == "a1"
        svc.stop()

    def test_releases_slot_when_submit_raises(self) -> None:
        """If executor.submit() raises, the claimed pool slot must be released."""
        step = _make_fake_step(kind="execute", step_id="s1")
        attempt = _make_fake_attempt(step_attempt_id="a1", step_id="s1")
        store = _make_fake_store(
            ready_attempts=[attempt],
            steps={"s1": step},
        )
        runner = _make_fake_runner(store)
        cfg = _make_pool_config(executor_max=2)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        # Force the executor to raise on submit (e.g. shutdown).
        svc._inner._executor.shutdown(wait=False)
        svc._inner._executor.submit = MagicMock(side_effect=RuntimeError("executor shutdown"))

        with pytest.raises(RuntimeError, match="executor shutdown"):
            svc._try_claim_and_dispatch()

        # Slot must have been released — no leak.
        status = svc.get_pool_status()
        assert status.active_slots == 0
        assert status.by_role["executor"] == 0
        svc.stop()


# ── _reap_futures (slot release on completion) ───────────────────────────────


class TestReapFutures:
    def test_releases_slot_on_success(self) -> None:
        runner = _make_fake_runner()
        cfg = _make_pool_config(executor_max=2)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        # Manually claim a slot and create a finished future.
        slot = svc._pool.claim_slot(WorkerRole.executor)
        assert slot is not None

        future: concurrent.futures.Future[None] = concurrent.futures.Future()
        future.set_result(None)

        with svc._inner._lock:
            svc._inner._futures[future] = "attempt-1"
        with svc._slot_lock:
            svc._slot_map[future] = slot.slot_id

        svc._reap_futures()

        # Slot should be released.
        status = svc.get_pool_status()
        assert status.by_role["executor"] == 0
        svc.stop()

    def test_releases_slot_on_failure(self) -> None:
        runner = _make_fake_runner()
        cfg = _make_pool_config(executor_max=2)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        slot = svc._pool.claim_slot(WorkerRole.executor)
        assert slot is not None

        future: concurrent.futures.Future[None] = concurrent.futures.Future()
        future.set_exception(RuntimeError("boom"))

        with svc._inner._lock:
            svc._inner._futures[future] = "attempt-2"
        with svc._slot_lock:
            svc._slot_map[future] = slot.slot_id

        svc._reap_futures()

        # Slot should be released even on failure.
        status = svc.get_pool_status()
        assert status.by_role["executor"] == 0
        svc.stop()


# ── full lifecycle (start/stop) ──────────────────────────────────────────────


class TestLifecycle:
    def test_start_and_stop(self) -> None:
        store = _make_fake_store()
        runner = _make_fake_runner(store)
        cfg = _make_pool_config()
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        svc.start()
        assert svc._inner._thread is not None
        assert svc._inner._thread.is_alive()

        svc.stop()
        svc._inner._thread.join(timeout=2)
        assert not svc._inner._thread.is_alive()

    def test_wake_does_not_raise(self) -> None:
        runner = _make_fake_runner()
        svc = PoolAwareDispatchService(runner, pool_config=_make_pool_config())
        # Should not raise even when the loop is not running.
        svc.wake()
        svc.stop()


# ── per-role concurrency enforcement ─────────────────────────────────────────


class TestPerRoleConcurrency:
    def test_executor_concurrency_limit(self) -> None:
        """Executor slots should be limited to max_active."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(executor_max=2)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        s1 = svc._pool.claim_slot(WorkerRole.executor)
        s2 = svc._pool.claim_slot(WorkerRole.executor)
        s3 = svc._pool.claim_slot(WorkerRole.executor)

        assert s1 is not None
        assert s2 is not None
        assert s3 is None  # limit reached

        status = svc.get_pool_status()
        assert status.by_role["executor"] == 2
        svc.stop()

    def test_independent_role_pools(self) -> None:
        """Different roles have independent slot pools."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(executor_max=1, planner_max=1, verifier_max=1)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        e = svc._pool.claim_slot(WorkerRole.executor)
        p = svc._pool.claim_slot(WorkerRole.planner)
        v = svc._pool.claim_slot(WorkerRole.verifier)

        assert e is not None
        assert p is not None
        assert v is not None

        # Each role is full but the others shouldn't be affected.
        assert not svc._pool.can_accept(WorkerRole.executor)
        assert not svc._pool.can_accept(WorkerRole.planner)
        assert not svc._pool.can_accept(WorkerRole.verifier)

        status = svc.get_pool_status()
        assert status.active_slots == 3
        svc.stop()

    def test_slot_release_re_enables_role(self) -> None:
        """Releasing a slot makes the role available again."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(planner_max=1)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        slot = svc._pool.claim_slot(WorkerRole.planner)
        assert slot is not None
        assert not svc._pool.can_accept(WorkerRole.planner)

        svc._pool.release_slot(slot.slot_id)
        assert svc._pool.can_accept(WorkerRole.planner)
        svc.stop()


# ── global active cap ────────────────────────────────────────────────────────


class TestGlobalActiveCap:
    def test_global_cap_blocks_when_reached(self) -> None:
        """When max_global_active is set, claims are rejected once the total
        busy slot count reaches the cap — even if per-role slots remain."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=3,
            planner_max=3,
            max_global_active=4,
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        # Claim 3 executor + 1 planner = 4 total (at cap)
        for _ in range(3):
            assert svc._pool.claim_slot(WorkerRole.executor) is not None
        assert svc._pool.claim_slot(WorkerRole.planner) is not None

        # Next claim should fail even though planner has 2 idle slots.
        assert svc._pool.claim_slot(WorkerRole.planner) is None
        assert svc._pool.can_accept(WorkerRole.planner) is False

        svc.stop()

    def test_global_cap_defaults_to_total_role_slots(self) -> None:
        """When max_global_active=0, the effective cap is the sum of all
        per-role max_active values."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(executor_max=2, planner_max=1, max_global_active=0)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        # Should be able to fill all 5 slots (2+1+1+1)
        assert svc._pool.claim_slot(WorkerRole.executor) is not None
        assert svc._pool.claim_slot(WorkerRole.executor) is not None
        assert svc._pool.claim_slot(WorkerRole.planner) is not None
        assert svc._pool.claim_slot(WorkerRole.verifier) is not None
        assert svc._pool.claim_slot(WorkerRole.benchmarker) is not None

        # All slots full — cannot accept more.
        assert svc._pool.can_accept(WorkerRole.executor) is False
        svc.stop()

    def test_global_cap_release_allows_new_claims(self) -> None:
        """Releasing a slot below the global cap allows new claims."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(executor_max=2, planner_max=2, max_global_active=2)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        s1 = svc._pool.claim_slot(WorkerRole.executor)
        s2 = svc._pool.claim_slot(WorkerRole.planner)
        assert s1 is not None
        assert s2 is not None
        assert svc._pool.claim_slot(WorkerRole.executor) is None

        svc._pool.release_slot(s1.slot_id)
        assert svc._pool.claim_slot(WorkerRole.executor) is not None
        svc.stop()


# ── per-supervisor limit ─────────────────────────────────────────────────────


class TestPerSupervisorLimit:
    def test_supervisor_cap_blocks_single_supervisor(self) -> None:
        """A single supervisor cannot exceed max_per_supervisor slots."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=4,
            max_per_supervisor=2,
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        s1 = svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-A")
        s2 = svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-A")
        s3 = svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-A")

        assert s1 is not None
        assert s2 is not None
        assert s3 is None  # supervisor A at cap

        svc.stop()

    def test_different_supervisors_independent(self) -> None:
        """Different supervisors have independent per-supervisor budgets."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=4,
            max_per_supervisor=2,
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        assert svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-A") is not None
        assert svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-A") is not None
        # sup-A is at cap, but sup-B can still claim.
        assert svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-B") is not None

        svc.stop()

    def test_can_accept_with_supervisor_cap(self) -> None:
        """can_accept should respect per-supervisor limits."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(executor_max=4, max_per_supervisor=1)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        assert svc._pool.can_accept(WorkerRole.executor, supervisor_id="sup-A") is True
        svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-A")
        assert svc._pool.can_accept(WorkerRole.executor, supervisor_id="sup-A") is False
        assert svc._pool.can_accept(WorkerRole.executor, supervisor_id="sup-B") is True

        svc.stop()

    def test_no_supervisor_id_bypasses_check(self) -> None:
        """When supervisor_id is None, per-supervisor cap is not applied."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(executor_max=4, max_per_supervisor=1)
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        # Claims without supervisor_id should not be capped.
        assert svc._pool.claim_slot(WorkerRole.executor) is not None
        assert svc._pool.claim_slot(WorkerRole.executor) is not None
        assert svc._pool.claim_slot(WorkerRole.executor) is not None
        assert svc._pool.claim_slot(WorkerRole.executor) is not None

        svc.stop()


# ── conflict-domain: workspace ───────────────────────────────────────────────


class TestWorkspaceConflictLimits:
    def test_same_workspace_blocked(self) -> None:
        """Two workers cannot operate on the same workspace when
        max_same_workspace=1."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=4,
            conflict_limits={"max_same_workspace": 1},
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        s1 = svc._pool.claim_slot(WorkerRole.executor, workspace="ws-1")
        s2 = svc._pool.claim_slot(WorkerRole.executor, workspace="ws-1")

        assert s1 is not None
        assert s2 is None  # same workspace conflict

        svc.stop()

    def test_different_workspaces_independent(self) -> None:
        """Workers on different workspaces are not blocked."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=4,
            conflict_limits={"max_same_workspace": 1},
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        s1 = svc._pool.claim_slot(WorkerRole.executor, workspace="ws-1")
        s2 = svc._pool.claim_slot(WorkerRole.executor, workspace="ws-2")

        assert s1 is not None
        assert s2 is not None

        svc.stop()

    def test_release_clears_workspace_conflict(self) -> None:
        """Releasing a slot clears its workspace, allowing a new claim."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=4,
            conflict_limits={"max_same_workspace": 1},
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        s1 = svc._pool.claim_slot(WorkerRole.executor, workspace="ws-1")
        assert s1 is not None
        assert svc._pool.can_accept(WorkerRole.executor, workspace="ws-1") is False

        svc._pool.release_slot(s1.slot_id)
        assert svc._pool.can_accept(WorkerRole.executor, workspace="ws-1") is True

        svc.stop()

    def test_no_workspace_bypasses_conflict_check(self) -> None:
        """When workspace is None, conflict check is not applied."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=4,
            conflict_limits={"max_same_workspace": 1},
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        assert svc._pool.claim_slot(WorkerRole.executor) is not None
        assert svc._pool.claim_slot(WorkerRole.executor) is not None

        svc.stop()

    def test_can_accept_with_workspace_conflict(self) -> None:
        """can_accept should return False when workspace is at limit."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=4,
            conflict_limits={"max_same_workspace": 1},
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        svc._pool.claim_slot(WorkerRole.executor, workspace="ws-1")
        assert svc._pool.can_accept(WorkerRole.executor, workspace="ws-1") is False
        assert svc._pool.can_accept(WorkerRole.executor, workspace="ws-2") is True

        svc.stop()


# ── conflict-domain: module ──────────────────────────────────────────────────


class TestModuleConflictLimits:
    def test_same_module_blocked_at_limit(self) -> None:
        """Claims are blocked when max_same_module is reached."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=4,
            conflict_limits={"max_same_module": 2},
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        s1 = svc._pool.claim_slot(WorkerRole.executor, module="kernel.policy")
        s2 = svc._pool.claim_slot(WorkerRole.executor, module="kernel.policy")
        s3 = svc._pool.claim_slot(WorkerRole.executor, module="kernel.policy")

        assert s1 is not None
        assert s2 is not None
        assert s3 is None  # module limit

        svc.stop()

    def test_different_modules_independent(self) -> None:
        """Workers on different modules are not blocked."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=4,
            conflict_limits={"max_same_module": 1},
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        s1 = svc._pool.claim_slot(WorkerRole.executor, module="kernel.policy")
        s2 = svc._pool.claim_slot(WorkerRole.executor, module="kernel.task")

        assert s1 is not None
        assert s2 is not None

        svc.stop()


# ── combined admission control ───────────────────────────────────────────────


class TestCombinedAdmissionControl:
    def test_global_cap_and_supervisor_cap_interact(self) -> None:
        """Global cap can block even when supervisor cap is not reached."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=4,
            planner_max=4,
            max_global_active=3,
            max_per_supervisor=2,
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        # sup-A claims 2 (at supervisor cap)
        assert svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-A") is not None
        assert svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-A") is not None

        # sup-B claims 1 (now global total = 3 = cap)
        assert svc._pool.claim_slot(WorkerRole.planner, supervisor_id="sup-B") is not None

        # sup-B has supervisor room (1/2) but global cap (3/3) blocks it.
        assert svc._pool.claim_slot(WorkerRole.planner, supervisor_id="sup-B") is None

        svc.stop()

    def test_workspace_and_supervisor_combined(self) -> None:
        """Both workspace conflict and supervisor cap must be satisfied."""
        runner = _make_fake_runner()
        cfg = _make_pool_config(
            executor_max=4,
            max_per_supervisor=3,
            conflict_limits={"max_same_workspace": 1},
        )
        svc = PoolAwareDispatchService(runner, pool_config=cfg)

        # sup-A, ws-1
        assert (
            svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-A", workspace="ws-1")
            is not None
        )

        # sup-A, ws-1 again — blocked by workspace conflict
        assert (
            svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-A", workspace="ws-1")
            is None
        )

        # sup-A, ws-2 — different workspace, OK
        assert (
            svc._pool.claim_slot(WorkerRole.executor, supervisor_id="sup-A", workspace="ws-2")
            is not None
        )

        svc.stop()
