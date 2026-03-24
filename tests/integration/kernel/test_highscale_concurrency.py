"""Integration tests for high-scale (2048-task) concurrency support.

Validates IN-clause chunking, ready_for_dispatch lifecycle, concurrent claim
safety, LRU lock eviction, batch updates, and WriteBatcher integration.
"""

from __future__ import annotations

import time

from hermit.kernel.ledger.journal.store import KernelStore


def _make_task(store: KernelStore, description: str, *, status: str = "running") -> str:
    """Create a task with minimal boilerplate and return its task_id."""
    conv_id = store._id("conv")
    store.ensure_conversation(conv_id, source_channel="test")
    task = store.create_task(
        conversation_id=conv_id,
        title=description,
        goal=description,
        source_channel="test",
        status=status,
    )
    return task.task_id


class TestHighScaleConcurrency:
    """Verify kernel store operations at 2048-task scale."""

    def test_batch_events_for_2048_tasks(self, kernel_store: KernelStore) -> None:
        """list_events_for_tasks handles 2048 task_ids with IN-clause chunking."""
        store = kernel_store
        task_ids: list[str] = []
        for i in range(2048):
            tid = _make_task(store, f"task-{i}")
            store.create_step(task_id=tid, kind="execute")
            task_ids.append(tid)

        # Batch fetch events for all 2048 tasks
        result = store.list_events_for_tasks(task_ids)
        assert len(result) == 2048
        for tid in task_ids:
            assert tid in result
            # At least task.created + step.started events
            assert len(result[tid]) > 0

    def test_get_last_event_per_task_100(self, kernel_store: KernelStore) -> None:
        """get_last_event_per_task handles 100 task_ids (kept small for speed)."""
        store = kernel_store
        task_ids: list[str] = []
        for i in range(100):
            tid = _make_task(store, f"task-{i}")
            task_ids.append(tid)

        result = store.get_last_event_per_task(task_ids)
        assert len(result) == 100

    def test_ready_for_dispatch_lifecycle(self, kernel_store: KernelStore) -> None:
        """ready_for_dispatch flag is correctly set through step lifecycle."""
        store = kernel_store
        tid = _make_task(store, "lifecycle test")

        # Step with no dependencies -> ready_for_dispatch = 1
        s1 = store.create_step(task_id=tid, kind="execute")
        row = store._row(
            "SELECT ready_for_dispatch FROM steps WHERE step_id = ?",
            (s1.step_id,),
        )
        assert row is not None
        assert int(row["ready_for_dispatch"]) == 1

        # Step with dependencies -> ready_for_dispatch = 0
        s2 = store.create_step(
            task_id=tid,
            kind="verify",
            depends_on=[s1.step_id],
            status="waiting",
        )
        row2 = store._row(
            "SELECT ready_for_dispatch FROM steps WHERE step_id = ?",
            (s2.step_id,),
        )
        assert row2 is not None
        assert int(row2["ready_for_dispatch"]) == 0

    def test_claim_uses_ready_for_dispatch(self, kernel_store: KernelStore) -> None:
        """claim_next_ready_step_attempt only considers ready_for_dispatch=1 steps."""
        store = kernel_store
        tid = _make_task(store, "claim test", status="queued")

        # Create a ready step with ready_for_dispatch = 1
        s = store.create_step(task_id=tid, kind="execute", status="ready")
        store.create_step_attempt(
            task_id=tid,
            step_id=s.step_id,
            attempt=1,
            status="ready",
            context={},
        )

        attempt = store.claim_next_ready_step_attempt()
        assert attempt is not None

    def test_concurrent_claims_no_double_dispatch(self, kernel_store: KernelStore) -> None:
        """Sequential claims never double-dispatch the same attempt.

        Note: in-memory SQLite uses a single shared connection, so true
        multi-threaded concurrency is not safely testable here. This test
        validates the claim-and-transition invariant: once an attempt is
        claimed, subsequent claims never return it again.
        """
        store = kernel_store
        num_tasks = 50
        # Create tasks with ready steps
        for i in range(num_tasks):
            tid = _make_task(store, f"task-{i}", status="queued")
            s = store.create_step(task_id=tid, kind="execute", status="ready")
            store.create_step_attempt(
                task_id=tid,
                step_id=s.step_id,
                attempt=1,
                status="ready",
                context={},
            )

        claimed: list[str] = []
        for _ in range(num_tasks + 10):  # Try more than available
            attempt = store.claim_next_ready_step_attempt()
            if attempt:
                claimed.append(attempt.step_attempt_id)

        # No duplicates -- each attempt claimed exactly once
        assert len(claimed) == len(set(claimed))
        assert len(claimed) == num_tasks

        # No more attempts available
        extra = store.claim_next_ready_step_attempt()
        assert extra is None

    def test_task_lock_lru_eviction(self, kernel_store: KernelStore) -> None:
        """Task lock LRU doesn't grow unbounded."""
        store = kernel_store
        # Create more tasks than the LRU limit
        for i in range(100):
            tid = _make_task(store, f"task-{i}")
            store.create_step(task_id=tid, kind="execute")

        # Lock count should be bounded
        assert len(store._task_locks) <= store._MAX_TASK_LOCKS

    def test_batch_update_step_attempts(self, kernel_store: KernelStore) -> None:
        """batch_update_step_attempts updates all rows atomically."""
        store = kernel_store
        tid = _make_task(store, "batch update test")
        s = store.create_step(task_id=tid, kind="execute")

        attempts = []
        for i in range(5):
            a = store.create_step_attempt(
                task_id=tid,
                step_id=s.step_id,
                attempt=i + 1,
                status="ready",
                context={},
            )
            attempts.append(a)

        updates = [
            (
                "failed",
                a.step_attempt_id,
                time.time(),
            )
            for a in attempts
        ]
        count = store.batch_update_step_attempts(updates)
        assert count == 5

        for a in attempts:
            updated = store.get_step_attempt(a.step_attempt_id)
            assert updated is not None
            assert updated.status == "failed"

    def test_write_batcher_integration(self, kernel_store: KernelStore) -> None:
        """WriteBatcher flushes and data is readable after flush."""
        store = kernel_store
        tid = _make_task(store, "batcher test")

        # Use _batch_execute for a non-critical write
        store._batch_execute(
            "UPDATE tasks SET title = ? WHERE task_id = ?",
            ("updated via batcher", tid),
        )

        # After batch_execute (which includes flush), data should be readable
        row = store._row("SELECT title FROM tasks WHERE task_id = ?", (tid,))
        assert row is not None
        assert str(row["title"]) == "updated via batcher"

    def test_schema_version_is_19(self, kernel_store: KernelStore) -> None:
        """Verify schema version is correctly set to 19."""
        assert kernel_store.schema_version() == "19"

    def test_migration_backfill_ready_for_dispatch(self, kernel_store: KernelStore) -> None:
        """Steps with no dependencies and ready/running status get backfilled."""
        store = kernel_store
        tid = _make_task(store, "backfill test")

        # Manually insert a step with ready_for_dispatch=0 that should be backfilled
        step_id = store._id("step")
        now = time.time()
        store._conn.execute(
            """
            INSERT INTO steps (
                step_id, task_id, kind, status, attempt,
                depends_on_json, max_attempts,
                started_at, created_at, updated_at, ready_for_dispatch
            ) VALUES (?, ?, ?, ?, 1, '[]', 1, ?, ?, ?, 0)
            """,
            (step_id, tid, "execute", "ready", now, now, now),
        )
        store._conn.commit()

        # Run the migration (idempotent)
        store._migrate_to_v19()

        row = store._row(
            "SELECT ready_for_dispatch FROM steps WHERE step_id = ?",
            (step_id,),
        )
        assert row is not None
        assert int(row["ready_for_dispatch"]) == 1
