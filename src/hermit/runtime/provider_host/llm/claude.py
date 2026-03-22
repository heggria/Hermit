from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any, cast

from hermit.runtime.capability.registry.tools import ToolSpec
from hermit.runtime.control.lifecycle.budgets import ExecutionBudget, get_runtime_budget
from hermit.runtime.provider_host.shared.contracts import (
    Provider,
    ProviderEvent,
    ProviderFeatures,
    ProviderRequest,
    ProviderResponse,
    UsageMetrics,
)
from hermit.runtime.provider_host.shared.images import prepare_messages_for_provider
from hermit.runtime.provider_host.shared.messages import (
    normalize_block,
    split_internal_tool_context,
)

_CACHE_CONTROL_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


def _set_cache_on_message(messages: list[dict[str, Any]], idx: int) -> None:
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
        new_content: list[dict[str, Any]] = [
            dict(cast(dict[str, Any], block)) for block in cast(list[Any], content)
        ]
        last_block: dict[str, Any] = new_content[-1]
        if last_block.get("cache_control") != _CACHE_CONTROL_EPHEMERAL:
            new_content[-1] = {**last_block, "cache_control": _CACHE_CONTROL_EPHEMERAL}
        messages[idx] = {**msg, "content": new_content}


def _inject_cache_control(
    messages: list[dict[str, Any]],
    system_prompt: str | None,
    *,
    internal_contexts: list[str] | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    system_payload: Any = system_prompt
    if system_prompt:
        # Split system prompt into stable base (cacheable) and dynamic contexts (non-cached).
        # This allows Anthropic to cache the stable prefix across calls even when
        # internal tool contexts change.
        blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": _CACHE_CONTROL_EPHEMERAL,
            }
        ]
        if internal_contexts:
            from hermit.runtime.provider_host.shared.messages import (
                _INTERNAL_TOOL_CONTEXT_PREAMBLE,
            )

            internal_section = "\n\n".join(
                [
                    "<internal_tool_contexts>",
                    _INTERNAL_TOOL_CONTEXT_PREAMBLE,
                    "",
                    *internal_contexts,
                    "</internal_tool_contexts>",
                ]
            )
            blocks.append({"type": "text", "text": internal_section})
        system_payload = blocks

    if not messages:
        return system_payload, messages

    result = list(messages)
    _set_cache_on_message(result, -1)
    if len(result) >= 4:
        _set_cache_on_message(result, 1)
    return system_payload, result


