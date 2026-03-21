"""Test that concurrent event appends to the same task produce a valid hash chain."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.proofs.proofs import ProofService


def _create_task(store: KernelStore) -> str:
    """Create a task and return its task_id."""
    record = store.create_task(
        conversation_id=store.generate_id("conv"),
        title="concurrency test",
        goal="test hash chain under concurrency",
        source_channel="test",
    )
    return record.task_id


def test_concurrent_appends_produce_valid_chain(tmp_path):
    """Concurrent event appends to the same task must not fork the hash chain."""
    store = KernelStore(tmp_path / "chain.db")
    try:
        task_id = _create_task(store)

        n_workers = 8
        n_events = 50
        errors: list[str] = []

        def append_event(i: int) -> str:
            with store._get_conn():
                return store._append_event_tx(
                    event_id=store.generate_id("event"),
                    event_type="test.concurrent",
                    entity_type="task",
                    entity_id=task_id,
                    task_id=task_id,
                    actor="kernel",
                    payload={"index": i},
                )

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(append_event, i) for i in range(n_events)]
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as exc:
                    errors.append(str(exc))

        assert not errors, f"Event appends raised errors: {errors}"

        # Verify chain integrity via ProofService
        proof_svc = ProofService(store)
        result = proof_svc.verify_task_chain(task_id)
        assert result["valid"], f"Hash chain invalid: {result}"

        # Verify no duplicate prev_event_hash (no fork)
        rows = store._rows(
            "SELECT prev_event_hash FROM events WHERE task_id = ? ORDER BY event_seq ASC",
            (task_id,),
        )
        prev_hashes = [str(r["prev_event_hash"] or "") for r in rows]
        non_empty = [h for h in prev_hashes if h]
        assert len(non_empty) == len(set(non_empty)), (
            f"Duplicate prev_event_hash detected — chain forked: {prev_hashes}"
        )
        # +1 for the task.created event from create_task
        assert len(rows) == n_events + 1
    finally:
        store.close()


def test_concurrent_appends_to_different_tasks(tmp_path):
    """Events for different tasks should not interfere with each other's chains."""
    store = KernelStore(tmp_path / "multi.db")
    try:
        task_ids = [_create_task(store) for _ in range(4)]
        n_events_per_task = 20

        def append_for_task(task_id: str, i: int) -> str:
            with store._get_conn():
                return store._append_event_tx(
                    event_id=store.generate_id("event"),
                    event_type="test.multi",
                    entity_type="task",
                    entity_id=task_id,
                    task_id=task_id,
                    actor="kernel",
                    payload={"index": i},
                )

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = []
            for tid in task_ids:
                for i in range(n_events_per_task):
                    futures.append(pool.submit(append_for_task, tid, i))
            for fut in as_completed(futures):
                fut.result()

        proof_svc = ProofService(store)
        for tid in task_ids:
            result = proof_svc.verify_task_chain(tid)
            assert result["valid"], f"Chain invalid for {tid}: {result}"
    finally:
        store.close()


def test_interleaved_concurrent_appends_preserve_per_task_chains(tmp_path):
    """Two tasks append 10 events each concurrently; each task's hash chain
    must be independently valid and the chains must not share hashes.

    This is the regression test for the concurrent hash chain interleaving
    bug where INSERT+commit happening outside the per-task lock allowed
    event_seq ordering to diverge from the hash chain ordering.
    """
    store = KernelStore(tmp_path / "interleave.db")
    try:
        task_a = _create_task(store)
        task_b = _create_task(store)
        n_events = 10

        def append_for_task(task_id: str, i: int) -> str:
            return store.append_event(
                event_type=f"test.interleave.{i}",
                entity_type="task",
                entity_id=task_id,
                task_id=task_id,
                actor="kernel",
                payload={"task": task_id, "index": i},
            )

        # Submit events for both tasks interleaved across threads.
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = []
            for i in range(n_events):
                futures.append(pool.submit(append_for_task, task_a, i))
                futures.append(pool.submit(append_for_task, task_b, i))
            for fut in as_completed(futures):
                fut.result()

        # Verify each task's chain is independently valid via ProofService.
        proof_svc = ProofService(store)
        result_a = proof_svc.verify_task_chain(task_a)
        result_b = proof_svc.verify_task_chain(task_b)
        assert result_a["valid"], f"Task A chain invalid: {result_a}"
        assert result_b["valid"], f"Task B chain invalid: {result_b}"

        # Verify prev_event_hash continuity for each task.
        for tid, label in [(task_a, "A"), (task_b, "B")]:
            rows = store._rows(
                "SELECT event_hash, prev_event_hash FROM events "
                "WHERE task_id = ? ORDER BY event_seq ASC",
                (tid,),
            )
            # +1 for the task.created event
            assert len(rows) == n_events + 1, (
                f"Task {label}: expected {n_events + 1} events, got {len(rows)}"
            )
            prev: str | None = None
            for ev in rows:
                assert ev["prev_event_hash"] == prev, (
                    f"Task {label} chain broken: "
                    f"prev_event_hash={ev['prev_event_hash']!r}, expected={prev!r}"
                )
                prev = ev["event_hash"]

        # Verify the two chains are independent (no shared hashes).
        hashes_a = {
            str(r["event_hash"])
            for r in store._rows("SELECT event_hash FROM events WHERE task_id = ?", (task_a,))
        }
        hashes_b = {
            str(r["event_hash"])
            for r in store._rows("SELECT event_hash FROM events WHERE task_id = ?", (task_b,))
        }
        assert hashes_a.isdisjoint(hashes_b), (
            "Task chains must be independent — found shared hashes"
        )
    finally:
        store.close()
