"""Atomic file write via tempfile + os.replace().

os.replace() (POSIX rename) is guaranteed atomic on the same filesystem,
so readers never see a partially written file.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write *content* to *path* atomically.

    The write is staged to a sibling temp file in the same directory and then
    renamed over *path*.  Because rename(2) is atomic on POSIX systems (and
    nearly so on Windows via MoveFileEx), readers always see either the old or
    the new content — never a partial write.

    The parent directory is created if it does not exist.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
