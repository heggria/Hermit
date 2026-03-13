from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from hermit.core.budgets import ExecutionBudget, get_runtime_budget
from hermit.core.tools import ToolSpec
from hermit.provider.contracts import (
    Provider,
    ProviderEvent,
    ProviderFeatures,
    ProviderRequest,
    ProviderResponse,
    UsageMetrics,
)
from hermit.provider.images import prepare_messages_for_provider
from hermit.provider.messages import normalize_block

_CACHE_CONTROL_EPHEMERAL: Dict[str, str] = {"type": "ephemeral"}


def _set_cache_on_message(messages: List[Dict[str, Any]], idx: int) -> None:
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
        new_content = [dict(block) for block in content]
        last_block = new_content[-1]
        if last_block.get("cache_control") != _CACHE_CONTROL_EPHEMERAL:
            new_content[-1] = {**last_block, "cache_control": _CACHE_CONTROL_EPHEMERAL}
        messages[idx] = {**msg, "content": new_content}


def _inject_cache_control(
    messages: List[Dict[str, Any]],
    system_prompt: Optional[str],
) -> tuple[Any, List[Dict[str, Any]]]:
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

    result = list(messages)
    _set_cache_on_message(result, -1)
    if len(result) >= 4:
        _set_cache_on_message(result, 1)
    return system_payload, result


def _cache_tools(tool_schemas: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    if not tool_schemas:
        return tool_schemas
    schemas = list(tool_schemas)
    last = schemas[-1]
    if last.get("cache_control") != _CACHE_CONTROL_EPHEMERAL:
        schemas[-1] = {**last, "cache_control": _CACHE_CONTROL_EPHEMERAL}
    return schemas


def _strip_thinking_blocks(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            cleaned.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            cleaned.append(msg)
            continue
        filtered = [block for block in content if block.get("type") != "thinking"]
        if not filtered:
            filtered = [{"type": "text", "text": ""}]
        cleaned.append({"role": "assistant", "content": filtered})
    return cleaned


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
        system_prompt: Optional[str] = None,
    ) -> None:
        self.client = client
        self.model = model
        self.system_prompt = system_prompt

    def clone(
        self,
        *,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> "ClaudeProvider":
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

    def _payload(self, request: ProviderRequest, *, stream: bool = False) -> Dict[str, Any]:
        prepared_messages = prepare_messages_for_provider(request.messages)
        system_prompt = request.system_prompt if request.system_prompt is not None else self.system_prompt
        system_payload, cached_messages = _inject_cache_control(
            list(prepared_messages[:-1]),
            system_prompt,
        )
        cached_messages = cached_messages + [prepared_messages[-1]] if prepared_messages else []
        if request.thinking_budget > 0:
            cached_messages = _strip_thinking_blocks(cached_messages)
        payload: Dict[str, Any] = {
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
        response = self.client.messages.create(**self._payload(request))
        content = [normalize_block(block) for block in (getattr(response, "content", None) or [])]
        api_error = getattr(response, "error", None)
        if api_error:
            if isinstance(api_error, dict):
                error = str(api_error.get("message", repr(api_error)))
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
        raw_stream = self.client.messages.create(**self._payload(request, stream=True))
        current_block: Optional[Dict[str, Any]] = None
        usage = UsageMetrics()
        stop_reason: Optional[str] = None

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
    system_prompt: Optional[str] = None,
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
