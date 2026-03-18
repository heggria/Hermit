"""Tests for overnight dashboard FastAPI router."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from hermit.kernel.ledger.journal.store import KernelStore
from hermit.plugins.builtin.hooks.overnight.dashboard import create_overnight_router


@pytest.fixture
def store(tmp_path: Path) -> KernelStore:
    db = tmp_path / "state.db"
    return KernelStore(db)


@pytest.fixture
def dashboard_app(store: KernelStore) -> FastAPI:
    fastapi_app = FastAPI()
    router = create_overnight_router(store)
    fastapi_app.include_router(router)
    return fastapi_app


async def test_latest_endpoint(dashboard_app: FastAPI) -> None:
    transport = ASGITransport(app=dashboard_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/overnight/latest")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks_completed" in data
        assert "lookback_hours" in data


async def test_latest_custom_lookback(dashboard_app: FastAPI) -> None:
    transport = ASGITransport(app=dashboard_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/overnight/latest?lookback=24")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lookback_hours"] == 24


async def test_history_endpoint(dashboard_app: FastAPI) -> None:
    transport = ASGITransport(app=dashboard_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/overnight/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "not_implemented"


async def test_signals_endpoint_no_method(dashboard_app: FastAPI) -> None:
    """When store has no list_signals, should return empty list."""
    transport = ASGITransport(app=dashboard_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/overnight/signals")
        assert resp.status_code == 200
        assert resp.json() == []
