"""Tests for overnight dashboard router."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Force-import the report module so patch() can resolve the dotted path.
import hermit.plugins.builtin.hooks.overnight.report as _report_mod  # noqa: F401

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_app(store: object | None = None) -> FastAPI:
    """Build a minimal FastAPI app with the overnight router attached."""
    from hermit.plugins.builtin.hooks.overnight.dashboard import create_overnight_router

    app = FastAPI()
    router = create_overnight_router(store)  # type: ignore[arg-type]
    app.include_router(router)
    return app


# ------------------------------------------------------------------
# /overnight/latest
# ------------------------------------------------------------------


class TestLatestEndpoint:
    def test_latest_returns_dashboard_json(self) -> None:
        fake_summary = SimpleNamespace(tasks_completed=[], total_governed_actions=0)
        dashboard_json = {"status": "ok", "tasks_completed": 0}

        with patch(
            "hermit.plugins.builtin.hooks.overnight.report.OvernightReportService"
        ) as MockService:
            instance = MockService.return_value
            instance.generate.return_value = fake_summary
            instance.format_dashboard_json.return_value = dashboard_json

            app = _make_app(store=MagicMock())
            client = TestClient(app)
            resp = client.get("/overnight/latest")

        assert resp.status_code == 200
        assert resp.json() == dashboard_json
        instance.generate.assert_called_once_with(lookback_hours=12)

    def test_latest_custom_lookback(self) -> None:
        with patch(
            "hermit.plugins.builtin.hooks.overnight.report.OvernightReportService"
        ) as MockService:
            instance = MockService.return_value
            instance.generate.return_value = SimpleNamespace()
            instance.format_dashboard_json.return_value = {}

            app = _make_app(store=MagicMock())
            client = TestClient(app)
            resp = client.get("/overnight/latest?lookback=24")

        assert resp.status_code == 200
        instance.generate.assert_called_once_with(lookback_hours=24)


# ------------------------------------------------------------------
# /overnight/history
# ------------------------------------------------------------------


class TestHistoryEndpoint:
    def test_history_returns_not_implemented(self) -> None:
        with patch("hermit.plugins.builtin.hooks.overnight.report.OvernightReportService"):
            app = _make_app(store=MagicMock())
            client = TestClient(app)
            resp = client.get("/overnight/history")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "not_implemented"


# ------------------------------------------------------------------
# /overnight/signals
# ------------------------------------------------------------------


class TestSignalsEndpoint:
    def test_signals_returns_empty_when_no_list_signals(self) -> None:
        store = MagicMock(spec=[])  # no list_signals attribute
        with patch("hermit.plugins.builtin.hooks.overnight.report.OvernightReportService"):
            app = _make_app(store=store)
            client = TestClient(app)
            resp = client.get("/overnight/signals")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_signals_returns_formatted_list(self) -> None:
        sig = SimpleNamespace(
            signal_id="sig_abc",
            source_kind="lint_violation",
            summary="2 lint issues",
            disposition="pending",
            risk_level="low",
            created_at=1700000000.0,
        )
        store = MagicMock()
        store.list_signals.return_value = [sig]
        with patch("hermit.plugins.builtin.hooks.overnight.report.OvernightReportService"):
            app = _make_app(store=store)
            client = TestClient(app)
            resp = client.get("/overnight/signals?limit=10")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["signal_id"] == "sig_abc"
        assert data[0]["source_kind"] == "lint_violation"
        store.list_signals.assert_called_once_with(limit=10)
