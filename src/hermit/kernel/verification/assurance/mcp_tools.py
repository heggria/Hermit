"""MCP tool definitions for assurance replay capabilities."""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger()


ASSURANCE_TOOLS = [
    {
        "name": "hermit_assurance_replay_task",
        "description": (
            "Replay a task's governance trace through the assurance system. "
            "Runs invariant checks, contract checks, and failure attribution "
            "against the recorded trace. Returns an assurance report with first "
            "violation, root cause, and evidence refs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to replay",
                },
                "attribution_mode": {
                    "type": "string",
                    "enum": ["off", "post_run"],
                    "description": "Whether to run failure attribution. Default: post_run",
                    "default": "post_run",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "hermit_assurance_check_trace",
        "description": (
            "Run assurance checks (invariants + contracts) against a task's "
            "recorded trace without full replay. Lighter weight than replay "
            "-- just validates the trace."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID whose trace to check",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "hermit_assurance_report",
        "description": (
            "Get the latest assurance report for a task, or generate one if none exists."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to get the assurance report for",
                },
                "format": {
                    "type": "string",
                    "enum": ["json", "markdown"],
                    "description": "Output format. Default: json",
                    "default": "json",
                },
            },
            "required": ["task_id"],
        },
    },
]


def handle_assurance_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    lab: Any,  # AssuranceLab -- use Any to avoid import issues
) -> dict[str, Any]:
    """Handle an assurance MCP tool call.

    Args:
        tool_name: The tool name (e.g., "hermit_assurance_replay_task")
        arguments: The tool arguments
        lab: An AssuranceLab instance

    Returns:
        Tool result dict
    """
    if tool_name == "hermit_assurance_replay_task":
        task_id = arguments["task_id"]
        attribution_mode = arguments.get("attribution_mode", "post_run")

        report = lab.replay_task(task_id, attribution_mode=attribution_mode)
        if report is None:
            return {"error": f"No trace found for task {task_id}"}

        from hermit.kernel.verification.assurance.reporting import AssuranceReporter

        reporter = AssuranceReporter()
        return reporter.emit_json(report)

    elif tool_name == "hermit_assurance_check_trace":
        task_id = arguments["task_id"]
        # Load trace and run just invariant + contract checks
        envelopes = lab.recorder.load_task_trace(task_id)
        if not envelopes:
            return {"error": f"No trace found for task {task_id}"}

        invariant_violations = lab.invariant_engine.check(envelopes, task_id=task_id)
        contract_violations = lab.contract_engine.evaluate_post_run(envelopes, task_id=task_id)

        return {
            "task_id": task_id,
            "envelope_count": len(envelopes),
            "invariant_violations": len(invariant_violations),
            "contract_violations": len(contract_violations),
            "first_violation": (
                {
                    "type": "invariant",
                    "id": invariant_violations[0].invariant_id,
                    "severity": invariant_violations[0].severity,
                }
                if invariant_violations
                else {
                    "type": "contract",
                    "id": contract_violations[0].contract_id,
                    "severity": contract_violations[0].severity,
                }
                if contract_violations
                else None
            ),
            "status": ("pass" if not invariant_violations and not contract_violations else "fail"),
        }

    elif tool_name == "hermit_assurance_report":
        task_id = arguments["task_id"]
        fmt = arguments.get("format", "json")

        report = lab.replay_task(task_id)
        if report is None:
            return {"error": f"No trace found for task {task_id}"}

        from hermit.kernel.verification.assurance.reporting import AssuranceReporter

        reporter = AssuranceReporter()

        if fmt == "markdown":
            return {"markdown": reporter.emit_markdown(report)}
        return reporter.emit_json(report)

    return {"error": f"Unknown tool: {tool_name}"}
