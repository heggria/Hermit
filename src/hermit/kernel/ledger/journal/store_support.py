from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, cast

UNSET = object()
_UNSET = UNSET


def json_loads(raw: str | None) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        import structlog

        structlog.get_logger().warning("json_parse_error", raw_preview=raw[:200] if raw else "")
        return {}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_json_from_raw(raw: str | None) -> str:
    if raw is None or raw == "":
        return canonical_json({})
    try:
        return canonical_json(json.loads(raw))
    except json.JSONDecodeError:
        return canonical_json(raw)


def sha256_hex(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def sqlite_optional_text(value: Any, *, default: str | None = None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return default


def sqlite_optional_float(value: Any, *, default: float | None = None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return default


def sqlite_int(value: Any, *, default: int = 0, minimum: int | None = None) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    if minimum is not None:
        normalized = max(normalized, minimum)
    return normalized


def sqlite_dict(value: Any, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, Any], value))
    return dict(default or {})


def sqlite_list(value: Any, *, default: Sequence[Any] | None = None) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[Any], value))
    return list(default or [])


_json_loads = json_loads
_canonical_json = canonical_json
_canonical_json_from_raw = canonical_json_from_raw
_sha256_hex = sha256_hex
_sqlite_optional_text = sqlite_optional_text
_sqlite_optional_float = sqlite_optional_float
_sqlite_int = sqlite_int
_sqlite_dict = sqlite_dict
_sqlite_list = sqlite_list
