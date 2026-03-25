"""Shared metadata parsing utilities for self_modify modules."""

from __future__ import annotations

import json
from typing import Any


def parse_metadata(raw: Any) -> dict[str, Any]:
    """Parse JSON metadata that may be a JSON string, dict, or None.

    Handles str (JSON-encoded), dict (already parsed), None, and
    malformed inputs.  Returns an empty dict on any failure.
    """
    if not raw:
        return {}
    try:
        meta = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(meta, dict):
        return {}
    return meta
