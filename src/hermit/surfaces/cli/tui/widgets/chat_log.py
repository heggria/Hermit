"""Scrollable container for chat messages."""

from __future__ import annotations

from textual.containers import VerticalScroll


class ChatLog(VerticalScroll):
    """Scrollable container that holds ChatMessage widgets."""

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
        padding: 0 1;
    }
    """

    def scroll_to_latest(self) -> None:
        """Scroll to the bottom of the chat log."""
        self.scroll_end(animate=False)
