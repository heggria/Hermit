from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import structlog

from hermit.infra.system.i18n import resolve_locale
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
from hermit.runtime.capability.registry.skill_loader import SkillLoader
from hermit.runtime.capability.registry.subagent_executor import SubagentExecutor
from hermit.runtime.capability.registry.system_prompt_builder import SystemPromptBuilder
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
        self._registry: ToolRegistry | None = None

        # Extracted delegates -- created with only the fields needed for tool
        # building.  Runtime dependencies are injected later via
        # configure_subagent_runtime() once the AgentRuntime is available.
        self._subagent_executor = SubagentExecutor(
            hooks=self.hooks,
            settings=settings,
        )

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
            tool = self._subagent_executor.build_delegation_tool(spec)
            try:
                registry.register(tool)
            except ValueError:
                log.warning("duplicate_delegation_tool", name=tool.name)

        skill_loader = SkillLoader(all_skills=self._all_skills, settings=self.settings)
        skill_loader.register_skill_tool(registry)

        self.hooks.fire(HookEvent.REGISTER_TOOLS, registry=registry)

    def _read_skill_handler(self, payload: dict[str, Any]) -> str:
        """Backward-compatible delegate to SkillLoader."""
        loader = SkillLoader(all_skills=self._all_skills, settings=self.settings)
        return loader._read_skill_handler(payload)

    @property
    def all_commands(self) -> list[CommandSpec]:
        return list(self._all_commands)

    def configure_subagent_runtime(
        self, runtime: AgentRuntime, on_tool_call: ToolCallback | None = None
    ) -> None:
        self._subagent_executor.configure_runtime(
            runtime=runtime,
            registry=self._registry,
            on_tool_call=on_tool_call,
        )

    @property
    def _prompt_builder(self) -> SystemPromptBuilder:
        """Return a cached SystemPromptBuilder, lazily created once."""
        builder = getattr(self, "_cached_prompt_builder", None)
        if builder is None:
            builder = SystemPromptBuilder(
                all_skills=self._all_skills,
                all_rules_parts=self._all_rules_parts,
                hooks=self.hooks,
                settings=self.settings,
            )
            self._cached_prompt_builder = builder
        return builder

    def build_system_prompt(
        self,
        base_prompt: str,
        preloaded_skills: list[str] | None = None,
    ) -> str:
        return self._prompt_builder.build_system_prompt(
            base_prompt, preloaded_skills=preloaded_skills
        )

    def on_session_start(self, session_id: str, *, runner: Any = None) -> None:
        self.hooks.fire(HookEvent.SESSION_START, session_id=session_id, runner=runner)

    def on_session_end(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        self.hooks.fire(HookEvent.SESSION_END, session_id=session_id, messages=messages)

    def setup_commands(self, runner: _RunnerCommandHost) -> None:
        """Inject plugin-registered commands into the runner instance."""
        for spec in self._all_commands:
            runner.add_command(spec.name, spec.handler, spec.help_text, spec.cli_only)

    # Allowed keys that PRE_RUN hook dict results may contain (besides "prompt").
    # Unknown keys are logged as warnings and dropped to prevent hooks from
    # injecting arbitrary control signals into the runner.
    _ALLOWED_RUN_OPTS = frozenset(
        {
            "prompt",
            "disable_tools",
            "planning_mode",
            "readonly_only",
            "policy_profile",
        }
    )

    def on_pre_run(self, prompt: str, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        """Fire PRE_RUN hooks.

        Hooks may return:
        - str: replaces the prompt (backward compatible)
        - dict: {"prompt": "...", ...} to replace prompt AND pass control
          signals (e.g. disable_tools=True) to the runner.

        Dict return values are validated against ``_ALLOWED_RUN_OPTS``.
        Unknown keys are logged and dropped.
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

                unknown_keys = set(result_map.keys()) - self._ALLOWED_RUN_OPTS
                if unknown_keys:
                    log.warning(
                        "pre_run_hook_unknown_keys",
                        unknown_keys=sorted(unknown_keys),
                        allowed_keys=sorted(self._ALLOWED_RUN_OPTS - {"prompt"}),
                    )

                run_opts.update(
                    {
                        key: value
                        for key, value in result_map.items()
                        if key != "prompt" and key in self._ALLOWED_RUN_OPTS
                    }
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
