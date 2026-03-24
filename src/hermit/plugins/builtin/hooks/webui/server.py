"""WebUI HTTP server — FastAPI-based dashboard with SSE and WebSocket support."""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from hermit.runtime.control.runner.runner import AgentRunner

_log = structlog.get_logger()

_DIST_DIR = Path(__file__).parent / "frontend" / "dist"


class WebUIServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8323,
        open_browser: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._open_browser = open_browser
        self._runner: AgentRunner | None = None
        self._runner_lock = threading.Lock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

        self._app = FastAPI(title="Hermit WebUI", docs_url=None, redoc_url=None)

        self._app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Register self for dependency injection
        from hermit.plugins.builtin.hooks.webui.api.deps import set_server

        set_server(self)

        # Health endpoint
        self._app.add_api_route("/api/health", self._health, methods=["GET"])

        # Mount API routers
        from hermit.plugins.builtin.hooks.webui.api.approvals import router as approvals_router
        from hermit.plugins.builtin.hooks.webui.api.artifacts import router as artifacts_router
        from hermit.plugins.builtin.hooks.webui.api.chat import router as chat_router
        from hermit.plugins.builtin.hooks.webui.api.config import router as config_router
        from hermit.plugins.builtin.hooks.webui.api.grants import router as grants_router
        from hermit.plugins.builtin.hooks.webui.api.iterations import router as iterations_router
        from hermit.plugins.builtin.hooks.webui.api.memory import router as memory_router
        from hermit.plugins.builtin.hooks.webui.api.memory_stats import (
            router as memory_stats_router,
        )
        from hermit.plugins.builtin.hooks.webui.api.metrics import router as metrics_router
        from hermit.plugins.builtin.hooks.webui.api.patterns import router as patterns_router
        from hermit.plugins.builtin.hooks.webui.api.policy import router as policy_router
        from hermit.plugins.builtin.hooks.webui.api.reconciliation import (
            router as reconciliation_router,
        )
        from hermit.plugins.builtin.hooks.webui.api.schedules import router as schedules_router
        from hermit.plugins.builtin.hooks.webui.api.signals import router as signals_router
        from hermit.plugins.builtin.hooks.webui.api.stream import router as stream_router
        from hermit.plugins.builtin.hooks.webui.api.tasks import router as tasks_router
        from hermit.plugins.builtin.hooks.webui.api.webhooks import router as webhooks_router

        self._app.include_router(tasks_router, prefix="/api")
        self._app.include_router(approvals_router, prefix="/api")
        self._app.include_router(metrics_router, prefix="/api")
        self._app.include_router(memory_router, prefix="/api")
        self._app.include_router(memory_stats_router, prefix="/api")
        self._app.include_router(signals_router, prefix="/api")
        self._app.include_router(policy_router, prefix="/api")
        self._app.include_router(config_router, prefix="/api")
        self._app.include_router(stream_router, prefix="/api")
        self._app.include_router(chat_router, prefix="/api")
        self._app.include_router(grants_router, prefix="/api")
        self._app.include_router(artifacts_router, prefix="/api")
        self._app.include_router(iterations_router, prefix="/api")
        self._app.include_router(schedules_router, prefix="/api")
        self._app.include_router(webhooks_router, prefix="/api")
        self._app.include_router(reconciliation_router, prefix="/api")
        self._app.include_router(patterns_router, prefix="/api")

        # Mount static files for SPA (must be last)
        if _DIST_DIR.exists():
            assets_dir = _DIST_DIR / "assets"
            if assets_dir.exists():
                self._app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="static")

            @self._app.get("/{full_path:path}")
            async def serve_spa(full_path: str) -> FileResponse:
                file_path = _DIST_DIR / full_path
                if file_path.exists() and file_path.is_file():
                    return FileResponse(str(file_path))
                return FileResponse(str(_DIST_DIR / "index.html"))

    # ------------------------------------------------------------------
    # Runner access (thread-safe)
    # ------------------------------------------------------------------

    def swap_runner(self, new_runner: AgentRunner) -> None:
        with self._runner_lock:
            self._runner = new_runner
        _log.info("webui_runner_swapped")  # type: ignore[call-arg]

    def _get_runner(self) -> AgentRunner:
        with self._runner_lock:
            runner = self._runner
        if runner is None:
            raise HTTPException(status_code=503, detail="Runner not attached")
        return runner

    def _get_store(self) -> Any:
        with self._runner_lock:
            runner = self._runner
        if runner is None:
            raise HTTPException(status_code=503, detail="Runner not attached")
        task_controller = getattr(runner, "task_controller", None)
        if task_controller is not None:
            return task_controller.store
        store = getattr(getattr(runner, "agent", None), "kernel_store", None)
        if store is None:
            raise HTTPException(status_code=503, detail="Kernel store not available")
        return store

    # ------------------------------------------------------------------
    # Utility endpoints
    # ------------------------------------------------------------------

    async def _health(self) -> dict[str, str]:
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, runner: AgentRunner) -> None:
        with self._runner_lock:
            self._runner = runner

        uv_config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(uv_config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="webui-http",
            daemon=True,
        )
        self._thread.start()

        _log.info(  # type: ignore[call-arg]
            "webui_server_started",
            host=self._host,
            port=self._port,
            url=f"http://{self._host}:{self._port}",
        )

        if self._open_browser:
            try:
                webbrowser.open(f"http://127.0.0.1:{self._port}")
            except Exception:
                pass

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                _log.warning("webui_server_thread_still_alive")  # type: ignore[call-arg]
        _log.info("webui_server_stopped")  # type: ignore[call-arg]
