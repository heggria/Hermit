"""Canonical path helpers for the Hermit workspace.

Centralises ``project_root()`` so every package can import a single,
authoritative implementation instead of duplicating the walk-up logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import overload


def project_root() -> Path | None:
    """Return the repository root (directory containing ``pyproject.toml``).

    Walks up from this module's own location, stopping at the first ancestor
    that contains a ``pyproject.toml`` file.  Returns *None* when no such
    ancestor exists (e.g. running from an installed wheel).
    """
    current = Path(__file__).resolve().parent
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return None


@overload
def project_path(*parts: str, fallback_to_cwd: bool = ...) -> Path: ...


@overload
def project_path(*parts: str, fallback_to_cwd: bool) -> Path | None: ...


def project_path(*parts: str, fallback_to_cwd: bool = True) -> Path | None:
    """Return a path relative to the repository root.

    When project root cannot be resolved, falls back to ``Path.cwd()`` by
    default (return type is always ``Path``).  Pass ``fallback_to_cwd=False``
    to receive ``None`` instead when the root is unavailable.

    Args:
        *parts: Path segments appended to the resolved root.
        fallback_to_cwd: When *True* (default) and the project root cannot be
            found, return a path relative to the current working directory.
            When *False*, return *None* so callers can detect the missing root.

    Returns:
        A resolved :class:`~pathlib.Path`, or *None* only when
        ``fallback_to_cwd=False`` and the root cannot be determined.
    """
    project = project_root()
    if project is None:
        if not fallback_to_cwd:
            return None
        return Path.cwd().joinpath(*parts)
    return project.joinpath(*parts)
