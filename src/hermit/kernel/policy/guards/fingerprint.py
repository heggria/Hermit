from __future__ import annotations

import hashlib
import json
from typing import Any


def build_action_fingerprint(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
