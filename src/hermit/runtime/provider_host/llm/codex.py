from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from hermit.runtime.capability.registry.tools import ToolSpec, serialize_tool_result
from hermit.runtime.control.lifecycle.budgets import get_runtime_budget
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
    append_internal_tool_context,
    split_internal_tool_context,
)

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_CODEX_OAUTH_BASE_URL = "https://chatgpt.com/backend-api/codex/responses"
_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
_USER_AGENT = "Hermit/1.0"


def _responses_url(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/responses"):
        return root
    if root.endswith("/v1"):
        return f"{root}/responses"
    return f"{root}/v1/responses"


def _stringify_tool_output(content: Any) -> str:
    serialized = serialize_tool_result(content)
    if isinstance(serialized, str):
        return serialized
    return json.dumps(serialized, ensure_ascii=False)


def _tool_result_image_parts(content: Any, *, codex_oauth: bool) -> list[dict[str, Any]]:
    image_part = _image_part_from_block
    if isinstance(content, dict) and str(cast(dict[str, Any], content).get("type", "")) == "image":
        return [image_part(cast(dict[str, Any], content))]
    if not isinstance(content, list):
        return []
    parts: list[dict[str, Any]] = []
    for item in cast(list[Any], content):
        block = cast(dict[str, Any], item) if isinstance(item, dict) else None
        if block is not None and str(block.get("type", "")) == "image":
            parts.append(image_part(block))
    return parts


def _tool_result_output(content: Any, *, codex_oauth: bool) -> str:
    images = _tool_result_image_parts(content, codex_oauth=codex_oauth)
    if not images:
        return _stringify_tool_output(content)
    if isinstance(content, dict) and str(cast(dict[str, Any], content).get("type", "")) == "image":
        return "[tool returned image content]"
    if isinstance(content, list):
        non_image_blocks: list[Any] = [
            item
            for item in cast(list[Any], content)
            if not (
                isinstance(item, dict)
                and str(cast(dict[str, Any], item).get("type", "")) == "image"
            )
        ]
        if non_image_blocks:
            return _stringify_tool_output(non_image_blocks)
    return "[tool returned image content]"


def _tool_result_follow_up_items(
    call_id: str, content: Any, *, codex_oauth: bool
) -> list[dict[str, Any]]:
    images = _tool_result_image_parts(content, codex_oauth=codex_oauth)
    if not images:
        return []
    return [
        {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": f"Tool result for call {call_id} includes image content.",
                },
                *images,
            ],
        }
    ]


