"""Bottom status bar widget."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class StatusBar(Widget):
    """Status bar showing model, tokens, session, and generation state."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
        layout: horizontal;
    }
    StatusBar .status-model {
        width: auto;
        padding: 0 1 0 0;
    }
    StatusBar .status-tokens {
        width: auto;
        padding: 0 1;
    }
    StatusBar .status-session {
        width: auto;
        padding: 0 1;
    }
    StatusBar .status-state {
        width: auto;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        model: str = "",
        session_id: str = "cli",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._model = model
        self._session_id = session_id
        self._input_tokens = 0
        self._output_tokens = 0
        self._is_generating = False

    def compose(self) -> ComposeResult:
        yield Static(self._model, classes="status-model", id="sb-model")
        yield Static("", classes="status-tokens", id="sb-tokens")
        yield Static(f"session: {self._session_id}", classes="status-session", id="sb-session")
        yield Static("", classes="status-state", id="sb-state")

    def update_tokens(self, input_tokens: int, output_tokens: int) -> None:
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        text = f"| ↑{self._format_count(self._input_tokens)} ↓{self._format_count(self._output_tokens)} tokens"
        self.query_one("#sb-tokens", Static).update(text)

    def set_generating(self, generating: bool) -> None:
        self._is_generating = generating
        state = self.query_one("#sb-state", Static)
        state.update("| [bold green]● generating...[/]" if generating else "")

    @staticmethod
    def _format_count(n: int) -> str:
        if n >= 1000:
            return f"{n / 1000:.1f}k"
        return str(n)
