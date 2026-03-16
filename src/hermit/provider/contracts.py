from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from hermit.core.tools import ToolSpec


def _usage_extra() -> dict[str, int]:
    return {}


def _tool_specs() -> list[ToolSpec]:
    return []


def _metadata_map() -> dict[str, Any]:
    return {}


@dataclass(frozen=True)
class ProviderFeatures:
    supports_streaming: bool = False
    supports_thinking: bool = False
    supports_images: bool = False
    supports_prompt_cache: bool = False
    supports_tool_calling: bool = False
    supports_structured_output: bool = False


@dataclass
class UsageMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    extra: dict[str, int] = field(default_factory=_usage_extra)


@dataclass
class ToolCall:
    id: str
    name: str
    payload: dict[str, Any]


@dataclass
class ToolResult:
    tool_use_id: str
    content: Any
    is_error: bool = False


@dataclass
class ProviderRequest:
    model: str
    max_tokens: int
    messages: list[dict[str, Any]]
    system_prompt: str | None = None
    tools: list[ToolSpec] = field(default_factory=_tool_specs)
    thinking_budget: int = 0
    stream: bool = False
    metadata: dict[str, Any] = field(default_factory=_metadata_map)


@dataclass
class ProviderResponse:
    content: list[dict[str, Any]]
    stop_reason: str | None = None
    error: str | None = None
    usage: UsageMetrics = field(default_factory=UsageMetrics)


@dataclass
class ProviderEvent:
    type: str
    text: str = ""
    block: dict[str, Any] | None = None
    stop_reason: str | None = None
    usage: UsageMetrics | None = None


class Provider(Protocol):
    name: str
    features: ProviderFeatures

    def generate(self, request: ProviderRequest) -> ProviderResponse: ...

    def stream(self, request: ProviderRequest) -> Iterable[ProviderEvent]: ...

    def clone(
        self,
        *,
        model: str | None = None,
        system_prompt: str | None = None,
    ) -> "Provider": ...


class ProviderFactory(Protocol):
    def create(self, settings: Any) -> Provider: ...
