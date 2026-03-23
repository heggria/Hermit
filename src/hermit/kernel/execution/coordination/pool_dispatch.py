"""Pool-aware dispatch service with role-based slot management.

Wraps :class:`KernelDispatchService` and gates step-attempt dispatch on
:class:`WorkerPoolManager` admission control.  Instead of a flat
``worker_count`` limit, each step attempt is mapped to a
:class:`WorkerRole` via its ``step.kind`` field and must claim an idle
slot before being submitted to the thread pool.
"""

from __future__ import annotations

import concurrent.futures
import threading
from typing import Any, cast

import structlog

from hermit.kernel.execution.coordination.dispatch import (
    POLL_INTERVAL_SECONDS,
    KernelDispatchService,
)
from hermit.kernel.execution.workers.models import (
    DEFAULT_CONFLICT_LIMITS,
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


def step_kind_to_role(kind: str) -> WorkerRole:
    """Map a step kind string to the corresponding :class:`WorkerRole`.

    Unknown kinds fall back to ``executor``.
    """
    return _KIND_TO_ROLE.get(kind, _DEFAULT_ROLE)


# ── default pool configuration ──────────────────────────────────────────────


def _default_pool_config() -> WorkerPoolConfig:
    """Build the default pool configuration with sensible per-role limits.

    Spec-defined concurrency limits:
    - exec: max 4
    - verify: max 3
    - planner: max 2  (spec says "1-2")
    - benchmarker: max 2
    - researcher: max 2
    - tester: max 2
    - spec: max 1
    - reconciler: max 1

    Conflict-domain limits from spec:
    - max_same_workspace: 1
    - max_same_module: 2
    """
    return WorkerPoolConfig(
        pool_id="kernel-dispatch",
        team_id="default",
        slots={
            WorkerRole.executor: WorkerSlotConfig(
                role=WorkerRole.executor,
                max_active=4,
                accepted_step_kinds=["execute", "code", "patch", "edit", "publish", "rollback"],
                output_artifact_kinds=["diff", "command_output"],
            ),
            WorkerRole.verifier: WorkerSlotConfig(
                role=WorkerRole.verifier,
                max_active=3,
                accepted_step_kinds=["review", "verify", "check"],
                output_artifact_kinds=["verdict", "critique"],
            ),
            WorkerRole.planner: WorkerSlotConfig(
                role=WorkerRole.planner,
                max_active=2,
                accepted_step_kinds=["plan", "decompose"],
                output_artifact_kinds=["contract_packet", "dag_fragment"],
            ),
            WorkerRole.benchmarker: WorkerSlotConfig(
                role=WorkerRole.benchmarker,
                max_active=2,
                accepted_step_kinds=["benchmark"],
                output_artifact_kinds=["benchmark_report", "raw_metrics"],
            ),
            WorkerRole.researcher: WorkerSlotConfig(
                role=WorkerRole.researcher,
                max_active=2,
                accepted_step_kinds=["search", "research", "inspect"],
                output_artifact_kinds=["evidence_bundle", "inspection_report"],
            ),
            WorkerRole.tester: WorkerSlotConfig(
                role=WorkerRole.tester,
                max_active=2,
                accepted_step_kinds=["test", "run_tests"],
                output_artifact_kinds=["test_report"],
            ),
            WorkerRole.spec: WorkerSlotConfig(
                role=WorkerRole.spec,
                max_active=1,
                accepted_step_kinds=["spec"],
                output_artifact_kinds=["iteration_spec"],
            ),
            WorkerRole.reconciler: WorkerSlotConfig(
                role=WorkerRole.reconciler,
                max_active=1,
                accepted_step_kinds=["reconcile", "learn"],
                output_artifact_kinds=["reconciliation_record", "lesson_pack"],
            ),
        },
        conflict_limits=dict(DEFAULT_CONFLICT_LIMITS),
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

        # Total thread pool size = sum of all per-role max_active slots.
        total_workers = sum(sc.max_active for sc in config.slots.values())
        total_workers = max(total_workers, 1)

        # Build the inner dispatch service with the aggregate worker count.
        # We override _capacity_available and the dispatch loop to use pool
        # slots instead of the flat counter, but the thread pool still needs
        # enough threads to handle concurrent work across all roles.
        self._inner = KernelDispatchService(runner, worker_count=total_workers)

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

    def check_heartbeat_timeouts(self) -> None:
        """Scan running attempts for heartbeat timeouts (delegated to inner)."""
        self._inner.check_heartbeat_timeouts()

    # ── pool-gated dispatch loop ─────────────────────────────────────────

    def _loop(self) -> None:
        """Main dispatch loop — claims slots before submitting attempts."""
        while not self._inner.stop_event.is_set():
            self._reap_futures()
            self._inner.check_heartbeat_timeouts()

            claimed = False
            while True:
                attempt_info = self._try_claim_and_dispatch()
                if attempt_info is None:
                    break
                claimed = True

            if claimed:
                continue
            self._inner.wake_event.wait(POLL_INTERVAL_SECONDS)
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
            log.warning("pool_dispatch_peek_failed")
            return None

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
        try:
            future = self._inner.executor.submit(handler, attempt.step_attempt_id)
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
        """Reap completed futures, release pool slots, and handle failures."""
        done: list[concurrent.futures.Future[Any]] = []
        with self._inner.lock:
            for future in list(self._inner.futures):
                if future.done():
                    done.append(future)

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

            try:
                future.result()
                self._inner.on_attempt_completed(attempt_id)
            except Exception:
                log.exception(
                    "pool_dispatch_attempt_failed",
                    step_attempt_id=attempt_id,
                )
                self._inner.force_fail_attempt(attempt_id)
