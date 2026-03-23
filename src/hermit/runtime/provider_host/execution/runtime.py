from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeGuard, cast

import structlog

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.kernel.authority.grants import CapabilityGrantError
from hermit.kernel.context.models.context import TaskExecutionContext
from hermit.kernel.errors import KernelError
from hermit.kernel.execution.executor.executor import ToolExecutionResult, ToolExecutor
from hermit.runtime.capability.registry.tools import ToolRegistry, serialize_tool_result
from hermit.runtime.provider_host.shared.contracts import Provider, ProviderRequest, UsageMetrics
from hermit.runtime.provider_host.shared.messages import (
    block_value,
    extract_text,
    extract_thinking,
    normalize_block,
    normalize_messages,
)

log = structlog.get_logger()

if TYPE_CHECKING:
    from hermit.kernel import ArtifactStore, KernelStore, TaskController
    from hermit.kernel.execution.competition.deliberation_integration import DeliberationIntegration

StreamCallback = Callable[[str, str], None]
ToolCallback = Callable[[str, dict[str, Any], Any], None]
ToolStartCallback = Callable[[str, dict[str, Any]], None]

_TOOL_RESULT_BLOCK_TYPES = {"text", "image"}
_CONTEXT_TOO_LONG_MARKERS = ("prompt is too long", "context window", "maximum context length")


def _message_list() -> list[dict[str, Any]]:
    return []


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


def _is_tool_result_block(value: Any) -> TypeGuard[dict[str, Any]]:
    if not isinstance(value, dict):
        return False
    block = cast(dict[str, Any], value)
    return block.get("type") in _TOOL_RESULT_BLOCK_TYPES


def format_tool_result_content(value: Any, limit: int) -> Any:
    serialized = serialize_tool_result(value)
    if isinstance(serialized, str):
        return truncate_middle_text(serialized, limit)
    if _is_tool_result_block(serialized):
        return [serialized]
    if isinstance(serialized, dict):
        return _tool_result_json_text(serialized, limit)
    if all(_is_tool_result_block(item) for item in serialized):
        return serialized
    return _tool_result_json_text(serialized, limit)


