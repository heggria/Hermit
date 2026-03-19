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
