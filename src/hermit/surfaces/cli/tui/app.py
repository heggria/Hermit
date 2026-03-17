"""HermitApp — main Textual application for ``hermit chat --tui``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header, Static

from .bridge import RunnerBridge
from .messages import GenerationComplete, InputSubmitted, ToolCompleted, ToolStarted
from .widgets.approval_banner import ApprovalBanner
from .widgets.chat_log import ChatLog
from .widgets.chat_message import ChatMessage, SystemMessage
from .widgets.input_area import InputArea
from .widgets.status_bar import StatusBar
from .widgets.tool_display import ToolDisplay

if TYPE_CHECKING:
    from hermit.runtime.assembly.config import Settings
    from hermit.runtime.capability.registry.manager import PluginManager
    from hermit.runtime.control.runner.runner import AgentRunner

CSS_PATH = Path(__file__).parent / "styles" / "hermit.tcss"


class HermitApp(App):
    """Textual TUI for Hermit chat."""

    TITLE = "Hermit"
    CSS_PATH = CSS_PATH

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(
        self,
        runner: AgentRunner,
        pm: PluginManager,
        session_id: str = "cli",
        settings: Settings | None = None,
    ) -> None:
        super().__init__()
        self._runner = runner
        self._pm = pm
        self._session_id = session_id
        self._settings = settings
        self._bridge: RunnerBridge | None = None
        self._tool_widgets: dict[str, ToolDisplay] = {}
        self._model = getattr(settings, "model", "") if settings else ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield ChatLog(id="chat-log")
        yield StatusBar(model=self._model, session_id=self._session_id, id="status-bar")
        yield InputArea(id="input-area")

    def on_mount(self) -> None:
        self._bridge = RunnerBridge(self, self._runner, self._session_id)
        chat_log = self.query_one("#chat-log", ChatLog)
        chat_log.mount(
            Static(
                f"[dim]Hermit chat (session={self._session_id}). "
                f"Type /help for commands, Ctrl+Q to quit.[/]",
                classes="welcome-banner",
            )
        )
        self.query_one("#input-area", InputArea).focus()

    def on_input_submitted(self, message: InputSubmitted) -> None:
        if not self._bridge:
            return
        text = message.text

        chat_log = self.query_one("#chat-log", ChatLog)
        chat_log.mount(ChatMessage(role="user", content=text))
        chat_log.scroll_to_latest()

        input_area = self.query_one("#input-area", InputArea)
        input_area.set_disabled(True)

        status = self.query_one("#status-bar", StatusBar)
        status.set_generating(True)

        self._tool_widgets.clear()
        self._bridge.submit(text)

    def on_tool_started(self, message: ToolStarted) -> None:
        chat_log = self.query_one("#chat-log", ChatLog)
        widget = ToolDisplay(message.name, message.inputs)
        self._tool_widgets[message.name] = widget
        chat_log.mount(widget)
        chat_log.scroll_to_latest()

    def on_tool_completed(self, message: ToolCompleted) -> None:
        widget = self._tool_widgets.get(message.name)
        if widget:
            widget.mark_complete(message.result)

    def on_generation_complete(self, message: GenerationComplete) -> None:
        result = message.result
        chat_log = self.query_one("#chat-log", ChatLog)
        input_area = self.query_one("#input-area", InputArea)
        status = self.query_one("#status-bar", StatusBar)

        status.set_generating(False)
        input_area.set_disabled(False)

        if result.is_command:
            chat_log.mount(SystemMessage(result.text))
            if result.should_exit:
                self._do_quit()
                return
        elif result.agent_result:
            ar = result.agent_result
            chat_log.mount(
                ChatMessage(
                    role="assistant",
                    content=ar.text,
                    thinking=ar.thinking,
                    agent_result=ar,
                )
            )
            status.update_tokens(ar.input_tokens, ar.output_tokens)

        chat_log.scroll_to_latest()
        input_area.focus()

    def on_approval_banner_approved(self, message: ApprovalBanner.Approved) -> None:
        if self._bridge:
            self._bridge.submit(f"approve {message.approval_id}")

    def on_approval_banner_rejected(self, message: ApprovalBanner.Rejected) -> None:
        if self._bridge:
            self._bridge.submit(f"reject {message.approval_id}")

    def action_cancel(self) -> None:
        pass

    def action_quit(self) -> None:
        self._do_quit()

    def _do_quit(self) -> None:
        if self._bridge:
            self._bridge.close_session()
        self.exit()