@dataclass
class AgentResult:
    text: str
    turns: int
    tool_calls: int
    thinking: str = ""
    messages: list[dict[str, Any]] = field(default_factory=_message_list)
    blocked: bool = False
    suspended: bool = False
    waiting_kind: str | None = None
    approval_id: str | None = None
    observation: dict[str, Any] | None = None
    step_attempt_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    execution_status: str = "succeeded"
    status_managed_by_kernel: bool = False


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
        system_prompt: str | None = None,
        tool_executor: ToolExecutor | None = None,
        locale: str | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.model = model
        self.max_tokens = max_tokens
        self.max_turns = max_turns
        self.tool_output_limit = tool_output_limit
        self.thinking_budget = thinking_budget
        self.system_prompt = system_prompt
        self.tool_executor = tool_executor
        self.locale = resolve_locale(locale)
        self.workspace_root: str | None = None
        self.kernel_store: KernelStore | None = None
        self.artifact_store: ArtifactStore | None = None
        self.task_controller: TaskController | None = None
        self.deliberation: DeliberationIntegration | None = None

    def clone(
        self,
        *,
        registry: ToolRegistry | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        max_turns: int | None = None,
    ) -> AgentRuntime:
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
            tool_executor=self.tool_executor,
            locale=self.locale,
        )

    def _t(self, message_key: str, *, default: str | None = None, **kwargs: object) -> str:
        return tr(message_key, locale=self.locale, default=default, **kwargs)

    def _usage_to_result(self, usage: UsageMetrics, **kwargs: Any) -> AgentResult:
        return AgentResult(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            **kwargs,
        )

    def _tool_result_block(
        self, *, tool_name: str, tool_use_id: str, content: Any, is_error: bool = False
    ) -> dict[str, Any]:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if is_error:
            block["is_error"] = True
        try:
            tool = self.registry.get(tool_name)
        except KeyError:
            tool = None
        if tool is not None and tool.result_is_internal_context:
            block["internal_context"] = True
            block["tool_name"] = tool_name
        return block

    def _request(
        self,
        messages: list[dict[str, Any]],
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
            messages=messages,
            system_prompt=self.system_prompt,
            tools=tools,
            thinking_budget=thinking_budget,
            stream=stream,
        )

    def run(
        self,
        prompt: str,
        message_history: list[dict[str, Any]] | None = None,
        compiled_messages: list[dict[str, Any]] | None = None,
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
        disable_tools: bool = False,
        readonly_only: bool = False,
        task_context: TaskExecutionContext | None = None,
    ) -> AgentResult:
        if compiled_messages is not None:
            messages = normalize_messages(compiled_messages)
        else:
            messages = normalize_messages(message_history or [])
            messages.append({"role": "user", "content": prompt})
        return self._run_from_messages(
            messages,
            start_turn=1,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
            disable_tools=disable_tools,
            readonly_only=readonly_only,
            task_context=task_context,
        )

    def resume(
        self,
        *,
        step_attempt_id: str,
        task_context: TaskExecutionContext,
        on_tool_call: ToolCallback | None = None,
        on_tool_start: ToolStartCallback | None = None,
    ) -> AgentResult:
        if self.tool_executor is None:
            raise RuntimeError(
                self._t(
                    "kernel.runtime.error.resume_requires_tool_executor",
                    default="Task resume requires a configured ToolExecutor",
                )
            )
        loader = getattr(self.tool_executor, "load_suspended_state", None) or getattr(
            self.tool_executor, "load_blocked_state"
        )
        snapshot = loader(step_attempt_id)
        messages = normalize_messages(list(snapshot.get("messages", [])))
        pending_tool_blocks = list(snapshot.get("pending_tool_blocks", []))
        tool_result_blocks = list(snapshot.get("tool_result_blocks", []))
        next_turn = int(snapshot.get("next_turn", 1))
        disable_tools = bool(snapshot.get("disable_tools", False))
        readonly_only = bool(snapshot.get("readonly_only", False))
        suspend_kind = str(snapshot.get("suspend_kind", "awaiting_approval") or "awaiting_approval")
        observation = dict(snapshot.get("observation", {}) or {})
        tool_calls = 0
        usage = UsageMetrics()

        if suspend_kind == "observing" and observation.get("terminal_status"):
            tool_result_blocks, tool_calls = self._resume_observation_turn(
                pending_tool_blocks=pending_tool_blocks,
                tool_result_blocks=tool_result_blocks,
                observation=observation,
                on_tool_call=on_tool_call,
            )
            remaining_tool_blocks = pending_tool_blocks[1:]
            if remaining_tool_blocks:
                executed = self._execute_tool_turn(
                    messages=messages,
                    tool_use_blocks=remaining_tool_blocks,
                    tool_result_blocks=tool_result_blocks,
                    turn=next_turn - 1,
                    on_tool_call=on_tool_call,
                    on_tool_start=on_tool_start,
                    disable_tools=disable_tools,
                    readonly_only=readonly_only,
                    task_context=task_context,
                    usage=usage,
                    tool_calls=tool_calls,
                )
                if isinstance(executed, AgentResult):
                    return executed
                tool_result_blocks, tool_calls = executed
        else:
            executed = self._execute_tool_turn(
                messages=messages,
                tool_use_blocks=pending_tool_blocks,
                tool_result_blocks=tool_result_blocks,
                turn=next_turn - 1,
                on_tool_call=on_tool_call,
                on_tool_start=on_tool_start,
                disable_tools=disable_tools,
                readonly_only=readonly_only,
                task_context=task_context,
                usage=usage,
                tool_calls=tool_calls,
            )
            if isinstance(executed, AgentResult):
                return executed
            tool_result_blocks, tool_calls = executed
        messages.append({"role": "user", "content": tool_result_blocks})
        clearer = getattr(self.tool_executor, "clear_suspended_state", None) or getattr(
            self.tool_executor, "clear_blocked_state"
        )
        clearer(step_attempt_id)
        return self._run_from_messages(
            messages,
            start_turn=next_turn,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
            disable_tools=disable_tools,
            readonly_only=readonly_only,
            task_context=task_context,
            usage=usage,
            tool_calls=tool_calls,
        )

    @staticmethod
    def _is_context_too_long(exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(marker in msg for marker in _CONTEXT_TOO_LONG_MARKERS)

    def _trim_messages_for_retry(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        half_limit = max(self.tool_output_limit // 2, 256)
        trimmed = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                trimmed.append(msg)
                continue
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, str) and len(inner) > half_limit:
                        block = {**block, "content": truncate_middle_text(inner, half_limit)}
                new_blocks.append(block)
            trimmed.append({**msg, "content": new_blocks})
        if len(trimmed) > 6:
            kept_head = []
            kept_tail = trimmed[-4:]
            for m in trimmed[:-4]:
                if m.get("role") == "system":
                    kept_head.append(m)
                    break
            trimmed = kept_head + kept_tail
        return trimmed

    def _run_from_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        start_turn: int,
        on_tool_call: ToolCallback | None,
        on_tool_start: ToolStartCallback | None,
        disable_tools: bool,
        readonly_only: bool,
        task_context: TaskExecutionContext | None,
        usage: UsageMetrics | None = None,
        tool_calls: int = 0,
    ) -> AgentResult:
        usage = usage or UsageMetrics()

        for turn in range(start_turn, self.max_turns + 1):
            messages = self._apply_appended_notes(messages, task_context)
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
                if self._is_context_too_long(exc):
                    log.warning(
                        "context_too_long_retry",
                        provider=self.provider.name,
                        turn=turn,
                        error=str(exc),
                    )
                    try:
                        trimmed = self._trim_messages_for_retry(messages)
                        response = self.provider.generate(
                            self._request(
                                trimmed,
                                disable_tools=disable_tools,
                                readonly_only=readonly_only,
                                stream=False,
                            )
                        )
                        messages = trimmed
                    except Exception as retry_exc:
                        log.error(
                            "context_too_long_retry_failed",
                            provider=self.provider.name,
                            turn=turn,
                            error=str(retry_exc),
                        )
                        return self._usage_to_result(
                            usage,
                            text=f"[API Error] {exc}",
                            turns=turn,
                            tool_calls=tool_calls,
                            messages=messages,
                            task_id=task_context.task_id if task_context else None,
                            step_id=task_context.step_id if task_context else None,
                            step_attempt_id=task_context.step_attempt_id if task_context else None,
                            execution_status="failed",
                        )
                else:
                    log.error(
                        "provider_call_error",
                        provider=self.provider.name,
                        turn=turn,
                        error=str(exc),
                    )
                    return self._usage_to_result(
                        usage,
                        text=f"[API Error] {exc}",
                        turns=turn,
                        tool_calls=tool_calls,
                        messages=messages,
                        task_id=task_context.task_id if task_context else None,
                        step_id=task_context.step_id if task_context else None,
                        step_attempt_id=task_context.step_attempt_id if task_context else None,
                        execution_status="failed",
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
                    task_id=task_context.task_id if task_context else None,
                    step_id=task_context.step_id if task_context else None,
                    step_attempt_id=task_context.step_attempt_id if task_context else None,
                    execution_status="failed",
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
                    task_id=task_context.task_id if task_context else None,
                    step_id=task_context.step_id if task_context else None,
                    step_attempt_id=task_context.step_attempt_id if task_context else None,
                    execution_status="succeeded",
                )

            tool_use_blocks = [
                block for block in response_blocks if block_value(block, "type") == "tool_use"
            ]
            if not tool_use_blocks:
                raise RuntimeError(
                    self._t(
                        "kernel.runtime.error.tool_use_without_blocks",
                        default="Provider requested tool_use without tool blocks.",
                    )
                )

            executed = self._execute_tool_turn(
                messages=messages,
                tool_use_blocks=tool_use_blocks,
                tool_result_blocks=[],
                turn=turn,
                on_tool_call=on_tool_call,
                on_tool_start=on_tool_start,
                disable_tools=disable_tools,
                readonly_only=readonly_only,
                task_context=task_context,
                usage=usage,
                tool_calls=tool_calls,
            )
            if isinstance(executed, AgentResult):
                return executed
            tool_result_blocks, tool_calls = executed

            messages.append({"role": "user", "content": tool_result_blocks})

        log.warning("max_turns_exceeded", max_turns=self.max_turns, tool_calls=tool_calls)
        messages.append(
            {
                "role": "user",
                "content": self._t(
                    "prompt.runtime.max_turns_exceeded",
                    default="You have reached the maximum turn limit. Stop using tools and provide the best possible final answer based on the work completed so far.",
                ),
            }
        )
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
                task_id=task_context.task_id if task_context else None,
                step_id=task_context.step_id if task_context else None,
                step_attempt_id=task_context.step_attempt_id if task_context else None,
                execution_status="succeeded",
            )
        except Exception as exc:
            log.error("final_summary_failed", error=str(exc))
            return self._usage_to_result(
                usage,
                text=self._t(
                    "prompt.runtime.final_summary_failed",
                    default="Reached the maximum turn limit ({max_turns}) and the final summary request failed: {error}. Respond with a brief explanation of what was completed and what remains blocked.",
                    max_turns=self.max_turns,
                    error=exc,
                ),
                turns=self.max_turns + 1,
                tool_calls=tool_calls,
                messages=messages,
                task_id=task_context.task_id if task_context else None,
                step_id=task_context.step_id if task_context else None,
                step_attempt_id=task_context.step_attempt_id if task_context else None,
                execution_status="failed",
            )

    def _execute_tool_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        tool_use_blocks: list[dict[str, Any]],
        tool_result_blocks: list[dict[str, Any]],
        turn: int,
        on_tool_call: ToolCallback | None,
        on_tool_start: ToolStartCallback | None,
        disable_tools: bool,
        readonly_only: bool,
        task_context: TaskExecutionContext | None,
        usage: UsageMetrics,
        tool_calls: int,
    ) -> AgentResult | tuple[list[dict[str, Any]], int]:
        for index, block in enumerate(tool_use_blocks):
            tool_name = str(block_value(block, "name"))
            tool_input = dict(block_value(block, "input", {}) or {})
            if on_tool_start:
                on_tool_start(tool_name, tool_input)
            try:
                exec_result = self._execute_tool(
                    task_context=task_context,
                    tool_name=tool_name,
                    tool_input=tool_input,
                )
                serialized = exec_result.model_content
            except KeyError:
                available_tools = [tool.name for tool in self.registry.list_tools()]
                serialized = f"Error: Unknown tool '{tool_name}'. Available: {available_tools}"
                exec_result = ToolExecutionResult(model_content=serialized, raw_result=serialized)
            except CapabilityGrantError as exc:
                log.warning(
                    "capability_grant_error",
                    tool=tool_name,
                    error_code=exc.code,
                    error=str(exc),
                    task_id=task_context.task_id if task_context else None,
                    step_attempt_id=task_context.step_attempt_id if task_context else None,
                )
                serialized = f"[Capability Denied] {exc}"
                exec_result = ToolExecutionResult(
                    model_content=serialized,
                    raw_result={"error": str(exc), "error_code": exc.code},
                    denied=True,
                    result_code="dispatch_denied",
                    execution_status="dispatch_denied",
                )
            except KernelError as exc:
                log.warning(
                    "kernel_governance_error",
                    tool=tool_name,
                    error_code=exc.code,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    task_id=task_context.task_id if task_context else None,
                    step_attempt_id=task_context.step_attempt_id if task_context else None,
                )
                serialized = f"[Governance Error] {type(exc).__name__}: {exc}"
                exec_result = ToolExecutionResult(
                    model_content=serialized,
                    raw_result={"error": str(exc), "error_code": exc.code},
                    denied=True,
                    result_code="governance_error",
                    execution_status="governance_error",
                )
            except Exception as exc:
                log.error(
                    "tool_execution_error",
                    tool=tool_name,
                    error_type=type(exc).__name__,
                    error=str(exc),
                    task_id=task_context.task_id if task_context else None,
                    step_attempt_id=task_context.step_attempt_id if task_context else None,
                )
                serialized = f"Error executing {tool_name}: {type(exc).__name__}: {exc}"
                exec_result = ToolExecutionResult(model_content=serialized, raw_result=serialized)

            if (
                (exec_result.suspended or exec_result.blocked)
                and self.tool_executor is not None
                and task_context is not None
            ):
                note_cursor = (
                    self.tool_executor.current_note_cursor(task_context.step_attempt_id)
                    if hasattr(self.tool_executor, "current_note_cursor")
                    else 0
                )
                supports_suspend_persist = hasattr(self.tool_executor, "persist_suspended_state")
                persister = getattr(self.tool_executor, "persist_suspended_state", None) or getattr(
                    self.tool_executor, "persist_blocked_state"
                )
                persister(
                    task_context,
                    pending_tool_blocks=tool_use_blocks[index:],
                    tool_result_blocks=tool_result_blocks,
                    messages=messages,
                    next_turn=turn + 1,
                    disable_tools=disable_tools,
                    readonly_only=readonly_only,
                    **(
                        {
                            "suspend_kind": exec_result.waiting_kind or "awaiting_approval",
                            "note_cursor_event_seq": note_cursor,
                            "observation": exec_result.observation,
                        }
                        if supports_suspend_persist
                        else {}
                    ),
                )
                blocked_message = exec_result.approval_message or str(exec_result.model_content)
                blocked_messages = list(messages)
                blocked_messages.append(
                    {"role": "assistant", "content": [{"type": "text", "text": blocked_message}]}
                )
                return self._usage_to_result(
                    usage,
                    text=blocked_message,
                    turns=turn,
                    tool_calls=tool_calls,
                    messages=blocked_messages,
                    blocked=True,
                    suspended=True,
                    waiting_kind=exec_result.waiting_kind,
                    approval_id=exec_result.approval_id,
                    observation=exec_result.observation.to_dict()
                    if exec_result.observation is not None
                    else None,
                    task_id=task_context.task_id,
                    step_id=task_context.step_id,
                    step_attempt_id=task_context.step_attempt_id,
                    execution_status=exec_result.execution_status,
                    status_managed_by_kernel=exec_result.state_applied,
                )

            if exec_result.denied and task_context is not None:
                denied_message = str(exec_result.model_content)
                denied_messages = list(messages)
                denied_messages.append(
                    {"role": "assistant", "content": [{"type": "text", "text": denied_message}]}
                )
                return self._usage_to_result(
                    usage,
                    text=denied_message,
                    turns=turn,
                    tool_calls=tool_calls,
                    messages=denied_messages,
                    task_id=task_context.task_id,
                    step_id=task_context.step_id,
                    step_attempt_id=task_context.step_attempt_id,
                    execution_status=exec_result.execution_status,
                    status_managed_by_kernel=exec_result.state_applied,
                )

            if on_tool_call:
                on_tool_call(tool_name, tool_input, serialized)
            if exec_result.receipt_id:
                pd = exec_result.policy_decision
                log.info(
                    "governed_tool_execution",
                    tool=tool_name,
                    receipt=exec_result.receipt_id,
                    decision=exec_result.decision_id,
                    grant=exec_result.capability_grant_id,
                    action_class=pd.action_class if pd else None,
                    verdict=pd.verdict if pd else None,
                    risk=pd.risk_level if pd else None,
                )
            tool_result_blocks.append(
                self._tool_result_block(
                    tool_name=tool_name,
                    tool_use_id=str(block_value(block, "id")),
                    content=serialized,
                )
            )
            tool_calls += 1
        return tool_result_blocks, tool_calls

    def _execute_tool(
        self,
        *,
        task_context: TaskExecutionContext | None,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> ToolExecutionResult:
        if self.tool_executor is None:
            raise RuntimeError(
                self._t(
                    "kernel.runtime.error.tool_execution_requires_executor",
                    default="Task-scoped kernel executor is required for tool execution.",
                )
            )
        if task_context is None:
            raise RuntimeError(
                self._t(
                    "kernel.runtime.error.tool_execution_missing_context",
                    default="Tool '{tool_name}' requires task-scoped governed execution; task context is missing.",
                    tool_name=tool_name,
                )
            )
        return self.tool_executor.execute(task_context, tool_name, tool_input)

    def _apply_appended_notes(
        self,
        messages: list[dict[str, Any]],
        task_context: TaskExecutionContext | None,
    ) -> list[dict[str, Any]]:
        if (
            self.tool_executor is None
            or task_context is None
            or not hasattr(self.tool_executor, "consume_appended_notes")
        ):
            return messages
        appended, _cursor = self.tool_executor.consume_appended_notes(task_context)
        if not appended:
            return messages
        return normalize_messages(list(messages) + appended)

    def _resume_observation_turn(
        self,
        *,
        pending_tool_blocks: list[dict[str, Any]],
        tool_result_blocks: list[dict[str, Any]],
        observation: dict[str, Any],
        on_tool_call: ToolCallback | None,
    ) -> tuple[list[dict[str, Any]], int]:
        if not pending_tool_blocks:
            return tool_result_blocks, 0
        current = pending_tool_blocks[0]
        content = observation.get("final_model_content")
        if on_tool_call:
            on_tool_call(
                str(block_value(current, "name")),
                dict(block_value(current, "input", {}) or {}),
                content,
            )
        tool_result_blocks.append(
            self._tool_result_block(
                tool_name=str(block_value(current, "name")),
                tool_use_id=str(block_value(current, "id")),
                content=content,
                is_error=bool(observation.get("final_is_error", False)),
            )
        )
        return tool_result_blocks, 1

    def run_stream(
        self,
        prompt: str,
        message_history: list[dict[str, Any]] | None = None,
        on_token: StreamCallback | None = None,
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

        messages: list[dict[str, Any]] = normalize_messages(message_history or [])
        messages.append({"role": "user", "content": prompt})
        tool_calls = 0
        usage = UsageMetrics()

        for turn in range(1, self.max_turns + 1):
            response_blocks: list[dict[str, Any]] = []
            stop_reason: str | None = None
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
                if self._is_context_too_long(exc):
                    log.warning(
                        "stream_context_too_long_retry",
                        provider=self.provider.name,
                        turn=turn,
                        error=str(exc),
                    )
                    try:
                        messages = self._trim_messages_for_retry(messages)
                        continue
                    except Exception:
                        pass
                log.error("stream_error", provider=self.provider.name, turn=turn, error=str(exc))
                return self._usage_to_result(
                    usage,
                    text=f"[Stream Error] {exc}",
                    turns=turn,
                    tool_calls=tool_calls,
                    messages=messages,
                    execution_status="failed",
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
                    execution_status="succeeded",
                )

            tool_use_blocks = [
                block for block in response_blocks if block_value(block, "type") == "tool_use"
            ]
            if not tool_use_blocks:
                raise RuntimeError(
                    self._t(
                        "kernel.runtime.error.tool_use_without_blocks",
                        default="Provider requested tool_use without tool blocks.",
                    )
                )

            tool_result_blocks: list[dict[str, Any]] = []
            for block in tool_use_blocks:
                tool_name = str(block_value(block, "name"))
                tool_input = dict(block_value(block, "input", {}) or {})
                try:
                    if self.tool_executor is None:
                        raise RuntimeError(
                            self._t(
                                "kernel.runtime.error.streaming_tool_execution_requires_executor",
                                default="Task-scoped kernel executor is required for streaming tool execution.",
                            )
                        )
                    exec_result = self._execute_tool(
                        task_context=None,
                        tool_name=tool_name,
                        tool_input=tool_input,
                    )
                    serialized = exec_result.model_content
                except KeyError:
                    available_tools = [tool.name for tool in self.registry.list_tools()]
                    serialized = f"Error: Unknown tool '{tool_name}'. Available: {available_tools}"
                except Exception as exc:
                    serialized = self._t(
                        "kernel.runtime.error.serialized_tool_execution",
                        default="Error executing {tool_name}: {error_type}: {error}",
                        tool_name=tool_name,
                        error_type=type(exc).__name__,
                        error=exc,
                    )
                tool_result_blocks.append(
                    self._tool_result_block(
                        tool_name=tool_name,
                        tool_use_id=str(block_value(block, "id")),
                        content=serialized,
                    )
                )
                tool_calls += 1
            messages.append({"role": "user", "content": tool_result_blocks})

        messages.append(
            {
                "role": "user",
                "content": self._t(
                    "prompt.runtime.max_turns_exceeded",
                    default="You have reached the maximum turn limit. Stop using tools and provide the best possible final answer based on the work completed so far.",
                ),
            }
        )
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
                execution_status="succeeded",
            )
        except Exception as exc:
            log.error("final_summary_failed_stream", error=str(exc))
            return self._usage_to_result(
                usage,
                text=self._t(
                    "prompt.runtime.final_summary_failed",
                    default="Reached the maximum turn limit ({max_turns}) and the final summary request failed: {error}. Respond with a brief explanation of what was completed and what remains blocked.",
                    max_turns=self.max_turns,
                    error=exc,
                ),
                turns=self.max_turns + 1,
                tool_calls=tool_calls,
                messages=messages,
                execution_status="failed",
            )
