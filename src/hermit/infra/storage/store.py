"""Thread-safe, atomically-written JSON file store.

JsonStore wraps a single JSON file and exposes three operations:

* ``read()``  — load current contents (no lock held).
* ``write()`` — atomic overwrite (no lock held).
* ``update()`` — context manager that holds the lock for the full
  read → modify → write cycle, eliminating TOCTOU races.

Example::

    store = JsonStore(Path("~/.hermit/memory/session_state.json"),
                      default={"session_index": 0})

    with store.update() as data:
        data["session_index"] += 1
        idx = data["session_index"]
    # lock released, file atomically updated
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from hermit.infra.locking.lock import FileGuard
from hermit.infra.storage.atomic import atomic_write

logger = logging.getLogger(__name__)


class JsonStore:
    """Atomic, thread-safe store for a single JSON file.

    Args:
        path: Path to the JSON file.  Need not exist yet.
        default: Value returned by ``read()`` when the file is absent or
            empty.  Defaults to ``{}``.
        cross_process: When True, also acquire an OS-level flock so that
            multiple *processes* are serialised (e.g. two adapter servers
            sharing the same state file).
    """

    def __init__(
        self,
        path: Path,
        default: dict[str, Any] | None = None,
        cross_process: bool = False,
    ) -> None:
        self.path = Path(path)
        self._default: dict[str, Any] = default if default is not None else {}
        self._cross_process = cross_process

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        """Return the current file contents as a dict.

        Returns ``default`` if the file does not exist yet.

        Raises:
            OSError: If the file exists but cannot be read (e.g. permission
                denied).  Only ``FileNotFoundError`` is silently treated as
                "use default"; all other I/O errors are re-raised so callers
                can detect misconfigured paths or permission problems early.
            json.JSONDecodeError: If the file exists but contains invalid
                JSON.  A warning is logged and the default is returned so
                that a truncated write during a previous crash does not
                permanently break the store, but the corruption is visible
                in logs.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return dict(self._default)
        # Other OSError subtypes (PermissionError, etc.) are intentionally
        # re-raised so callers notice misconfigured or inaccessible paths.

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "JsonStore: corrupt JSON in %s (%s); returning default. "
                "Consider inspecting or deleting the file.",
                self.path,
                exc,
            )
            return dict(self._default)

    def write(self, data: dict[str, Any]) -> None:
        """Atomically overwrite the file with *data*.

        This is safe for a single writer; for concurrent read-modify-write
        use ``update()`` instead.

        Raises:
            TypeError: If *data* is not a ``dict``.  Prevents accidentally
                writing a non-dict value (e.g. a list or ``None``) that would
                silently corrupt the store and cause ``read()`` to fall back
                to the default on the next access.
        """
        if not isinstance(data, dict):
            raise TypeError(f"JsonStore.write() expects a dict, got {type(data).__name__!r}")
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2))

    @contextlib.contextmanager
    def update(self) -> Iterator[dict[str, Any]]:
        """Atomic read-modify-write as a context manager.

        Acquires the lock, reads current data, yields it for in-place
        modification, then atomically writes the (modified) data back.
        If the body raises an exception the file is left unchanged.

        Example::

            with store.update() as data:
                data["counter"] = data.get("counter", 0) + 1
        """
        with FileGuard.acquire(self.path, self._cross_process):
            data = self.read()
            try:
                yield data
            except Exception:
                raise  # do not persist partial / invalid state
            else:
                self.write(data)
