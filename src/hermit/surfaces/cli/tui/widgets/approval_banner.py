"""Inline approval banner widget."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static


class ApprovalBanner(Widget):
    """Non-modal inline banner for pending approvals."""

    DEFAULT_CSS = """
    ApprovalBanner {
        height: auto;
        margin: 1 0 0 2;
        padding: 1 2;
        background: $warning-darken-3;
        color: $text;
        border: tall $warning;
        layout: vertical;
    }
    """

    class Approved(Message):
        def __init__(self, approval_id: str) -> None:
            super().__init__()
            self.approval_id = approval_id

    class Rejected(Message):
        def __init__(self, approval_id: str) -> None:
            super().__init__()
            self.approval_id = approval_id

    def __init__(self, approval_id: str, summary: str = "") -> None:
        super().__init__()
        self._approval_id = approval_id
        self._summary = summary

    def compose(self) -> ComposeResult:
        text = "[bold]Approval required[/]"
        if self._summary:
            text += f"\n{self._summary}"
        text += f"\n[dim]ID: {self._approval_id}[/]"
        text += "\n[bold yellow]\\[y][/] approve  [bold yellow]\\[n][/] reject"
        yield Static(text)

    def on_key(self, event) -> None:
        if event.key == "y":
            self.post_message(self.Approved(self._approval_id))
            self.remove()
        elif event.key == "n":
            self.post_message(self.Rejected(self._approval_id))
            self.remove()
