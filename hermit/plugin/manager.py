from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

from hermit.core.tools import ToolRegistry, ToolSpec
from hermit.plugin.base import (
    AdapterProtocol,
    AdapterSpec,
    CommandSpec,
    HookEvent,
    McpServerSpec,
    PluginManifest,
    SubagentSpec,
)
from hermit.plugin.hooks import HooksEngine
from hermit.plugin.loader import discover_plugins, load_plugin_entries
from hermit.plugin.rules import load_rules_text
from hermit.plugin.skills import SkillDefinition, load_skills

log = structlog.get_logger()


class PluginManager:
    def __init__(self, settings: Any = None) -> None:
        self.hooks = HooksEngine()
        self.settings = settings
        self._manifests: List[PluginManifest] = []
        self._all_skills: List[SkillDefinition] = []
        self._all_rules_parts: List[str] = []
        self._all_tools: List[ToolSpec] = []
        self._all_subagents: List[SubagentSpec] = []
        self._all_adapters: Dict[str, AdapterSpec] = {}
        self._all_mcp: List[McpServerSpec] = []
        self._all_commands: List[CommandSpec] = []
        self._mcp_manager: Any = None
        self._runtime: Any = None
        self._registry: Optional[ToolRegistry] = None
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
        plugin_dir = Path(manifest.plugin_dir)
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
        for tool in self._all_tools:
            try:
                registry.register(tool)
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
            registry.register(ToolSpec(
                name="read_skill",
                description=(
                    "Load a skill's full instructions into context. "
                    "Use when a task matches a skill's description from the catalog."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Skill name from the available_skills catalog",
                            "enum": skill_names,
                        },
                    },
                    "required": ["name"],
                },
                handler=self._read_skill_handler,
            ))

        self.hooks.fire(HookEvent.REGISTER_TOOLS, registry=registry)

    def _read_skill_handler(self, payload: dict) -> str:
        name = str(payload.get("name", ""))
        for skill in self._all_skills:
            if skill.name == name:
                return f'<skill_content name="{name}">\n{skill.content}\n</skill_content>'
        available = ", ".join(s.name for s in self._all_skills)
        return f"Skill '{name}' not found. Available: {available}"

    def configure_subagent_runtime(self, runtime: Any, on_tool_call: Any = None) -> None:
        self._runtime = runtime
        self._model = runtime.model
        self._max_tokens = runtime.max_tokens
        self._tool_output_limit = runtime.tool_output_limit
        self._on_tool_call = on_tool_call

    def build_system_prompt(
        self,
        base_prompt: str,
        preloaded_skills: Optional[List[str]] = None,
    ) -> str:
        parts: List[str] = [base_prompt]

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
                "The following skills provide specialized instructions.",
                "When a task matches a skill's description, call read_skill to load its full instructions.",
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

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        self.hooks.fire(HookEvent.SESSION_END, session_id=session_id, messages=messages)

    def setup_commands(self, runner: Any) -> None:
        """Inject plugin-registered commands into the runner instance."""
        for spec in self._all_commands:
            runner.add_command(spec.name, spec.handler, spec.help_text, spec.cli_only)

    def on_pre_run(self, prompt: str, **kwargs: Any) -> tuple[str, Dict[str, Any]]:
        """Fire PRE_RUN hooks.

        Hooks may return:
        - str: replaces the prompt (backward compatible)
        - dict: {"prompt": "...", ...} to replace prompt AND pass control
          signals (e.g. disable_tools=True) to the runner.
        """
        run_opts: Dict[str, Any] = {}
        results = self.hooks.fire(HookEvent.PRE_RUN, prompt=prompt, **kwargs)
        for result in results:
            if isinstance(result, str):
                prompt = result
            elif isinstance(result, dict):
                if "prompt" in result:
                    prompt = result.pop("prompt")
                run_opts.update(result)
        return prompt, run_opts

    def on_post_run(self, result: Any, **kwargs: Any) -> None:
        self.hooks.fire(HookEvent.POST_RUN, result=result, **kwargs)

    def get_adapter(self, name: str) -> AdapterProtocol:
        """Instantiate a registered adapter by name."""
        spec = self._all_adapters.get(name)
        if spec is None:
            available = list(self._all_adapters.keys()) or ["(none)"]
            raise KeyError(
                f"Adapter '{name}' not found. Available: {', '.join(available)}"
            )
        return spec.factory(self.settings)

    def list_adapters(self) -> List[str]:
        return list(self._all_adapters.keys())

    @property
    def manifests(self) -> List[PluginManifest]:
        return list(self._manifests)

    def _build_delegation_tool(self, spec: SubagentSpec) -> ToolSpec:
        def handler(payload: dict[str, Any]) -> str:
            return self._run_subagent(spec, str(payload.get("task", "")))

        return ToolSpec(
            name=f"delegate_{spec.name}",
            description=f"Delegate a task to the {spec.name} subagent. {spec.description}",
            input_schema={
                "type": "object",
                "properties": {"task": {"type": "string", "description": "Task description"}},
                "required": ["task"],
            },
            handler=handler,
        )

    def _run_subagent(self, spec: SubagentSpec, task: str) -> str:
        if not self._runtime or not self._registry:
            return f"[Subagent '{spec.name}' unavailable: agent runner not configured]"

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

        def _sub_tool_call(name: str, inputs: dict, result: object) -> None:
            compact = ", ".join(f"{k}={repr(v)[:50]}" for k, v in inputs.items())
            text = result if isinstance(result, str) else str(result)
            preview = text[:150].replace("\n", " ")
            if len(text) > 150:
                preview += "..."
            sys.stderr.write(f"{MAGENTA}  │{RESET}   ▸ {name}({compact})\n")
            sys.stderr.write(f"{MAGENTA}  │{RESET}   {DIM}→ {preview}{RESET}\n")
            sys.stderr.flush()

        callback = self._on_tool_call or _sub_tool_call

        try:
            result = sub_agent.run(task, on_tool_call=callback)
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
    def mcp_specs(self) -> List[McpServerSpec]:
        return list(self._all_mcp)

    def start_mcp_servers(self, registry: ToolRegistry) -> None:
        """Connect to all declared MCP servers, discover tools, register them."""
        if not self._all_mcp:
            return
        from hermit.plugin.mcp_client import McpClientManager
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
