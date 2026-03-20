"""Concurrent task throughput performance benchmarks."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore

pytestmark = pytest.mark.benchmark


class TestConcurrentThroughputBenchmarks:
    """Benchmark concurrent task/step operations to measure kernel throughput."""

    def test_parallel_task_creation(self, benchmark, tmp_db_path: Path):
        """Benchmark creating tasks from multiple threads simultaneously."""
        store = KernelStore(tmp_db_path)

        def create_batch():
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = []
                for i in range(20):
                    futures.append(
                        pool.submit(
                            store.create_task,
                            conversation_id=f"par-conv-{i}",
                            title=f"Parallel task {i}",
                            goal="parallel benchmark",
                            source_channel="benchmark",
                        )
                    )
                results = [f.result() for f in futures]
            return results

        results = benchmark.pedantic(create_batch, iterations=1, rounds=5, warmup_rounds=1)
        assert len(results) == 20
        store.close()

    def test_parallel_event_append(self, benchmark, tmp_db_path: Path):
        """Benchmark appending events from multiple threads to different tasks."""
        store = KernelStore(tmp_db_path)
        # Pre-create tasks for separate event chains
        task_ids = []
        for i in range(4):
            task = store.create_task(
                conversation_id=f"evt-conv-{i}",
                title=f"Event task {i}",
                goal="event benchmark",
                source_channel="benchmark",
            )
            task_ids.append(task.task_id)
        counter = [0]

        def append_parallel():
            counter[0] += 1
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = []
                for idx, tid in enumerate(task_ids):
                    for j in range(5):
                        futures.append(
                            pool.submit(
                                store.append_event,
                                event_type="tool_execution",
                                entity_type="step",
                                entity_id=f"step-par-{counter[0]}-{idx}-{j}",
                                task_id=tid,
                                payload={"idx": idx, "j": j},
                            )
                        )
                results = [f.result() for f in futures]
            return results

        results = benchmark.pedantic(append_parallel, iterations=1, rounds=5, warmup_rounds=1)
        assert len(results) == 20
        store.close()

    def test_parallel_claim_contention(self, benchmark, tmp_db_path: Path):
        """Benchmark claim contention — multiple threads racing to claim attempts."""
        store = KernelStore(tmp_db_path)
        counter = [0]

        def contended_claim():
            counter[0] += 1
            task = store.create_task(
                conversation_id=f"contend-conv-{counter[0]}",
                title=f"Contention task {counter[0]}",
                goal="contention benchmark",
                source_channel="benchmark",
            )
            # Create 4 ready step attempts
            for i in range(4):
                step = store.create_step(
                    task_id=task.task_id,
                    kind="execute",
                    title=f"Contend step {i}",
                )
                store.create_step_attempt(
                    task_id=task.task_id,
                    step_id=step.step_id,
                    status="ready",
                )
            # 8 threads race to claim 4 attempts
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(store.claim_next_ready_step_attempt) for _ in range(8)]
                results = [f.result() for f in futures]
            claimed = [r for r in results if r is not None]
            return claimed

        claimed = benchmark.pedantic(contended_claim, iterations=1, rounds=5, warmup_rounds=1)
        # Each attempt should be claimed at most once
        claimed_ids = [c.step_attempt_id for c in claimed]
        assert len(claimed_ids) == len(set(claimed_ids)), "Duplicate claims detected!"
        store.close()

    def test_throughput_task_lifecycle(self, benchmark, tmp_db_path: Path):
        """Benchmark full task lifecycle: create -> step -> attempt -> claim -> complete."""
        store = KernelStore(tmp_db_path)
        counter = [0]

        def full_lifecycle():
            counter[0] += 1
            # 1. Create task
            task = store.create_task(
                conversation_id=f"lifecycle-conv-{counter[0]}",
                title=f"Lifecycle task {counter[0]}",
                goal="lifecycle benchmark",
                source_channel="benchmark",
            )
            # 2. Create step (status="ready" so it can be claimed)
            step = store.create_step(
                task_id=task.task_id,
                kind="execute",
                title="Lifecycle step",
                status="ready",
            )
            # 3. Create attempt
            store.create_step_attempt(
                task_id=task.task_id,
                step_id=step.step_id,
                status="ready",
            )
            # 4. Claim
            claimed = store.claim_next_ready_step_attempt()
            # 5. Complete
            if claimed:
                store.update_step_attempt(
                    claimed.step_attempt_id,
                    status="succeeded",
                )
                store.update_step(step.step_id, status="succeeded")
                store.update_task_status(task.task_id, status="completed")
            return claimed

        result = benchmark(full_lifecycle)
        assert result is not None
        store.close()
