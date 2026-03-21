"""Benchmark history store — machine-aware, source-versioned historical tracking.

Provides:
- MachineFingerprint: stable machine identity for result stratification (Conbench approach)
- compute_source_hash: SHA-256 of benchmark function source (ASV versioning)
- BenchmarkHistoryRecord: immutable record for a single benchmark execution
- BenchmarkHistoryStoreMixin: SQLite mixin for KernelStore benchmark persistence
- is_comparable: comparability check between two history records
"""

from __future__ import annotations

import hashlib
import inspect
import json
import platform
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hermit.kernel.ledger.journal.store_types import KernelStoreTypingBase

__all__ = [
    "BenchmarkHistoryRecord",
    "BenchmarkHistoryStoreMixin",
    "MachineFingerprint",
    "compute_source_hash",
    "is_comparable",
]


# ---------------------------------------------------------------------------
# Machine Fingerprint (Conbench approach)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MachineFingerprint:
    """Machine identity for result stratification (Conbench approach).

    A stable fingerprint of the execution environment so benchmark results
    recorded on different machines are never naively compared.
    """

    node_name: str
    cpu_count: int
    architecture: str
    platform: str
    python_version: str

    @staticmethod
    def current() -> MachineFingerprint:
        """Capture current machine fingerprint."""
        import os
        import sys

        return MachineFingerprint(
            node_name=platform.node(),
            cpu_count=os.cpu_count() or 1,
            architecture=platform.machine(),
            platform=platform.platform(),
            python_version=sys.version.split()[0],
        )

    def hash(self) -> str:
        """MD5 hash of fingerprint for comparison."""
        payload = (
            f"{self.node_name}|{self.cpu_count}|"
            f"{self.architecture}|{self.platform}|{self.python_version}"
        )
        return hashlib.md5(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Benchmark Versioning (ASV method)
# ---------------------------------------------------------------------------


def compute_source_hash(fn: Callable[..., Any] | str) -> str:
    """SHA-256 hash of benchmark function source code (ASV method).

    If *fn* is callable, uses ``inspect.getsource()`` to retrieve the source.
    If *fn* is a string (raw source code), hashes directly.

    Source code changes cause the hash to change, invalidating old results
    so that only structurally-identical benchmarks are compared.
    """
    if isinstance(fn, str):
        source = fn
    else:
        source = inspect.getsource(fn)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# History Record Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkHistoryRecord:
    """Single benchmark execution record for historical tracking."""

    run_id: str
    profile_id: str
    task_id: str | None
    git_commit: str | None
    source_hash: str
    machine_fingerprint: str  # MachineFingerprint.hash()
    metric_name: str
    samples: tuple[float, ...]
    median: float
    ci_lower: float
    ci_upper: float
    environment: dict[str, str]
    created_at: str  # ISO 8601


# ---------------------------------------------------------------------------
# Comparison Helper
# ---------------------------------------------------------------------------


def is_comparable(
    record_a: BenchmarkHistoryRecord,
    record_b: BenchmarkHistoryRecord,
) -> bool:
    """Check if two records can be meaningfully compared.

    Comparable when:
    - Same profile_id
    - Same metric_name
    - Same source_hash (ASV versioning)
    - Same machine_fingerprint (Conbench stratification)
    """
    return (
        record_a.profile_id == record_b.profile_id
        and record_a.metric_name == record_b.metric_name
        and record_a.source_hash == record_b.source_hash
        and record_a.machine_fingerprint == record_b.machine_fingerprint
    )


# ---------------------------------------------------------------------------
# Serialisation helpers (stdlib-only)
# ---------------------------------------------------------------------------


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def _json_loads(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# History Store (SQLite mixin)
# ---------------------------------------------------------------------------

BENCHMARK_HISTORY_DDL = """\
CREATE TABLE IF NOT EXISTS benchmark_history (
    run_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    task_id TEXT,
    git_commit TEXT,
    source_hash TEXT NOT NULL,
    machine_fingerprint TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    samples TEXT NOT NULL,
    median REAL NOT NULL,
    ci_lower REAL NOT NULL,
    ci_upper REAL NOT NULL,
    environment TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

BENCHMARK_HISTORY_INDEXES = [
    ("CREATE INDEX IF NOT EXISTS idx_bench_hist_profile ON benchmark_history(profile_id)"),
    ("CREATE INDEX IF NOT EXISTS idx_bench_hist_source ON benchmark_history(source_hash)"),
    ("CREATE INDEX IF NOT EXISTS idx_bench_hist_machine ON benchmark_history(machine_fingerprint)"),
    ("CREATE INDEX IF NOT EXISTS idx_bench_hist_created ON benchmark_history(created_at)"),
]


class BenchmarkHistoryStoreMixin(KernelStoreTypingBase):
    """Mixin for KernelStore providing benchmark history persistence.

    Uses the same pattern as ``SelfIterateStoreMixin``: inherits from
    ``KernelStoreTypingBase`` for access to ``_get_conn``, ``_row``, and
    ``_rows`` stubs, and expects to be composed into ``KernelStore``.
    """

    def _ensure_benchmark_history_table(self) -> None:
        """Create benchmark_history table if not exists."""
        conn = self._get_conn()
        conn.executescript(BENCHMARK_HISTORY_DDL)
        for idx_ddl in BENCHMARK_HISTORY_INDEXES:
            conn.execute(idx_ddl)

    def store_benchmark_run(self, record: BenchmarkHistoryRecord) -> None:
        """Store a benchmark run result."""
        conn = self._get_conn()
        with conn:
            conn.execute(
                """INSERT INTO benchmark_history (
                    run_id, profile_id, task_id, git_commit, source_hash,
                    machine_fingerprint, metric_name, samples, median,
                    ci_lower, ci_upper, environment, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.run_id,
                    record.profile_id,
                    record.task_id,
                    record.git_commit,
                    record.source_hash,
                    record.machine_fingerprint,
                    record.metric_name,
                    _json_dumps(list(record.samples)),
                    record.median,
                    record.ci_lower,
                    record.ci_upper,
                    _json_dumps(record.environment),
                    record.created_at,
                ),
            )

    def get_benchmark_history(
        self,
        profile_id: str,
        metric_name: str,
        source_hash: str | None = None,
        machine_fingerprint: str | None = None,
        limit: int = 100,
    ) -> list[BenchmarkHistoryRecord]:
        """Retrieve historical benchmark results for comparison.

        Filters:
        - Same profile_id (benchmark type)
        - Same metric_name (which metric to compare)
        - Same source_hash if provided (ASV versioning — skip incompatible)
        - Same machine_fingerprint if provided (Conbench stratification)

        Returns most recent first, up to *limit*.
        """
        clauses = ["profile_id = ?", "metric_name = ?"]
        params: list[Any] = [profile_id, metric_name]
        if source_hash is not None:
            clauses.append("source_hash = ?")
            params.append(source_hash)
        if machine_fingerprint is not None:
            clauses.append("machine_fingerprint = ?")
            params.append(machine_fingerprint)
        where = " AND ".join(clauses)
        params.append(limit)
        rows = self._rows(
            f"SELECT * FROM benchmark_history WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params,
        )
        return [_record_from_row(r) for r in rows]

    def get_baseline_samples(
        self,
        profile_id: str,
        metric_name: str,
        source_hash: str,
        machine_fingerprint: str,
        limit: int = 100,
    ) -> list[float]:
        """Get flattened baseline samples for regression detection.

        Returns all individual sample values from matching historical runs,
        most-recent runs first, up to *limit* runs.
        """
        rows = self._rows(
            "SELECT samples FROM benchmark_history "
            "WHERE profile_id = ? AND metric_name = ? "
            "AND source_hash = ? AND machine_fingerprint = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (profile_id, metric_name, source_hash, machine_fingerprint, limit),
        )
        result: list[float] = []
        for row in rows:
            parsed = _json_loads(row["samples"])
            if isinstance(parsed, list):
                result.extend(float(v) for v in parsed)
        return result


# ---------------------------------------------------------------------------
# Row → Record conversion (module-level to keep mixin lean)
# ---------------------------------------------------------------------------


def _record_from_row(row: Any) -> BenchmarkHistoryRecord:
    """Convert a ``sqlite3.Row`` to an immutable ``BenchmarkHistoryRecord``."""
    raw_samples = _json_loads(row["samples"])
    samples = tuple(float(v) for v in raw_samples) if isinstance(raw_samples, list) else ()
    raw_env = _json_loads(row["environment"])
    environment = dict(raw_env) if isinstance(raw_env, dict) else {}
    return BenchmarkHistoryRecord(
        run_id=str(row["run_id"]),
        profile_id=str(row["profile_id"]),
        task_id=row["task_id"] if row["task_id"] is not None else None,
        git_commit=row["git_commit"] if row["git_commit"] is not None else None,
        source_hash=str(row["source_hash"]),
        machine_fingerprint=str(row["machine_fingerprint"]),
        metric_name=str(row["metric_name"]),
        samples=samples,
        median=float(row["median"]),
        ci_lower=float(row["ci_lower"]),
        ci_upper=float(row["ci_upper"]),
        environment=environment,
        created_at=str(row["created_at"]),
    )
