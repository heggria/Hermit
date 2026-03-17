"""Custom Textual messages for the Hermit TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.message import Message

if TYPE_CHECKING:
    from hermit.runtime.control.runner.runner import DispatchResult


class InputSubmitted(Message):
    """User submitted text from the input area."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ToolStarted(Message):
    """A tool execution has begun."""

    def __init__(self, name: str, inputs: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.name = name
        self.inputs = inputs or {}


class ToolCompleted(Message):
    """A tool execution has finished."""

    def __init__(self, name: str, inputs: dict[str, Any] | None = None, result: Any = None) -> None:
        super().__init__()
        self.name = name
        self.inputs = inputs or {}
        self.result = result


class GenerationComplete(Message):
    """The agent has finished generating a response."""

    def __init__(self, result: DispatchResult) -> None:
        super().__init__()
        self.result = result
