from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import structlog

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.runtime.capability.contracts.base import SubagentSpec
from hermit.runtime.capability.contracts.hooks import HooksEngine
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec, localize_tool_spec

log = structlog.get_logger()

if TYPE_CHECKING:
    from hermit.runtime.provider_host.execution.runtime import ToolCallback

# ANSI escape codes for subagent output
_DIM = "\033[2m"
_MAGENTA = "\033[35m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_RESET = "\033[0m"


class SubagentExecutor:
    """Builds delegation tools and runs subagents within the governed kernel.

    Extracted from PluginManager to isolate subagent lifecycle concerns:
    tool construction, principal registration, event emission, and execution.
    """

    def __init__(
        self,
        *,
        hooks: HooksEngine,
        runtime: Any,
        settings: Any,
        registry: ToolRegistry | None,
        model: str,
        max_tokens: int,
        tool_output_limit: int,
        on_tool_call: Any,
    ) -> None:
        self.hooks = hooks
        self._runtime = runtime
        self.settings = settings
        self._registry = registry
        self._model = model
        self._max_tokens = max_tokens
        self._tool_output_limit = tool_output_limit
        self._on_tool_call = on_tool_call

    def build_delegation_tool(self, spec: SubagentSpec) -> ToolSpec:
        """Build a ToolSpec that delegates work to a subagent."""
        locale = resolve_locale(getattr(self.settings, "locale", None))

        def handler(payload: dict[str, Any]) -> str:
            return self.run_subagent(spec, str(payload.get("task", "")))

        if getattr(spec, "governed", False):
            action_class = "delegate_execution"
            readonly = False
            risk_hint = "medium"
            requires_receipt = True
        else:
            action_class = "delegate_reasoning"
            readonly = True
            risk_hint = "low"
            requires_receipt = False

        return localize_tool_spec(
            ToolSpec(
                name=f"delegate_{spec.name}",
                description=tr(
                    "prompt.delegation.description",
                    locale=locale,
                    default=f"Delegate a task to the {spec.name} subagent. {spec.description}",
                    name=spec.name,
                    description=spec.description,
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description_key": "prompt.delegation.task",
                        }
                    },
                    "required": ["task"],
                },
                handler=handler,
                readonly=readonly,
                action_class=action_class,
                idempotent=True,
                risk_hint=risk_hint,
                requires_receipt=requires_receipt,
            ),
            locale=locale,
        )

    def register_subagent_principal(self, spec: SubagentSpec) -> str | None:
        """Register a principal for a governed subagent, returning the principal_id."""
        if not spec.governed:
            return None
        store = getattr(self._runtime, "kernel_store", None)
        if store is None:
            return None
        principal_id = f"principal_subagent_{spec.name}"
        try:
            store.ensure_principal(
                principal_id=principal_id,
                principal_type="subagent",
                display_name=spec.name,
                metadata={"parent_principal": "principal_user", "tools": spec.tools},
            )
        except Exception:
            log.warning("subagent_principal_registration_failed", subagent=spec.name)
            return None
        return principal_id

    def emit_subagent_event(
        self,
        event_type: str,
        spec: SubagentSpec,
        principal_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emit a ledger event for subagent lifecycle."""
        store = getattr(self._runtime, "kernel_store", None)
        if store is None:
            return
        try:
            store.append_event(
                event_type=event_type,
                entity_type="subagent",
                entity_id=principal_id,
                task_id=None,
                actor=principal_id,
                payload=payload or {},
            )
        except Exception:
            log.warning(
                "subagent_event_failed",
                event_type=event_type,
                subagent=spec.name,
            )

    def run_subagent(self, spec: SubagentSpec, task: str) -> str:
        """Run a subagent with the given task and return the result text."""
        if not self._runtime or not self._registry:
            locale = resolve_locale(getattr(self.settings, "locale", None))
            return tr(
                "prompt.delegation.unavailable",
                locale=locale,
                default=f"[Subagent '{spec.name}' unavailable: agent runner not configured]",
                name=spec.name,
            )

        principal_id = self.register_subagent_principal(spec)

        sub_registry = ToolRegistry()
        available: list[str] = []
        for tool_name in spec.tools:
            try:
                sub_registry.register(self._registry.get(tool_name))
                available.append(tool_name)
            except KeyError:
                log.warning("subagent_tool_not_found", subagent=spec.name, tool=tool_name)

        system_prompt = spec.system_prompt
        if spec.context_fragments:
            context_block = "\n".join(spec.context_fragments)
            system_prompt = f"<task_context>\n{context_block}\n</task_context>\n\n{system_prompt}"

        sub_agent = self._runtime.clone(
            registry=sub_registry,
            model=spec.model or self._model,
            max_turns=15,
            system_prompt=system_prompt,
        )

        task_preview = task[:80] + ("..." if len(task) > 80 else "")
        tools_str = ", ".join(available)
        sys.stderr.write(
            f"\n{_MAGENTA}  \u250c\u2500 subagent:{spec.name} "
            f"{_DIM}[tools: {tools_str}]{_RESET}\n"
            f"{_MAGENTA}  \u2502{_RESET} {_DIM}{task_preview}{_RESET}\n"
        )
        sys.stderr.flush()

        if principal_id:
            self.emit_subagent_event(
                "subagent_spawned",
                spec,
                principal_id,
                {"task_preview": task_preview, "tools": available},
            )

        def _sub_tool_call(name: str, inputs: dict[str, Any], result: object) -> None:
            compact = ", ".join(f"{k}={repr(v)[:50]}" for k, v in inputs.items())
            text = result if isinstance(result, str) else str(result)
            preview = text[:150].replace("\n", " ")
            if len(text) > 150:
                preview += "..."
            sys.stderr.write(f"{_MAGENTA}  \u2502{_RESET}   \u25b8 {name}({compact})\n")
            sys.stderr.write(f"{_MAGENTA}  \u2502{_RESET}   {_DIM}\u2192 {preview}{_RESET}\n")
            sys.stderr.flush()

        callback: ToolCallback = self._on_tool_call or _sub_tool_call

        try:
            result = sub_agent.run(task, on_tool_call=callback, readonly_only=not spec.governed)
            sys.stderr.write(
                f"{_MAGENTA}  \u2514\u2500{_RESET} {_GREEN}done{_RESET} "
                f"{_DIM}({result.turns} turns, {result.tool_calls} tool calls){_RESET}\n\n"
            )
            sys.stderr.flush()
            if principal_id:
                self.emit_subagent_event(
                    "subagent_completed",
                    spec,
                    principal_id,
                    {"turns": result.turns, "tool_calls": result.tool_calls},
                )
            return result.text
        except Exception as exc:
            sys.stderr.write(f"{_MAGENTA}  \u2514\u2500{_RESET} {_RED}error: {exc}{_RESET}\n\n")
            sys.stderr.flush()
            if principal_id:
                self.emit_subagent_event(
                    "subagent_failed",
                    spec,
                    principal_id,
                    {"error": str(exc)},
                )
            return f"[Subagent '{spec.name}' error: {exc}]"
