from __future__ import annotations

import os
import shutil
from pathlib import Path


def _uv_fallback_paths() -> tuple[Path, ...]:
    home = Path.home()
    return (
        home / ".local" / "bin" / "uv",
        Path("/opt/homebrew/bin/uv"),
        Path("/usr/local/bin/uv"),
    )


def resolve_uv_bin() -> str:
    override = os.environ.get("HERMIT_UV_BIN")
    if override:
        return override

    discovered = shutil.which("uv")
    if discovered:
        return discovered

    for candidate in _uv_fallback_paths():
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise FileNotFoundError(
        "Could not find `uv`. Put it on PATH or set HERMIT_UV_BIN=/absolute/path/to/uv."
    )
