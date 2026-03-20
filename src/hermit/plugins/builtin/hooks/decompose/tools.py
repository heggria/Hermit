"""Agent-facing tools for spec generation and task decomposition."""

from __future__ import annotations

import json
from typing import Any

from hermit.plugins.builtin.hooks.decompose.models import GeneratedSpec
from hermit.plugins.builtin.hooks.decompose.spec_generator import SpecGenerator
from hermit.plugins.builtin.hooks.decompose.task_decomposer import TaskDecomposer
from hermit.runtime.capability.contracts.base import PluginContext
from hermit.runtime.capability.registry.tools import ToolSpec

_generator = SpecGenerator()
_decomposer = TaskDecomposer()


def _handle_generate_spec(payload: dict[str, Any]) -> str:
    """Generate a structured spec from a goal description."""
    goal = str(payload.get("goal", ""))
    if not goal.strip():
        return "Error: 'goal' is required."

    raw_constraints = payload.get("constraints")
    constraints: tuple[str, ...] | None = None
    if isinstance(raw_constraints, list):
        constraints = tuple(str(c) for c in raw_constraints)

    spec = _generator.generate(goal=goal, constraints=constraints)
    return json.dumps(
        {
            "spec_id": spec.spec_id,
            "title": spec.title,
            "goal": spec.goal,
            "constraints": list(spec.constraints),
            "acceptance_criteria": list(spec.acceptance_criteria),
            "file_plan": [dict(e) for e in spec.file_plan],
            "trust_zone": spec.trust_zone,
        },
        indent=2,
    )


def _handle_decompose_spec(payload: dict[str, Any]) -> str:
    """Decompose a spec into a DAG of executable steps."""
    spec_id = str(payload.get("spec_id", ""))
    goal = str(payload.get("goal", ""))
    if not spec_id or not goal:
        return "Error: 'spec_id' and 'goal' are required."

    raw_file_plan = payload.get("file_plan", [])
    file_plan = tuple(dict(e) for e in raw_file_plan if isinstance(e, dict))
    raw_criteria = payload.get("acceptance_criteria", [])
    acceptance_criteria = tuple(str(c) for c in raw_criteria)
    raw_constraints = payload.get("constraints", [])
    constraints = tuple(str(c) for c in raw_constraints)

    spec = GeneratedSpec(
        spec_id=spec_id,
        title=goal.split("\n")[0][:80],
        goal=goal,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        file_plan=file_plan,
    )
    plan = _decomposer.decompose(spec)
    return json.dumps(
        {
            "spec_id": plan.spec_id,
            "steps": list(plan.steps),
            "dependency_graph": dict(plan.dependency_graph),
            "estimated_duration_minutes": plan.estimated_duration_minutes,
        },
        indent=2,
    )


def register(ctx: PluginContext) -> None:
    """Register governed tools for spec generation and decomposition."""
    _gen_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "High-level task description"},
            "constraints": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["goal"],
    }
    _dec_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "spec_id": {"type": "string", "description": "Spec identifier"},
            "goal": {"type": "string", "description": "Goal description"},
            "file_plan": {"type": "array", "items": {"type": "object"}},
            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
            "constraints": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["spec_id", "goal"],
    }
    ctx.add_tool(
        ToolSpec(
            name="generate_spec",
            description="Generate a structured specification from a goal description.",
            input_schema=_gen_schema,
            handler=_handle_generate_spec,
            action_class="spec_generation",
            risk_hint="low",
            requires_receipt=False,
            readonly=True,
            idempotent=True,
        )
    )
    ctx.add_tool(
        ToolSpec(
            name="decompose_spec",
            description="Decompose a spec into a DAG of executable steps.",
            input_schema=_dec_schema,
            handler=_handle_decompose_spec,
            action_class="task_decomposition",
            risk_hint="low",
            requires_receipt=False,
            readonly=True,
            idempotent=True,
        )
    )
