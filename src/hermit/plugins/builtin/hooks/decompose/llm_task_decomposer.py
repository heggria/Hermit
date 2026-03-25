"""LLM-native task decomposer — replaces rule-based TaskDecomposer with LLM-driven DAG generation."""

from __future__ import annotations

from typing import Any

import structlog

from hermit.plugins.builtin.hooks.decompose.models import (
    DecompositionPlan,
    GeneratedSpec,
)
from hermit.plugins.builtin.hooks.decompose.task_decomposer import TaskDecomposer
from hermit.runtime.provider_host.execution.vision_services import _parse_json_response
from hermit.runtime.provider_host.shared.contracts import Provider, ProviderRequest

log = structlog.get_logger()

_VALID_STEP_KINDS = frozenset({"code", "test", "review", "execute"})

_SYSTEM_PROMPT = """\
You are a task decomposition engine. Given a specification (goal, constraints, \
acceptance criteria, and an optional file plan), produce a JSON DAG of executable steps.

Output ONLY valid JSON with this schema:
{
  "steps": [
    {
      "key": "<unique_snake_case_identifier>",
      "kind": "code" | "test" | "review" | "execute",
      "title": "<short human-readable title>",
      "depends_on": ["<key of a prior step>", ...],
      "description": "<what this step does>",
      "metadata": {}
    }
  ],
  "estimated_duration_minutes": <integer>,
  "rationale": "<brief explanation of the decomposition strategy>"
}

Rules:
- Group logically related changes into single steps. Do not create one step per line of code.
- Create parallel steps where possible — independent file modifications should not depend on \
each other.
- Test steps must depend on the code steps they validate.
- Always include a final step with key "final_check" and kind "review" that depends on ALL \
other steps. Its title should be "Run final verification".
- If no file_plan is provided, create a single step with key "implement_goal" and kind \
"execute" that captures the entire goal, followed by the "final_check" step.
- Every key must be unique.
- "depends_on" may only reference keys of steps that appear earlier in the array.
- Valid kinds: "code", "test", "review", "execute".
- Keep the total number of steps reasonable (typically 3-12).
"""


class LLMTaskDecomposer:
    """Decomposes a GeneratedSpec into a DecompositionPlan using an LLM.

    Falls back to the deterministic ``TaskDecomposer`` on any failure.
    """

    def __init__(self, provider: Provider, *, model: str, max_tokens: int = 4096) -> None:
        self._provider = provider
        self._model = model
        self._max_tokens = max_tokens
        self._fallback = TaskDecomposer()

    def decompose(self, spec: GeneratedSpec) -> DecompositionPlan:
        """Decompose *spec* into a DAG plan via LLM, falling back on failure."""
        try:
            return self._decompose_via_llm(spec)
        except Exception:
            log.warning(
                "llm_decompose_failed_falling_back",
                spec_id=spec.spec_id,
                exc_info=True,
            )
            return self._fallback.decompose(spec)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _decompose_via_llm(self, spec: GeneratedSpec) -> DecompositionPlan:
        user_content = self._build_user_message(spec)
        request = ProviderRequest(
            model=self._model,
            max_tokens=self._max_tokens,
            system_prompt=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )

        response = self._provider.generate(request=request)
        parsed = _parse_json_response(response)
        if parsed is None:
            raise ValueError("LLM returned unparseable response")

        self._validate_decomposition_output(parsed)
        return self._to_plan(spec, parsed)

    @staticmethod
    def _build_user_message(spec: GeneratedSpec) -> str:
        parts = [f"Goal: {spec.goal}"]
        if spec.constraints:
            parts.append("Constraints:\n" + "\n".join(f"- {c}" for c in spec.constraints))
        if spec.acceptance_criteria:
            parts.append(
                "Acceptance criteria:\n" + "\n".join(f"- {c}" for c in spec.acceptance_criteria)
            )
        if spec.file_plan:
            lines = []
            for entry in spec.file_plan:
                path = entry.get("path", "")
                action = entry.get("action", "")
                reason = entry.get("reason", "")
                lines.append(f"  {action} {path} — {reason}")
            parts.append("File plan:\n" + "\n".join(lines))
        return "\n\n".join(parts)

    @staticmethod
    def _validate_decomposition_output(data: dict[str, Any]) -> None:
        """Validate LLM output structure; raises ``ValueError`` on problems."""
        steps = data.get("steps")
        if not isinstance(steps, list) or len(steps) == 0:
            raise ValueError("'steps' must be a non-empty list")

        seen_keys: set[str] = set()
        ordered_keys: list[str] = []

        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"step[{idx}] is not a dict")

            key = step.get("key")
            if not isinstance(key, str) or not key:
                raise ValueError(f"step[{idx}] missing or empty 'key'")
            if key in seen_keys:
                raise ValueError(f"duplicate step key: {key!r}")
            seen_keys.add(key)
            ordered_keys.append(key)

            kind = step.get("kind", "")
            if kind not in _VALID_STEP_KINDS:
                raise ValueError(f"step[{idx}] invalid kind: {kind!r}")

            depends_on = step.get("depends_on", [])
            if not isinstance(depends_on, list):
                raise ValueError(f"step[{idx}] 'depends_on' must be a list")
            for dep in depends_on:
                if dep not in seen_keys:
                    raise ValueError(f"step[{idx}] depends_on {dep!r} which is not a prior step")

        if ordered_keys[-1] != "final_check":
            raise ValueError("last step must have key 'final_check'")

    @staticmethod
    def _to_plan(spec: GeneratedSpec, data: dict[str, Any]) -> DecompositionPlan:
        raw_steps: list[dict[str, Any]] = data["steps"]
        steps: list[dict[str, Any]] = []
        dependency_graph: dict[str, list[str]] = {}

        for raw in raw_steps:
            key = raw["key"]
            deps = raw.get("depends_on", [])
            step: dict[str, Any] = {
                "key": key,
                "kind": raw.get("kind", "execute"),
                "title": raw.get("title", key),
                "depends_on": deps,
                "metadata": raw.get("metadata", {}),
            }
            description = raw.get("description")
            if description:
                step["metadata"] = {**step["metadata"], "description": description}
            steps.append(step)
            dependency_graph[key] = list(deps)

        return DecompositionPlan(
            spec_id=spec.spec_id,
            steps=tuple(steps),
            dependency_graph=dependency_graph,
            estimated_duration_minutes=int(data.get("estimated_duration_minutes", 0)),
        )