def _cache_tools(tool_schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not tool_schemas:
        return tool_schemas
    schemas = list(tool_schemas)
    last = schemas[-1]
    if last.get("cache_control") != _CACHE_CONTROL_EPHEMERAL:
        schemas[-1] = {**last, "cache_control": _CACHE_CONTROL_EPHEMERAL}
    return schemas


def _strip_thinking_blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            cleaned.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            cleaned.append(msg)
            continue
        filtered: list[dict[str, Any]] = [
            cast(dict[str, Any], block)
            for block in cast(list[Any], content)
            if cast(dict[str, Any], block).get("type") != "thinking"
        ]
        if not filtered:
            filtered = [{"type": "text", "text": ""}]
        cleaned.append({"role": "assistant", "content": filtered})
    return cleaned


_CONTENT_FILTER_MAX_RETRIES: int = 2
_CONTENT_FILTER_RETRY_DELAY: float = 1.0


def _is_content_filter_error(exc: BaseException) -> bool:
    """Return True if *exc* looks like a content-filter (sensitive_words_detected) error."""
    return "sensitive_words_detected" in str(exc)


def _nudge_payload_for_retry(payload: dict, attempt: int) -> dict:
    """Return a copy of *payload* with a metadata hint to vary the request hash on retry."""
    return {**payload, "metadata": {"retry_hint": f"attempt_{attempt}"}}


class ClaudeProvider(Provider):
    name = "claude"
    features = ProviderFeatures(
        supports_streaming=True,
        supports_thinking=True,
        supports_images=True,
        supports_prompt_cache=True,
        supports_tool_calling=True,
        supports_structured_output=False,
    )

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        system_prompt: str | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.system_prompt = system_prompt

    def clone(
        self,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
    ) -> ClaudeProvider:
        return ClaudeProvider(
            self.client,
            model=model or self.model,
            system_prompt=self.system_prompt if system_prompt is None else system_prompt,
        )

    def _tool_schema(self, tool: ToolSpec) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }

    def _usage(self, response: Any) -> UsageMetrics:
        usage = getattr(response, "usage", None)
        if usage is None:
            return UsageMetrics()
        return UsageMetrics(
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0),
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0),
        )

    def _payload(self, request: ProviderRequest, *, stream: bool = False) -> dict[str, Any]:
        prepared_messages = prepare_messages_for_provider(request.messages)
        prepared_messages, internal_contexts = split_internal_tool_context(prepared_messages)
        system_prompt = (
            request.system_prompt if request.system_prompt is not None else self.system_prompt
        )
        system_payload, cached_messages = _inject_cache_control(
            list(prepared_messages[:-1]),
            system_prompt,
            internal_contexts=internal_contexts,
        )
        cached_messages = cached_messages + [prepared_messages[-1]] if prepared_messages else []
        if request.thinking_budget > 0:
            cached_messages = _strip_thinking_blocks(cached_messages)
        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "max_tokens": request.max_tokens,
            "messages": cached_messages,
        }
        if request.thinking_budget > 0:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": request.thinking_budget,
            }
        if request.tools:
            payload["tools"] = _cache_tools([self._tool_schema(tool) for tool in request.tools])
        if system_payload:
            payload["system"] = system_payload
        if stream:
            payload["stream"] = True
        return payload

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        payload = self._payload(request)
        last_exc: BaseException | None = None
        for _attempt in range(_CONTENT_FILTER_MAX_RETRIES + 1):
            try:
                if _attempt > 0:
                    time.sleep(_CONTENT_FILTER_RETRY_DELAY)
                    response = self.client.messages.create(
                        **_nudge_payload_for_retry(payload, _attempt)
                    )
                else:
                    response = self.client.messages.create(**payload)
                break
            except Exception as exc:
                if _is_content_filter_error(exc) and _attempt < _CONTENT_FILTER_MAX_RETRIES:
                    last_exc = exc
                    continue
                raise
        else:
            raise last_exc  # type: ignore[misc]
        raw_content: list[Any] = list(getattr(response, "content", None) or [])
        content = [normalize_block(block) for block in raw_content]
        api_error = getattr(response, "error", None)
        if api_error:
            if isinstance(api_error, dict):
                typed_error = cast(dict[str, Any], api_error)
                error = str(typed_error.get("message", repr(typed_error)))
            else:
                error = str(api_error)
        else:
            error = None
        return ProviderResponse(
            content=content,
            stop_reason=getattr(response, "stop_reason", None),
            error=error,
            usage=self._usage(response),
        )

    def stream(self, request: ProviderRequest) -> Iterable[ProviderEvent]:
        payload = self._payload(request, stream=True)
        last_exc: BaseException | None = None
        raw_stream = None
        for _attempt in range(_CONTENT_FILTER_MAX_RETRIES + 1):
            try:
                if _attempt > 0:
                    time.sleep(_CONTENT_FILTER_RETRY_DELAY)
                    raw_stream = self.client.messages.create(
                        **_nudge_payload_for_retry(payload, _attempt)
                    )
                else:
                    raw_stream = self.client.messages.create(**payload)
                break
            except Exception as exc:
                if _is_content_filter_error(exc) and _attempt < _CONTENT_FILTER_MAX_RETRIES:
                    last_exc = exc
                    continue
                raise
        else:
            raise last_exc  # type: ignore[misc]
        current_block: dict[str, Any] | None = None
        usage = UsageMetrics()
        stop_reason: str | None = None

        for event in raw_stream:
            event_type = getattr(event, "type", "")
            if event_type == "message_start":
                msg = getattr(event, "message", None)
                if msg:
                    stop_reason = getattr(msg, "stop_reason", None)
                    usage = self._usage(msg)
            elif event_type == "content_block_start":
                cb = getattr(event, "content_block", None)
                current_block = normalize_block(cb) if cb else {"type": "text", "text": ""}
            elif event_type == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta and current_block:
                    delta_type = getattr(delta, "type", "")
                    if delta_type == "text_delta":
                        chunk = getattr(delta, "text", "")
                        current_block.setdefault("text", "")
                        current_block["text"] += chunk
                        yield ProviderEvent(type="text", text=chunk)
                    elif delta_type == "thinking_delta":
                        chunk = getattr(delta, "thinking", "")
                        current_block.setdefault("thinking", "")
                        current_block["thinking"] += chunk
                        yield ProviderEvent(type="thinking", text=chunk)
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
                        import json

                        try:
                            current_block["input"] = json.loads(current_block.pop("_partial_json"))
                        except Exception:
                            current_block.pop("_partial_json", None)
                    block = dict(current_block)
                    current_block = None
                    yield ProviderEvent(type="block_end", block=block)
            elif event_type == "message_delta":
                delta = getattr(event, "delta", None)
                if delta:
                    sr = getattr(delta, "stop_reason", None)
                    if sr:
                        stop_reason = sr
                event_usage = getattr(event, "usage", None)
                if event_usage:
                    usage.output_tokens = getattr(event_usage, "output_tokens", 0)

        yield ProviderEvent(type="message_end", stop_reason=stop_reason, usage=usage)


def build_claude_provider(
    settings: Any,
    *,
    model: str,
    system_prompt: str | None = None,
) -> ClaudeProvider:
    from anthropic import Anthropic

    kwargs: dict[str, Any] = {}
    if settings.claude_api_key:
        kwargs["api_key"] = settings.claude_api_key
    if settings.claude_auth_token:
        kwargs["auth_token"] = settings.claude_auth_token
    if settings.claude_base_url:
        kwargs["base_url"] = settings.claude_base_url
    if settings.parsed_claude_headers:
        kwargs["default_headers"] = settings.parsed_claude_headers
    if hasattr(settings, "execution_budget"):
        budget = settings.execution_budget()
    elif hasattr(settings, "command_timeout_seconds"):
        legacy = max(float(getattr(settings, "command_timeout_seconds", 30) or 30), 1.0)
        budget = ExecutionBudget(
            ingress_ack_deadline=5.0,
            provider_connect_timeout=legacy,
            provider_read_timeout=600.0,
            provider_stream_idle_timeout=600.0,
            tool_soft_deadline=legacy,
            tool_hard_deadline=max(legacy, 600.0),
            observation_window=600.0,
            observation_poll_interval=5.0,
        )
    else:
        budget = get_runtime_budget()
    import httpx

    kwargs["timeout"] = httpx.Timeout(
        budget.provider_read_timeout,
        connect=budget.provider_connect_timeout,
        read=budget.provider_stream_idle_timeout,
        write=budget.provider_read_timeout,
        pool=budget.provider_read_timeout,
    )
    client = Anthropic(**kwargs)
    return ClaudeProvider(client, model=model, system_prompt=system_prompt)
