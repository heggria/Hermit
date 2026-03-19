"""Per-path file locking with two layers of protection.

Layer 1 — threading.RLock (in-process):
    Serialises concurrent threads within the same process (e.g. feishu
    ThreadPoolExecutor handling multiple messages simultaneously).

Layer 2 — fcntl.flock LOCK_EX (cross-process, optional):
    Serialises concurrent *processes* sharing the same file (e.g. two
    ``hermit serve`` adapters writing to the same memories.md).
    Only available on POSIX; silently skipped on Windows.

Usage::

    with FileGuard.acquire(path):            # in-process only
        ...
    with FileGuard.acquire(path, cross_process=True):   # + flock
        ...
"""

from __future__ import annotations

import contextlib
import threading
import weakref
from collections.abc import Iterator
from pathlib import Path

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None

_has_fcntl = _fcntl is not None


class FileGuard:
    """Registry of per-path threading.RLock instances.

    Each canonical (resolved) path gets exactly one RLock for the lifetime of
    the lock object.  Entries are automatically removed from the registry once
    no caller holds a reference to the RLock (WeakValueDictionary), preventing
    unbounded growth when many distinct paths are accessed over time.
    """

    # WeakValueDictionary: entries are evicted automatically when the RLock is
    # no longer referenced by any acquire() caller, preventing the memory leak
    # that would occur with a plain dict whose entries are never removed.
    _registry: weakref.WeakValueDictionary[Path, threading.RLock] = weakref.WeakValueDictionary()
    _registry_lock: threading.Lock = threading.Lock()

    @classmethod
    def _get_rlock(cls, path: Path) -> threading.RLock:
        canonical = path.resolve()
        with cls._registry_lock:
            rlock = cls._registry.get(canonical)
            if rlock is None:
                rlock = threading.RLock()
                cls._registry[canonical] = rlock
            return rlock

    @classmethod
    def cleanup(cls) -> int:
        """Remove all entries from the registry.

        Normally unnecessary — WeakValueDictionary handles cleanup
        automatically.  Exposed here for testing and explicit teardown.

        Returns:
            Number of entries that were present before clearing.
        """
        with cls._registry_lock:
            count = len(cls._registry)
            cls._registry.clear()
            return count

    @classmethod
    @contextlib.contextmanager
    def acquire(cls, path: Path, cross_process: bool = False) -> Iterator[None]:
        """Context manager that acquires all necessary locks for *path*.

        Args:
            path: The file to protect.  Need not exist yet.
            cross_process: When True and fcntl is available, also acquire an
                exclusive flock on a sibling ``.lock`` file so that concurrent
                *processes* are serialised as well.
        """
        path = Path(path)
        rlock = cls._get_rlock(path)

        with rlock:
            if cross_process and _has_fcntl:
                lock_file = path.with_suffix(path.suffix + ".lock")
                lock_file.parent.mkdir(parents=True, exist_ok=True)
                assert _fcntl is not None
                with open(lock_file, "a") as fh:
                    _fcntl.flock(fh, _fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        _fcntl.flock(fh, _fcntl.LOCK_UN)
            else:
                yield
