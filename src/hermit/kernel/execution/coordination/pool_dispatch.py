"""Pool-aware dispatch service with role-based slot management.

Wraps :class:`KernelDispatchService` and gates step-attempt dispatch on
:class:`WorkerPoolManager` admission control.  Instead of a flat
``worker_count`` limit, each step attempt is mapped to a
:class:`WorkerRole` via its ``step.kind`` field and must claim an idle
slot before being submitted to the thread pool.

Logical slot capacity (up to 2048 total) is decoupled from physical
thread count (controlled by ``max_physical_threads`` or the
``HERMIT_DISPATCH_THREAD_MAX`` env var).
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
from typing import Any, cast

import structlog

from hermit.kernel.execution.coordination.dispatch import (
    KernelDispatchService,
)
from hermit.kernel.execution.workers.models import (
    WorkerPoolConfig,
    WorkerPoolStatus,
    WorkerRole,
    WorkerSlotConfig,
)
from hermit.kernel.execution.workers.pool import WorkerPoolManager

__all__ = [
    "PoolAwareDispatchService",
    "step_kind_to_role",
]

log = structlog.get_logger()

# ── step kind → worker role mapping ─────────────────────────────────────────

_KIND_TO_ROLE: dict[str, WorkerRole] = {
    # planner
    "plan": WorkerRole.planner,
    "decompose": WorkerRole.planner,
    # spec (dedicated role per self-loop spec)
    "spec": WorkerRole.spec,
    # executor
    "execute": WorkerRole.executor,
    "code": WorkerRole.executor,
    "patch": WorkerRole.executor,
    "edit": WorkerRole.executor,
    "publish": WorkerRole.executor,
    "rollback": WorkerRole.executor,
    # verifier
    "review": WorkerRole.verifier,
    "verify": WorkerRole.verifier,
    "check": WorkerRole.verifier,
    # benchmarker
    "benchmark": WorkerRole.benchmarker,
    # tester (dedicated role per self-loop spec: test_worker)
    "test": WorkerRole.tester,
    "run_tests": WorkerRole.tester,
    # researcher
    "search": WorkerRole.researcher,
    "research": WorkerRole.researcher,
    "inspect": WorkerRole.researcher,
    # reconciler
    "reconcile": WorkerRole.reconciler,
    "learn": WorkerRole.reconciler,
}

_DEFAULT_ROLE = WorkerRole.executor

# Maximum number of step attempts to claim and dispatch per tick of the
# dispatch loop.  Prevents the inner loop from monopolising the thread
# when a large backlog of ready attempts exists.
_MAX_CLAIMS_PER_TICK = 16


def step_kind_to_role(kind: str) -> WorkerRole:
    """Map a step kind string to the corresponding :class:`WorkerRole`.

    Unknown kinds fall back to ``executor``.
    """
    return _KIND_TO_ROLE.get(kind, _DEFAULT_ROLE)


# ── default pool configuration ──────────────────────────────────────────────


def _env_int(key: str, default: int) -> int:
    """Read an integer from env, falling back to *default*."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _default_pool_config() -> WorkerPoolConfig:
    """Build the default pool configuration with high-capacity per-role limits.

    Each per-role limit is overridable via env vars (e.g.
    ``HERMIT_POOL_EXECUTOR_MAX=512``).

    Default logical slot totals: 256+128+64+64+64+64+32+32+32 = 736.
    Physical thread pool is capped separately by ``max_physical_threads``
    (default 256) or ``HERMIT_DISPATCH_THREAD_MAX``.
    """
    executor_max = _env_int("HERMIT_POOL_EXECUTOR_MAX", 256)
    verifier_max = _env_int("HERMIT_POOL_VERIFIER_MAX", 128)
    planner_max = _env_int("HERMIT_POOL_PLANNER_MAX", 64)
    benchmarker_max = _env_int("HERMIT_POOL_BENCHMARKER_MAX", 64)
    researcher_max = _env_int("HERMIT_POOL_RESEARCHER_MAX", 64)
    tester_max = _env_int("HERMIT_POOL_TESTER_MAX", 64)
    spec_max = _env_int("HERMIT_POOL_SPEC_MAX", 32)
    reconciler_max = _env_int("HERMIT_POOL_RECONCILER_MAX", 32)
    reviewer_max = _env_int("HERMIT_POOL_REVIEWER_MAX", 32)

    max_same_workspace = _env_int("HERMIT_MAX_SAME_WORKSPACE", 8)
    max_same_module = _env_int("HERMIT_MAX_SAME_MODULE", 16)

    max_physical = _env_int("HERMIT_DISPATCH_THREAD_MAX", 256)

    return WorkerPoolConfig(
        pool_id="kernel-dispatch",
        team_id="default",
        slots={
            WorkerRole.executor: WorkerSlotConfig(
                role=WorkerRole.executor,
                max_active=executor_max,
                accepted_step_kinds=["execute", "code", "patch", "edit", "publish", "rollback"],
                output_artifact_kinds=["diff", "command_output"],
            ),
            WorkerRole.verifier: WorkerSlotConfig(
                role=WorkerRole.verifier,
                max_active=verifier_max,
                accepted_step_kinds=["review", "verify", "check"],
                output_artifact_kinds=["verdict", "critique"],
            ),
            WorkerRole.planner: WorkerSlotConfig(
                role=WorkerRole.planner,
                max_active=planner_max,
                accepted_step_kinds=["plan", "decompose"],
                output_artifact_kinds=["contract_packet", "dag_fragment"],
            ),
            WorkerRole.benchmarker: WorkerSlotConfig(
                role=WorkerRole.benchmarker,
                max_active=benchmarker_max,
                accepted_step_kinds=["benchmark"],
                output_artifact_kinds=["benchmark_report", "raw_metrics"],
            ),
            WorkerRole.researcher: WorkerSlotConfig(
                role=WorkerRole.researcher,
                max_active=researcher_max,
                accepted_step_kinds=["search", "research", "inspect"],
                output_artifact_kinds=["evidence_bundle", "inspection_report"],
            ),
            WorkerRole.tester: WorkerSlotConfig(
                role=WorkerRole.tester,
                max_active=tester_max,
                accepted_step_kinds=["test", "run_tests"],
                output_artifact_kinds=["test_report"],
            ),
            WorkerRole.spec: WorkerSlotConfig(
                role=WorkerRole.spec,
                max_active=spec_max,
                accepted_step_kinds=["spec"],
                output_artifact_kinds=["iteration_spec"],
            ),
            WorkerRole.reconciler: WorkerSlotConfig(
                role=WorkerRole.reconciler,
                max_active=reconciler_max,
                accepted_step_kinds=["reconcile", "learn"],
                output_artifact_kinds=["reconciliation_record", "lesson_pack"],
            ),
            WorkerRole.reviewer: WorkerSlotConfig(
                role=WorkerRole.reviewer,
                max_active=reviewer_max,
                accepted_step_kinds=["review"],
                output_artifact_kinds=["review_report"],
            ),
        },
        conflict_limits={
            "max_same_workspace": max_same_workspace,
            "max_same_module": max_same_module,
        },
        max_physical_threads=max_physical,
    )


