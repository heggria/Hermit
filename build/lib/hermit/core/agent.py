from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

import structlog

from hermit.core.tools import ToolRegistry, serialize_tool_result

log = structlog.get_logger()


def _block_value(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


_ALLOWED_BLOCK_KEYS = {
    "text": {"type", "text"},
    "tool_use": {"type", "id", "name", "input"},
    "tool_result": {"type", "tool_use_id", "content", "is_error"},
    "thinking": {"type", "thinking", "signature"},
}
_FALLBACK_KEYS = {"type", "text", "id", "name", "input", "thinking", "signature", "tool_use_id", "content"}


def _normalize_block(block: Any) -> Dict[str, Any]:
    """Convert SDK content block objects to plain dicts, keeping only API-safe fields."""
    if isinstance(block, dict):
        block_type = block.get("type", "")
        allowed = _ALLOWED_BLOCK_KEYS.get(block_type)
        if allowed:
            return {k: v for k, v in block.items() if k in allowed}
        return block

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
        return raw

    block_type = raw.get("type", "")
    allowed = _ALLOWED_BLOCK_KEYS.get(block_type)
    if allowed:
        return {k: v for k, v in raw.items() if k in allowed}
    return raw


_CACHE_CONTROL_EPHEMERAL: Dict[str, str] = {"type": "ephemeral"}


def _set_cache_on_message(messages: List[Dict[str, Any]], idx: int) -> None:
    """In-place: add cache_control to the last content block of messages[idx]."""
    msg = messages[idx]
    content = msg.get("content")
    if isinstance(content, str):
        messages[idx] = {
            **msg,
            "content": [
                {"type": "text", "text": content, "cache_control": _CACHE_CONTROL_EPHEMERAL}
            ],
        }
    elif isinstance(content, list) and content:
        new_content = [dict(b) for b in content]
        last_block = new_content[-1]
        if last_block.get("cache_control") != _CACHE_CONTROL_EPHEMERAL:
            new_content[-1] = {**last_block, "cache_control": _CACHE_CONTROL_EPHEMERAL}
        messages[idx] = {**msg, "content": new_content}


def _inject_cache_control(
    messages: List[Dict[str, Any]],
    system_prompt: Optional[str],
) -> tuple[Any, List[Dict[str, Any]]]:
    """Add cache_control markers for Anthropic prompt caching.

    Anthropic supports up to 4 cache breakpoints. We use up to 3:
    1. System prompt (static, always cached).
    2. Stable prefix: first assistant message (index 1) — never changes once set,
       so all subsequent turns within the same run get a cache hit on early context.
    3. Rolling tail: last message in history — caches the latest accumulated context.

    This two-breakpoint message strategy ensures:
    - Multi-turn tool-use loops reuse the stable early context on every turn.
    - The growing tail (new tool results) is cached for the next turn.

    Returns (system_payload, messages_with_cache).
    """
    system_payload: Any = system_prompt
    if system_prompt:
        system_payload = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": _CACHE_CONTROL_EPHEMERAL,
            }
        ]

    if not messages:
        return system_payload, messages

    result = list(messages)  # shallow copy of outer list; _set_cache_on_message copies each touched msg

    # Rolling breakpoint: always mark the last message in history.
    _set_cache_on_message(result, -1)

    # Stable breakpoint: first assistant message (index 1), if history is long enough
    # that the two breakpoints don't collapse onto the same message.
    # len >= 4 ensures idx 1 and idx -1 are at least 3 apart (meaningful distinct regions).
    if len(result) >= 4:
        _set_cache_on_message(result, 1)

    return system_payload, result


