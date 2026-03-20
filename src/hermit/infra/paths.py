"""Canonical path helpers for the Hermit workspace.

Centralises ``project_root()`` so every package can import a single,
authoritative implementation instead of duplicating the walk-up logic.
"""

from __future__ import annotations

from pathlib import Path


def project_root() -> Path | None:
    """Return the repository root (directory containing ``pyproject.toml``).

    Walks up from this module's own location.  Returns *None* when running
    from an installed wheel where no ``pyproject.toml`` is present.
    """
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "pyproject.toml").exists():
        return candidate
    return None


def project_path(*parts: str, fallback_to_cwd: bool = True) -> Path | None:
    """Return a path relative to the repository root.

    When project root cannot be resolved, falls back to ``Path.cwd()`` by
    default. Pass ``fallback_to_cwd=False`` to return ``None`` instead.
    """
    project = project_root()
    if project is None:
        if not fallback_to_cwd:
            return None
        return Path.cwd().joinpath(*parts)
    return project.joinpath(*parts)
