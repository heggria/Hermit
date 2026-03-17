"""Chat input area with history support."""

from __future__ import annotations

from typing import Any

from textual.events import Key
from textual.widgets import TextArea

from ..messages import InputSubmitted


class InputArea(TextArea):
    """Multi-line text input with Enter-to-submit and history navigation.

    - **Enter** submits the message
    - **Shift+Enter** inserts a newline
    - **Up/Down** at first/last line navigates history
    """

    DEFAULT_CSS = """
    InputArea {
        dock: bottom;
        height: auto;
        min-height: 3;
        max-height: 10;
        margin: 0 1;
        border: tall $accent;
    }
    InputArea:focus {
        border: tall $accent-lighten-1;
    }
    InputArea.-disabled {
        opacity: 0.5;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(language=None, **kwargs)
        self._history: list[str] = []
        self._history_index = -1
        self._draft = ""

    async def _on_key(self, event: Key) -> None:
        if event.key == "enter":
            # Enter = submit; prevent TextArea from inserting a newline.
            event.prevent_default()
            event.stop()
            self._submit()
            return

        if event.key == "shift+enter":
            # Let TextArea handle it as a normal Enter (inserts newline).
            # Replace the key so TextArea sees a plain "enter".
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return

        if event.key == "up" and self._at_first_line():
            event.prevent_default()
            event.stop()
            self._navigate_history(-1)
            return

        if event.key == "down" and self._at_last_line():
            event.prevent_default()
            event.stop()
            self._navigate_history(1)
            return

    def _submit(self) -> None:
        text = self.text.strip()
        if not text:
            return
        self._history.append(text)
        self._history_index = -1
        self._draft = ""
        self.clear()
        self.post_message(InputSubmitted(text))

    def set_disabled(self, disabled: bool) -> None:
        self.read_only = disabled
        self.set_class(disabled, "-disabled")

    def _at_first_line(self) -> bool:
        return self.cursor_location[0] == 0

    def _at_last_line(self) -> bool:
        return self.cursor_location[0] >= self.document.line_count - 1

    def _navigate_history(self, direction: int) -> None:
        if not self._history:
            return
        if self._history_index == -1:
            self._draft = self.text

        new_idx = self._history_index + direction
        if new_idx < -1:
            new_idx = -1
        elif new_idx >= len(self._history):
            return

        self._history_index = new_idx
        if new_idx == -1:
            self.load_text(self._draft)
        else:
            idx = len(self._history) - 1 - new_idx
            if 0 <= idx < len(self._history):
                self.load_text(self._history[idx])