# ── team-aware pool config builder ─────────────────────────────────────────


def build_team_pool_config(
    team: Any,
    *,
    base_config: WorkerPoolConfig | None = None,
) -> WorkerPoolConfig:
    """Build a :class:`WorkerPoolConfig` from a team's ``role_assembly``.

    The role_assembly's count values become the ``max_active`` slots for each
    matching :class:`WorkerRole`.  Roles not present in the team get zero slots.

    If *base_config* is provided it is used as the template and only the slot
    limits are overridden; otherwise :func:`_default_pool_config` is used.
    """
    _TEAM_ROLE_MAP: dict[str, WorkerRole] = {
        "researcher": WorkerRole.researcher,
        "planner": WorkerRole.planner,
        "executor": WorkerRole.executor,
        "coder": WorkerRole.executor,
        "reviewer": WorkerRole.reviewer,
        "verifier": WorkerRole.verifier,
        "tester": WorkerRole.tester,
        "benchmarker": WorkerRole.benchmarker,
        "spec": WorkerRole.spec,
        "reconciler": WorkerRole.reconciler,
    }

    base = base_config or _default_pool_config()
    team_slots: dict[WorkerRole, WorkerSlotConfig] = {}

    role_assembly: dict[str, Any] = getattr(team, "role_assembly", {}) or {}
    for _role_name, slot_spec in role_assembly.items():
        role_type = getattr(slot_spec, "role", _role_name)
        worker_role = _TEAM_ROLE_MAP.get(role_type) or _TEAM_ROLE_MAP.get(_role_name)
        if worker_role is None:
            continue

        count = getattr(slot_spec, "count", 1)
        base_slot = base.slots.get(worker_role)
        if base_slot is not None:
            team_slots[worker_role] = WorkerSlotConfig(
                role=worker_role,
                max_active=count,
                accepted_step_kinds=list(base_slot.accepted_step_kinds),
                output_artifact_kinds=list(base_slot.output_artifact_kinds),
            )
        else:
            team_slots[worker_role] = WorkerSlotConfig(
                role=worker_role,
                max_active=count,
            )

    team_id = getattr(team, "team_id", "team")
    return WorkerPoolConfig(
        pool_id=f"team-{team_id[:8]}",
        team_id=team_id,
        slots=team_slots,
        conflict_limits=dict(base.conflict_limits),
        max_physical_threads=base.max_physical_threads,
    )


