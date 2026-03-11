from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import structlog

from hermit.core.tools import ToolRegistry, serialize_tool_result
from hermit.provider.contracts import Provider, ProviderRequest, UsageMetrics
from hermit.provider.messages import (
    block_value,
    extract_text,
    extract_thinking,
    normalize_block,
    normalize_messages,
)

log = structlog.get_logger()

StreamCallback = Callable[[str, str], None]
ToolCallback = Callable[[str, Dict[str, Any], Any], None]
ToolStartCallback = Callable[[str, Dict[str, Any]], None]

_TOOL_RESULT_BLOCK_TYPES = {"text", "image"}


def truncate_middle_text(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 32:
        return text[:limit]
    head = max(1, limit // 2 - 8)
    tail = max(1, limit - head - len("\n...\n"))
    return f"{text[:head]}\n...\n{text[-tail:]}"


def _tool_result_json_text(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True)
    return truncate_middle_text(text, limit)


def _is_tool_result_block(value: Any) -> bool:
    return isinstance(value, dict) and value.get("type") in _TOOL_RESULT_BLOCK_TYPES


def format_tool_result_content(value: Any, limit: int) -> Any:
    serialized = serialize_tool_result(value)
    if isinstance(serialized, str):
        return truncate_middle_text(serialized, limit)
    if _is_tool_result_block(serialized):
        return [serialized]
    if isinstance(serialized, dict):
        return _tool_result_json_text(serialized, limit)
    if isinstance(serialized, list):
        if all(_is_tool_result_block(item) for item in serialized):
            return serialized
        return _tool_result_json_text(serialized, limit)
    return serialized


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


class AgentRuntime:
    def __init__(
        self,
        provider: Provider,
        registry: ToolRegistry,
        model: str,
        max_tokens: int = 2048,
        max_turns: int = 10,
        tool_output_limit: int = 4000,
        thinking_budget: int = 0,
        system_prompt: Optional[str] = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.model = model
        self.max_tokens = max_tokens
        self.max_turns = max_turns
        self.tool_output_limit = tool_output_limit
        self.thinking_budget = thinking_budget
        self.system_prompt = system_prompt

    def clone(
        self,
        *,
        registry: Optional[ToolRegistry] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_turns: Optional[int] = None,
    ) -> "AgentRuntime":
        return AgentRuntime(
            provider=self.provider.clone(
                model=model or self.model,
                system_prompt=self.system_prompt if system_prompt is None else system_prompt,
            ),
            registry=registry or self.registry,
            model=model or self.model,
            max_tokens=self.max_tokens,
            max_turns=max_turns or self.max_turns,
            tool_output_limit=self.tool_output_limit,
            thinking_budget=self.thinking_budget,
            system_prompt=self.system_prompt if system_prompt is None else system_prompt,
        )

    def _usage_to_result(self, usage: UsageMetrics, **kwargs: Any) -> AgentResult:
        return AgentResult(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            **kwargs,
        )

    def _request(
        self,
        messages: List[Dict[str, Any]],
        *,
        disable_tools: bool,
        readonly_only: bool,
        stream: bool,
    ) -> ProviderRequest:
        tools = [] if disable_tools else self.registry.list_tools(readonly_only=readonly_only)
        thinking_budget = self.thinking_budget if self.provider.features.supports_thinking else 0
        if self.thinking_budget > 0 and thinking_budget == 0:
            log.debug("provider_ignores_thinking_budget", provider=self.provider.name)
        return ProviderRequest(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=normalize_messages(messages),
            system_prompt=self.system_prompt,
            tools=tools,
            thinking_budget=thinking_budget,
            stream=stream,
        )

    def run(
        self,
        prompt: str,
        message_history: Optional[List[Dict[str, Any]]] = None,
        on_tool_call: Optional[ToolCallback] = None,
        on_tool_start: Optional[ToolStartCallback] = None,
        disable_tools: bool = False,
        readonly_only: bool = False,
    ) -> AgentResult:
        messages: List[Dict[str, Any]] = normalize_messages(message_history or [])
        messages.append({"role": "user", "content": prompt})
        tool_calls = 0
        usage = UsageMetrics()

        for turn in range(1, self.max_turns + 1):
            try:
                response = self.provider.generate(
                    self._request(
                        messages,
                        disable_tools=disable_tools,
                        readonly_only=readonly_only,
                        stream=False,
                    )
                )
            except Exception as exc:
                log.error("provider_call_error", provider=self.provider.name, turn=turn, error=str(exc))
                return self._usage_to_result(
                    usage,
                    text=f"[API Error] {exc}",
                    turns=turn,
                    tool_calls=tool_calls,
                    messages=messages,
                )

            usage.input_tokens += response.usage.input_tokens
            usage.output_tokens += response.usage.output_tokens
            usage.cache_read_tokens += response.usage.cache_read_tokens
            usage.cache_creation_tokens += response.usage.cache_creation_tokens

            if response.error:
                return self._usage_to_result(
                    usage,
                    text=f"[API Error] {response.error}",
                    turns=turn,
                    tool_calls=tool_calls,
                    messages=messages,
                )

            response_blocks = [normalize_block(block) for block in response.content]
            messages.append({"role": "assistant", "content": response_blocks})

            if response.stop_reason != "tool_use":
                return self._usage_to_result(
                    usage,
                    text=extract_text(response_blocks),
                    turns=turn,
                    tool_calls=tool_calls,
                    thinking=extract_thinking(response_blocks),
                    messages=messages,
                )

            tool_use_blocks = [block for block in response_blocks if block_value(block, "type") == "tool_use"]
            if not tool_use_blocks:
                raise RuntimeError("Provider requested tool_use without tool blocks.")

            tool_result_blocks: List[Dict[str, Any]] = []
            for block in tool_use_blocks:
                tool_name = str(block_value(block, "name"))
                tool_input = dict(block_value(block, "input", {}) or {})
                if on_tool_start:
                    on_tool_start(tool_name, tool_input)
                try:
                    raw_result = self.registry.call(tool_name, tool_input)
                    serialized = format_tool_result_content(raw_result, self.tool_output_limit)
                except KeyError:
                    serialized = f"Error: Unknown tool '{tool_name}'. Available: {list(self.registry._tools.keys())}"
                except Exception as exc:
                    serialized = f"Error executing {tool_name}: {type(exc).__name__}: {exc}"
                if on_tool_call:
                    on_tool_call(tool_name, tool_input, serialized)
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block_value(block, "id"),
                        "content": serialized,
                    }
                )
                tool_calls += 1

            messages.append({"role": "user", "content": tool_result_blocks})

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
            response = self.provider.generate(
                self._request(
                    messages,
                    disable_tools=True,
                    readonly_only=False,
                    stream=False,
                )
            )
            usage.input_tokens += response.usage.input_tokens
            usage.output_tokens += response.usage.output_tokens
            usage.cache_read_tokens += response.usage.cache_read_tokens
            usage.cache_creation_tokens += response.usage.cache_creation_tokens
            response_blocks = [normalize_block(block) for block in response.content]
            messages.append({"role": "assistant", "content": response_blocks})
            return self._usage_to_result(
                usage,
                text=extract_text(response_blocks),
                turns=self.max_turns + 1,
                tool_calls=tool_calls,
                messages=messages,
            )
        except Exception as exc:
            log.error("final_summary_failed", error=str(exc))
            return self._usage_to_result(
                usage,
                text=f"（分析未完成：超过最大轮次 {self.max_turns}，且汇总失败：{exc}）",
                turns=self.max_turns + 1,
                tool_calls=tool_calls,
                messages=messages,
            )

    def run_stream(
        self,
        prompt: str,
        message_history: Optional[List[Dict[str, Any]]] = None,
        on_token: Optional[StreamCallback] = None,
    ) -> AgentResult:
        if on_token is None:
            on_token = lambda kind, text: None  # noqa: E731
        if not self.provider.features.supports_streaming:
            result = self.run(prompt, message_history=message_history)
            if result.thinking:
                on_token("thinking", result.thinking)
            if result.text:
                on_token("text", result.text)
            return result

        messages: List[Dict[str, Any]] = normalize_messages(message_history or [])
        messages.append({"role": "user", "content": prompt})
        tool_calls = 0
        usage = UsageMetrics()

        for turn in range(1, self.max_turns + 1):
            response_blocks: List[Dict[str, Any]] = []
            stop_reason: Optional[str] = None
            try:
                for event in self.provider.stream(
                    self._request(messages, disable_tools=False, readonly_only=False, stream=True)
                ):
                    if event.type in {"text", "thinking"}:
                        on_token(event.type, event.text)
                    elif event.type == "block_end" and event.block is not None:
                        response_blocks.append(normalize_block(event.block))
                        on_token("block_end", "")
                    elif event.type == "message_end":
                        stop_reason = event.stop_reason
                        if event.usage:
                            usage.input_tokens += event.usage.input_tokens
                            usage.output_tokens += event.usage.output_tokens
                            usage.cache_read_tokens += event.usage.cache_read_tokens
                            usage.cache_creation_tokens += event.usage.cache_creation_tokens
            except Exception as exc:
                log.error("stream_error", provider=self.provider.name, turn=turn, error=str(exc))
                return self._usage_to_result(
                    usage,
                    text=f"[Stream Error] {exc}",
                    turns=turn,
                    tool_calls=tool_calls,
                    messages=messages,
                )

            messages.append({"role": "assistant", "content": response_blocks})
            if stop_reason != "tool_use":
                return self._usage_to_result(
                    usage,
                    text=extract_text(response_blocks),
                    turns=turn,
                    tool_calls=tool_calls,
                    thinking=extract_thinking(response_blocks),
                    messages=messages,
                )

            tool_use_blocks = [block for block in response_blocks if block_value(block, "type") == "tool_use"]
            if not tool_use_blocks:
                raise RuntimeError("Provider requested tool_use without tool blocks.")

            tool_result_blocks: List[Dict[str, Any]] = []
            for block in tool_use_blocks:
                tool_name = str(block_value(block, "name"))
                tool_input = dict(block_value(block, "input", {}) or {})
                try:
                    raw_result = self.registry.call(tool_name, tool_input)
                    serialized = format_tool_result_content(raw_result, self.tool_output_limit)
                except KeyError:
                    serialized = f"Error: Unknown tool '{tool_name}'. Available: {list(self.registry._tools.keys())}"
                except Exception as exc:
                    serialized = f"Error executing {tool_name}: {type(exc).__name__}: {exc}"
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block_value(block, "id"),
                        "content": serialized,
                    }
                )
                tool_calls += 1
            messages.append({"role": "user", "content": tool_result_blocks})

        messages.append({
            "role": "user",
            "content": (
                "[系统提示] 你已使用了最大允许的工具调用轮次。"
                "请根据目前收集到的信息，直接给出你的最终回答。"
                "不要再调用任何工具。"
            ),
        })
        try:
            response = self.provider.generate(
                self._request(
                    messages,
                    disable_tools=True,
                    readonly_only=False,
                    stream=False,
                )
            )
            usage.input_tokens += response.usage.input_tokens
            usage.output_tokens += response.usage.output_tokens
            usage.cache_read_tokens += response.usage.cache_read_tokens
            usage.cache_creation_tokens += response.usage.cache_creation_tokens
            response_blocks = [normalize_block(block) for block in response.content]
            messages.append({"role": "assistant", "content": response_blocks})
            return self._usage_to_result(
                usage,
                text=extract_text(response_blocks),
                turns=self.max_turns + 1,
                tool_calls=tool_calls,
                messages=messages,
            )
        except Exception as exc:
            log.error("final_summary_failed_stream", error=str(exc))
            return self._usage_to_result(
                usage,
                text=f"（分析未完成：超过最大轮次 {self.max_turns}，且汇总失败：{exc}）",
                turns=self.max_turns + 1,
                tool_calls=tool_calls,
                messages=messages,
            )
