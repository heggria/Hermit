from __future__ import annotations

from typing import Any, Dict, Iterable, List

_ALLOWED_BLOCK_KEYS = {
    "text": {"type", "text"},
    "tool_use": {"type", "id", "name", "input"},
    "tool_result": {"type", "tool_use_id", "content", "is_error"},
    "thinking": {"type", "thinking", "signature"},
    "image": {"type", "source"},
}
_FALLBACK_KEYS = {
    "type",
    "text",
    "id",
    "name",
    "input",
    "thinking",
    "signature",
    "tool_use_id",
    "content",
    "is_error",
    "source",
}


def block_value(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def normalize_block(block: Any) -> Dict[str, Any]:
    """Convert SDK blocks or raw dicts to Hermit's internal block shape."""
    if isinstance(block, dict):
        block_type = str(block.get("type", ""))
        allowed = _ALLOWED_BLOCK_KEYS.get(block_type)
        if allowed:
            return {k: v for k, v in block.items() if k in allowed}
        return dict(block)

    raw: Dict[str, Any] = {}
    if hasattr(block, "model_dump"):
        raw = block.model_dump()
    elif hasattr(block, "to_dict"):
        raw = block.to_dict()
    else:
        for attr in _FALLBACK_KEYS:
            val = getattr(block, attr, None)
            if val is not None:
                raw[attr] = val

    block_type = str(raw.get("type", ""))
    allowed = _ALLOWED_BLOCK_KEYS.get(block_type)
    if allowed:
        return {k: v for k, v in raw.items() if k in allowed}
    return raw


def normalize_message(message: Dict[str, Any]) -> Dict[str, Any]:
    role = str(message.get("role", "user"))
    content = message.get("content", "")
    if isinstance(content, list):
        return {
            "role": role,
            "content": [normalize_block(block) for block in content],
        }
    return {"role": role, "content": content}


def normalize_messages(messages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [normalize_message(message) for message in messages]


def extract_text(blocks: list[Any]) -> str:
    text_parts: list[str] = []
    for block in blocks:
        if block_value(block, "type") == "text":
            text = block_value(block, "text", "")
            if text:
                text_parts.append(str(text))
    return "\n".join(text_parts).strip()


def extract_thinking(blocks: list[Any]) -> str:
    parts: list[str] = []
    for block in blocks:
        if block_value(block, "type") == "thinking":
            text = block_value(block, "thinking", "")
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()
