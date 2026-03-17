"""Widget showing tool execution state."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import LoadingIndicator, Static


def _compact_args(inputs: dict[str, Any], limit: int = 60) -> str:
    parts: list[str] = []
    for k, v in inputs.items():
        r = repr(v)
        if len(r) > limit:
            r = r[:limit] + "..."
        parts.append(f"{k}={r}")
    return ", ".join(parts)


def _result_preview(result: Any, limit: int = 200) -> str:
    text = result if isinstance(result, str) else str(result)
    preview = text[:limit].replace("\n", " ")
    if len(text) > limit:
        preview += "..."
    return preview


class ToolDisplay(Widget):
    """Displays a single tool call — running or completed."""

    DEFAULT_CSS = """
    ToolDisplay {
        height: auto;
        margin: 0 0 0 2;
        layout: horizontal;
    }
    ToolDisplay .tool-running {
        color: $accent;
        width: 1fr;
    }
    ToolDisplay .tool-done {
        color: $text-muted;
        width: 1fr;
    }
    ToolDisplay LoadingIndicator {
        width: 4;
        height: 1;
        min-height: 1;
        padding: 0;
    }
    """

    def __init__(self, name: str, inputs: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._tool_name = name
        self._tool_inputs = inputs or {}
        self._completed = False
        self._result: Any = None

    def compose(self) -> ComposeResult:
        args = _compact_args(self._tool_inputs)
        label = f"[bold cyan]▸[/] {self._tool_name}({args})"
        yield Static(label, classes="tool-running", id="tool-label")
        yield LoadingIndicator(id="tool-spinner")

    def mark_complete(self, result: Any) -> None:
        self._completed = True
        self._result = result
        args = _compact_args(self._tool_inputs)
        preview = _result_preview(result)
        label_widget = self.query_one("#tool-label", Static)
        label_widget.update(f"[dim]▸ {self._tool_name}({args}) → {preview}[/]")
        label_widget.set_classes("tool-done")
        spinner = self.query_one("#tool-spinner", LoadingIndicator)
        spinner.display = False