def _error_code_message(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    error = payload.get("error")
    if isinstance(error, dict):
        error_dict = cast(dict[str, Any], error)
        code = error_dict.get("code")
        message = error_dict.get("message") or error_dict.get("detail")
        return (
            str(code) if code is not None else None,
            str(message) if message is not None else None,
        )
    response = payload.get("response")
    if isinstance(response, dict):
        response_dict = cast(dict[str, Any], response)
        response_error = response_dict.get("error")
        if isinstance(response_error, dict):
            response_error_dict = cast(dict[str, Any], response_error)
            code = response_error_dict.get("code")
            message = response_error_dict.get("message") or response_error_dict.get("detail")
            return (
                str(code) if code is not None else None,
                str(message) if message is not None else None,
            )
        incomplete = response_dict.get("incomplete_details")
        if isinstance(incomplete, dict):
            incomplete_dict = cast(dict[str, Any], incomplete)
            reason = incomplete_dict.get("reason")
            message = incomplete_dict.get("message") or reason
            return (
                str(reason) if reason is not None else None,
                str(message) if message is not None else None,
            )
    code = payload.get("code")
    message = payload.get("message") or payload.get("detail")
    return (str(code) if code is not None else None, str(message) if message is not None else None)


def _format_stream_error(prefix: str, payload: dict[str, Any]) -> str:
    code, message = _error_code_message(payload)
    if code and message:
        return f"{prefix} {code}: {message}"
    if message:
        return f"{prefix}: {message}"
    return f"{prefix}: {json.dumps(payload, ensure_ascii=False)[:500]}"


def _image_part_from_block(block: dict[str, Any]) -> dict[str, Any]:
    source_raw = block.get("source", {})
    if not isinstance(source_raw, dict):
        raise ValueError("Invalid image block: missing source")
    source = cast(dict[str, Any], source_raw)
    source_type = str(source.get("type", ""))
    if source_type == "url":
        url = str(source.get("url", "")).strip()
        if not url:
            raise ValueError("Invalid image block: empty image URL")
        return {"type": "input_image", "image_url": url}
    if source_type == "base64":
        media_type = str(source.get("media_type", "")).strip() or "application/octet-stream"
        data = str(source.get("data", "")).strip()
        if not data:
            raise ValueError("Invalid image block: empty base64 image data")
        return {"type": "input_image", "image_url": f"data:{media_type};base64,{data}"}
    raise ValueError(f"Unsupported image source type: {source_type or 'unknown'}")


def _message_content_parts(content: Any, *, codex_oauth: bool = False) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "input_text", "text": str(content)}]

    parts: list[dict[str, Any]] = []
    for item in cast(list[Any], content):
        if not isinstance(item, dict):
            continue
        block = cast(dict[str, Any], item)
        block_type = str(block.get("type", ""))
        if block_type == "text":
            text = str(block.get("text", ""))
            if text:
                parts.append({"type": "input_text", "text": text})
        elif block_type == "image":
            parts.append(_image_part_from_block(block))
        elif block_type == "thinking":
            continue
    return parts


def _responses_input(
    messages: list[dict[str, Any]], *, codex_oauth: bool = False
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = message.get("content", "")

        if isinstance(content, list):
            message_parts = _message_content_parts(content, codex_oauth=codex_oauth)
            if message_parts:
                if (
                    codex_oauth
                    and len(message_parts) == 1
                    and message_parts[0].get("type") == "input_text"
                ):
                    items.append(
                        {"type": "message", "role": role, "content": message_parts[0]["text"]}
                    )
                else:
                    items.append({"type": "message", "role": role, "content": message_parts})

            for item in cast(list[Any], content):
                if not isinstance(item, dict):
                    continue
                block = cast(dict[str, Any], item)
                block_type = str(block.get("type", ""))
                if block_type == "tool_use":
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": str(block.get("id", "")),
                            "name": str(block.get("name", "")),
                            "arguments": json.dumps(
                                block.get("input", {}) or {}, ensure_ascii=False
                            ),
                        }
                    )
                elif block_type == "tool_result":
                    call_id = str(block.get("tool_use_id", ""))
                    output = _tool_result_output(block.get("content"), codex_oauth=codex_oauth)
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": output,
                        }
                    )
                    items.extend(
                        _tool_result_follow_up_items(
                            call_id, block.get("content"), codex_oauth=codex_oauth
                        )
                    )
            continue

        items.append(
            {
                "type": "message",
                "role": role,
                "content": content if codex_oauth else _message_content_parts(content),
            }
        )
    return items


def _normalize_openai_schema(schema: Any) -> Any:
    if isinstance(schema, dict):
        schema_dict = cast(dict[str, Any], schema)
        normalized: dict[str, Any] = {
            str(key): _normalize_openai_schema(value) for key, value in schema_dict.items()
        }
        schema_type = normalized.get("type")
        if schema_type == "array" and "items" not in normalized:
            normalized["items"] = {}
        return normalized
    if isinstance(schema, list):
        return [_normalize_openai_schema(item) for item in cast(list[Any], schema)]
    return schema


def _tool_schema(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": _normalize_openai_schema(tool.input_schema),
    }


