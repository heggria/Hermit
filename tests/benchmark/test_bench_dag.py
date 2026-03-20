"""DAG dispatch and step claiming performance benchmarks."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore

pytestmark = pytest.mark.benchmark


class TestDAGDispatchBenchmarks:
    """Benchmark DAG step creation, claiming, and dispatch operations."""

    def test_create_step(self, benchmark, tmp_db_path: Path):
        """Benchmark creating a step for an existing task."""
        store = KernelStore(tmp_db_path)
        task = store.create_task(
            conversation_id="bench-conv-dag",
            title="DAG benchmark task",
            goal="benchmark step creation",
            source_channel="benchmark",
        )
        counter = [0]

        def create():
            counter[0] += 1
            return store.create_step(
                task_id=task.task_id,
                kind="execute",
                title=f"Step {counter[0]}",
            )

        result = benchmark(create)
        assert result.step_id is not None
        store.close()

    def test_create_step_attempt(self, benchmark, tmp_db_path: Path):
        """Benchmark creating a step attempt."""
        store = KernelStore(tmp_db_path)
        task = store.create_task(
            conversation_id="bench-conv-attempt",
            title="Attempt benchmark task",
            goal="benchmark attempt creation",
            source_channel="benchmark",
        )
        step = store.create_step(
            task_id=task.task_id,
            kind="execute",
            title="Attempt step",
        )
        counter = [0]

        def create_attempt():
            counter[0] += 1
            return store.create_step_attempt(
                task_id=task.task_id,
                step_id=step.step_id,
                attempt=counter[0],
                status="ready",
            )

        result = benchmark(create_attempt)
        assert result.step_attempt_id is not None
        store.close()

    def test_claim_step_attempt(self, benchmark, tmp_db_path: Path):
        """Benchmark claiming the next ready step attempt."""
        store = KernelStore(tmp_db_path)
        task = store.create_task(
            conversation_id="bench-conv-claim",
            title="Claim benchmark task",
            goal="benchmark claiming",
            source_channel="benchmark",
        )
        counter = [0]

        def setup_and_claim():
            counter[0] += 1
            step = store.create_step(
                task_id=task.task_id,
                kind="execute",
                title=f"Claim step {counter[0]}",
                status="ready",
            )
            store.create_step_attempt(
                task_id=task.task_id,
                step_id=step.step_id,
                status="ready",
            )
            return store.claim_next_ready_step_attempt()

        result = benchmark(setup_and_claim)
        assert result is not None
        store.close()

    def test_claim_with_dependencies(self, benchmark, tmp_db_path: Path):
        """Benchmark claiming with dependency checks (DAG pattern)."""
        store = KernelStore(tmp_db_path)
        counter = [0]

        def setup_dag_and_claim():
            counter[0] += 1
            # Fresh task per iteration to avoid accumulation
            task = store.create_task(
                conversation_id=f"bench-conv-deps-{counter[0]}",
                title=f"DAG deps benchmark {counter[0]}",
                goal="benchmark claiming with deps",
                source_channel="benchmark",
            )
            # Create upstream step (already succeeded)
            upstream = store.create_step(
                task_id=task.task_id,
                kind="execute",
                title=f"Upstream {counter[0]}",
                status="succeeded",
            )
            # Create downstream step depending on upstream
            # Kernel forces status="waiting" when depends_on is set,
            # so we activate it manually to simulate dependency resolution.
            downstream = store.create_step(
                task_id=task.task_id,
                kind="execute",
                title=f"Downstream {counter[0]}",
                depends_on=[upstream.step_id],
            )
            store.update_step(downstream.step_id, status="ready")
            store.create_step_attempt(
                task_id=task.task_id,
                step_id=downstream.step_id,
                status="ready",
            )
            claimed = store.claim_next_ready_step_attempt()
            assert claimed is not None
            return claimed

        benchmark.pedantic(setup_dag_and_claim, iterations=1, rounds=50, warmup_rounds=5)
        store.close()

    def test_list_steps_for_task(self, benchmark, tmp_db_path: Path):
        """Benchmark listing all steps for a task with many steps."""
        store = KernelStore(tmp_db_path)
        task = store.create_task(
            conversation_id="bench-conv-liststeps",
            title="List steps benchmark",
            goal="benchmark step listing",
            source_channel="benchmark",
        )
        # Pre-create 20 steps
        for i in range(20):
            store.create_step(
                task_id=task.task_id,
                kind="execute",
                title=f"Step {i}",
            )

        def list_steps():
            return store.list_steps(task_id=task.task_id)

        result = benchmark(list_steps)
        assert len(result) == 20
        store.close()
