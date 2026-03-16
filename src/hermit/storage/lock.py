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
from pathlib import Path
from typing import Dict, Iterator

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None

_has_fcntl = _fcntl is not None


class FileGuard:
    """Registry of per-path threading.RLock instances.

    Each canonical (resolved) path gets exactly one RLock for the lifetime of
    the process, so all callers sharing the same path will serialise through the
    same lock object.
    """

    _registry: Dict[Path, threading.RLock] = {}
    _registry_lock: threading.Lock = threading.Lock()

    @classmethod
    def _get_rlock(cls, path: Path) -> threading.RLock:
        canonical = path.resolve()
        with cls._registry_lock:
            if canonical not in cls._registry:
                cls._registry[canonical] = threading.RLock()
            return cls._registry[canonical]

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
