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

    After the rename the parent directory is also fsync'd so that the new
    directory entry is durable even if the system crashes immediately after.
    On platforms where directory fsync is unsupported (e.g. Windows) the
    OSError is silently ignored — the rename itself still provides atomicity.

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
        # Guard against os.fdopen() itself raising (e.g. invalid encoding),
        # which would leave *fd* open and the temp file on disk forever.
        try:
            fh = os.fdopen(fd, "w", encoding=encoding)
        except Exception:
            os.close(fd)
            raise

        with fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)

        # Fsync the directory so the renamed entry is durable on crash.
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Directory fsync is not supported on all platforms (e.g. Windows).
            pass
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
