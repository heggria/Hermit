"""TaskDecomposer — deterministic decomposition of specs into DAG steps."""

from __future__ import annotations

from typing import Any

from hermit.plugins.builtin.hooks.decompose.models import (
    DecompositionPlan,
    GeneratedSpec,
)


def _file_key(path: str, action: str) -> str:
    """Generate a step key from a file path and action."""
    safe = path.replace("/", "_").replace(".", "_")
    return f"{action}_{safe}"


class TaskDecomposer:
    """Decomposes a GeneratedSpec into a DecompositionPlan with StepNode-compatible dicts.

    Rules (deterministic, no LLM):
    - Each file_plan entry with action='create' -> code step
    - Each file_plan entry with action='modify' -> code step
      (depends on create steps it might import from)
    - If no file_plan entries, generate a primary execute step as fallback
    - Each acceptance_criterion -> review step (depends on all code steps)
    - Final 'make check' -> review step (depends on all review steps)
    """

    def decompose(self, spec: GeneratedSpec) -> DecompositionPlan:
        """Produce a DecompositionPlan from the given spec."""
        steps: list[dict[str, Any]] = []
        dependency_graph: dict[str, list[str]] = {}
        code_step_keys: list[str] = []
        create_step_keys: list[str] = []

        # Phase 1: code steps from file_plan
        for entry in spec.file_plan:
            path = entry.get("path", "")
            action = entry.get("action", "create")
            reason = entry.get("reason", "")
            key = _file_key(path, action)

            deps: list[str] = []
            if action == "modify":
                deps = list(create_step_keys)

            step = {
                "key": key,
                "kind": "code",
                "title": f"{action.capitalize()} {path}",
                "depends_on": deps,
                "metadata": {"path": path, "action": action, "reason": reason},
            }
            steps.append(step)
            dependency_graph[key] = deps
            code_step_keys.append(key)

            if action == "create":
                create_step_keys.append(key)

        # Phase 1b: if no file_plan entries, generate a primary execute step
        # so the DAG always has at least one implementation step.
        if not code_step_keys:
            key = "implement_goal"
            goal_title = spec.goal.split("\n")[0][:80].strip() or "(implement goal)"
            step = {
                "key": key,
                "kind": "execute",
                "title": goal_title,
                "depends_on": [],
                "metadata": {
                    "goal": spec.goal[:500],
                    "constraints": list(spec.constraints),
                },
            }
            steps.append(step)
            dependency_graph[key] = []
            code_step_keys.append(key)

        # Phase 2: review steps from acceptance_criteria
        review_step_keys: list[str] = []
        for idx, criterion in enumerate(spec.acceptance_criteria):
            key = f"review_{idx}"
            deps = list(code_step_keys)
            step = {
                "key": key,
                "kind": "review",
                "title": f"Verify: {criterion[:60]}",
                "depends_on": deps,
                "metadata": {"criterion": criterion},
            }
            steps.append(step)
            dependency_graph[key] = deps
            review_step_keys.append(key)

        # Phase 3: final 'make check' step
        final_key = "final_check"
        final_deps = list(review_step_keys) if review_step_keys else list(code_step_keys)
        steps.append(
            {
                "key": final_key,
                "kind": "review",
                "title": "Run make check",
                "depends_on": final_deps,
                "metadata": {"criterion": "make check passes"},
            }
        )
        dependency_graph[final_key] = final_deps

        # Estimate: 5 min per code step, 2 min per review step
        estimated = len(code_step_keys) * 5 + (len(review_step_keys) + 1) * 2

        return DecompositionPlan(
            spec_id=spec.spec_id,
            steps=tuple(steps),
            dependency_graph=dependency_graph,
            estimated_duration_minutes=estimated,
        )
