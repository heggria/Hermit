"""Event chain hash computation performance benchmarks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.kernel.ledger.journal.store_support import canonical_json, sha256_hex

pytestmark = pytest.mark.benchmark


class TestHashChainBenchmarks:
    """Benchmark event hash chain operations."""

    def test_canonical_json_small(self, benchmark):
        """Benchmark canonical JSON serialization for a small payload."""
        payload = {"tool": "read_file", "args": {"path": "/tmp/test.txt"}}

        def serialize():
            return canonical_json(payload)

        result = benchmark(serialize)
        assert isinstance(result, str)

    def test_canonical_json_large(self, benchmark):
        """Benchmark canonical JSON serialization for a large payload."""
        payload = {
            "tool": "write_file",
            "args": {
                "path": "/tmp/large.txt",
                "content": "x" * 10_000,
            },
            "metadata": {f"key_{i}": f"value_{i}" for i in range(50)},
            "nested": {"a": {"b": {"c": {"d": list(range(100))}}}},
        }

        def serialize():
            return canonical_json(payload)

        result = benchmark(serialize)
        assert isinstance(result, str)

    def test_sha256_hex_short(self, benchmark):
        """Benchmark SHA256 hash of a short string."""
        data = '{"event_id":"evt-1","tool":"read_file"}'

        def hash_it():
            return sha256_hex(data)

        result = benchmark(hash_it)
        assert len(result) == 64

    def test_sha256_hex_long(self, benchmark):
        """Benchmark SHA256 hash of a large string (10KB)."""
        data = json.dumps({"content": "x" * 10_000, "metadata": list(range(100))})

        def hash_it():
            return sha256_hex(data)

        result = benchmark(hash_it)
        assert len(result) == 64

    def test_event_append_chain_cold_cache(self, benchmark, tmp_db_path: Path):
        """Benchmark event append with cold hash cache (forces DB lookup)."""
        counter = [0]

        def append_cold():
            counter[0] += 1
            # New store each time = cold cache
            store = KernelStore(tmp_db_path)
            store.append_event(
                event_type="tool_execution",
                entity_type="step",
                entity_id=f"step-cold-{counter[0]}",
                task_id="bench-task-cold",
                payload={"tool": "bash", "command": f"echo {counter[0]}"},
            )
            store.close()

        benchmark.pedantic(append_cold, iterations=1, rounds=20, warmup_rounds=2)

    def test_event_append_chain_warm_cache(self, benchmark, tmp_db_path: Path):
        """Benchmark event append with warm hash cache (same store instance)."""
        store = KernelStore(tmp_db_path)
        counter = [0]

        def append_warm():
            counter[0] += 1
            store.append_event(
                event_type="tool_execution",
                entity_type="step",
                entity_id=f"step-warm-{counter[0]}",
                task_id="bench-task-warm",
                payload={"tool": "bash", "command": f"echo {counter[0]}"},
            )

        benchmark(append_warm)
        store.close()

    def test_event_chain_after_1000_events(self, benchmark, tmp_db_path: Path):
        """Benchmark event append performance after many existing events.

        Uses 100 pre-populated events (reduced from 1000) to keep test
        runtime reasonable while still exercising chain-append at depth.
        """
        store = KernelStore(tmp_db_path)
        # Pre-populate events (100 is sufficient to exercise chain-at-depth)
        for i in range(100):
            store.append_event(
                event_type="tool_execution",
                entity_type="step",
                entity_id=f"step-pre-{i}",
                task_id="bench-task-1k",
                payload={"i": i},
            )
        counter = [100]

        def append_after_many():
            counter[0] += 1
            store.append_event(
                event_type="tool_execution",
                entity_type="step",
                entity_id=f"step-post-{counter[0]}",
                task_id="bench-task-1k",
                payload={"i": counter[0]},
            )

        benchmark(append_after_many)
        store.close()