# ── pool-aware dispatch service ─────────────────────────────────────────────


class PoolAwareDispatchService:
    """Dispatch service that uses :class:`WorkerPoolManager` for role-aware
    slot management.

    Maps ``step.kind`` to :class:`WorkerRole`:

    - ``plan`` / ``decompose``                              -> planner
    - ``spec``                                              -> spec
    - ``execute`` / ``code`` / ``patch`` / ``edit``
      / ``publish`` / ``rollback``                          -> executor
    - ``review`` / ``verify`` / ``check``                   -> verifier
    - ``benchmark``                                         -> benchmarker
    - ``test`` / ``run_tests``                              -> tester
    - ``search`` / ``research`` / ``inspect``               -> researcher
    - ``reconcile`` / ``learn``                              -> reconciler

    Admission control layers:

    1. Per-role slot limits (from ``WorkerPoolConfig.slots``).
    2. Global active cap (``WorkerPoolConfig.max_global_active``).
    3. Per-supervisor limit (``WorkerPoolConfig.max_per_supervisor``).
    4. Conflict-domain limits: ``max_same_workspace`` and ``max_same_module``.

    Only claims ready attempts when the matching role has available slots.
    On completion (or failure), the slot is released back to the pool.
    """

    def __init__(
        self,
        runner: Any,
        *,
        pool_config: WorkerPoolConfig | None = None,
    ) -> None:
        config = pool_config or _default_pool_config()
        self._pool = WorkerPoolManager(config)

        # Physical thread pool: capped by max_physical_threads, decoupled
        # from the logical slot capacity.
        total_logical = sum(sc.max_active for sc in config.slots.values())
        thread_max = min(total_logical, config.max_physical_threads)
        thread_max = max(thread_max, 1)

        # Build the inner dispatch service with the physical thread count.
        self._inner = KernelDispatchService(runner, worker_count=thread_max)

        # Slot tracking: future -> slot_id, used to release slots on
        # completion or failure.
        self._slot_map: dict[concurrent.futures.Future[Any], str] = {}
        self._slot_lock = threading.Lock()

        # Keep a reference to runner for store access.
        self._runner = runner

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the pool-aware dispatch loop.

        Recovers interrupted attempts (delegated to the inner service),
        then launches a daemon thread running the pool-gated dispatch loop.
        Also starts the dedicated lease-reaper thread from the inner service.
        """
        self._inner.recover_interrupted_attempts()
        self._inner.thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="kernel-pool-dispatch-loop",
        )
        self._inner.thread.start()
        self._inner.reaper_thread = threading.Thread(
            target=self._inner.reaper_loop,
            daemon=True,
            name="lease-reaper",
        )
        self._inner.reaper_thread.start()

    def stop(self) -> None:
        """Signal the dispatch loop to stop and shut down the thread pool."""
        self._inner.stop()

    def wake(self) -> None:
        """Wake the dispatch loop to check for newly ready step attempts."""
        self._inner.wake()

    # ── pool status ──────────────────────────────────────────────────────

    def get_pool_status(self) -> WorkerPoolStatus:
        """Return a point-in-time snapshot of pool utilisation."""
        return self._pool.get_status()

    # ── delegated methods ────────────────────────────────────────────────

    def register_kind_handler(self, kind: str, handler: Any) -> None:
        """Register a custom handler for a step kind (delegated to inner)."""
        self._inner.register_kind_handler(kind, handler)

    def report_heartbeat(self, step_attempt_id: str) -> None:
        """Record a heartbeat for a running step attempt (delegated to inner)."""
        self._inner.report_heartbeat(step_attempt_id)

    def pause_dispatch(self) -> None:
        """Pause dispatch — loop skips claiming until resumed."""
        self._inner.pause_dispatch()

    def resume_dispatch(self) -> None:
        """Resume dispatch and wake loop immediately."""
        self._inner.resume_dispatch()

    def check_heartbeat_timeouts(self) -> None:
        """Scan running attempts for heartbeat timeouts (delegated to inner)."""
        self._inner.check_heartbeat_timeouts()

    # ── pool-gated dispatch loop ─────────────────────────────────────────

    def _loop(self) -> None:
        """Main dispatch loop — claims slots before submitting attempts.

        Uses adaptive poll intervals based on pool utilization:
        - <50% utilization: 10ms (lots of capacity, fill fast)
        - 50-90% utilization: 50ms (normal operation)
        - >90% utilization: 200ms (nearly full, back off)

        Batch claiming is capped at ``_MAX_CLAIMS_PER_TICK`` to prevent
        the inner loop from monopolising the dispatch thread when many
        ready attempts are queued.
        """
        _heartbeat_counter = 0
        while not self._inner.stop_event.is_set():
            self._reap_futures()
            # Only check heartbeat timeouts every 10 iterations to reduce overhead.
            _heartbeat_counter += 1
            if _heartbeat_counter >= 10:
                self._inner.check_heartbeat_timeouts()
                _heartbeat_counter = 0

            claimed_count = 0
            if not self._inner._paused.is_set():
                while claimed_count < _MAX_CLAIMS_PER_TICK:
                    attempt_info = self._try_claim_and_dispatch()
                    if attempt_info is None:
                        break
                    claimed_count += 1

            # Adaptive wait time based on pool utilization.
            pool_status = self._pool.get_status()
            total = pool_status.active_slots + pool_status.idle_slots
            utilization = pool_status.active_slots / max(total, 1)
            if utilization < 0.5:
                wait_time = 0.01  # 10ms — lots of capacity, fill fast
            elif utilization < 0.9:
                wait_time = 0.05  # 50ms — normal operation
            else:
                wait_time = 0.2  # 200ms — nearly full, back off

            self._inner.wake_event.wait(wait_time)
            self._inner.wake_event.clear()

    def _resolve_role_for_attempt(self, step_attempt_id: str) -> WorkerRole:
        """Resolve the :class:`WorkerRole` for a step attempt by reading
        the parent step's ``kind`` field.
        """
        try:
            store = self._runner.task_controller.store
            attempt = store.get_step_attempt(step_attempt_id)
            if attempt is not None:
                step = store.get_step(attempt.step_id)
                if step is not None:
                    return step_kind_to_role(step.kind)
        except Exception:
            log.warning(
                "pool_dispatch_role_resolve_failed",
                step_attempt_id=step_attempt_id,
            )
        return _DEFAULT_ROLE

    def _peek_next_ready_role(
        self,
    ) -> tuple[WorkerRole, str | None, str | None] | None:
        """Peek at the next ready step attempt and determine its role.

        Returns a ``(role, supervisor_id, workspace)`` tuple, or ``None``
        if no ready attempt exists.  Does **not** claim the attempt — that
        is done in ``_try_claim_and_dispatch`` after the pool slot is
        secured.
        """
        try:
            store = self._runner.task_controller.store
            attempts = store.list_step_attempts(status="ready", limit=1)
            if not attempts:
                return None
            attempt = attempts[0]
            step = store.get_step(attempt.step_id)
            role = step_kind_to_role(step.kind) if step is not None else _DEFAULT_ROLE
            # Extract supervisor/workspace from attempt context for
            # admission control (per-supervisor cap, conflict domains).
            attempt_context = cast(
                dict[Any, Any] | None,
                getattr(attempt, "context", None),
            )
            ctx: dict[str, Any] = (
                cast(dict[str, Any], attempt_context) if isinstance(attempt_context, dict) else {}
            )
            supervisor_id = cast(str | None, ctx.get("supervisor_id"))
            workspace = cast(str | None, ctx.get("workspace"))
            return (role, supervisor_id, workspace)
        except Exception:
            log.warning("pool_dispatch_peek_failed", exc_info=True)
            return None

    @staticmethod
    def _dispatch_with_cleanup(
        handler: Any,
        attempt_id: str,
        store: Any,
    ) -> Any:
        """Run *handler* and close the thread-local SQLite connection afterwards.

        Worker threads in the ThreadPoolExecutor create thread-local SQLite
        connections via ``KernelStore._conn``.  Without explicit cleanup the
        connection (and its file descriptors) leaks until the thread is
        reaped by the pool — which may never happen for long-lived pools.
        """
        try:
            return handler(attempt_id)
        finally:
            try:
                store.close_thread_conn()
            except Exception:
                pass

    def _try_claim_and_dispatch(self) -> str | None:
        """Attempt to claim a pool slot and dispatch the next ready attempt.

        Returns the step_attempt_id on success, or ``None`` when no ready
        attempt is available or no pool slot can be claimed for the required
        role.

        The flow:
        1. Peek at the next ready step attempt to determine the required role,
           supervisor_id, and workspace.
        2. Try to claim a pool slot for that role (checked against global cap,
           per-supervisor limit, and conflict-domain limits).
        3. Actually claim the step attempt from the store.
        4. Submit the attempt to the thread pool.

        If step 3 fails (another thread grabbed the attempt), the pool slot
        is released immediately to avoid leaks.
        """
        peek = self._peek_next_ready_role()
        if peek is None:
            return None

        role, supervisor_id, workspace = peek

        # Claim pool slot (reservation).  claim_slot() returns None when no
        # slot is available — no separate can_accept() pre-check needed.
        slot = self._pool.claim_slot(role, supervisor_id=supervisor_id, workspace=workspace)
        if slot is None:
            log.debug(
                "pool_dispatch.no_slot_available",
                role=str(role),
                supervisor_id=supervisor_id,
                workspace=workspace,
            )
            return None

        # Now claim the actual step attempt from the store.
        attempt = self._runner.task_controller.store.claim_next_ready_step_attempt()
        if attempt is None:
            # No attempt to dispatch — release the slot.
            self._pool.release_slot(slot.slot_id)
            return None

        # Gate: check if deliberation is required before dispatch.
        # Delegates to the inner service which updates step status and
        # records a ledger event when deliberation is needed.
        if self._inner.check_deliberation_needed(attempt.step_attempt_id):
            self._pool.release_slot(slot.slot_id)
            log.info(
                "pool_dispatch.deliberation_required",
                step_attempt_id=attempt.step_attempt_id,
                role=str(role),
            )
            return attempt.step_attempt_id

        # Resolve handler and submit.  Wrap in try/except so that if
        # submit() raises (e.g. executor shutdown), the claimed pool slot
        # is released — otherwise the slot leaks permanently.
        handler = self._inner.resolve_handler(attempt.step_attempt_id)
        store = self._runner.task_controller.store
        try:
            future = self._inner.executor.submit(
                self._dispatch_with_cleanup, handler, attempt.step_attempt_id, store
            )
        except Exception:
            self._pool.release_slot(slot.slot_id)
            raise
        with self._inner.lock:
            self._inner.futures[future] = attempt.step_attempt_id
        with self._slot_lock:
            self._slot_map[future] = slot.slot_id

        log.debug(
            "pool_dispatch_submitted",
            step_attempt_id=attempt.step_attempt_id,
            role=str(role),
            slot_id=slot.slot_id,
        )
        return attempt.step_attempt_id

    def _reap_futures(self) -> None:
        """Reap completed futures, release pool slots, and handle failures.

        After releasing slots, wakes the dispatch loop so that newly
        available capacity can be filled promptly.
        """
        done: list[concurrent.futures.Future[Any]] = []
        with self._inner.lock:
            for future in list(self._inner.futures):
                if future.done():
                    done.append(future)

        slots_released = False
        for future in done:
            attempt_id = ""
            slot_id = ""
            with self._inner.lock:
                attempt_id = self._inner.futures.pop(future, "")
            with self._slot_lock:
                slot_id = self._slot_map.pop(future, "")

            # Always release the pool slot regardless of outcome.
            if slot_id:
                self._pool.release_slot(slot_id)
                slots_released = True

            try:
                future.result()
                self._inner.on_attempt_completed(attempt_id)
            except Exception:
                log.exception(
                    "pool_dispatch_attempt_failed",
                    step_attempt_id=attempt_id,
                )
                self._inner.force_fail_attempt(attempt_id)

        # Wake the dispatch loop when slots were released so new ready
        # attempts can fill the freed capacity immediately.
        if slots_released:
            self._inner.wake_event.set()
