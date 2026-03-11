from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, Optional

from hermit.core.session import SessionManager
from hermit.provider.runtime import AgentResult, AgentRuntime, ToolCallback, ToolStartCallback

if TYPE_CHECKING:
    from hermit.plugin.manager import PluginManager

CommandHandler = Callable[["AgentRunner", str, str], "DispatchResult"]


@dataclass
class DispatchResult:
    """Unified result returned by AgentRunner.dispatch() for both commands and agent replies."""

    text: str
    is_command: bool = False
    should_exit: bool = False
    agent_result: Optional[AgentResult] = None


class AgentRunner:
    """Unified orchestration layer: session + agent + plugin hooks.

    Both CLI commands and adapter plugins call this instead of
    duplicating the get_session -> run -> save -> hooks flow.
    """

    # Class-level registry for core commands (populated by decorators at import time).
    _core_commands: Dict[str, tuple[CommandHandler, str, bool]] = {}

    @classmethod
    def register_command(
        cls, name: str, help_text: str, cli_only: bool = False
    ) -> Callable[[CommandHandler], CommandHandler]:
        """Decorator to register a core slash command."""
        def decorator(fn: CommandHandler) -> CommandHandler:
            cls._core_commands[name] = (fn, help_text, cli_only)
            return fn
        return decorator

    def __init__(
        self,
        agent: AgentRuntime,
        session_manager: SessionManager,
        plugin_manager: PluginManager,
        serve_mode: bool = False,
    ) -> None:
        self.agent = agent
        self.session_manager = session_manager
        self.pm = plugin_manager
        self.serve_mode = serve_mode
        self._session_started: set[str] = set()
        # Instance-level copy: core commands + plugin commands added later via add_command()
        self._commands: Dict[str, tuple[CommandHandler, str, bool]] = dict(self._core_commands)

    def add_command(
        self, name: str, handler: CommandHandler, help_text: str, cli_only: bool = False,
    ) -> None:
        """Register a command on this runner instance (used by plugins)."""
        self._commands[name] = (handler, help_text, cli_only)

    # ------------------------------------------------------------------
    # Public dispatch entry point
    # ------------------------------------------------------------------

    def dispatch(
        self,
        session_id: str,
        text: str,
        on_tool_call: Optional[ToolCallback] = None,
        on_tool_start: Optional[ToolStartCallback] = None,
    ) -> DispatchResult:
        """Route a raw user message: slash commands are handled here; everything
        else is forwarded to the agent.
        """
        stripped = text.strip()
        if stripped.startswith("/"):
            cmd = stripped.split()[0].lower()
            entry = self._commands.get(cmd)
            if entry:
                handler, _help, _cli = entry
                return handler(self, session_id, stripped)
            return DispatchResult(
                text=f"未知命令：{cmd}。输入 /help 查看可用命令。",
                is_command=True,
            )

        agent_result = self.handle(
            session_id, text,
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
        )
        return DispatchResult(
            text=agent_result.text or "",
            agent_result=agent_result,
        )

    def handle(
        self,
        session_id: str,
        text: str,
        on_tool_call: Optional[ToolCallback] = None,
        on_tool_start: Optional[ToolStartCallback] = None,
    ) -> AgentResult:
        """Process a single user message within a session."""
        session = self.session_manager.get_or_create(session_id)

        if session_id not in self._session_started:
            self.pm.on_session_start(session_id)
            self._session_started.add(session_id)

        prompt, run_opts = self.pm.on_pre_run(
            text, session_id=session_id, session=session, messages=list(session.messages),
            runner=self,
        )

        now = datetime.datetime.now()
        session_started = datetime.datetime.fromtimestamp(session.created_at)
        time_ctx = (
            f"<session_time>"
            f"session_started_at={session_started.strftime('%Y-%m-%d %H:%M:%S')} "
            f"message_sent_at={now.strftime('%Y-%m-%d %H:%M:%S')}"
            f"</session_time>\n\n"
        )
        prompt = time_ctx + prompt

        result = self.agent.run(
            prompt,
            message_history=list(session.messages),
            on_tool_call=on_tool_call,
            on_tool_start=on_tool_start,
            disable_tools=run_opts.get("disable_tools", False),
            readonly_only=run_opts.get("readonly_only", False),
        )

        session.total_input_tokens      += result.input_tokens
        session.total_output_tokens     += result.output_tokens
        session.total_cache_read_tokens += result.cache_read_tokens
        session.total_cache_creation_tokens += result.cache_creation_tokens

        session.messages = result.messages
        self.session_manager.save(session)
        self.pm.on_post_run(result, session_id=session_id, session=session)
        return result

    def close_session(self, session_id: str) -> None:
        """End a session, fire hooks, and archive."""
        session = self.session_manager.get_or_create(session_id)
        self.pm.on_session_end(session_id, session.messages)
        self.session_manager.close(session_id)
        self._session_started.discard(session_id)

    def reset_session(self, session_id: str) -> None:
        """Close current session and start a fresh one."""
        self.close_session(session_id)
        self.session_manager.get_or_create(session_id)
        self.pm.on_session_start(session_id)
        self._session_started.add(session_id)


# ------------------------------------------------------------------
# Core slash commands (always available, not from plugins)
# ------------------------------------------------------------------

@AgentRunner.register_command("/new", "开启新会话，清空当前上下文")
def _cmd_new(runner: AgentRunner, session_id: str, _text: str) -> DispatchResult:
    runner.reset_session(session_id)
    return DispatchResult("已开启新会话。", is_command=True)


@AgentRunner.register_command("/history", "显示当前会话的消息轮次统计")
def _cmd_history(runner: AgentRunner, session_id: str, _text: str) -> DispatchResult:
    session = runner.session_manager.get_or_create(session_id)
    user_turns = sum(1 for m in session.messages if m.get("role") == "user")
    total = len(session.messages)
    return DispatchResult(
        f"当前会话：{user_turns} 轮用户消息，共 {total} 条记录。",
        is_command=True,
    )


@AgentRunner.register_command("/quit", "退出（仅 CLI 模式）", cli_only=True)
def _cmd_quit(_runner: AgentRunner, _session_id: str, _text: str) -> DispatchResult:
    return DispatchResult("Bye.", is_command=True, should_exit=True)


@AgentRunner.register_command("/help", "显示所有可用命令")
def _cmd_help(runner: AgentRunner, _session_id: str, _text: str) -> DispatchResult:
    lines = ["**可用命令**"]
    for cmd, (_fn, help_text, cli_only) in sorted(runner._commands.items()):
        if runner.serve_mode and cli_only:
            continue
        lines.append(f"- `{cmd}` — {help_text}")
    return DispatchResult("\n".join(lines), is_command=True)
