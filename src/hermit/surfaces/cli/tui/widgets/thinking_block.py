"""Collapsible thinking block widget."""

from __future__ import annotations

from textual.widgets import Collapsible, Static


class ThinkingBlock(Collapsible):
    """Displays model thinking in a collapsible block."""

    DEFAULT_CSS = """
    ThinkingBlock {
        margin: 0 0 1 2;
    }
    ThinkingBlock > Contents {
        padding: 0 1;
    }
    """

    def __init__(self, thinking: str) -> None:
        content = Static(thinking, classes="thinking-text")
        super().__init__(content, title="thinking", collapsed=True)
