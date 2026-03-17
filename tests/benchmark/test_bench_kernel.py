"""Kernel-layer performance benchmarks."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore

pytestmark = pytest.mark.benchmark


class TestKernelStoreBenchmarks:
    """Benchmark KernelStore read/write operations."""

    def test_store_init(self, benchmark, tmp_db_path: Path):
        """Benchmark KernelStore initialization."""

        def init_store():
            store = KernelStore(tmp_db_path)
            store.close()

        benchmark(init_store)

    def test_store_append_event(self, benchmark, tmp_db_path: Path):
        """Benchmark appending a single event to the ledger."""
        store = KernelStore(tmp_db_path)

        counter = [0]

        def append_event():
            counter[0] += 1
            store.append_event(
                event_type="tool_execution",
                entity_type="step",
                entity_id=f"step-{counter[0]}",
                task_id="bench-task-1",
                payload={"tool": "read_file", "args": {"path": "/tmp/test.txt"}},
            )

        benchmark(append_event)
        store.close()

    def test_store_list_tasks(self, benchmark, tmp_db_path: Path):
        """Benchmark listing tasks from the store."""
        store = KernelStore(tmp_db_path)
        for i in range(50):
            store.create_task(
                conversation_id=f"bench-conv-{i}",
                title=f"Benchmark task {i}",
                goal="benchmark goal",
                source_channel="benchmark",
            )

        def list_tasks():
            return store.list_tasks()

        benchmark(list_tasks)
        store.close()
