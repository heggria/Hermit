"""Compact command plugin: compress session context via LLM summarization.

Also provides auto-compact: when the last API call's input_tokens exceeds
AUTO_COMPACT_THRESHOLD, the session history is compacted automatically before
the next user message is processed.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from hermit.i18n import resolve_locale, tr
from hermit.plugin.base import CommandSpec, HookEvent

log = logging.getLogger(__name__)

AUTO_COMPACT_THRESHOLD = 150_000  # tokens

_state: dict[str, Any] = {
    "last_input_tokens": 0,
}


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------


def _locale_for_runner(runner: Any = None) -> str:
    settings = getattr(getattr(runner, "pm", None), "settings", None)
    return resolve_locale(getattr(settings, "locale", None))


def _t(
    message_key: str,
    *,
    runner: Any = None,
    locale: str | None = None,
    default: str | None = None,
    **kwargs: object,
) -> str:
    return tr(message_key, locale=locale or _locale_for_runner(runner), default=default, **kwargs)


def _serialize_messages(messages: list, *, locale: str | None = None) -> str:
    """Convert message history into readable text for LLM summarization."""
    resolved_locale = resolve_locale(locale)
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(f"[{role}]: {content}")
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(f"[{role}]: {block.get('text', '')}")
                elif btype == "thinking":
                    pass  # skip — too noisy for summarization
                elif btype == "tool_use":
                    inp = block.get("input", {})
                    inp_str = json.dumps(inp, ensure_ascii=False)[:100]
                    parts.append(
                        f"[{role}]: "
                        + _t(
                            "kernel.compact.serialize.tool_use",
                            locale=resolved_locale,
                            name=block.get("name", ""),
                            input=inp_str,
                        )
                    )
                elif btype == "tool_result":
                    result_text = str(block.get("content", ""))[:200]
                    parts.append(f"[tool_result]: {result_text}")
    return "\n\n".join(parts)


def _sanitize_messages(messages: list) -> list:
    """Return a cleaned copy of messages safe for Anthropic tool-use sequencing."""
    cleaned: list[dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        copied = dict(msg)
        content = copied.get("content")
        if isinstance(content, list):
            copied["content"] = [
                dict(block) if isinstance(block, dict) else block
                for block in content
            ]
        cleaned.append(copied)

    def _content_to_blocks(content: Any) -> list[dict[str, Any]]:
        if isinstance(content, list):
            return content
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        return []

    tail = cleaned[-1] if cleaned else None
    if tail and tail.get("role") == "assistant" and isinstance(tail.get("content"), list):
        tail_blocks = [block for block in tail["content"] if isinstance(block, dict)]
        has_tool_use = any(block.get("type") == "tool_use" for block in tail_blocks)
        has_text = any(block.get("type") == "text" and block.get("text") for block in tail_blocks)
        if has_tool_use and not has_text:
            orphan_ids = [block.get("id") for block in tail_blocks if block.get("type") == "tool_use" and block.get("id")]
            log.warning("compact_sanitize_removed_trailing_tool_use", extra={"tool_use_ids": orphan_ids})
            cleaned.pop()

    for index, msg in enumerate(cleaned):
        if msg.get("role") != "assistant":
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        tool_use_ids = [
            block.get("id")
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
        ]
        if not tool_use_ids:
            continue

        next_msg = cleaned[index + 1] if index + 1 < len(cleaned) else None
        next_is_user = isinstance(next_msg, dict) and next_msg.get("role") == "user"
        next_blocks = _content_to_blocks(next_msg.get("content")) if next_is_user else []
        result_ids = {
            block.get("tool_use_id")
            for block in next_blocks
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id")
        }
        orphan_ids = [tool_use_id for tool_use_id in tool_use_ids if tool_use_id not in result_ids]
        if not orphan_ids:
            continue

        log.warning("compact_sanitize_orphaned_tool_use", extra={"tool_use_ids": orphan_ids, "message_index": index})

        synthetic_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": "[compact: result unavailable]",
                "is_error": True,
            }
            for tool_use_id in orphan_ids
        ]

        if next_is_user and next_msg is not None:
            next_msg["content"] = next_blocks + synthetic_blocks
        else:
            cleaned.insert(index + 1, {"role": "user", "content": synthetic_blocks})

    return cleaned


def _do_compact(runner: Any, session: Any) -> tuple[bool, str]:
    """Run LLM summarization and replace session.messages.

    Returns (success, message).
    """
    locale = _locale_for_runner(runner)
    if not session.messages:
        return False, _t("kernel.compact.empty", locale=locale)

    sanitized_messages = _sanitize_messages(session.messages)
    original_count = len(sanitized_messages)
    history_text = _serialize_messages(sanitized_messages, locale=locale)

    try:
        response = runner.agent.client.messages.create(
            model=runner.agent.model,
            max_tokens=2048,
            system=_t("kernel.compact.system_prompt", locale=locale),
            messages=[{"role": "user", "content": history_text}],
        )
        raw = getattr(response, "content", None) or []
        summary = ""
        for block in raw:
            text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
            if text:
                summary += text
        summary = summary.strip()
        if not summary:
            return False, _t("kernel.compact.summary_missing", locale=locale)
    except Exception as exc:
        return False, _t("kernel.compact.failed", locale=locale, error=exc)

    session.messages = [
        {"role": "user", "content": f"<compacted_context>\n{summary}\n</compacted_context>"},
        {"role": "assistant", "content": _t("kernel.compact.assistant_ack", locale=locale)},
    ]
    session.total_input_tokens = 0
    session.total_output_tokens = 0
    session.total_cache_read_tokens = 0
    session.total_cache_creation_tokens = 0
    runner.session_manager.save(session)
    _state["last_input_tokens"] = 0

    preview = summary[:200] + ("…" if len(summary) > 200 else "")
    return True, _t(
        "kernel.compact.success",
        locale=locale,
        original_count=original_count,
        preview=preview,
    )


# ------------------------------------------------------------------
# Command handler
# ------------------------------------------------------------------

def _cmd_compact(runner: Any, session_id: str, _text: str) -> Any:
    from hermit.core.runner import DispatchResult

    session = runner.session_manager.get_or_create(session_id)
    success, msg = _do_compact(runner, session)
    return DispatchResult(msg, is_command=True)


# ------------------------------------------------------------------
# Hooks
# ------------------------------------------------------------------

def _post_run_hook(result: Any, **kwargs: Any) -> None:
    """Track last turn's input_tokens — this reflects the actual context size."""
    tokens = getattr(result, "input_tokens", 0)
    if tokens:
        _state["last_input_tokens"] = tokens


