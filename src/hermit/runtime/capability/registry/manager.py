from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import structlog

from hermit.infra.system.i18n import resolve_locale, tr
from hermit.runtime.capability.contracts.base import (
    AdapterProtocol,
    AdapterSpec,
    CommandHandler,
    CommandSpec,
    HookEvent,
    McpServerSpec,
    PluginManifest,
    SubagentSpec,
)
from hermit.runtime.capability.contracts.hooks import HooksEngine
from hermit.runtime.capability.contracts.rules import load_rules_text
from hermit.runtime.capability.contracts.skills import SkillDefinition, load_skills
from hermit.runtime.capability.loader.loader import discover_plugins, load_plugin_entries
from hermit.runtime.capability.registry.tools import ToolRegistry, ToolSpec, localize_tool_spec

log = structlog.get_logger()

if TYPE_CHECKING:
    from hermit.runtime.provider_host.execution.runtime import AgentRuntime, ToolCallback


class _RunnerCommandHost(Protocol):
    def add_command(
        self, name: str, handler: CommandHandler, help_text: str, cli_only: bool = False
    ) -> None: ...


class PluginManager:
    def __init__(self, settings: Any = None) -> None:
        self.hooks = HooksEngine()
        self.settings = settings
        self._manifests: list[PluginManifest] = []
        self._all_skills: list[SkillDefinition] = []
        self._all_rules_parts: list[str] = []
        self._all_tools: list[ToolSpec] = []
        self._all_subagents: list[SubagentSpec] = []
        self._all_adapters: dict[str, AdapterSpec] = {}
        self._all_mcp: list[McpServerSpec] = []
        self._all_commands: list[CommandSpec] = []
        self._mcp_manager: Any = None
        self._runtime: Any = None
        self._registry: ToolRegistry | None = None
        self._model: str = ""
        self._max_tokens: int = 2048
        self._tool_output_limit: int = 4000
        self._on_tool_call: Any = None

    def discover_and_load(self, *search_dirs: Path) -> None:
        manifests = discover_plugins(*search_dirs)
        for manifest in manifests:
            if self._is_disabled(manifest):
                log.info(
                    "skipping_disabled_plugin",
                    name=manifest.name,
                    builtin=manifest.builtin,
                )
                continue
            self._load_one(manifest)

    def _is_disabled(self, manifest: PluginManifest) -> bool:
        if not manifest.builtin or self.settings is None:
            return False
        disabled = getattr(self.settings, "disabled_builtin_plugins", [])
        return manifest.name in set(disabled or [])

    def _load_one(self, manifest: PluginManifest) -> None:
        plugin_dir = Path(str(manifest.plugin_dir))
        log.info("loading_plugin", name=manifest.name, builtin=manifest.builtin)

        skills_dir = plugin_dir / "skills"
        if skills_dir.is_dir():
            self._all_skills.extend(load_skills(skills_dir))

        rules_dir = plugin_dir / "rules"
        if rules_dir.is_dir():
            text = load_rules_text(rules_dir)
            if text:
                self._all_rules_parts.append(text)

        ctx = load_plugin_entries(manifest, self.hooks, settings=self.settings)
        self._all_tools.extend(ctx.tools)
        self._all_subagents.extend(ctx.subagents)
        for adapter in ctx.adapters:
            self._all_adapters[adapter.name] = adapter
        self._all_mcp.extend(ctx.mcp_servers)
        self._all_commands.extend(ctx.commands)

        self._manifests.append(manifest)

    def setup_tools(self, registry: ToolRegistry) -> None:
        self._registry = registry
        locale = resolve_locale(getattr(self.settings, "locale", None))
        for tool in self._all_tools:
            try:
                registry.register(localize_tool_spec(tool, locale=locale))
            except ValueError:
                log.warning("duplicate_tool", name=tool.name)

        for spec in self._all_subagents:
            tool = self._build_delegation_tool(spec)
            try:
                registry.register(tool)
            except ValueError:
                log.warning("duplicate_delegation_tool", name=tool.name)

        if self._all_skills:
            skill_names = [s.name for s in self._all_skills]
            registry.register(
                localize_tool_spec(
                    ToolSpec(
                        name="read_skill",
                        description=(
                            "Load a skill's full instructions into context. "
                            "Use when a task matches a skill's description from the catalog."
                        ),
                        description_key="prompt.available_skills.read_skill.description",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description_key": "prompt.available_skills.read_skill.name",
                                    "enum": skill_names,
                                },
                            },
                            "required": ["name"],
                        },
                        handler=self._read_skill_handler,
                        readonly=True,
                        action_class="read_local",
                        idempotent=True,
                        risk_hint="low",
                        requires_receipt=False,
                        result_is_internal_context=True,
                    ),
                    locale=locale,
                )
            )

        self.hooks.fire(HookEvent.REGISTER_TOOLS, registry=registry)

    @property
    def all_commands(self) -> list[CommandSpec]:
        return list(self._all_commands)

    def _read_skill_handler(self, payload: dict[str, Any]) -> str:
        name = str(payload.get("name", ""))
        locale = resolve_locale(getattr(self.settings, "locale", None))
        for skill in self._all_skills:
            if skill.name == name:
                return f'<skill_content name="{name}">\n{skill.content}\n</skill_content>'
        available = ", ".join(s.name for s in self._all_skills)
        return tr(
            "prompt.available_skills.read_skill.not_found",
            locale=locale,
            default=f"Skill '{name}' not found. Available: {available}",
            name=name,
            available=available,
        )

    def configure_subagent_runtime(
        self, runtime: AgentRuntime, on_tool_call: ToolCallback | None = None
    ) -> None:
        self._runtime = runtime
        self._model = runtime.model
        self._max_tokens = runtime.max_tokens
        self._tool_output_limit = runtime.tool_output_limit
        self._on_tool_call = on_tool_call

    def build_system_prompt(
        self,
        base_prompt: str,
        preloaded_skills: list[str] | None = None,
    ) -> str:
        locale = resolve_locale(getattr(self.settings, "locale", None))
        parts: list[str] = [base_prompt]

        if self._all_rules_parts:
            combined = "\n\n".join(self._all_rules_parts)
            parts.append(f"<rules_context>\n{combined}\n</rules_context>")

        preloaded = set(preloaded_skills or [])
        catalog_skills = [s for s in self._all_skills if s.name not in preloaded]

        for skill in self._all_skills:
            if skill.name in preloaded:
                parts.append(
                    f'<skill_content name="{skill.name}">\n{skill.content}\n</skill_content>'
                )

        if catalog_skills:
            lines = [
                "<available_skills>",
                tr("prompt.available_skills.intro", locale=locale),
                tr("prompt.available_skills.guidance", locale=locale),
                "",
            ]
            for skill in catalog_skills:
                lines.append(f'  <skill name="{skill.name}">{skill.description}</skill>')
            lines.append("</available_skills>")
            parts.append("\n".join(lines))

        for fragment in self.hooks.fire(HookEvent.SYSTEM_PROMPT):
            if fragment:
                parts.append(str(fragment))

        return "\n\n".join(p for p in parts if p)

    def on_session_start(self, session_id: str) -> None:
        self.hooks.fire(HookEvent.SESSION_START, session_id=session_id)

    def on_session_end(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        self.hooks.fire(HookEvent.SESSION_END, session_id=session_id, messages=messages)

    def setup_commands(self, runner: _RunnerCommandHost) -> None:
        """Inject plugin-registered commands into the runner instance."""
        for spec in self._all_commands:
            runner.add_command(spec.name, spec.handler, spec.help_text, spec.cli_only)

    def on_pre_run(self, prompt: str, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        """Fire PRE_RUN hooks.

        Hooks may return:
        - str: replaces the prompt (backward compatible)
        - dict: {"prompt": "...", ...} to replace prompt AND pass control
          signals (e.g. disable_tools=True) to the runner.
        """
        run_opts: dict[str, Any] = {}
        results = self.hooks.fire(HookEvent.PRE_RUN, prompt=prompt, **kwargs)
        for result in results:
            if isinstance(result, str):
                prompt = result
            elif isinstance(result, dict):
                result_map = cast(dict[str, Any], result)
                prompt_value = result_map.get("prompt")
                if prompt_value is not None:
                    prompt = str(prompt_value)
                run_opts.update(
                    {key: value for key, value in result_map.items() if key != "prompt"}
                )
        return prompt, run_opts

    def on_post_run(self, result: Any, **kwargs: Any) -> None:
        self.hooks.fire(HookEvent.POST_RUN, result=result, **kwargs)

    def get_adapter(self, name: str) -> AdapterProtocol:
        """Instantiate a registered adapter by name."""
        spec = self._all_adapters.get(name)
        if spec is None:
            available = list(self._all_adapters.keys()) or ["(none)"]
            raise KeyError(f"Adapter '{name}' not found. Available: {', '.join(available)}")
        return spec.factory(self.settings)

    def list_adapters(self) -> list[str]:
        return list(self._all_adapters.keys())

    @property
    def manifests(self) -> list[PluginManifest]:
        return list(self._manifests)

    def _build_delegation_tool(self, spec: SubagentSpec) -> ToolSpec:
        locale = resolve_locale(getattr(self.settings, "locale", None))

        def handler(payload: dict[str, Any]) -> str:
            return self._run_subagent(spec, str(payload.get("task", "")))

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
                readonly=True,
                action_class="delegate_reasoning",
                idempotent=True,
                risk_hint="low",
                requires_receipt=False,
            ),
            locale=locale,
        )

    def _run_subagent(self, spec: SubagentSpec, task: str) -> str:
        if not self._runtime or not self._registry:
            locale = resolve_locale(getattr(self.settings, "locale", None))
            return tr(
                "prompt.delegation.unavailable",
                locale=locale,
                default=f"[Subagent '{spec.name}' unavailable: agent runner not configured]",
                name=spec.name,
            )

        import sys

        DIM = "\033[2m"
        MAGENTA = "\033[35m"
        GREEN = "\033[32m"
        RED = "\033[31m"
        RESET = "\033[0m"

        sub_registry = ToolRegistry()
        available: list[str] = []
        for tool_name in spec.tools:
            try:
                sub_registry.register(self._registry.get(tool_name))
                available.append(tool_name)
            except KeyError:
                log.warning("subagent_tool_not_found", subagent=spec.name, tool=tool_name)

        sub_agent = self._runtime.clone(
            registry=sub_registry,
            model=spec.model or self._model,
            max_turns=15,
            system_prompt=spec.system_prompt,
        )

        task_preview = task[:80] + ("..." if len(task) > 80 else "")
        tools_str = ", ".join(available)
        sys.stderr.write(
            f"\n{MAGENTA}  ┌─ subagent:{spec.name} "
            f"{DIM}[tools: {tools_str}]{RESET}\n"
            f"{MAGENTA}  │{RESET} {DIM}{task_preview}{RESET}\n"
        )
        sys.stderr.flush()

        def _sub_tool_call(name: str, inputs: dict[str, Any], result: object) -> None:
            compact = ", ".join(f"{k}={repr(v)[:50]}" for k, v in inputs.items())
            text = result if isinstance(result, str) else str(result)
            preview = text[:150].replace("\n", " ")
            if len(text) > 150:
                preview += "..."
            sys.stderr.write(f"{MAGENTA}  │{RESET}   ▸ {name}({compact})\n")
            sys.stderr.write(f"{MAGENTA}  │{RESET}   {DIM}→ {preview}{RESET}\n")
            sys.stderr.flush()

        callback: ToolCallback = self._on_tool_call or _sub_tool_call

        try:
            result = sub_agent.run(task, on_tool_call=callback, readonly_only=True)
            sys.stderr.write(
                f"{MAGENTA}  └─{RESET} {GREEN}done{RESET} "
                f"{DIM}({result.turns} turns, {result.tool_calls} tool calls){RESET}\n\n"
            )
            sys.stderr.flush()
            return result.text
        except Exception as exc:
            sys.stderr.write(f"{MAGENTA}  └─{RESET} {RED}error: {exc}{RESET}\n\n")
            sys.stderr.flush()
            return f"[Subagent '{spec.name}' error: {exc}]"

    # ── MCP lifecycle ──────────────────────────────────────────────

    @property
    def mcp_specs(self) -> list[McpServerSpec]:
        return list(self._all_mcp)

    def start_mcp_servers(self, registry: ToolRegistry) -> None:
        """Connect to all declared MCP servers, discover tools, register them."""
        if not self._all_mcp:
            return
        from hermit.runtime.capability.resolver.mcp_client import McpClientManager

        self._mcp_manager = McpClientManager()
        try:
            self._mcp_manager.connect_all_sync(self._all_mcp)
        except Exception:
            log.exception("mcp_startup_error")
        for tool in self._mcp_manager.get_tool_specs():
            try:
                registry.register(tool)
            except ValueError:
                log.warning("mcp_duplicate_tool", name=tool.name)

    def stop_mcp_servers(self) -> None:
        """Disconnect all MCP servers and clean up."""
        if self._mcp_manager is not None:
            self._mcp_manager.close_all_sync()
            self._mcp_manager = None
