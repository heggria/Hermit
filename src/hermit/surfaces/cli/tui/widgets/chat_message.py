"""Single chat message widget with role, markdown content, thinking, and tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Markdown, Static

from .approval_banner import ApprovalBanner
from .thinking_block import ThinkingBlock

if TYPE_CHECKING:
    from hermit.runtime.provider_host.execution.runtime import AgentResult


class ChatMessage(Widget):
    """Renders a single chat message (user or assistant)."""

    DEFAULT_CSS = """
    ChatMessage {
        height: auto;
        margin: 1 0;
        padding: 0;
        layout: vertical;
    }
    ChatMessage .role-user {
        color: $success;
        text-style: bold;
    }
    ChatMessage .role-assistant {
        color: $primary;
        text-style: bold;
    }
    ChatMessage .role-system {
        color: $warning;
        text-style: bold;
    }
    ChatMessage .msg-content {
        margin: 0 0 0 2;
        height: auto;
    }
    """

    def __init__(
        self,
        role: str,
        content: str,
        thinking: str = "",
        agent_result: AgentResult | None = None,
    ) -> None:
        super().__init__()
        self._role = role
        self._content = content
        self._thinking = thinking
        self._agent_result = agent_result

    def compose(self) -> ComposeResult:
        label_class = f"role-{self._role}"
        role_display = self._role.capitalize()
        yield Static(f"[bold]{role_display}[/]", classes=label_class)

        if self._thinking:
            yield ThinkingBlock(self._thinking)

        with Vertical(classes="msg-content"):
            yield Markdown(self._content)

        if self._agent_result and self._agent_result.blocked and self._agent_result.approval_id:
            yield ApprovalBanner(self._agent_result.approval_id)


class SystemMessage(Static):
    """Renders a system/command output message."""

    DEFAULT_CSS = """
    SystemMessage {
        height: auto;
        margin: 0 0 0 2;
        color: $text-muted;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__(text)
