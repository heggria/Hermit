"""Write batcher for non-critical SQLite writes.

Buffers INSERT/UPDATE statements and flushes them in batches to reduce
SQLite write lock contention.  Critical-path writes (hash chain events,
claim queries) bypass the batcher entirely.
"""

from __future__ import annotations

import os
import queue
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger()

_BATCH_SIZE = int(os.environ.get("HERMIT_WRITE_BATCH_SIZE", "64"))
_FLUSH_INTERVAL_MS = int(os.environ.get("HERMIT_WRITE_BATCH_INTERVAL_MS", "50"))


@dataclass(frozen=True)
class _WriteOp:
    sql: str
    params: tuple[Any, ...]


class WriteBatcher:
    """Buffers non-critical writes and flushes them in batches."""

    def __init__(
        self,
        get_conn: Callable[[], sqlite3.Connection],
        close_conn: Callable[[], None] | None = None,
    ) -> None:
        self._get_conn = get_conn
        self._close_conn = close_conn
        self._queue: queue.Queue[_WriteOp] = queue.Queue(maxsize=4096)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._flush_loop, daemon=True, name="write-batcher")
        self._thread.start()

    def enqueue(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """Submit a write operation for batch execution."""
        try:
            self._queue.put_nowait(_WriteOp(sql=sql, params=params))
        except queue.Full:
            # Queue full -- flush synchronously to avoid data loss
            self._flush()
            self._queue.put_nowait(_WriteOp(sql=sql, params=params))

    def flush(self) -> int:
        """Flush all pending writes.  Returns count of flushed operations."""
        return self._flush()

    def stop(self) -> None:
        """Stop the batcher and flush remaining writes."""
        self._stop.set()
        self._flush()
        self._thread.join(timeout=5.0)

    def _flush_loop(self) -> None:
        interval = _FLUSH_INTERVAL_MS / 1000.0
        try:
            while not self._stop.is_set():
                self._stop.wait(interval)
                if not self._queue.empty():
                    self._flush()
        finally:
            # Close thread-local SQLite connection on loop exit so that
            # the file descriptor is not leaked when the daemon thread
            # terminates.
            if self._close_conn is not None:
                try:
                    self._close_conn()
                except Exception:
                    pass

    def _flush(self) -> int:
        ops: list[_WriteOp] = []
        while len(ops) < _BATCH_SIZE * 4:
            try:
                ops.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if not ops:
            return 0

        conn = self._get_conn()
        # Group by SQL template for executemany optimisation
        grouped: dict[str, list[tuple[Any, ...]]] = {}
        for op in ops:
            grouped.setdefault(op.sql, []).append(op.params)

        try:
            with conn:
                for sql, params_list in grouped.items():
                    if len(params_list) == 1:
                        conn.execute(sql, params_list[0])
                    else:
                        conn.executemany(sql, params_list)
        except Exception:
            # Log the failure with enough context for operators to diagnose
            # the issue.  The ops have already been dequeued, so they cannot
            # be retried automatically — surface the loss explicitly rather
            # than letting it pass silently.
            first_sql = next(iter(grouped))
            log.exception(
                "write_batcher_flush_error",
                dropped_ops=len(ops),
                first_sql_template=first_sql,
            )
            return 0

        return len(ops)

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()
