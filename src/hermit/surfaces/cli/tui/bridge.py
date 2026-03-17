"""RunnerBridge: connects AgentRunner to the Textual event loop."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from .messages import GenerationComplete, ToolCompleted, ToolStarted

if TYPE_CHECKING:
    from textual.app import App

    from hermit.runtime.control.runner.runner import AgentRunner, DispatchResult


class RunnerBridge:
    """Bridges AgentRunner.dispatch() into Textual messages via a worker thread."""

    def __init__(self, app: App[None], runner: AgentRunner, session_id: str) -> None:
        self._app = app
        self._runner = runner
        self._session_id = session_id
        self.is_generating = False
        self.pending_approval_id: str | None = None

    def submit(self, text: str) -> None:
        """Submit user text — runs dispatch in a dedicated thread."""
        self.is_generating = True
        thread = threading.Thread(target=self._dispatch, args=(text,), daemon=True)
        thread.start()

    def _dispatch(self, text: str) -> DispatchResult:
        """Called inside the worker thread."""
        result = self._runner.dispatch(
            self._session_id,
            text,
            on_tool_call=self._on_tool_call,
            on_tool_start=self._on_tool_start,
        )
        self._app.call_from_thread(self._on_generation_complete, result)
        return result

    def _on_tool_start(self, name: str, inputs: dict[str, Any]) -> None:
        self._app.call_from_thread(self._app.post_message, ToolStarted(name, inputs))

    def _on_tool_call(self, name: str, inputs: dict[str, Any], result: Any) -> None:
        self._app.call_from_thread(self._app.post_message, ToolCompleted(name, inputs, result))

    def _on_generation_complete(self, result: DispatchResult) -> None:
        self.is_generating = False
        if result.agent_result and result.agent_result.blocked:
            self.pending_approval_id = result.agent_result.approval_id
        else:
            self.pending_approval_id = None
        self._app.post_message(GenerationComplete(result))

    def close_session(self) -> None:
        self._runner.close_session(self._session_id)
