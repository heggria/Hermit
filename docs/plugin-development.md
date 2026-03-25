# Plugin Development Guide

Hermit plugins are the equivalent of kernel modules and system services in a
traditional OS. They extend the kernel's capabilities without modifying core
code -- just like device drivers, filesystem modules, and network protocols
extend Linux.

This guide covers everything you need to create, test, and ship Hermit plugins.
By the end of the Quick Start section you will have a working tool plugin
running inside Hermit.

## Table of Contents

- [Plugin Architecture Overview](#plugin-architecture-overview)
- [Plugin Structure](#plugin-structure)
- [Quick Start: Your First Kernel Module](#quick-start-your-first-kernel-module)
- [Plugin Types Deep Dive](#plugin-types-deep-dive)
  - [Tools](#tools)
  - [Hooks](#hooks)
  - [Adapters](#adapters)
  - [Commands](#commands)
  - [MCP](#mcp)
  - [Subagents](#subagents)
- [Plugin Variables](#plugin-variables)
- [Discovery and Loading](#discovery-and-loading)
- [Skills and Rules](#skills-and-rules)
- [Testing Plugins](#testing-plugins)
- [Best Practices](#best-practices)

---

## Plugin Architecture Overview

Hermit's plugin system supports six entry-point types. A single plugin can
register multiple entry points simultaneously (for example, a scheduler plugin
registers both `tools` and `hooks`).

| Type | OS Equivalent | Purpose | Entry key | Registration method |
|------|---------------|---------|-----------|---------------------|
| **Tools** | System calls / ioctl | Expose callable tools to the agent | `tools` | `ctx.add_tool(ToolSpec(...))` |
| **Hooks** | Kernel event handlers (netfilter, inotify) | React to lifecycle events (session start, post-run, serve start, etc.) | `hooks` | `ctx.add_hook(HookEvent.X, handler, priority)` |
| **Adapters** | Device drivers (TTY, network interface) | Bridge external messaging platforms (Slack, Feishu, Telegram) | `adapter` | `ctx.add_adapter(AdapterSpec(...))` |
| **Commands** | Shell builtins | Register slash commands (`/compact`, `/plan`) | `commands` | `ctx.add_command(CommandSpec(...))` |
| **MCP** | System service (D-Bus, systemd) | Connect to external MCP servers with governed tool routing | `mcp` | `ctx.add_mcp(McpServerSpec(...))` |
| **Subagents** | Child processes with delegation | Define delegatable sub-agents with scoped tools and system prompts | `subagents` | `ctx.add_subagent(SubagentSpec(...))` |

Every plugin lives in its own directory and declares its metadata in a
`plugin.toml` manifest. Hermit discovers plugins at startup by scanning two
locations in order:

1. **Builtin plugins** -- `src/hermit/plugins/builtin/`
2. **User plugins** -- `~/.hermit/plugins/`

---

## Plugin Structure

A minimal plugin is a directory with a `plugin.toml` and one or more Python
modules referenced by the entry points.

```
my-plugin/
├── plugin.toml          # Manifest (required)
├── tools.py             # Tool registration (if entry.tools is set)
├── hooks.py             # Hook registration (if entry.hooks is set)
├── commands.py          # Command registration (if entry.commands is set)
├── mcp.py               # MCP server registration (if entry.mcp is set)
├── adapter.py           # Adapter registration (if entry.adapter is set)
├── subagents.py         # Subagent registration (if entry.subagents is set)
├── skills/              # Optional skill definitions
│   └── my-skill/
│       └── SKILL.md
└── rules/               # Optional rule files
    └── conventions.md
```

### plugin.toml Format

```toml
[plugin]
name = "my-plugin"              # Unique plugin name (required)
version = "0.1.0"               # Semver version (required)
description = "What it does"    # Human-readable description
author = "Your Name"            # Optional author
builtin = false                 # true only for plugins shipped with Hermit

[entry]
tools = "tools:register"        # "module:function" — called with PluginContext
hooks = "hooks:register"        # Multiple entry types allowed
commands = "commands:register"

[variables.my_api_key]          # Configurable variables (see Plugin Variables)
env = ["MY_API_KEY"]
secret = true
required = true
description = "API key for the service"

[variables.max_results]
env = ["MY_MAX_RESULTS"]
default = 10
description = "Maximum results to return"
```

Each `[entry]` value follows the pattern `"module_name:function_name"`. Hermit
imports the Python module from the plugin directory and calls the function with
a `PluginContext` instance.

---

## Quick Start: Your First Kernel Module

Your first kernel module -- a simple tool plugin that registers a new system
call. Let's build a `timestamp` tool plugin that returns the current UTC
timestamp. This takes about 5 minutes.

### Step 1: Create the plugin directory

```bash
mkdir -p ~/.hermit/plugins/timestamp
```

### Step 2: Write the manifest

Create `~/.hermit/plugins/timestamp/plugin.toml`:

```toml
[plugin]
name = "timestamp"
version = "0.1.0"
description = "Returns the current UTC timestamp"

[entry]
tools = "tools:register"
```

### Step 3: Implement the tool

Create `~/.hermit/plugins/timestamp/tools.py`:

```python
"""Timestamp tool plugin."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.registry.tools import ToolSpec


def _handle_timestamp(payload: dict[str, Any]) -> str:
    fmt = str(payload.get("format", "iso"))
    now = datetime.now(timezone.utc)
    if fmt == "unix":
        return str(int(now.timestamp()))
    if fmt == "date":
        return now.strftime("%Y-%m-%d")
    return now.isoformat()


def register(ctx: PluginContext) -> None:
    ctx.add_tool(
        ToolSpec(
            name="timestamp",
            description=(
                "Return the current UTC timestamp. "
                "Supports iso, unix, and date formats."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["iso", "unix", "date"],
                        "description": "Output format: iso (default), unix, or date",
                    },
                },
                "required": [],
            },
            handler=_handle_timestamp,
            readonly=True,               # No side effects
            action_class="read_local",   # Governance: what kind of action
            idempotent=True,             # Same input always same output
            risk_hint="low",             # Risk level
            requires_receipt=False,      # Readonly tools must set False
        )
    )
```

### Step 4: Verify it works

Start a Hermit session. The plugin is auto-discovered from `~/.hermit/plugins/`:

```bash
hermit chat
```

Then ask: "What is the current timestamp?" -- Hermit will use your new
`timestamp` tool.

You can also check that the plugin loaded:

```bash
hermit plugin list
```

---

## Plugin Types Deep Dive

### Tools

Tools are the most common plugin type. They expose callable functions to the
agent during governed execution.

#### ToolSpec fields

Every tool must declare governance metadata. This is enforced at registration
time -- missing fields raise `ToolGovernanceError`.

```python
@dataclass
class ToolSpec:
    name: str                          # Unique tool name
    description: str                   # Shown to the LLM
    input_schema: dict[str, Any]       # JSON Schema for parameters
    handler: ToolHandler               # Function(payload) -> result
    description_key: str | None        # i18n key (optional)
    readonly: bool                     # True = no side effects
    action_class: str | None           # "read_local", "write_local",
                                       # "execute_command", "network_read",
                                       # "external_mutation", etc.
    resource_scope_hint: str | list[str] | None  # Path or scope hint
    idempotent: bool                   # Retryable without side effects?
    risk_hint: str | None              # "low", "medium", "high", "critical"
    requires_receipt: bool | None      # Must produce an execution receipt?
    supports_preview: bool             # Can preview before execution?
```

**Governance validation rules:**

- Every tool must declare `action_class`.
- Readonly tools must set `requires_receipt=False`.
- Mutating tools (readonly=False) must declare both `risk_hint` and
  `requires_receipt`.

#### Example: Read-only tool (web search)

From the builtin `web-tools` plugin:

```python
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.registry.tools import ToolSpec


def register(ctx: PluginContext) -> None:
    ctx.add_tool(
        ToolSpec(
            name="web_search",
            description="Search the web and return results.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
            handler=handle_search,
            readonly=True,
            action_class="network_read",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        )
    )
```

#### Example: Mutating tool (file write)

Mutating tools require receipts and higher governance scrutiny:

```python
ctx.add_tool(
    ToolSpec(
        name="deploy_config",
        description="Write a configuration file to the deployment directory.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        handler=handle_deploy,
        readonly=False,
        action_class="write_local",
        risk_hint="high",
        requires_receipt=True,
        supports_preview=True,
    )
)
```

#### Contextual handlers

If your tool handler needs access to the current task context (e.g., to read
the parent task ID for spawning subtasks), use a two-argument signature. Hermit
detects this automatically via `inspect`:

```python
from hermit.kernel.context.models.context import TaskExecutionContext


def handle_with_context(payload: dict[str, Any], task_context: TaskExecutionContext) -> str:
    parent_id = task_context.task_id
    return f"Running under task {parent_id}"
```

---

### Hooks

Hooks let your plugin react to lifecycle events. They are the primary
extensibility mechanism for cross-cutting concerns like memory, scheduling,
and monitoring.

#### Available hook events

```python
from hermit.runtime.capability.contracts.base import HookEvent

class HookEvent(StrEnum):
    SYSTEM_PROMPT    = "system_prompt"      # Inject text into system prompt
    REGISTER_TOOLS   = "register_tools"     # Modify tool registration
    SESSION_START    = "session_start"      # New conversation started
    SESSION_END      = "session_end"        # Conversation ended
    PRE_RUN          = "pre_run"            # Before agent execution
    POST_RUN         = "post_run"           # After agent execution
    SERVE_START      = "serve_start"        # Server starting up
    SERVE_STOP       = "serve_stop"         # Server shutting down
    DISPATCH_RESULT  = "dispatch_result"    # Task dispatch completed
    SUBTASK_SPAWN    = "subtask_spawn"      # Child step created
    SUBTASK_COMPLETE = "subtask_complete"   # Child step finished
```

#### Registration

```python
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext


def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.POST_RUN, _on_post_run, priority=20)
    ctx.add_hook(HookEvent.SESSION_END, _on_session_end, priority=90)
```

#### Priority

Hooks with **lower priority numbers run first** (priority 10 runs before
priority 20). Use priorities to control execution order when multiple plugins
hook the same event.

Common priority ranges:
- **0-10** -- Infrastructure (scheduler, core services)
- **10-30** -- Standard hooks (memory injection, trigger analysis)
- **50+** -- Late hooks (cleanup, finalization)
- **90** -- End-of-lifecycle hooks

#### Signature-adaptive calling

The `HooksEngine` inspects each handler's signature and only passes keyword
arguments the handler accepts. This means your hook does not need to accept
every possible argument -- just declare what you need:

```python
# Accepts only the arguments it cares about -- extra kwargs are ignored
def _on_post_run(result, session_id: str = "", **kwargs):
    if result and hasattr(result, "messages"):
        analyze(result.messages)

# SYSTEM_PROMPT hooks return a string to inject
def _on_system_prompt(**kwargs) -> str:
    return "<custom-context>Your injected context here</custom-context>"
```

#### Example: Full hook plugin (trigger)

From the builtin `trigger` plugin:

```python
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext

_engine = None

def _on_serve_start(*, runner=None, **kw):
    if _engine is not None and runner is not None:
        _engine.set_runner(runner)

def _on_post_run(result, session_id: str = "", **kwargs):
    if _engine is None:
        return
    _engine.analyze_and_dispatch(result, session_id=session_id, **kwargs)

def register(ctx: PluginContext) -> None:
    global _engine
    enabled = bool(ctx.get_var("trigger_enabled", True))
    if not enabled:
        return
    _engine = TriggerEngine(
        cooldown_seconds=int(ctx.get_var("trigger_cooldown_seconds", 86400)),
        max_tasks_per_run=int(ctx.get_var("trigger_max_tasks_per_run", 3)),
    )
    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=25)
    ctx.add_hook(HookEvent.POST_RUN, _on_post_run, priority=30)
```

#### Example: Scheduler hooks (SERVE_START/SERVE_STOP lifecycle)

From the builtin `scheduler` plugin, showing the serve lifecycle and
hot-reload pattern:

```python
def _on_serve_start(*, settings, runner=None, reload_mode: bool = False, **kw):
    global _engine
    if reload_mode and _engine is not None:
        # Hot-swap: keep scheduler running, just update the runner reference
        _engine.set_runner(runner)
        return
    _engine = SchedulerEngine(settings, hooks_ref)
    if runner is not None:
        _engine.set_runner(runner)
    _engine.start(catch_up=bool(getattr(settings, "scheduler_catch_up", True)))

def _on_serve_stop(*, reload_mode: bool = False, **kw):
    global _engine
    if reload_mode:
        return  # Keep scheduler running during reload
    if _engine is not None:
        _engine.stop()
        _engine = None

def register(ctx: PluginContext) -> None:
    ctx.add_hook(HookEvent.SERVE_START, _on_serve_start, priority=10)
    ctx.add_hook(HookEvent.SERVE_STOP, _on_serve_stop, priority=10)
```

---

### Adapters

Adapters bridge external messaging platforms to Hermit. An adapter receives
messages from the platform, routes them through `AgentRunner.dispatch()`, and
sends responses back.

#### AdapterProtocol

Every adapter must implement the `AdapterProtocol`:

```python
class AdapterProtocol(Protocol):
    @property
    def required_skills(self) -> list[str]:
        """Skill names to preload into the system prompt."""
        return []

    async def start(self, runner: AgentRunner) -> None:
        """Start listening for messages (blocking)."""
        ...

    async def stop(self) -> None:
        """Gracefully shut down."""
        ...
```

#### Registration

```python
from hermit.runtime.capability.contracts.base import AdapterSpec, PluginContext


def register(ctx: PluginContext) -> None:
    ctx.add_adapter(
        AdapterSpec(
            name="slack",
            description="Slack messaging via Socket Mode",
            factory=SlackAdapter,  # Class or callable that returns AdapterProtocol
        )
    )
```

The `factory` is called when `hermit serve slack` starts. It receives
`settings` as an argument.

#### Example: Minimal adapter

```python
import asyncio
from typing import Any

from hermit.runtime.capability.contracts.base import AdapterSpec


class MyAdapter:
    @property
    def required_skills(self) -> list[str]:
        return []

    def __init__(self, settings: Any = None) -> None:
        self._runner = None
        self._stopped = False

    async def start(self, runner) -> None:
        self._runner = runner
        # Your message-receiving loop here
        while not self._stopped:
            message = await self._poll_for_messages()
            if message and self._runner:
                result = await asyncio.to_thread(
                    self._runner.dispatch, message.session_id, message.text
                )
                if result and result.text:
                    await self._send_reply(message, result.text)

    async def stop(self) -> None:
        self._stopped = True


def register(ctx) -> None:
    ctx.add_adapter(
        AdapterSpec(
            name="my-platform",
            description="My messaging platform adapter",
            factory=MyAdapter,
        )
    )
```

Key points:
- `runner.dispatch()` is synchronous -- use `asyncio.to_thread()` to call it
  from async code.
- Build session IDs that map to conversations (e.g., `"slack:{channel}:{user}"`).
- Handle deduplication of messages yourself.
- Close sessions on shutdown so `SESSION_END` hooks fire properly.

---

### Commands

Commands are slash commands available in interactive sessions (`hermit chat`
and adapters). They receive the runner, session ID, and raw input text.

#### CommandSpec fields

```python
@dataclass
class CommandSpec:
    name: str              # Must start with "/" (e.g., "/compact")
    help_text: str         # Shown in /help (can be an i18n key)
    handler: CommandHandler # Function(runner, session_id, text) -> DispatchResult
    cli_only: bool         # If True, only available in CLI mode
```

#### Example: Simple command

From the builtin `compact` plugin:

```python
from hermit.runtime.capability.contracts.base import CommandSpec


def _cmd_compact(runner, session_id: str, _text: str):
    from hermit.runtime.control.runner.runner import DispatchResult
    # ... perform compaction ...
    return DispatchResult("Context compacted.", is_command=True)


def register(ctx) -> None:
    ctx.add_command(
        CommandSpec(
            name="/compact",
            help_text="Compress session context via LLM summary",
            handler=_cmd_compact,
        )
    )
```

#### Example: Command with subcommands

From the builtin `planner` plugin:

```python
def _cmd_plan(runner, session_id: str, text: str):
    parts = text.strip().split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""
    if subcommand == "confirm":
        return runner.dispatch_control_action(session_id, action="plan_confirm", target_id="")
    if subcommand == "off":
        return runner.dispatch_control_action(session_id, action="plan_exit", target_id="")
    return runner.dispatch_control_action(session_id, action="plan_enter", target_id="")


def register(ctx) -> None:
    ctx.add_command(
        CommandSpec(name="/plan", help_text="Enter/exit planning mode", handler=_cmd_plan)
    )
```

---

### MCP

MCP plugins connect Hermit to external MCP (Model Context Protocol) servers.
Each tool exposed by the MCP server goes through Hermit's governance pipeline.

#### McpServerSpec fields

```python
@dataclass
class McpServerSpec:
    name: str                                    # Server name
    description: str                             # Human-readable description
    transport: str                               # "stdio" or "http"
    command: list[str] | None                    # For stdio: command + args
    env: dict[str, str] | None                   # For stdio: environment vars
    url: str | None                              # For http: server URL
    headers: dict[str, str] | None               # For http: request headers
    allowed_tools: list[str] | None              # Whitelist (None = allow all)
    tool_governance: dict[str, McpToolGovernance] # Per-tool governance rules
```

#### McpToolGovernance

Every MCP tool needs governance metadata to pass through the policy engine:

```python
@dataclass(frozen=True)
class McpToolGovernance:
    action_class: str         # Required: "network_read", "external_mutation", etc.
    risk_hint: str            # Required: "low", "medium", "high", "critical"
    requires_receipt: bool    # Required: True for mutations
    readonly: bool            # Default False
    supports_preview: bool    # Default False
```

#### Example: HTTP transport (GitHub MCP)

From the builtin `github` plugin:

```python
from hermit.runtime.capability.contracts.base import McpServerSpec, McpToolGovernance, PluginContext

_READ_TOOLS = {"get_file_contents", "list_issues", "search_code", ...}
_MUTATION_TOOLS = {"create_pull_request", "push_files", ...}

_GOVERNANCE = {
    **{name: McpToolGovernance(
        action_class="network_read", risk_hint="low",
        requires_receipt=False, readonly=True,
    ) for name in _READ_TOOLS},
    **{name: McpToolGovernance(
        action_class="external_mutation", risk_hint="high",
        requires_receipt=True,
    ) for name in _MUTATION_TOOLS},
}

def register(ctx: PluginContext) -> None:
    token = str(ctx.get_var("github_pat", "") or "").strip()
    url = str(ctx.config.get("url", "") or "").strip() or DEFAULT_URL

    ctx.add_mcp(McpServerSpec(
        name="github",
        description="GitHub MCP server",
        transport="http",
        url=url,
        headers={"Authorization": f"Bearer {token}"} if token else None,
        allowed_tools=sorted(_GOVERNANCE),
        tool_governance=dict(_GOVERNANCE),
    ))
```

#### Example: Stdio transport

For MCP servers launched as child processes:

```python
def register(ctx: PluginContext) -> None:
    ctx.add_mcp(McpServerSpec(
        name="my-mcp-server",
        description="My custom MCP server",
        transport="stdio",
        command=["npx", "-y", "my-mcp-package"],
        env={"API_KEY": str(ctx.get_var("my_api_key", ""))},
        tool_governance={
            "my_read_tool": McpToolGovernance(
                action_class="network_read", risk_hint="low",
                requires_receipt=False, readonly=True,
            ),
            "my_write_tool": McpToolGovernance(
                action_class="external_mutation", risk_hint="high",
                requires_receipt=True,
            ),
        },
    ))
```

#### Alternative: .mcp.json files

The builtin `mcp-loader` plugin also loads MCP server configurations from JSON
files compatible with Claude Code / Cursor:

- `~/.hermit/mcp.json` -- global configuration
- `<project>/.mcp.json` -- project-level configuration

```json
{
  "mcpServers": {
    "my-server": {
      "command": "npx",
      "args": ["-y", "my-mcp-package"],
      "env": {"API_KEY": "..."}
    },
    "remote-server": {
      "url": "https://api.example.com/mcp/",
      "headers": {"Authorization": "Bearer ..."},
      "toolGovernance": {
        "read_data": {
          "actionClass": "network_read",
          "riskHint": "low",
          "requiresReceipt": false,
          "readonly": true
        }
      }
    }
  }
}
```

---

### Subagents

Subagents define specialized agents that the main agent can delegate tasks to.
They get their own system prompt and scoped tool access.

#### SubagentSpec fields

```python
@dataclass
class SubagentSpec:
    name: str                  # Subagent name (generates delegate_{name} tool)
    description: str           # What this subagent does
    system_prompt: str         # System prompt for the subagent
    tools: list[str]           # Tools available to the subagent
    model: str                 # Override model (empty = use default)
    policy_profile: str        # "readonly", "default", "autonomous"
    governed: bool             # Whether execution is kernel-governed
```

#### Example: Research and coding subagents

From the builtin `orchestrator` plugin:

```python
from hermit.runtime.capability.contracts.base import PluginContext, SubagentSpec


def register(ctx: PluginContext) -> None:
    ctx.add_subagent(
        SubagentSpec(
            name="researcher",
            description="Research a topic using web search and synthesize findings.",
            system_prompt=(
                "You are a research specialist. "
                "Use web_search to find relevant information, web_fetch to read "
                "full articles, and synthesize findings into concise summaries."
            ),
            tools=["web_search", "web_fetch", "bash", "read_file"],
        )
    )
    ctx.add_subagent(
        SubagentSpec(
            name="coder",
            description="Write, review, refactor, or debug code.",
            system_prompt=(
                "You are a coding specialist. Write clean, tested code. "
                "Follow existing project conventions."
            ),
            tools=["web_search", "web_fetch", "read_file", "write_file", "bash"],
        )
    )
```

When loaded, Hermit automatically creates `delegate_researcher` and
`delegate_coder` tools that the main agent can call to delegate work.

---

## Plugin Variables

Plugin variables provide configurable settings that are resolved from multiple
sources with a clear precedence order.

### Definition in plugin.toml

```toml
[variables.my_api_key]
setting = "my_api_key"                  # Config key in config.toml
env = ["MY_API_KEY", "MY_ALT_KEY"]      # Environment variable names
required = true                         # Fail if not found
secret = true                           # Mask in logs
description = "API key for the service"

[variables.max_retries]
setting = "my_plugin_max_retries"
env = ["MY_MAX_RETRIES"]
default = 3                             # Default value if not set
description = "Maximum retry attempts"
```

### Resolution order

Variables are resolved in this order (first non-empty value wins):

1. **Plugin-specific config** -- `~/.hermit/config.toml` plugin variables
   section (loaded via `load_plugin_variables`)
2. **Settings attribute** -- `settings.{setting}` if `setting` is declared
3. **Environment variables** -- Each `env` key is checked in order
4. **Default value** -- `default` if specified

### Accessing variables in code

```python
def register(ctx: PluginContext) -> None:
    api_key = ctx.get_var("my_api_key", "")
    max_retries = int(ctx.get_var("max_retries", 3))

    if not api_key:
        log.warning("my_plugin_no_api_key")
        return

    # Use the resolved values...
```

### Template interpolation in config

Variables can be interpolated into the `[config]` section using `{{ var_name }}`
syntax:

```toml
[config]
url = "{{ my_base_url }}/api/v1"

[config.headers]
Authorization = "Bearer {{ my_api_key }}"

[variables.my_base_url]
env = ["MY_BASE_URL"]
default = "https://api.example.com"

[variables.my_api_key]
env = ["MY_API_KEY"]
secret = true
```

In your `register()` function, the resolved config is available via
`ctx.config`:

```python
def register(ctx: PluginContext) -> None:
    url = ctx.config.get("url", "")           # Already interpolated
    headers = ctx.config.get("headers", {})   # {"Authorization": "Bearer <key>"}
```

---

## Discovery and Loading

### Discovery paths

Like `modprobe` loading kernel modules, Hermit discovers plugins at startup
from two paths: built-in (`src/hermit/plugins/builtin/`) and user-installed
(`~/.hermit/plugins/`).

Hermit scans these directories for plugins:

1. **Builtin**: `src/hermit/plugins/builtin/` -- ships with Hermit
2. **User**: `~/.hermit/plugins/` -- your custom plugins

Within each directory, Hermit discovers plugins by:

1. Looking for `plugin.toml` in each immediate subdirectory.
2. If a subdirectory has no `plugin.toml`, recursing one level deeper (this
   supports the category layout: `hooks/memory/plugin.toml`,
   `adapters/slack/plugin.toml`, etc.).

### Loading process

For each discovered `PluginManifest`:

1. Parse `plugin.toml` into a `PluginManifest` dataclass.
2. Resolve plugin variables (config, settings, env, defaults).
3. Render config templates with resolved variables.
4. For each `[entry]` dimension, import the module and call the function with
   a `PluginContext`.
5. Collect all tools, hooks, adapters, commands, MCP servers, and subagents
   from the context.
6. Additionally load `skills/` and `rules/` directories if present.

### External module loading

For user plugins (non-builtin), Hermit loads Python modules using
`importlib.util.spec_from_file_location`. The plugin directory is temporarily
added to `sys.path` during loading, so you can use relative imports within
your plugin.

### Declarative plugins

A plugin can consist of only `skills/` and `rules/` directories with no Python
entry points. This is useful for packaging curated instructions and reference
material:

```
code-review/
├── plugin.toml
├── skills/
│   └── code-review/
│       └── SKILL.md
└── rules/
    └── standards.md
```

```toml
[plugin]
name = "code-review"
version = "1.0.0"
description = "Code review standards and skills"
```

---

## Skills and Rules

### Skills

Skills are Markdown files that provide deep, actionable reference material for
specific tasks. They are injected into the agent's context either on-demand
(via the `read_skill` tool) or preloaded by adapters.

Place skills in `your-plugin/skills/<skill-name>/SKILL.md`:

```markdown
---
name: web-search
description: How to use web_search effectively
---

## Strategy

Start with recent results, then widen the time window...
```

The YAML front matter (`name`, `description`) is parsed by Hermit's skill
loader.

### Rules

Rules are Markdown files that define standards and conventions. They are
injected into the system prompt automatically.

Place rules in `your-plugin/rules/`:

```
my-plugin/
└── rules/
    └── coding-standards.md
```

---

## Testing Plugins

### Unit testing tools

```python
from pathlib import Path

from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


def test_timestamp_tool_registers():
    """Verify the tool registers correctly."""
    hooks = HooksEngine()
    ctx = PluginContext(hooks)

    from my_plugin.tools import register
    register(ctx)

    assert len(ctx.tools) == 1
    assert ctx.tools[0].name == "timestamp"
    assert ctx.tools[0].readonly is True


def test_timestamp_tool_handler():
    """Verify the tool handler returns expected output."""
    from my_plugin.tools import _handle_timestamp

    result = _handle_timestamp({"format": "unix"})
    assert result.isdigit()

    result = _handle_timestamp({"format": "date"})
    assert len(result) == 10  # YYYY-MM-DD
```

### Unit testing hooks

```python
from hermit.runtime.capability.contracts.base import HookEvent, PluginContext
from hermit.runtime.capability.contracts.hooks import HooksEngine


def test_hook_registration():
    """Verify hooks are registered on the expected events."""
    hooks = HooksEngine()
    ctx = PluginContext(hooks)

    from my_plugin.hooks import register
    register(ctx)

    assert hooks.has_handlers(HookEvent.POST_RUN)
    assert hooks.has_handlers(HookEvent.SESSION_END)


def test_system_prompt_hook():
    """Verify system prompt hook returns injectable text."""
    hooks = HooksEngine()
    hooks.register(HookEvent.SYSTEM_PROMPT, lambda: "<my-context>Hello</my-context>")

    results = hooks.fire(HookEvent.SYSTEM_PROMPT)
    assert len(results) == 1
    assert "<my-context>" in results[0]
```

### Testing plugin loading with PluginManager

```python
from pathlib import Path

from hermit.runtime.capability.registry.manager import PluginManager


def test_plugin_discovery(tmp_path: Path):
    """End-to-end: plugin discovered and loaded."""
    plugin_dir = tmp_path / "plugins" / "my-plugin"
    plugin_dir.mkdir(parents=True)

    (plugin_dir / "plugin.toml").write_text(
        '[plugin]\nname = "my-plugin"\nversion = "0.1.0"\n\n'
        '[entry]\ntools = "tools:register"\n',
        encoding="utf-8",
    )
    (plugin_dir / "tools.py").write_text(
        'from hermit.runtime.capability.registry.tools import ToolSpec\n'
        'def register(ctx):\n'
        '    ctx.add_tool(ToolSpec(\n'
        '        name="my_tool", description="Test",\n'
        '        input_schema={"type": "object", "properties": {}, "required": []},\n'
        '        handler=lambda p: "ok",\n'
        '        readonly=True, action_class="read_local",\n'
        '        idempotent=True, risk_hint="low", requires_receipt=False,\n'
        '    ))\n',
        encoding="utf-8",
    )

    pm = PluginManager()
    pm.discover_and_load(tmp_path / "plugins")

    assert len(pm.manifests) == 1
    assert pm.manifests[0].name == "my-plugin"
```

### Running tests

```bash
uv run pytest tests/unit/plugins/ -q
uv run pytest tests/unit/plugins/tools/test_my_plugin.py::test_specific -q
```

---

## Best Practices

### Naming

- Plugin names should be lowercase with hyphens: `my-plugin`, `web-tools`.
- Tool names should be lowercase with underscores: `web_search`, `grok_search`.
- Command names must start with `/`: `/compact`, `/plan`.
- Subagent names become tool names as `delegate_{name}` -- keep them short.

### Governance compliance

Every tool **must** declare governance metadata. This is not optional -- Hermit
raises `ToolGovernanceError` at registration time for tools with missing
metadata.

- Always set `action_class` to describe the type of action.
- Set `readonly=True` for tools with no side effects.
- Set `risk_hint` to reflect the actual risk level.
- Set `requires_receipt=True` for any tool that mutates state.
- Use `supports_preview=True` for tools where a dry-run makes sense.

### Action class reference

| Action class | Description | Examples |
|---|---|---|
| `read_local` | Read from local filesystem | `read_file`, `list_hermit_files` |
| `write_local` | Write to local filesystem | `write_file` |
| `execute_command` | Run a shell command | `bash` |
| `network_read` | Read from network (HTTP GET, search) | `web_search`, `web_fetch` |
| `external_mutation` | Mutate external service (API POST, etc.) | `create_pull_request`, `push_files` |

### Error handling

- Return clear error messages from tool handlers -- the agent sees these.
- Use `structlog.get_logger()` for structured logging.
- Validate inputs early in tool handlers.
- Handle missing API keys gracefully (log a warning, return an error message).

```python
import structlog

log = structlog.get_logger()

def handle_my_tool(payload: dict[str, Any]) -> str:
    query = str(payload.get("query", "")).strip()
    if not query:
        return "Error: query is required."
    try:
        return do_work(query)
    except Exception as exc:
        log.exception("my_tool_error", query=query)
        return f"Error: {exc}"
```

### Plugin lifecycle

- Use `SERVE_START` / `SERVE_STOP` for long-running background services.
- Handle `reload_mode=True` in serve hooks to support hot-reload without
  restarting services.
- Clean up resources in `SERVE_STOP` -- cancel background tasks, close
  connections.
- For adapters, flush active sessions in `stop()` so `SESSION_END` hooks fire.

### Keep it focused

- One plugin should do one thing well.
- If your plugin needs both tools and hooks, that is fine -- but keep the
  scope narrow.
- Prefer small, composable plugins over monolithic ones.

### Dependencies

If your plugin depends on another plugin, declare it in `plugin.toml`:

```toml
[dependencies]
requires = ["web-tools"]
```

### Secret management

- Mark sensitive variables with `secret = true` in `plugin.toml`.
- Never log secret values.
- Use environment variables or `~/.hermit/.env` for secrets.
- Check for missing secrets at registration time and log a warning.

### i18n support

For builtin plugins, use `description_key` fields pointing to locale keys in
`src/hermit/infra/system/locales/`. For user plugins, plain `description`
strings are sufficient.

```toml
[plugin]
description_key = "plugin.my_plugin.description"
description = "Fallback English description"
```

In tool specs:

```python
ToolSpec(
    name="my_tool",
    description="English fallback",
    description_key="tools.my_plugin.my_tool.description",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description_key": "tools.my_plugin.my_tool.query",
            },
        },
        "required": ["query"],
    },
    ...
)
```

---

## See Also

- [Architecture Overview](./architecture.md) -- Hermit's layer architecture and design decisions
- [MCP Integration](./mcp-integration.md) -- MCP Integration Guide (for MCP plugin type)
- [Getting Started](./getting-started.md) -- Installation and first run
- [Use Cases](./use-cases.md) -- Use Cases showing plugins in action
