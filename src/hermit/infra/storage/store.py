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
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from hermit.infra.locking.lock import FileGuard
from hermit.infra.storage.atomic import atomic_write


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
        default: Optional[Dict[str, Any]] = None,
        cross_process: bool = False,
    ) -> None:
        self.path = Path(path)
        self._default: Dict[str, Any] = default if default is not None else {}
        self._cross_process = cross_process

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self) -> Dict[str, Any]:
        """Return the current file contents as a dict.

        Returns ``default`` if the file does not exist or contains invalid
        JSON, rather than raising.
        """
        if not self.path.exists():
            return dict(self._default)
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return dict(self._default)

    def write(self, data: Dict[str, Any]) -> None:
        """Atomically overwrite the file with *data*.

        This is safe for a single writer; for concurrent read-modify-write
        use ``update()`` instead.
        """
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2))

    @contextlib.contextmanager
    def update(self) -> Iterator[Dict[str, Any]]:
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
