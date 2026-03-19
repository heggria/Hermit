"""Test that concurrent event appends to the same task produce a valid hash chain."""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.verification.proofs.proofs import ProofService


def _make_file_store() -> KernelStore:
    """Create a file-backed KernelStore so each thread gets its own connection."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        pass
    return KernelStore(Path(tmp.name))


def _create_task(store: KernelStore) -> str:
    """Create a task and return its task_id."""
    record = store.create_task(
        conversation_id=store.generate_id("conv"),
        title="concurrency test",
        goal="test hash chain under concurrency",
        source_channel="test",
    )
    return record.task_id


def test_concurrent_appends_produce_valid_chain():
    """Concurrent event appends to the same task must not fork the hash chain."""
    store = _make_file_store()
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


def test_concurrent_appends_to_different_tasks():
    """Events for different tasks should not interfere with each other's chains."""
    store = _make_file_store()

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
