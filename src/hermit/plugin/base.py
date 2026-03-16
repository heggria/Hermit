from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Protocol

if TYPE_CHECKING:
    from hermit.core.runner import AgentRunner
    from hermit.plugin.hooks import HooksEngine


CommandHandler = Callable[["AgentRunner", str, str], Any]


class HookEvent(str, Enum):
    SYSTEM_PROMPT = "system_prompt"
    REGISTER_TOOLS = "register_tools"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    PRE_RUN = "pre_run"
    POST_RUN = "post_run"
    SERVE_START = "serve_start"
    SERVE_STOP = "serve_stop"
    DISPATCH_RESULT = "dispatch_result"

    def __str__(self) -> str:
        return self.value


@dataclass
class SubagentSpec:
    name: str
    description: str
    system_prompt: str
    tools: List[str] = field(default_factory=list[str])
    model: str = ""


@dataclass(frozen=True)
class McpToolGovernance:
    action_class: str
    risk_hint: str
    requires_receipt: bool
    readonly: bool = False
    supports_preview: bool = False

    def __post_init__(self) -> None:
        action_class = str(self.action_class or "").strip()
        if not action_class:
            raise ValueError("MCP tool governance must declare action_class.")
        risk_hint = str(self.risk_hint or "").strip()
        if not risk_hint:
            raise ValueError("MCP tool governance must declare risk_hint.")
        if self.readonly and self.requires_receipt:
            raise ValueError("Readonly MCP tools cannot require receipts.")


@dataclass
class McpServerSpec:
    """Describes an MCP server to connect to."""

    name: str
    description: str
    transport: str  # "stdio" | "http"
    command: Optional[List[str]] = None
    env: Optional[dict[str, str]] = None
    url: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    allowed_tools: Optional[List[str]] = None
    tool_governance: dict[str, McpToolGovernance] = field(
        default_factory=dict[str, McpToolGovernance]
    )


@dataclass
class CommandSpec:
    """Describes a slash command registered by a plugin."""

    name: str
    help_text: str
    handler: CommandHandler
    cli_only: bool = False


@dataclass
class PluginVariableSpec:
    name: str
    setting: Optional[str] = None
    env: List[str] = field(default_factory=list[str])
    default: Any = None
    required: bool = False
    secret: bool = False
    description: str = ""


@dataclass
class AdapterSpec:
    """Describes an adapter that bridges an external messaging platform to Hermit."""

    name: str
    description: str
    factory: Callable[..., AdapterProtocol]


class AdapterProtocol(Protocol):
    """Interface that every message-channel adapter must implement."""

    @property
    def required_skills(self) -> List[str]:
        """Skill names to preload into system prompt at adapter startup.

        Equivalent to Claude Code subagent's ``skills`` field — the full
        content of each listed skill is injected into the agent's system
        prompt rather than requiring on-demand ``read_skill`` activation.
        """
        return []

    async def start(self, runner: AgentRunner) -> None:
        """Start listening for external messages (blocking)."""
        ...

    async def stop(self) -> None:
        """Gracefully shut down the adapter."""
        ...


@dataclass
class PluginManifest:
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    builtin: bool = False
    entry: dict[str, str] = field(default_factory=dict[str, str])
    config: dict[str, Any] = field(default_factory=dict[str, Any])
    variables: dict[str, PluginVariableSpec] = field(default_factory=dict[str, PluginVariableSpec])
    dependencies: List[str] = field(default_factory=list[str])
    plugin_dir: Optional[Any] = None


class PluginContext:
    """Registration handle given to each plugin during its register() call."""

    def __init__(self, hooks_engine: HooksEngine, settings: Any = None) -> None:
        self._hooks = hooks_engine
        self.settings = settings
        self.manifest: PluginManifest | None = None
        self.plugin_vars: dict[str, Any] = {}
        self.config: dict[str, Any] = {}
        self.tools: list[Any] = []
        self.subagents: list[SubagentSpec] = []
        self.adapters: list[AdapterSpec] = []
        self.mcp_servers: list[McpServerSpec] = []
        self.commands: list[CommandSpec] = []

    def add_hook(
        self,
        event: HookEvent,
        handler: Callable[..., Any],
        priority: int = 0,
    ) -> None:
        self._hooks.register(event, handler, priority)

    def add_tool(self, tool: Any) -> None:
        self.tools.append(tool)

    def add_subagent(self, spec: SubagentSpec) -> None:
        self.subagents.append(spec)

    def add_adapter(self, spec: AdapterSpec) -> None:
        self.adapters.append(spec)

    def add_mcp(self, spec: McpServerSpec) -> None:
        self.mcp_servers.append(spec)

    def add_command(self, spec: CommandSpec) -> None:
        self.commands.append(spec)

    def get_var(self, name: str, default: Any = None) -> Any:
        return self.plugin_vars.get(name, default)