def _cache_tools(tool_schemas: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Return a copy of tool_schemas with cache_control on the last entry.

    Tool definitions are identical across every turn within a session, so caching
    them avoids re-tokenising ~2000+ tokens on every API call after the first.
    """
    if not tool_schemas:
        return tool_schemas
    schemas = list(tool_schemas)
    last = schemas[-1]
    if last.get("cache_control") != _CACHE_CONTROL_EPHEMERAL:
        schemas[-1] = {**last, "cache_control": _CACHE_CONTROL_EPHEMERAL}
    return schemas


def _strip_thinking_blocks(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove thinking blocks from assistant messages before sending to API.

    Anthropic requires thinking blocks in history to carry valid signatures,
    which proxy gateways often don't support. Stripping them is the safe default.
    """
    cleaned: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            cleaned.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            cleaned.append(msg)
            continue
        filtered = [b for b in content if b.get("type") != "thinking"]
        if not filtered:
            filtered = [{"type": "text", "text": ""}]
        cleaned.append({"role": "assistant", "content": filtered})
    return cleaned


def truncate_middle_text(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 32:
        return text[:limit]
    head = max(1, limit // 2 - 8)
    tail = max(1, limit - head - len("\n...\n"))
    return f"{text[:head]}\n...\n{text[-tail:]}"


def _format_tool_result_content(value: Any, limit: int) -> Any:
    serialized = serialize_tool_result(value)
    if isinstance(serialized, str):
        return truncate_middle_text(serialized, limit)
    if isinstance(serialized, dict):
        return [serialized]
    return serialized


class MessageCreateClient(Protocol):
    def create(self, **kwargs: Any) -> Any: ...
    def stream(self, **kwargs: Any) -> Any: ...


class AnthropicClientProtocol(Protocol):
    @property
    def messages(self) -> MessageCreateClient: ...


StreamCallback = Callable[[str, str], None]
ToolCallback = Callable[[str, Dict[str, Any], Any], None]
ToolStartCallback = Callable[[str, Dict[str, Any]], None]


@dataclass
class AgentResult:
    text: str
    turns: int
    tool_calls: int
    thinking: str = ""
    messages: List[Dict[str, Any]] = None  # type: ignore[assignment]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


class ClaudeAgent:
    """Simple manual Anthropic tool loop with injectable client for tests."""

    def __init__(
        self,
        client: AnthropicClientProtocol,
        registry: ToolRegistry,
        model: str,
        max_tokens: int = 2048,
        max_turns: int = 10,
        tool_output_limit: int = 4000,
        thinking_budget: int = 0,
        system_prompt: Optional[str] = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.model = model
        self.max_tokens = max_tokens
        self.max_turns = max_turns
        self.tool_output_limit = tool_output_limit
        self.thinking_budget = thinking_budget
        self.system_prompt = system_prompt

    def run(
        self,
        prompt: str,
        message_history: Optional[List[Dict[str, Any]]] = None,
        on_tool_call: Optional[ToolCallback] = None,
        on_tool_start: Optional["ToolStartCallback"] = None,
        disable_tools: bool = False,
        readonly_only: bool = False,
    ) -> AgentResult:
        messages: List[Dict[str, Any]] = list(message_history or [])
        messages.append({"role": "user", "content": prompt})
        tool_calls = 0
        _in = _out = _cache_read = _cache_creation = 0

        def _collect_usage(resp: Any) -> None:
            nonlocal _in, _out, _cache_read, _cache_creation
            u = getattr(resp, "usage", None)
            if u:
                _in             += getattr(u, "input_tokens", 0)
                _out            += getattr(u, "output_tokens", 0)
                _cache_read     += getattr(u, "cache_read_input_tokens", 0)
                _cache_creation += getattr(u, "cache_creation_input_tokens", 0)

        for turn in range(1, self.max_turns + 1):
            # Inject cache_control before (optionally) stripping thinking blocks.
            system_payload, cached_messages = _inject_cache_control(
                messages[:-1],  # history only (exclude the just-appended user message)
                self.system_prompt,
            )
            # Re-attach the current user message (no cache marker — it varies each turn).
            cached_messages = cached_messages + [messages[-1]]
            clean_messages = _strip_thinking_blocks(cached_messages) if self.thinking_budget > 0 else cached_messages
            payload: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": clean_messages,
            }
            if self.thinking_budget > 0:
                payload["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self.thinking_budget,
                }
            if not disable_tools:
                tool_schemas = self.registry.anthropic_tools(readonly_only=readonly_only)
                if tool_schemas:
                    payload["tools"] = _cache_tools(tool_schemas)
            if system_payload:
                payload["system"] = system_payload

            try:
                response = self.client.messages.create(**payload)
            except Exception as exc:
                log.error("api_call_error", turn=turn, error=str(exc))
                return AgentResult(
                    text=f"[API Error] {exc}",
                    turns=turn,
                    tool_calls=tool_calls,
                    messages=messages,
                    input_tokens=_in, output_tokens=_out,
                    cache_read_tokens=_cache_read, cache_creation_tokens=_cache_creation,
                )

            _collect_usage(response)
            raw_content = getattr(response, "content", None) or []
            response_blocks = [_normalize_block(b) for b in raw_content]
            stop_reason = getattr(response, "stop_reason", None)

            api_error = getattr(response, "error", None)
            if api_error or (not response_blocks and getattr(response, "type", None) == "error"):
                error_msg = ""
                if isinstance(api_error, dict):
                    error_msg = api_error.get("message", repr(api_error))
                elif api_error:
                    error_msg = str(api_error)
                else:
                    error_msg = "API returned empty response"
                log.error("api_error", turn=turn, error=error_msg)
                return AgentResult(
                    text=f"[API Error] {error_msg}",
                    turns=turn,
                    tool_calls=tool_calls,
                    messages=messages,
                    input_tokens=_in, output_tokens=_out,
                    cache_read_tokens=_cache_read, cache_creation_tokens=_cache_creation,
                )

            log.debug(
                "agent_response",
                turn=turn,
                stop_reason=stop_reason,
                block_count=len(response_blocks),
                block_types=[b.get("type") for b in response_blocks],
            )
            messages.append({"role": "assistant", "content": response_blocks})

            if stop_reason != "tool_use":
                return AgentResult(
                    text=self._extract_text(response_blocks),
                    turns=turn,
                    tool_calls=tool_calls,
                    thinking=self._extract_thinking(response_blocks),
                    messages=messages,
                    input_tokens=_in, output_tokens=_out,
                    cache_read_tokens=_cache_read, cache_creation_tokens=_cache_creation,
                )

            tool_use_blocks = [block for block in response_blocks if _block_value(block, "type") == "tool_use"]
            if not tool_use_blocks:
                raise RuntimeError("Claude requested tool_use without tool blocks.")

            tool_result_blocks: List[Dict[str, Any]] = []
            for block in tool_use_blocks:
                tool_name = str(_block_value(block, "name"))
                tool_input = dict(_block_value(block, "input", {}) or {})
                if on_tool_start:
                    on_tool_start(tool_name, tool_input)
                try:
                    raw_result = self.registry.call(tool_name, tool_input)
                    serialized = _format_tool_result_content(raw_result, self.tool_output_limit)
                except KeyError:
                    serialized = f"Error: Unknown tool '{tool_name}'. Available: {list(self.registry._tools.keys())}"
                except Exception as exc:
                    serialized = f"Error executing {tool_name}: {type(exc).__name__}: {exc}"
                if on_tool_call:
                    on_tool_call(tool_name, tool_input, serialized)
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": _block_value(block, "id"),
                        "content": serialized,
                    }
                )
                tool_calls += 1

            messages.append({"role": "user", "content": tool_result_blocks})

        # Max turns reached — make one final call without tools to get a text summary
        log.warning("max_turns_exceeded", max_turns=self.max_turns, tool_calls=tool_calls)
        messages.append({
            "role": "user",
            "content": (
                "[系统提示] 你已使用了最大允许的工具调用轮次。"
                "请根据目前收集到的信息，直接给出你的最终回答。"
                "不要再调用任何工具。"
            ),
        })
        try:
            system_payload, cached_msgs = _inject_cache_control(messages[:-1], self.system_prompt)
            cached_msgs = cached_msgs + [messages[-1]]
            final_payload: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": _strip_thinking_blocks(cached_msgs) if self.thinking_budget > 0 else cached_msgs,
            }
            if system_payload:
                final_payload["system"] = system_payload
            final_resp = self.client.messages.create(**final_payload)
            _collect_usage(final_resp)
            final_blocks = [_normalize_block(b) for b in (getattr(final_resp, "content", None) or [])]
            messages.append({"role": "assistant", "content": final_blocks})
            return AgentResult(
                text=self._extract_text(final_blocks),
                turns=self.max_turns + 1,
                tool_calls=tool_calls,
                messages=messages,
                input_tokens=_in, output_tokens=_out,
                cache_read_tokens=_cache_read, cache_creation_tokens=_cache_creation,
            )
        except Exception as exc:
            log.error("final_summary_failed", error=str(exc))
            return AgentResult(
                text=f"（分析未完成：超过最大轮次 {self.max_turns}，且汇总失败：{exc}）",
                turns=self.max_turns + 1,
                tool_calls=tool_calls,
                messages=messages,
                input_tokens=_in, output_tokens=_out,
                cache_read_tokens=_cache_read, cache_creation_tokens=_cache_creation,
            )

    def run_stream(
        self,
        prompt: str,
        message_history: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[StreamCallback] = None,
    ) -> AgentResult:
        """Tool loop with streaming text output between tool calls."""

        if on_token is None:
            on_token = lambda kind, text: None  # noqa: E731

        messages: List[Dict[str, Any]] = list(message_history or [])
        messages.append({"role": "user", "content": prompt})
        tool_calls = 0
        _in = _out = _cache_read = _cache_creation = 0

        for turn in range(1, self.max_turns + 1):
            system_payload, cached_messages = _inject_cache_control(
                messages[:-1],
                self.system_prompt,
            )
            cached_messages = cached_messages + [messages[-1]]
            payload: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": cached_messages,
            }
            if self.thinking_budget > 0:
                payload["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self.thinking_budget,
                }
            tool_schemas = self.registry.anthropic_tools()
            if tool_schemas:
                payload["tools"] = _cache_tools(tool_schemas)
            if system_payload:
                payload["system"] = system_payload

            try:
                response_blocks, stop_reason, turn_usage = self._stream_one_turn(payload, on_token)
            except Exception as exc:
                import traceback
                tb = traceback.format_exc()
                log.error("stream_error", turn=turn, error=str(exc), traceback=tb)
                return AgentResult(
                    text=f"[Stream Error] {exc}",
                    turns=turn,
                    tool_calls=tool_calls,
                    messages=messages,
                    input_tokens=_in, output_tokens=_out,
                    cache_read_tokens=_cache_read, cache_creation_tokens=_cache_creation,
                )

            _in             += turn_usage.get("input_tokens", 0)
            _out            += turn_usage.get("output_tokens", 0)
            _cache_read     += turn_usage.get("cache_read_input_tokens", 0)
            _cache_creation += turn_usage.get("cache_creation_input_tokens", 0)

            messages.append({"role": "assistant", "content": response_blocks})

            if stop_reason != "tool_use":
                return AgentResult(
                    text=self._extract_text(response_blocks),
                    turns=turn,
                    tool_calls=tool_calls,
                    thinking=self._extract_thinking(response_blocks),
                    messages=messages,
                    input_tokens=_in, output_tokens=_out,
                    cache_read_tokens=_cache_read, cache_creation_tokens=_cache_creation,
                )

            tool_use_blocks = [b for b in response_blocks if _block_value(b, "type") == "tool_use"]
            if not tool_use_blocks:
                raise RuntimeError("Claude requested tool_use without tool blocks.")

            tool_result_blocks: List[Dict[str, Any]] = []
            for block in tool_use_blocks:
                tool_name = str(_block_value(block, "name"))
                tool_input = dict(_block_value(block, "input", {}) or {})
                try:
                    raw_result = self.registry.call(tool_name, tool_input)
                    serialized = _format_tool_result_content(raw_result, self.tool_output_limit)
                except KeyError:
                    serialized = f"Error: Unknown tool '{tool_name}'. Available: {list(self.registry._tools.keys())}"
                except Exception as exc:
                    serialized = f"Error executing {tool_name}: {type(exc).__name__}: {exc}"
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": _block_value(block, "id"),
                        "content": serialized,
                    }
                )
                tool_calls += 1

            messages.append({"role": "user", "content": tool_result_blocks})

        # Max turns reached — fall back to a non-streaming final summary call
        log.warning("max_turns_exceeded_stream", max_turns=self.max_turns, tool_calls=tool_calls)
        messages.append({
            "role": "user",
            "content": (
                "[系统提示] 你已使用了最大允许的工具调用轮次。"
                "请根据目前收集到的信息，直接给出你的最终回答。"
                "不要再调用任何工具。"
            ),
        })
        try:
            system_payload_s, cached_msgs_s = _inject_cache_control(messages[:-1], self.system_prompt)
            cached_msgs_s = cached_msgs_s + [messages[-1]]
            final_payload: Dict[str, Any] = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": _strip_thinking_blocks(cached_msgs_s) if self.thinking_budget > 0 else cached_msgs_s,
            }
            if system_payload_s:
                final_payload["system"] = system_payload_s
            final_resp = self.client.messages.create(**final_payload)
            u = getattr(final_resp, "usage", None)
            if u:
                _in             += getattr(u, "input_tokens", 0)
                _out            += getattr(u, "output_tokens", 0)
                _cache_read     += getattr(u, "cache_read_input_tokens", 0)
                _cache_creation += getattr(u, "cache_creation_input_tokens", 0)
            final_blocks = [_normalize_block(b) for b in (getattr(final_resp, "content", None) or [])]
            messages.append({"role": "assistant", "content": final_blocks})
            return AgentResult(
                text=self._extract_text(final_blocks),
                turns=self.max_turns + 1,
                tool_calls=tool_calls,
                messages=messages,
                input_tokens=_in, output_tokens=_out,
                cache_read_tokens=_cache_read, cache_creation_tokens=_cache_creation,
            )
        except Exception as exc:
            log.error("final_summary_failed_stream", error=str(exc))
            return AgentResult(
                text=f"（分析未完成：超过最大轮次 {self.max_turns}，且汇总失败：{exc}）",
                turns=self.max_turns + 1,
                tool_calls=tool_calls,
                messages=messages,
                input_tokens=_in, output_tokens=_out,
                cache_read_tokens=_cache_read, cache_creation_tokens=_cache_creation,
            )

    def _stream_one_turn(
        self, payload: Dict[str, Any], on_token: StreamCallback,
    ) -> tuple:
        """Stream one API turn. Returns (blocks, stop_reason, usage_dict)."""
        payload["stream"] = True
        raw_stream = self.client.messages.create(**payload)

        blocks: List[Dict[str, Any]] = []
        current_block: Optional[Dict[str, Any]] = None
        stop_reason: Optional[str] = None
        usage: Dict[str, int] = {}

        for event in raw_stream:
            event_type = getattr(event, "type", "")

            if event_type == "message_start":
                msg = getattr(event, "message", None)
                if msg:
                    stop_reason = getattr(msg, "stop_reason", None)
                    u = getattr(msg, "usage", None)
                    if u:
                        usage["input_tokens"] = getattr(u, "input_tokens", 0)
                        usage["cache_read_input_tokens"] = getattr(u, "cache_read_input_tokens", 0)
                        usage["cache_creation_input_tokens"] = getattr(u, "cache_creation_input_tokens", 0)

            elif event_type == "content_block_start":
                cb = getattr(event, "content_block", None)
                if cb:
                    current_block = _normalize_block(cb)
                else:
                    current_block = {"type": "text", "text": ""}

            elif event_type == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta and current_block:
                    delta_type = getattr(delta, "type", "")
                    if delta_type == "text_delta":
                        chunk = getattr(delta, "text", "")
                        current_block.setdefault("text", "")
                        current_block["text"] += chunk
                        on_token("text", chunk)
                    elif delta_type == "thinking_delta":
                        chunk = getattr(delta, "thinking", "")
                        current_block.setdefault("thinking", "")
                        current_block["thinking"] += chunk
                        on_token("thinking", chunk)
                    elif delta_type == "input_json_delta":
                        partial = getattr(delta, "partial_json", "")
                        current_block.setdefault("_partial_json", "")
                        current_block["_partial_json"] += partial
                    elif delta_type == "signature_delta":
                        sig = getattr(delta, "signature", "")
                        current_block["signature"] = current_block.get("signature", "") + sig

            elif event_type == "content_block_stop":
                if current_block:
                    if "_partial_json" in current_block:
                        import json as _json
                        try:
                            current_block["input"] = _json.loads(current_block.pop("_partial_json"))
                        except Exception:
                            current_block.pop("_partial_json", None)
                    blocks.append(current_block)
                    on_token("block_end", "")
                    current_block = None

            elif event_type == "message_delta":
                delta = getattr(event, "delta", None)
                if delta:
                    sr = getattr(delta, "stop_reason", None)
                    if sr:
                        stop_reason = sr
                    u = getattr(event, "usage", None)
                    if u:
                        usage["output_tokens"] = getattr(u, "output_tokens", 0)

        return blocks, stop_reason, usage

    @staticmethod
    def _extract_text(blocks: list[Any]) -> str:
        text_parts: list[str] = []
        for block in blocks:
            if _block_value(block, "type") == "text":
                text = _block_value(block, "text", "")
                if text:
                    text_parts.append(str(text))
        return "\n".join(text_parts).strip()

    @staticmethod
    def _extract_thinking(blocks: list[Any]) -> str:
        parts: list[str] = []
        for block in blocks:
            if _block_value(block, "type") == "thinking":
                text = _block_value(block, "thinking", "")
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