def _usage(data: dict[str, Any]) -> UsageMetrics:
    usage_raw = data.get("usage", {})
    if not isinstance(usage_raw, dict):
        return UsageMetrics()
    usage = cast(dict[str, Any], usage_raw)
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    extra: dict[str, int] = {
        str(key): int(value)
        for key, value in usage.items()
        if isinstance(value, int) and key not in {"input_tokens", "output_tokens"}
    }
    return UsageMetrics(input_tokens=input_tokens, output_tokens=output_tokens, extra=extra)


def _parse_output(data: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw_item in cast(list[Any], data.get("output", []) or []):
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, Any], raw_item)
        item_type = str(item.get("type", ""))
        if item_type == "message":
            for raw_block in cast(list[Any], item.get("content", []) or []):
                if not isinstance(raw_block, dict):
                    continue
                block = cast(dict[str, Any], raw_block)
                if block.get("type") == "output_text":
                    text = str(block.get("text", ""))
                    if text:
                        blocks.append({"type": "text", "text": text})
        elif item_type == "function_call":
            raw_arguments = item.get("arguments", "{}")
            try:
                payload: dict[str, Any] = (
                    cast(dict[str, Any], json.loads(raw_arguments))
                    if isinstance(raw_arguments, str)
                    else dict(cast(dict[str, Any], raw_arguments or {}))
                )
            except Exception:
                payload = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(item.get("call_id") or item.get("id") or ""),
                    "name": str(item.get("name", "")),
                    "input": payload,
                }
            )
    return blocks


def _json_error_message(body: str) -> str:
    try:
        parsed = json.loads(body)
    except Exception:
        return body[:500]
    if isinstance(parsed, dict):
        parsed_dict = cast(dict[str, Any], parsed)
        error = parsed_dict.get("error")
        if isinstance(error, dict):
            return str(cast(dict[str, Any], error).get("message") or body[:500])
        detail = parsed_dict.get("detail")
        if detail:
            return str(detail)
    return body[:500]


