"""Parse and normalize incoming Feishu event payloads."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, cast

from hermit.infra.system.i18n import tr


@dataclass
class FeishuMessage:
    chat_id: str
    message_id: str
    sender_id: str
    text: str
    message_type: str
    chat_type: str  # "p2p" or "group"
    image_keys: list[str]
    reply_to_message_id: str = ""
    quoted_message_id: str = ""


_AT_PATTERN = re.compile(r"@_user_\d+\s*")


def _collect_image_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        image_key = d.get("image_key")
        if image_key:
            keys.append(str(image_key))
        for child in d.values():
            keys.extend(_collect_image_keys(child))
    elif isinstance(value, list):
        lst = cast(list[Any], value)
        for item in lst:
            keys.extend(_collect_image_keys(item))
    return keys


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _extract_post_text(parsed: dict[str, Any]) -> str:
    payload: dict[str, Any] = parsed
    for value in parsed.values():
        if isinstance(value, dict) and ("content" in value or "title" in value):
            payload = cast(dict[str, Any], value)
            break

    segments: list[str] = []
    title: Any = payload.get("title")
    if isinstance(title, str) and title.strip():
        segments.append(title.strip())

    content: Any = payload.get("content", [])
    if isinstance(content, list):
        for paragraph in cast(list[Any], content):
            if not isinstance(paragraph, list):
                continue
            pieces: list[str] = []
            for node in cast(list[Any], paragraph):
                if not isinstance(node, dict):
                    continue
                node_d = cast(dict[str, Any], node)
                tag = str(node_d.get("tag", ""))
                if tag in {"text", "a", "md"}:
                    text: Any = node_d.get("text")
                    if isinstance(text, str) and text.strip():
                        pieces.append(text.strip())
                elif tag == "at":
                    user_name: Any = node_d.get("user_name") or node_d.get("name")
                    user_id: Any = node_d.get("user_id")
                    if isinstance(user_name, str) and user_name.strip():
                        pieces.append(f"@{user_name.strip()}")
                    elif user_id == "all":
                        pieces.append(tr("feishu.normalize.mention_all"))
            paragraph_text = "".join(pieces).strip()
            if paragraph_text:
                segments.append(paragraph_text)

    return "\n".join(_dedupe_preserve_order(segments))


def _extract_text(parsed: Any, message_type: str, raw_content: str) -> tuple[str, list[str]]:
    image_keys = _collect_image_keys(parsed)

    if message_type == "image":
        return "", image_keys

    if isinstance(parsed, dict):
        parsed_d = cast(dict[str, Any], parsed)
        text: Any = parsed_d.get("text")
        if isinstance(text, str):
            return text, image_keys

        if message_type == "post":
            post_text = _extract_post_text(parsed_d)
            if post_text:
                return post_text, image_keys

        return "", image_keys

    if isinstance(parsed, str):
        return parsed, image_keys

    return raw_content, image_keys


def normalize_event(event: dict[str, Any]) -> FeishuMessage:
    """Extract a FeishuMessage from a lark-oapi event dict.

    Handles both raw dict (test) and SDK P2ImMessageReceiveV1 shapes.
    """
    msg: dict[str, Any] = cast(dict[str, Any], event.get("message", {}))
    sender: dict[str, Any] = cast(dict[str, Any], event.get("sender", {}))

    raw_content: Any = msg.get("content", "")
    message_type = str(msg.get("message_type", "text"))
    image_keys: list[str] = []
    text = ""
    if isinstance(raw_content, str):
        try:
            parsed = json.loads(raw_content)
            text, image_keys = _extract_text(parsed, message_type, raw_content)
        except (json.JSONDecodeError, AttributeError):
            text = raw_content
    else:
        text = str(raw_content)

    chat_type = str(msg.get("chat_type", "p2p"))
    if chat_type == "group":
        text = _AT_PATTERN.sub("", text).strip()

    sender_id_dict: dict[str, Any] = cast(dict[str, Any], sender.get("sender_id", {}))
    return FeishuMessage(
        chat_id=str(msg.get("chat_id", "")),
        message_id=str(msg.get("message_id", "")),
        sender_id=str(sender_id_dict.get("open_id", "")),
        text=text,
        message_type=message_type,
        chat_type=chat_type,
        image_keys=image_keys,
        reply_to_message_id=str(
            msg.get("reply_to_message_id")
            or msg.get("parent_id")
            or msg.get("reply_in_thread_from_message_id")
            or ""
        ),
        quoted_message_id=str(
            msg.get("quoted_message_id") or msg.get("root_id") or msg.get("upper_message_id") or ""
        ),
    )
