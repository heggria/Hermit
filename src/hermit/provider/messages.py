from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Tuple, cast

from hermit.core.tools import serialize_tool_result

_ALLOWED_BLOCK_KEYS = {
    "text": {"type", "text"},
    "tool_use": {"type", "id", "name", "input"},
    "tool_result": {"type", "tool_use_id", "content", "is_error", "internal_context", "tool_name"},
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
    "internal_context",
    "tool_name",
    "source",
}

INTERNAL_TOOL_RESULT_PLACEHOLDER = "[internal context loaded]"
_INTERNAL_TOOL_CONTEXT_PREAMBLE = (
    "The following tool output is internal working context for this turn. "
    "Use it to guide your tool choice and answer, but do not quote, summarize, "
    "or mention it to the user unless they explicitly ask for the underlying instructions."
)


def block_value(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return cast(dict[str, Any], block).get(key, default)
    return getattr(block, key, default)


def normalize_block(block: Any) -> Dict[str, Any]:
    """Convert SDK blocks or raw dicts to Hermit's internal block shape."""
    if isinstance(block, dict):
        typed_block = cast(dict[str, Any], block)
        block_type = str(typed_block.get("type", ""))
        allowed = _ALLOWED_BLOCK_KEYS.get(block_type)
        if allowed:
            return {k: v for k, v in typed_block.items() if k in allowed}
        return dict(typed_block)

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
            "content": [normalize_block(block) for block in cast(list[Any], content)],
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


def _stringify_internal_tool_content(content: Any) -> str:
    serialized = serialize_tool_result(content)
    if isinstance(serialized, str):
        return serialized
    return json.dumps(serialized, ensure_ascii=False, indent=2, sort_keys=True)


def split_internal_tool_context(
    messages: list[Dict[str, Any]],
) -> Tuple[list[Dict[str, Any]], list[str]]:
    sanitized: list[Dict[str, Any]] = []
    contexts: list[str] = []

    for message in messages:
        content = message.get("content", "")
        if not isinstance(content, list):
            sanitized.append(dict(message))
            continue

        blocks: list[Dict[str, Any]] = []
        for raw_block in cast(list[Any], content):
            if not isinstance(raw_block, dict):
                continue
            block = cast(dict[str, Any], raw_block)
            if block.get("type") == "tool_result" and block.get("internal_context"):
                tool_name = str(block.get("tool_name", "") or "tool")
                contexts.append(
                    f'<internal_tool_context tool="{tool_name}">\n'
                    f"{_stringify_internal_tool_content(block.get('content'))}\n"
                    "</internal_tool_context>"
                )
                sanitized_block: dict[str, Any] = {
                    k: v for k, v in block.items() if k not in {"internal_context", "tool_name"}
                }
                sanitized_block["content"] = INTERNAL_TOOL_RESULT_PLACEHOLDER
                blocks.append(sanitized_block)
                continue
            blocks.append(dict(block))
        sanitized.append({**message, "content": blocks})

    return sanitized, contexts


def append_internal_tool_context(system_prompt: str | None, contexts: list[str]) -> str | None:
    if not contexts:
        return system_prompt
    internal_section = "\n\n".join(
        [
            "<internal_tool_contexts>",
            _INTERNAL_TOOL_CONTEXT_PREAMBLE,
            "",
            *contexts,
            "</internal_tool_contexts>",
        ]
    )
    if system_prompt:
        return f"{system_prompt}\n\n{internal_section}"
    return internal_section
