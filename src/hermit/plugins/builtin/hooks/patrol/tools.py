"""Agent-facing tools for manual patrol triggering and status."""

from __future__ import annotations

from typing import Any

from hermit.plugins.builtin.hooks.patrol.engine import PatrolEngine
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.registry.tools import ToolSpec

_engine: PatrolEngine | None = None


def set_engine(engine: PatrolEngine) -> None:
    global _engine
    _engine = engine


def _handle_patrol_run(payload: dict[str, Any]) -> str:
    if _engine is None:
        return "Patrol engine is not running."
    report = _engine.run_patrol()
    lines = [f"Patrol complete: {report.total_issues} issue(s) found"]
    for check in report.checks:
        lines.append(f"  {check.check_name}: {check.status} ({check.issue_count} issues)")
        if check.issues:
            for issue in check.issues[:5]:
                detail = issue.get("message") or issue.get("text") or str(issue)
                lines.append(f"    - {detail}")
            if len(check.issues) > 5:
                lines.append(f"    ... and {len(check.issues) - 5} more")
    return "\n".join(lines)


def _handle_patrol_status(payload: dict[str, Any]) -> str:
    if _engine is None:
        return "Patrol engine is not running."
    report = _engine.last_report
    if report is None:
        return "No patrol report available yet."
    duration = report.finished_at - report.started_at
    lines = [
        f"Last patrol: {report.total_issues} issue(s), "
        f"{len(report.checks)} check(s) run in {duration:.1f}s",
    ]
    for check in report.checks:
        lines.append(f"  {check.check_name}: {check.status} ({check.issue_count} issues)")
    return "\n".join(lines)


def register(ctx: PluginContext) -> None:
    ctx.add_tool(
        ToolSpec(
            name="patrol_run",
            description=(
                "Run a patrol check now. Executes all configured code health checks "
                "(lint, test, todo_scan, etc.) and returns results immediately."
            ),
            input_schema={
                "type": "object",
                "properties": {},
            },
            handler=_handle_patrol_run,
            action_class="patrol_execution",
            risk_hint="medium",
            requires_receipt=True,
        )
    )

    ctx.add_tool(
        ToolSpec(
            name="patrol_status",
            description="Show the most recent patrol report summary.",
            input_schema={
                "type": "object",
                "properties": {},
            },
            handler=_handle_patrol_status,
            readonly=True,
            action_class="read_local",
            idempotent=True,
            risk_hint="low",
            requires_receipt=False,
        )
    )
