"""Runtime-layer performance benchmarks."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermit.infra.storage.store import JsonStore

pytestmark = pytest.mark.benchmark


class TestJsonStoreBenchmarks:
    """Benchmark JsonStore read/write operations."""

    def test_json_store_write(self, benchmark, tmp_json_path: Path):
        """Benchmark JsonStore write operation."""
        store = JsonStore(tmp_json_path)

        counter = [0]

        def write_data():
            counter[0] += 1
            store.write({"key": f"value-{counter[0]}", "nested": {"a": 1, "b": [1, 2, 3]}})

        benchmark(write_data)

    def test_json_store_read(self, benchmark, tmp_json_path: Path):
        """Benchmark JsonStore read operation."""
        store = JsonStore(tmp_json_path)
        store.write({"key": "value", "nested": {"a": 1, "b": list(range(100))}})

        def read_data():
            return store.read()

        benchmark(read_data)

    def test_json_store_update(self, benchmark, tmp_json_path: Path):
        """Benchmark JsonStore update operation."""
        store = JsonStore(tmp_json_path)
        store.write({"counter": 0, "items": []})

        counter = [0]

        def update_data():
            counter[0] += 1
            with store.update() as data:
                data["counter"] = counter[0]

        benchmark(update_data)
