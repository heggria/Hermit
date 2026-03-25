"""Dependency injection for WebUI API routes."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

if TYPE_CHECKING:
    from hermit.runtime.control.runner.runner import AgentRunner

_server_ref: Any = None
_lock = threading.Lock()


def set_server(server: Any) -> None:
    global _server_ref
    with _lock:
        _server_ref = server


def get_runner() -> AgentRunner:
    with _lock:
        server = _server_ref
    if server is None:
        raise HTTPException(status_code=503, detail="WebUI server not initialized")
    return server._get_runner()


def get_store() -> Any:
    with _lock:
        server = _server_ref
    if server is None:
        raise HTTPException(status_code=503, detail="WebUI server not initialized")
    return server._get_store()


def get_server() -> Any:
    """Return the raw WebUI server instance."""
    with _lock:
        server = _server_ref
    if server is None:
        raise HTTPException(status_code=503, detail="WebUI server not initialized")
    return server
