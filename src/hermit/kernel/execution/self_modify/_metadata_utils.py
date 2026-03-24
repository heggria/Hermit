"""Shared metadata parsing utilities for self_modify modules."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

logger = logging.getLogger(__name__)


def parse_metadata(raw: Any) -> dict[str, Any]:
    """Parse JSON metadata that may be a JSON string, dict, or None.

    Handles str (JSON-encoded), dict (already parsed), None, and
    malformed inputs.  Returns an empty dict on any failure.

    Args:
        raw: The raw metadata value.  Accepted types are ``str``
            (JSON-encoded), ``dict`` (already parsed), and ``None``.

    Returns:
        A parsed ``dict``, or an empty dict when *raw* is ``None``,
        an empty string, or any value that cannot be decoded.
    """
    if raw is None or raw == "":
        return {}
    try:
        meta = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError) as exc:
        logger.debug("parse_metadata: could not parse value %r: %s", raw, exc)
        return {}
    if not isinstance(meta, dict):
        logger.debug(
            "parse_metadata: expected a JSON object, got %s; returning {}",
            type(meta).__name__,
        )
        return {}
    return cast(dict[str, Any], meta)