class CodexProvider(Provider):
    name = "codex"
    features = ProviderFeatures(
        supports_streaming=False,
        supports_thinking=False,
        supports_images=True,
        supports_prompt_cache=False,
        supports_tool_calling=True,
        supports_structured_output=False,
    )

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        cwd: Path | None = None,
        system_prompt: str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        default_headers: dict[str, str] | None = None,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAI API key is required for the Codex Responses provider")
        self.api_key = api_key.strip()
        self.model = model
        self.cwd = cwd or Path.cwd()
        self.system_prompt = system_prompt
        self.base_url = base_url or _DEFAULT_BASE_URL
        self.default_headers = default_headers or {}
        budget = get_runtime_budget()
        self.connect_timeout = float(connect_timeout or budget.provider_connect_timeout)
        self.read_timeout = float(read_timeout or budget.provider_read_timeout)

    def clone(self, *, model: str | None = None, system_prompt: str | None = None) -> CodexProvider:
        return CodexProvider(
            api_key=self.api_key,
            model=model or self.model,
            cwd=self.cwd,
            system_prompt=self.system_prompt if system_prompt is None else system_prompt,
            base_url=self.base_url,
            default_headers=dict(self.default_headers),
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
        )

    def _payload(self, request: ProviderRequest) -> dict[str, Any]:
        prepared_messages = prepare_messages_for_provider(request.messages)
        sanitized_messages, internal_contexts = split_internal_tool_context(prepared_messages)
        instructions = (
            request.system_prompt if request.system_prompt is not None else self.system_prompt
        )
        instructions = append_internal_tool_context(instructions, internal_contexts)
        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "input": _responses_input(sanitized_messages),
            "max_output_tokens": request.max_tokens,
            "store": False,
        }
        if instructions:
            payload["instructions"] = instructions
        if request.tools:
            payload["tools"] = [_tool_schema(tool) for tool in request.tools]
            payload["tool_choice"] = "auto"
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }
        headers.update(self.default_headers)
        return headers

    def _request_json(self, request: ProviderRequest) -> dict[str, Any]:
        body = json.dumps(self._payload(request), ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            _responses_url(self.base_url),
            data=body,
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(http_request, timeout=self.read_timeout) as response:
            return cast(dict[str, Any], json.loads(response.read().decode("utf-8")))

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        try:
            data = self._request_json(request)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return ProviderResponse(
                content=[],
                error=f"OpenAI Responses API error {exc.code}: {_json_error_message(body)}",
            )
        except Exception as exc:
            return ProviderResponse(content=[], error=f"OpenAI Responses API error: {exc}")

        content = _parse_output(data)
        stop_reason = (
            "tool_use" if any(block.get("type") == "tool_use" for block in content) else "end_turn"
        )
        if not content and isinstance(data.get("error"), dict):
            message = str(
                cast(dict[str, Any], data["error"]).get("message", "Unknown OpenAI API error")
            )
            return ProviderResponse(content=[], error=message, usage=_usage(data))
        return ProviderResponse(content=content, stop_reason=stop_reason, usage=_usage(data))

    def stream(self, request: ProviderRequest) -> Iterable[ProviderEvent]:
        response = self.generate(request)
        if response.error:
            raise RuntimeError(response.error)
        for block in response.content:
            if block.get("type") == "text":
                text = str(block.get("text", ""))
                if text:
                    yield ProviderEvent(type="text", text=text)
            yield ProviderEvent(type="block_end", block=block)
        yield ProviderEvent(
            type="message_end", stop_reason=response.stop_reason, usage=response.usage
        )


def _decode_unverified_jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
        claims = json.loads(decoded)
        return cast(dict[str, Any], claims) if isinstance(claims, dict) else {}
    except Exception:
        return {}


class CodexOAuthTokenManager:
    def __init__(self, *, auth_path: Path, timeout_seconds: float | None = None) -> None:
        self.auth_path = auth_path
        budget = get_runtime_budget()
        self.timeout_seconds = float(timeout_seconds or budget.provider_read_timeout)

    def _read(self) -> dict[str, Any]:
        data = json.loads(self.auth_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("Invalid ~/.codex/auth.json format")
        return cast(dict[str, Any], data)

    def _write(self, data: dict[str, Any]) -> None:
        self.auth_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _refresh(self, data: dict[str, Any]) -> dict[str, Any]:
        tokens_raw = data.get("tokens")
        if not isinstance(tokens_raw, dict):
            raise RuntimeError("Missing tokens in ~/.codex/auth.json")
        tokens = cast(dict[str, Any], tokens_raw)
        refresh_token = tokens.get("refresh_token")
        access_token = tokens.get("access_token")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise RuntimeError("Missing refresh_token in ~/.codex/auth.json")
        if not isinstance(access_token, str) or not access_token.strip():
            raise RuntimeError("Missing access_token in ~/.codex/auth.json")
        claims = _decode_unverified_jwt_claims(access_token)
        client_id = claims.get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            raise RuntimeError("Missing client_id in Codex OAuth token claims")

        payload = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            _OAUTH_TOKEN_URL,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                refreshed = cast(dict[str, Any], json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Codex OAuth refresh failed: {_json_error_message(body)}") from exc
        except Exception as exc:
            raise RuntimeError(f"Codex OAuth refresh failed: {exc}") from exc

        access = str(refreshed.get("access_token", "")).strip()
        refresh = str(refreshed.get("refresh_token", refresh_token)).strip()
        if not access:
            raise RuntimeError("Codex OAuth refresh did not return access_token")
        tokens["access_token"] = access
        if refresh:
            tokens["refresh_token"] = refresh
        id_token = refreshed.get("id_token")
        if isinstance(id_token, str) and id_token.strip():
            tokens["id_token"] = id_token.strip()
        self._write(
            {
                **data,
                "tokens": tokens,
                "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        return self._read()

    def get_access_token(self) -> str:
        data = self._read()
        tokens_raw = data.get("tokens")
        if not isinstance(tokens_raw, dict):
            raise RuntimeError("Missing tokens in ~/.codex/auth.json")
        tokens = cast(dict[str, Any], tokens_raw)
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise RuntimeError("Missing access_token in ~/.codex/auth.json")
        claims = _decode_unverified_jwt_claims(access_token)
        expires_at = int(claims.get("exp", 0) or 0)
        if expires_at and expires_at > int(time.time()) + 60:
            return access_token.strip()
        refreshed = self._refresh(data)
        refreshed_tokens_raw = refreshed.get("tokens", {})
        refreshed_tokens = (
            cast(dict[str, Any], refreshed_tokens_raw)
            if isinstance(refreshed_tokens_raw, dict)
            else {}
        )
        refreshed_access = refreshed_tokens.get("access_token")
        if not isinstance(refreshed_access, str) or not refreshed_access.strip():
            raise RuntimeError("Refreshed Codex auth missing access_token")
        return refreshed_access.strip()


class CodexOAuthProvider(Provider):
    name = "codex-oauth"
    features = ProviderFeatures(
        supports_streaming=True,
        supports_thinking=False,
        supports_images=True,
        supports_prompt_cache=False,
        supports_tool_calling=True,
        supports_structured_output=False,
    )

    def __init__(
        self,
        *,
        token_manager: CodexOAuthTokenManager,
        model: str,
        system_prompt: str | None = None,
        base_url: str = _CODEX_OAUTH_BASE_URL,
        default_headers: dict[str, str] | None = None,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
    ) -> None:
        self.token_manager = token_manager
        self.model = model
        self.system_prompt = system_prompt
        self.base_url = base_url
        self.default_headers = default_headers or {}
        budget = get_runtime_budget()
        self.connect_timeout = float(connect_timeout or budget.provider_connect_timeout)
        self.read_timeout = float(read_timeout or budget.provider_read_timeout)

    def clone(
        self, *, model: str | None = None, system_prompt: str | None = None
    ) -> CodexOAuthProvider:
        return CodexOAuthProvider(
            token_manager=self.token_manager,
            model=model or self.model,
            system_prompt=self.system_prompt if system_prompt is None else system_prompt,
            base_url=self.base_url,
            default_headers=dict(self.default_headers),
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
        )

    def _payload(self, request: ProviderRequest) -> dict[str, Any]:
        prepared_messages = prepare_messages_for_provider(request.messages)
        instructions = (
            request.system_prompt if request.system_prompt is not None else self.system_prompt
        )
        if not instructions:
            instructions = "You are Hermit's coding assistant."
        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "instructions": instructions,
            "input": _responses_input(prepared_messages, codex_oauth=True),
            "store": False,
            "stream": True,
        }
        if request.tools:
            payload["tools"] = [_tool_schema(tool) for tool in request.tools]
            payload["tool_choice"] = "auto"
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token_manager.get_access_token()}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": _USER_AGENT,
        }
        headers.update(self.default_headers)
        return headers

    def _open_stream(self, request: ProviderRequest) -> Any:
        body = json.dumps(self._payload(request), ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            self.base_url,
            data=body,
            headers=self._headers(),
            method="POST",
        )
        return urllib.request.urlopen(http_request, timeout=self.read_timeout)

    def _stream_impl(self, request: ProviderRequest) -> Iterable[ProviderEvent]:
        current_text: dict[str, str] = {}
        usage = UsageMetrics()
        stop_reason = "end_turn"
        with self._open_stream(request) as response:
            event_type = ""
            data_lines: list[str] = []

            def flush_event() -> Iterable[ProviderEvent]:
                nonlocal usage, stop_reason
                if not event_type or not data_lines:
                    return []
                raw = "\n".join(data_lines)
                try:
                    payload = cast(dict[str, Any], json.loads(raw))
                except Exception:
                    return []
                events: list[ProviderEvent] = []
                if event_type == "response.output_text.delta":
                    chunk = str(payload.get("delta", ""))
                    item_id = str(payload.get("item_id", ""))
                    if chunk:
                        current_text[item_id] = current_text.get(item_id, "") + chunk
                        events.append(ProviderEvent(type="text", text=chunk))
                elif event_type == "response.output_item.done":
                    item_raw = payload.get("item", {})
                    if isinstance(item_raw, dict):
                        item = cast(dict[str, Any], item_raw)
                        item_type = str(item.get("type", ""))
                        if item_type == "message":
                            text = ""
                            for raw_part in cast(list[Any], item.get("content", []) or []):
                                if isinstance(raw_part, dict):
                                    part = cast(dict[str, Any], raw_part)
                                    if part.get("type") == "output_text":
                                        text += str(part.get("text", ""))
                            events.append(
                                ProviderEvent(
                                    type="block_end", block={"type": "text", "text": text}
                                )
                            )
                        elif item_type == "function_call":
                            raw_arguments = item.get("arguments", "{}")
                            try:
                                arguments: dict[str, Any] = (
                                    cast(dict[str, Any], json.loads(raw_arguments))
                                    if isinstance(raw_arguments, str)
                                    else dict(cast(dict[str, Any], raw_arguments or {}))
                                )
                            except Exception:
                                arguments = {}
                            events.append(
                                ProviderEvent(
                                    type="block_end",
                                    block={
                                        "type": "tool_use",
                                        "id": str(item.get("call_id") or item.get("id") or ""),
                                        "name": str(item.get("name", "")),
                                        "input": arguments,
                                    },
                                )
                            )
                elif event_type == "response.completed":
                    response_obj_raw = payload.get("response", {})
                    if isinstance(response_obj_raw, dict):
                        response_obj = cast(dict[str, Any], response_obj_raw)
                        usage = _usage(response_obj)
                        output = cast(list[Any], response_obj.get("output", []) or [])
                        if any(
                            isinstance(i, dict)
                            and cast(dict[str, Any], i).get("type") == "function_call"
                            for i in output
                        ):
                            stop_reason = "tool_use"
                        events.append(
                            ProviderEvent(type="message_end", stop_reason=stop_reason, usage=usage)
                        )
                elif event_type == "response.failed":
                    raise RuntimeError(_format_stream_error("Codex OAuth stream error", payload))
                elif event_type == "response.incomplete":
                    raise RuntimeError(
                        _format_stream_error("Codex OAuth stream incomplete", payload)
                    )
                elif event_type == "error":
                    raise RuntimeError(_format_stream_error("Codex OAuth stream error", payload))
                return events

            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                if not line:
                    for event in flush_event():
                        yield event
                    event_type = ""
                    data_lines = []
                    continue
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].strip())
            for event in flush_event():
                yield event

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        content: list[dict[str, Any]] = []
        usage = UsageMetrics()
        stop_reason = "end_turn"
        try:
            for event in self._stream_impl(request):
                if event.type == "block_end" and event.block is not None:
                    content.append(event.block)
                elif event.type == "message_end":
                    stop_reason = event.stop_reason or stop_reason
                    usage = event.usage or usage
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return ProviderResponse(
                content=[], error=f"Codex OAuth API error {exc.code}: {_json_error_message(body)}"
            )
        except Exception as exc:
            return ProviderResponse(content=[], error=f"Codex OAuth API error: {exc}")
        return ProviderResponse(content=content, stop_reason=stop_reason, usage=usage)

    def stream(self, request: ProviderRequest) -> Iterable[ProviderEvent]:
        try:
            yield from self._stream_impl(request)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Codex OAuth API error {exc.code}: {_json_error_message(body)}"
            ) from exc
