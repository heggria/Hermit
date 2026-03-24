#!/usr/bin/env python3
"""Hermit Live Dashboard — real-time Textual TUI for governed task monitoring.

Usage:
    # Standalone (shows all active tasks):
    uv run python scripts/showcase-dashboard.py

    # With specific task IDs (launched by showcase-throughput.py):
    uv run python scripts/showcase-dashboard.py --task-ids t1,t2,t3

    # With existing MCP session:
    uv run python scripts/showcase-dashboard.py --session-id <sid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import DataTable, Digits, Footer, Header, Label, ProgressBar, Static

# ── MCP helpers ───────────────────────────────────────────────────────────────

MCP_URL = os.environ.get("HERMIT_MCP_URL", "http://127.0.0.1:8322/mcp")
STATE_FILE = Path("/tmp/hermit_showcase_state.json")


def _curl_mcp(method: str, params: dict, session_id: str = "") -> tuple[str, str]:
    payload = {
        "jsonrpc": "2.0",
        "id": f"req-{method}-{int(time.time() * 1000)}",
        "method": method if method == "initialize" else "tools/call",
        "params": params if method == "initialize" else {"name": method, "arguments": params},
    }
    cmd = [
        "curl",
        "-s",
        "-D",
        "-",
        "-X",
        "POST",
        MCP_URL,
        "-H",
        "Content-Type: application/json",
        "-H",
        "Accept: application/json, text/event-stream",
    ]
    if session_id:
        cmd += ["-H", f"Mcp-Session-Id: {session_id}"]
    cmd += ["-d", json.dumps(payload)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError(f"curl exit {r.returncode}")
    out = r.stdout
    sid = session_id
    for line in out.split("\n"):
        if line.lower().startswith("mcp-session-id:"):
            sid = line.split(":", 1)[1].strip()
            break
    parts = re.split(r"\r?\n\r?\n", out, maxsplit=1)
    return (parts[1] if len(parts) > 1 else out), sid


def _parse_body(body: str) -> dict:
    for line in body.strip().split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    try:
        return json.loads(body.strip())
    except json.JSONDecodeError:
        return {}


def _extract(response: dict) -> dict:
    if response.get("error"):
        raise RuntimeError(str(response["error"]))
    result = response.get("result", {})
    content = result.get("content", []) if isinstance(result, dict) else []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = str(item.get("text", "") or "").strip()
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
    return result if isinstance(result, dict) else {}


def _init_session() -> str:
    _body, sid = _curl_mcp(
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "hermit-dashboard", "version": "1.0"},
        },
    )
    if not sid:
        raise RuntimeError("MCP initialize: no session id returned")
    return sid


# ── Status display ────────────────────────────────────────────────────────────

_STATUS: dict[str, tuple[str, str]] = {
    "running": ("●", "cyan"),
    "queued": ("○", "bright_blue"),
    "completed": ("✓", "green"),
    "failed": ("✗", "red"),
    "blocked": ("⚠", "yellow"),
    "cancelled": ("⊘", "bright_black"),
}


def _status_cell(status: str) -> Text:
    icon, color = _STATUS.get(status, ("?", "white"))
    t = Text()
    t.append(f"{icon} ", style=color)
    t.append(status, style=color)
    return t


# ── Widgets ───────────────────────────────────────────────────────────────────


class StatCard(Widget):
    """Stat card: label + big Digits number."""

    DEFAULT_CSS = """
    StatCard {
        width: 1fr;
        height: 9;
        border: tall $accent;
        layout: vertical;
        align: center middle;
        padding: 1 1;
    }
    StatCard > Label {
        width: 100%;
        text-align: center;
        color: $text-muted;
        text-style: bold;
    }
    StatCard > Digits {
        width: 100%;
        text-align: center;
    }
    StatCard.running  { border: tall cyan; }
    StatCard.running  Digits { color: cyan; }
    StatCard.done     { border: tall green; }
    StatCard.done     Digits { color: green; }
    StatCard.failed   { border: tall red; }
    StatCard.failed   Digits { color: red; }
    StatCard.blocked  { border: tall yellow; }
    StatCard.blocked  Digits { color: yellow; }
    """

    def __init__(self, label: str, card_class: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._label = label
        if card_class:
            self.add_class(card_class)

    def compose(self) -> ComposeResult:
        yield Label(self._label)
        yield Digits("0")

    def set_value(self, v: int) -> None:
        self.query_one(Digits).update(str(v))


class ProgressRow(Horizontal):
    """Progress bar with percentage label."""

    DEFAULT_CSS = """
    ProgressRow {
        height: 3;
        padding: 0 2;
        align: center middle;
    }
    ProgressRow > .prog-label {
        width: 12;
        color: $text-muted;
        text-style: bold;
    }
    ProgressRow > ProgressBar {
        width: 1fr;
    }
    ProgressRow > .prog-stat {
        width: 22;
        text-align: right;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Progress", classes="prog-label")
        yield ProgressBar(total=100, show_eta=False, id="prog-bar")
        yield Label("", classes="prog-stat", id="prog-stat")

    def update_progress(self, done: int, total: int) -> None:
        pct = done / total * 100 if total else 0
        pb = self.query_one(ProgressBar)
        pb.total = float(total) if total > 0 else 100.0
        pb.progress = float(done)
        self.query_one("#prog-stat", Label).update(
            f"[bold]{pct:.0f}%[/bold]  ({done} / {total} tasks)"
        )


# ── Main app ──────────────────────────────────────────────────────────────────


class HermitDashboard(App):
    """Full-screen real-time Hermit task dashboard."""

    TITLE = "HERMIT LIVE DASHBOARD"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "manual_refresh", "Refresh"),
    ]

    CSS = """
    Screen { background: $surface; }

    #status-bar {
        height: 1;
        background: $panel;
        padding: 0 2;
    }
    #status-bar.healthy   { background: #003300; }
    #status-bar.degraded  { background: #332200; }
    #status-bar.unhealthy { background: #330000; }

    #stats-row { height: 9; }

    #task-table {
        height: 1fr;
        border: tall $surface;
    }

    #bottom-row { height: 12; }

    #gov-panel {
        width: 28;
        border: tall $surface;
        padding: 0 1;
    }

    #event-panel {
        width: 1fr;
        border: tall $surface;
        padding: 0 1;
        overflow-y: auto;
    }
    """

    def __init__(
        self,
        task_ids: list[str] | None = None,
        session_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._task_ids: list[str] = task_ids or []
        self._session_id = session_id
        self._refreshing = False
        self._events: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Connecting to Hermit MCP…", id="status-bar")
        with Horizontal(id="stats-row"):
            yield StatCard("TOTAL", id="card-total")
            yield StatCard("RUNNING", card_class="running", id="card-running")
            yield StatCard("DONE", card_class="done", id="card-done")
            yield StatCard("FAILED", card_class="failed", id="card-failed")
            yield StatCard("BLOCKED", card_class="blocked", id="card-blocked")
        yield ProgressRow(id="progress-row")
        yield DataTable(id="task-table", cursor_type="row")
        with Horizontal(id="bottom-row"):
            yield Static("GOVERNANCE\n\nConnecting…", id="gov-panel")
            yield Static("RECENT EVENTS\n\nConnecting…", id="event-panel")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("ID", width=10)
        table.add_column("STATUS", width=14)
        table.add_column("UPDATED", width=10)
        table.add_column("TITLE")

        # Load task IDs from state file if not provided via CLI
        if not self._task_ids and STATE_FILE.exists():
            try:
                state = json.loads(STATE_FILE.read_text())
                age = time.time() - float(state.get("submitted_at", 0))
                if age < 3600:
                    self._task_ids = list(state.get("task_ids", []))
            except Exception:
                pass

        self.set_interval(2.0, self._refresh)

    async def _refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            data = await asyncio.to_thread(self._fetch)
            if data:
                self._apply(data)
        except Exception:
            pass
        finally:
            self._refreshing = False

    def _fetch(self) -> dict | None:
        """Blocking MCP fetches — runs in thread pool."""
        # Init session on first run
        if not self._session_id:
            try:
                self._session_id = _init_session()
            except Exception as e:
                return {"error": str(e)}

        task_rows: list[dict] = []

        if self._task_ids:
            # Session-scoped: track specific submitted tasks
            try:
                body, self._session_id = _curl_mcp(
                    "hermit_task_status",
                    {"task_ids": self._task_ids},
                    self._session_id,
                )
                payload = _extract(_parse_body(body))
                for r in payload.get("tasks", []):
                    if not isinstance(r, dict):
                        continue
                    task = r.get("task", r)
                    task["_blocked"] = bool(r.get("is_blocked", False))
                    task["_events"] = r.get("recent_events") or []
                    task_rows.append(task)
            except Exception:
                pass
        else:
            # Standalone: discover all active tasks
            try:
                body, self._session_id = _curl_mcp(
                    "hermit_list_tasks",
                    {"status": "", "limit": 50},
                    self._session_id,
                )
                payload = _extract(_parse_body(body))
                for t in payload.get("tasks", []):
                    t["_blocked"] = False
                    t["_events"] = []
                    task_rows.append(t)
            except Exception:
                pass

        health: dict = {}
        try:
            body, self._session_id = _curl_mcp(
                "hermit_metrics",
                {"kind": "health", "window_hours": 1.0},
                self._session_id,
            )
            health = _extract(_parse_body(body))
        except Exception:
            pass

        gov: dict = {}
        try:
            body, self._session_id = _curl_mcp(
                "hermit_metrics",
                {"kind": "governance", "window_hours": 1.0},
                self._session_id,
            )
            gov = _extract(_parse_body(body))
        except Exception:
            pass

        return {"task_rows": task_rows, "health": health, "gov": gov}

    def _apply(self, data: dict) -> None:
        """Update all widgets — runs in event loop thread."""
        if err := data.get("error"):
            self.query_one("#status-bar", Static).update(f"[red]Connection error: {err}[/red]")
            return

        task_rows: list[dict] = data.get("task_rows", [])
        health: dict = data.get("health", {})
        gov: dict = data.get("gov", {})

        # ── Count statuses ────────────────────────────────────────────────
        counts: dict[str, int] = {
            "running": 0,
            "queued": 0,
            "completed": 0,
            "failed": 0,
            "blocked": 0,
            "cancelled": 0,
        }
        for t in task_rows:
            if t.get("_blocked"):
                counts["blocked"] += 1
            else:
                s = str(t.get("status", "") or "")
                counts[s] = counts.get(s, 0) + 1

        total = len(self._task_ids) if self._task_ids else len(task_rows)
        done = counts["completed"]
        running = counts["running"]
        failed = counts["failed"]
        blocked = counts["blocked"]

        # ── Status bar ────────────────────────────────────────────────────
        level = str(health.get("health_level", "unknown") or "unknown")
        score = float(health.get("health_score", 0) or 0)
        tput = health.get("throughput") or {}
        tph = float(tput.get("throughput_per_hour", 0) or 0)
        fail_r = float(tput.get("failure_rate", 0) or 0)
        stale = int(health.get("total_stale_tasks", 0) or 0)

        icons = {"healthy": "●", "degraded": "▲", "unhealthy": "✗"}
        colors = {"healthy": "green", "degraded": "yellow", "unhealthy": "red"}
        lv_icon = icons.get(level, "○")
        lv_color = colors.get(level, "white")

        sb = self.query_one("#status-bar", Static)
        sb.update(
            f"[{lv_color}]{lv_icon} {level.upper()}[/{lv_color}]"
            f"   Score: {score:.0f}"
            f"   {tph:.1f} tasks/hr"
            f"   Failure: {fail_r * 100:.1f}%"
            f"   Stale: {stale}"
        )
        sb.remove_class("healthy", "degraded", "unhealthy")
        if level in ("healthy", "degraded", "unhealthy"):
            sb.add_class(level)

        # ── Stat cards ────────────────────────────────────────────────────
        self.query_one("#card-total", StatCard).set_value(total)
        self.query_one("#card-running", StatCard).set_value(running)
        self.query_one("#card-done", StatCard).set_value(done)
        self.query_one("#card-failed", StatCard).set_value(failed)
        self.query_one("#card-blocked", StatCard).set_value(blocked)

        # ── Progress bar ──────────────────────────────────────────────────
        self.query_one(ProgressRow).update_progress(done, total)

        # ── Task table ────────────────────────────────────────────────────
        table = self.query_one(DataTable)
        table.clear()
        sorted_rows = sorted(
            task_rows,
            key=lambda t: float(t.get("created_at") or 0),
            reverse=False,
        )
        for t in sorted_rows[:50]:
            tid = (str(t.get("task_id", "")) or "")[:8] + "…"
            status = "blocked" if t.get("_blocked") else str(t.get("status", "") or "")
            upd = float(t.get("updated_at") or 0)
            t_str = time.strftime("%H:%M:%S", time.localtime(upd)) if upd else "--:--:--"
            title = str(t.get("title") or t.get("goal") or "")[:70]
            table.add_row(tid, _status_cell(status), t_str, title)

        # ── Recent events ─────────────────────────────────────────────────
        for t in task_rows:
            for ev in (t.get("_events") or [])[-1:]:
                ts = float(ev.get("occurred_at", 0) or 0)
                t_str2 = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "--:--:--"
                eid = str(ev.get("entity_id", ""))[:8]
                etype = str(ev.get("event_type", ""))
                self._events.append(f"[dim][{t_str2}][/dim] [bold cyan]{eid}[/bold cyan] → {etype}")
        self._events = self._events[-20:]
        self.query_one("#event-panel", Static).update(
            "[bold]RECENT EVENTS[/bold]\n\n" + "\n".join(self._events[-14:])
        )

        # ── Governance ────────────────────────────────────────────────────
        apr = float(gov.get("approval_rate", 0) or 0) * 100
        risk = gov.get("risk_summary", {})
        rh = int(risk.get("high", 0) or 0)
        rm = int(risk.get("medium", 0) or 0)
        rl = int(risk.get("low", 0) or 0)
        self.query_one("#gov-panel", Static).update(
            "[bold]GOVERNANCE[/bold]\n\n"
            f"Risk  [red]H:{rh}[/red]  [yellow]M:{rm}[/yellow]  [green]L:{rl}[/green]\n"
            f"Approvals: {apr:.1f}%\n"
            f"Stale: {stale} tasks"
        )

    def action_manual_refresh(self) -> None:
        self.call_after_refresh(self._refresh)

    async def action_quit(self) -> None:
        self.exit()


# ── CLI entry ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermit Live Dashboard")
    parser.add_argument("--task-ids", default="", help="Comma-separated task IDs to track")
    parser.add_argument("--session-id", default="", help="Existing MCP session ID")
    args = parser.parse_args()

    task_ids = [t.strip() for t in args.task_ids.split(",") if t.strip()]
    HermitDashboard(task_ids=task_ids, session_id=args.session_id).run()


if __name__ == "__main__":
    main()