def _pre_run_hook(prompt: str, session: Any = None, session_id: str = "",
                  runner: Any = None, **kwargs: Any) -> str | dict[str, Any]:
    """Auto-compact when last input tokens exceeded the threshold."""
    if runner is None or session is None:
        return prompt
    if _state["last_input_tokens"] < AUTO_COMPACT_THRESHOLD:
        return prompt

    log.info(
        "auto_compact_triggered",
        extra={"last_input_tokens": _state["last_input_tokens"], "threshold": AUTO_COMPACT_THRESHOLD},
    )
    success, summary_msg = _do_compact(runner, session)
    if success:
        # Notify via a system note prepended to the prompt so the user knows it happened
        notice = _t(
            "kernel.compact.auto_notice",
            runner=runner,
            last_input_tokens=_state.get("last_input_tokens", 0),
            threshold=AUTO_COMPACT_THRESHOLD,
        )
        # _do_compact resets last_input_tokens, so we read before reset above; just notify.
        return {"prompt": notice + prompt}
    return prompt


# ------------------------------------------------------------------
# Plugin registration
# ------------------------------------------------------------------

def register(ctx: Any) -> None:
    ctx.add_hook(HookEvent.POST_RUN, _post_run_hook, priority=10)
    ctx.add_hook(HookEvent.PRE_RUN, _pre_run_hook, priority=90)
    ctx.add_command(CommandSpec(
        name="/compact",
        help_text="kernel.compact.command.help",
        handler=_cmd_compact,
    ))
