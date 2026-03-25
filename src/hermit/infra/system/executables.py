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
        override_path = Path(override)
        if not override_path.exists():
            raise FileNotFoundError(
                f"HERMIT_UV_BIN is set to '{override}' but that path does not exist."
            )
        if not os.access(override_path, os.X_OK):
            raise PermissionError(
                f"HERMIT_UV_BIN is set to '{override}' but that file is not executable."
            )
        # Return the validated Path as a string so the caller always receives
        # a normalized, absolute path — consistent with the fallback branches
        # below, and avoids returning a raw env-var string that may contain
        # trailing whitespace or relative components.
        return str(override_path)

    discovered = shutil.which("uv")
    if discovered:
        return discovered

    for candidate in _uv_fallback_paths():
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise FileNotFoundError(
        "Could not find `uv`. Put it on PATH or set HERMIT_UV_BIN=/absolute/path/to/uv."
    )
