"""OvernightSummary dataclass and OvernightReportService for overnight reporting."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from hermit.kernel.ledger.journal.store import KernelStore

log = structlog.get_logger()


@dataclass
class OvernightSummary:
    tasks_completed: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    tasks_failed: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    tasks_blocked: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    tasks_auto_generated: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    total_governed_actions: int = 0
    boundary_violations_prevented: int = 0
    approvals_pending: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    signals_emitted: int = 0
    signals_acted: int = 0
    lookback_hours: int = 12
    generated_at: float = 0.0


class OvernightReportService:
    def __init__(self, store: KernelStore) -> None:
        self._store = store

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return self._store._rows(sql, params)

    def _query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return self._store._row(sql, params)

    def generate(self, *, lookback_hours: int = 12) -> OvernightSummary:
        now = time.time()
        since = now - (lookback_hours * 3600)
        summary = OvernightSummary(lookback_hours=lookback_hours, generated_at=now)

        summary.tasks_completed = [
            dict(r)
            for r in self._query(
                "SELECT task_id, title, goal, status, updated_at "
                "FROM tasks WHERE status = 'completed' AND updated_at >= ?",
                (since,),
            )
        ]
        summary.tasks_failed = [
            dict(r)
            for r in self._query(
                "SELECT task_id, title, goal, status, updated_at "
                "FROM tasks WHERE status = 'failed' AND updated_at >= ?",
                (since,),
            )
        ]
        summary.tasks_blocked = [
            dict(r)
            for r in self._query(
                "SELECT task_id, title, goal, status, updated_at "
                "FROM tasks WHERE status = 'blocked' AND updated_at >= ?",
                (since,),
            )
        ]

        row = self._query_one(
            "SELECT COUNT(*) as cnt FROM receipts WHERE created_at >= ?",
            (since,),
        )
        summary.total_governed_actions = int(row["cnt"]) if row else 0

        summary.approvals_pending = [
            dict(r)
            for r in self._query(
                "SELECT approval_id, task_id, status, requested_at "
                "FROM approvals WHERE status = 'pending'",
            )
        ]

        if hasattr(self._store, "signal_stats"):
            stats = self._store.signal_stats(since=since)
            summary.signals_emitted = sum(stats.values())
            summary.signals_acted = stats.get("acted", 0)

        return summary

    def format_markdown(self, summary: OvernightSummary) -> str:
        lines = ["# Overnight Report", ""]
        lines.append(f"**Lookback**: {summary.lookback_hours}h")
        lines.append("")
        lines.append(f"## Tasks Completed ({len(summary.tasks_completed)})")
        for t in summary.tasks_completed:
            lines.append(f"- [{t.get('task_id', '?')}] {t.get('title', 'untitled')}")
        lines.append("")
        lines.append(f"## Tasks Failed ({len(summary.tasks_failed)})")
        for t in summary.tasks_failed:
            lines.append(f"- [{t.get('task_id', '?')}] {t.get('title', 'untitled')}")
        lines.append("")
        lines.append(f"## Tasks Blocked ({len(summary.tasks_blocked)})")
        for t in summary.tasks_blocked:
            lines.append(f"- [{t.get('task_id', '?')}] {t.get('title', 'untitled')}")
        lines.append("")
        lines.append("## Governance")
        lines.append(f"- Governed actions: {summary.total_governed_actions}")
        lines.append(f"- Pending approvals: {len(summary.approvals_pending)}")
        lines.append(f"- Signals emitted: {summary.signals_emitted}")
        lines.append(f"- Signals acted: {summary.signals_acted}")
        return "\n".join(lines)

    def format_dashboard_json(self, summary: OvernightSummary) -> dict[str, Any]:
        return {
            "tasks_completed": len(summary.tasks_completed),
            "tasks_failed": len(summary.tasks_failed),
            "tasks_blocked": len(summary.tasks_blocked),
            "total_governed_actions": summary.total_governed_actions,
            "approvals_pending": len(summary.approvals_pending),
            "signals_emitted": summary.signals_emitted,
            "signals_acted": summary.signals_acted,
            "lookback_hours": summary.lookback_hours,
            "generated_at": summary.generated_at,
        }
